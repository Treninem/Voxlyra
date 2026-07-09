(function initTelegram() {
  const tg = window.Telegram?.WebApp;
  if (!tg) return;
  try {
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.('#090b18');
    tg.setBackgroundColor?.('#090b18');
  } catch (_) {}
})();

const root = document.documentElement;
const DEFAULTS = {
  theme: 'system', fontSize: 18, lineHeight: 1.78, readerWidth: 'normal',
  audioRate: 1, rewindStep: 15, autoplayNext: false, saveOnPause: true,
  notifications: true, notificationChapters: true, notificationAudio: true, notificationDiscounts: true,
  contrast: 'normal', focusMode: false, showReaderAds: true,
};
let meDataPromise = null;
let autoProgressTimer = null;
let sleepTimerHandle = null;
let sleepTimerEndsAt = 0;

function getStoredBool(key, fallback) {
  const value = localStorage.getItem(key);
  if (value === null) return fallback;
  return value === '1' || value === 'true';
}

function getPrefs() {
  return {
    theme: localStorage.getItem('voxTheme') || DEFAULTS.theme,
    fontSize: Number(localStorage.getItem('readerFontSize') || DEFAULTS.fontSize),
    lineHeight: Number(localStorage.getItem('readerLineHeight') || DEFAULTS.lineHeight),
    readerWidth: localStorage.getItem('readerWidth') || DEFAULTS.readerWidth,
    audioRate: Number(localStorage.getItem('voxAudioRate') || DEFAULTS.audioRate),
    rewindStep: Number(localStorage.getItem('voxRewindStep') || DEFAULTS.rewindStep),
    autoplayNext: getStoredBool('voxAutoplayNext', DEFAULTS.autoplayNext),
    saveOnPause: getStoredBool('voxSaveOnPause', DEFAULTS.saveOnPause),
    notifications: getStoredBool('voxNotifications', DEFAULTS.notifications),
    notificationChapters: getStoredBool('voxNotificationChapters', DEFAULTS.notificationChapters),
    notificationAudio: getStoredBool('voxNotificationAudio', DEFAULTS.notificationAudio),
    notificationDiscounts: getStoredBool('voxNotificationDiscounts', DEFAULTS.notificationDiscounts),
    contrast: localStorage.getItem('voxContrast') || DEFAULTS.contrast,
    focusMode: getStoredBool('voxFocusMode', DEFAULTS.focusMode),
    showReaderAds: getStoredBool('voxShowReaderAds', DEFAULTS.showReaderAds),
  };
}

function setPref(key, value) {
  const map = {
    theme: 'voxTheme', fontSize: 'readerFontSize', lineHeight: 'readerLineHeight',
    readerWidth: 'readerWidth', audioRate: 'voxAudioRate', rewindStep: 'voxRewindStep',
    autoplayNext: 'voxAutoplayNext', saveOnPause: 'voxSaveOnPause',
    notifications: 'voxNotifications', notificationChapters: 'voxNotificationChapters',
    notificationAudio: 'voxNotificationAudio', notificationDiscounts: 'voxNotificationDiscounts',
    contrast: 'voxContrast', focusMode: 'voxFocusMode',
    showReaderAds: 'voxShowReaderAds',
  };
  localStorage.setItem(map[key], typeof value === 'boolean' ? (value ? '1' : '0') : String(value));
  applySettings();
}

function notify(message) {
  const tg = window.Telegram?.WebApp;
  try { tg?.HapticFeedback?.impactOccurred('light'); } catch (_) {}
  const toast = document.getElementById('toast');
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => toast.classList.remove('show'), 2200);
}

function applyTheme(theme = getPrefs().theme) {
  document.body.classList.remove('light-theme', 'dark-theme', 'sepia-theme');
  const tg = window.Telegram?.WebApp;
  const resolved = theme === 'system' ? (tg?.colorScheme === 'light' ? 'light' : 'dark') : theme;
  document.body.classList.add(`${resolved}-theme`);
  localStorage.setItem('voxTheme', theme);
}

function applySettings() {
  const prefs = getPrefs();
  applyTheme(prefs.theme);
  root.style.setProperty('--reader-font-size', `${Math.max(14, Math.min(28, prefs.fontSize))}px`);
  root.style.setProperty('--reader-line-height', String(Math.max(1.45, Math.min(2.15, prefs.lineHeight))));
  document.body.classList.toggle('reader-wide', prefs.readerWidth === 'wide');
  document.body.classList.toggle('high-contrast', prefs.contrast === 'high');
  document.body.classList.toggle('focus-mode', Boolean(prefs.focusMode));
  document.body.classList.toggle('ads-hidden', !Boolean(prefs.showReaderAds));

  document.querySelectorAll('[data-theme]').forEach((btn) => btn.classList.toggle('active', btn.dataset.theme === prefs.theme));
  document.querySelectorAll('[data-line-height]').forEach((btn) => btn.classList.toggle('active', Number(btn.dataset.lineHeight) === prefs.lineHeight));
  document.querySelectorAll('[data-reader-width]').forEach((btn) => btn.classList.toggle('active', btn.dataset.readerWidth === prefs.readerWidth));
  document.querySelectorAll('[data-rate]').forEach((btn) => btn.classList.toggle('active-rate', Number(btn.dataset.rate) === prefs.audioRate));
  document.querySelectorAll('[data-rewind]').forEach((btn) => btn.classList.toggle('active', Number(btn.dataset.rewind) === prefs.rewindStep));
  document.querySelectorAll('[data-contrast]').forEach((btn) => btn.classList.toggle('active', btn.dataset.contrast === prefs.contrast));
  document.querySelectorAll('[data-toggle]').forEach((btn) => {
    const enabled = Boolean(prefs[btn.dataset.toggle]);
    btn.classList.toggle('active', enabled);
    const labels = btn.querySelectorAll('span');
    if (labels.length > 1) labels[labels.length - 1].textContent = enabled ? 'Включено' : 'Выключено';
  });

  const fontValue = document.getElementById('fontValue');
  if (fontValue) fontValue.textContent = `${prefs.fontSize}px`;
  const lineValue = document.getElementById('lineHeightValue');
  if (lineValue) lineValue.textContent = prefs.lineHeight === 1.58 ? 'Компактно' : prefs.lineHeight === 1.95 ? 'Просторно' : 'Обычно';
  const rewindValue = document.getElementById('rewindValue');
  if (rewindValue) rewindValue.textContent = `${prefs.rewindStep} сек.`;
  const preview = document.getElementById('settingsPreviewText');
  if (preview) { preview.style.fontSize = `${prefs.fontSize}px`; preview.style.lineHeight = String(prefs.lineHeight); }
  const player = document.getElementById('voxPlayer');
  if (player) player.playbackRate = prefs.audioRate;
  document.querySelectorAll('#audioBack').forEach((btn) => { btn.textContent = `−${prefs.rewindStep}`; });
  document.querySelectorAll('#audioForward').forEach((btn) => { btn.textContent = `+${prefs.rewindStep}`; });
}

function changeFont(delta) {
  setPref('fontSize', Math.max(14, Math.min(28, getPrefs().fontSize + delta)));
}

async function resetSettings() {
  ['voxTheme','readerFontSize','readerLineHeight','readerWidth','voxAudioRate','voxRewindStep','voxAutoplayNext','voxSaveOnPause','voxNotifications','voxNotificationChapters','voxNotificationAudio','voxNotificationDiscounts','voxContrast','voxFocusMode','voxShowReaderAds'].forEach((key) => localStorage.removeItem(key));
  applySettings();
  if (tgInitData()) {
    try { await apiFetch('/api/preferences', { method: 'DELETE' }); }
    catch (_) { notify('Локальные настройки сброшены'); return; }
  }
  notify('Настройки возвращены к исходным');
}

function tgInitData() { return window.Telegram?.WebApp?.initData || ''; }

async function apiFetch(url, options = {}) {
  const headers = Object.assign({}, options.headers || {}, { 'X-Telegram-Init-Data': tgInitData() });
  if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, Object.assign({}, options, { headers }));
  if (!response.ok) {
    let message = 'Не удалось выполнить действие';
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const contentType = response.headers.get('content-type') || '';
  return contentType.includes('application/json') ? response.json() : response;
}

const SERVER_NOTIFICATION_KEYS = {
  notifications: 'notifications',
  notificationChapters: 'notifications_chapters',
  notificationAudio: 'notifications_audio',
  notificationDiscounts: 'notifications_discounts',
};

async function saveNotificationPreference(key, enabled) {
  const serverKey = SERVER_NOTIFICATION_KEYS[key];
  if (!serverKey || !tgInitData()) return;
  await apiFetch('/api/preferences', {
    method: 'PATCH',
    body: JSON.stringify({ key: serverKey, value: enabled ? '1' : '0' }),
  });
}

async function syncNotificationPreferences() {
  if (!tgInitData()) return;
  try {
    const data = await apiFetch('/api/preferences');
    const prefs = data.preferences || {};
    const values = {
      notifications: prefs.notifications !== '0',
      notificationChapters: prefs.notifications_chapters !== '0',
      notificationAudio: prefs.notifications_audio !== '0',
      notificationDiscounts: prefs.notifications_discounts !== '0',
    };
    Object.entries(values).forEach(([key, value]) => {
      const storageKey = {
        notifications: 'voxNotifications', notificationChapters: 'voxNotificationChapters',
        notificationAudio: 'voxNotificationAudio', notificationDiscounts: 'voxNotificationDiscounts',
      }[key];
      localStorage.setItem(storageKey, value ? '1' : '0');
    });
    applySettings();
  } catch (_) {}
}

function loadMeData() {
  if (!meDataPromise) meDataPromise = apiFetch('/api/me');
  return meDataPromise;
}

function escapeHtml(value) {
  return String(value ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;');
}

function formatTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(value / 3600);
  const m = Math.floor((value % 3600) / 60);
  const s = String(value % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2,'0')}:${s}` : `${m}:${s}`;
}

function dynamicCover(item, kind = 'book') {
  if (item.cover_path && item.book_id) return `<img class="cover-image cover-mini" src="/media/cover/${Number(item.book_id)}" alt="" loading="lazy">`;
  const title = item.title || item.book_title || 'В';
  return `<div class="cover-mini${kind === 'audio' ? ' audio' : ''}">${escapeHtml(title[0] || 'В')}</div>`;
}

function continueCard(item, kind = 'reading') {
  const isAudio = kind === 'audio';
  const href = isAudio ? `/audio/${Number(item.audio_chapter_id)}` : `/reader/${Number(item.chapter_id)}`;
  const progress = isAudio
    ? (item.duration_seconds ? Math.min(100, Math.round(Number(item.position_seconds || 0) / Number(item.duration_seconds) * 100)) : 0)
    : Math.max(0, Math.min(100, Number(item.position_percent || 0)));
  const subtitle = isAudio ? `Аудиоглава ${item.audio_number || ''} · ${formatTime(item.position_seconds || 0)}` : `Глава ${item.chapter_number || ''} · ${progress}%`;
  return `<a class="continue-card" href="${href}">${dynamicCover(item, isAudio ? 'audio' : 'book')}<div><span>${isAudio ? 'Слушаете' : 'Читаете'}</span><h3>${escapeHtml(item.title || 'Книга')}</h3><p>${escapeHtml(subtitle)}</p><div class="mini-progress"><i style="width:${progress}%"></i></div></div></a>`;
}

function renderComments(comments) {
  const box = document.getElementById('commentsList');
  if (!box) return;
  box.innerHTML = comments?.length ? comments.map((item) => {
    const name = item.username ? `@${item.username}` : (item.full_name || 'Читатель');
    return `<article class="comment-card"><b>${escapeHtml(name)}</b><p>${escapeHtml(item.text)}</p></article>`;
  }).join('') : '<p class="muted">Комментариев пока нет. Можно начать обсуждение.</p>';
}

function renderReviews(reviews) {
  const box = document.getElementById('reviewsList');
  if (!box) return;
  box.innerHTML = reviews?.length ? reviews.map((item) => {
    const name = item.username ? `@${item.username}` : (item.full_name || 'Читатель');
    const stars = '★'.repeat(Math.max(1, Math.min(5, Number(item.rating || 5))));
    return `<article class="comment-card"><b>${stars} ${escapeHtml(name)}</b><p>${escapeHtml(item.text || 'Без текста')}</p></article>`;
  }).join('') : '<p class="muted">Отзывов пока нет. Можно стать первым.</p>';
}

function calcReadingPercent() {
  const top = window.scrollY || document.documentElement.scrollTop || 0;
  const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
  return Math.max(0, Math.min(100, Math.round(top / max * 100)));
}

function updateReaderProgressBar() {
  const percent = calcReadingPercent();
  const bar = document.getElementById('readerProgressBar');
  const label = document.getElementById('progressLabel');
  if (bar) bar.style.width = `${percent}%`;
  if (label) label.textContent = `Прочитано ${percent}%`;
}

async function saveReaderProgress(forcedPercent = null) {
  const reader = document.getElementById('readerText');
  if (!reader || !tgInitData()) return;
  const percent = forcedPercent === null ? calcReadingPercent() : Number(forcedPercent);
  await apiFetch(`/api/reader/${reader.dataset.chapterId}/progress`, { method: 'POST', body: JSON.stringify({ position_percent: percent }) });
  const label = document.getElementById('progressLabel');
  if (label) label.textContent = `Сохранено ${Math.max(0, Math.min(100, percent))}%`;
}

async function initReader() {
  const reader = document.getElementById('readerText');
  if (!reader) return;
  const status = document.getElementById('readerStatus');
  const paragraphs = document.getElementById('readerParagraphs');
  updateReaderProgressBar();
  window.addEventListener('scroll', updateReaderProgressBar, { passive: true });
  if (!tgInitData()) {
    if (status) status.textContent = 'Откройте главу внутри Telegram, чтобы сохранять место и видеть покупки.';
    return;
  }
  try {
    const data = await apiFetch(`/api/reader/${reader.dataset.chapterId}`);
    if (!data.allowed) {
      if (status) status.textContent = 'Для этой главы нужен доступ.';
      if (paragraphs) paragraphs.innerHTML = `<section class="empty-card paywall-card"><div class="empty-icon">◇</div><h3>Глава закрыта</h3><p><b>${Number(data.chapter.price_stars || 0)} Stars</b></p>${data.purchase_url ? `<a class="button-link" href="${escapeHtml(data.purchase_url)}">Купить главу</a>` : ''}</section>`;
      return;
    }
    if (status) status.textContent = data.progress_percent ? `Продолжаем с отметки ${data.progress_percent}%` : 'Глава открыта';
    if (paragraphs && data.chapter.text) {
      paragraphs.innerHTML = data.chapter.text.split('\n').filter((p) => p.trim()).map((p) => `<p>${escapeHtml(p)}</p>`).join('');
      if (data.progress_percent > 0) setTimeout(() => {
        const max = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
        window.scrollTo({ top: max * data.progress_percent / 100, behavior: 'smooth' });
      }, 250);
    }
    renderComments(data.comments);
  } catch (error) {
    if (status) status.textContent = 'Не удалось открыть главу. Попробуйте ещё раз.';
  }
}

function seekAudio(seconds) {
  const player = document.getElementById('voxPlayer');
  if (!player || !Number.isFinite(player.duration)) return;
  player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + seconds));
}

function setRate(rate) {
  setPref('audioRate', Number(rate));
  const player = document.getElementById('voxPlayer');
  if (player) player.playbackRate = Number(rate);
}

async function saveAudioProgress() {
  const page = document.getElementById('audioPage');
  const player = document.getElementById('voxPlayer');
  if (!page || !player?.src || !tgInitData()) return;
  await apiFetch(`/api/audio/${page.dataset.audioId}/progress`, { method: 'POST', body: JSON.stringify({ position_seconds: Math.floor(player.currentTime || 0) }) });
}

function updateAudioTime(player) {
  const current = document.getElementById('audioProgressLabel');
  const duration = document.getElementById('audioDurationLabel');
  if (current) current.textContent = formatTime(player.currentTime || 0);
  if (duration) duration.textContent = formatTime(Number.isFinite(player.duration) ? player.duration : 0);
}

function setSleepTimer(minutes) {
  clearTimeout(sleepTimerHandle);
  sleepTimerHandle = null;
  sleepTimerEndsAt = 0;
  const status = document.getElementById('sleepTimerStatus');
  if (!minutes) { if (status) status.textContent = 'Выключен'; notify('Таймер сна выключен'); return; }
  sleepTimerEndsAt = Date.now() + minutes * 60_000;
  if (status) status.textContent = `Остановится через ${minutes} мин`;
  sleepTimerHandle = setTimeout(() => {
    const player = document.getElementById('voxPlayer');
    player?.pause();
    saveAudioProgress().catch(() => {});
    if (status) status.textContent = 'Плеер остановлен';
    notify('Таймер сна остановил аудио');
  }, minutes * 60_000);
  notify(`Таймер сна: ${minutes} мин`);
}

async function initAudioPage() {
  const page = document.getElementById('audioPage');
  if (!page) return;
  const status = document.getElementById('audioStatus');
  const paywall = document.getElementById('audioPaywall');
  const player = document.getElementById('voxPlayer');
  if (!tgInitData()) {
    if (status) status.textContent = 'Откройте аудио внутри Telegram, чтобы проверить доступ и сохранять позицию.';
    return;
  }
  try {
    const meta = await apiFetch(`/api/audio/${page.dataset.audioId}/meta`);
    if (!meta.allowed) {
      if (status) status.textContent = 'Для этой аудиоглавы нужен доступ.';
      if (paywall) paywall.hidden = false;
      if (player) player.style.display = 'none';
      return;
    }
    if (status) status.textContent = meta.progress_seconds ? `Продолжаем с ${formatTime(meta.progress_seconds)}` : 'Можно слушать';
    const response = await apiFetch(`/api/audio/${page.dataset.audioId}/file`);
    const blob = await response.blob();
    if (!player) return;
    player.src = URL.createObjectURL(blob);
    player.style.display = 'block';
    player.addEventListener('loadedmetadata', () => {
      player.playbackRate = getPrefs().audioRate;
      if (meta.progress_seconds > 0 && meta.progress_seconds < player.duration) player.currentTime = meta.progress_seconds;
      updateAudioTime(player);
    });
    player.addEventListener('timeupdate', () => updateAudioTime(player));
    player.addEventListener('pause', () => { if (getPrefs().saveOnPause) saveAudioProgress().catch(() => {}); });
    player.addEventListener('ended', () => {
      saveAudioProgress().catch(() => {});
      const next = document.getElementById('nextAudioLink');
      if (getPrefs().autoplayNext && next) window.location.href = next.href;
    });
  } catch (_) {
    if (status) status.textContent = 'Не удалось загрузить аудио. Попробуйте ещё раз.';
  }
}

async function initBookPage() {
  const page = document.getElementById('bookPage');
  if (!page || !tgInitData()) return;
  try {
    const state = await apiFetch(`/api/book/${page.dataset.bookId}/state`);
    renderReviews(state.reviews);
    const reading = document.getElementById('bookmarkReading');
    const favorite = document.getElementById('bookmarkFavorite');
    if (state.bookmark?.status === 'reading' && reading) { reading.classList.add('saved'); reading.textContent = 'В библиотеке'; }
    if (state.bookmark?.status === 'favorite' && favorite) { favorite.classList.add('saved'); favorite.textContent = 'В любимом'; }
    if (state.my_review) {
      const rating = document.getElementById('reviewRating');
      const text = document.getElementById('reviewText');
      if (rating) rating.value = String(state.my_review.rating || 5);
      if (text) text.value = state.my_review.text || '';
    }
  } catch (_) {}
}

function normalizeCatalogText(value) {
  return String(value || '')
    .toLocaleLowerCase('ru-RU')
    .replaceAll('ё', 'е')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function catalogEditDistance(left, right) {
  const a = String(left || '');
  const b = String(right || '');
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  let previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost);
    }
    previous = current;
  }
  return previous[b.length];
}

function catalogWordSimilarity(left, right) {
  if (!left || !right) return 0;
  if (left === right) return 1;
  if (left.startsWith(right) || right.startsWith(left)) return 0.94;
  if (left.includes(right) || right.includes(left)) return 0.86;
  const longest = Math.max(left.length, right.length);
  return longest ? 1 - catalogEditDistance(left, right) / longest : 0;
}

function catalogTextScore(card, normalizedQuery) {
  if (!normalizedQuery) return 1;
  const title = normalizeCatalogText(card.dataset.title || '');
  const author = normalizeCatalogText(card.dataset.author || '');
  if (title === normalizedQuery) return 100000;
  if (title.startsWith(normalizedQuery)) return 90000 - title.length;
  if (title.includes(normalizedQuery)) return 80000 - title.indexOf(normalizedQuery);
  if (author === normalizedQuery) return 70000;
  if (author.includes(normalizedQuery)) return 65000;

  const queryWords = normalizedQuery.split(' ').filter(Boolean);
  const titleWords = title.split(' ').filter(Boolean);
  const authorWords = author.split(' ').filter(Boolean);
  if (!queryWords.length) return 1;

  let titleTotal = 0;
  let titleMatched = 0;
  queryWords.forEach((queryWord) => {
    let best = 0;
    titleWords.forEach((titleWord) => { best = Math.max(best, catalogWordSimilarity(queryWord, titleWord)); });
    if (best >= (queryWord.length <= 3 ? 0.9 : 0.66)) titleMatched += 1;
    titleTotal += best;
  });
  if (titleMatched === queryWords.length) {
    const phraseSimilarity = catalogWordSimilarity(normalizedQuery, title);
    return 60000 + Math.round((titleTotal / queryWords.length) * 1000) + Math.round(phraseSimilarity * 500);
  }

  let authorMatched = 0;
  let authorTotal = 0;
  queryWords.forEach((queryWord) => {
    let best = 0;
    authorWords.forEach((authorWord) => { best = Math.max(best, catalogWordSimilarity(queryWord, authorWord)); });
    if (best >= (queryWord.length <= 3 ? 0.9 : 0.72)) authorMatched += 1;
    authorTotal += best;
  });
  if (authorMatched === queryWords.length) return 50000 + Math.round((authorTotal / queryWords.length) * 1000);

  if (queryWords.length === 1 && titleTotal >= 0.68) return 40000 + Math.round(titleTotal * 1000);
  return 0;
}

function applyCatalogFilter() {
  const grid = document.getElementById('catalogGrid');
  if (!grid) return;
  const query = normalizeCatalogText(document.getElementById('catalogSearch')?.value || '');
  const active = document.querySelector('[data-catalog-filter].active')?.dataset.catalogFilter || 'all';
  const cards = Array.from(grid.querySelectorAll('[data-catalog-card]'));
  cards.forEach((card, index) => {
    if (!card.dataset.catalogOrder) card.dataset.catalogOrder = String(index);
  });

  const exactTitleExists = Boolean(query) && cards.some((card) => normalizeCatalogText(card.dataset.title || '') === query);
  let visible = 0;
  const ranked = cards.map((card) => {
    const title = normalizeCatalogText(card.dataset.title || '');
    const score = exactTitleExists ? (title === query ? 100000 : 0) : catalogTextScore(card, query);
    const matchesText = !query || score > 0;
    const matchesFilter = active === 'all'
      || (active === 'audio' && card.dataset.audio === '1')
      || (active === 'free' && card.dataset.free === '1')
      || (active === 'popular' && Number(card.dataset.popular || 0) > 0);
    const show = matchesText && matchesFilter;
    card.hidden = !show;
    if (show) visible += 1;
    return { card, score, original: Number(card.dataset.catalogOrder || 0) };
  });

  ranked.sort((left, right) => {
    if (!query) return left.original - right.original;
    return right.score - left.score || left.original - right.original;
  }).forEach(({ card }) => grid.appendChild(card));

  const empty = document.getElementById('catalogEmptySearch');
  if (empty) empty.hidden = visible !== 0;
}

async function initContinueShelves() {
  const readingSection = document.getElementById('continueSection');
  const audioSection = document.getElementById('continueAudioSection');
  if ((!readingSection && !audioSection) || !tgInitData()) return;
  try {
    const data = await loadMeData();
    if (readingSection && data.continue_reading?.length) {
      document.getElementById('continueShelf').innerHTML = data.continue_reading.map((item) => continueCard(item, 'reading')).join('');
      readingSection.hidden = false;
    }
    if (audioSection && data.continue_listening?.length) {
      document.getElementById('continueAudioShelf').innerHTML = data.continue_listening.map((item) => continueCard(item, 'audio')).join('');
      audioSection.hidden = false;
    }
  } catch (_) {}
}

function bookmarkCard(item) {
  const labels = { reading: 'Читаю', favorite: 'Любимое', planned: 'В планах', finished: 'Прочитано', dropped: 'Отложено' };
  return `<a class="book-card library-book-card" href="/book/${Number(item.book_id)}">${dynamicCover(item)}<div class="book-info"><span class="eyebrow">${escapeHtml(labels[item.status] || 'В библиотеке')}</span><h3>${escapeHtml(item.title || 'Книга')}</h3><p>${escapeHtml(item.pen_name || 'Автор не указан')}</p></div></a>`;
}

function purchaseCard(item) {
  const refunded = item.status === 'refunded';
  let title = item.book_title || item.chapter_title || item.audio_title || 'Покупка';
  let href = item.audio_chapter_id ? `/audio/${Number(item.audio_chapter_id)}` : item.chapter_id ? `/reader/${Number(item.chapter_id)}` : item.book_id ? `/book/${Number(item.book_id)}` : '#';
  let type = item.audio_chapter_id ? 'Аудиоглава' : item.chapter_id ? 'Глава' : 'Книга';
  return `<a class="purchase-card${refunded ? ' refunded' : ''}" href="${href}"><div><span>${type}</span><h3>${escapeHtml(title)}</h3><p>${refunded ? 'Возврат оформлен' : 'Доступ открыт'}</p></div><b>${Number(item.amount_stars || 0)} Stars</b></a>`;
}

function renderLibraryTab(tab, data) {
  const content = document.getElementById('libraryContent');
  if (!content) return;
  if (tab === 'continue') {
    const reading = data.continue_reading || [];
    const audio = data.continue_listening || [];
    if (!reading.length && !audio.length) {
      content.innerHTML = '<article class="empty-card premium-empty"><div class="empty-icon">◇</div><h3>Продолжать пока нечего</h3><p>Откройте любую главу или аудио — прогресс появится здесь.</p><a class="button-link" href="/catalog">Выбрать книгу</a></article>';
      return;
    }
    content.innerHTML = `${reading.length ? `<div class="section-title slim"><h2>Чтение</h2></div><div class="library-continue-grid">${reading.map((item) => continueCard(item,'reading')).join('')}</div>` : ''}${audio.length ? `<div class="section-title slim"><h2>Аудио</h2></div><div class="library-continue-grid">${audio.map((item) => continueCard(item,'audio')).join('')}</div>` : ''}`;
    return;
  }
  if (tab === 'saved') {
    const items = data.bookmarks || [];
    content.innerHTML = items.length ? `<div class="book-list">${items.map(bookmarkCard).join('')}</div>` : '<article class="empty-card premium-empty"><div class="empty-icon">☆</div><h3>Полка пока пустая</h3><p>Добавляйте книги в библиотеку или любимое.</p><a class="button-link" href="/catalog">Открыть каталог</a></article>';
    return;
  }
  const purchases = data.purchases || [];
  content.innerHTML = purchases.length ? `<div class="purchase-list">${purchases.map(purchaseCard).join('')}</div>` : '<article class="empty-card premium-empty"><div class="empty-icon">★</div><h3>Покупок пока нет</h3><p>После покупки книги, главы или аудио доступ появится здесь.</p></article>';
}

async function initLibrary() {
  const page = document.getElementById('libraryPage');
  if (!page) return;
  const content = document.getElementById('libraryContent');
  if (!tgInitData()) {
    if (content) content.innerHTML = '<article class="empty-card premium-empty"><div class="empty-icon">◇</div><h3>Откройте внутри Telegram</h3><p>Личная библиотека привязана к вашему Telegram-профилю.</p><a class="button-link" href="/catalog">Смотреть каталог</a></article>';
    return;
  }
  try {
    const data = await loadMeData();
    page._libraryData = data;
    const authorEntry = document.getElementById('authorStudioEntry');
    if (authorEntry && data.author?.enabled) authorEntry.hidden = false;
    const controlEntry = document.getElementById('controlCenterEntry');
    if (controlEntry && data.control?.enabled) {
      controlEntry.hidden = false;
      const hint = document.getElementById('controlCenterHint');
      if (hint) hint.textContent = data.control.owner ? 'Полное управление платформой' : 'Доступные разделы модерации';
    }
    renderLibraryTab('continue', data);
  } catch (_) {
    if (content) content.innerHTML = '<article class="empty-card premium-empty"><h3>Не удалось открыть полку</h3><p>Закройте Mini App и откройте его снова из бота.</p></article>';
  }
}

function markActiveNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.bottom-nav a').forEach((link) => {
    const nav = link.dataset.nav;
    const active = (nav === 'home' && path === '/') ||
      (nav === 'books' && (path.startsWith('/catalog') || path.startsWith('/book'))) ||
      (nav === 'audio' && path.startsWith('/audio')) || (nav === 'library' && (path.startsWith('/library') || path.startsWith('/author') || path.startsWith('/control'))) || (nav === 'settings' && path.startsWith('/settings'));
    link.classList.toggle('active', active);
  });
}

function bindEvents() {
  document.addEventListener('input', (event) => { if (event.target.id === 'catalogSearch') applyCatalogFilter(); });
  document.addEventListener('click', async (event) => {
    const target = event.target.closest('button, a');
    if (!target) return;

    if (target.matches('[data-theme]')) { event.preventDefault(); setPref('theme', target.dataset.theme); return; }
    if (target.id === 'fontPlus') { event.preventDefault(); changeFont(1); return; }
    if (target.id === 'fontMinus') { event.preventDefault(); changeFont(-1); return; }
    if (target.id === 'fontReset') { event.preventDefault(); setPref('fontSize', DEFAULTS.fontSize); return; }
    if (target.matches('[data-line-height]')) { event.preventDefault(); setPref('lineHeight', Number(target.dataset.lineHeight)); return; }
    if (target.matches('[data-reader-width]')) { event.preventDefault(); setPref('readerWidth', target.dataset.readerWidth); return; }
    if (target.matches('[data-rate]')) { event.preventDefault(); setRate(Number(target.dataset.rate)); return; }
    if (target.matches('[data-rewind]')) { event.preventDefault(); setPref('rewindStep', Number(target.dataset.rewind)); return; }
    if (target.matches('[data-contrast]')) { event.preventDefault(); setPref('contrast', target.dataset.contrast); return; }
    if (target.matches('[data-toggle]')) {
      event.preventDefault();
      const key = target.dataset.toggle;
      const oldValue = Boolean(getPrefs()[key]);
      const newValue = !oldValue;
      setPref(key, newValue);
      if (SERVER_NOTIFICATION_KEYS[key]) {
        try { await saveNotificationPreference(key, newValue); notify('Настройка уведомлений сохранена'); }
        catch (_) { setPref(key, oldValue); notify('Не удалось сохранить выбор'); }
      }
      return;
    }
    if (target.id === 'resetLocalSettings') { event.preventDefault(); await resetSettings(); return; }
    if (target.id === 'readerSettingsToggle') { event.preventDefault(); const panel = document.getElementById('readerQuickSettings'); if (panel) panel.hidden = !panel.hidden; return; }
    if (target.id === 'saveReadingProgress') { event.preventDefault(); try { await saveReaderProgress(); notify('Место сохранено'); } catch (_) { notify('Не удалось сохранить место'); } return; }
    if (target.matches('[data-reader-nav="next"]')) { event.preventDefault(); const href = target.href; try { await saveReaderProgress(100); } catch (_) {} window.location.href = href; return; }
    if (target.id === 'saveAudioProgress') { event.preventDefault(); try { await saveAudioProgress(); notify('Место сохранено'); } catch (_) { notify('Не удалось сохранить место'); } return; }
    if (target.id === 'audioBack') { event.preventDefault(); seekAudio(-getPrefs().rewindStep); return; }
    if (target.id === 'audioForward') { event.preventDefault(); seekAudio(getPrefs().rewindStep); return; }
    if (target.matches('[data-sleep-minutes]')) { event.preventDefault(); setSleepTimer(Number(target.dataset.sleepMinutes)); return; }
    if (target.matches('[data-catalog-filter]')) { event.preventDefault(); document.querySelectorAll('[data-catalog-filter]').forEach((btn) => btn.classList.remove('active')); target.classList.add('active'); applyCatalogFilter(); return; }
    if (target.matches('[data-library-tab]')) { event.preventDefault(); document.querySelectorAll('[data-library-tab]').forEach((btn) => btn.classList.remove('active')); target.classList.add('active'); const page = document.getElementById('libraryPage'); if (page?._libraryData) renderLibraryTab(target.dataset.libraryTab, page._libraryData); return; }

    if (target.matches('[data-card-bookmark]')) {
      event.preventDefault();
      const bookId = Number(target.dataset.cardBookmark || 0);
      if (!bookId) return;
      try {
        await apiFetch(`/api/book/${bookId}/bookmark`, { method: 'POST', body: JSON.stringify({ status: 'reading' }) });
        target.classList.add('saved');
        target.textContent = 'В библиотеке';
        target.setAttribute('aria-pressed', 'true');
        notify('Книга добавлена в библиотеку');
      } catch (_) { notify('Откройте Mini App из бота'); }
      return;
    }

    if (target.id === 'bookmarkReading' || target.id === 'bookmarkFavorite') {
      event.preventDefault();
      const page = document.getElementById('bookPage');
      if (!page) return;
      const status = target.id === 'bookmarkFavorite' ? 'favorite' : 'reading';
      try {
        await apiFetch(`/api/book/${page.dataset.bookId}/bookmark`, { method: 'POST', body: JSON.stringify({ status }) });
        target.classList.add('saved'); target.textContent = status === 'favorite' ? 'В любимом' : 'В библиотеке'; notify('Сохранено');
      } catch (_) { notify('Откройте Mini App из бота'); }
      return;
    }

    if (target.id === 'sendReview') {
      event.preventDefault();
      const page = document.getElementById('bookPage');
      if (!page) return;
      try {
        const data = await apiFetch(`/api/book/${page.dataset.bookId}/review`, { method: 'POST', body: JSON.stringify({ rating: document.getElementById('reviewRating')?.value || 5, text: document.getElementById('reviewText')?.value || '' }) });
        renderReviews(data.reviews); notify('Отзыв сохранён');
      } catch (_) { notify('Откройте Mini App из бота'); }
      return;
    }

    if (target.id === 'sendComment') {
      event.preventDefault();
      const reader = document.getElementById('readerText');
      const field = document.getElementById('commentText');
      if (!reader || !field) return;
      try {
        const data = await apiFetch(`/api/reader/${reader.dataset.chapterId}/comments`, { method: 'POST', body: JSON.stringify({ text: field.value }) });
        field.value = ''; renderComments(data.comments); notify('Комментарий опубликован');
      } catch (error) { notify(error.message || 'Комментарий не отправлен'); }
      return;
    }
  });

  document.querySelectorAll('.reader-ad-card').forEach((link) => link.addEventListener('click', () => {
    apiFetch('/api/reader/ad-click', { method: 'POST', body: JSON.stringify({ promoted_book_id: link.dataset.promotedBookId, campaign_id: link.dataset.campaignId || null, source_book_id: link.dataset.sourceBookId || null, source_chapter_id: link.dataset.sourceChapterId || null }) }).catch(() => {});
  }));

  window.addEventListener('scroll', () => {
    if (!document.getElementById('readerText') || !tgInitData()) return;
    clearTimeout(autoProgressTimer);
    autoProgressTimer = setTimeout(() => saveReaderProgress().catch(() => {}), 1400);
  }, { passive: true });
  window.addEventListener('pagehide', () => {
    if (document.getElementById('readerText')) saveReaderProgress().catch(() => {});
    if (document.getElementById('audioPage')) saveAudioProgress().catch(() => {});
  });
}

document.addEventListener('DOMContentLoaded', () => {
  applySettings();
  syncNotificationPreferences();
  markActiveNav();
  bindEvents();
  applyCatalogFilter();
  initContinueShelves();
  initReader();
  initAudioPage();
  initBookPage();
  initLibrary();
});
