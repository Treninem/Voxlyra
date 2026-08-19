(() => {
  'use strict';

  const icon = document.getElementById('libraryProfileIcon');
  const initial = document.getElementById('libraryProfileInitial');
  const choose = document.getElementById('profileAvatarChoose');
  const reset = document.getElementById('profileAvatarReset');
  const file = document.getElementById('profileAvatarFile');
  const hint = document.getElementById('profileAvatarHint');
  if (!icon || !choose || !reset || !file || typeof window.apiFetch !== 'function') return;

  let objectUrl = '';
  let customActive = false;
  let applyingCustom = false;

  function releaseObjectUrl() {
    if (!objectUrl) return;
    URL.revokeObjectURL(objectUrl);
    objectUrl = '';
  }

  function platformName() {
    try { return typeof window.voxPlatform === 'function' && window.voxPlatform() === 'vk' ? 'VK' : 'Telegram'; }
    catch (_) { return 'платформы'; }
  }

  function setHint(custom) {
    if (!hint) return;
    hint.textContent = custom
      ? 'Используется ваш общий аватар VoxLyra — он одинаковый в Telegram и VK.'
      : `Используется фото из ${platformName()}. Можно установить свой общий аватар VoxLyra.`;
  }

  function applyBlob(blob) {
    releaseObjectUrl();
    objectUrl = URL.createObjectURL(blob);
    applyingCustom = true;
    icon.src = objectUrl;
    icon.hidden = false;
    icon.classList.add('telegram-avatar');
    icon.dataset.voxCustomAvatar = '1';
    if (initial) initial.hidden = true;
    reset.hidden = false;
    customActive = true;
    setHint(true);
    queueMicrotask(() => { applyingCustom = false; });
  }

  async function loadCustomAvatar() {
    try {
      const response = await window.apiFetch(`/api/me/custom-avatar?v=${Date.now()}`, { cache: 'no-store' });
      const blob = await response.blob();
      if (!blob || blob.size <= 0) throw new Error('Пустой аватар');
      applyBlob(blob);
      return true;
    } catch (_) {
      customActive = false;
      reset.hidden = true;
      setHint(false);
      return false;
    }
  }

  choose.addEventListener('click', () => file.click());

  file.addEventListener('change', async () => {
    const selected = file.files?.[0];
    if (!selected) return;
    if (selected.size > 8 * 1024 * 1024) {
      window.notify?.('Аватар должен быть не больше 8 МБ');
      file.value = '';
      return;
    }
    choose.disabled = true;
    reset.disabled = true;
    const form = new FormData();
    form.append('avatar', selected, selected.name || 'avatar');
    try {
      await window.apiFetch('/api/me/custom-avatar', { method: 'POST', body: form });
      await loadCustomAvatar();
      window.notify?.('Аватар сохранён для Telegram и VK');
    } catch (error) {
      window.notify?.(error?.message || 'Не удалось сохранить аватар');
    } finally {
      choose.disabled = false;
      reset.disabled = false;
      file.value = '';
    }
  });

  reset.addEventListener('click', async () => {
    reset.disabled = true;
    choose.disabled = true;
    try {
      await window.apiFetch('/api/me/custom-avatar', { method: 'DELETE' });
      customActive = false;
      releaseObjectUrl();
      icon.removeAttribute('data-vox-custom-avatar');
      reset.hidden = true;
      setHint(false);
      window.notify?.(`Возвращено фото из ${platformName()}`);
      // Existing library bootstrap already knows how to fetch the native Telegram/VK avatar.
      // A same-document reload preserves the signed launch context and prevents stale image races.
      window.setTimeout(() => window.location.reload(), 120);
    } catch (error) {
      window.notify?.(error?.message || 'Не удалось вернуть фото из платформы');
      reset.disabled = false;
      choose.disabled = false;
    }
  });

  const observer = new MutationObserver(() => {
    if (!customActive || applyingCustom || !objectUrl) return;
    if (icon.src === objectUrl && !icon.hidden) return;
    applyingCustom = true;
    icon.src = objectUrl;
    icon.hidden = false;
    if (initial) initial.hidden = true;
    queueMicrotask(() => { applyingCustom = false; });
  });
  observer.observe(icon, { attributes: true, attributeFilter: ['src', 'hidden'] });

  window.addEventListener('beforeunload', releaseObjectUrl, { once: true });
  loadCustomAvatar();
})();
