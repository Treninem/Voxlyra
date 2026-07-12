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
  ttsVoice: 'irina', ttsStyle: 'natural', ttsRate: 1, ttsAutoNext: true,
  notifications: true, notificationChapters: true, notificationAudio: true, notificationDiscounts: true,
  notificationReminders: true, notificationAchievements: true,
  contrast: 'normal', focusMode: false, showReaderAds: true,
  profileFrame: 'standard', seasonalDecor: true,
};
let meDataPromise = null;
let autoProgressTimer = null;
let sleepTimerHandle = null;
let sleepTimerEndsAt = 0;
let readerTtsMeta = null;
let readerTtsLoading = false;
let readerTtsProgressTimer = null;
let readerTtsSleepTimer = null;
let readerTtsPrefetch = null;
let readerTtsObjectUrl = '';
let readerTtsCacheTimer = null;
let readerTtsCacheListener = null;
let readerTtsLifecycleBound = false;
const TTS_DEVICE_CACHE_NAME = 'voxlyra-reader-tts-v3-continuity';
const TTS_LEGACY_DEVICE_CACHE_NAMES = ['voxlyra-reader-tts-v2-quality'];
const TTS_DEVICE_CACHE_PREFIX = `${window.location.origin}/__voxlyra_tts_cache__/`;
const READER_TTS_PLAYER_VERSION = 'v1.11.0-stage3-continuity-1';
const READER_TTS_TRANSITION_LEAD_SECONDS = 0.22;
const READER_TTS_CROSSFADE_MS = 90;
const READER_TTS_STALL_TIMEOUT_MS = 7000;

async function migrateReaderTtsDeviceCache() {
  if (!('caches' in window)) return;
  try {
    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => (name.startsWith('voxlyra-reader-tts-') || TTS_LEGACY_DEVICE_CACHE_NAMES.includes(name)) && name !== TTS_DEVICE_CACHE_NAME)
      .map((name) => caches.delete(name)));
  } catch (_) {}
}

function releaseReaderTtsObjectUrl(player) {
  const value = String(player?.dataset?.voxObjectUrl || '');
  if (value.startsWith('blob:')) {
    try { URL.revokeObjectURL(value); } catch (_) {}
  }
  if (player?.dataset) player.dataset.voxObjectUrl = '';
}

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
    ttsVoice: localStorage.getItem('voxTtsVoice') || DEFAULTS.ttsVoice,
    ttsStyle: localStorage.getItem('voxTtsStyle') || DEFAULTS.ttsStyle,
    ttsRate: Number(localStorage.getItem('voxTtsRate') || DEFAULTS.ttsRate),
    ttsAutoNext: getStoredBool('voxTtsAutoNext', DEFAULTS.ttsAutoNext),
    rewindStep: Number(localStorage.getItem('voxRewindStep') || DEFAULTS.rewindStep),
    autoplayNext: getStoredBool('voxAutoplayNext', DEFAULTS.autoplayNext),
    saveOnPause: getStoredBool('voxSaveOnPause', DEFAULTS.saveOnPause),
    notifications: getStoredBool('voxNotifications', DEFAULTS.notifications),
    notificationChapters: getStoredBool('voxNotificationChapters', DEFAULTS.notificationChapters),
    notificationAudio: getStoredBool('voxNotificationAudio', DEFAULTS.notificationAudio),
    notificationDiscounts: getStoredBool('voxNotificationDiscounts', DEFAULTS.notificationDiscounts),
    notificationReminders: getStoredBool('voxNotificationReminders', DEFAULTS.notificationReminders),
    notificationAchievements: getStoredBool('voxNotificationAchievements', DEFAULTS.notificationAchievements),
    contrast: localStorage.getItem('voxContrast') || DEFAULTS.contrast,
    focusMode: getStoredBool('voxFocusMode', DEFAULTS.focusMode),
    showReaderAds: getStoredBool('voxShowReaderAds', DEFAULTS.showReaderAds),
    profileFrame: localStorage.getItem('voxProfileFrame') || DEFAULTS.profileFrame,
    seasonalDecor: getStoredBool('voxSeasonalDecor', DEFAULTS.seasonalDecor),
  };
}

function setPref(key, value) {
  const map = {
    theme: 'voxTheme', fontSize: 'readerFontSize', lineHeight: 'readerLineHeight',
    readerWidth: 'readerWidth', audioRate: 'voxAudioRate', rewindStep: 'voxRewindStep',
    ttsVoice: 'voxTtsVoice', ttsStyle: 'voxTtsStyle', ttsRate: 'voxTtsRate', ttsAutoNext: 'voxTtsAutoNext',
    autoplayNext: 'voxAutoplayNext', saveOnPause: 'voxSaveOnPause',
    notifications: 'voxNotifications', notificationChapters: 'voxNotificationChapters',
    notificationAudio: 'voxNotificationAudio', notificationDiscounts: 'voxNotificationDiscounts',
    notificationReminders: 'voxNotificationReminders', notificationAchievements: 'voxNotificationAchievements',
    contrast: 'voxContrast', focusMode: 'voxFocusMode',
    showReaderAds: 'voxShowReaderAds', profileFrame: 'voxProfileFrame', seasonalDecor: 'voxSeasonalDecor',
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

let contentProtectionBound = false;

function protectedTarget(event) {
  return event?.target?.closest?.('.protected-content');
}

function bindContentProtectionEvents() {
  if (contentProtectionBound) return;
  contentProtectionBound = true;
  ['copy', 'cut', 'contextmenu', 'dragstart', 'selectstart'].forEach((type) => {
    document.addEventListener(type, (event) => {
      if (!protectedTarget(event)) return;
      event.preventDefault();
      notify('Автор разрешил только чтение внутри Вокслиры');
    }, { capture: true });
  });
  document.addEventListener('keydown', (event) => {
    if (!document.querySelector('.protected-content')) return;
    const key = String(event.key || '').toLowerCase();
    if ((event.ctrlKey || event.metaKey) && ['c', 'x', 's', 'p', 'a', 'u'].includes(key)) {
      event.preventDefault();
      notify('Копирование и сохранение отключены автором');
    }
  }, { capture: true });
}

function applyContentProtection(protection, rootElement = null) {
  const target = rootElement || document.getElementById('readerText') || document.getElementById('graphicReader');
  if (!target) return;
  const enabled = Boolean(protection?.protected);
  target.classList.toggle('protected-content', enabled);
  target.querySelectorAll('img').forEach((image) => { image.draggable = !enabled; });
  document.querySelectorAll('.content-watermark-layer').forEach((node) => node.remove());
  if (enabled && protection?.watermark) {
    const layer = document.createElement('div');
    layer.className = 'content-watermark-layer';
    layer.setAttribute('aria-hidden', 'true');
    const label = String(protection.watermark || 'Вокслира');
    layer.innerHTML = Array.from({ length: 18 }, () => `<span>${escapeHtml(label)}</span>`).join('');
    target.appendChild(layer);
  }
  if (enabled) bindContentProtectionEvents();
}
window.applyContentProtection = applyContentProtection;

async function downloadAllowedBook(url) {
  if (!url) return;
  const response = await apiFetch(url);
  const blob = await response.blob();
  const disposition = response.headers.get('content-disposition') || '';
  const match = disposition.match(/filename\*?=(?:UTF-8''|\")?([^\";]+)/i);
  const filename = decodeURIComponent((match?.[1] || 'book.txt').replaceAll('"', ''));
  const objectUrl = URL.createObjectURL(blob);
  try {
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    link.rel = 'noopener';
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
  }
}

function currentSeasonKey(date = new Date()) {
  const month = date.getMonth() + 1;
  const day = date.getDate();
  if ((month === 12 && day >= 20) || (month === 1 && day <= 10)) return 'new-year';
  if (month === 10 && day >= 20) return 'halloween';
  if ([12, 1, 2].includes(month)) return 'winter';
  if ([9, 10, 11].includes(month)) return 'autumn';
  return 'calm';
}

function applyProfileFrame(frame = getPrefs().profileFrame) {
  const allowed = ['standard', 'author', 'moderator', 'premium', 'holiday'];
  const safe = allowed.includes(frame) ? frame : 'standard';
  document.querySelectorAll('[data-profile-frame]').forEach((button) => button.classList.toggle('active', button.dataset.profileFrame === safe));
  const medallion = document.getElementById('libraryProfileFrame');
  if (medallion) {
    allowed.forEach((name) => medallion.classList.remove(`frame-${name}`));
    medallion.classList.add(`frame-${safe}`);
  }
}

function initVoxSplash() {
  const splash = document.getElementById('voxSplash');
  if (!splash || document.documentElement.classList.contains('vox-splash-seen')) return;
  const started = performance.now();
  let hidden = false;
  const hide = () => {
    if (hidden) return;
    const wait = Math.max(0, 920 - (performance.now() - started));
    setTimeout(() => {
      if (hidden) return;
      hidden = true;
      splash.classList.add('is-leaving');
      try { sessionStorage.setItem('voxSplashSeen', '1'); } catch (_) {}
      setTimeout(() => splash.remove(), 520);
    }, wait);
  };
  if (document.readyState === 'complete') hide();
  else window.addEventListener('load', hide, { once: true });
  setTimeout(hide, 1900);
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
  document.body.classList.toggle('seasonal-decor', Boolean(prefs.seasonalDecor));
  document.body.dataset.season = currentSeasonKey();
  applyProfileFrame(prefs.profileFrame);

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
  document.querySelectorAll('#readerTtsPlayer, #readerTtsBuffer').forEach((ttsPlayer) => { ttsPlayer.playbackRate = prefs.ttsRate; });
  const ttsVoice = document.getElementById('readerTtsVoice');
  if (ttsVoice && Array.from(ttsVoice.options).some((item) => item.value === prefs.ttsVoice)) ttsVoice.value = prefs.ttsVoice;
  const ttsStyle = document.getElementById('readerTtsStyle');
  if (ttsStyle && Array.from(ttsStyle.options).some((item) => item.value === prefs.ttsStyle)) ttsStyle.value = prefs.ttsStyle;
  const ttsRate = document.getElementById('readerTtsRate');
  if (ttsRate) ttsRate.value = String(prefs.ttsRate);
  const ttsAutoNext = document.getElementById('readerTtsAutoNext');
  if (ttsAutoNext) ttsAutoNext.checked = Boolean(prefs.ttsAutoNext);
  document.querySelectorAll('#audioBack').forEach((btn) => { btn.textContent = `−${prefs.rewindStep}`; });
  document.querySelectorAll('#audioForward').forEach((btn) => { btn.textContent = `+${prefs.rewindStep}`; });
}

function changeFont(delta) {
  setPref('fontSize', Math.max(14, Math.min(28, getPrefs().fontSize + delta)));
}

async function resetSettings() {
  ['voxTheme','readerFontSize','readerLineHeight','readerWidth','voxAudioRate','voxRewindStep','voxAutoplayNext','voxSaveOnPause','voxTtsVoice','voxTtsStyle','voxTtsRate','voxTtsAutoNext','voxNotifications','voxNotificationChapters','voxNotificationAudio','voxNotificationDiscounts','voxNotificationReminders','voxNotificationAchievements','voxContrast','voxFocusMode','voxShowReaderAds','voxProfileFrame','voxSeasonalDecor'].forEach((key) => localStorage.removeItem(key));
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
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  const contentType = response.headers.get('content-type') || '';
  return contentType.includes('application/json') ? response.json() : response;
}

const SERVER_NOTIFICATION_KEYS = {
  notifications: 'notifications',
  notificationChapters: 'notifications_chapters',
  notificationAudio: 'notifications_audio',
  notificationDiscounts: 'notifications_discounts',
  notificationReminders: 'notifications_reminders',
  notificationAchievements: 'notifications_achievements',
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
      notificationReminders: prefs.notifications_reminders !== '0',
      notificationAchievements: prefs.notifications_achievements !== '0',
    };
    Object.entries(values).forEach(([key, value]) => {
      const storageKey = {
        notifications: 'voxNotifications', notificationChapters: 'voxNotificationChapters',
        notificationAudio: 'voxNotificationAudio', notificationDiscounts: 'voxNotificationDiscounts',
        notificationReminders: 'voxNotificationReminders', notificationAchievements: 'voxNotificationAchievements',
    notificationReminders: 'voxNotificationReminders', notificationAchievements: 'voxNotificationAchievements',
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

function emptyStateMarkup(kind, title, text, href = '', action = '', extraClass = '') {
  const button = href && action ? `<a class="button-link" href="${escapeHtml(href)}">${escapeHtml(action)}</a>` : '';
  return `<article class="empty-card premium-empty illustrated-empty ${escapeHtml(extraClass)}" data-empty-state="${escapeHtml(kind)}"><img class="empty-state-art" src="/static/img/miniapp/empty/${escapeHtml(kind)}.webp" alt="" aria-hidden="true" loading="lazy"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p>${button}</article>`;
}

function formatTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(value / 3600);
  const m = Math.floor((value % 3600) / 60);
  const s = String(value % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2,'0')}:${s}` : `${m}:${s}`;
}

function dynamicCover(item, kind = 'book') {
  const title = item.title || item.book_title || 'В';
  const letter = escapeHtml(title[0] || 'В');
  const extra = kind === 'audio' ? ' audio' : '';
  if (item.book_id) {
    const coverVersion = encodeURIComponent(String(item.updated_at || item.book_id));
    return `<img class="cover-image cover-mini${extra}" src="/media/cover/${Number(item.book_id)}?v=${coverVersion}" alt="Обложка ${escapeHtml(title)}" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false"><div class="cover-mini${extra}" hidden>${letter}</div>`;
  }
  return `<div class="cover-mini${extra}">${letter}</div>`;
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

const CHAPTER_REACTION_META = {
  fire: { icon: '🔥', title: 'Захватывающе' },
  heart: { icon: '💜', title: 'Понравилось' },
  cry: { icon: '😢', title: 'Тронуло' },
  laugh: { icon: '😂', title: 'Смешно' },
  shock: { icon: '🤯', title: 'Неожиданно' },
  epic: { icon: '⚔️', title: 'Эпично' },
};

function renderChapterReactions(reactions) {
  const list = document.getElementById('chapterReactionList');
  if (!list) return;
  const counts = reactions?.counts || {};
  const selected = String(reactions?.selected || '');
  list.querySelectorAll('[data-chapter-reaction]').forEach((button) => {
    const code = String(button.dataset.chapterReaction || '');
    const count = Math.max(0, Number(counts[code] || 0));
    button.classList.toggle('selected', code === selected);
    button.setAttribute('aria-pressed', code === selected ? 'true' : 'false');
    const counter = button.querySelector('small');
    if (counter) counter.textContent = String(count);
  });
}

function commentDisplayName(item) {
  return item?.username ? `@${item.username}` : (item?.full_name || 'Читатель');
}

function commentCardMarkup(item, isReply = false) {
  const id = Number(item.id || 0);
  const name = commentDisplayName(item);
  const liked = Boolean(Number(item.viewer_liked || 0));
  const likeCount = Math.max(0, Number(item.like_count || 0));
  const spoiler = Boolean(Number(item.is_spoiler || 0));
  const replyClass = isReply ? ' comment-reply' : '';
  const spoilerMarkup = spoiler
    ? `<button type="button" class="comment-spoiler-cover" data-comment-spoiler-reveal="${id}">Спойлер скрыт · показать</button><p class="comment-spoiler-text" hidden>${escapeHtml(item.text || '')}</p>`
    : `<p>${escapeHtml(item.text || '')}</p>`;
  return `<article class="comment-card${replyClass}" data-comment-id="${id}">
    <header><b>${escapeHtml(name)}</b>${spoiler ? '<span class="comment-badge">Спойлер</span>' : ''}${isReply ? '<span class="comment-badge subtle">Ответ</span>' : ''}</header>
    ${spoilerMarkup}
    <footer class="comment-actions">
      <button type="button" class="quiet-link comment-like${liked ? ' selected' : ''}" data-comment-like="${id}" aria-pressed="${liked ? 'true' : 'false'}">♥ <span>${likeCount}</span></button>
      <button type="button" class="quiet-link" data-comment-reply="${id}" data-comment-name="${escapeHtml(name)}">Ответить</button>
      <button type="button" class="quiet-link danger" data-comment-report="${id}">Пожаловаться</button>
    </footer>
  </article>`;
}

function renderComments(comments) {
  const box = document.getElementById('commentsList');
  if (!box) return;
  const items = Array.isArray(comments) ? comments : [];
  if (!items.length) {
    box.innerHTML = '<p class="muted">Комментариев пока нет. Можно начать обсуждение.</p>';
    return;
  }
  const roots = [];
  const replies = new Map();
  items.forEach((item) => {
    const parentId = Number(item.parent_id || 0);
    if (!parentId) roots.push(item);
    else {
      if (!replies.has(parentId)) replies.set(parentId, []);
      replies.get(parentId).push(item);
    }
  });
  const knownRootIds = new Set(roots.map((item) => Number(item.id || 0)));
  items.filter((item) => Number(item.parent_id || 0) && !knownRootIds.has(Number(item.parent_id || 0))).forEach((item) => roots.push({ ...item, parent_id: null }));
  box.innerHTML = roots.map((root) => {
    const rootId = Number(root.id || 0);
    const children = (replies.get(rootId) || []).map((item) => commentCardMarkup(item, true)).join('');
    return `<div class="comment-thread">${commentCardMarkup(root, false)}${children ? `<div class="comment-replies">${children}</div>` : ''}</div>`;
  }).join('');
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

function readerTextMap() {
  const content = document.getElementById('readerParagraphs');
  if (!content) return null;
  const paragraphs = Array.from(content.querySelectorAll(':scope > p')).filter((p) => (p.textContent || '').trim());
  if (!paragraphs.length) return null;

  const scrollTop = window.scrollY || document.documentElement.scrollTop || 0;
  const toolbar = document.querySelector('.reader-toolbar');
  const toolbarHeight = toolbar ? Math.max(0, toolbar.getBoundingClientRect().height) : 0;
  const viewportHeight = Math.max(220, window.innerHeight - toolbarHeight);
  const contentRect = content.getBoundingClientRect();
  const contentTop = scrollTop + contentRect.top;
  const contentBottom = scrollTop + contentRect.bottom;
  const contentHeight = Math.max(1, contentBottom - contentTop);

  // Процент зависит только от пути прокрутки внутри текста главы. Заголовок, реклама,
  // комментарии и кнопки под главой больше не могут сразу давать 80–99%.
  const startScroll = Math.max(0, contentTop - toolbarHeight - 20);
  const naturalEnd = contentBottom - window.innerHeight + Math.max(70, viewportHeight * 0.14);
  const minimumJourney = Math.max(140, Math.min(viewportHeight * 0.62, contentHeight * 0.45));
  const endScroll = Math.max(startScroll + minimumJourney, naturalEnd);
  return { startScroll, endScroll, scrollTop };
}

function calcReadingPercent() {
  const map = readerTextMap();
  if (!map) return 0;
  if (map.scrollTop <= map.startScroll) return 0;
  if (map.scrollTop >= map.endScroll) return 100;
  const percent = (map.scrollTop - map.startScroll) / Math.max(1, map.endScroll - map.startScroll) * 100;
  return Math.max(0, Math.min(100, Math.round(percent)));
}

function scrollToReadingPercent(percent, behavior = 'auto') {
  const map = readerTextMap();
  if (!map) return;
  const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
  const target = map.startScroll + (map.endScroll - map.startScroll) * safePercent / 100;
  window.scrollTo({ top: Math.max(0, target), behavior });
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
  const result = await apiFetch(`/api/reader/${reader.dataset.chapterId}/progress`, { method: 'POST', body: JSON.stringify({ position_percent: percent }) });
  (result.achievements?.new || []).forEach((item) => notify(`Новое достижение: ${item.title}`));
  const label = document.getElementById('progressLabel');
  if (label) label.textContent = `Сохранено ${Math.max(0, Math.min(100, percent))}%`;
}

async function initReader() {
  const reader = document.getElementById('readerText');
  if (!reader) return;
  const status = document.getElementById('readerStatus');
  const paragraphs = document.getElementById('readerParagraphs');
  bindReaderQuoteCards();
  updateReaderProgressBar();
  window.addEventListener('scroll', updateReaderProgressBar, { passive: true });
  window.addEventListener('resize', updateReaderProgressBar, { passive: true });
  if (!tgInitData()) {
    if (status) status.textContent = 'Откройте главу внутри Telegram, чтобы сохранять место и видеть покупки.';
    return;
  }
  try {
    const data = await apiFetch(`/api/reader/${reader.dataset.chapterId}`);
    if (!data.allowed) {
      const canBuyChapter = Boolean(data.can_buy_chapter);
      const packageRemaining = canBuyChapter ? Number(data.package_credits?.remaining || 0) : 0;
      if (status) status.textContent = packageRemaining > 0
        ? `Можно открыть из пакета · осталось ${packageRemaining}`
        : (canBuyChapter ? 'Эту главу можно купить отдельно или открыть покупкой всей книги.' : 'Глава доступна после покупки всей книги.');
      if (paragraphs) {
        const packageButton = packageRemaining > 0
          ? `<button class="button-link gold-button" id="unlockChapterWithPackage" type="button">Открыть за 1 главу из пакета · осталось ${packageRemaining}</button>`
          : '';
        const chapterOffer = canBuyChapter
          ? `<p><b>Эта глава: ${Number(data.chapter.price_stars || 0)} Stars</b> · ≈ ${(Number(data.chapter.buyer_estimate_minor || 0) / 100).toFixed(2)} ₽</p>${data.purchase_url ? `<a class="button-link secondary" href="${escapeHtml(data.purchase_url)}">Купить только эту главу</a>` : ''}`
          : '<p>Отдельная покупка этой главы не предусмотрена.</p>';
        const bookOffer = data.book_purchase_url
          ? `<a class="button-link gold-button" href="${escapeHtml(data.book_purchase_url)}">Купить всю книгу · ${Number(data.chapter.book_price_stars || 0)} Stars</a>`
          : '';
        const packagesLink = canBuyChapter ? `<a class="quiet-link" href="/book/${Number(data.chapter.book_id)}#chapterPackages">Посмотреть пакеты глав</a>` : '';
        paragraphs.innerHTML = `<section class="empty-card paywall-card"><div class="empty-icon">◇</div><h3>Глава закрыта</h3>${chapterOffer}${packageButton}${bookOffer}${packagesLink}</section>`;
        const unlockButton = document.getElementById('unlockChapterWithPackage');
        if (unlockButton) {
          unlockButton.addEventListener('click', async () => {
            const approved = window.confirm(`Списать 1 открытие из пакета? После этого глава останется доступной навсегда. В пакете останется ${Math.max(0, packageRemaining - 1)}.`);
            if (!approved) return;
            unlockButton.disabled = true;
            unlockButton.textContent = 'Открываем главу…';
            try {
              await apiFetch(data.package_unlock_url || `/api/reader/${reader.dataset.chapterId}/unlock-package`, { method: 'POST' });
              window.location.reload();
            } catch (error) {
              unlockButton.disabled = false;
              unlockButton.textContent = `Открыть за 1 главу из пакета · осталось ${packageRemaining}`;
              notify(error.message || 'Не удалось использовать пакет');
            }
          });
        }
      }
      return;
    }
    const moderationNotice = document.getElementById('readerModerationNotice');
    const commentsBox = document.getElementById('commentsBox');
    const reactionsBox = document.getElementById('chapterReactions');
    const saveProgressButton = document.getElementById('saveReadingProgress');
    if (data.moderation_access) {
      if (moderationNotice) moderationNotice.hidden = false;
      if (commentsBox) commentsBox.hidden = true;
      if (reactionsBox) reactionsBox.hidden = true;
      const assistantPanel = document.getElementById('readerAssistantPanel');
      if (assistantPanel) assistantPanel.hidden = true;
      if (saveProgressButton) saveProgressButton.hidden = true;
      const quoteButton = document.getElementById('readerQuoteStart');
      if (quoteButton) quoteButton.hidden = true;
      if (status) status.textContent = 'Открыто в служебном режиме проверки';
    } else {
      if (moderationNotice) moderationNotice.hidden = true;
      if (commentsBox) commentsBox.hidden = false;
      if (reactionsBox) reactionsBox.hidden = false;
      const assistantPanel = document.getElementById('readerAssistantPanel');
      if (assistantPanel) { assistantPanel.hidden = false; assistantPanel.dataset.chapterId = String(data.chapter.id); }
      if (status) status.textContent = data.progress_percent ? `Продолжаем с отметки ${data.progress_percent}%` : 'Глава открыта';
    }
    applyContentProtection(data.protection, reader);
    const readerActions = document.querySelector('.reader-actions');
    if (readerActions && data.protection?.allow_download && data.protection?.download_url) {
      let downloadButton = document.getElementById('downloadBookText');
      if (!downloadButton) {
        downloadButton = document.createElement('button');
        downloadButton.id = 'downloadBookText';
        downloadButton.className = 'secondary';
        downloadButton.type = 'button';
        downloadButton.textContent = 'Скачать текст';
        readerActions.appendChild(downloadButton);
      }
      downloadButton.onclick = () => downloadAllowedBook(data.protection.download_url).catch((error) => notify(error.message));
    }
    updateReaderNavigation(data);
    const jumpInput = document.getElementById('chapterJumpNumber');
    if (jumpInput && data.chapter_bounds) {
      if (Number(data.chapter_bounds.min_number || 0) > 0) jumpInput.min = String(data.chapter_bounds.min_number);
      if (Number(data.chapter_bounds.max_number || 0) > 0) jumpInput.max = String(data.chapter_bounds.max_number);
    }
    if (paragraphs && data.chapter.text) {
      paragraphs.innerHTML = data.chapter.text.split('\n').filter((p) => p.trim()).map((p) => `<p>${escapeHtml(p)}</p>`).join('');
      setTimeout(() => {
        if (data.progress_percent > 0) scrollToReadingPercent(data.progress_percent, 'smooth');
        updateReaderProgressBar();
      }, 250);
    }
    renderComments(data.comments);
    renderChapterReactions(data.reactions);
  } catch (error) {
    if (status) status.textContent = 'Не удалось открыть главу. Попробуйте ещё раз.';
  }
}

function readerTtsPlayers() {
  return [document.getElementById('readerTtsPlayer'), document.getElementById('readerTtsBuffer')].filter(Boolean);
}

function readerTtsPlayer() {
  return readerTtsPlayers()[readerTtsStream.activeSlot] || readerTtsPlayers()[0] || null;
}

function readerTtsStandbyPlayer() {
  const players = readerTtsPlayers();
  return players[readerTtsStream.activeSlot === 0 ? 1 : 0] || null;
}

function readerTtsChapterId() {
  const panel = document.getElementById('readerTtsPanel');
  return Number(panel?.dataset.chapterId || document.getElementById('readerText')?.dataset.chapterId || 0);
}

function readerTtsCurrentProfile() {
  return {
    voice: document.getElementById('readerTtsVoice')?.value || getPrefs().ttsVoice,
    rate: Number(document.getElementById('readerTtsRate')?.value || getPrefs().ttsRate || 1),
    style: document.getElementById('readerTtsStyle')?.value || getPrefs().ttsStyle,
  };
}

function readerTtsProfileKey(profile = readerTtsCurrentProfile()) {
  return `${profile.voice}:${profile.style}:${Number(profile.rate || 1).toFixed(2)}`;
}

const readerTtsStream = {
  sessionId: '',
  segments: new Map(),
  segmentCount: 0,
  currentIndex: 0,
  activeSlot: 0,
  accumulatedSeconds: 0,
  estimatedTotalSeconds: 0,
  requestedPlay: false,
  switching: false,
  pollTimer: null,
  operation: 0,
  prefetchedChapter: null,
  nextPrepared: null,
  segmentRetries: new Map(),
  transitionKey: '',
  transitionPromise: null,
  watchdogTimer: null,
  lastProgressAt: 0,
  lastProgressPosition: 0,
  lastRecoveryAt: 0,
  firstAudioReported: false,
};

function updateReaderTtsStatus(text) {
  const status = document.getElementById('readerTtsStatus');
  if (status) status.textContent = text;
}

function reportReaderTtsEvent(event, details = {}, sessionId = readerTtsStream.sessionId) {
  if (!sessionId || !tgInitData()) return Promise.resolve(null);
  return apiFetch(`/api/reader/tts/session/${encodeURIComponent(sessionId)}/event`, {
    method: 'POST',
    body: JSON.stringify({
      event,
      segment_index: readerTtsStream.currentIndex,
      player_version: READER_TTS_PLAYER_VERSION,
      details,
    }),
  }).catch(() => null);
}

function resetReaderTtsPlayerVolume(player) {
  if (!player) return;
  player.volume = 1;
}

function fadeReaderTtsVolume(player, from, to, durationMs = READER_TTS_CROSSFADE_MS) {
  if (!player) return Promise.resolve();
  const started = performance.now();
  player.volume = Math.max(0, Math.min(1, Number(from)));
  return new Promise((resolve) => {
    const step = (now) => {
      const progress = Math.min(1, Math.max(0, (now - started) / Math.max(1, durationMs)));
      player.volume = Math.max(0, Math.min(1, Number(from) + (Number(to) - Number(from)) * progress));
      if (progress >= 1) resolve();
      else requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  });
}

async function crossfadeReaderTtsPlayers(oldPlayer, nextPlayer, { shouldPlay = true } = {}) {
  if (!nextPlayer) return;
  nextPlayer.currentTime = 0;
  nextPlayer.playbackRate = Number(readerTtsMeta?.playback_rate || getPrefs().ttsRate || 1);
  if (!shouldPlay) {
    oldPlayer?.pause();
    resetReaderTtsPlayerVolume(oldPlayer);
    resetReaderTtsPlayerVolume(nextPlayer);
    return;
  }
  nextPlayer.volume = 0;
  try {
    await nextPlayer.play();
  } catch (error) {
    oldPlayer?.pause();
    resetReaderTtsPlayerVolume(nextPlayer);
    try { await nextPlayer.play(); }
    catch (_) {
      reportReaderTtsEvent('autoplay_blocked', { reason: String(error?.message || 'play_failed') });
      throw new Error('Telegram остановил автоматическое продолжение. Нажмите воспроизведение один раз.');
    }
  }
  await Promise.all([
    fadeReaderTtsVolume(nextPlayer, 0, 1),
    oldPlayer && !oldPlayer.paused ? fadeReaderTtsVolume(oldPlayer, oldPlayer.volume, 0) : Promise.resolve(),
  ]);
  oldPlayer?.pause();
  resetReaderTtsPlayerVolume(oldPlayer);
  resetReaderTtsPlayerVolume(nextPlayer);
}

function setReaderTtsOptionsExpanded(expanded) {
  const panel = document.getElementById('readerTtsPanel');
  const options = document.getElementById('readerTtsOptions');
  const toggle = document.getElementById('readerTtsSettingsToggle');
  if (!panel || !options || !toggle) return;
  const open = Boolean(expanded);
  options.hidden = !open;
  toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  panel.classList.toggle('is-collapsed', !open);
}

function readerTtsTime(seconds) { return formatTime(Math.max(0, Number(seconds) || 0)); }

function readerTtsProgressStorageKey(chapterId, profile = readerTtsCurrentProfile()) {
  return `voxTtsStreamProgress:${Number(chapterId)}:${readerTtsProfileKey(profile)}`;
}

function getLocalReaderTtsProgress(chapterId, profile) {
  try {
    const value = JSON.parse(localStorage.getItem(readerTtsProgressStorageKey(chapterId, profile)) || '{}');
    return {
      segmentIndex: Math.max(0, Number(value.segmentIndex || 0)),
      segmentTime: Math.max(0, Number(value.segmentTime || 0)),
      positionSeconds: Math.max(0, Number(value.positionSeconds || 0)),
    };
  } catch (_) { return { segmentIndex: 0, segmentTime: 0, positionSeconds: 0 }; }
}

function readerTtsGlobalPosition() {
  const player = readerTtsPlayer();
  return Math.max(0, readerTtsStream.accumulatedSeconds + Number(player?.currentTime || 0));
}

function saveLocalReaderTtsProgress(chapterId, profile) {
  if (!chapterId) return;
  localStorage.setItem(readerTtsProgressStorageKey(chapterId, profile), JSON.stringify({
    segmentIndex: readerTtsStream.currentIndex,
    segmentTime: Math.max(0, Number(readerTtsPlayer()?.currentTime || 0)),
    positionSeconds: readerTtsGlobalPosition(),
    savedAt: Date.now(),
  }));
}

async function saveReaderTtsProgress() {
  const chapterId = Number(readerTtsMeta?.chapter?.id || readerTtsChapterId());
  if (!chapterId || !readerTtsStream.sessionId) return;
  const profile = {
    voice: readerTtsMeta?.voice || getPrefs().ttsVoice,
    rate: Number(readerTtsMeta?.playback_rate || getPrefs().ttsRate),
    style: readerTtsMeta?.style || getPrefs().ttsStyle,
  };
  const position = Math.floor(readerTtsGlobalPosition());
  saveLocalReaderTtsProgress(chapterId, profile);
  if (!tgInitData()) return;
  await apiFetch(`/api/reader/${chapterId}/tts/progress`, {
    method: 'POST',
    body: JSON.stringify({ position_seconds: position, voice: profile.voice, rate: profile.rate, style: profile.style }),
  });
}

function readerTtsKnownDuration(index) {
  return Math.max(0, Number(readerTtsStream.segments.get(Number(index))?.duration_ms || 0) / 1000);
}

function readerTtsRecalculateAccumulated(index) {
  let seconds = 0;
  for (let i = 0; i < Number(index); i += 1) seconds += readerTtsKnownDuration(i);
  readerTtsStream.accumulatedSeconds = seconds;
}

function readerTtsEstimateTotal() {
  let knownDuration = 0;
  let knownChars = 0;
  let allChars = 0;
  readerTtsStream.segments.forEach((segment) => {
    allChars += Number(segment.chars || 0);
    if (Number(segment.duration_ms || 0) > 0) {
      knownDuration += Number(segment.duration_ms) / 1000;
      knownChars += Number(segment.chars || 0);
    }
  });
  const charsPerSecond = knownChars > 0 && knownDuration > 0 ? knownChars / knownDuration : 13.5;
  const chapterChars = Number(readerTtsMeta?.diagnostics?.characters || allChars || 0);
  // currentTime и duration всегда относятся к исходной шкале аудио, независимо от playbackRate.
  readerTtsStream.estimatedTotalSeconds = chapterChars > 0 ? chapterChars / Math.max(5, charsPerSecond) : 0;
}

function updateReaderTtsProgressUi() {
  const position = readerTtsGlobalPosition();
  const total = Math.max(position, readerTtsStream.estimatedTotalSeconds || 0);
  const current = document.getElementById('readerTtsCurrentTime');
  const duration = document.getElementById('readerTtsTotalTime');
  const range = document.getElementById('readerTtsProgressRange');
  if (current) current.textContent = readerTtsTime(position);
  if (duration) duration.textContent = total > 0 ? readerTtsTime(total) : '—:—';
  if (range) {
    range.max = String(Math.max(1, Math.floor(total || 1)));
    range.value = String(Math.min(Number(range.max), Math.floor(position)));
  }
  const play = document.getElementById('readerTtsPlayPause');
  if (play) play.textContent = readerTtsPlayer()?.paused ? '▶' : '⏸';
}

async function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function openTtsDeviceCache() {
  if (!('caches' in window)) return null;
  try { return await caches.open(TTS_DEVICE_CACHE_NAME); }
  catch (_) { return null; }
}

function readerTtsSegmentCacheRequest(segment) {
  const digest = encodeURIComponent(String(segment?.digest || `${readerTtsStream.sessionId}-${segment?.index || 0}`));
  return new Request(`${TTS_DEVICE_CACHE_PREFIX}segment/${digest}.mp3`);
}

async function getCachedReaderTtsAudio(segment) {
  return readerTtsCachedSegmentSource(segment);
}

async function readerTtsCachedSegmentSource(segment) {
  const cache = await openTtsDeviceCache();
  if (!cache) return null;
  try {
    const request = readerTtsSegmentCacheRequest(segment);
    const response = await cache.match(request);
    if (!response) return null;
    const blob = await response.blob();
    if (blob.size <= 800) {
      await cache.delete(request);
      return null;
    }
    return URL.createObjectURL(blob);
  } catch (_) { return null; }
}

async function deleteCachedReaderTtsAudio(segment) {
  const cache = await openTtsDeviceCache();
  if (!cache || !segment) return false;
  try { return await cache.delete(readerTtsSegmentCacheRequest(segment)); }
  catch (_) { return false; }
}

async function cacheReaderTtsSegment(segment) {
  if (!segment?.url || readerTtsMeta?.device_cache_allowed === false) return false;
  const cache = await openTtsDeviceCache();
  if (!cache) return false;
  try {
    const request = readerTtsSegmentCacheRequest(segment);
    if (await cache.match(request)) return true;
    const response = await fetch(segment.url, { credentials: 'same-origin' });
    if (!response.ok) return false;
    await cache.put(request, response.clone());
    return true;
  } catch (_) { return false; }
}

async function apiFetchWithRetry(url, options = {}, attempts = 3, timeoutMs = 120000) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await apiFetch(url, Object.assign({}, options, { signal: controller.signal }));
    } catch (error) {
      lastError = error;
      const status = Number(error?.status || 0);
      const retryable = !status || status === 408 || status === 425 || status === 429 || status >= 500;
      if (!retryable || attempt >= attempts) throw error;
      await wait(450 * attempt);
    } finally { clearTimeout(timer); }
  }
  throw lastError || new Error('Не удалось связаться с сервером');
}

function mergeReaderTtsManifest(manifest) {
  if (!manifest) return;
  if (manifest.session_id) readerTtsStream.sessionId = manifest.session_id;
  if (manifest.segment_count) readerTtsStream.segmentCount = Number(manifest.segment_count);
  (manifest.segments || []).forEach((segment) => {
    const previous = readerTtsStream.segments.get(Number(segment.index)) || {};
    readerTtsStream.segments.set(Number(segment.index), Object.assign({}, previous, segment));
  });
  readerTtsEstimateTotal();
}

async function requestReaderTtsWindow(start, count = 10, operation = readerTtsStream.operation) {
  if (!readerTtsStream.sessionId || operation !== readerTtsStream.operation) return null;
  const manifest = await apiFetchWithRetry(
    `/api/reader/tts/session/${encodeURIComponent(readerTtsStream.sessionId)}?start=${Math.max(0, Number(start))}&count=${Math.max(1, Number(count))}`,
    {}, 3, 45000,
  );
  if (operation !== readerTtsStream.operation) return null;
  mergeReaderTtsManifest(manifest);
  return manifest;
}

async function waitForReaderTtsSegment(index, operation = readerTtsStream.operation, timeoutMs = 120000) {
  const started = Date.now();
  while (operation === readerTtsStream.operation && Date.now() - started < timeoutMs) {
    let segment = readerTtsStream.segments.get(Number(index));
    if (segment?.status === 'ready' && segment.url) return segment;
    if (segment?.status === 'failed') throw new Error(segment.error || 'Не удалось озвучить фрагмент.');
    await requestReaderTtsWindow(Math.max(0, Number(index) - 1), 6, operation);
    segment = readerTtsStream.segments.get(Number(index));
    if (segment?.status === 'ready' && segment.url) return segment;
    await wait(500);
  }
  throw new Error('Озвучивание готовится слишком долго. Повторите попытку.');
}

async function loadAudioElement(player, segment, operation = readerTtsStream.operation) {
  if (!player || !segment?.url) throw new Error('Аудиофрагмент недоступен.');
  const cachedSource = await readerTtsCachedSegmentSource(segment);
  const source = cachedSource || segment.url;
  releaseReaderTtsObjectUrl(player);
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      player.removeEventListener('canplay', ready);
      player.removeEventListener('loadedmetadata', ready);
      player.removeEventListener('error', failed);
    };
    const ready = () => {
      cleanup();
      if (operation !== readerTtsStream.operation) return reject(new Error('Операция отменена.'));
      player.dataset.segmentIndex = String(segment.index);
      player.dataset.segmentDigest = String(segment.digest || '');
      player.dataset.voxObjectUrl = cachedSource || '';
      player.playbackRate = Number(readerTtsMeta?.playback_rate || getPrefs().ttsRate || 1);
      if (!cachedSource) cacheReaderTtsSegment(segment).catch(() => {});
      resolve(player);
    };
    const failed = () => {
      cleanup();
      if (cachedSource) {
        try { URL.revokeObjectURL(cachedSource); } catch (_) {}
      }
      reject(new Error('Не удалось загрузить фрагмент.'));
    };
    cleanup();
    player.addEventListener('canplay', ready, { once: true });
    player.addEventListener('loadedmetadata', ready, { once: true });
    player.addEventListener('error', failed, { once: true });
    player.src = source;
    player.preload = 'auto';
    player.load();
  });
}

async function prepareReaderTtsSegment(index, player, operation = readerTtsStream.operation) {
  const segment = await waitForReaderTtsSegment(index, operation);
  await loadAudioElement(player, segment, operation);
  return segment;
}

function setReaderTtsSleep(minutes) {
  clearTimeout(readerTtsSleepTimer);
  readerTtsSleepTimer = null;
  if (!minutes) {
    updateReaderTtsStatus(readerTtsMeta ? `Глава ${readerTtsMeta.chapter.number} · таймер выключен` : 'Таймер сна выключен');
    notify('Таймер сна выключен');
    return;
  }
  readerTtsSleepTimer = setTimeout(() => {
    readerTtsPlayers().forEach((player) => player.pause());
    readerTtsStream.requestedPlay = false;
    saveReaderTtsProgress().catch(() => {});
    updateReaderTtsStatus('Таймер сна остановил озвучивание');
  }, Number(minutes) * 60_000);
  notify(`Таймер сна: ${minutes} мин`);
}

function updateReaderNavigation(meta) {
  const nav = document.querySelector('.chapter-navigation:not(.audio-navigation)');
  if (!nav || !meta?.chapter) return;
  const previous = meta.navigation?.previous;
  const next = meta.navigation?.next;
  nav.innerHTML = `${previous ? `<a href="/reader/${Number(previous.id)}" data-reader-nav="previous"><small>Предыдущая</small><strong>← ${escapeHtml(previous.title)}</strong></a>` : '<span></span>'}${next ? `<a href="/reader/${Number(next.id)}" data-reader-nav="next"><small>Следующая</small><strong>${escapeHtml(next.title)} →</strong></a>` : `<a href="/book/${Number(meta.chapter.book_id)}"><small>Конец</small><strong>К книге →</strong></a>`}`;
}

function updateReaderPageForTts(meta) {
  if (!meta?.chapter) return;
  const chapter = meta.chapter;
  const panel = document.getElementById('readerTtsPanel');
  const reader = document.getElementById('readerText');
  const status = document.getElementById('readerStatus');
  const paragraphs = document.getElementById('readerParagraphs');
  if (panel) { panel.dataset.chapterId = String(chapter.id); panel.dataset.bookId = String(chapter.book_id); }
  if (reader) reader.dataset.chapterId = String(chapter.id);
  if (status) { status.dataset.chapterId = String(chapter.id); status.textContent = meta.moderation_access ? 'Открыто в служебном режиме проверки' : 'Глава открыта'; }
  const moderationNotice = document.getElementById('readerModerationNotice');
  if (moderationNotice) moderationNotice.hidden = !meta.moderation_access;
  const commentsBox = document.getElementById('commentsBox');
  const reactionsBox = document.getElementById('chapterReactions');
  if (commentsBox) commentsBox.hidden = Boolean(meta.moderation_access);
  if (reactionsBox) reactionsBox.hidden = Boolean(meta.moderation_access);
  const assistantPanel = document.getElementById('readerAssistantPanel');
  if (assistantPanel) {
    assistantPanel.hidden = Boolean(meta.moderation_access);
    assistantPanel.dataset.chapterId = String(chapter.id);
    delete assistantPanel.dataset.loaded;
    const assistantBody = document.getElementById('readerAssistantBody');
    const assistantAnswer = document.getElementById('readerAssistantAnswer');
    if (assistantBody) assistantBody.hidden = true;
    if (assistantAnswer) assistantAnswer.hidden = true;
    const assistantToggle = document.getElementById('readerAssistantToggle');
    if (assistantToggle) { assistantToggle.textContent = 'Открыть'; assistantToggle.setAttribute('aria-expanded', 'false'); }
  }
  const saveProgressButton = document.getElementById('saveReadingProgress');
  if (saveProgressButton && meta.moderation_access) saveProgressButton.hidden = true;
  document.querySelector('.reader-toolbar-title small')?.replaceChildren(document.createTextNode(chapter.book_title || 'Книга'));
  document.querySelector('.reader-toolbar-title strong')?.replaceChildren(document.createTextNode(`Глава ${chapter.number}`));
  const kicker = document.querySelector('.reader-kicker');
  if (kicker) kicker.textContent = `${chapter.book_title || 'Книга'} · ${chapter.pen_name || 'Автор не указан'}`;
  const heading = reader?.querySelector('h1');
  if (heading) heading.textContent = chapter.title || `Глава ${chapter.number}`;
  if (paragraphs) paragraphs.innerHTML = String(chapter.text || '').split('\n').filter((item) => item.trim()).map((item) => `<p>${escapeHtml(item)}</p>`).join('');
  const jumpForm = document.getElementById('chapterJumpForm');
  if (jumpForm) jumpForm.dataset.bookId = String(chapter.book_id);
  const jumpInput = document.getElementById('chapterJumpNumber');
  if (jumpInput) jumpInput.value = String(chapter.number);
  const comments = document.getElementById('commentsList');
  if (comments) comments.innerHTML = '<p class="muted">Комментарии обновятся при обычном открытии главы.</p>';
  updateReaderNavigation(meta);
  document.title = `${chapter.title || `Глава ${chapter.number}`} — ${chapter.book_title || 'Вокслира'}`;
  history.pushState({ readerTts: true, chapterId: chapter.id }, '', `/reader/${Number(chapter.id)}?tts=1`);
  setTimeout(updateReaderProgressBar, 100);
}

function updateReaderTtsMediaSession(meta) {
  if (!('mediaSession' in navigator) || !meta?.chapter) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: meta.chapter.title || `Глава ${meta.chapter.number}`,
      artist: meta.chapter.pen_name || 'Автор не указан',
      album: meta.chapter.book_title || 'Вокслира',
      artwork: [{ src: `/media/cover/${Number(meta.chapter.book_id)}`, sizes: '512x512' }],
    });
  } catch (_) {}
}

function updateReaderTtsPositionState() {
  if (!('mediaSession' in navigator)) return;
  const total = Math.max(readerTtsGlobalPosition(), readerTtsStream.estimatedTotalSeconds || 0);
  if (total <= 0) return;
  try {
    navigator.mediaSession.setPositionState({
      duration: total,
      playbackRate: Number(readerTtsMeta?.playback_rate || 1),
      position: Math.min(total, readerTtsGlobalPosition()),
    });
  } catch (_) {}
}

async function readerTtsTogglePlay(forcePlay = null) {
  const active = readerTtsPlayer();
  if (!active?.src) return;
  const shouldPlay = forcePlay === null ? active.paused : Boolean(forcePlay);
  readerTtsStream.requestedPlay = shouldPlay;
  if (shouldPlay) {
    readerTtsPlayers().forEach((player) => { player.playbackRate = Number(readerTtsMeta?.playback_rate || getPrefs().ttsRate || 1); });
    try {
      await active.play();
      startReaderTtsWatchdog();
    } catch (error) {
      updateReaderTtsStatus('Нажмите кнопку воспроизведения ещё раз.');
      reportReaderTtsEvent('autoplay_blocked', { reason: String(error?.message || error) });
    }
  } else {
    active.pause();
    stopReaderTtsWatchdog();
  }
  updateReaderTtsProgressUi();
}

function bindReaderTtsMediaActions() {
  if (!('mediaSession' in navigator)) return;
  const handlers = {
    play: () => readerTtsTogglePlay(true),
    pause: () => readerTtsTogglePlay(false),
    seekbackward: (details) => seekReaderTts(-(details.seekOffset || 15)),
    seekforward: (details) => seekReaderTts(details.seekOffset || 15),
    seekto: (details) => { if (Number.isFinite(details.seekTime)) seekReaderTtsTo(Number(details.seekTime)); },
    previoustrack: () => { const id = readerTtsMeta?.navigation?.previous?.id; if (id) loadReaderTtsChapter(Number(id), true); },
    nexttrack: () => { const id = readerTtsMeta?.navigation?.next?.id; if (id) loadReaderTtsChapter(Number(id), true); },
  };
  Object.entries(handlers).forEach(([action, handler]) => { try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) {} });
}

async function preloadReaderTtsNextSegment(operation = readerTtsStream.operation) {
  const nextIndex = readerTtsStream.currentIndex + 1;
  if (nextIndex >= readerTtsStream.segmentCount || operation !== readerTtsStream.operation) return null;
  const standby = readerTtsStandbyPlayer();
  if (!standby) return null;
  if (Number(standby.dataset.segmentIndex) === nextIndex && standby.readyState >= 2) return standby;
  try {
    await prepareReaderTtsSegment(nextIndex, standby, operation);
    return standby;
  } catch (_) { return null; }
}

function mergePrefetchedReaderTtsManifest(meta, update) {
  if (!meta || !update) return meta;
  const byIndex = new Map((meta.segments || []).map((item) => [Number(item.index), item]));
  (update.segments || []).forEach((item) => {
    const previous = byIndex.get(Number(item.index)) || {};
    byIndex.set(Number(item.index), Object.assign({}, previous, item));
  });
  return Object.assign({}, meta, update, {
    chapter: meta.chapter,
    navigation: meta.navigation,
    moderation_access: meta.moderation_access,
    access_mode: meta.access_mode,
    device_cache_allowed: meta.device_cache_allowed,
    playback_rate: meta.playback_rate,
    segments: Array.from(byIndex.values()).sort((a, b) => Number(a.index) - Number(b.index)),
  });
}

async function waitForPrefetchedReaderTtsFirst(meta, operation, timeoutMs = 120000) {
  let prepared = meta;
  const started = Date.now();
  while (operation === readerTtsStream.operation && Date.now() - started < timeoutMs) {
    const first = (prepared?.segments || []).find((item) => Number(item.index) === 0);
    if (first?.status === 'ready' && first.url) return prepared;
    if (first?.status === 'failed') throw new Error(first.error || 'Не удалось подготовить следующую главу.');
    const update = await apiFetchWithRetry(
      `/api/reader/tts/session/${encodeURIComponent(prepared.session_id)}?start=0&count=3`, {}, 3, 45000,
    );
    prepared = mergePrefetchedReaderTtsManifest(prepared, update);
    await wait(450);
  }
  if (operation !== readerTtsStream.operation) return null;
  throw new Error('Следующая глава не успела подготовиться.');
}

async function primePrefetchedReaderTtsChapter(nextMeta, operation = readerTtsStream.operation) {
  if (!nextMeta?.chapter || operation !== readerTtsStream.operation) return false;
  if (readerTtsStream.currentIndex < readerTtsStream.segmentCount - 1) return false;
  const standby = readerTtsStandbyPlayer();
  const first = (nextMeta.segments || []).find((item) => Number(item.index) === 0);
  if (!standby || !first?.url) return false;
  if (standby.dataset.nextChapterId === String(nextMeta.chapter.id) && standby.readyState >= 2) return true;
  await loadAudioElement(standby, first, operation);
  standby.dataset.nextChapterId = String(nextMeta.chapter.id);
  standby.dataset.nextSessionId = String(nextMeta.session_id || '');
  return true;
}

async function startNextReaderTtsPrefetch(meta) {
  const nextId = Number(meta?.navigation?.next?.id || 0);
  if (!getPrefs().ttsAutoNext || !nextId) return null;
  const existing = readerTtsStream.prefetchedChapter;
  if (existing?.chapterId === nextId) {
    if (readerTtsStream.nextPrepared) primePrefetchedReaderTtsChapter(readerTtsStream.nextPrepared).catch(() => {});
    return existing.readyPromise || existing.promise || null;
  }
  const remaining = readerTtsStream.segmentCount - readerTtsStream.currentIndex - 1;
  const threshold = Math.max(1, Math.floor(readerTtsStream.segmentCount * 0.45));
  if (remaining > 6 && readerTtsStream.currentIndex < threshold) return null;
  const operation = readerTtsStream.operation;
  const currentSession = readerTtsStream.sessionId;
  const profile = { voice: meta.voice, rate: Number(meta.playback_rate || getPrefs().ttsRate), style: meta.style };
  reportReaderTtsEvent('chapter_prefetch_start', { next_chapter_id: nextId, remaining }, currentSession);
  const promise = apiFetchWithRetry(`/api/reader/${nextId}/tts/session`, {
    method: 'POST', body: JSON.stringify({ voice: profile.voice, style: profile.style, rate: profile.rate, high_quality: true }),
  }, 3, 120000);
  const holder = {
    chapterId: nextId,
    profileKey: readerTtsProfileKey(profile),
    promise,
    readyPromise: null,
    sessionId: '',
  };
  readerTtsStream.prefetchedChapter = holder;
  holder.readyPromise = promise.then(async (created) => {
    if (!created || operation !== readerTtsStream.operation) return null;
    holder.sessionId = String(created.session_id || '');
    const ready = await waitForPrefetchedReaderTtsFirst(created, operation);
    if (!ready || operation !== readerTtsStream.operation) return null;
    readerTtsStream.nextPrepared = ready;
    reportReaderTtsEvent('chapter_prefetch_ready', {
      next_chapter_id: nextId,
      first_provider: ready.segments?.[0]?.provider || '',
    }, currentSession);
    await primePrefetchedReaderTtsChapter(ready, operation).catch(() => false);
    return ready;
  }).catch((error) => {
    if (readerTtsStream.prefetchedChapter === holder) {
      holder.error = String(error?.message || error || 'prefetch_failed');
    }
    return null;
  });
  return holder.readyPromise;
}

async function switchReaderTtsSegment({ seamless = false, reason = 'ended' } = {}) {
  if (readerTtsStream.switching) return false;
  const operation = readerTtsStream.operation;
  const nextIndex = readerTtsStream.currentIndex + 1;
  const transitionKey = `${readerTtsStream.sessionId}:segment:${nextIndex}`;
  if (readerTtsStream.transitionKey === transitionKey) return false;
  readerTtsStream.switching = true;
  readerTtsStream.transitionKey = transitionKey;
  const oldPlayer = readerTtsPlayer();
  try {
    if (nextIndex >= readerTtsStream.segmentCount) {
      return await finishReaderTtsChapter(operation, { seamless, reason });
    }
    let nextPlayer = readerTtsStandbyPlayer();
    if (!nextPlayer || Number(nextPlayer.dataset.segmentIndex) !== nextIndex || nextPlayer.readyState < 2) {
      if (seamless) return false;
      updateReaderTtsStatus('Подгружаем следующий фрагмент…');
      nextPlayer = readerTtsStandbyPlayer();
      await prepareReaderTtsSegment(nextIndex, nextPlayer, operation);
    }
    if (operation !== readerTtsStream.operation) return false;
    reportReaderTtsEvent('segment_transition_start', { from: readerTtsStream.currentIndex, to: nextIndex, seamless, reason });
    const completedDuration = readerTtsKnownDuration(readerTtsStream.currentIndex) || Number(oldPlayer?.duration || 0);
    await crossfadeReaderTtsPlayers(oldPlayer, nextPlayer, { shouldPlay: readerTtsStream.requestedPlay });
    if (operation !== readerTtsStream.operation) return false;
    readerTtsStream.accumulatedSeconds += completedDuration;
    readerTtsStream.activeSlot = readerTtsStream.activeSlot === 0 ? 1 : 0;
    readerTtsStream.currentIndex = nextIndex;
    readerTtsStream.lastProgressAt = Date.now();
    readerTtsStream.lastProgressPosition = 0;
    updateReaderTtsStatus(`Глава ${readerTtsMeta.chapter.number} · фрагмент ${nextIndex + 1} из ${readerTtsStream.segmentCount}`);
    reportReaderTtsEvent('segment_transition_complete', { index: nextIndex, seamless, reason });
    preloadReaderTtsNextSegment(operation).catch(() => {});
    requestReaderTtsWindow(nextIndex + 1, 12, operation).catch(() => {});
    startNextReaderTtsPrefetch(readerTtsMeta).catch(() => {});
    return true;
  } catch (error) {
    updateReaderTtsStatus(error.message || 'Не удалось продолжить озвучивание.');
    reportReaderTtsEvent('player_error', { scope: 'segment_transition', reason: String(error?.message || error) });
    return false;
  } finally {
    readerTtsStream.switching = false;
    readerTtsStream.transitionKey = '';
    updateReaderTtsProgressUi();
  }
}

async function activatePrefetchedReaderTtsChapter(nextMeta, operation, { seamless = false, reason = 'ended' } = {}) {
  if (!nextMeta?.chapter || !nextMeta?.session_id || operation !== readerTtsStream.operation) return false;
  const nextId = Number(nextMeta.chapter.id);
  await primePrefetchedReaderTtsChapter(nextMeta, operation);
  const standby = readerTtsStandbyPlayer();
  const first = (nextMeta.segments || []).find((item) => Number(item.index) === 0);
  if (!standby || standby.dataset.nextChapterId !== String(nextId) || standby.readyState < 2) {
    if (!first?.url || seamless) return false;
    await loadAudioElement(standby, first, operation);
  }
  const oldSession = readerTtsStream.sessionId;
  const oldPlayer = readerTtsPlayer();
  reportReaderTtsEvent('chapter_transition_start', { next_chapter_id: nextId, seamless, reason }, oldSession);
  await crossfadeReaderTtsPlayers(oldPlayer, standby, { shouldPlay: readerTtsStream.requestedPlay });
  if (operation !== readerTtsStream.operation) return false;
  readerTtsMeta = nextMeta;
  readerTtsStream.activeSlot = readerTtsStream.activeSlot === 0 ? 1 : 0;
  readerTtsStream.sessionId = nextMeta.session_id;
  readerTtsStream.segments = new Map();
  readerTtsStream.segmentCount = Number(nextMeta.segment_count || 0);
  readerTtsStream.currentIndex = 0;
  readerTtsStream.accumulatedSeconds = 0;
  readerTtsStream.prefetchedChapter = null;
  readerTtsStream.nextPrepared = null;
  readerTtsStream.transitionKey = '';
  readerTtsStream.firstAudioReported = true;
  mergeReaderTtsManifest(nextMeta);
  updateReaderPageForTts(nextMeta);
  updateReaderTtsMediaSession(nextMeta);
  standby.dataset.nextChapterId = '';
  standby.dataset.nextSessionId = '';
  standby.dataset.segmentIndex = '0';
  standby.currentTime = Math.max(0, Number(standby.currentTime || 0));
  standby.playbackRate = Number(nextMeta.playback_rate || getPrefs().ttsRate || 1);
  updateReaderTtsStatus(`Глава ${nextMeta.chapter.number} · продолжаем без остановки`);
  reportReaderTtsEvent('chapter_transition_complete', { previous_session_id: oldSession, seamless, reason }, nextMeta.session_id);
  preloadReaderTtsNextSegment(operation).catch(() => {});
  requestReaderTtsWindow(1, 12, operation).catch(() => {});
  startNextReaderTtsPrefetch(nextMeta).catch(() => {});
  if (oldSession && oldSession !== nextMeta.session_id) apiFetch(`/api/reader/tts/session/${encodeURIComponent(oldSession)}`, { method: 'DELETE' }).catch(() => {});
  return true;
}

async function finishReaderTtsChapter(operation, { seamless = false, reason = 'ended' } = {}) {
  const nextId = Number(readerTtsMeta?.navigation?.next?.id || 0);
  if (!getPrefs().ttsAutoNext || !nextId) {
    if (seamless) return false;
    try { await saveReaderProgress(100); } catch (_) {}
    try { await saveReaderTtsProgress(); } catch (_) {}
    readerTtsStream.requestedPlay = false;
    updateReaderTtsStatus(nextId ? 'Глава закончена' : 'Книга закончена');
    return true;
  }
  let nextMeta = readerTtsStream.nextPrepared;
  const profileKey = readerTtsProfileKey({ voice: readerTtsMeta.voice, rate: readerTtsMeta.playback_rate, style: readerTtsMeta.style });
  const holder = readerTtsStream.prefetchedChapter;
  if (!nextMeta && holder?.chapterId === nextId && holder?.profileKey === profileKey) {
    if (seamless) return false;
    updateReaderTtsStatus('Подготавливаем продолжение…');
    nextMeta = await (holder.readyPromise || holder.promise);
  }
  if (!nextMeta && !seamless) {
    const started = await startNextReaderTtsPrefetch(readerTtsMeta);
    nextMeta = await started;
  }
  if (operation !== readerTtsStream.operation || !nextMeta) {
    if (!seamless) updateReaderTtsStatus('Продолжение ещё готовится. Нажмите воспроизведение через несколько секунд.');
    return false;
  }
  const activated = await activatePrefetchedReaderTtsChapter(nextMeta, operation, { seamless, reason });
  if (activated) {
    try { await saveReaderProgress(100); } catch (_) {}
    return true;
  }
  if (seamless) return false;
  updateReaderTtsStatus('Переключаем на следующую главу…');
  await loadReaderTtsChapter(nextId, true, nextMeta);
  return true;
}

function maybeStartReaderTtsBoundaryTransition(player) {
  if (!player || player !== readerTtsPlayer() || readerTtsStream.switching || !readerTtsStream.requestedPlay) return;
  const duration = Number(player.duration || 0);
  const current = Number(player.currentTime || 0);
  if (!duration || !Number.isFinite(duration)) return;
  const remaining = duration - current;
  if (remaining > READER_TTS_TRANSITION_LEAD_SECONDS) return;
  const lastSegment = readerTtsStream.currentIndex >= readerTtsStream.segmentCount - 1;
  if (!lastSegment) {
    const standby = readerTtsStandbyPlayer();
    const nextIndex = readerTtsStream.currentIndex + 1;
    if (standby && Number(standby.dataset.segmentIndex) === nextIndex && standby.readyState >= 2) {
      switchReaderTtsSegment({ seamless: true, reason: 'lead_time' }).catch(() => {});
    }
    return;
  }
  const nextMeta = readerTtsStream.nextPrepared;
  const standby = readerTtsStandbyPlayer();
  if (nextMeta && standby?.dataset.nextChapterId === String(nextMeta.chapter?.id || '') && standby.readyState >= 2) {
    switchReaderTtsSegment({ seamless: true, reason: 'lead_time' }).catch(() => {});
  }
}

async function recoverReaderTtsPlayback(reason = 'watchdog') {
  if (!readerTtsStream.sessionId || !readerTtsStream.requestedPlay || readerTtsStream.switching) return false;
  const now = Date.now();
  if (now - Number(readerTtsStream.lastRecoveryAt || 0) < 6000) return false;
  readerTtsStream.lastRecoveryAt = now;
  const operation = readerTtsStream.operation;
  const index = readerTtsStream.currentIndex;
  const player = readerTtsPlayer();
  const currentTime = Math.max(0, Number(player?.currentTime || 0));
  reportReaderTtsEvent('player_recovery_start', { reason, current_time: currentTime, ready_state: player?.readyState || 0 });
  updateReaderTtsStatus('Восстанавливаем непрерывное воспроизведение…');
  try {
    const duration = Number(player?.duration || 0);
    if (duration > 0 && duration - currentTime <= 1.0) {
      const switched = await switchReaderTtsSegment({ seamless: false, reason: `recovery_${reason}` });
      if (switched) {
        reportReaderTtsEvent('player_recovered', { reason, method: 'advance' });
        return true;
      }
    }
    await requestReaderTtsWindow(Math.max(0, index - 1), 6, operation);
    const segment = await waitForReaderTtsSegment(index, operation, 60000);
    await deleteCachedReaderTtsAudio(segment);
    await loadAudioElement(player, Object.assign({}, segment, { url: `${segment.url}&recover=${Date.now()}` }), operation);
    player.currentTime = Math.min(currentTime, Math.max(0, Number(player.duration || currentTime) - 0.05));
    resetReaderTtsPlayerVolume(player);
    if (readerTtsStream.requestedPlay) await player.play();
    readerTtsStream.lastProgressAt = Date.now();
    readerTtsStream.lastProgressPosition = Number(player.currentTime || 0);
    preloadReaderTtsNextSegment(operation).catch(() => {});
    updateReaderTtsStatus(`Глава ${readerTtsMeta?.chapter?.number || ''} · воспроизведение восстановлено`);
    reportReaderTtsEvent('player_recovered', { reason, method: 'reload_segment', current_time: player.currentTime });
    return true;
  } catch (error) {
    updateReaderTtsStatus('Не удалось восстановить звук автоматически. Нажмите ▶ один раз.');
    reportReaderTtsEvent('player_recovery_failed', { reason, error: String(error?.message || error) });
    return false;
  }
}

function startReaderTtsWatchdog() {
  if (readerTtsStream.watchdogTimer) return;
  readerTtsStream.lastProgressAt = Date.now();
  readerTtsStream.lastProgressPosition = Number(readerTtsPlayer()?.currentTime || 0);
  readerTtsStream.watchdogTimer = setInterval(() => {
    if (!readerTtsStream.sessionId || !readerTtsStream.requestedPlay || readerTtsStream.switching) return;
    const player = readerTtsPlayer();
    if (!player?.src) return;
    const current = Number(player.currentTime || 0);
    if (Math.abs(current - Number(readerTtsStream.lastProgressPosition || 0)) > 0.04) {
      readerTtsStream.lastProgressPosition = current;
      readerTtsStream.lastProgressAt = Date.now();
      return;
    }
    if (Date.now() - Number(readerTtsStream.lastProgressAt || 0) < READER_TTS_STALL_TIMEOUT_MS) return;
    reportReaderTtsEvent('player_stalled', {
      reason: 'watchdog_no_progress', current_time: current, ready_state: player.readyState, network_state: player.networkState,
    });
    recoverReaderTtsPlayback('watchdog_no_progress').catch(() => {});
  }, 2000);
}

function stopReaderTtsWatchdog() {
  clearInterval(readerTtsStream.watchdogTimer);
  readerTtsStream.watchdogTimer = null;
}

async function seekReaderTtsTo(targetSeconds) {
  if (!readerTtsStream.sessionId) return;
  const target = Math.max(0, Number(targetSeconds || 0));
  let elapsed = 0;
  let index = 0;
  for (; index < readerTtsStream.segmentCount; index += 1) {
    const duration = readerTtsKnownDuration(index);
    if (!duration) break;
    if (elapsed + duration >= target) break;
    elapsed += duration;
  }
  if (index >= readerTtsStream.segmentCount || !readerTtsKnownDuration(index)) {
    const current = readerTtsPlayer();
    if (target >= readerTtsStream.accumulatedSeconds && target <= readerTtsStream.accumulatedSeconds + Number(current?.duration || 0)) {
      current.currentTime = target - readerTtsStream.accumulatedSeconds;
    }
    return;
  }
  const operation = readerTtsStream.operation;
  const wasPlaying = readerTtsStream.requestedPlay;
  readerTtsPlayers().forEach((player) => player.pause());
  const player = readerTtsPlayer();
  await prepareReaderTtsSegment(index, player, operation);
  readerTtsStream.currentIndex = index;
  readerTtsStream.accumulatedSeconds = elapsed;
  player.currentTime = Math.max(0, target - elapsed);
  if (wasPlaying) await player.play().catch(() => {});
  preloadReaderTtsNextSegment(operation).catch(() => {});
  updateReaderTtsProgressUi();
}

function seekReaderTts(seconds) {
  seekReaderTtsTo(readerTtsGlobalPosition() + Number(seconds || 0)).catch(() => {});
}

async function closeReaderTtsSession() {
  const sessionId = readerTtsStream.sessionId;
  const prefetchedSessionId = String(readerTtsStream.prefetchedChapter?.sessionId || readerTtsStream.nextPrepared?.session_id || '');
  if (sessionId) await reportReaderTtsEvent('session_closed', {}, sessionId);
  readerTtsStream.operation += 1;
  stopReaderTtsWatchdog();
  clearTimeout(readerTtsStream.pollTimer);
  readerTtsStream.pollTimer = null;
  readerTtsPlayers().forEach((player) => {
    player.pause();
    resetReaderTtsPlayerVolume(player);
    releaseReaderTtsObjectUrl(player);
    player.removeAttribute('src');
    player.load();
    player.dataset.segmentIndex = '';
    player.dataset.nextChapterId = '';
    player.dataset.nextSessionId = '';
  });
  readerTtsStream.sessionId = '';
  readerTtsStream.segments = new Map();
  readerTtsStream.segmentCount = 0;
  readerTtsStream.currentIndex = 0;
  readerTtsStream.accumulatedSeconds = 0;
  readerTtsStream.estimatedTotalSeconds = 0;
  readerTtsStream.prefetchedChapter = null;
  readerTtsStream.nextPrepared = null;
  readerTtsStream.transitionKey = '';
  readerTtsStream.transitionPromise = null;
  readerTtsStream.lastProgressAt = 0;
  readerTtsStream.lastProgressPosition = 0;
  readerTtsStream.firstAudioReported = false;
  if (sessionId && tgInitData()) apiFetch(`/api/reader/tts/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }).catch(() => {});
  if (prefetchedSessionId && prefetchedSessionId !== sessionId && tgInitData()) {
    apiFetch(`/api/reader/tts/session/${encodeURIComponent(prefetchedSessionId)}`, { method: 'DELETE' }).catch(() => {});
  }
}

async function applyReaderTtsSession(meta, autoPlay = false) {
  if (!meta?.chapter || !meta?.session_id) throw new Error('Сервер не вернул сессию озвучивания.');
  readerTtsMeta = meta;
  setPref('ttsVoice', meta.voice || getPrefs().ttsVoice);
  setPref('ttsStyle', meta.style || getPrefs().ttsStyle);
  setPref('ttsRate', Number(meta.playback_rate || getPrefs().ttsRate || 1));
  readerTtsStream.sessionId = meta.session_id;
  readerTtsStream.segmentCount = Number(meta.segment_count || 0);
  readerTtsStream.currentIndex = 0;
  readerTtsStream.activeSlot = 0;
  readerTtsStream.accumulatedSeconds = 0;
  readerTtsStream.requestedPlay = Boolean(autoPlay);
  readerTtsStream.prefetchedChapter = null;
  readerTtsStream.nextPrepared = null;
  readerTtsStream.transitionKey = '';
  readerTtsStream.transitionPromise = null;
  readerTtsStream.lastProgressAt = Date.now();
  readerTtsStream.lastProgressPosition = 0;
  readerTtsStream.firstAudioReported = false;
  readerTtsStream.segmentRetries = new Map();
  readerTtsPlayers().forEach((player, index) => {
    resetReaderTtsPlayerVolume(player);
    player.dataset.nextChapterId = '';
    player.dataset.nextSessionId = '';
    if (index !== 0) player.dataset.segmentIndex = '';
  });
  mergeReaderTtsManifest(meta);
  updateReaderPageForTts(meta);
  updateReaderTtsMediaSession(meta);
  const panel = document.getElementById('readerTtsPanel');
  panel?.classList.add('has-audio');
  document.getElementById('readerTtsStreamControls')?.removeAttribute('hidden');
  document.getElementById('readerTtsControls')?.removeAttribute('hidden');
  document.getElementById('readerTtsSleep')?.removeAttribute('hidden');
  const operation = readerTtsStream.operation;
  updateReaderTtsStatus('Первый фрагмент готовится…');
  const first = await prepareReaderTtsSegment(0, readerTtsPlayers()[0], operation);
  if (!first || operation !== readerTtsStream.operation) return;
  reportReaderTtsEvent('session_applied', {
    chapter_id: meta.chapter.id,
    segment_count: readerTtsStream.segmentCount,
    first_provider: first.provider || '',
    player_version: READER_TTS_PLAYER_VERSION,
  });
  const profile = { voice: meta.voice, rate: Number(meta.playback_rate || 1), style: meta.style };
  const local = getLocalReaderTtsProgress(meta.chapter.id, profile);
  if (local.segmentIndex > 0 && local.segmentIndex < readerTtsStream.segmentCount) {
    await prepareReaderTtsSegment(local.segmentIndex, readerTtsPlayers()[0], operation);
    readerTtsStream.currentIndex = local.segmentIndex;
    readerTtsRecalculateAccumulated(local.segmentIndex);
    readerTtsStream.accumulatedSeconds = Math.max(readerTtsStream.accumulatedSeconds, local.positionSeconds - local.segmentTime);
    readerTtsPlayers()[0].currentTime = local.segmentTime;
  } else if (local.segmentTime > 0) readerTtsPlayers()[0].currentTime = local.segmentTime;
  readerTtsPlayers().forEach((player) => { player.playbackRate = Number(meta.playback_rate || 1); });
  updateReaderTtsStatus(`Глава ${meta.chapter.number} · озвучивание запущено`);
  preloadReaderTtsNextSegment(operation).catch(() => {});
  requestReaderTtsWindow(readerTtsStream.currentIndex + 1, 12, operation).catch(() => {});
  if (autoPlay) await readerTtsTogglePlay(true);
  updateReaderTtsProgressUi();
}

async function loadReaderTtsChapter(chapterId, autoPlay = false, preparedMeta = null) {
  if (readerTtsLoading || !chapterId) return;
  const panel = document.getElementById('readerTtsPanel');
  if (!panel) return;
  readerTtsLoading = true;
  panel.classList.add('is-generating');
  const startButton = document.getElementById('readerTtsStart');
  if (startButton) startButton.textContent = 'Подготавливаем…';
  updateReaderTtsStatus(navigator.onLine ? 'Запускаем первый фрагмент…' : 'Нет соединения с сервером…');
  const oldSession = readerTtsStream.sessionId;
  const oldPrefetchedSession = String(readerTtsStream.prefetchedChapter?.sessionId || readerTtsStream.nextPrepared?.session_id || '');
  const oldOperation = readerTtsStream.operation;
  readerTtsStream.operation += 1;
  const operation = readerTtsStream.operation;
  stopReaderTtsWatchdog();
  readerTtsPlayers().forEach((player) => {
    player.pause();
    resetReaderTtsPlayerVolume(player);
  });
  try {
    const profile = readerTtsCurrentProfile();
    const meta = preparedMeta || await apiFetchWithRetry(`/api/reader/${Number(chapterId)}/tts/session`, {
      method: 'POST',
      body: JSON.stringify({ voice: profile.voice, style: profile.style, rate: profile.rate, high_quality: true }),
    }, 3, 120000);
    if (operation !== readerTtsStream.operation) return;
    readerTtsStream.segments = new Map();
    await applyReaderTtsSession(meta, autoPlay);
    if (oldSession && oldSession !== meta.session_id) apiFetch(`/api/reader/tts/session/${encodeURIComponent(oldSession)}`, { method: 'DELETE' }).catch(() => {});
    if (oldPrefetchedSession && oldPrefetchedSession !== meta.session_id && oldPrefetchedSession !== oldSession) {
      apiFetch(`/api/reader/tts/session/${encodeURIComponent(oldPrefetchedSession)}`, { method: 'DELETE' }).catch(() => {});
    }
  } catch (error) {
    if (operation === readerTtsStream.operation) {
      updateReaderTtsStatus(error.message || 'Не удалось подготовить озвучивание');
      notify(error.message || 'Озвучивание недоступно');
    } else readerTtsStream.operation = oldOperation;
  } finally {
    readerTtsLoading = false;
    panel.classList.remove('is-generating');
    if (startButton) startButton.textContent = '▶ Озвучить';
  }
}

function bindReaderTtsPlayerEvents(player) {
  if (!player || player.dataset.voxBound === '1') return;
  player.dataset.voxBound = '1';
  player.addEventListener('play', () => {
    document.getElementById('readerTtsPanel')?.classList.add('is-playing');
    readerTtsStream.requestedPlay = true;
    readerTtsStream.lastProgressAt = Date.now();
    readerTtsStream.lastProgressPosition = Number(player.currentTime || 0);
    startReaderTtsWatchdog();
    if (!readerTtsStream.firstAudioReported && player === readerTtsPlayer()) {
      readerTtsStream.firstAudioReported = true;
      reportReaderTtsEvent('first_audio_play', {
        provider: readerTtsStream.segments.get(readerTtsStream.currentIndex)?.provider || '',
        chapter_id: readerTtsMeta?.chapter?.id || 0,
      });
    }
    try { navigator.mediaSession.playbackState = 'playing'; } catch (_) {}
    updateReaderTtsProgressUi();
  });
  player.addEventListener('pause', () => {
    if (readerTtsPlayers().every((item) => item.paused)) {
      document.getElementById('readerTtsPanel')?.classList.remove('is-playing');
      if (!readerTtsStream.switching) stopReaderTtsWatchdog();
    }
    try { navigator.mediaSession.playbackState = 'paused'; } catch (_) {}
    updateReaderTtsProgressUi();
  });
  player.addEventListener('timeupdate', () => {
    if (player !== readerTtsPlayer()) return;
    const current = Number(player.currentTime || 0);
    if (Math.abs(current - Number(readerTtsStream.lastProgressPosition || 0)) > 0.03) {
      readerTtsStream.lastProgressPosition = current;
      readerTtsStream.lastProgressAt = Date.now();
    }
    updateReaderTtsProgressUi();
    updateReaderTtsPositionState();
    clearTimeout(readerTtsProgressTimer);
    readerTtsProgressTimer = setTimeout(() => saveReaderTtsProgress().catch(() => {}), 4000);
    const remaining = Number(player.duration || 0) - current;
    if (remaining < 18) preloadReaderTtsNextSegment().catch(() => {});
    startNextReaderTtsPrefetch(readerTtsMeta).catch(() => {});
    maybeStartReaderTtsBoundaryTransition(player);
  });
  player.addEventListener('ended', () => {
    if (player !== readerTtsPlayer()) return;
    switchReaderTtsSegment({ seamless: false, reason: 'ended' }).catch(() => {});
  });
  player.addEventListener('stalled', () => {
    if (player === readerTtsPlayer() && !player.paused) {
      updateReaderTtsStatus('Связь нестабильна, удерживаем очередь…');
      reportReaderTtsEvent('player_stalled', {
        reason: 'media_stalled', current_time: player.currentTime, ready_state: player.readyState, network_state: player.networkState,
      });
    }
  });
  player.addEventListener('waiting', () => {
    if (player === readerTtsPlayer() && readerTtsStream.requestedPlay) {
      reportReaderTtsEvent('player_stalled', {
        reason: 'media_waiting', current_time: player.currentTime, ready_state: player.readyState, network_state: player.networkState,
      });
    }
  });
  player.addEventListener('error', async () => {
    if (player !== readerTtsPlayer() || !readerTtsStream.sessionId) return;
    const index = readerTtsStream.currentIndex;
    const retries = Number(readerTtsStream.segmentRetries.get(index) || 0);
    reportReaderTtsEvent('player_error', {
      scope: 'audio_element', retries, code: player.error?.code || 0, message: player.error?.message || '',
    });
    if (retries >= 2) {
      updateReaderTtsStatus('Не удалось загрузить фрагмент. Нажмите воспроизведение для повтора.');
      return;
    }
    readerTtsStream.segmentRetries.set(index, retries + 1);
    await recoverReaderTtsPlayback('audio_error');
  });
}

function bindReaderTtsLifecycleEvents() {
  if (readerTtsLifecycleBound) return;
  readerTtsLifecycleBound = true;
  window.addEventListener('online', () => {
    if (!readerTtsStream.sessionId) return;
    reportReaderTtsEvent('network_online', { requested_play: readerTtsStream.requestedPlay });
    if (readerTtsStream.requestedPlay) recoverReaderTtsPlayback('network_online').catch(() => {});
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible' || !readerTtsStream.sessionId) return;
    reportReaderTtsEvent('visibility_restore', { requested_play: readerTtsStream.requestedPlay });
    if (readerTtsStream.requestedPlay && readerTtsPlayer()?.paused) {
      readerTtsTogglePlay(true).catch(() => recoverReaderTtsPlayback('visibility_restore'));
    }
  });
  window.addEventListener('pagehide', () => {
    saveReaderTtsProgress().catch(() => {});
  });
}

async function initReaderTts() {
  const panel = document.getElementById('readerTtsPanel');
  if (!panel || !readerTtsPlayers().length) return;
  applySettings();
  migrateReaderTtsDeviceCache().catch(() => {});
  setReaderTtsOptionsExpanded(false);
  bindReaderTtsMediaActions();
  bindReaderTtsLifecycleEvents();
  readerTtsPlayers().forEach(bindReaderTtsPlayerEvents);
  if (!tgInitData()) {
    updateReaderTtsStatus('Откройте книгу внутри Telegram, чтобы включить озвучивание.');
    return;
  }
  try {
    const data = await apiFetchWithRetry('/api/reader/tts/voices', {}, 3, 30000);
    if (!data.enabled) {
      updateReaderTtsStatus(data.message || 'Озвучивание пока недоступно');
      document.getElementById('readerTtsStart')?.setAttribute('disabled', 'disabled');
      return;
    }
    const select = document.getElementById('readerTtsVoice');
    if (select && data.voices?.length) {
      select.innerHTML = data.voices.map((voice) => `<option value="${escapeHtml(voice.code)}">${escapeHtml(voice.label)}</option>`).join('');
      select.value = data.voices.some((item) => item.code === getPrefs().ttsVoice) ? getPrefs().ttsVoice : data.voices[0].code;
    }
    const styleSelect = document.getElementById('readerTtsStyle');
    if (styleSelect && data.styles?.length) {
      styleSelect.innerHTML = data.styles.map((style) => `<option value="${escapeHtml(style.code)}">${escapeHtml(style.label)}</option>`).join('');
      styleSelect.value = data.styles.some((item) => item.code === getPrefs().ttsStyle) ? getPrefs().ttsStyle : data.styles[0].code;
    }
    const rateSelect = document.getElementById('readerTtsRate');
    if (rateSelect && data.rates?.length) {
      const labels = { '0.75': '0.75× · медленно', '0.9': '0.9× · спокойно', '1': '1× · обычно', '1.15': '1.15× · быстрее', '1.3': '1.3× · быстро', '1.45': '1.45× · очень быстро' };
      rateSelect.innerHTML = data.rates.map((rate) => `<option value="${rate}">${labels[String(rate)] || `${rate}×`}</option>`).join('');
      rateSelect.value = data.rates.some((item) => Number(item) === Number(getPrefs().ttsRate)) ? String(getPrefs().ttsRate) : String(data.rates[0]);
    }
    updateReaderTtsStatus('Нажмите «Озвучить»');
  } catch (error) { updateReaderTtsStatus(error.message || 'Не удалось проверить озвучивание'); }
  window.addEventListener('online', () => {
    if (readerTtsStream.sessionId) requestReaderTtsWindow(readerTtsStream.currentIndex, 8).catch(() => {});
  });
}


// Старый адрес цельного файла включал &rate=${encodeURIComponent(rate)}&style=${encodeURIComponent(style)}.
// Совместимость проверок прежних версий: раньше переход выполнялся как loadReaderTtsChapter(nextId, true).
// В старом цельном MP3 использовалось player.playbackRate = 1; v1.10.5 меняет скорость без повторной генерации.
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


function assistantChapterLink(source) {
  const number = Number(source?.chapter_number || 0);
  const title = escapeHtml(source?.chapter_title || `Глава ${number}`);
  const href = Number(source?.chapter_id || 0) ? `/reader/${Number(source.chapter_id)}` : '#';
  return `<a class="reader-assistant-source" href="${href}">Глава ${number} · ${title}</a>`;
}

function renderBookAssistantContext(data) {
  const recapView = document.querySelector('[data-assistant-view="recap"]');
  const charactersView = document.querySelector('[data-assistant-view="characters"]');
  const termsView = document.querySelector('[data-assistant-view="terms"]');
  const notice = document.getElementById('readerAssistantNotice');
  if (notice) notice.textContent = data?.notice || 'Помощник использует только доступные вам главы до текущей включительно.';

  const recap = Array.isArray(data?.recap) ? data.recap : [];
  if (recapView) {
    const currentSummary = String(data?.current_summary || '').trim();
    const currentBlock = currentSummary
      ? `<article class="reader-assistant-current"><strong>Текущая глава</strong><p>${escapeHtml(currentSummary)}</p></article>`
      : '';
    const previous = recap.length
      ? `<div class="reader-assistant-list">${recap.map((item) => `<article><header>${assistantChapterLink(item)}</header><p>${escapeHtml(item.summary || '')}</p></article>`).join('')}</div>`
      : '<p class="muted">До текущей главы пока нет доступных глав для напоминания.</p>';
    recapView.innerHTML = `${currentBlock}<h3>Ранее</h3>${previous}`;
  }

  const characters = Array.isArray(data?.characters) ? data.characters : [];
  if (charactersView) {
    charactersView.innerHTML = characters.length
      ? `<div class="reader-assistant-entity-grid">${characters.map((item) => `<article><strong>${escapeHtml(item.name || '')}</strong><small>Упоминаний: ${Number(item.count || 0)} · впервые в доступном контексте: глава ${Number(item.chapter_number || 0)}</small><p>${escapeHtml(item.excerpt || '')}</p></article>`).join('')}</div>`
      : '<p class="muted">Персонажи пока не определены. Можно задать вопрос по имени вручную.</p>';
  }

  const terms = Array.isArray(data?.terms) ? data.terms : [];
  if (termsView) {
    termsView.innerHTML = terms.length
      ? `<div class="reader-assistant-entity-grid">${terms.map((item) => `<article><strong>${escapeHtml(item.term || '')}</strong><small>Упоминаний: ${Number(item.count || 0)} · глава ${Number(item.chapter_number || 0)}</small><p>${escapeHtml(item.excerpt || '')}</p></article>`).join('')}</div>`
      : '<p class="muted">Необычные термины в доступных главах пока не найдены.</p>';
  }
}

async function loadBookAssistantContext(force = false) {
  const panel = document.getElementById('readerAssistantPanel');
  if (!panel || !tgInitData()) return null;
  if (panel.dataset.loaded === '1' && !force) return null;
  const body = document.getElementById('readerAssistantBody');
  const notice = document.getElementById('readerAssistantNotice');
  if (notice) notice.textContent = 'Собираем события, персонажей и термины…';
  try {
    const data = await apiFetch(`/api/reader/${Number(panel.dataset.chapterId || 0)}/assistant`);
    renderBookAssistantContext(data);
    panel.dataset.loaded = '1';
    return data;
  } catch (error) {
    if (notice) notice.textContent = error.message || 'Не удалось открыть помощника.';
    if (body) body.hidden = false;
    return null;
  }
}

function renderBookAssistantAnswer(data) {
  const box = document.getElementById('readerAssistantAnswer');
  if (!box) return;
  const answer = String(data?.answer || '').trim();
  const paragraphs = answer.split(/\n{2,}/).filter(Boolean).map((part) => `<p>${escapeHtml(part)}</p>`).join('');
  const sources = Array.isArray(data?.sources) ? data.sources : [];
  const sourceMarkup = sources.length
    ? `<div class="reader-assistant-sources"><span>Основано на:</span>${sources.map(assistantChapterLink).join('')}</div>`
    : '';
  box.innerHTML = `${paragraphs || '<p>Надёжный ответ не найден.</p>'}${sourceMarkup}<small>${escapeHtml(data?.notice || '')}</small>`;
  box.hidden = false;
  box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function askBookAssistant(questionOverride = '') {
  const panel = document.getElementById('readerAssistantPanel');
  const field = document.getElementById('readerAssistantQuestion');
  const button = document.getElementById('readerAssistantAsk');
  const question = String(questionOverride || field?.value || '').trim();
  if (!panel || question.length < 3) { notify('Введите более точный вопрос'); return; }
  if (field && questionOverride) field.value = questionOverride;
  if (button) { button.disabled = true; button.textContent = 'Ищем в прочитанном…'; }
  try {
    const data = await apiFetch(`/api/reader/${Number(panel.dataset.chapterId || 0)}/assistant/ask`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    });
    renderBookAssistantAnswer(data);
  } catch (error) {
    notify(error.message || 'Помощник не смог ответить');
  } finally {
    if (button) { button.disabled = false; button.textContent = 'Спросить'; }
  }
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
      || (active === 'graphic' && card.dataset.graphic === '1')
      || (active === 'audio' && card.dataset.audio === '1')
      || (active === 'free' && card.dataset.free === '1')
      || (active === 'popular' && Number(card.dataset.popular || 0) > 0)
      || (['comic', 'manga', 'manhwa', 'webtoon', 'graphic_novel'].includes(active) && card.dataset.contentType === active);
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


function recommendationCard(item) {
  const bookId = Number(item.id || 0);
  const title = escapeHtml(item.title || 'Книга');
  const author = escapeHtml(item.pen_name || 'Автор не указан');
  const reason = escapeHtml(item.recommendation_reason || 'Подобрано для вас');
  const updated = encodeURIComponent(String(item.updated_at || bookId));
  const contentType = String(item.content_type || 'book');
  const isGraphic = contentType !== 'book';
  const firstTarget = Number(item.first_graphic_chapter_id || 0)
    ? `/comic/${Number(item.first_graphic_chapter_id)}`
    : Number(item.first_chapter_id || 0)
      ? `/reader/${Number(item.first_chapter_id)}`
      : `/book/${bookId}`;
  const actionText = isGraphic ? 'Смотреть' : 'Читать';
  const price = Number(item.price_stars || 0) > 0 ? `Вся книга: ${Number(item.price_stars)} Stars` : 'Бесплатно';
  const rating = Number(item.rating || 0) > 0 ? `<span>★ ${Number(item.rating).toFixed(1)}</span>` : '';
  const chapters = isGraphic ? `${Number(item.graphic_pages_count || 0)} стр.` : `${Number(item.chapters_count || 0)} глав`;
  return `<article class="book-card premium-book-card shelf recommendation-card" data-recommendation-card="${bookId}">
    <a class="book-cover-link" href="/book/${bookId}" data-recommendation-open="${bookId}" data-recommendation-reason="${reason}">
      <img class="cover-image cover-mini" src="/media/cover/${bookId}?v=${updated}" alt="Обложка произведения ${title}" loading="lazy" onerror="this.hidden=true;this.nextElementSibling.hidden=false">
      <div class="cover-mini" aria-hidden="true" hidden>${title.slice(0, 1)}</div>
      <span class="recommendation-reason">${reason}</span>
    </a>
    <div class="book-info">
      <div class="book-meta-line"><span>${escapeHtml(item.age_limit || '16+')}</span>${rating}<span>${chapters}</span></div>
      <a class="book-title-link" href="/book/${bookId}" data-recommendation-open="${bookId}" data-recommendation-reason="${reason}"><h3>${title}</h3></a>
      <p class="book-author">${author}</p>
      <div class="book-price-line"><strong>${price}</strong></div>
      <div class="card-actions">
        <a class="card-action primary" href="${firstTarget}" data-recommendation-open="${bookId}" data-recommendation-reason="${reason}">${actionText}</a>
        <button class="card-action save" type="button" data-card-bookmark="${bookId}">В библиотеку</button>
        <button class="card-action recommendation-dismiss" type="button" data-recommendation-dismiss="${bookId}" aria-label="Не показывать эту рекомендацию">Не интересно</button>
      </div>
    </div>
  </article>`;
}

async function loadForYouRecommendations() {
  const section = document.getElementById('forYouSection');
  const shelf = document.getElementById('forYouShelf');
  if (!section || !shelf || !tgInitData()) return;
  section.classList.add('loading');
  try {
    const data = await apiFetch('/api/recommendations/for-you?limit=12');
    const items = Array.isArray(data.items) ? data.items : [];
    if (!items.length) {
      section.hidden = true;
      return;
    }
    shelf.innerHTML = items.map(recommendationCard).join('');
    const subtitle = document.getElementById('forYouSubtitle');
    if (subtitle) subtitle.textContent = data.personalized
      ? 'Подобрано по вашим жанрам, авторам, чтению и оценкам.'
      : 'Стартовая подборка, пока VoxLyra знакомится с вашими интересами.';
    section.hidden = false;
    apiFetch('/api/recommendations/events', {
      method: 'POST',
      body: JSON.stringify({ event_type: 'impression', book_ids: items.map((item) => Number(item.id || 0)).filter(Boolean) }),
    }).catch(() => {});
  } catch (_) {
    section.hidden = true;
  } finally {
    section.classList.remove('loading');
  }
}

async function dismissRecommendation(bookId) {
  const card = document.querySelector(`[data-recommendation-card="${Number(bookId)}"]`);
  if (card) {
    card.classList.add('recommendation-removing');
    setTimeout(() => card.remove(), 180);
  }
  try {
    await apiFetch('/api/recommendations/events', {
      method: 'POST',
      body: JSON.stringify({ event_type: 'dismiss', book_id: Number(bookId) }),
    });
  } catch (_) {}
  const shelf = document.getElementById('forYouShelf');
  if (shelf && !shelf.querySelector('[data-recommendation-card]')) document.getElementById('forYouSection').hidden = true;
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
  const isPackage = item.purchase_kind === 'chapter_package';
  const isPremium = item.purchase_kind === 'premium';
  const packageTotal = Number(item.chapter_package_total ?? item.chapter_package_count ?? 0);
  let title = isPremium ? 'VoxLyra Premium' : isPackage ? (item.chapter_package_title || `Пакет на ${packageTotal} глав`) : (item.book_title || item.chapter_title || item.audio_title || 'Покупка');
  let href = isPremium ? '/premium' : item.audio_chapter_id ? `/audio/${Number(item.audio_chapter_id)}` : item.chapter_id ? `/reader/${Number(item.chapter_id)}` : item.book_id ? `/book/${Number(item.book_id)}` : '#';
  let type = isPremium ? 'Подписка' : isPackage ? 'Пакет глав' : item.audio_chapter_id ? 'Аудиоглава' : item.chapter_id ? 'Глава' : 'Книга';
  const stateText = refunded ? 'Возврат оформлен' : isPremium ? 'Управление подпиской' : isPackage ? `Осталось открытий: ${Number(item.chapter_package_remaining || 0)} из ${packageTotal}` : 'Доступ открыт';
  return `<a class="purchase-card${refunded ? ' refunded' : ''}" href="${href}"><div><span>${type}</span><h3>${escapeHtml(title)}</h3><p>${escapeHtml(stateText)}</p></div><b>${Number(item.amount_stars || 0)} Stars</b></a>`;
}

function renderLibraryTab(tab, data) {
  const content = document.getElementById('libraryContent');
  if (!content) return;
  if (tab === 'continue') {
    const reading = data.continue_reading || [];
    const audio = data.continue_listening || [];
    if (!reading.length && !audio.length) {
      content.innerHTML = emptyStateMarkup('history-empty', 'Продолжать пока нечего', 'Откройте любую главу или аудио — прогресс появится здесь.', '/catalog', 'Выбрать книгу');
      return;
    }
    content.innerHTML = `${reading.length ? `<div class="section-title slim"><h2>Чтение</h2></div><div class="library-continue-grid">${reading.map((item) => continueCard(item,'reading')).join('')}</div>` : ''}${audio.length ? `<div class="section-title slim"><h2>Аудио</h2></div><div class="library-continue-grid">${audio.map((item) => continueCard(item,'audio')).join('')}</div>` : ''}`;
    return;
  }
  if (tab === 'saved') {
    const items = data.bookmarks || [];
    content.innerHTML = items.length ? `<div class="book-list">${items.map(bookmarkCard).join('')}</div>` : emptyStateMarkup('no-bookmarks', 'Полка пока пустая', 'Добавляйте книги в библиотеку или любимое.', '/catalog', 'Открыть каталог');
    return;
  }
  const purchases = data.purchases || [];
  const packageBalances = (data.chapter_package_balances || []).filter((item) => Number(item.remaining_credits || 0) > 0);
  const packageBlock = packageBalances.length ? `<section class="library-package-balances"><div class="section-title slim"><h2>Доступные главы из пакетов</h2></div><div class="chapter-package-balance-grid">${packageBalances.map((item) => `<a class="chapter-package-balance-card" href="/book/${Number(item.book_id)}"><span>${escapeHtml(item.book_title || 'Книга')}</span><strong>${Number(item.remaining_credits || 0)} глав</strong><small>${escapeHtml(item.package_title || 'Пакет')} · использовано ${Number(item.used_credits || 0)} из ${Number(item.total_credits || 0)}</small></a>`).join('')}</div></section>` : '';
  content.innerHTML = packageBlock + (purchases.length ? `<div class="purchase-list">${purchases.map(purchaseCard).join('')}</div>` : emptyStateMarkup('no-books', 'Покупок пока нет', 'После покупки книги, главы, пакета или аудио доступ появится здесь.'));
}

function renderLibraryAchievements(payload, expanded = false) {
  const panel = document.getElementById('libraryAchievementPanel');
  const grid = document.getElementById('libraryAchievements');
  const toggle = document.getElementById('toggleAllAchievements');
  if (!panel || !grid) return;
  const items = payload?.items || [];
  panel.hidden = !items.length;
  const visible = expanded ? items : items.slice(0, 4);
  grid.innerHTML = visible.map((item) => `<article class="achievement-card"><span class="achievement-icon">${escapeHtml(item.icon || '✦')}</span><div><strong>${escapeHtml(item.title || 'Достижение')}</strong><p>${escapeHtml(item.description || '')}</p></div></article>`).join('');
  if (toggle) {
    toggle.hidden = items.length <= 4;
    toggle.textContent = expanded ? 'Свернуть' : 'Показать все';
    toggle.dataset.expanded = expanded ? '1' : '0';
  }
  (payload?.new || []).forEach((item) => notify(`Новое достижение: ${item.title}`));
}


function premiumDateLabel(value) {
  if (!value) return '';
  try { return new Date(value).toLocaleDateString('ru-RU', { day: '2-digit', month: 'long', year: 'numeric' }); }
  catch (_) { return String(value); }
}

function renderPremiumInsights(insights) {
  const section = document.getElementById('premiumInsights');
  const grid = document.getElementById('premiumInsightGrid');
  if (!section || !grid || !insights) return;
  const items = [
    ['📖', 'Завершено глав', Number(insights.chapters_finished || 0)],
    ['📚', 'Начато книг', Number(insights.books_started || 0)],
    ['🎧', 'Минут прослушано', Number(insights.listening_minutes || 0)],
    ['🔥', 'Серия чтения', `${Number(insights.reading_streak_days || 0)} дн.`],
    ['🔖', 'В библиотеке', Number(insights.saved_books || 0)],
    ['🕰', 'Любимое время', escapeHtml(insights.favorite_period || '—')],
  ];
  grid.innerHTML = items.map(([icon, label, value]) => `<article class="premium-insight-card"><span>${icon}</span><div><strong>${value}</strong><small>${label}</small></div></article>`).join('');
  section.hidden = false;
}

async function refreshPremiumPage() {
  const page = document.getElementById('premiumPage');
  if (!page) return;
  const loading = document.getElementById('premiumLoading');
  const content = document.getElementById('premiumContent');
  const error = document.getElementById('premiumError');
  if (!tgInitData()) {
    if (loading) loading.hidden = true;
    if (error) error.hidden = false;
    return;
  }
  try {
    const data = await apiFetch('/api/premium/status');
    const plan = (data.plans || [])[0] || {};
    const sub = data.subscription || {};
    document.getElementById('premiumPlanTitle').textContent = plan.title || 'VoxLyra Premium';
    document.getElementById('premiumPrice').textContent = Number(plan.price_stars || 0);
    document.getElementById('premiumPlanDescription').textContent = plan.description || '';
    const features = document.getElementById('premiumFeatures');
    if (features) features.innerHTML = (plan.features || []).map((item) => `<article><span>✦</span><b>${escapeHtml(item.title || '')}</b></article>`).join('');
    const subscribe = document.getElementById('premiumSubscribe');
    const cancel = document.getElementById('premiumCancelRenew');
    const resume = document.getElementById('premiumResumeRenew');
    const statusText = document.getElementById('premiumStatusText');
    if (sub.active) {
      if (subscribe) {
        subscribe.hidden = Boolean(sub.is_recurring);
        subscribe.textContent = 'Продлить ещё на 30 дней';
      }
      if (cancel) cancel.hidden = !(sub.is_recurring && sub.auto_renew);
      if (resume) resume.hidden = !(sub.is_recurring && !sub.auto_renew);
      if (statusText) statusText.textContent = `Premium активен до ${premiumDateLabel(sub.expires_at)}${sub.auto_renew ? ' · автопродление включено' : ' · без автопродления'}.`;
      renderPremiumInsights(data.insights);
    } else {
      if (subscribe) { subscribe.hidden = false; subscribe.textContent = 'Оформить Premium'; }
      if (cancel) cancel.hidden = true;
      if (resume) resume.hidden = true;
      if (statusText) statusText.textContent = 'Подписка не активна. Базовые функции VoxLyra продолжают работать.';
    }
    if (loading) loading.hidden = true;
    if (content) content.hidden = false;
    if (error) error.hidden = true;
  } catch (err) {
    if (loading) loading.hidden = true;
    if (content) content.hidden = true;
    if (error) error.hidden = false;
    const text = document.getElementById('premiumErrorText');
    if (text) text.textContent = err.message || 'Не удалось загрузить Premium.';
  }
}

async function openPremiumCheckout() {
  const button = document.getElementById('premiumSubscribe');
  if (button) button.disabled = true;
  try {
    const data = await apiFetch('/api/premium/checkout', { method: 'POST', body: JSON.stringify({ plan_code: 'monthly' }) });
    const tg = window.Telegram?.WebApp;
    if (tg?.openInvoice) {
      tg.openInvoice(data.invoice_link, (status) => {
        if (status === 'paid') {
          notify('Premium активирован');
          setTimeout(() => refreshPremiumPage(), 800);
        } else if (status === 'cancelled') notify('Оплата отменена');
        else if (status === 'failed') notify('Telegram не завершил оплату');
      });
    } else {
      window.location.assign(data.invoice_link);
    }
  } catch (err) { notify(err.message || 'Не удалось открыть оплату'); }
  finally { if (button) button.disabled = false; }
}

async function changePremiumRenew(enabled) {
  const button = document.getElementById(enabled ? 'premiumResumeRenew' : 'premiumCancelRenew');
  if (button) button.disabled = true;
  try {
    await apiFetch('/api/premium/auto-renew', { method: 'POST', body: JSON.stringify({ enabled }) });
    notify(enabled ? 'Автопродление включено' : 'Автопродление отключено');
    await refreshPremiumPage();
  } catch (err) { notify(err.message || 'Не удалось изменить подписку'); }
  finally { if (button) button.disabled = false; }
}

function initPremiumPage() {
  if (!document.getElementById('premiumPage')) return;
  document.getElementById('premiumSubscribe')?.addEventListener('click', openPremiumCheckout);
  document.getElementById('premiumCancelRenew')?.addEventListener('click', () => changePremiumRenew(false));
  document.getElementById('premiumResumeRenew')?.addEventListener('click', () => changePremiumRenew(true));
  refreshPremiumPage();
}

async function initLibrary() {
  const page = document.getElementById('libraryPage');
  if (!page) return;
  const content = document.getElementById('libraryContent');
  if (!tgInitData()) {
    if (content) content.innerHTML = emptyStateMarkup('no-books', 'Откройте внутри Telegram', 'Личная библиотека привязана к вашему Telegram-профилю.', '/catalog', 'Смотреть каталог');
    return;
  }
  try {
    const data = await loadMeData();
    page._libraryData = data;
    const profileName = String(data.user?.full_name || data.user?.username || '').trim();
    const initial = (profileName || 'В').slice(0, 1).toUpperCase();
    const profileInitial = document.getElementById('libraryProfileInitial');
    const profileFallback = document.getElementById('libraryProfileFallback');
    const profileHeading = document.getElementById('libraryProfileName');
    if (profileInitial) { profileInitial.textContent = initial; profileInitial.hidden = false; }
    if (profileFallback) profileFallback.hidden = true;
    if (profileHeading && profileName) profileHeading.textContent = `Моё · ${profileName.split(/\s+/)[0]}`;
    applyProfileFrame();
    renderLibraryAchievements(data.achievements);
    const premiumBadge = document.getElementById('libraryPremiumBadge');
    if (premiumBadge) premiumBadge.hidden = !Boolean(data.premium?.active);
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
    if (content) content.innerHTML = emptyStateMarkup('nothing-found', 'Не удалось открыть полку', 'Закройте Mini App и откройте его снова из бота.');
  }
}

let readerQuoteObjectUrl = '';

function setReaderQuoteMode(enabled) {
  const panel = document.getElementById('readerQuotePanel');
  const reader = document.getElementById('readerParagraphs');
  if (panel) panel.hidden = !enabled;
  reader?.classList.toggle('quote-selection-mode', Boolean(enabled));
  if (enabled) panel?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function clearReaderQuotePreview() {
  if (readerQuoteObjectUrl) URL.revokeObjectURL(readerQuoteObjectUrl);
  readerQuoteObjectUrl = '';
  const preview = document.getElementById('readerQuotePreview');
  const image = document.getElementById('readerQuoteImage');
  if (preview) preview.hidden = true;
  if (image) image.removeAttribute('src');
}

async function createReaderQuoteCard() {
  const chapterId = Number(document.getElementById('readerText')?.dataset.chapterId || 0);
  const quote = String(document.getElementById('readerQuoteText')?.value || '').trim();
  if (!chapterId || quote.length < 20) { notify('Выберите цитату длиной не менее 20 символов'); return; }
  const button = document.getElementById('readerQuoteCreate');
  if (button) { button.disabled = true; button.textContent = 'Создаём…'; }
  clearReaderQuotePreview();
  try {
    const response = await fetch(`/api/reader/${chapterId}/quote-card`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': tgInitData() },
      body: JSON.stringify({ quote, style: document.getElementById('readerQuoteStyle')?.value || 'standard' }),
    });
    if (!response.ok) {
      let message = 'Не удалось создать карточку';
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    readerQuoteObjectUrl = URL.createObjectURL(blob);
    const image = document.getElementById('readerQuoteImage');
    const preview = document.getElementById('readerQuotePreview');
    const download = document.getElementById('readerQuoteDownload');
    if (image) image.src = readerQuoteObjectUrl;
    if (download) download.href = readerQuoteObjectUrl;
    if (preview) preview.hidden = false;
  } catch (error) { notify(error.message || 'Не удалось создать карточку'); }
  finally { if (button) { button.disabled = false; button.textContent = 'Создать изображение'; } }
}

async function shareReaderQuoteCard() {
  if (!readerQuoteObjectUrl) return;
  try {
    const blob = await fetch(readerQuoteObjectUrl).then((response) => response.blob());
    const file = new File([blob], 'voxlyra_quote.png', { type: 'image/png' });
    if (navigator.canShare?.({ files: [file] })) {
      await navigator.share({ files: [file], title: 'Цитата из VoxLyra' });
      return;
    }
  } catch (_) {}
  document.getElementById('readerQuoteDownload')?.click();
}

function bindReaderQuoteCards() {
  const start = document.getElementById('readerQuoteStart');
  if (!start) return;
  const styleSelect = document.getElementById('readerQuoteStyle');
  if (styleSelect && tgInitData()) {
    apiFetch('/api/premium/status').then((data) => {
      const active = Boolean(data.subscription?.active);
      styleSelect.querySelectorAll('option').forEach((option) => {
        if (option.value !== 'standard') option.disabled = !active;
      });
      if (!active) styleSelect.value = 'standard';
    }).catch(() => {});
  }
  start.addEventListener('click', () => setReaderQuoteMode(true));
  document.getElementById('readerQuoteClose')?.addEventListener('click', () => { setReaderQuoteMode(false); clearReaderQuotePreview(); });
  document.getElementById('readerQuoteCancelMode')?.addEventListener('click', () => {
    document.getElementById('readerQuoteText').value = '';
    clearReaderQuotePreview();
    setReaderQuoteMode(false);
  });
  document.getElementById('readerQuoteCreate')?.addEventListener('click', createReaderQuoteCard);
  document.getElementById('readerQuoteShare')?.addEventListener('click', shareReaderQuoteCard);
  document.getElementById('readerParagraphs')?.addEventListener('click', (event) => {
    if (!event.currentTarget.classList.contains('quote-selection-mode')) return;
    const paragraph = event.target.closest('p');
    if (!paragraph) return;
    const textarea = document.getElementById('readerQuoteText');
    if (textarea) textarea.value = String(paragraph.textContent || '').trim().slice(0, 480);
    event.currentTarget.querySelectorAll('p.quote-selected').forEach((item) => item.classList.remove('quote-selected'));
    paragraph.classList.add('quote-selected');
    document.getElementById('readerQuotePanel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  });
}

function markActiveNav() {
  const path = window.location.pathname;
  document.querySelectorAll('.bottom-nav a').forEach((link) => {
    const nav = link.dataset.nav;
    const active = (nav === 'home' && path === '/') ||
      (nav === 'books' && (path.startsWith('/catalog') || path.startsWith('/book') || path.startsWith('/reader/'))) ||
      (nav === 'comics' && (path.startsWith('/comics') || path.startsWith('/comic/'))) ||
      (nav === 'audio' && path.startsWith('/audio')) || (nav === 'library' && (path.startsWith('/library') || path.startsWith('/author') || path.startsWith('/control'))) || (nav === 'settings' && path.startsWith('/settings'));
    link.classList.toggle('active', active);
  });
}

async function openChapterByNumber(form) {
  const bookId = Number(form?.dataset.bookId || 0);
  const input = form?.querySelector('input[name="chapter_number"]');
  const chapterNumber = Number(input?.value || 0);
  if (!bookId || !Number.isInteger(chapterNumber) || chapterNumber < 1) {
    notify('Введите номер главы');
    input?.focus();
    return;
  }
  try {
    const data = await apiFetch(`/api/book/${bookId}/chapter-number/${chapterNumber}`);
    window.location.href = data.reader_url;
  } catch (error) {
    notify(error.message || 'Такая глава не найдена');
    input?.focus();
    input?.select();
  }
}

function bindEvents() {
  document.addEventListener('input', (event) => { if (event.target.id === 'catalogSearch') applyCatalogFilter(); });
  document.addEventListener('change', async (event) => {
    if (event.target.id === 'readerTtsVoice') {
      setPref('ttsVoice', event.target.value);
      if (readerTtsStream.sessionId) await loadReaderTtsChapter(readerTtsChapterId(), readerTtsStream.requestedPlay);
      return;
    }
    if (event.target.id === 'readerTtsStyle') {
      setPref('ttsStyle', event.target.value);
      if (readerTtsStream.sessionId) await loadReaderTtsChapter(readerTtsChapterId(), readerTtsStream.requestedPlay);
      return;
    }
    if (event.target.id === 'readerTtsRate') {
      const rate = Number(event.target.value) || 1;
      setPref('ttsRate', rate);
      if (readerTtsMeta) readerTtsMeta.playback_rate = rate;
      readerTtsPlayers().forEach((player) => { player.playbackRate = rate; });
      readerTtsEstimateTotal();
      updateReaderTtsProgressUi();
      notify(`Скорость ${rate}×`);
      return;
    }
    if (event.target.id === 'readerTtsProgressRange') {
      await seekReaderTtsTo(Number(event.target.value || 0));
      return;
    }
    if (event.target.id === 'readerTtsAutoNext') {
      setPref('ttsAutoNext', Boolean(event.target.checked));
      notify(event.target.checked ? 'Автопереход включён' : 'Автопереход выключен');
    }
  });
  document.addEventListener('submit', async (event) => {
    if (event.target.id !== 'chapterJumpForm') return;
    event.preventDefault();
    await openChapterByNumber(event.target);
  });
  document.addEventListener('click', async (event) => {
    const target = event.target.closest('button, a');
    if (!target) return;

    if (target.id === 'refreshRecommendations') { event.preventDefault(); await loadForYouRecommendations(); return; }
    if (target.matches('[data-recommendation-dismiss]')) {
      event.preventDefault();
      await dismissRecommendation(Number(target.dataset.recommendationDismiss || 0));
      return;
    }
    if (target.matches('[data-recommendation-open]')) {
      apiFetch('/api/recommendations/events', {
        method: 'POST',
        body: JSON.stringify({
          event_type: 'open',
          book_id: Number(target.dataset.recommendationOpen || 0),
          reason: target.dataset.recommendationReason || '',
        }),
      }).catch(() => {});
    }

    if (target.matches('[data-profile-frame]')) { event.preventDefault(); setPref('profileFrame', target.dataset.profileFrame); notify('Рамка профиля сохранена'); return; }
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
    if (target.id === 'readerTtsSettingsToggle') { event.preventDefault(); setReaderTtsOptionsExpanded(target.getAttribute('aria-expanded') !== 'true'); return; }
    if (target.id === 'saveReadingProgress') { event.preventDefault(); try { await saveReaderProgress(); notify('Место сохранено'); } catch (_) { notify('Не удалось сохранить место'); } return; }
    if (target.id === 'readerTtsStart') { event.preventDefault(); await loadReaderTtsChapter(readerTtsChapterId(), true); return; }
    if (target.id === 'readerTtsPlayPause') { event.preventDefault(); await readerTtsTogglePlay(); return; }
    if (target.id === 'readerTtsBack') { event.preventDefault(); seekReaderTts(-15); return; }
    if (target.id === 'readerTtsForward') { event.preventDefault(); seekReaderTts(15); return; }
    if (target.id === 'readerTtsSave') { event.preventDefault(); try { await saveReaderTtsProgress(); notify('Место озвучивания сохранено'); } catch (_) { notify('Не удалось сохранить место'); } return; }
    if (target.matches('[data-tts-sleep]')) { event.preventDefault(); setReaderTtsSleep(Number(target.dataset.ttsSleep)); return; }
    if (target.matches('[data-reader-nav="next"]')) { event.preventDefault(); const href = target.href; try { await saveReaderProgress(100); } catch (_) {} window.location.href = href; return; }
    if (target.id === 'saveAudioProgress') { event.preventDefault(); try { await saveAudioProgress(); notify('Место сохранено'); } catch (_) { notify('Не удалось сохранить место'); } return; }
    if (target.id === 'audioBack') { event.preventDefault(); seekAudio(-getPrefs().rewindStep); return; }
    if (target.id === 'audioForward') { event.preventDefault(); seekAudio(getPrefs().rewindStep); return; }
    if (target.matches('[data-sleep-minutes]')) { event.preventDefault(); setSleepTimer(Number(target.dataset.sleepMinutes)); return; }
    if (target.matches('[data-catalog-filter]')) { event.preventDefault(); document.querySelectorAll('[data-catalog-filter]').forEach((btn) => btn.classList.remove('active')); target.classList.add('active'); applyCatalogFilter(); return; }
    if (target.matches('[data-open-graphic-filter]')) {
      event.preventDefault();
      const filter = document.querySelector('[data-catalog-filter="graphic"]');
      if (filter) {
        document.querySelectorAll('[data-catalog-filter]').forEach((btn) => btn.classList.remove('active'));
        filter.classList.add('active');
        document.getElementById('all-books')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
        applyCatalogFilter();
      }
      return;
    }
    if (target.id === 'toggleAllAchievements') {
      const page = document.getElementById('libraryPage');
      const expanded = target.dataset.expanded !== '1';
      if (page?._libraryData) renderLibraryAchievements(page._libraryData.achievements, expanded);
      return;
    }
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

    if (target.matches('[data-chapter-reaction]')) {
      event.preventDefault();
      const reader = document.getElementById('readerText');
      if (!reader) return;
      target.disabled = true;
      try {
        const data = await apiFetch(`/api/reader/${reader.dataset.chapterId}/reactions`, {
          method: 'POST',
          body: JSON.stringify({ reaction: target.dataset.chapterReaction || '' }),
        });
        renderChapterReactions(data.reactions);
      } catch (error) {
        notify(error.message || 'Не удалось сохранить реакцию');
      } finally {
        target.disabled = false;
      }
      return;
    }

    if (target.matches('[data-comment-spoiler-reveal]')) {
      event.preventDefault();
      const card = target.closest('.comment-card');
      const textNode = card?.querySelector('.comment-spoiler-text');
      if (!textNode) return;
      const willShow = Boolean(textNode.hidden);
      textNode.hidden = !willShow;
      target.textContent = willShow ? 'Скрыть спойлер' : 'Спойлер скрыт · показать';
      return;
    }

    if (target.matches('[data-comment-reply]')) {
      event.preventDefault();
      const commentsBox = document.getElementById('commentsBox');
      const replyBar = document.getElementById('commentReplyBar');
      const replyLabel = document.getElementById('commentReplyLabel');
      const field = document.getElementById('commentText');
      if (!commentsBox || !replyBar || !replyLabel || !field) return;
      commentsBox.dataset.replyTo = String(target.dataset.commentReply || '');
      replyLabel.textContent = `Ответ для ${target.dataset.commentName || 'читателя'}`;
      replyBar.hidden = false;
      field.focus();
      return;
    }

    if (target.id === 'cancelCommentReply') {
      event.preventDefault();
      const commentsBox = document.getElementById('commentsBox');
      const replyBar = document.getElementById('commentReplyBar');
      if (commentsBox) delete commentsBox.dataset.replyTo;
      if (replyBar) replyBar.hidden = true;
      return;
    }

    if (target.matches('[data-comment-like]')) {
      event.preventDefault();
      const commentId = Number(target.dataset.commentLike || 0);
      if (!commentId) return;
      target.disabled = true;
      try {
        const data = await apiFetch(`/api/comments/${commentId}/like`, { method: 'POST' });
        target.classList.toggle('selected', Boolean(data.liked));
        target.setAttribute('aria-pressed', data.liked ? 'true' : 'false');
        const count = target.querySelector('span');
        if (count) count.textContent = String(Number(data.like_count || 0));
      } catch (error) {
        notify(error.message || 'Не удалось поставить отметку');
      } finally {
        target.disabled = false;
      }
      return;
    }

    if (target.matches('[data-comment-report]')) {
      event.preventDefault();
      const commentId = Number(target.dataset.commentReport || 0);
      if (!commentId) return;
      const reason = window.prompt('Почему этот комментарий нужно проверить?');
      if (reason === null) return;
      if (reason.trim().length < 3) { notify('Опишите причину подробнее'); return; }
      target.disabled = true;
      try {
        const data = await apiFetch(`/api/comments/${commentId}/report`, {
          method: 'POST',
          body: JSON.stringify({ reason: reason.trim() }),
        });
        notify(data.message || 'Жалоба отправлена');
      } catch (error) {
        notify(error.message || 'Не удалось отправить жалобу');
      } finally {
        target.disabled = false;
      }
      return;
    }

    if (target.id === 'readerAssistantToggle') {
      event.preventDefault();
      const body = document.getElementById('readerAssistantBody');
      if (!body) return;
      const open = body.hidden;
      body.hidden = !open;
      target.textContent = open ? 'Свернуть' : 'Открыть';
      target.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) loadBookAssistantContext().catch(() => {});
      return;
    }

    if (target.matches('[data-assistant-tab]')) {
      event.preventDefault();
      const tab = String(target.dataset.assistantTab || 'recap');
      document.querySelectorAll('[data-assistant-tab]').forEach((button) => button.classList.toggle('active', button === target));
      document.querySelectorAll('[data-assistant-view]').forEach((view) => { view.hidden = view.dataset.assistantView !== tab; });
      return;
    }

    if (target.matches('[data-assistant-question]')) {
      event.preventDefault();
      askBookAssistant(String(target.dataset.assistantQuestion || '')).catch(() => {});
      return;
    }

    if (target.id === 'readerAssistantAsk') {
      event.preventDefault();
      askBookAssistant().catch(() => {});
      return;
    }

    if (target.id === 'sendComment') {
      event.preventDefault();
      const reader = document.getElementById('readerText');
      const field = document.getElementById('commentText');
      const commentsBox = document.getElementById('commentsBox');
      const spoiler = document.getElementById('commentSpoiler');
      if (!reader || !field) return;
      const textValue = String(field.value || '').trim();
      if (textValue.length < 2) { notify('Комментарий слишком короткий'); return; }
      target.disabled = true;
      try {
        const data = await apiFetch(`/api/reader/${reader.dataset.chapterId}/comments`, {
          method: 'POST',
          body: JSON.stringify({
            text: textValue,
            parent_id: Number(commentsBox?.dataset.replyTo || 0) || null,
            is_spoiler: Boolean(spoiler?.checked),
          }),
        });
        field.value = '';
        if (spoiler) spoiler.checked = false;
        if (commentsBox) delete commentsBox.dataset.replyTo;
        const replyBar = document.getElementById('commentReplyBar');
        if (replyBar) replyBar.hidden = true;
        renderComments(data.comments);
        notify('Комментарий опубликован');
      } catch (error) {
        notify(error.message || 'Комментарий не отправлен');
      } finally {
        target.disabled = false;
      }
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
    if (readerTtsPlayer()?.src) saveReaderTtsProgress().catch(() => {});
    if (document.getElementById('audioPage')) saveAudioProgress().catch(() => {});
    releaseReaderTtsObjectUrl();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initVoxSplash();
  applySettings();
  syncNotificationPreferences();
  markActiveNav();
  bindEvents();
  applyCatalogFilter();
  initContinueShelves();
  loadForYouRecommendations();
  initReader();
  initReaderTts();
  initAudioPage();
  initBookPage();
  initLibrary();
  initPremiumPage();
});

// v1.9.8 — единый возврат: экранная кнопка, Telegram BackButton и безопасный запасной маршрут.
(function initRouteNavigation() {
  const nav = document.getElementById('routeNav');
  const back = document.getElementById('routeBackButton');
  const tg = window.Telegram?.WebApp;
  const path = window.location.pathname || '/';
  const isHome = path === '/' || path === '';

  function fallbackUrl() {
    if (path.startsWith('/book/')) return '/catalog';
    if (path.startsWith('/reader/') || path.startsWith('/comic/') || path.startsWith('/audio/')) {
      const explicit = document.querySelector('[data-reader-book-url], .quiet-back[href^="/book/"]');
      return explicit?.getAttribute('data-reader-book-url') || explicit?.getAttribute('href') || '/library';
    }
    if (['/catalog', '/comics', '/library', '/settings', '/premium', '/author', '/control', '/audio'].some((prefix) => path === prefix || path.startsWith(`${prefix}/`))) return '/';
    return '/';
  }

  function canUseHistory() {
    if (window.history.length <= 1) return false;
    try {
      const ref = document.referrer ? new URL(document.referrer) : null;
      return Boolean(ref && ref.origin === window.location.origin && ref.pathname !== path);
    } catch (_) { return false; }
  }

  function goBack() {
    if (canUseHistory()) window.history.back();
    else window.location.assign(fallbackUrl());
  }

  if (!isHome) {
    if (nav) nav.hidden = false;
    back?.addEventListener('click', goBack);
    try {
      tg?.BackButton?.show();
      tg?.BackButton?.onClick(goBack);
    } catch (_) {}
  } else {
    if (nav) nav.hidden = true;
    try { tg?.BackButton?.hide(); } catch (_) {}
  }
})();
