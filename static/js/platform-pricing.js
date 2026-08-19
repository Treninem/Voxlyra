(() => {
  'use strict';

  const PRICE_INPUT_SELECTOR = [
    '#bookPriceInput',
    '#graphicChapterPrice',
    '#chapterPackagePriceInput',
    '#graphicChapterEditPrice',
    '#chapterBulkPriceInput',
    '#chapterPriceInput',
    '[data-volume-price]',
  ].join(',');

  function isVK() {
    try {
      return typeof window.voxPlatform === 'function'
        ? window.voxPlatform() === 'vk'
        : document.documentElement.dataset.voxPlatform === 'vk';
    } catch (_) {
      return document.documentElement.dataset.voxPlatform === 'vk';
    }
  }

  function ratio() {
    const raw = Number(document.querySelector('meta[name="voxlyra-vk-votes-per-star"]')?.content || 1);
    return Number.isFinite(raw) && raw > 0 ? Math.max(1, raw) : 1;
  }

  function starsToVotes(stars) {
    const value = Math.max(0, Math.floor(Number(stars || 0)));
    return value <= 0 ? 0 : Math.max(1, Math.ceil(value * ratio()));
  }

  function votesToStars(votes) {
    const value = Math.max(0, Math.floor(Number(votes || 0)));
    return value <= 0 ? 0 : Math.max(1, Math.ceil(value / ratio()));
  }

  function priceInputs(root = document) {
    return Array.from(root.querySelectorAll?.(PRICE_INPUT_SELECTOR) || []);
  }

  function localizeLabel(input) {
    const label = input?.closest?.('label');
    if (!label || label.dataset.voxNativeCurrency === 'vk') return;
    label.childNodes.forEach((node) => {
      if (node.nodeType !== Node.TEXT_NODE) return;
      node.nodeValue = String(node.nodeValue || '').replace(/Stars/gi, 'голосов VK');
    });
    const small = label.querySelector('small');
    if (small && !/VK|голос/i.test(small.textContent || '')) {
      const base = String(small.textContent || '').trim();
      small.textContent = `${base}${base ? ' ' : ''}В VK цена задаётся в голосах; для Telegram эквивалент в Stars рассчитывается автоматически.`;
    }
    label.dataset.voxNativeCurrency = 'vk';
    input.setAttribute('inputmode', 'numeric');
    input.setAttribute('step', '1');
  }

  function nativeizeInput(input) {
    if (!isVK() || !input) return;
    localizeLabel(input);
    if (input.dataset.voxPriceState === 'temporary-canonical') return;
    const current = String(input.value ?? '');
    if (input.dataset.voxPriceState === 'native' && current === String(input.dataset.voxNativeValue ?? '')) return;
    const canonicalStars = Number(current || 0);
    const votes = starsToVotes(canonicalStars);
    input.value = String(votes);
    input.dataset.voxPriceState = 'native';
    input.dataset.voxNativeValue = String(votes);
    input.dataset.voxCanonicalStars = String(Math.max(0, Math.floor(canonicalStars || 0)));
  }

  function nativeizeAll() {
    if (!isVK()) return;
    priceInputs().forEach(nativeizeInput);
  }

  function canonicalizeInput(input) {
    if (!isVK() || !input) return;
    if (input.dataset.voxPriceState !== 'native') {
      nativeizeInput(input);
    }
    const requestedVotes = Math.max(0, Math.floor(Number(input.value || 0)));
    const stars = votesToStars(requestedVotes);
    input.value = String(stars);
    input.dataset.voxRequestedVotes = String(requestedVotes);
    input.dataset.voxPriceState = 'temporary-canonical';
    input.dataset.voxCanonicalStars = String(stars);
  }

  function restoreNativeInput(input) {
    if (!isVK() || !input || input.dataset.voxPriceState !== 'temporary-canonical') return;
    const stars = Math.max(0, Math.floor(Number(input.value || input.dataset.voxCanonicalStars || 0)));
    const effectiveVotes = starsToVotes(stars);
    input.value = String(effectiveVotes);
    input.dataset.voxPriceState = 'native';
    input.dataset.voxNativeValue = String(effectiveVotes);
    input.dataset.voxCanonicalStars = String(stars);
    input.removeAttribute('data-vox-requested-votes');
  }

  function canonicalizeAll() {
    if (!isVK()) return;
    priceInputs().forEach(canonicalizeInput);
  }

  function restoreAllSoon() {
    window.setTimeout(() => priceInputs().forEach(restoreNativeInput), 0);
    window.setTimeout(nativeizeAll, 80);
  }

  function isPriceInput(target) {
    return target instanceof Element && Boolean(target.matches?.(PRICE_INPUT_SELECTOR));
  }

  function preparePriceEvent(event) {
    if (!isVK() || !isPriceInput(event.target)) return;
    canonicalizeInput(event.target);
    restoreAllSoon();
  }

  document.addEventListener('input', preparePriceEvent, true);
  document.addEventListener('change', preparePriceEvent, true);

  document.addEventListener('submit', (event) => {
    if (!isVK() || !(event.target instanceof Element) || !event.target.closest('#authorStudio')) return;
    canonicalizeAll();
    restoreAllSoon();
  }, true);

  document.addEventListener('click', (event) => {
    if (!isVK() || !(event.target instanceof Element)) return;
    const action = event.target.closest('#authorStudio button, #authorStudio [data-save-graphic-volume]');
    if (!action) return;
    canonicalizeAll();
    restoreAllSoon();
  }, true);

  const originalApiFetch = window.apiFetch;
  if (typeof originalApiFetch === 'function') {
    window.apiFetch = async function voxNativePriceApiFetch(...args) {
      try {
        return await originalApiFetch.apply(this, args);
      } finally {
        window.setTimeout(nativeizeAll, 0);
        window.setTimeout(nativeizeAll, 120);
      }
    };
  }

  function installObserver() {
    const studio = document.getElementById('authorStudio');
    if (!studio || !window.MutationObserver) return;
    let scheduled = false;
    const observer = new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      requestAnimationFrame(() => {
        scheduled = false;
        nativeizeAll();
      });
    });
    observer.observe(studio, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
  }

  window.voxPlatformPricing = Object.freeze({
    starsToVotes,
    votesToStars,
    nativeizeAll,
    canonicalizeAll,
  });

  if (!isVK()) return;
  installObserver();
  nativeizeAll();
  window.setTimeout(nativeizeAll, 0);
  window.setTimeout(nativeizeAll, 200);
  window.setTimeout(nativeizeAll, 1000);
})();
