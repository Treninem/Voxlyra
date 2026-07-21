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

(function routeTelegramMiniAppStartParam() {
  const path = window.location.pathname || '/';
  if (path !== '/' && path !== '') return;
  const query = new URLSearchParams(window.location.search);
  const unsafe = window.Telegram?.WebApp?.initDataUnsafe || {};
  const startParam = String(query.get('tgWebAppStartParam') || unsafe.start_param || '').trim();
  const bookMatch = /^book_(\d+)$/.exec(startParam);
  if (!bookMatch) return;
  const bookId = Number(bookMatch[1]);
  if (Number.isInteger(bookId) && bookId > 0) {
    // Telegram передаёт авторизационные параметры в адресе запуска. Сохраняем
    // query/hash при переходе, иначе новая страница может решить, что открыта
    // вне Mini App, и попросить пользователя вернуться в Telegram.
    window.location.replace(`/book/${bookId}${window.location.search || ''}${window.location.hash || ''}`);
  }
})();

(function handPublicBookLinkToTelegramMiniApp() {
  const match = /^\/book\/(\d+)\/?$/.exec(window.location.pathname || '');
  if (!match || window.Telegram?.WebApp?.initData) return;
  const username = String(document.querySelector('meta[name="voxlyra-bot-username"]')?.content || '').trim().replace(/^@/, '');
  const bookId = Number(match[1]);
  if (!username || !Number.isInteger(bookId) || bookId <= 0) return;
  window.location.replace(`https://t.me/${encodeURIComponent(username)}?startapp=book_${bookId}`);
})();

const root = document.documentElement;
const DEFAULTS = {
  theme: 'system', fontSize: 18, lineHeight: 1.78, readerWidth: 'normal',
  audioRate: 1, rewindStep: 15, autoplayNext: false, saveOnPause: true,
  ttsVoice: 'irina', ttsStyle: 'natural', ttsRate: 1, ttsAutoNext: true,
  notifications: true, notificationChapters: true, notificationAudio: true, notificationDiscounts: true,
  notificationReminders: true, notificationAchievements: true, notificationFollowedOnly: true,
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
const READER_TTS_PLAYER_VERSION = 'v1.11.1-final-continuity-1';
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
    notificationFollowedOnly: getStoredBool('voxNotificationFollowedOnly', DEFAULTS.notificationFollowedOnly),
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
    notificationFollowedOnly: 'voxNotificationFollowedOnly',
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
  ['voxTheme','readerFontSize','readerLineHeight','readerWidth','voxAudioRate','voxRewindStep','voxAutoplayNext','voxSaveOnPause','voxTtsVoice','voxTtsStyle','voxTtsRate','voxTtsAutoNext','voxNotifications','voxNotificationChapters','voxNotificationAudio','voxNotificationDiscounts','voxNotificationReminders','voxNotificationAchievements','voxNotificationFollowedOnly','voxContrast','voxFocusMode','voxShowReaderAds','voxProfileFrame','voxSeasonalDecor'].forEach((key) => localStorage.removeItem(key));
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
  notificationFollowedOnly: 'notifications_followed_only',
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
      notificationFollowedOnly: prefs.notifications_followed_only !== '0',
    };
    Object.entries(values).forEach(([key, value]) => {
      const storageKey = {
        notifications: 'voxNotifications', notificationChapters: 'voxNotificationChapters',
        notificationAudio: 'voxNotificationAudio', notificationDiscounts: 'voxNotificationDiscounts',
        notificationReminders: 'voxNotificationReminders', notificationAchievements: 'voxNotificationAchievements',
        notificationFollowedOnly: 'voxNotificationFollowedOnly',
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
  const resolvedKind = String(item.continue_kind || kind || 'reading');
  const isAudio = resolvedKind === 'audio';
  const isGraphic = resolvedKind === 'graphic';
  const href = item.continue_url || (isAudio
    ? `/audio/${Number(item.audio_chapter_id)}`
    : isGraphic
      ? `/comic/${Number(item.graphic_chapter_id)}#page=${Math.max(1, Number(item.page_number || 1))}`
      : `/reader/${Number(item.chapter_id)}`);
  const progress = isAudio
    ? (item.duration_seconds ? Math.min(100, Math.round(Number(item.position_seconds || 0) / Number(item.duration_seconds) * 100)) : 0)
    : isGraphic
      ? (item.pages_count ? Math.min(100, Math.round(Number(item.page_number || 1) / Number(item.pages_count) * 100)) : 0)
      : Math.max(0, Math.min(100, Number(item.position_percent || 0)));
  const subtitle = isAudio
    ? `Аудиоглава ${item.audio_number || ''} · ${formatTime(item.position_seconds || 0)}`
    : isGraphic
      ? `Глава ${item.graphic_number || ''} · страница ${Number(item.page_number || 1)} из ${Number(item.pages_count || 0) || '…'}`
      : `Глава ${item.chapter_number || ''} · ${progress}%`;
  const label = isAudio ? 'Слушаете' : isGraphic ? 'Смотрите' : 'Читаете';
  return `<a class="continue-card continue-${resolvedKind}" href="${href}">${dynamicCover(item, isAudio ? 'audio' : 'book')}<div><span>${label}</span><h3>${escapeHtml(item.title || 'Книга')}</h3><p>${escapeHtml(subtitle)}</p><div class="mini-progress"><i style="width:${progress}%"></i></div></div></a>`;
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

const VOX_PROGRESS_QUEUE_KEY = 'voxlyra_progress_sync_v11320';

function readProgressSyncQueue() {
  try {
    const value = JSON.parse(localStorage.getItem(VOX_PROGRESS_QUEUE_KEY) || '[]');
    return Array.isArray(value) ? value : [];
  } catch (_) { return []; }
}

function queueProgressSync(kind, targetId, position) {
  const cleanKind = String(kind || '');
  const cleanTarget = Number(targetId || 0);
  if (!['text', 'audio', 'graphic'].includes(cleanKind) || !cleanTarget) return;
  const items = readProgressSyncQueue().filter((item) => !(item.kind === cleanKind && Number(item.target_id) === cleanTarget));
  items.push({ kind: cleanKind, target_id: cleanTarget, position: Math.max(0, Number(position || 0)), queued_at: new Date().toISOString() });
  try { localStorage.setItem(VOX_PROGRESS_QUEUE_KEY, JSON.stringify(items.slice(-100))); } catch (_) {}
}

function dropProgressSync(kind, targetId) {
  const items = readProgressSyncQueue().filter((item) => !(item.kind === String(kind) && Number(item.target_id) === Number(targetId)));
  try { localStorage.setItem(VOX_PROGRESS_QUEUE_KEY, JSON.stringify(items)); } catch (_) {}
}

async function flushProgressSyncQueue() {
  if (!tgInitData()) return null;
  const updates = readProgressSyncQueue();
  if (!updates.length) {
    try { return await apiFetch('/api/progress/sync'); } catch (_) { return null; }
  }
  try {
    const result = await apiFetch('/api/progress/sync', { method: 'POST', body: JSON.stringify({ updates }) });
    localStorage.removeItem(VOX_PROGRESS_QUEUE_KEY);
    return result;
  } catch (_) { return null; }
}

window.queueVoxProgressSync = queueProgressSync;
window.dropVoxProgressSync = dropProgressSync;
window.flushVoxProgressSync = flushProgressSyncQueue;

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
  queueProgressSync('text', Number(reader.dataset.chapterId), percent);
  const result = await apiFetch(`/api/reader/${reader.dataset.chapterId}/progress`, { method: 'POST', body: JSON.stringify({ position_percent: percent }) });
  dropProgressSync('text', Number(reader.dataset.chapterId));
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
      // Бесплатная книга никогда не должна попадать на экран покупки. Если
      // сервер вернул противоречивое старое состояние, показываем безопасное
      // нейтральное сообщение и не предлагаем покупку за 0 Stars.
      const freeAccessExpected = data.pricing_mode === 'free' || Boolean(data.chapter?.is_free);
      if (freeAccessExpected) {
        if (status) status.textContent = 'Глава бесплатная. Обновляем доступ…';
        if (paragraphs) paragraphs.innerHTML = '<section class="empty-card access-card"><div class="empty-icon">✦</div><h3>Глава бесплатная</h3><p>Обновите страницу внутри Telegram. Покупка для этой главы не требуется.</p></section>';
        return;
      }
      const premiumRequired = Boolean(data.premium_required || data.chapter?.premium_required);
      const canBuyChapter = Boolean(data.can_buy_chapter) && Number(data.chapter?.price_stars || 0) > 0;
      const packageRemaining = canBuyChapter ? Number(data.package_credits?.remaining || 0) : 0;
      if (status) status.textContent = premiumRequired
        ? 'Эта глава доступна по VoxLyra Premium.'
        : packageRemaining > 0
          ? `Можно открыть из пакета · осталось ${packageRemaining}`
          : (canBuyChapter ? 'Эту главу можно купить отдельно или открыть покупкой всей книги.' : 'Глава доступна после покупки всей книги.');
      if (paragraphs) {
        if (premiumRequired) {
          paragraphs.innerHTML = `<section class="empty-card paywall-card premium-paywall-card"><div class="empty-icon">👑</div><h3>Глава доступна по VoxLyra Premium</h3><p>Подписка открывает эту главу и другие произведения, которые авторы включили в Premium.</p><a class="button-link premium-subscription-button" href="${escapeHtml(data.premium_url || '/premium')}">Открыть VoxLyra Premium</a></section>`;
          return;
        }
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
  const position = Math.floor(player.currentTime || 0);
  queueProgressSync('audio', Number(page.dataset.audioId), position);
  await apiFetch(`/api/audio/${page.dataset.audioId}/progress`, { method: 'POST', body: JSON.stringify({ position_seconds: position }) });
  dropProgressSync('audio', Number(page.dataset.audioId));
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
    const subscribeBook = document.getElementById('subscribeBook');
    const subscribeAuthor = document.getElementById('subscribeAuthor');
    if (subscribeBook && state.book_subscription) { subscribeBook.classList.add('saved'); subscribeBook.textContent = '🔔 Вы подписаны'; }
    if (subscribeAuthor && state.author_subscription) { subscribeAuthor.classList.add('saved'); subscribeAuthor.textContent = '✦ Вы подписаны на автора'; }
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
  const price = String(item.pricing_type || '') === 'premium' ? '👑 По подписке Premium' : Number(item.price_stars || 0) > 0 ? `Вся книга: ${Number(item.price_stars)} Stars` : 'Бесплатно';
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
    if (readingSection && data.continue_items?.length) {
      document.getElementById('continueShelf').innerHTML = data.continue_items.slice(0, 12).map((item) => continueCard(item, item.continue_kind)).join('');
      readingSection.hidden = false;
    }
    if (audioSection && data.continue_listening?.length) {
      document.getElementById('continueAudioShelf').innerHTML = data.continue_listening.map((item) => continueCard(item, 'audio')).join('');
      audioSection.hidden = false;
    }
  } catch (_) {}
}

function bookmarkCard(item, shelves = []) {
  const labels = { reading: 'Читаю', favorite: 'Любимое', planned: 'В планах', finished: 'Прочитано', dropped: 'Отложено' };
  const shelfOptions = shelves.length
    ? `<label class="library-shelf-picker">Добавить на полку<select data-shelf-book-picker="${Number(item.book_id)}"><option value="">Выберите полку</option>${shelves.map((shelf) => `<option value="${Number(shelf.id)}">${escapeHtml(shelf.icon || '📚')} ${escapeHtml(shelf.name || 'Полка')}</option>`).join('')}</select></label>`
    : '<small class="muted">Создайте свою полку во вкладке «Полки».</small>';
  return `<article class="book-card library-book-card">${dynamicCover(item)}<div class="book-info"><span class="eyebrow">${escapeHtml(labels[item.status] || 'В библиотеке')}</span><a href="/book/${Number(item.book_id)}"><h3>${escapeHtml(item.title || 'Книга')}</h3></a><p>${escapeHtml(item.pen_name || 'Автор не указан')}</p>${shelfOptions}</div></article>`;
}

function customShelfBookCard(item, shelfId) {
  return `<article class="custom-shelf-book">${dynamicCover(item)}<div><a href="/book/${Number(item.book_id)}"><h3>${escapeHtml(item.title || 'Книга')}</h3></a><p>${escapeHtml(item.pen_name || 'Автор не указан')}</p></div><button type="button" class="secondary compact-button" data-shelf-remove-book="${Number(item.book_id)}" data-shelf-id="${Number(shelfId)}">Убрать</button></article>`;
}

function historyCard(item) {
  const kind = String(item.content_type || 'text');
  const href = kind === 'audio' ? `/audio/${Number(item.target_id)}` : kind === 'graphic' ? `/comic/${Number(item.target_id)}#page=${Math.max(1, Number(item.position_value || 1))}` : `/reader/${Number(item.target_id)}`;
  const detail = kind === 'audio'
    ? `Аудиоглава ${Number(item.audio_number || 0) || ''} · ${formatTime(item.position_value || 0)}`
    : kind === 'graphic'
      ? `Глава ${Number(item.graphic_number || 0) || ''} · страница ${Math.max(1, Number(item.position_value || 1))}`
      : `Глава ${Number(item.chapter_number || 0) || ''} · ${Math.max(0, Math.min(100, Number(item.position_value || 0)))}%`;
  const label = kind === 'audio' ? 'Слушали' : kind === 'graphic' ? 'Смотрели' : 'Читали';
  return `<article class="history-card" data-history-card="${Number(item.id)}">${dynamicCover(item)}<div><span>${label}</span><a href="${href}"><h3>${escapeHtml(item.title || 'Книга')}</h3></a><p>${escapeHtml(detail)}</p><small>Открытий: ${Number(item.open_count || 1)}</small></div><button type="button" class="secondary compact-button" data-history-delete="${Number(item.id)}">Удалить</button></article>`;
}

function annotationCard(item) {
  const isQuote = String(item.annotation_type) === 'quote';
  const body = isQuote ? item.selected_text : item.note_text;
  return `<article class="annotation-card annotation-${escapeHtml(item.color || 'violet')}" data-annotation-card="${Number(item.id)}"><div class="annotation-card-head"><span>${isQuote ? 'Цитата' : 'Заметка'} · глава ${Number(item.chapter_number || 0)}</span><button type="button" class="quiet-link" data-annotation-delete="${Number(item.id)}">Удалить</button></div><a href="/reader/${Number(item.chapter_id)}"><h3>${escapeHtml(item.title || 'Книга')}</h3></a><p>${escapeHtml(body || '')}</p>${isQuote && item.note_text ? `<small>${escapeHtml(item.note_text)}</small>` : ''}</article>`;
}

function journalStatusLabel(status) {
  return ({ planned: 'В планах', reading: 'Читаю', paused: 'Пауза', finished: 'Завершено', dropped: 'Отложено' })[String(status || 'reading')] || 'Читаю';
}

function journalStatusOptions(current) {
  const items = [['planned','В планах'], ['reading','Читаю'], ['paused','Пауза'], ['finished','Завершено'], ['dropped','Отложено']];
  return items.map(([value, label]) => `<option value="${value}" ${String(current) === value ? 'selected' : ''}>${label}</option>`).join('');
}

function journalRatingOptions(current) {
  const selected = Math.max(0, Math.min(5, Number(current || 0)));
  return Array.from({ length: 6 }, (_, value) => `<option value="${value}" ${selected === value ? 'selected' : ''}>${value ? `${value} из 5` : 'Без оценки'}</option>`).join('');
}

function readingCycleStatusLabel(status) {
  return ({ reading: 'Читаю', paused: 'Пауза', finished: 'Завершено', dropped: 'Остановлено' })[String(status || 'reading')] || 'Читаю';
}

function readingCycleStatusOptions(current) {
  const labels = [['reading','Читаю'],['paused','Пауза'],['finished','Завершено'],['dropped','Остановлено']];
  return labels.map(([value,label]) => `<option value="${value}" ${String(current || 'reading') === value ? 'selected' : ''}>${label}</option>`).join('');
}

function readingCycleCard(cycle) {
  const number = Math.max(1, Number(cycle.cycle_number || 1));
  const label = number === 1 ? 'Первое чтение' : `${number}-й цикл`;
  return `<form class="reading-cycle-card${String(cycle.status) === 'finished' ? ' finished' : ''}" data-reading-cycle-form="${Number(cycle.id || 0)}">
    <div class="reading-cycle-heading"><div><span>${escapeHtml(label)}</span><strong>${escapeHtml(readingCycleStatusLabel(cycle.status))}</strong></div><small>${cycle.finished_on ? `завершено ${escapeHtml(activityDateLabel(cycle.finished_on))}` : cycle.started_on ? `начато ${escapeHtml(activityDateLabel(cycle.started_on))}` : 'даты не указаны'}</small></div>
    <div class="reading-cycle-fields">
      <label>Статус<select name="status">${readingCycleStatusOptions(cycle.status)}</select></label>
      <label>Начало<input type="date" name="started_on" value="${escapeHtml(cycle.started_on || '')}"></label>
      <label>Завершение<input type="date" name="finished_on" value="${escapeHtml(cycle.finished_on || '')}"></label>
    </div>
    <label class="reading-cycle-note">Заметка о цикле<textarea name="note" maxlength="2000" rows="2" placeholder="Что изменилось при повторном чтении…">${escapeHtml(cycle.note || '')}</textarea></label>
    <button type="submit" class="secondary compact-button">Сохранить цикл</button>
  </form>`;
}

function yearListOptions(item, context) {
  const currentYear = Number(context?.yearLists?.year || context?.summary?.current_year || new Date().getFullYear());
  const years = new Set([currentYear, ...(context?.yearLists?.available_years || []), ...(item.year_lists || []).map((row) => Number(row.list_year || 0))]);
  return [...years].filter((year) => year >= 1900).sort((a,b) => b-a).map((year) => `<option value="${year}" ${year === currentYear ? 'selected' : ''}>${year}</option>`).join('');
}

function yearListForm(item, context) {
  const currentYear = Number(context?.yearLists?.year || context?.summary?.current_year || new Date().getFullYear());
  const selected = new Set((item.year_lists || []).filter((row) => Number(row.list_year) === currentYear).map((row) => String(row.list_code || '')));
  const choices = [['best','Лучшее года'],['discovery','Открытие года'],['emotional','Самое эмоциональное'],['reread','Хочу перечитать']];
  return `<form class="reading-year-list-form" data-year-list-form="${Number(item.book_id || 0)}">
    <div class="reading-year-list-heading"><div><span class="eyebrow">Личные списки</span><strong>Мои итоги года</strong></div><label>Год<select name="year">${yearListOptions(item, context)}</select></label></div>
    <div class="reading-year-list-choices">${choices.map(([code,label]) => `<label><input type="checkbox" name="list_codes" value="${code}" ${selected.has(code) ? 'checked' : ''}><span>${label}</span></label>`).join('')}</div>
    <button type="submit" class="secondary compact-button">Сохранить списки</button>
  </form>`;
}

function startRereadMarkup(item) {
  const completed = Math.max(0, Number(item.completed_cycles || 0));
  if (!completed || item.active_cycle) return '';
  const today = new Date().toISOString().slice(0, 10);
  return `<form class="start-reread-form" data-reread-start-form="${Number(item.book_id || 0)}"><div><span class="eyebrow">Следующий круг</span><strong>Начать перечитывание</strong><small>Предыдущие даты и впечатления сохранятся.</small></div><label>Дата начала<input type="date" name="started_on" value="${today}"></label><input name="note" maxlength="2000" placeholder="Необязательная заметка"><button type="submit">Начать</button></form>`;
}

function readingJournalCard(item, context = {}) {
  const bookId = Number(item.book_id || 0);
  const activity = item.last_activity_at || item.history_updated_at || '';
  const detail = [
    journalStatusLabel(item.status),
    item.started_on ? `впервые начато ${activityDateLabel(item.started_on)}` : '',
    item.finished_on ? `последнее завершение ${activityDateLabel(item.finished_on)}` : '',
  ].filter(Boolean).join(' · ');
  const cycles = item.cycles || [];
  return `<article class="reading-journal-card" data-reading-journal-card="${bookId}">
    <div class="reading-journal-book">${dynamicCover(item)}<div><span class="eyebrow">${escapeHtml(detail || 'Личная запись')}</span><a href="/book/${bookId}"><h3>${escapeHtml(item.title || 'Произведение')}</h3></a><p>${escapeHtml(item.pen_name || 'Автор не указан')}</p><small>${Number(item.history_items || 0)} записей истории · ${Number(item.annotation_items || 0)} заметок и цитат · ${Number(item.completed_cycles || 0)} завершённых циклов${Number(item.reread_count || 0) ? ` · перечитано ${Number(item.reread_count || 0)} раз` : ''}${activity ? ` · активность ${escapeHtml(activityDateLabel(String(activity).slice(0,10)))}` : ''}</small></div></div>
    <form class="reading-journal-form" data-reading-journal-form="${bookId}">
      <div class="reading-journal-fields">
        <label>Общий статус<select name="status">${journalStatusOptions(item.status)}</select></label>
        <label>Первое начало<input type="date" name="started_on" value="${escapeHtml(item.started_on || '')}"></label>
        <label>Последнее завершение<input type="date" name="finished_on" value="${escapeHtml(item.finished_on || '')}"></label>
        <label>Личная оценка<select name="private_rating">${journalRatingOptions(item.private_rating)}</select></label>
      </div>
      <label class="reading-journal-impression">Мои впечатления<textarea name="impression" maxlength="6000" rows="4" placeholder="Что запомнилось, какие чувства осталось после чтения…">${escapeHtml(item.impression || '')}</textarea></label>
      <div class="reading-journal-actions"><button type="submit">Сохранить запись</button><button type="button" class="secondary" data-journal-delete="${bookId}">Очистить запись</button></div>
    </form>
    <section class="reading-cycle-panel"><div class="section-title slim"><h3>Циклы чтения</h3><p>Каждое перечитывание хранит собственные даты и заметку.</p></div>${cycles.length ? `<div class="reading-cycle-list">${cycles.map(readingCycleCard).join('')}</div>` : '<p class="muted">Цикл появится после начала чтения.</p>'}${startRereadMarkup(item)}</section>
    ${yearListForm(item, context)}
  </article>`;
}

function readingJournalSummaryMarkup(summary) {
  const data = summary || {};
  return `<div class="reading-journal-summary">
    <article><strong>${activityNumber(data.total)}</strong><small>в дневнике</small></article>
    <article><strong>${activityNumber(data.completed_cycles)}</strong><small>завершённых циклов</small></article>
    <article><strong>${activityNumber(data.completed_rereads)}</strong><small>перечитываний</small></article>
    <article><strong>${activityNumber(data.year_list_books)}</strong><small>в списках года</small></article>
  </div>`;
}

function completionCalendarDay(item) {
  const count = Math.max(0, Number(item.count || 0));
  const titles = (item.items || []).map((row) => `${row.title}${Number(row.cycle_number || 1) > 1 ? ` · цикл ${row.cycle_number}` : ''}`).join('; ');
  const label = item.future ? `${activityDateLabel(item.date)}: день ещё впереди` : count ? `${activityDateLabel(item.date)}: ${titles}` : `${activityDateLabel(item.date)}: завершений нет`;
  return `<span class="completion-calendar-day${count ? ' completed' : ''}${count > 1 ? ' multiple' : ''}${item.future ? ' future' : ''}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${count > 1 ? count : ''}</span>`;
}

function completionCalendarMarkup(calendar, yearLists) {
  const data = calendar || {};
  const lists = yearLists || {};
  const days = data.days || [];
  const years = data.available_years || [new Date().getFullYear()];
  const selectedYear = Number(data.year || new Date().getFullYear());
  const leading = days.length ? (new Date(`${days[0].date}T12:00:00Z`).getUTCDay() + 6) % 7 : 0;
  const placeholders = Array.from({length: leading}, () => '<span class="completion-calendar-day placeholder"></span>').join('');
  const groups = (lists.groups || []).map((group) => `<article class="year-list-group"><div><strong>${escapeHtml(group.label || 'Личный список')}</strong><small>${Number(group.count || 0)} произведений</small></div>${(group.items || []).length ? `<div>${group.items.map((item) => `<a href="/book/${Number(item.book_id || 0)}">${escapeHtml(item.title || 'Произведение')}</a>`).join('')}</div>` : '<p>Пока пусто</p>'}</article>`).join('');
  return `<section class="completion-calendar-panel">
    <div class="section-title split-title slim"><div><span class="eyebrow">Личная хронология</span><h2>Завершения за ${selectedYear} год</h2><p>${escapeHtml(data.privacy_note || 'Календарь виден только вам.')}</p></div><label class="year-activity-select">Год<select id="completionCalendarYear">${years.map((year) => `<option value="${Number(year)}" ${Number(year) === selectedYear ? 'selected' : ''}>${Number(year)}</option>`).join('')}</select></label></div>
    <div class="completion-calendar-summary"><article><strong>${Number(data.total_completions || 0)}</strong><small>завершений</small></article><article><strong>${Number(data.unique_books || 0)}</strong><small>произведений</small></article><article><strong>${Number(data.rereads || 0)}</strong><small>повторных циклов</small></article><article><strong>${Number(lists.total_items || 0)}</strong><small>в списках года</small></article></div>
    <div class="completion-calendar-scroll"><div class="completion-calendar-grid">${placeholders}${days.map(completionCalendarDay).join('')}</div></div>
    <div class="completion-calendar-legend"><i></i><span>завершённое произведение</span><i class="multiple"></i><span>несколько завершений в день</span></div>
    <div class="year-list-groups">${groups}</div>
  </section>`;
}

function applyReadingJournalPayload(page, result) {
  if (!page?._libraryData || !result) return;
  page._libraryData.reading_journal = result.reading_journal || page._libraryData.reading_journal || [];
  page._libraryData.reading_journal_summary = result.reading_journal_summary || page._libraryData.reading_journal_summary || {};
  page._libraryData.completion_calendar = result.completion_calendar || page._libraryData.completion_calendar || {};
  page._libraryData.year_lists = result.year_lists || page._libraryData.year_lists || {};
  page._libraryData.reread_summary = result.reread_summary || page._libraryData.reread_summary || {};
  page._libraryData.journal_import_history = result.journal_import_history || page._libraryData.journal_import_history || [];
}

async function downloadReadingJournal(format) {
  try {
    const response = await fetch(`/api/library/journal/export?format=${encodeURIComponent(format)}`, {
      headers: { 'X-Telegram-Init-Data': tgInitData() },
    });
    if (!response.ok) {
      let message = 'Не удалось выгрузить дневник';
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match?.[1] || `voxlyra-reading-diary.${format === 'csv' ? 'csv' : 'json'}`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = filename; link.rel = 'noopener';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('Экспорт подготовлен');
  } catch (error) { notify(error.message || 'Не удалось выгрузить дневник'); }
}


function journalImportActionLabel(action) {
  if (action === 'add') return 'добавить';
  if (action === 'update') return 'обновить';
  if (action === 'fill') return 'заполнить пустое';
  return 'восстановить';
}

function journalImportStatusLabel(status) {
  return ({ planned: 'в планах', reading: 'читаю', paused: 'пауза', finished: 'завершено', dropped: 'отложено' })[String(status || '')] || String(status || '');
}

function journalImportListLabel(code) {
  return ({ best: 'Лучшее года', discovery: 'Открытие года', emotional: 'Самое эмоциональное', reread: 'Хочу перечитать' })[String(code || '')] || String(code || '');
}

function journalImportSelectionItemMarkup(item) {
  const id = escapeHtml(item.selection_id || '');
  const actionCode = String(item.action || '');
  const kind = String(item.kind || '');
  const action = journalImportActionLabel(actionCode);
  const author = item.author ? ` · ${escapeHtml(item.author)}` : '';
  let meta = action;
  if (kind === 'journal') {
    meta += item.status ? ` · ${escapeHtml(journalImportStatusLabel(item.status))}` : '';
    if (Number(item.private_rating || 0) > 0) meta += ` · оценка ${Number(item.private_rating)}/5`;
    if (item.has_impression) meta += ' · есть впечатление';
  } else if (kind === 'cycle') {
    meta = `Цикл №${Number(item.cycle_number || 1)} · ${action}`;
    if (item.status) meta += ` · ${escapeHtml(journalImportStatusLabel(item.status))}`;
    if (item.started_on) meta += ` · с ${escapeHtml(item.started_on)}`;
    if (item.finished_on) meta += ` по ${escapeHtml(item.finished_on)}`;
    if (item.has_note) meta += ' · есть заметка';
  } else if (kind === 'year_list') {
    meta = `${escapeHtml(journalImportListLabel(item.list_code))} · ${Number(item.year || 0)} · ${action}`;
    if (item.has_note) meta += ' · есть заметка';
  }
  const dependency = item.requires_journal ? ` data-journal-import-depends="${escapeHtml(item.requires_journal)}"` : '';
  const searchText = escapeHtml([item.title, item.author, meta, journalImportActionLabel(actionCode), journalImportListLabel(item.list_code)].filter(Boolean).join(' ').toLocaleLowerCase('ru-RU'));
  return `<label class="journal-import-select-item" data-journal-import-item data-import-kind="${escapeHtml(kind)}" data-import-action="${escapeHtml(actionCode)}" data-import-search="${searchText}"><input type="checkbox" data-journal-import-select value="${id}"${dependency} checked><span><strong>${escapeHtml(item.title || 'Без названия')}</strong><small>${meta}${author}</small></span></label>`;
}

function journalImportSelectionSection(title, items, open = false) {
  const list = Array.isArray(items) ? items : [];
  if (!list.length) return '';
  return `<details class="journal-import-select-section" data-journal-import-group ${open ? 'open' : ''}><summary><span>${escapeHtml(title)}</span><b data-journal-import-group-count>${list.length}</b></summary><div class="journal-import-select-list">${list.map(journalImportSelectionItemMarkup).join('')}</div></details>`;
}

function readingJournalImportRoot() {
  return document.getElementById('readingJournalImportPreview');
}

function readingJournalImportItems(root = readingJournalImportRoot()) {
  return root ? [...root.querySelectorAll('[data-journal-import-item]')] : [];
}

function selectedReadingJournalImportIds() {
  const root = readingJournalImportRoot();
  return root ? [...root.querySelectorAll('[data-journal-import-select]:checked')].map((input) => String(input.value || '')).filter(Boolean) : [];
}

function enforceReadingJournalImportDependencies(root = readingJournalImportRoot()) {
  if (!root) return;
  const inputs = [...root.querySelectorAll('[data-journal-import-select]')];
  inputs.filter((input) => input.checked && input.dataset.journalImportDepends).forEach((input) => {
    const dependency = String(input.dataset.journalImportDepends || '');
    const dependencyInput = inputs.find((candidate) => String(candidate.value || '') === dependency);
    if (dependencyInput) dependencyInput.checked = true;
  });
  inputs.filter((input) => !input.checked && String(input.value || '').startsWith('journal:')).forEach((input) => {
    inputs.forEach((candidate) => {
      if (String(candidate.dataset.journalImportDepends || '') === String(input.value || '')) candidate.checked = false;
    });
  });
}

function applyReadingJournalImportFilters(root = readingJournalImportRoot()) {
  if (!root) return;
  const query = String(root.querySelector('[data-journal-import-search]')?.value || '').trim().toLocaleLowerCase('ru-RU');
  const kind = String(root.querySelector('[data-journal-import-kind-filter]')?.value || 'all');
  const action = String(root.querySelector('[data-journal-import-action-filter]')?.value || 'all');
  const filtering = Boolean(query || kind !== 'all' || action !== 'all');
  let visible = 0;
  readingJournalImportItems(root).forEach((item) => {
    const matchesQuery = !query || String(item.dataset.importSearch || '').includes(query);
    const matchesKind = kind === 'all' || String(item.dataset.importKind || '') === kind;
    const matchesAction = action === 'all' || String(item.dataset.importAction || '') === action || (action === 'update_or_fill' && ['update', 'fill'].includes(String(item.dataset.importAction || '')));
    item.hidden = !(matchesQuery && matchesKind && matchesAction);
    if (!item.hidden) visible += 1;
  });
  root.querySelectorAll('[data-journal-import-group]').forEach((group) => {
    const groupItems = [...group.querySelectorAll('[data-journal-import-item]')];
    const groupVisible = groupItems.filter((item) => !item.hidden).length;
    const groupSelected = groupItems.filter((item) => !item.hidden && item.querySelector('[data-journal-import-select]')?.checked).length;
    group.hidden = groupVisible <= 0;
    if (filtering && groupVisible > 0) group.open = true;
    const count = group.querySelector('[data-journal-import-group-count]');
    if (count) count.textContent = `${groupSelected}/${groupVisible}`;
  });
  const visibleCounter = root.querySelector('[data-journal-import-visible-count]');
  if (visibleCounter) visibleCounter.textContent = String(visible);
  const empty = root.querySelector('[data-journal-import-filter-empty]');
  if (empty) empty.hidden = visible > 0;
}

function updateReadingJournalImportSelection() {
  const root = readingJournalImportRoot();
  if (!root) return;
  enforceReadingJournalImportDependencies(root);
  applyReadingJournalImportFilters(root);
  const selected = selectedReadingJournalImportIds();
  const total = root.querySelectorAll('[data-journal-import-select]').length;
  const visibleSelected = readingJournalImportItems(root).filter((item) => !item.hidden && item.querySelector('[data-journal-import-select]')?.checked).length;
  const counter = root.querySelector('[data-journal-import-selected-count]');
  const apply = root.querySelector('[data-journal-import-apply]');
  if (counter) counter.textContent = `${selected.length} из ${total}`;
  const visibleSelection = root.querySelector('[data-journal-import-visible-selected]');
  if (visibleSelection) visibleSelection.textContent = String(visibleSelected);
  if (apply) {
    apply.disabled = selected.length <= 0;
    apply.textContent = `Применить выбранное: ${selected.length}`;
  }
}

function selectReadingJournalImportPreset(preset) {
  const root = readingJournalImportRoot();
  if (!root) return;
  const items = readingJournalImportItems(root);
  items.forEach((item) => {
    const input = item.querySelector('[data-journal-import-select]');
    if (!input) return;
    const action = String(item.dataset.importAction || '');
    const kind = String(item.dataset.importKind || '');
    if (preset === 'all') input.checked = true;
    else if (preset === 'none') input.checked = false;
    else if (preset === 'visible') { if (!item.hidden) input.checked = true; }
    else if (preset === 'visible-none') { if (!item.hidden) input.checked = false; }
    else if (preset === 'new') input.checked = action === 'add';
    else if (preset === 'updates') input.checked = action === 'update' || action === 'fill';
    else if (preset === 'cycles') input.checked = kind === 'cycle';
  });
  updateReadingJournalImportSelection();
}

function readingJournalImportPreviewMarkup(preview) {
  if (!preview) return '<div id="readingJournalImportPreview"></div>';
  const changes = preview.changes || {};
  const source = preview.source_counts || {};
  const ignored = preview.ignored_sections || {};
  const selectable = preview.selectable_items || {};
  const added = Number(changes.journal_add || 0) + Number(changes.cycles_add || 0) + Number(changes.year_lists_add || 0);
  const updated = Number(changes.journal_update || 0) + Number(changes.cycles_update || 0) + Number(changes.year_lists_update || 0);
  const filled = Number(changes.journal_fill || 0) + Number(changes.cycles_fill || 0) + Number(changes.year_lists_fill || 0);
  const unchanged = Number(changes.journal_unchanged || 0) + Number(changes.cycles_unchanged || 0) + Number(changes.year_lists_unchanged || 0);
  const protectedCount = Number(preview.total_protected || 0);
  const ignoredCount = Number(ignored.history || 0) + Number(ignored.annotations || 0) + Number(ignored.daily_activity || 0);
  const missing = (preview.missing_books || []).slice(0, 8);
  const protectedExamples = (preview.protected_examples || []).slice(0, 8);
  const details = [];
  if (missing.length) details.push(`<details><summary>Не найденные произведения: ${Number(preview.missing_book_count || missing.length)}</summary><ul>${missing.map((item) => `<li><strong>${escapeHtml(item.title || 'Без названия')}</strong>${item.author ? ` — ${escapeHtml(item.author)}` : ''}</li>`).join('')}</ul></details>`);
  if (protectedExamples.length) details.push(`<details><summary>Защищённые текущие записи: ${protectedCount}</summary><ul>${protectedExamples.map((item) => `<li><strong>${escapeHtml(item.title || 'Произведение')}</strong> · ${escapeHtml(item.section || '')}: ${escapeHtml(item.reason || '')}</li>`).join('')}</ul></details>`);
  const ignoredNote = ignoredCount ? `<p class="journal-import-note">История позиций, заметки/цитаты и дневная активность из файла не импортируются: они привязаны к внутренним главам и остаются в текущем аккаунте без изменений.</p>` : '';
  const selectionSections = [
    journalImportSelectionSection('Произведения и впечатления', selectable.journal || [], true),
    journalImportSelectionSection('Циклы чтения и перечитывания', selectable.cycles || []),
    journalImportSelectionSection('Отметки в списках года', selectable.year_lists || []),
  ].join('');
  const selectionTotal = Number(preview.total_changes || 0);
  const selectionBlock = selectionTotal > 0 ? `<section class="journal-import-selection"><div class="journal-import-selection-head"><div><span class="eyebrow">Выборочное восстановление</span><h4>Что применить</h4><p>${escapeHtml(preview.selection_note || 'Отметьте только нужные записи.')}</p></div><strong data-journal-import-selected-count>${selectionTotal} из ${selectionTotal}</strong></div><div class="journal-import-filter-bar"><label class="journal-import-search"><span>Поиск</span><input type="search" data-journal-import-search placeholder="Название, автор, список или статус" autocomplete="off"></label><label><span>Тип записи</span><select data-journal-import-kind-filter><option value="all">Все типы</option><option value="journal">Произведения</option><option value="cycle">Циклы</option><option value="year_list">Списки года</option></select></label><label><span>Действие</span><select data-journal-import-action-filter><option value="all">Все действия</option><option value="add">Только новые</option><option value="update_or_fill">Только обновления</option><option value="fill">Только заполнение пустого</option></select></label></div><div class="journal-import-filter-summary">Показано <strong data-journal-import-visible-count>${selectionTotal}</strong> · выбрано среди показанных <strong data-journal-import-visible-selected>${selectionTotal}</strong></div><div class="journal-import-selection-tools"><button type="button" class="secondary compact-button" data-journal-import-preset="all">Выбрать всё</button><button type="button" class="secondary compact-button" data-journal-import-preset="none">Снять всё</button><button type="button" class="secondary compact-button" data-journal-import-preset="new">Только новые</button><button type="button" class="secondary compact-button" data-journal-import-preset="updates">Только обновления</button><button type="button" class="secondary compact-button" data-journal-import-preset="cycles">Только циклы</button><button type="button" class="secondary compact-button" data-journal-import-preset="visible">Выбрать показанные</button><button type="button" class="secondary compact-button" data-journal-import-preset="visible-none">Снять показанные</button></div><p class="journal-import-filter-empty" data-journal-import-filter-empty hidden>По заданным фильтрам ничего не найдено. Уже выбранные записи при этом сохранены.</p>${selectionSections}<p class="journal-import-dependency-note">Поиск и фильтры не сбрасывают выбор. При выборе цикла базовая запись произведения включается автоматически, только если без неё цикл не сможет отображаться в дневнике.</p></section>` : '';
  return `<section class="journal-import-preview" id="readingJournalImportPreview">
    <div class="journal-import-preview-head"><div><span class="eyebrow">Предварительная проверка</span><h3>Восстановление дневника</h3><p>Экспорт ${escapeHtml(preview.source_version || '')} · записей ${Number(source.journal || 0)}, циклов ${Number(source.cycles || 0)}, отметок списков ${Number(source.year_lists || 0)}</p></div><span class="journal-import-safe">Без перезаписи новых данных</span></div>
    <div class="journal-import-stats">
      <article><strong>${added}</strong><small>будет добавлено</small></article>
      <article><strong>${updated}</strong><small>новее текущих</small></article>
      <article><strong>${filled}</strong><small>заполнит пустые поля</small></article>
      <article><strong>${protectedCount}</strong><small>защищено</small></article>
      <article><strong>${unchanged}</strong><small>без изменений</small></article>
      <article><strong>${Number(preview.invalid_records || 0)}</strong><small>ошибочных записей</small></article>
    </div>
    <p class="journal-import-safety">${escapeHtml(preview.safety_note || 'Более новые текущие данные не будут перезаписаны.')}</p>
    ${ignoredNote}${details.join('')}${selectionBlock}
    <div class="journal-import-actions"><button type="button" data-journal-import-apply="${escapeHtml(preview.preview_token || '')}" ${selectionTotal <= 0 ? 'disabled' : ''}>Применить выбранное: ${selectionTotal}</button><button type="button" class="secondary" data-journal-import-cancel>Отмена</button></div>
  </section>`;
}


function journalImportDateTime(value) {
  if (!value) return 'дата не указана';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function readingJournalImportHistoryMarkup(history) {
  const items = Array.isArray(history) ? history : [];
  if (!items.length) return '<section class="journal-import-history"><div class="section-title slim"><span class="eyebrow">Резервные точки</span><h3>История восстановлений</h3><p>После первого применённого импорта здесь появится автоматическая резервная копия.</p></div></section>';
  return `<section class="journal-import-history"><div class="section-title slim"><span class="eyebrow">Резервные точки</span><h3>История восстановлений</h3><p>Перед каждым импортом VoxLyra сохраняет приватную копию. Отмена не затрагивает записи, изменённые после восстановления.</p></div><div class="journal-import-history-list">${items.map((item) => {
    const counts = item.counts || {};
    const rollback = item.rollback_counts || {};
    const partial = item.status === 'rolled_back_partial';
    const rolledBack = item.status === 'rolled_back' || partial;
    const status = partial ? `Отменён частично · защищено ${Number(rollback.protected || 0)}` : rolledBack ? 'Импорт отменён' : 'Импорт применён';
    const actions = `<button type="button" class="secondary compact-button" data-journal-import-backup="${Number(item.id || 0)}">Скачать резерв</button>${item.can_rollback ? `<button type="button" class="danger-button compact-button" data-journal-import-rollback="${Number(item.id || 0)}">Отменить импорт</button>` : ''}`;
    return `<article class="journal-import-history-card ${rolledBack ? 'rolled-back' : ''}"><div><span>${escapeHtml(status)}</span><h4>Экспорт ${escapeHtml(item.source_version || '')}</h4><p>${journalImportDateTime(item.applied_at)} · применено ${Number(counts.total || 0)} изменений${rolledBack ? ` · восстановлено ${Number(rollback.restored || 0)}` : ''}</p></div><div class="journal-import-history-actions">${actions}</div></article>`;
  }).join('')}</div></section>`;
}

async function downloadReadingJournalBackup(runId) {
  const response = await fetch(`/api/library/journal/import/${Number(runId)}/backup`, { headers: { 'X-Telegram-Init-Data': tgInitData() } });
  if (!response.ok) {
    let message = 'Не удалось скачать резервную точку';
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = `voxlyra-before-import-${Number(runId)}.json`; link.rel = 'noopener';
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function rollbackReadingJournalImport(runId) {
  return apiFetch(`/api/library/journal/import/${Number(runId)}/rollback`, { method: 'POST', body: JSON.stringify({}) });
}

async function previewReadingJournalImport(file) {
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) throw new Error('JSON-файл больше допустимых 5 МБ');
  const form = new FormData();
  form.append('file', file, file.name || 'voxlyra-reading-history.json');
  return apiFetch('/api/library/journal/import/preview', { method: 'POST', body: form });
}

async function applyReadingJournalImport(previewToken, selectedItems) {
  return apiFetch('/api/library/journal/import/apply', {
    method: 'POST',
    body: JSON.stringify({
      preview_token: String(previewToken || ''),
      selected_items: Array.isArray(selectedItems) ? selectedItems : [],
    }),
  });
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

function subscriptionBookCard(item) {
  return `<article class="subscription-card" data-subscription-book-card="${Number(item.book_id)}">${dynamicCover(item)}<div><span>Книга</span><a href="/book/${Number(item.book_id)}"><h3>${escapeHtml(item.title || 'Книга')}</h3></a><p>${escapeHtml(item.pen_name || 'Автор не указан')} · ${Number(item.chapters_count || 0)} глав</p><small>${item.notify_chapters ? 'Новые главы' : ''}${item.notify_chapters && item.notify_audio ? ' · ' : ''}${item.notify_audio ? 'Аудио' : ''}</small></div><button type="button" class="secondary compact-button" data-unsubscribe-book="${Number(item.book_id)}">Отписаться</button></article>`;
}

function subscriptionAuthorCard(item) {
  return `<article class="subscription-card author-subscription-card" data-subscription-author-card="${Number(item.author_id)}"><div class="subscription-avatar">${escapeHtml((item.pen_name || 'А')[0])}</div><div><span>Автор</span><h3>${escapeHtml(item.pen_name || 'Автор')}</h3><p>${Number(item.books_count || 0)} опубликованных книг</p><small>Новые книги${item.notify_chapters ? ' · главы' : ''}${item.notify_audio ? ' · аудио' : ''}</small></div><button type="button" class="secondary compact-button" data-unsubscribe-author="${Number(item.author_id)}">Отписаться</button></article>`;
}

function activityNumber(value) {
  return Math.max(0, Number(value || 0));
}

function activityGoalCard(item) {
  const current = activityNumber(item.current);
  const target = activityNumber(item.target);
  const percent = Math.max(0, Math.min(100, activityNumber(item.percent)));
  const state = target <= 0 ? 'Цель выключена' : item.completed ? 'Выполнено' : `${current} из ${target} ${escapeHtml(item.unit || '')}`;
  return `<article class="reading-goal-card${item.completed ? ' completed' : ''}${target <= 0 ? ' disabled' : ''}"><div><strong>${escapeHtml(item.label || 'Цель')}</strong><span>${state}</span></div><div class="reading-goal-progress" aria-label="Выполнено ${percent}%"><i style="width:${percent}%"></i></div></article>`;
}

function activityCalendarCell(item) {
  const date = new Date(`${item.date}T12:00:00Z`);
  const day = Number.isNaN(date.getTime()) ? String(item.date || '').slice(-2) : date.toLocaleDateString('ru-RU', { day: '2-digit' });
  const label = `${item.date}: ${activityNumber(item.sessions)} открытых материалов, ${activityNumber(item.text_chapters)} текстовых глав, ${activityNumber(item.audio_minutes)} мин. аудио, ${activityNumber(item.graphic_pages)} страниц`;
  return `<span class="activity-calendar-day intensity-${Math.max(0, Math.min(4, activityNumber(item.intensity)))}" title="${escapeHtml(label)}"><b>${day}</b></span>`;
}

function readingReminderWeekdays(settings) {
  const selected = new Set((settings?.reminder_weekdays || []).map((value) => Number(value)));
  const labels = [['Пн', 1], ['Вт', 2], ['Ср', 3], ['Чт', 4], ['Пт', 5], ['Сб', 6], ['Вс', 7]];
  return labels.map(([label, value]) => `<label class="weekday-choice"><input type="checkbox" name="reminder_weekdays" value="${value}" ${selected.has(value) ? 'checked' : ''}><span>${label}</span></label>`).join('');
}

function activityMonthLabel(monthKey) {
  const raw = String(monthKey || '');
  const date = new Date(`${raw}-01T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return raw || 'месяц';
  return date.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric', timeZone: 'UTC' }).replace(' г.', '');
}

function activityComparisonCard(item) {
  const current = activityNumber(item.current);
  const previous = activityNumber(item.previous);
  const delta = Number(item.delta || 0);
  const trend = String(item.trend || 'same');
  const icon = trend === 'up' ? '↗' : trend === 'down' ? '↘' : trend === 'new' ? '✦' : '→';
  const change = trend === 'new'
    ? 'новая активность'
    : delta === 0
      ? 'без изменений'
      : `${delta > 0 ? '+' : ''}${delta} ${escapeHtml(item.unit || '')}`;
  return `<article class="monthly-comparison-card trend-${escapeHtml(trend)}"><div><span>${icon}</span><small>${escapeHtml(item.label || 'Показатель')}</small></div><strong>${current}</strong><p>${change} · было ${previous}</p></article>`;
}

function activityTrendMarkup(items) {
  if (!items.length) return '<p class="muted">Данных для динамики пока недостаточно.</p>';
  return `<div class="monthly-trend-list">${items.map((item) => {
    const totals = item.totals || {};
    const label = activityMonthLabel(item.month).split(' ')[0].slice(0, 3);
    const title = `${activityMonthLabel(item.month)}: ${activityNumber(totals.active_days)} активных дней, ${activityNumber(totals.text_chapters)} глав, ${activityNumber(totals.audio_minutes)} мин. аудио, ${activityNumber(totals.graphic_pages)} стр.`;
    return `<div class="monthly-trend-item" title="${escapeHtml(title)}"><div class="monthly-trend-bar"><i style="height:${Math.max(4, Math.min(100, activityNumber(item.intensity)))}%"></i></div><b>${escapeHtml(label)}</b><small>${activityNumber(totals.active_days)} дн.</small></div>`;
  }).join('')}</div>`;
}

function activityBestDayMarkup(bestDay) {
  if (!bestDay?.date) return '<p class="muted">Самый насыщенный день появится после первой активности в этом месяце.</p>';
  const date = new Date(`${bestDay.date}T12:00:00Z`);
  const label = Number.isNaN(date.getTime()) ? String(bestDay.date) : date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', timeZone: 'UTC' });
  return `<p><b>${escapeHtml(label)}</b> · ${activityNumber(bestDay.text_chapters)} глав, ${activityNumber(bestDay.audio_minutes)} мин. аудио, ${activityNumber(bestDay.graphic_pages)} стр. комиксов.</p>`;
}


function activityDateLabel(raw, options = { day: 'numeric', month: 'long', year: 'numeric' }) {
  const date = new Date(`${String(raw || '').slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? String(raw || '') : date.toLocaleDateString('ru-RU', { ...options, timeZone: 'UTC' });
}

function personalRecordCard(item) {
  const date = item.date ? `<small>${escapeHtml(activityDateLabel(item.date))}</small>` : '';
  const note = item.note ? `<p>${escapeHtml(item.note)}</p>` : '';
  return `<article class="personal-record-card"><span>${escapeHtml(item.icon || '✦')}</span><div><small>${escapeHtml(item.title || 'Личный рекорд')}</small><strong>${activityNumber(item.value)} ${escapeHtml(item.unit || '')}</strong>${date}${note}</div></article>`;
}

function milestoneCard(item, upcoming = false) {
  const achievedDate = item.achieved_at ? `Получено ${activityDateLabel(item.achieved_at)}` : `${activityNumber(item.current)} из ${activityNumber(item.target)} ${escapeHtml(item.unit || '')}`;
  return `<article class="reading-milestone-card${item.achieved ? ' achieved' : ''}${upcoming ? ' upcoming' : ''}"><span class="reading-milestone-icon">${escapeHtml(item.icon || '✦')}</span><div><strong>${escapeHtml(item.title || 'Веха')}</strong><p>${escapeHtml(item.description || '')}</p><small>${escapeHtml(achievedDate)}</small>${!item.achieved ? `<div class="reading-milestone-progress"><i style="width:${Math.max(0, Math.min(100, activityNumber(item.progress)))}%"></i></div>` : ''}</div></article>`;
}

function yearActivityDayCell(item) {
  const title = item.future
    ? `${activityDateLabel(item.date)}: день ещё впереди`
    : `${activityDateLabel(item.date)}: ${activityNumber(item.sessions)} сеансов, ${activityNumber(item.text_chapters)} глав, ${activityNumber(item.audio_minutes)} мин. аудио, ${activityNumber(item.graphic_pages)} стр.`;
  return `<span class="year-activity-cell intensity-${Math.max(0, Math.min(4, activityNumber(item.intensity)))}${item.future ? ' future' : ''}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}"></span>`;
}

function yearlyActivityMarkup(yearly) {
  const data = yearly || {};
  const days = data.days || [];
  const years = data.available_years || [new Date().getFullYear()];
  const selectedYear = Number(data.year || new Date().getFullYear());
  const totals = data.totals || {};
  const leading = days.length ? (new Date(`${days[0].date}T12:00:00Z`).getUTCDay() + 6) % 7 : 0;
  const placeholders = Array.from({ length: leading }, () => '<span class="year-activity-cell placeholder"></span>').join('');
  const monthItems = (data.months || []).filter((item) => !item.future).map((item) => {
    const monthTotals = item.totals || {};
    return `<article><span>${escapeHtml(activityMonthLabel(item.month).split(' ')[0])}</span><b>${activityNumber(monthTotals.active_days)} дн.</b><small>${activityNumber(monthTotals.text_chapters)} гл. · ${activityNumber(monthTotals.audio_minutes)} мин. · ${activityNumber(monthTotals.graphic_pages)} стр.</small></article>`;
  }).join('');
  const strongest = data.strongest_month || {};
  const strongestText = strongest.month ? `Самый насыщенный месяц: ${activityMonthLabel(strongest.month)} · ${activityNumber(strongest.totals?.active_days)} активных дней.` : 'Самый насыщенный месяц появится после активности.';
  return `<section class="year-activity-panel">
    <div class="section-title split-title slim"><div><span class="eyebrow">Личная карта</span><h2>Активность за ${selectedYear} год</h2><p>${escapeHtml(data.privacy_note || 'Карта видна только вам.')}</p></div><label class="year-activity-select">Год<select id="readingActivityYear">${years.map((year) => `<option value="${Number(year)}" ${Number(year) === selectedYear ? 'selected' : ''}>${Number(year)}</option>`).join('')}</select></label></div>
    <div class="year-activity-summary"><article><strong>${activityNumber(totals.active_days)}</strong><small>активных дней</small></article><article><strong>${activityNumber(totals.text_chapters)}</strong><small>текстовых глав</small></article><article><strong>${activityNumber(totals.audio_minutes)}</strong><small>минут аудио</small></article><article><strong>${activityNumber(totals.graphic_pages)}</strong><small>страниц комиксов</small></article></div>
    <div class="year-activity-scroll"><div class="year-activity-heatmap" role="img" aria-label="Годовая карта активности">${placeholders}${days.map(yearActivityDayCell).join('')}</div></div>
    <div class="year-activity-legend"><span>меньше</span>${[0,1,2,3,4].map((level) => `<i class="year-activity-cell intensity-${level}"></i>`).join('')}<span>больше</span></div>
    <p class="year-activity-strongest">${escapeHtml(strongestText)}</p>
    <div class="year-month-list">${monthItems}</div>
  </section>`;
}

function readingActivityMarkup(dashboard, reminderSettings) {
  const data = dashboard || {};
  const reminders = reminderSettings || {};
  const week = data.week_totals || {};
  const month = data.month_totals || {};
  const monthly = data.monthly_summary || {};
  const goals = data.goals || {};
  const goalItems = data.goal_items || [];
  const calendar = data.calendar || [];
  const comparisons = monthly.comparisons || [];
  const trend = monthly.six_month_trend || [];
  const recommendation = monthly.recommendation || {};
  const averages = monthly.averages_per_active_day || {};
  const records = data.personal_records || [];
  const milestones = data.milestones || {};
  const achievedMilestones = milestones.latest || [];
  const upcomingMilestones = milestones.upcoming || [];
  const yearly = data.yearly_activity || {};
  return `<div class="section-title slim"><span class="eyebrow">Личный ритм</span><h2>Статистика чтения</h2><p>Текст, аудио и комиксы учитываются вместе. Серия сохраняется, когда есть хотя бы один сеанс за день.</p></div>
    <div class="reading-stat-grid">
      <article><span>🔥</span><strong>${activityNumber(data.current_streak)}</strong><small>текущая серия, дней</small></article>
      <article><span>🏆</span><strong>${activityNumber(data.best_streak)}</strong><small>лучшая серия, дней</small></article>
      <article><span>📖</span><strong>${activityNumber(week.text_chapters)}</strong><small>текстовых глав за неделю</small></article>
      <article><span>🎧</span><strong>${activityNumber(week.audio_minutes)}</strong><small>минут аудио за неделю</small></article>
      <article><span>🖼</span><strong>${activityNumber(week.graphic_pages)}</strong><small>страниц за неделю</small></article>
      <article><span>📅</span><strong>${activityNumber(month.active_days)}</strong><small>активных дней за месяц</small></article>
    </div>
    <section class="monthly-summary-panel">
      <div class="section-title split-title slim"><div><span class="eyebrow">Итоги месяца</span><h2>${escapeHtml(activityMonthLabel(monthly.current_month))}</h2><p>Сравнение с таким же количеством дней ${escapeHtml(activityMonthLabel(monthly.previous_month))}, чтобы неполный месяц не выглядел слабее полного.</p></div></div>
      <div class="monthly-comparison-grid">${comparisons.map(activityComparisonCard).join('')}</div>
      <div class="monthly-summary-details">
        <article><span>Самый насыщенный день</span>${activityBestDayMarkup(monthly.best_day)}</article>
        <article><span>В среднем за активный день</span><p><b>${Number(averages.text_chapters || 0).toLocaleString('ru-RU')}</b> глав · <b>${Number(averages.audio_minutes || 0).toLocaleString('ru-RU')}</b> мин. аудио · <b>${Number(averages.graphic_pages || 0).toLocaleString('ru-RU')}</b> стр.</p></article>
      </div>
      <article class="reading-rhythm-card"><span>Личный ориентир</span><h3>${escapeHtml(recommendation.title || 'Гибкий ритм')}</h3><p>${escapeHtml(recommendation.text || 'Выбирайте удобный темп без давления.')}</p></article>
      <div class="section-title slim monthly-trend-title"><h3>Динамика за полгода</h3><p>Высота показывает общую активность, а подпись — число активных дней.</p></div>
      ${activityTrendMarkup(trend)}
    </section>
    <section class="personal-records-panel">
      <div class="section-title slim"><span class="eyebrow">Только ваши данные</span><h2>Личные рекорды</h2><p>Рекорды не участвуют в публичных рейтингах и сравнениях с другими читателями.</p></div>
      ${records.length ? `<div class="personal-record-grid">${records.map(personalRecordCard).join('')}</div>` : `<p class="muted">Первый личный рекорд появится после сохранённой активности.</p>`}
    </section>
    <section class="reading-milestones-panel">
      <div class="section-title split-title slim"><div><span class="eyebrow">Памятные вехи</span><h2>${activityNumber(milestones.achieved_count)} из ${activityNumber(milestones.total_count)}</h2><p>Это личная хронология, а не обязательный список задач.</p></div></div>
      ${achievedMilestones.length ? `<div class="reading-milestone-grid">${achievedMilestones.map((item) => milestoneCard(item)).join('')}</div>` : `<p class="muted">Первая веха сохранится после первого активного дня.</p>`}
      ${upcomingMilestones.length ? `<div class="section-title slim milestone-upcoming-title"><h3>Ближайшие вехи</h3><p>Показываются для ориентира без напоминаний и давления.</p></div><div class="reading-milestone-grid upcoming-grid">${upcomingMilestones.map((item) => milestoneCard(item, true)).join('')}</div>` : ""}
    </section>
    ${yearlyActivityMarkup(yearly)}
    <section class="reading-goals-panel">
      <div class="section-title split-title slim"><div><h2>Цели на неделю</h2><p>${activityNumber(data.completed_goals)} из ${activityNumber(data.enabled_goals)} выполнено.</p></div></div>
      <div class="reading-goal-list">${goalItems.map(activityGoalCard).join('')}</div>
      <form id="readingGoalsForm" class="reading-goals-form">
        <label>Активных дней<input name="active_days_week" type="number" min="0" max="7" value="${activityNumber(goals.active_days_week)}"></label>
        <label>Текстовых глав<input name="text_chapters_week" type="number" min="0" max="200" value="${activityNumber(goals.text_chapters_week)}"></label>
        <label>Минут аудио<input name="audio_minutes_week" type="number" min="0" max="10080" value="${activityNumber(goals.audio_minutes_week)}"></label>
        <label>Страниц комиксов<input name="graphic_pages_week" type="number" min="0" max="5000" value="${activityNumber(goals.graphic_pages_week)}"></label>
        <button type="submit">Сохранить цели</button>
      </form>
      <small class="muted">Ноль отключает отдельную цель.</small>
    </section>
    <section class="reading-reminder-panel">
      <div class="section-title slim"><span class="eyebrow">Без навязчивости</span><h2>Напоминания и отчёты</h2><p>Выберите удобное расписание. VoxLyra не повторяет один и тот же отчёт и уважает общее отключение уведомлений.</p></div>
      <form id="readingReminderForm" class="reading-reminder-form">
        <label class="setting-inline-check"><input type="checkbox" name="reminder_enabled" ${reminders.reminder_enabled !== false ? 'checked' : ''}><span><b>Напоминать продолжить чтение</b><small>Только после выбранного количества дней без активности.</small></span></label>
        <div class="reminder-fields">
          <label>Время<input name="reminder_time" type="time" value="${escapeHtml(reminders.reminder_time || '19:00')}"></label>
          <label>После паузы<input name="inactive_days" type="number" min="1" max="30" value="${Math.max(1, Number(reminders.inactive_days || 3))}"><small>дней</small></label>
        </div>
        <fieldset class="weekday-fieldset"><legend>Дни напоминаний</legend><div class="weekday-choices">${readingReminderWeekdays(reminders)}</div></fieldset>
        <label class="setting-inline-check"><input type="checkbox" name="weekly_report_enabled" ${reminders.weekly_report_enabled !== false ? 'checked' : ''}><span><b>Личный отчёт за неделю</b><small>Активные дни, главы, аудио, комиксы, серия и выполненные цели.</small></span></label>
        <div class="reminder-fields">
          <label>День отчёта<select name="weekly_report_weekday">${[['Понедельник',1],['Вторник',2],['Среда',3],['Четверг',4],['Пятница',5],['Суббота',6],['Воскресенье',7]].map(([label,value]) => `<option value="${value}" ${Number(reminders.weekly_report_weekday || 7) === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
          <label>Время отчёта<input name="weekly_report_time" type="time" value="${escapeHtml(reminders.weekly_report_time || '20:00')}"></label>
        </div>
        <label class="setting-inline-check"><input type="checkbox" name="monthly_report_enabled" ${reminders.monthly_report_enabled !== false ? 'checked' : ''}><span><b>Личный итог за месяц</b><small>Приходит в начале нового месяца и сравнивает завершённый месяц с предыдущим.</small></span></label>
        <div class="reminder-fields">
          <label>Число месяца<select name="monthly_report_day">${Array.from({length:7}, (_,index) => index + 1).map((value) => `<option value="${value}" ${Number(reminders.monthly_report_day || 1) === value ? 'selected' : ''}>${value}-е число</option>`).join('')}</select></label>
          <label>Время итога<input name="monthly_report_time" type="time" value="${escapeHtml(reminders.monthly_report_time || '20:00')}"></label>
        </div>
        <button type="submit">Сохранить расписание</button>
      </form>
    </section>
    <section class="activity-calendar-panel"><div class="section-title slim"><h2>Последние 35 дней</h2><p>Чем ярче ячейка, тем больше активности в этот день.</p></div><div class="activity-calendar">${calendar.map(activityCalendarCell).join('')}</div></section>`;
}

function renderLibraryTab(tab, data) {
  const content = document.getElementById('libraryContent');
  if (!content) return;
  if (tab === 'continue') {
    const items = data.continue_items || [];
    if (!items.length) {
      content.innerHTML = emptyStateMarkup('history-empty', 'Продолжать пока нечего', 'Откройте текстовую, аудио- или графическую главу — место появится здесь.', '/catalog', 'Выбрать произведение');
      return;
    }
    content.innerHTML = `<div class="section-title slim"><h2>С последнего места</h2><p>Книги, аудио и комиксы собраны по времени последнего открытия.</p></div><div class="library-continue-grid">${items.map((item) => continueCard(item, item.continue_kind)).join('')}</div>`;
    return;
  }
  if (tab === 'activity') {
    content.innerHTML = readingActivityMarkup(data.reading_dashboard || {}, data.reading_notification_settings || {});
    return;
  }
  if (tab === 'journal') {
    const items = data.reading_journal || [];
    const summary = data.reading_journal_summary || {};
    const context = { summary, yearLists: data.year_lists || {} };
    const exportButtons = `<div class="reading-journal-export"><button type="button" class="secondary compact-button" data-journal-import>Восстановить из JSON</button><input type="file" id="readingJournalImportFile" accept="application/json,.json" hidden><button type="button" class="secondary compact-button" data-journal-export="json">Экспорт всей истории JSON</button><button type="button" class="secondary compact-button" data-journal-export="csv">Экспорт дневника CSV</button></div>`;
    content.innerHTML = `<div class="section-title split-title slim"><div><span class="eyebrow">Только для вас</span><h2>Читательский дневник</h2><p>${escapeHtml(summary.privacy_note || 'Даты, оценки, циклы и списки не публикуются как отзывы.')}</p></div>${exportButtons}</div>${readingJournalImportPreviewMarkup(data.journal_import_preview)}${readingJournalImportHistoryMarkup(data.journal_import_history || [])}${readingJournalSummaryMarkup(summary)}${completionCalendarMarkup(data.completion_calendar || {}, data.year_lists || {})}${items.length ? `<div class="reading-journal-list">${items.map((item) => readingJournalCard(item, context)).join('')}</div>` : emptyStateMarkup('history-empty', 'Дневник пока пуст', 'Откройте произведение или добавьте его в библиотеку — первая запись появится автоматически.', '/catalog', 'Выбрать произведение')}`;
    updateReadingJournalImportSelection();
    return;
  }
  if (tab === 'saved') {
    const items = data.bookmarks || [];
    const shelves = data.shelves || [];
    content.innerHTML = items.length ? `<div class="section-title slim"><h2>Сохранённые произведения</h2><p>Книгу можно перенести на любую созданную полку.</p></div><div class="book-list">${items.map((item) => bookmarkCard(item, shelves)).join('')}</div>` : emptyStateMarkup('no-bookmarks', 'Полка пока пустая', 'Добавляйте книги в библиотеку или любимое.', '/catalog', 'Открыть каталог');
    return;
  }
  if (tab === 'shelves') {
    const shelves = data.shelves || [];
    const form = `<form class="custom-shelf-form" id="customShelfForm"><input id="customShelfIcon" maxlength="8" value="📚" aria-label="Значок полки"><input id="customShelfName" maxlength="60" placeholder="Название новой полки" required><button type="submit">Создать</button></form>`;
    const sections = shelves.map((shelf) => `<section class="custom-shelf" data-custom-shelf="${Number(shelf.id)}"><div class="section-title split-title slim"><div><span class="eyebrow">${escapeHtml(shelf.icon || '📚')} Личная полка</span><h2>${escapeHtml(shelf.name || 'Полка')}</h2><p>${Number(shelf.books_count || 0)} произведений</p></div><button type="button" class="secondary compact-button" data-shelf-delete="${Number(shelf.id)}">Удалить полку</button></div>${(shelf.books || []).length ? `<div class="custom-shelf-list">${shelf.books.map((item) => customShelfBookCard(item, shelf.id)).join('')}</div>` : '<p class="muted">Добавьте сюда книгу из вкладки «Сохранённое».</p>'}</section>`).join('');
    content.innerHTML = `<div class="section-title slim"><h2>Мои полки</h2><p>Создавайте собственные подборки без ограничения стандартными статусами.</p></div>${form}${sections || emptyStateMarkup('no-bookmarks', 'Пользовательских полок пока нет', 'Создайте первую полку выше.')}`;
    return;
  }
  if (tab === 'history') {
    const items = data.reading_history || [];
    content.innerHTML = `<div class="section-title split-title slim"><div><h2>История чтения</h2><p>Последние открытые главы, аудио и комиксы.</p></div>${items.length ? '<button type="button" class="secondary compact-button" id="clearReadingHistory">Очистить</button>' : ''}</div>${items.length ? `<div class="history-list">${items.map(historyCard).join('')}</div>` : emptyStateMarkup('history-empty', 'История пока пустая', 'Открытые произведения появятся здесь автоматически.')}`;
    return;
  }
  if (tab === 'notes') {
    const items = data.annotations || [];
    content.innerHTML = `<div class="section-title slim"><h2>Заметки и цитаты</h2><p>Личные записи синхронизируются между устройствами и не видны другим читателям.</p></div>${items.length ? `<div class="annotation-list">${items.map(annotationCard).join('')}</div>` : emptyStateMarkup('no-bookmarks', 'Записей пока нет', 'Добавляйте заметки и сохраняйте цитаты прямо во время чтения.')}`;
    return;
  }
  if (tab === 'subscriptions') {
    const books = data.subscriptions?.books || [];
    const authors = data.subscriptions?.authors || [];
    if (!books.length && !authors.length) {
      content.innerHTML = emptyStateMarkup('no-bookmarks', 'Подписок пока нет', 'Подпишитесь на книгу или автора, чтобы получать только нужные обновления.', '/catalog', 'Найти книгу');
      return;
    }
    content.innerHTML = `${authors.length ? `<div class="section-title slim"><h2>Авторы</h2></div><div class="subscription-list">${authors.map(subscriptionAuthorCard).join('')}</div>` : ''}${books.length ? `<div class="section-title slim"><h2>Книги</h2></div><div class="subscription-list">${books.map(subscriptionBookCard).join('')}</div>` : ''}`;
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

async function openWalletTopup(amount) {
  const buttons = document.querySelectorAll('[data-wallet-topup]');
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const data = await apiFetch('/api/wallet/checkout', {
      method: 'POST',
      body: JSON.stringify({ amount_stars: Number(amount) }),
    });
    const telegram = window.Telegram?.WebApp;
    if (telegram?.openInvoice) {
      telegram.openInvoice(data.invoice_link, (status) => {
        if (status === 'paid') {
          notify('Баланс пополнен');
          setTimeout(() => { meDataPromise = null; loadMeData().then(renderWalletSummary).catch(() => {}); }, 900);
        } else if (status === 'cancelled') notify('Пополнение отменено');
        else if (status === 'failed') notify('Telegram не завершил оплату');
      });
    } else {
      window.location.assign(data.invoice_link);
    }
  } catch (error) {
    notify(error.message || 'Не удалось открыть пополнение');
  } finally {
    buttons.forEach((button) => { button.disabled = false; });
  }
}

function renderWalletSummary(data) {
  const wallet = data?.wallet || {};
  const economy = data?.bonus_economy || {};
  const stars = document.getElementById('walletStars');
  const points = document.getElementById('walletBonusPoints');
  const hint = document.getElementById('walletCashbackHint');
  const buttons = document.getElementById('walletTopupButtons');
  if (stars) stars.textContent = Number(wallet.wallet_stars || 0);
  if (points) points.textContent = Number(wallet.bonus_points || 0);
  if (hint) {
    const rate = Math.max(1, Number(economy.points_per_star || 100));
    const usable = Math.floor(Number(wallet.bonus_points || 0) / rate);
    const remainder = Number(wallet.bonus_points || 0) % rate;
    hint.textContent = `${rate} бонусов = 1 целая Star · доступно ${usable} Stars${remainder ? ` · ещё ${rate - remainder} бонусов до следующей` : ''}`;
  }
  if (buttons) {
    const packages = economy.topup_packages || [];
    buttons.innerHTML = packages.map((amount) => {
      const totalPoints = Math.floor(Number(amount) * Number(economy.bonus_percent || 0) * Number(economy.points_per_star || 100) / 100);
      return `<button type="button" class="secondary" data-wallet-topup="${Number(amount)}"><strong>${Number(amount)} Stars</strong><small>до +${totalPoints} бонусов</small></button>`;
    }).join('');
    buttons.querySelectorAll('[data-wallet-topup]').forEach((button) => button.addEventListener('click', () => openWalletTopup(button.dataset.walletTopup)));
  }
}

async function syncReadingTimezone(data) {
  const current = data?.reading_notification_settings || {};
  const detectedOffset = -new Date().getTimezoneOffset();
  if (!Number.isFinite(detectedOffset) || Number(current.timezone_offset_minutes || 0) === detectedOffset) return;
  try {
    const result = await apiFetch('/api/library/reminders', {
      method: 'PATCH',
      body: JSON.stringify({
        reminder_enabled: current.reminder_enabled !== false,
        reminder_time: current.reminder_time || '19:00',
        reminder_weekdays: current.reminder_weekdays || [1,2,3,4,5,6,7],
        inactive_days: Number(current.inactive_days || 3),
        weekly_report_enabled: current.weekly_report_enabled !== false,
        weekly_report_weekday: Number(current.weekly_report_weekday || 7),
        weekly_report_time: current.weekly_report_time || '20:00',
        monthly_report_enabled: current.monthly_report_enabled !== false,
        monthly_report_day: Number(current.monthly_report_day || 1),
        monthly_report_time: current.monthly_report_time || '20:00',
        timezone_offset_minutes: detectedOffset,
      }),
    });
    data.reading_notification_settings = result.reading_notification_settings || current;
  } catch (_) {}
}

async function initLibrary() {
  const page = document.getElementById('libraryPage');
  if (!page) return;
  const content = document.getElementById('libraryContent');
  const walletTopupButton = document.getElementById('walletTopupButton');
  const walletTopupPanel = document.getElementById('walletTopupPanel');
  walletTopupButton?.addEventListener('click', () => {
    if (!walletTopupPanel) return;
    walletTopupPanel.hidden = !walletTopupPanel.hidden;
    walletTopupButton.textContent = walletTopupPanel.hidden ? 'Пополнить' : 'Скрыть';
  });
  if (!tgInitData()) {
    if (content) content.innerHTML = emptyStateMarkup('no-books', 'Откройте внутри Telegram', 'Личная библиотека привязана к вашему Telegram-профилю.', '/catalog', 'Смотреть каталог');
    return;
  }
  try {
    const data = await loadMeData();
    await syncReadingTimezone(data);
    page._libraryData = data;
    renderWalletSummary(data);
    const profileName = String(data.user?.full_name || data.user?.username || '').trim();
    const username = String(data.user?.username || '').replace(/^@+/, '').trim();
    const telegramPhotoUrl = String(
      data.user?.photo_url || window.Telegram?.WebApp?.initDataUnsafe?.user?.photo_url || ''
    ).trim();
    const initial = (profileName || username || 'В').slice(0, 1).toUpperCase();
    const profileInitial = document.getElementById('libraryProfileInitial');
    const profileIcon = document.getElementById('libraryProfileIcon');
    const profileNameLabel = document.getElementById('libraryProfileName');
    const showInitialFallback = () => {
      if (profileIcon) profileIcon.hidden = true;
      if (profileInitial) {
        profileInitial.textContent = initial;
        profileInitial.hidden = false;
      }
    };
    if (profileIcon) {
      profileIcon.hidden = false;
      if (telegramPhotoUrl) {
        profileIcon.src = telegramPhotoUrl;
        profileIcon.classList.add('telegram-avatar');
      } else {
        profileIcon.classList.remove('telegram-avatar');
      }
      profileIcon.addEventListener('error', showInitialFallback, { once: true });
      if (profileIcon.complete && profileIcon.naturalWidth === 0) showInitialFallback();
    } else {
      showInitialFallback();
    }
    if (profileInitial && profileIcon && !profileIcon.hidden) profileInitial.hidden = true;
    if (profileNameLabel) {
      const label = username ? `@${username}` : profileName;
      profileNameLabel.textContent = label;
      profileNameLabel.hidden = !label;
    }
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
    const ownerBooksEntry = document.getElementById('ownerBooksEntry');
    if (ownerBooksEntry && data.control?.owner) ownerBooksEntry.hidden = false;
    const requestedTab = new URLSearchParams(window.location.search).get('tab');
    const initialTab = ['continue','activity','journal','saved','shelves','history','notes','subscriptions','purchases'].includes(requestedTab) ? requestedTab : 'continue';
    document.querySelectorAll('[data-library-tab]').forEach((button) => button.classList.toggle('active', button.dataset.libraryTab === initialTab));
    renderLibraryTab(initialTab, data);
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

async function saveReaderAnnotation(annotationType) {
  const chapterId = Number(document.getElementById('readerText')?.dataset.chapterId || 0);
  if (!chapterId || !tgInitData()) { notify('Откройте главу внутри Telegram'); return; }
  const isQuote = annotationType === 'quote';
  const selectedText = String(document.getElementById('readerQuoteText')?.value || '').trim();
  const noteText = isQuote ? '' : String(document.getElementById('readerNoteText')?.value || '').trim();
  if (isQuote && selectedText.length < 3) { notify('Выберите текст цитаты'); return; }
  if (!isQuote && !noteText) { notify('Введите текст заметки'); return; }
  const button = document.getElementById(isQuote ? 'readerQuoteSave' : 'readerNoteSave');
  if (button) button.disabled = true;
  try {
    await apiFetch(`/api/reader/${chapterId}/annotations`, {
      method: 'POST',
      body: JSON.stringify({
        annotation_type: annotationType,
        selected_text: selectedText,
        note_text: noteText,
        color: isQuote ? 'violet' : (document.getElementById('readerNoteColor')?.value || 'violet'),
        position_percent: calcReadingPercent(),
      }),
    });
    meDataPromise = null;
    if (isQuote) notify('Цитата сохранена');
    else {
      notify('Заметка сохранена');
      document.getElementById('readerNoteText').value = '';
      document.getElementById('readerNotePanel').hidden = true;
    }
    await loadReaderAnnotations();
  } catch (error) { notify(error.message || 'Не удалось сохранить запись'); }
  finally { if (button) button.disabled = false; }
}

async function loadReaderAnnotations() {
  const chapterId = Number(document.getElementById('readerText')?.dataset.chapterId || 0);
  const box = document.getElementById('readerAnnotationList');
  if (!chapterId || !box || !tgInitData()) return;
  try {
    const data = await apiFetch(`/api/reader/${chapterId}/annotations`);
    const items = data.annotations || [];
    box.hidden = !items.length;
    box.innerHTML = items.length ? `<div class="section-title slim"><span class="eyebrow">Личные записи</span><h2>В этой главе</h2></div><div class="annotation-list">${items.map(annotationCard).join('')}</div>` : '';
  } catch (_) { box.hidden = true; }
}

function bindReaderQuoteCards() {
  const start = document.getElementById('readerQuoteStart');
  const noteStart = document.getElementById('readerNoteStart');
  if (!start && !noteStart) return;
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
  start?.addEventListener('click', () => setReaderQuoteMode(true));
  noteStart?.addEventListener('click', () => {
    const panel = document.getElementById('readerNotePanel');
    if (panel) { panel.hidden = false; panel.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  });
  document.getElementById('readerQuoteClose')?.addEventListener('click', () => { setReaderQuoteMode(false); clearReaderQuotePreview(); });
  document.getElementById('readerQuoteCancelMode')?.addEventListener('click', () => {
    document.getElementById('readerQuoteText').value = '';
    clearReaderQuotePreview();
    setReaderQuoteMode(false);
  });
  document.getElementById('readerQuoteCreate')?.addEventListener('click', createReaderQuoteCard);
  document.getElementById('readerQuoteSave')?.addEventListener('click', () => saveReaderAnnotation('quote'));
  document.getElementById('readerQuoteShare')?.addEventListener('click', shareReaderQuoteCard);
  document.getElementById('readerNoteSave')?.addEventListener('click', () => saveReaderAnnotation('note'));
  const closeNote = () => { const panel = document.getElementById('readerNotePanel'); if (panel) panel.hidden = true; };
  document.getElementById('readerNoteClose')?.addEventListener('click', closeNote);
  document.getElementById('readerNoteCancel')?.addEventListener('click', closeNote);
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
  loadReaderAnnotations();
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
  document.addEventListener('input', (event) => {
    if (event.target.id === 'catalogSearch') applyCatalogFilter();
    if (event.target.matches('[data-journal-import-search]')) updateReadingJournalImportSelection();
  });
  document.addEventListener('change', async (event) => {
    if (event.target.matches('[data-journal-import-kind-filter], [data-journal-import-action-filter]')) {
      updateReadingJournalImportSelection();
      return;
    }
    if (event.target.matches('[data-journal-import-select]')) {
      const input = event.target;
      const dependency = String(input.dataset.journalImportDepends || '');
      if (input.checked && dependency) {
        const dependencyInput = [...document.querySelectorAll('#readingJournalImportPreview [data-journal-import-select]')]
          .find((item) => String(item.value || '') === dependency);
        if (dependencyInput) dependencyInput.checked = true;
      }
      if (!input.checked && String(input.value || '').startsWith('journal:')) {
        document.querySelectorAll('#readingJournalImportPreview [data-journal-import-depends]').forEach((cycleInput) => {
          if (String(cycleInput.dataset.journalImportDepends || '') === String(input.value || '')) cycleInput.checked = false;
        });
      }
      updateReadingJournalImportSelection();
      return;
    }
    if (event.target.id === 'readingJournalImportFile') {
      const input = event.target;
      const file = input.files?.[0];
      if (!file) return;
      input.disabled = true;
      try {
        const result = await previewReadingJournalImport(file);
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          page._libraryData.journal_import_preview = result.preview || null;
          renderLibraryTab('journal', page._libraryData);
        }
        notify(Number(result.preview?.total_changes || 0) ? 'Файл проверен — посмотрите изменения' : 'Файл проверен, новых данных нет');
      } catch (error) {
        notify(error.message || 'Не удалось проверить файл');
        input.disabled = false;
        input.value = '';
      }
      return;
    }
    if (event.target.id === 'readingActivityYear') {
      const year = Number(event.target.value || new Date().getFullYear());
      event.target.disabled = true;
      try {
        const result = await apiFetch(`/api/library/activity?year=${encodeURIComponent(year)}`);
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          page._libraryData.reading_dashboard = result.reading_dashboard || {};
          page._libraryData.reading_notification_settings = result.reading_notification_settings || page._libraryData.reading_notification_settings || {};
          renderLibraryTab('activity', page._libraryData);
        }
      } catch (error) {
        notify(error.message || 'Не удалось открыть выбранный год');
        event.target.disabled = false;
      }
      return;
    }
    if (event.target.id === 'completionCalendarYear') {
      const year = Number(event.target.value || new Date().getFullYear());
      event.target.disabled = true;
      try {
        const result = await apiFetch(`/api/library/completions?year=${encodeURIComponent(year)}`);
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          page._libraryData.completion_calendar = result.completion_calendar || {};
          page._libraryData.year_lists = result.year_lists || {};
          renderLibraryTab('journal', page._libraryData);
        }
      } catch (error) {
        notify(error.message || 'Не удалось открыть календарь выбранного года');
        event.target.disabled = false;
      }
      return;
    }
    if (event.target.matches('[data-shelf-book-picker]')) {
      const shelfId = Number(event.target.value || 0);
      const bookId = Number(event.target.dataset.shelfBookPicker || 0);
      if (!shelfId || !bookId) return;
      event.target.disabled = true;
      try {
        const result = await apiFetch(`/api/library/shelves/${shelfId}/books`, { method: 'POST', body: JSON.stringify({ book_id: bookId, enabled: true }) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) page._libraryData.shelves = result.shelves || [];
        meDataPromise = null;
        notify('Книга добавлена на полку');
        event.target.value = '';
      } catch (error) { notify(error.message || 'Не удалось добавить книгу'); }
      finally { event.target.disabled = false; }
      return;
    }
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
    if (event.target.id === 'readingReminderForm') {
      event.preventDefault();
      const form = new FormData(event.target);
      const payload = {
        reminder_enabled: Boolean(form.get('reminder_enabled')),
        reminder_time: String(form.get('reminder_time') || '19:00'),
        reminder_weekdays: form.getAll('reminder_weekdays').map((value) => Number(value)),
        inactive_days: Number(form.get('inactive_days') || 3),
        weekly_report_enabled: Boolean(form.get('weekly_report_enabled')),
        weekly_report_weekday: Number(form.get('weekly_report_weekday') || 7),
        weekly_report_time: String(form.get('weekly_report_time') || '20:00'),
        monthly_report_enabled: Boolean(form.get('monthly_report_enabled')),
        monthly_report_day: Number(form.get('monthly_report_day') || 1),
        monthly_report_time: String(form.get('monthly_report_time') || '20:00'),
        timezone_offset_minutes: -new Date().getTimezoneOffset(),
      };
      try {
        const result = await apiFetch('/api/library/reminders', { method: 'PATCH', body: JSON.stringify(payload) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          page._libraryData.reading_notification_settings = result.reading_notification_settings || {};
          renderLibraryTab('activity', page._libraryData);
        }
        meDataPromise = null;
        notify('Расписание сохранено');
      } catch (error) { notify(error.message || 'Не удалось сохранить расписание'); }
      return;
    }
    if (event.target.id === 'readingGoalsForm') {
      event.preventDefault();
      const form = new FormData(event.target);
      const payload = {
        active_days_week: Number(form.get('active_days_week') || 0),
        text_chapters_week: Number(form.get('text_chapters_week') || 0),
        audio_minutes_week: Number(form.get('audio_minutes_week') || 0),
        graphic_pages_week: Number(form.get('graphic_pages_week') || 0),
      };
      try {
        const result = await apiFetch('/api/library/goals', { method: 'PATCH', body: JSON.stringify(payload) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          page._libraryData.reading_dashboard = result.reading_dashboard || {};
          renderLibraryTab('activity', page._libraryData);
        }
        meDataPromise = null;
        notify('Цели сохранены');
      } catch (error) { notify(error.message || 'Не удалось сохранить цели'); }
      return;
    }
    if (event.target.matches('[data-reading-journal-form]')) {
      event.preventDefault();
      const bookId = Number(event.target.dataset.readingJournalForm || 0);
      const form = new FormData(event.target);
      const payload = {
        status: String(form.get('status') || 'reading'),
        started_on: String(form.get('started_on') || ''),
        finished_on: String(form.get('finished_on') || ''),
        private_rating: Number(form.get('private_rating') || 0),
        impression: String(form.get('impression') || ''),
      };
      const submit = event.target.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        const result = await apiFetch(`/api/library/journal/${bookId}`, { method: 'PATCH', body: JSON.stringify(payload) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          applyReadingJournalPayload(page, result);
          renderLibraryTab('journal', page._libraryData);
        }
        meDataPromise = null;
        notify('Запись дневника сохранена');
      } catch (error) { notify(error.message || 'Не удалось сохранить запись'); }
      finally { if (submit) submit.disabled = false; }
      return;
    }
    if (event.target.matches('[data-reread-start-form]')) {
      event.preventDefault();
      const bookId = Number(event.target.dataset.rereadStartForm || 0);
      const form = new FormData(event.target);
      const submit = event.target.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        const result = await apiFetch(`/api/library/journal/${bookId}/cycles`, { method: 'POST', body: JSON.stringify({ started_on: String(form.get('started_on') || ''), note: String(form.get('note') || '') }) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { applyReadingJournalPayload(page, result); renderLibraryTab('journal', page._libraryData); }
        meDataPromise = null;
        notify('Новый цикл чтения начат');
      } catch (error) { notify(error.message || 'Не удалось начать перечитывание'); }
      finally { if (submit) submit.disabled = false; }
      return;
    }
    if (event.target.matches('[data-reading-cycle-form]')) {
      event.preventDefault();
      const cycleId = Number(event.target.dataset.readingCycleForm || 0);
      const form = new FormData(event.target);
      const submit = event.target.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        const result = await apiFetch(`/api/library/journal/cycles/${cycleId}`, { method: 'PATCH', body: JSON.stringify({ status: String(form.get('status') || 'reading'), started_on: String(form.get('started_on') || ''), finished_on: String(form.get('finished_on') || ''), note: String(form.get('note') || '') }) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { applyReadingJournalPayload(page, result); renderLibraryTab('journal', page._libraryData); }
        meDataPromise = null;
        notify('Цикл сохранён');
      } catch (error) { notify(error.message || 'Не удалось сохранить цикл'); }
      finally { if (submit) submit.disabled = false; }
      return;
    }
    if (event.target.matches('[data-year-list-form]')) {
      event.preventDefault();
      const bookId = Number(event.target.dataset.yearListForm || 0);
      const form = new FormData(event.target);
      const payload = { year: Number(form.get('year') || new Date().getFullYear()), list_codes: form.getAll('list_codes').map((value) => String(value)) };
      const submit = event.target.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        const result = await apiFetch(`/api/library/journal/${bookId}/year-lists`, { method: 'PUT', body: JSON.stringify(payload) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { applyReadingJournalPayload(page, result); renderLibraryTab('journal', page._libraryData); }
        meDataPromise = null;
        notify('Личные списки сохранены');
      } catch (error) { notify(error.message || 'Не удалось сохранить списки года'); }
      finally { if (submit) submit.disabled = false; }
      return;
    }
    if (event.target.id === 'customShelfForm') {
      event.preventDefault();
      const name = String(document.getElementById('customShelfName')?.value || '').trim();
      const icon = String(document.getElementById('customShelfIcon')?.value || '📚').trim();
      try {
        const result = await apiFetch('/api/library/shelves', { method: 'POST', body: JSON.stringify({ name, icon }) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { page._libraryData.shelves = result.shelves || []; renderLibraryTab('shelves', page._libraryData); }
        meDataPromise = null;
        notify('Полка создана');
      } catch (error) { notify(error.message || 'Не удалось создать полку'); }
      return;
    }
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
    if (target.matches('[data-shelf-delete]')) {
      event.preventDefault();
      const shelfId = Number(target.dataset.shelfDelete || 0);
      if (!shelfId || !confirm('Удалить эту полку? Книги останутся в библиотеке.')) return;
      try {
        const result = await apiFetch(`/api/library/shelves/${shelfId}`, { method: 'DELETE' });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { page._libraryData.shelves = result.shelves || []; renderLibraryTab('shelves', page._libraryData); }
        meDataPromise = null;
        notify('Полка удалена');
      } catch (error) { notify(error.message || 'Не удалось удалить полку'); }
      return;
    }
    if (target.matches('[data-shelf-remove-book]')) {
      event.preventDefault();
      const shelfId = Number(target.dataset.shelfId || 0);
      const bookId = Number(target.dataset.shelfRemoveBook || 0);
      try {
        const result = await apiFetch(`/api/library/shelves/${shelfId}/books`, { method: 'POST', body: JSON.stringify({ book_id: bookId, enabled: false }) });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { page._libraryData.shelves = result.shelves || []; renderLibraryTab('shelves', page._libraryData); }
        meDataPromise = null;
        notify('Книга убрана с полки');
      } catch (error) { notify(error.message || 'Не удалось изменить полку'); }
      return;
    }
    if (target.matches('[data-journal-import]')) {
      event.preventDefault();
      document.getElementById('readingJournalImportFile')?.click();
      return;
    }
    if (target.matches('[data-journal-import-cancel]')) {
      event.preventDefault();
      const page = document.getElementById('libraryPage');
      if (page?._libraryData) {
        page._libraryData.journal_import_preview = null;
        renderLibraryTab('journal', page._libraryData);
      }
      return;
    }
    if (target.matches('[data-journal-import-preset]')) {
      event.preventDefault();
      selectReadingJournalImportPreset(String(target.dataset.journalImportPreset || ''));
      return;
    }
    if (target.matches('[data-journal-import-apply]')) {
      event.preventDefault();
      const token = String(target.dataset.journalImportApply || '');
      const selectedItems = selectedReadingJournalImportIds();
      if (!selectedItems.length) { notify('Выберите хотя бы одну запись'); return; }
      if (!token || target.disabled) return;
      target.disabled = true;
      try {
        const result = await applyReadingJournalImport(token, selectedItems);
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          applyReadingJournalPayload(page, result);
          page._libraryData.journal_import_preview = null;
          renderLibraryTab('journal', page._libraryData);
        }
        meDataPromise = null;
        const appliedCount = Number(result.import_result?.total_applied || 0);
        const skippedCount = Number(result.import_result?.skipped_after_recheck || 0);
        const autoCount = Number((result.import_result?.auto_included_selection_ids || []).length || 0);
        const extra = [skippedCount ? `защищено после повторной проверки: ${skippedCount}` : '', autoCount ? `служебно добавлено: ${autoCount}` : ''].filter(Boolean).join(' · ');
        notify(`Восстановлено изменений: ${appliedCount}${extra ? ` · ${extra}` : ''}`);
      } catch (error) {
        notify(error.message || 'Не удалось восстановить дневник');
        target.disabled = false;
      }
      return;
    }
    if (target.matches('[data-journal-import-backup]')) {
      event.preventDefault();
      const runId = Number(target.dataset.journalImportBackup || 0);
      if (!runId || target.disabled) return;
      target.disabled = true;
      try { await downloadReadingJournalBackup(runId); notify('Резервная точка подготовлена'); }
      catch (error) { notify(error.message || 'Не удалось скачать резервную точку'); }
      finally { target.disabled = false; }
      return;
    }
    if (target.matches('[data-journal-import-rollback]')) {
      event.preventDefault();
      const runId = Number(target.dataset.journalImportRollback || 0);
      if (!runId || target.disabled || !confirm('Отменить этот импорт? Ручные изменения, сделанные позже, останутся без изменений.')) return;
      target.disabled = true;
      try {
        const result = await rollbackReadingJournalImport(runId);
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { applyReadingJournalPayload(page, result); renderLibraryTab('journal', page._libraryData); }
        meDataPromise = null;
        const restored = Number(result.rollback_result?.total_restored || 0);
        const protectedCount = Number(result.rollback_result?.total_protected || 0);
        notify(protectedCount ? `Импорт отменён частично: восстановлено ${restored}, защищено ${protectedCount}` : `Импорт отменён: восстановлено ${restored}`);
      } catch (error) { notify(error.message || 'Не удалось отменить импорт'); target.disabled = false; }
      return;
    }
    if (target.matches('[data-journal-export]')) {
      event.preventDefault();
      await downloadReadingJournal(String(target.dataset.journalExport || 'json'));
      return;
    }
    if (target.matches('[data-journal-delete]')) {
      event.preventDefault();
      const bookId = Number(target.dataset.journalDelete || 0);
      if (!bookId || !confirm('Очистить личную запись дневника для этого произведения? История чтения и заметки останутся.')) return;
      try {
        const result = await apiFetch(`/api/library/journal/${bookId}`, { method: 'DELETE' });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          applyReadingJournalPayload(page, result);
          renderLibraryTab('journal', page._libraryData);
        }
        meDataPromise = null;
        notify('Запись очищена');
      } catch (error) { notify(error.message || 'Не удалось очистить запись'); }
      return;
    }
    if (target.id === 'clearReadingHistory') {
      event.preventDefault();
      if (!confirm('Очистить всю историю чтения? Сохранённые места останутся.')) return;
      try {
        await apiFetch('/api/library/history', { method: 'DELETE' });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { page._libraryData.reading_history = []; renderLibraryTab('history', page._libraryData); }
        meDataPromise = null;
        notify('История очищена');
      } catch (error) { notify(error.message || 'Не удалось очистить историю'); }
      return;
    }
    if (target.matches('[data-history-delete]')) {
      event.preventDefault();
      const historyId = Number(target.dataset.historyDelete || 0);
      try {
        await apiFetch(`/api/library/history/${historyId}`, { method: 'DELETE' });
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) { page._libraryData.reading_history = (page._libraryData.reading_history || []).filter((item) => Number(item.id) !== historyId); renderLibraryTab('history', page._libraryData); }
        meDataPromise = null;
      } catch (error) { notify(error.message || 'Не удалось удалить запись'); }
      return;
    }
    if (target.matches('[data-annotation-delete]')) {
      event.preventDefault();
      const annotationId = Number(target.dataset.annotationDelete || 0);
      try {
        await apiFetch(`/api/reader/annotations/${annotationId}`, { method: 'DELETE' });
        target.closest('[data-annotation-card]')?.remove();
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) page._libraryData.annotations = (page._libraryData.annotations || []).filter((item) => Number(item.id) !== annotationId);
        meDataPromise = null;
        notify('Запись удалена');
      } catch (error) { notify(error.message || 'Не удалось удалить запись'); }
      return;
    }

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

    if (target.id === 'subscribeBook' || target.id === 'subscribeAuthor') {
      event.preventDefault();
      const page = document.getElementById('bookPage');
      if (!page) return;
      const enabled = !target.classList.contains('saved');
      const isAuthor = target.id === 'subscribeAuthor';
      const id = Number(isAuthor ? page.dataset.authorId : page.dataset.bookId);
      if (!id) return;
      target.disabled = true;
      try {
        await apiFetch(isAuthor ? `/api/author/${id}/subscription` : `/api/book/${id}/subscription`, {
          method: 'POST', body: JSON.stringify({ enabled }),
        });
        target.classList.toggle('saved', enabled);
        target.textContent = isAuthor
          ? (enabled ? '✦ Вы подписаны на автора' : '✦ Подписаться на автора')
          : (enabled ? '🔔 Вы подписаны' : '🔔 Подписаться на книгу');
        meDataPromise = null;
        notify(enabled ? 'Подписка включена' : 'Подписка отключена');
      } catch (error) { notify(error.message || 'Не удалось изменить подписку'); }
      finally { target.disabled = false; }
      return;
    }

    if (target.matches('[data-unsubscribe-book], [data-unsubscribe-author]')) {
      event.preventDefault();
      const isAuthor = target.hasAttribute('data-unsubscribe-author');
      const id = Number(isAuthor ? target.dataset.unsubscribeAuthor : target.dataset.unsubscribeBook);
      if (!id) return;
      target.disabled = true;
      try {
        await apiFetch(isAuthor ? `/api/author/${id}/subscription` : `/api/book/${id}/subscription`, {
          method: 'POST', body: JSON.stringify({ enabled: false }),
        });
        target.closest('.subscription-card')?.remove();
        const page = document.getElementById('libraryPage');
        if (page?._libraryData) {
          const key = isAuthor ? 'authors' : 'books';
          page._libraryData.subscriptions[key] = (page._libraryData.subscriptions[key] || []).filter((item) => Number(item[isAuthor ? 'author_id' : 'book_id']) !== id);
          if (!(page._libraryData.subscriptions.books || []).length && !(page._libraryData.subscriptions.authors || []).length) renderLibraryTab('subscriptions', page._libraryData);
        }
        meDataPromise = null;
        notify('Подписка отключена');
      } catch (error) { target.disabled = false; notify(error.message || 'Не удалось отписаться'); }
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
  flushProgressSyncQueue().catch(() => {});
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
