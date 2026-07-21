const graphicState = {
  meta: null,
  chapterId: 0,
  pages: [],
  currentIndex: 0,
  layout: 'vertical',
  direction: 'ltr',
  objectUrls: new Map(),
  observer: null,
  saveTimer: null,
  cacheAllRunning: false,
  preloadRunning: new Set(),
  metaRefreshing: null,
  translationEnabled: false,
  translationLanguage: 'ru',
  frameMode: false,
  frameItems: [],
  frameIndex: 0,
  sessionKey: `comic-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  lastEventPageId: 0,
  lastNetworkErrorAt: 0,
};

const GRAPHIC_DB_NAME = 'voxlyra-graphic-cache';
const GRAPHIC_DB_VERSION = 2;
const GRAPHIC_STORE = 'pages';

function graphicMetaStorageKey(chapterId = graphicState.chapterId) {
  return `voxGraphicMeta:${Number(chapterId)}`;
}

function loadCachedGraphicMeta(chapterId = graphicState.chapterId) {
  try {
    const raw = localStorage.getItem(graphicMetaStorageKey(chapterId));
    if (!raw) return null;
    const item = JSON.parse(raw);
    if (!item?.meta || !Array.isArray(item.meta.pages)) return null;
    return item.meta;
  } catch (_) { return null; }
}

function saveCachedGraphicMeta(meta, chapterId = null) {
  if (!meta || meta.moderation_access || !Array.isArray(meta.pages)) return;
  const id = Number(chapterId || meta.chapter?.id || graphicState.chapterId);
  if (!id) return;
  try {
    localStorage.setItem(graphicMetaStorageKey(id), JSON.stringify({ savedAt: Date.now(), meta }));
  } catch (_) {}
}

function graphicConnectionProfile() {
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  const effectiveType = String(connection?.effectiveType || 'unknown');
  const saveData = Boolean(connection?.saveData);
  const slow = saveData || ['slow-2g', '2g'].includes(effectiveType);
  const medium = !slow && effectiveType === '3g';
  return { connection, effectiveType, saveData, slow, medium };
}

function waitForGraphicOnline(timeoutMs = 15000) {
  if (navigator.onLine !== false) return Promise.resolve();
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      window.removeEventListener('online', finish);
      resolve();
    };
    window.addEventListener('online', finish, { once: true });
    setTimeout(finish, timeoutMs);
  });
}

async function fetchGraphicPageResponse(url, attempts = 5) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      if (navigator.onLine === false) await waitForGraphicOnline();
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 30000);
      const response = await fetch(url, { cache: 'no-store', signal: controller.signal });
      clearTimeout(timer);
      if (response.ok) return response;
      const error = new Error('Страница не загрузилась');
      error.status = response.status;
      throw error;
    } catch (error) {
      lastError = error;
      const status = Number(error?.status || 0);
      if (attempt >= attempts || status === 403 || status === 404) break;
      const delay = Math.min(8000, 500 * (2 ** (attempt - 1))) + Math.round(Math.random() * 250);
      await new Promise((resolve) => setTimeout(resolve, delay));
    }
  }
  throw lastError || new Error('Страница не загрузилась');
}

function openGraphicDb() {
  return new Promise((resolve, reject) => {
    if (!('indexedDB' in window)) { resolve(null); return; }
    const request = indexedDB.open(GRAPHIC_DB_NAME, GRAPHIC_DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(GRAPHIC_STORE)) db.createObjectStore(GRAPHIC_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function graphicCacheGet(key) {
  const db = await openGraphicDb().catch(() => null);
  if (!db) return null;
  return new Promise((resolve) => {
    const tx = db.transaction(GRAPHIC_STORE, 'readwrite');
    const store = tx.objectStore(GRAPHIC_STORE);
    const request = store.get(key);
    request.onsuccess = () => {
      const item = request.result || null;
      if (item) {
        item.lastAccess = Date.now();
        store.put(item, key);
      }
      resolve(item);
    };
    request.onerror = () => resolve(null);
    tx.oncomplete = () => db.close();
    tx.onerror = () => { db.close(); resolve(null); };
  });
}

function graphicCacheLimits() {
  return {
    maxBytes: Math.max(64, Number(graphicState.meta?.delivery?.device_cache_max_mb || 512)) * 1024 * 1024,
    maxItems: Math.max(100, Number(graphicState.meta?.delivery?.device_cache_max_items || 1200)),
  };
}

async function listGraphicCacheEntries() {
  const db = await openGraphicDb().catch(() => null);
  if (!db) return [];
  return new Promise((resolve) => {
    const entries = [];
    const tx = db.transaction(GRAPHIC_STORE, 'readonly');
    const request = tx.objectStore(GRAPHIC_STORE).openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      entries.push({ key: String(cursor.key), ...(cursor.value || {}) });
      cursor.continue();
    };
    tx.oncomplete = () => { db.close(); resolve(entries); };
    tx.onerror = () => { db.close(); resolve(entries); };
  });
}

async function deleteGraphicCacheKeys(keys) {
  if (!keys.length) return;
  const db = await openGraphicDb().catch(() => null);
  if (!db) return;
  await new Promise((resolve) => {
    const tx = db.transaction(GRAPHIC_STORE, 'readwrite');
    const store = tx.objectStore(GRAPHIC_STORE);
    keys.forEach((key) => store.delete(key));
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); resolve(); };
  });
}

async function enforceGraphicCacheLimits() {
  const limits = graphicCacheLimits();
  const entries = await listGraphicCacheEntries();
  let totalBytes = entries.reduce((sum, item) => sum + Number(item.size || item.blob?.size || 0), 0);
  let totalItems = entries.length;
  if (totalBytes <= limits.maxBytes && totalItems <= limits.maxItems) return;
  const removable = entries
    .filter((item) => !item.pinned)
    .sort((a, b) => Number(a.lastAccess || a.savedAt || 0) - Number(b.lastAccess || b.savedAt || 0));
  const deleting = [];
  for (const item of removable) {
    if (totalBytes <= limits.maxBytes && totalItems <= limits.maxItems) break;
    deleting.push(item.key);
    totalBytes -= Number(item.size || item.blob?.size || 0);
    totalItems -= 1;
  }
  await deleteGraphicCacheKeys(deleting);
}

async function graphicCachePut(key, blob, meta = {}) {
  const db = await openGraphicDb().catch(() => null);
  if (!db) return false;
  const saved = await new Promise((resolve) => {
    const tx = db.transaction(GRAPHIC_STORE, 'readwrite');
    tx.objectStore(GRAPHIC_STORE).put({
      blob,
      size: Number(blob?.size || 0),
      savedAt: Date.now(),
      lastAccess: Date.now(),
      chapterId: Number(meta.chapterId || graphicState.chapterId || 0),
      bookId: Number(meta.bookId || graphicState.meta?.chapter?.book_id || 0),
      volumeNumber: Number(meta.volumeNumber || graphicState.meta?.chapter?.volume_number || 1),
      variant: String(meta.variant || 'auto'),
      pinned: Boolean(meta.pinned),
    }, key);
    tx.oncomplete = () => { db.close(); resolve(true); };
    tx.onerror = () => { db.close(); resolve(false); };
    tx.onabort = () => { db.close(); resolve(false); };
  });
  if (saved && !meta.pinned) enforceGraphicCacheLimits().catch(() => {});
  return saved;
}

async function graphicCacheDeletePrefix(prefix) {
  const entries = await listGraphicCacheEntries();
  await deleteGraphicCacheKeys(entries.filter((item) => item.key.startsWith(prefix)).map((item) => item.key));
}

async function graphicCacheMarkPrefixPinned(prefix, pinned = true) {
  const db = await openGraphicDb().catch(() => null);
  if (!db) return;
  await new Promise((resolve) => {
    const tx = db.transaction(GRAPHIC_STORE, 'readwrite');
    const store = tx.objectStore(GRAPHIC_STORE);
    const request = store.openCursor();
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      if (String(cursor.key).startsWith(prefix)) {
        const item = cursor.value || {};
        item.pinned = Boolean(pinned);
        item.lastAccess = Date.now();
        cursor.update(item);
      }
      cursor.continue();
    };
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); resolve(); };
  });
}

async function updateGraphicCacheStatus() {
  const box = document.getElementById('graphicCacheStatus');
  if (!box) return;
  const entries = await listGraphicCacheEntries();
  const bytes = entries.reduce((sum, item) => sum + Number(item.size || item.blob?.size || 0), 0);
  const pinned = entries.filter((item) => item.pinned).length;
  box.textContent = `На устройстве: ${(bytes / 1024 / 1024).toFixed(1)} МБ · ${entries.length} страниц${pinned ? ` · офлайн ${pinned}` : ''}`;
}

function releaseGraphicObjectUrls() {
  graphicState.objectUrls.forEach((url) => URL.revokeObjectURL(url));
  graphicState.objectUrls.clear();
}

function chooseGraphicVariant(page) {
  const variants = Object.entries(page?.variants || {})
    .map(([label, item]) => ({ label, ...(item || {}) }))
    .filter((item) => item.url)
    .sort((a, b) => Number(a.width || 0) - Number(b.width || 0));
  if (!variants.length) {
    return { label: 'legacy', url: page.url, width: Number(page.width || 0), cacheKey: page.cache_key || 'v1' };
  }
  const profile = graphicConnectionProfile();
  const containerWidth = Math.max(320, document.getElementById('graphicPages')?.clientWidth || window.innerWidth || 720);
  const slots = graphicState.layout === 'spread' ? 2 : 1;
  let desired = Math.ceil((containerWidth / slots) * Math.min(2, window.devicePixelRatio || 1));
  if (profile.slow) desired = Math.min(desired, 720);
  else if (profile.medium) desired = Math.min(desired, 1280);
  const selected = variants.find((item) => Number(item.width || 0) >= desired) || variants[variants.length - 1];
  return {
    label: selected.label,
    url: selected.url,
    width: Number(selected.width || 0),
    cacheKey: selected.checksum || `${page.cache_key || 'v1'}:${selected.label}`,
  };
}

function graphicPageCacheKey(page, chapterId = null, variant = null) {
  const id = Number(chapterId || page?._chapterId || graphicState.chapterId);
  const chosen = variant || chooseGraphicVariant(page);
  return `chapter:${id}:page:${Number(page.number)}:${chosen.label}:${chosen.cacheKey || page.cache_key || 'v1'}`;
}

async function refreshGraphicMeta() {
  if (graphicState.metaRefreshing) return graphicState.metaRefreshing;
  graphicState.metaRefreshing = apiFetchWithRetry(`/api/comic/${graphicState.chapterId}?language=${encodeURIComponent(graphicState.translationLanguage || 'ru')}`, {}, 3, 20000)
    .then((meta) => {
      if (meta?.allowed && Array.isArray(meta.pages)) {
        graphicState.meta = meta;
        graphicState.pages = meta.pages;
        saveCachedGraphicMeta(meta);
      }
      return meta;
    })
    .finally(() => { graphicState.metaRefreshing = null; });
  return graphicState.metaRefreshing;
}

async function loadGraphicPageBlob(page, { forceNetwork = false, pinned = false, chapterId = null, meta = null } = {}) {
  const activeMeta = meta || graphicState.meta;
  const id = Number(chapterId || page?._chapterId || activeMeta?.chapter?.id || graphicState.chapterId);
  let variant = chooseGraphicVariant(page);
  let key = graphicPageCacheKey(page, id, variant);
  const persistentAllowed = Boolean(activeMeta?.protection?.allow_download && !activeMeta?.moderation_access);
  if (!forceNetwork && persistentAllowed) {
    const cached = await graphicCacheGet(key);
    if (cached?.blob) {
      if (pinned && !cached.pinned) await graphicCacheMarkPrefixPinned(key, true);
      return { blob: cached.blob, cached: true, variant };
    }
  }
  let response;
  try {
    response = await fetchGraphicPageResponse(variant.url);
  } catch (error) {
    if (Number(error?.status || 0) === 403 && id === graphicState.chapterId && navigator.onLine !== false) {
      const fresh = await refreshGraphicMeta();
      const freshPage = fresh?.pages?.find((item) => Number(item.number) === Number(page.number));
      if (freshPage) {
        Object.assign(page, freshPage);
        variant = chooseGraphicVariant(page);
        key = graphicPageCacheKey(page, id, variant);
        response = await fetchGraphicPageResponse(variant.url);
      } else throw error;
    } else throw error;
  }
  const blob = await response.blob();
  if (persistentAllowed) {
    const saved = await graphicCachePut(key, blob, {
      pinned,
      chapterId: id,
      bookId: activeMeta?.chapter?.book_id,
      volumeNumber: activeMeta?.chapter?.volume_number,
      variant: variant.label,
    });
    if (!saved && pinned) throw new Error('На устройстве недостаточно места для офлайн-сохранения.');
  }
  return { blob, cached: false, variant };
}

async function applyGraphicImageSource(image, page, { retry = false } = {}) {
  if (!image || (image.dataset.loaded === '1' && !retry) || image.dataset.loading === '1') return;
  image.dataset.loading = '1';
  const figure = image.closest('.graphic-page');
  figure?.classList.remove('is-error');
  try {
    const result = await loadGraphicPageBlob(page);
    const objectUrl = URL.createObjectURL(result.blob);
    const objectKey = `${page._chapterId || graphicState.chapterId}:${page.number}`;
    const oldUrl = graphicState.objectUrls.get(objectKey);
    if (oldUrl) URL.revokeObjectURL(oldUrl);
    graphicState.objectUrls.set(objectKey, objectUrl);
    image.src = objectUrl;
    image.dataset.loaded = '1';
    image.dataset.variant = result.variant?.label || 'auto';
    figure?.classList.add('is-ready');
  } catch (error) {
    graphicState.lastNetworkErrorAt = Date.now();
    image.alt = `Страница ${page.number} временно недоступна`;
    delete image.dataset.loaded;
    figure?.classList.add('is-error');
    const placeholder = figure?.querySelector('.graphic-page-placeholder span');
    if (placeholder) {
      placeholder.textContent = navigator.onLine === false
        ? `Страница ${page.number} не сохранена офлайн`
        : (Number(error?.status || 0) === 403
          ? `Ссылка на страницу ${page.number} обновляется`
          : `Страница ${page.number} временно недоступна`);
    }
  } finally {
    delete image.dataset.loading;
  }
}

function graphicDefaultLayout(meta) {
  const mode = String(meta?.reading_mode || 'ltr');
  if (mode === 'vertical') return 'vertical';
  if (mode === 'spread') return 'spread';
  return 'single';
}

function graphicDefaultDirection(meta) {
  return String(meta?.reading_mode || '') === 'rtl' ? 'rtl' : 'ltr';
}

function graphicPreferenceKey(kind) {
  const bookId = Number(graphicState.meta?.chapter?.book_id || 0);
  return `voxGraphic:${bookId}:${kind}`;
}

function restoreGraphicPreferences(meta) {
  const savedLayout = localStorage.getItem(graphicPreferenceKey('layout'));
  const savedDirection = localStorage.getItem(graphicPreferenceKey('direction'));
  graphicState.layout = ['vertical', 'single', 'spread'].includes(savedLayout) ? savedLayout : graphicDefaultLayout(meta);
  graphicState.direction = ['ltr', 'rtl'].includes(savedDirection) ? savedDirection : graphicDefaultDirection(meta);
}

function updateGraphicSettingsButtons() {
  document.querySelectorAll('[data-graphic-layout]').forEach((button) => {
    button.classList.toggle('active', button.dataset.graphicLayout === graphicState.layout);
  });
  document.querySelectorAll('[data-graphic-direction]').forEach((button) => {
    button.classList.toggle('active', button.dataset.graphicDirection === graphicState.direction);
  });
}

function graphicTranslationMarkup(page) {
  if (!graphicState.translationEnabled) return '';
  const regions = Array.isArray(page?.layers?.translations) ? page.layers.translations : [];
  return `<div class="graphic-translation-layer">${regions.map((region) => `<div class="graphic-translation-region style-${escapeHtml(region.style || 'bubble')}" style="left:${Number(region.x || 0) * 100}%;top:${Number(region.y || 0) * 100}%;width:${Number(region.width || 0) * 100}%;height:${Number(region.height || 0) * 100}%">${escapeHtml(region.text || '')}</div>`).join('')}</div>`;
}

function graphicPageMarkup(page) {
  const ratio = page.width > 0 && page.height > 0 ? `${page.width} / ${page.height}` : '3 / 4';
  const report = Number(page.id || 0) > 0 && !graphicState.meta?.moderation_access
    ? `<button class="graphic-page-report" type="button" data-report-graphic-page="${Number(page.id)}" aria-label="Сообщить о проблеме на странице">⚑ Сообщить</button>`
    : '';
  const bookmark = page?.layers?.bookmarked ? '<span class="graphic-page-bookmark-mark">★</span>' : '';
  return `<figure class="graphic-page" id="page-${page.number}" data-page-number="${page.number}" style="--page-ratio:${ratio}">
    <div class="graphic-page-placeholder"><span>Страница ${page.number}</span><button type="button" data-retry-graphic-page="${page.number}">Повторить</button></div>
    <img data-graphic-page="${page.number}" alt="Страница ${page.number}" loading="lazy" decoding="async">
    ${graphicTranslationMarkup(page)}${bookmark}${report}
  </figure>`;
}

function setupGraphicObserver() {
  graphicState.observer?.disconnect();
  const profile = graphicConnectionProfile();
  const rootMargin = profile.slow ? '250px 0px' : profile.medium ? '600px 0px' : '1100px 0px';
  graphicState.observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const pageNumber = Number(entry.target.dataset.pageNumber || 1);
      const index = Math.max(0, graphicState.pages.findIndex((page) => Number(page.number) === pageNumber));
      graphicState.currentIndex = index;
      updateGraphicProgress();
      scheduleGraphicProgressSave();
      const image = entry.target.querySelector('img[data-graphic-page]');
      const page = graphicState.pages[index];
      if (image && page) applyGraphicImageSource(image, page);
      preloadGraphicAround(index);
    });
  }, { rootMargin, threshold: 0.08 });
  document.querySelectorAll('.graphic-page').forEach((node) => graphicState.observer.observe(node));
}

function renderGraphicVertical() {
  const box = document.getElementById('graphicPages');
  box.className = `graphic-pages vertical direction-${graphicState.direction}`;
  box.innerHTML = graphicState.pages.map(graphicPageMarkup).join('');
  document.getElementById('graphicPageControls').hidden = true;
  setupGraphicObserver();
  const target = box.querySelector(`[data-page-number="${graphicState.pages[graphicState.currentIndex]?.number || 1}"]`);
  setTimeout(() => target?.scrollIntoView({ block: 'center' }), 120);
}

function currentSpreadPages() {
  if (graphicState.layout !== 'spread') return [graphicState.pages[graphicState.currentIndex]].filter(Boolean);
  const first = Math.floor(graphicState.currentIndex / 2) * 2;
  const pair = [graphicState.pages[first], graphicState.pages[first + 1]].filter(Boolean);
  return graphicState.direction === 'rtl' ? pair.reverse() : pair;
}

function renderGraphicPaged() {
  graphicState.observer?.disconnect();
  const box = document.getElementById('graphicPages');
  const pages = currentSpreadPages();
  box.className = `graphic-pages paged ${graphicState.layout} direction-${graphicState.direction}`;
  box.innerHTML = pages.map(graphicPageMarkup).join('');
  document.getElementById('graphicPageControls').hidden = false;
  pages.forEach((page) => {
    const image = box.querySelector(`img[data-graphic-page="${page.number}"]`);
    applyGraphicImageSource(image, page);
  });
  updateGraphicProgress();
  scheduleGraphicProgressSave();
  preloadGraphicAround(graphicState.currentIndex);
}

function renderGraphicPages() {
  updateGraphicSettingsButtons();
  if (graphicState.layout === 'vertical') renderGraphicVertical();
  else renderGraphicPaged();
}

function updateGraphicProgress() {
  const total = Math.max(1, graphicState.pages.length);
  const current = Math.min(total, graphicState.currentIndex + 1);
  const percent = Math.round((current / total) * 100);
  const bar = document.getElementById('graphicProgressBar');
  if (bar) bar.style.width = `${percent}%`;
  const indicator = document.getElementById('graphicPageIndicator');
  if (indicator) indicator.textContent = `${current} / ${total}`;
  const previous = document.getElementById('graphicPreviousPage');
  const next = document.getElementById('graphicNextPage');
  if (previous) previous.disabled = graphicState.currentIndex <= 0;
  if (next) next.disabled = graphicState.currentIndex >= total - 1;
  updateGraphicAdvancedButtons();
  const page = graphicState.pages[graphicState.currentIndex];
  if (page?.id && Number(page.id) !== Number(graphicState.lastEventPageId)) {
    graphicState.lastEventPageId = Number(page.id);
    sendGraphicReadingEvent(current >= total ? 'complete' : 'page_view', page).catch(() => {});
  }
}

function moveGraphicPage(delta) {
  const step = graphicState.layout === 'spread' ? 2 : 1;
  graphicState.currentIndex = Math.max(0, Math.min(graphicState.pages.length - 1, graphicState.currentIndex + delta * step));
  renderGraphicPaged();
}

function scheduleGraphicProgressSave() {
  clearTimeout(graphicState.saveTimer);
  graphicState.saveTimer = setTimeout(saveGraphicProgress, 500);
}

async function saveGraphicProgress() {
  if (!graphicState.chapterId || graphicState.meta?.moderation_access || graphicState.meta?.preview_only || !tgInitData()) return;
  const page = graphicState.pages[graphicState.currentIndex];
  if (!page) return;
  try {
    window.queueVoxProgressSync?.('graphic', Number(graphicState.chapterId), Number(page.number));
    await apiFetch(`/api/comic/${graphicState.chapterId}/progress`, {
      method: 'POST',
      keepalive: true,
      body: JSON.stringify({ page_number: page.number }),
    });
    window.dropVoxProgressSync?.('graphic', Number(graphicState.chapterId));
  } catch (_) {}
}

function graphicPreloadCount() {
  const profile = graphicConnectionProfile();
  if (profile.slow) return Math.max(0, Number(graphicState.meta?.delivery?.preload_slow ?? 1));
  if (profile.medium) return Math.max(1, Math.min(3, Number(graphicState.meta?.delivery?.preload_fast ?? 6)));
  return Math.max(2, Number(graphicState.meta?.delivery?.preload_fast ?? 6));
}

async function preloadGraphicAround(index) {
  const count = graphicPreloadCount();
  const positions = [];
  // Сначала текущая и следующая страницы, затем предыдущие. Это помогает и
  // обычному чтению, и возврату назад без последовательной блокировки сети.
  for (let offset = 0; offset <= count; offset += 1) {
    const forward = index + offset;
    const backward = index - offset;
    if (forward >= 0 && forward < graphicState.pages.length && !positions.includes(forward)) positions.push(forward);
    if (offset > 0 && backward >= 0 && backward < graphicState.pages.length && !positions.includes(backward)) positions.push(backward);
  }
  const jobs = positions.map((position) => async () => {
    const page = graphicState.pages[position];
    const runKey = `${graphicState.chapterId}:${page.number}`;
    if (graphicState.preloadRunning.has(runKey)) return;
    graphicState.preloadRunning.add(runKey);
    try {
      const image = document.querySelector(`img[data-graphic-page="${page.number}"]`);
      if (image) await applyGraphicImageSource(image, page);
      else await loadGraphicPageBlob(page);
    } catch (_) {}
    finally { graphicState.preloadRunning.delete(runKey); }
  });
  const concurrency = graphicConnectionProfile().slow ? 1 : 3;
  for (let offset = 0; offset < jobs.length; offset += concurrency) {
    await Promise.allSettled(jobs.slice(offset, offset + concurrency).map((job) => job()));
  }
}

async function requestPersistentGraphicStorage() {
  try {
    if (navigator.storage?.persist) await navigator.storage.persist();
  } catch (_) {}
}

async function cacheGraphicMetaShell(meta) {
  const chapterId = Number(meta?.chapter?.id || 0);
  if (!chapterId) return;
  saveCachedGraphicMeta(meta, chapterId);
  try {
    const registration = await navigator.serviceWorker?.ready;
    registration?.active?.postMessage({ type: 'CACHE_COMIC_SHELL', urls: [`/comic/${chapterId}`] });
  } catch (_) {}
}

async function cacheMetaPages(meta, progressCallback = null) {
  const chapterId = Number(meta?.chapter?.id || 0);
  const pages = Array.isArray(meta?.pages) ? meta.pages : [];
  await cacheGraphicMetaShell(meta);
  for (let index = 0; index < pages.length; index += 1) {
    const page = pages[index];
    page._chapterId = chapterId;
    const variant = chooseGraphicVariant(page);
    const key = graphicPageCacheKey(page, chapterId, variant);
    const cached = await graphicCacheGet(key);
    if (!cached?.blob) {
      await loadGraphicPageBlob(page, { forceNetwork: true, pinned: true, chapterId, meta });
    } else if (!cached.pinned) {
      await graphicCacheMarkPrefixPinned(key, true);
    }
    if (progressCallback) progressCallback(index + 1, pages.length);
  }
}

async function cacheEntireGraphicChapter() {
  if (graphicState.cacheAllRunning || graphicState.meta?.moderation_access || !graphicState.meta?.protection?.allow_download) {
    notify('Автор разрешил только чтение внутри Вокслиры');
    return;
  }
  graphicState.cacheAllRunning = true;
  const button = document.getElementById('graphicCacheChapter');
  if (button) button.disabled = true;
  await requestPersistentGraphicStorage();
  try {
    await cacheMetaPages(graphicState.meta, (current, total) => {
      const status = document.getElementById('graphicReaderStatus');
      if (status) status.textContent = `Сохраняем главу: ${current} из ${total}`;
    });
    notify('Глава сохранена на устройстве');
    document.getElementById('graphicReaderStatus').textContent = 'Глава доступна офлайн на этом устройстве';
  } catch (error) {
    notify(error.message || 'Не удалось сохранить все страницы');
  } finally {
    graphicState.cacheAllRunning = false;
    if (button) button.disabled = false;
    updateGraphicCacheStatus();
  }
}

async function cacheGraphicVolume() {
  if (graphicState.cacheAllRunning || !graphicState.meta?.protection?.allow_download || graphicState.meta?.moderation_access) return;
  const bookId = Number(graphicState.meta?.chapter?.book_id || 0);
  const volume = Number(graphicState.meta?.chapter?.volume_number || 1);
  const button = document.getElementById('graphicCacheVolume');
  graphicState.cacheAllRunning = true;
  if (button) button.disabled = true;
  await requestPersistentGraphicStorage();
  try {
    const manifest = await apiFetchWithRetry(`/api/comic/book/${bookId}/offline-manifest?volume=${volume}`, {}, 4, 30000);
    const chapters = Array.isArray(manifest.chapters) ? manifest.chapters : [];
    const totalPages = chapters.reduce((sum, item) => sum + Number(item.meta?.pages?.length || 0), 0);
    let completed = 0;
    for (const item of chapters) {
      await cacheMetaPages(item.meta, () => {
        completed += 1;
        document.getElementById('graphicReaderStatus').textContent = `Сохраняем том ${volume}: ${completed} из ${totalPages}`;
      });
    }
    notify(`Том ${volume} сохранён на устройстве`);
    document.getElementById('graphicReaderStatus').textContent = `Том ${volume} доступен офлайн на этом устройстве`;
  } catch (error) {
    notify(error.message || 'Не удалось сохранить том');
  } finally {
    graphicState.cacheAllRunning = false;
    if (button) button.disabled = false;
    updateGraphicCacheStatus();
  }
}

async function clearGraphicChapterCache() {
  await graphicCacheDeletePrefix(`chapter:${graphicState.chapterId}:`);
  notify('Кэш главы очищен');
  updateGraphicCacheStatus();
}

async function clearGraphicVolumeCache() {
  const bookId = Number(graphicState.meta?.chapter?.book_id || 0);
  const volume = Number(graphicState.meta?.chapter?.volume_number || 1);
  const entries = await listGraphicCacheEntries();
  const keys = entries.filter((item) => Number(item.bookId) === bookId && Number(item.volumeNumber || 1) === volume).map((item) => item.key);
  await deleteGraphicCacheKeys(keys);
  notify(`Офлайн-кэш тома ${volume} очищен`);
  updateGraphicCacheStatus();
}

function updateGraphicNavigation(meta) {
  const nav = document.getElementById('graphicChapterNavigation');
  if (!nav) return;
  const previous = meta.navigation?.previous;
  const next = meta.navigation?.next;
  nav.innerHTML = `${previous ? `<a href="/comic/${Number(previous.id)}"><small>Предыдущая глава</small><strong>← ${escapeHtml(previous.title)}</strong></a>` : '<span></span>'}${next ? `<a href="/comic/${Number(next.id)}"><small>Следующая глава</small><strong>${escapeHtml(next.title)} →</strong></a>` : `<a href="/book/${Number(meta.chapter.book_id)}"><small>Конец</small><strong>К произведению →</strong></a>`}`;
}

async function registerGraphicServiceWorker() {
  if (!('serviceWorker' in navigator)) return;
  try { await navigator.serviceWorker.register('/comic-sw.js', { scope: '/' }); } catch (_) {}
}

function updateGraphicNetworkStatus() {
  const status = document.getElementById('graphicNetworkStatus');
  if (!status) return;
  const profile = graphicConnectionProfile();
  if (navigator.onLine === false) status.textContent = 'Нет сети — используем сохранённые страницы';
  else if (profile.slow) status.textContent = 'Слабая сеть — загружаем облегчённые страницы';
  else if (profile.medium) status.textContent = 'Нестабильная сеть — умеренная предзагрузка';
  else status.textContent = 'Соединение нормальное — страницы подгружаются заранее';
}

async function initGraphicReader() {
  const root = document.getElementById('graphicReader');
  if (!root) return;
  graphicState.chapterId = Number(root.dataset.chapterId || 0);
  const status = document.getElementById('graphicReaderStatus');
  await registerGraphicServiceWorker();
  updateGraphicNetworkStatus();
  if (!tgInitData()) {
    status.textContent = 'Откройте главу через Mini App в Telegram.';
    return;
  }
  try {
    let meta = null;
    let offlineMeta = false;
    try {
      const initialLanguage = localStorage.getItem('voxGraphicTranslationLanguage') || 'ru';
      graphicState.translationLanguage = initialLanguage;
      meta = await apiFetchWithRetry(`/api/comic/${graphicState.chapterId}?language=${encodeURIComponent(initialLanguage)}`, {}, 4, 25000);
      saveCachedGraphicMeta(meta);
    } catch (networkError) {
      meta = loadCachedGraphicMeta();
      if (!meta) throw networkError;
      offlineMeta = true;
    }
    graphicState.meta = meta;
    if (!meta.allowed) {
      const packageRemaining = Number(meta.package_credits?.remaining || 0);
      status.innerHTML = `<section class="comic-paywall-card"><h3>Графическая глава закрыта</h3><p>${packageRemaining > 0 ? `В пакете осталось <b>${packageRemaining}</b> открытий.` : 'Можно купить главу отдельно или выбрать пакет.'}</p>${packageRemaining > 0 ? `<button class="button-link gold-button" id="unlockGraphicWithPackage" type="button">Открыть за 1 главу из пакета</button>` : ''}${meta.purchase_url ? ` <a class="button-link secondary" href="${escapeHtml(meta.purchase_url)}">Купить эту главу</a>` : ''}<a class="quiet-link" href="/book/${Number(meta.chapter?.book_id || 0)}#chapterPackages">Посмотреть пакеты</a></section>`;
      const unlockButton = document.getElementById('unlockGraphicWithPackage');
      if (unlockButton) {
        unlockButton.addEventListener('click', async () => {
          if (!window.confirm(`Списать 1 открытие из пакета? Останется ${Math.max(0, packageRemaining - 1)}.`)) return;
          unlockButton.disabled = true;
          unlockButton.textContent = 'Открываем…';
          try {
            await apiFetch(meta.package_unlock_url || `/api/comic/${graphicState.chapterId}/unlock-package`, { method: 'POST' });
            window.location.reload();
          } catch (error) {
            unlockButton.disabled = false;
            unlockButton.textContent = 'Открыть за 1 главу из пакета';
            status.insertAdjacentHTML('beforeend', `<p class="error-text">${escapeHtml(error.message || 'Не удалось использовать пакет')}</p>`);
          }
        });
      }
      return;
    }
    graphicState.pages = Array.isArray(meta.pages) ? meta.pages : [];
    graphicState.pages.forEach((page) => { page._chapterId = graphicState.chapterId; });
    if (!graphicState.pages.length) {
      status.textContent = 'В этой главе пока нет страниц.';
      return;
    }
    restoreGraphicPreferences(meta);
    restoreGraphicAdvancedPreferences();
    const hashMatch = String(window.location.hash || '').match(/^#page-(\d+)$/);
    const targetPage = hashMatch ? Number(hashMatch[1]) : Number(meta.progress_page || 1);
    const progressIndex = graphicState.pages.findIndex((page) => Number(page.number) === targetPage);
    graphicState.currentIndex = Math.max(0, progressIndex);
    document.getElementById('graphicBookTitle').textContent = meta.chapter.book_title || 'Произведение';
    const volumeLabel = Number(meta.chapter.volume_number || 1) > 0 ? `Том ${Number(meta.chapter.volume_number || 1)} · ` : '';
    document.getElementById('graphicChapterTitle').textContent = `${volumeLabel}${meta.chapter.title || 'Глава'}`;
    document.getElementById('graphicModerationNotice').hidden = !meta.moderation_access;
    const previewNotice = document.getElementById('graphicPreviewNotice');
    if (previewNotice) previewNotice.hidden = !meta.preview_only;
    if (meta.preview_only) {
      const text = document.getElementById('graphicPreviewText');
      const buy = document.getElementById('graphicPreviewBuy');
      const packageRemaining = Number(meta.package_credits?.remaining || 0);
      if (text) text.textContent = `Показано ${graphicState.pages.length} бесплатных страниц. ${packageRemaining > 0 ? `В пакете осталось ${packageRemaining} открытий.` : 'Прогресс и офлайн-кэш для предпросмотра не сохраняются.'}`;
      if (buy) {
        if (packageRemaining > 0) {
          buy.removeAttribute('href');
          buy.textContent = 'Открыть главу из пакета';
          buy.onclick = async (event) => {
            event.preventDefault();
            if (!window.confirm(`Списать 1 открытие из пакета? Останется ${Math.max(0, packageRemaining - 1)}.`)) return;
            try {
              await apiFetch(meta.package_unlock_url || `/api/comic/${graphicState.chapterId}/unlock-package`, { method: 'POST' });
              window.location.reload();
            } catch (error) { status.textContent = error.message || 'Не удалось использовать пакет'; }
          };
        } else {
          buy.href = meta.purchase_url || '#';
          buy.textContent = 'Купить главу';
        }
      }
    }
    const canStore = Boolean(meta.protection?.allow_download && !meta.moderation_access && !meta.preview_only);
    ['graphicCacheChapter', 'graphicCacheVolume', 'graphicClearChapterCache', 'graphicClearVolumeCache'].forEach((id) => {
      const node = document.getElementById(id);
      if (node) node.hidden = !canStore;
    });
    window.applyContentProtection?.(meta.protection, document.getElementById('graphicPages'));
    status.textContent = offlineMeta ? `Без сети · ${graphicState.pages.length} стр. из кэша устройства` : `${meta.preview_only ? 'Предпросмотр' : (meta.content_type_label || 'Графическая глава')} · ${graphicState.pages.length} стр.${meta.protection?.protected ? ' · защищено автором' : ''}`;
    updateGraphicNavigation(meta);
    updateGraphicCacheStatus();
    renderGraphicPages();
    sendGraphicReadingEvent('open', currentGraphicPage()).catch(() => {});
  } catch (error) {
    status.textContent = error.message || 'Не удалось открыть страницы.';
  }
}


function currentGraphicPage() {
  return graphicState.pages[graphicState.currentIndex] || null;
}

function updateGraphicAdvancedButtons() {
  const page = currentGraphicPage();
  const bookmark = document.getElementById('graphicCurrentBookmark');
  if (bookmark) bookmark.textContent = page?.layers?.bookmarked ? '★ Закладка' : '☆ Закладка';
  const translation = document.getElementById('graphicTranslationToggle');
  if (translation) translation.textContent = `Перевод: ${graphicState.translationEnabled ? 'вкл.' : 'выкл.'}`;
  const frame = document.getElementById('graphicFrameMode');
  if (frame) {
    const count = Array.isArray(page?.layers?.frames) ? page.layers.frames.length : 0;
    frame.disabled = count <= 0;
    frame.textContent = count > 0 ? `Покадрово · ${count}` : 'Покадрово недоступно';
  }
}

async function sendGraphicReadingEvent(eventType, page = null) {
  if (!tgInitData() || graphicState.meta?.moderation_access || graphicState.meta?.preview_only) return;
  await apiFetch(`/api/comic/${graphicState.chapterId}/event`, {
    method: 'POST',
    body: JSON.stringify({
      event_type: eventType,
      graphic_page_id: Number(page?.id || 0) || null,
      session_key: graphicState.sessionKey,
    }),
  });
}

async function toggleCurrentGraphicBookmark() {
  const page = currentGraphicPage();
  if (!page?.id) return;
  try {
    const result = await apiFetch(`/api/comic/page/${Number(page.id)}/bookmark`, {
      method: 'POST', body: JSON.stringify({}),
    });
    page.layers = page.layers || {};
    page.layers.bookmarked = Boolean(result.bookmarked);
    updateGraphicAdvancedButtons();
    renderGraphicPages();
    notify(result.bookmarked ? 'Страница добавлена в закладки' : 'Закладка удалена');
  } catch (error) { notify(error.message || 'Не удалось изменить закладку'); }
}

function renderGraphicComments(page) {
  const list = document.getElementById('graphicCommentsList');
  const title = document.getElementById('graphicCommentsTitle');
  if (title) title.textContent = `Комментарии · страница ${Number(page?.number || 0)}`;
  const comments = Array.isArray(page?.layers?.comments) ? page.layers.comments : [];
  if (list) list.innerHTML = comments.length
    ? comments.map((item) => `<article class="graphic-comment-card"><strong>${escapeHtml(item.username ? '@' + item.username : item.full_name || 'Читатель')}</strong><p>${escapeHtml(item.text || '')}</p></article>`).join('')
    : '<p class="muted-text">Опубликованных комментариев пока нет.</p>';
}

function openGraphicComments() {
  const page = currentGraphicPage();
  if (!page?.id) return;
  renderGraphicComments(page);
  const drawer = document.getElementById('graphicCommentsDrawer');
  if (drawer) drawer.hidden = false;
}

async function sendGraphicComment() {
  const page = currentGraphicPage();
  const input = document.getElementById('graphicCommentText');
  const text = String(input?.value || '').trim();
  if (!page?.id || text.length < 2) { notify('Напишите комментарий'); return; }
  const button = document.getElementById('graphicCommentSend');
  if (button) button.disabled = true;
  try {
    const result = await apiFetch(`/api/comic/page/${Number(page.id)}/comments`, {
      method: 'POST', body: JSON.stringify({ text }),
    });
    if (input) input.value = '';
    notify(result.message || 'Комментарий отправлен на модерацию');
  } catch (error) { notify(error.message || 'Не удалось отправить комментарий'); }
  finally { if (button) button.disabled = false; }
}

async function searchGraphicText() {
  const input = document.getElementById('graphicSearchInput');
  const results = document.getElementById('graphicSearchResults');
  const query = String(input?.value || '').trim();
  if (query.length < 2) { notify('Введите не меньше двух символов'); return; }
  if (results) { results.hidden = false; results.innerHTML = '<p>Ищем…</p>'; }
  try {
    const bookId = Number(graphicState.meta?.chapter?.book_id || 0);
    const data = await apiFetch(`/api/comic/book/${bookId}/search?q=${encodeURIComponent(query)}&language=${encodeURIComponent(graphicState.translationLanguage)}`);
    const items = Array.isArray(data.items) ? data.items : [];
    if (!results) return;
    results.innerHTML = items.length ? items.map((item) => `<button class="graphic-search-result" type="button" data-graphic-search-chapter="${Number(item.graphic_chapter_id)}" data-graphic-search-page="${Number(item.page_number)}"><strong>Том ${Number(item.volume_number || 1)} · глава ${Number(item.chapter_number || 0)}</strong><span>Страница ${Number(item.page_number)} · ${escapeHtml(item.snippet || '')}</span></button>`).join('') : '<p>Совпадений в доступных главах нет.</p>';
  } catch (error) {
    if (results) results.innerHTML = `<p class="error-text">${escapeHtml(error.message || 'Поиск не выполнен')}</p>`;
  }
}

function goToGraphicSearchResult(chapterId, pageNumber) {
  if (Number(chapterId) !== Number(graphicState.chapterId)) {
    window.location.href = `/comic/${Number(chapterId)}#page-${Number(pageNumber)}`;
    return;
  }
  const index = graphicState.pages.findIndex((page) => Number(page.number) === Number(pageNumber));
  if (index < 0) return;
  graphicState.currentIndex = index;
  if (graphicState.layout === 'vertical') {
    document.getElementById(`page-${Number(pageNumber)}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } else renderGraphicPaged();
}

function buildGraphicFrameItems() {
  const items = [];
  graphicState.pages.forEach((page, pageIndex) => {
    const frames = Array.isArray(page?.layers?.frames) ? page.layers.frames : [];
    frames.forEach((frame, frameIndex) => items.push({ page, pageIndex, frame, frameIndex }));
  });
  graphicState.frameItems = items;
  const currentPage = currentGraphicPage();
  const first = items.findIndex((item) => Number(item.page?.id || 0) === Number(currentPage?.id || -1));
  graphicState.frameIndex = Math.max(0, first);
}

async function drawCurrentGraphicFrame() {
  const item = graphicState.frameItems[graphicState.frameIndex];
  const canvas = document.getElementById('graphicFrameCanvas');
  if (!item || !canvas) return;
  const result = await loadGraphicPageBlob(item.page);
  const bitmap = await createImageBitmap(result.blob);
  const frame = item.frame;
  const sx = Math.max(0, Math.round(Number(frame.x || 0) * bitmap.width));
  const sy = Math.max(0, Math.round(Number(frame.y || 0) * bitmap.height));
  const sw = Math.max(1, Math.min(bitmap.width - sx, Math.round(Number(frame.width || 1) * bitmap.width)));
  const sh = Math.max(1, Math.min(bitmap.height - sy, Math.round(Number(frame.height || 1) * bitmap.height)));
  const maxWidth = Math.max(320, Math.min(window.innerWidth - 24, 1200));
  const maxHeight = Math.max(320, Math.min(window.innerHeight * 0.72, 1000));
  const scale = Math.min(maxWidth / sw, maxHeight / sh);
  canvas.width = Math.max(1, Math.round(sw * scale));
  canvas.height = Math.max(1, Math.round(sh * scale));
  const context = canvas.getContext('2d');
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(bitmap, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  bitmap.close?.();
  graphicState.currentIndex = Number(item.pageIndex);
  document.getElementById('graphicFrameIndicator').textContent = `Кадр ${graphicState.frameIndex + 1} / ${graphicState.frameItems.length}`;
  document.getElementById('graphicFramePageLabel').textContent = `Страница ${Number(item.page.number)} · кадр ${Number(item.frameIndex) + 1}`;
  await sendGraphicReadingEvent('frame_view', item.page).catch(() => {});
  updateGraphicProgress();
}

async function enterGraphicFrameMode() {
  buildGraphicFrameItems();
  if (!graphicState.frameItems.length) { notify('Для этой главы автор ещё не настроил кадры'); return; }
  graphicState.frameMode = true;
  document.getElementById('graphicPages').hidden = true;
  document.getElementById('graphicPageControls').hidden = true;
  document.getElementById('graphicFrameViewer').hidden = false;
  await drawCurrentGraphicFrame();
}

function exitGraphicFrameMode() {
  graphicState.frameMode = false;
  document.getElementById('graphicFrameViewer').hidden = true;
  document.getElementById('graphicPages').hidden = false;
  renderGraphicPages();
}

async function moveGraphicFrame(delta) {
  if (!graphicState.frameItems.length) return;
  graphicState.frameIndex = Math.max(0, Math.min(graphicState.frameItems.length - 1, graphicState.frameIndex + delta));
  await drawCurrentGraphicFrame();
}

function restoreGraphicAdvancedPreferences() {
  graphicState.translationLanguage = localStorage.getItem('voxGraphicTranslationLanguage') || 'ru';
  graphicState.translationEnabled = localStorage.getItem('voxGraphicTranslationEnabled') === '1';
  const language = document.getElementById('graphicTranslationLanguage');
  if (language) language.value = graphicState.translationLanguage;
}

function bindGraphicReaderEvents() {
  document.getElementById('graphicSettingsToggle')?.addEventListener('click', (event) => {
    const panel = document.getElementById('graphicReaderSettings');
    panel.hidden = !panel.hidden;
    event.currentTarget.setAttribute('aria-expanded', panel.hidden ? 'false' : 'true');
  });
  document.querySelectorAll('[data-graphic-layout]').forEach((button) => button.addEventListener('click', () => {
    graphicState.layout = button.dataset.graphicLayout;
    localStorage.setItem(graphicPreferenceKey('layout'), graphicState.layout);
    releaseGraphicObjectUrls();
    renderGraphicPages();
  }));
  document.querySelectorAll('[data-graphic-direction]').forEach((button) => button.addEventListener('click', () => {
    graphicState.direction = button.dataset.graphicDirection;
    localStorage.setItem(graphicPreferenceKey('direction'), graphicState.direction);
    renderGraphicPages();
  }));
  document.getElementById('graphicPreviousPage')?.addEventListener('click', () => moveGraphicPage(-1));
  document.getElementById('graphicNextPage')?.addEventListener('click', () => moveGraphicPage(1));
  document.getElementById('graphicCacheChapter')?.addEventListener('click', cacheEntireGraphicChapter);
  document.getElementById('graphicCacheVolume')?.addEventListener('click', cacheGraphicVolume);
  document.getElementById('graphicClearChapterCache')?.addEventListener('click', clearGraphicChapterCache);
  document.getElementById('graphicClearVolumeCache')?.addEventListener('click', clearGraphicVolumeCache);
  document.getElementById('graphicPages')?.addEventListener('click', async (event) => {
    const report = event.target.closest('[data-report-graphic-page]');
    if (report) {
      event.preventDefault();
      event.stopPropagation();
      const reason = window.prompt('Что не так с этой страницей? Например: отсутствует текст, неправильный порядок, запрещённое содержимое.');
      if (reason === null) return;
      if (reason.trim().length < 5) { notify('Опишите проблему немного подробнее'); return; }
      try {
        await apiFetch(`/api/comic/page/${Number(report.dataset.reportGraphicPage)}/report`, { method: 'POST', body: JSON.stringify({ reason: reason.trim() }) });
        notify('Сообщение отправлено модератору');
      } catch (error) { notify(error.message || 'Не удалось отправить сообщение'); }
      return;
    }
    const retry = event.target.closest('[data-retry-graphic-page]');
    if (!retry) return;
    const pageNumber = Number(retry.dataset.retryGraphicPage || 0);
    const page = graphicState.pages.find((item) => Number(item.number) === pageNumber);
    const image = document.querySelector(`img[data-graphic-page="${pageNumber}"]`);
    if (page && image) applyGraphicImageSource(image, page, { retry: true });
  });
  document.getElementById('graphicPages')?.addEventListener('dblclick', (event) => {
    event.target.closest('.graphic-page')?.classList.toggle('zoomed');
  });
  document.getElementById('graphicTranslationToggle')?.addEventListener('click', () => {
    graphicState.translationEnabled = !graphicState.translationEnabled;
    localStorage.setItem('voxGraphicTranslationEnabled', graphicState.translationEnabled ? '1' : '0');
    renderGraphicPages();
  });
  document.getElementById('graphicTranslationLanguage')?.addEventListener('change', async (event) => {
    graphicState.translationLanguage = event.target.value || 'ru';
    localStorage.setItem('voxGraphicTranslationLanguage', graphicState.translationLanguage);
    try {
      const meta = await refreshGraphicMeta();
      if (meta?.pages) { graphicState.pages = meta.pages; renderGraphicPages(); }
    } catch (_) {}
  });
  document.getElementById('graphicFrameMode')?.addEventListener('click', enterGraphicFrameMode);
  document.getElementById('graphicExitFrameMode')?.addEventListener('click', exitGraphicFrameMode);
  document.getElementById('graphicPreviousFrame')?.addEventListener('click', () => moveGraphicFrame(-1));
  document.getElementById('graphicNextFrame')?.addEventListener('click', () => moveGraphicFrame(1));
  document.getElementById('graphicCurrentBookmark')?.addEventListener('click', toggleCurrentGraphicBookmark);
  document.getElementById('graphicCurrentComments')?.addEventListener('click', openGraphicComments);
  document.getElementById('graphicCommentsClose')?.addEventListener('click', () => { document.getElementById('graphicCommentsDrawer').hidden = true; });
  document.getElementById('graphicCommentSend')?.addEventListener('click', sendGraphicComment);
  document.getElementById('graphicSearchButton')?.addEventListener('click', searchGraphicText);
  document.getElementById('graphicSearchInput')?.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); searchGraphicText(); } });
  document.getElementById('graphicSearchResults')?.addEventListener('click', (event) => {
    const item = event.target.closest('[data-graphic-search-chapter]');
    if (item) goToGraphicSearchResult(Number(item.dataset.graphicSearchChapter), Number(item.dataset.graphicSearchPage));
  });
  let touchStartX = 0;
  document.getElementById('graphicPages')?.addEventListener('touchstart', (event) => {
    touchStartX = event.changedTouches?.[0]?.clientX || 0;
  }, { passive: true });
  document.getElementById('graphicPages')?.addEventListener('touchend', (event) => {
    if (graphicState.layout === 'vertical') return;
    const endX = event.changedTouches?.[0]?.clientX || 0;
    const distance = endX - touchStartX;
    if (Math.abs(distance) < 55) return;
    const forward = graphicState.direction === 'rtl' ? distance > 0 : distance < 0;
    moveGraphicPage(forward ? 1 : -1);
  }, { passive: true });
  window.addEventListener('online', () => {
    updateGraphicNetworkStatus();
    const retryFailedPages = async () => {
      if (graphicState.lastNetworkErrorAt && Date.now() - graphicState.lastNetworkErrorAt > 30_000) {
        await refreshGraphicMeta().catch(() => null);
      }
      const failed = document.querySelectorAll('.graphic-page.is-error img[data-graphic-page]');
      failed.forEach((image) => {
        const page = graphicState.pages.find((item) => Number(item.number) === Number(image.dataset.graphicPage));
        if (page) applyGraphicImageSource(image, page, { retry: true });
      });
    };
    retryFailedPages().catch(() => {});
  });
  window.addEventListener('offline', updateGraphicNetworkStatus);
  navigator.connection?.addEventListener?.('change', updateGraphicNetworkStatus);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') saveGraphicProgress().catch(() => {});
  });
  window.addEventListener('pagehide', () => {
    saveGraphicProgress().catch(() => {});
    sendGraphicReadingEvent('exit', currentGraphicPage()).catch(() => {});
    releaseGraphicObjectUrls();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('graphicReader')) return;
  bindGraphicReaderEvents();
  initGraphicReader();
});
