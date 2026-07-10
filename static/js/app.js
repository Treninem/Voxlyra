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
  ttsVoice: 'irina', ttsStyle: 'expressive', ttsRate: 1, ttsAutoNext: true,
  notifications: true, notificationChapters: true, notificationAudio: true, notificationDiscounts: true,
  contrast: 'normal', focusMode: false, showReaderAds: true,
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
const TTS_DEVICE_CACHE_NAME = 'voxlyra-reader-tts-v1';
const TTS_DEVICE_CACHE_PREFIX = `${window.location.origin}/__voxlyra_tts_cache__/`;

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
    contrast: localStorage.getItem('voxContrast') || DEFAULTS.contrast,
    focusMode: getStoredBool('voxFocusMode', DEFAULTS.focusMode),
    showReaderAds: getStoredBool('voxShowReaderAds', DEFAULTS.showReaderAds),
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
  const ttsPlayer = document.getElementById('readerTtsPlayer');
  if (ttsPlayer) ttsPlayer.playbackRate = 1;
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
  ['voxTheme','readerFontSize','readerLineHeight','readerWidth','voxAudioRate','voxRewindStep','voxAutoplayNext','voxSaveOnPause','voxTtsVoice','voxTtsStyle','voxTtsRate','voxTtsAutoNext','voxNotifications','voxNotificationChapters','voxNotificationAudio','voxNotificationDiscounts','voxContrast','voxFocusMode','voxShowReaderAds'].forEach((key) => localStorage.removeItem(key));
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
  window.addEventListener('resize', updateReaderProgressBar, { passive: true });
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
    const moderationNotice = document.getElementById('readerModerationNotice');
    const commentsBox = document.getElementById('commentsBox');
    const saveProgressButton = document.getElementById('saveReadingProgress');
    if (data.moderation_access) {
      if (moderationNotice) moderationNotice.hidden = false;
      if (commentsBox) commentsBox.hidden = true;
      if (saveProgressButton) saveProgressButton.hidden = true;
      if (status) status.textContent = 'Открыто в служебном режиме проверки';
    } else {
      if (moderationNotice) moderationNotice.hidden = true;
      if (status) status.textContent = data.progress_percent ? `Продолжаем с отметки ${data.progress_percent}%` : 'Глава открыта';
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
  } catch (error) {
    if (status) status.textContent = 'Не удалось открыть главу. Попробуйте ещё раз.';
  }
}

function readerTtsPlayer() { return document.getElementById('readerTtsPlayer'); }

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

function updateReaderTtsStatus(text) {
  const status = document.getElementById('readerTtsStatus');
  if (status) status.textContent = text;
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
  return `voxTtsProgress:${Number(chapterId)}:${readerTtsProfileKey(profile)}`;
}

function getLocalReaderTtsProgress(chapterId, profile) {
  const value = Number(localStorage.getItem(readerTtsProgressStorageKey(chapterId, profile)) || 0);
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function saveLocalReaderTtsProgress(chapterId, position, profile) {
  if (!chapterId) return;
  localStorage.setItem(readerTtsProgressStorageKey(chapterId, profile), String(Math.max(0, Math.floor(position || 0))));
}

async function saveReaderTtsProgress() {
  const player = readerTtsPlayer();
  const chapterId = Number(player?.dataset.chapterId || readerTtsChapterId());
  if (!player?.src || !chapterId) return;
  const profile = {
    voice: player.dataset.voice || getPrefs().ttsVoice,
    rate: Number(player.dataset.rate || getPrefs().ttsRate),
    style: player.dataset.style || getPrefs().ttsStyle,
  };
  const position = Math.max(0, Math.floor(player.currentTime || 0));
  saveLocalReaderTtsProgress(chapterId, position, profile);
  if (!tgInitData()) return;
  await apiFetch(`/api/reader/${chapterId}/tts/progress`, {
    method: 'POST',
    body: JSON.stringify({
      position_seconds: position,
      voice: profile.voice,
      rate: profile.rate,
      style: profile.style,
    }),
  });
}

function seekReaderTts(seconds) {
  const player = readerTtsPlayer();
  if (!player || !Number.isFinite(player.duration)) return;
  player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + Number(seconds || 0)));
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
    readerTtsPlayer()?.pause();
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
  if (commentsBox && meta.moderation_access) commentsBox.hidden = true;
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
  window.scrollTo({ top: 0, behavior: 'smooth' });
  setTimeout(updateReaderProgressBar, 100);
}

function updateReaderTtsMediaSession(meta) {
  if (!('mediaSession' in navigator) || !meta?.chapter) return;
  const chapter = meta.chapter;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: chapter.title || `Глава ${chapter.number}`,
      artist: chapter.pen_name || 'Автор не указан',
      album: chapter.book_title || 'Вокслира',
      artwork: [{ src: `/media/cover/${Number(chapter.book_id)}`, sizes: '512x512' }],
    });
  } catch (_) {}
}

function updateReaderTtsPositionState() {
  const player = readerTtsPlayer();
  if (!player || !('mediaSession' in navigator) || !Number.isFinite(player.duration) || player.duration <= 0) return;
  try {
    navigator.mediaSession.setPositionState({
      duration: player.duration,
      playbackRate: player.playbackRate || 1,
      position: Math.max(0, Math.min(player.duration, player.currentTime || 0)),
    });
  } catch (_) {}
}

function bindReaderTtsMediaActions() {
  if (!('mediaSession' in navigator)) return;
  const handlers = {
    play: () => readerTtsPlayer()?.play(),
    pause: () => readerTtsPlayer()?.pause(),
    seekbackward: (details) => seekReaderTts(-(details.seekOffset || 15)),
    seekforward: (details) => seekReaderTts(details.seekOffset || 15),
    seekto: (details) => { const player = readerTtsPlayer(); if (player && Number.isFinite(details.seekTime)) player.currentTime = details.seekTime; },
    previoustrack: () => { const id = readerTtsMeta?.navigation?.previous?.id; if (id) loadReaderTtsChapter(Number(id), true); },
    nexttrack: () => { const id = readerTtsMeta?.navigation?.next?.id; if (id) loadReaderTtsChapter(Number(id), true); },
  };
  Object.entries(handlers).forEach(([action, handler]) => {
    try { navigator.mediaSession.setActionHandler(action, handler); } catch (_) {}
  });
}

function wait(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function apiFetchWithRetry(url, options = {}, attempts = 3, timeoutMs = 600000) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      return await apiFetch(url, Object.assign({}, options, { signal: controller.signal }));
    } catch (error) {
      lastError = error;
      const status = Number(error?.status || 0);
      const retryable = !status || status === 408 || status === 429 || status >= 500;
      if (!retryable || attempt >= attempts) throw error;
      await wait(700 * attempt);
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError || new Error('Не удалось связаться с сервером');
}

function ttsDeviceAudioRequest(meta) {
  const chapterId = Number(meta?.chapter?.id || 0);
  const key = encodeURIComponent(String(meta?.cache_key || ''));
  return new Request(`${TTS_DEVICE_CACHE_PREFIX}audio/${chapterId}/${key}.mp3`);
}

function ttsDeviceMetaRequest(chapterId, profile = readerTtsCurrentProfile()) {
  return new Request(`${TTS_DEVICE_CACHE_PREFIX}meta/${Number(chapterId)}/${encodeURIComponent(readerTtsProfileKey(profile))}.json`);
}

async function openTtsDeviceCache() {
  if (!('caches' in window)) return null;
  try { return await caches.open(TTS_DEVICE_CACHE_NAME); }
  catch (_) { return null; }
}

async function cacheReaderTtsMetadata(meta) {
  if (meta?.device_cache_allowed === false) return;
  const cache = await openTtsDeviceCache();
  if (!cache || !meta?.chapter?.id) return;
  const profile = { voice: meta.voice, rate: Number(meta.rate || 1), style: meta.style };
  const response = new Response(JSON.stringify(meta), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'X-Voxlyra-Cached-At': String(Date.now()) },
  });
  try { await cache.put(ttsDeviceMetaRequest(meta.chapter.id, profile), response); }
  catch (_) {}
}

async function getCachedReaderTtsMetadata(chapterId, profile) {
  const cache = await openTtsDeviceCache();
  if (!cache) return null;
  try {
    const response = await cache.match(ttsDeviceMetaRequest(chapterId, profile));
    if (!response) return null;
    const meta = await response.json();
    meta._fromDevice = true;
    return meta;
  } catch (_) { return null; }
}

async function getCachedReaderTtsAudio(meta) {
  if (!meta?.cache_key) return null;
  const cache = await openTtsDeviceCache();
  if (!cache) return null;
  try { return await cache.match(ttsDeviceAudioRequest(meta)); }
  catch (_) { return null; }
}

async function pruneReaderTtsDeviceCache(meta) {
  const cache = await openTtsDeviceCache();
  if (!cache || !meta?.chapter?.id) return;
  const chapterId = Number(meta.chapter.id);
  const keepAudio = ttsDeviceAudioRequest(meta).url;
  const profile = { voice: meta.voice, rate: Number(meta.rate || 1), style: meta.style };
  const keepMeta = ttsDeviceMetaRequest(chapterId, profile).url;
  let keys = [];
  try { keys = await cache.keys(); } catch (_) { return; }
  const chapterAudioPrefix = `${TTS_DEVICE_CACHE_PREFIX}audio/${chapterId}/`;
  const chapterMetaPrefix = `${TTS_DEVICE_CACHE_PREFIX}meta/${chapterId}/`;
  await Promise.all(keys.map(async (request) => {
    if ((request.url.startsWith(chapterAudioPrefix) && request.url !== keepAudio) ||
        (request.url.startsWith(chapterMetaPrefix) && request.url !== keepMeta)) {
      try { await cache.delete(request); } catch (_) {}
    }
  }));

  // На устройстве храним не более 40 полностью подготовленных глав.
  try {
    keys = (await cache.keys()).filter((request) => request.url.startsWith(`${TTS_DEVICE_CACHE_PREFIX}audio/`));
    if (keys.length <= 40) return;
    const dated = [];
    for (const request of keys) {
      const response = await cache.match(request);
      dated.push({ request, time: Number(response?.headers.get('X-Voxlyra-Cached-At') || 0) });
    }
    dated.sort((a, b) => a.time - b.time);
    for (const item of dated.slice(0, Math.max(0, dated.length - 40))) await cache.delete(item.request);
  } catch (_) {}
}

async function cacheReaderTtsAudio(meta, attempts = 2) {
  if (meta?.device_cache_allowed === false || !meta?.audio_url || !meta?.cache_key) return false;
  const already = await getCachedReaderTtsAudio(meta);
  if (already) return true;
  const cache = await openTtsDeviceCache();
  if (!cache) return false;
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 900000);
    try {
      const response = await fetch(meta.audio_url, {
        credentials: 'same-origin',
        headers: { 'X-Telegram-Init-Data': tgInitData() },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error('Озвучивание не загрузилось');
      const blob = await response.blob();
      const stored = new Response(blob, {
        headers: {
          'Content-Type': response.headers.get('content-type') || 'audio/mpeg',
          'Content-Length': String(blob.size),
          'X-Voxlyra-Cached-At': String(Date.now()),
        },
      });
      await cache.put(ttsDeviceAudioRequest(meta), stored);
      const metaProfileKey = readerTtsProfileKey({ voice: meta.voice, rate: meta.rate, style: meta.style });
      const activeForCurrent = Number(meta.chapter.id) === readerTtsChapterId() && metaProfileKey === readerTtsProfileKey();
      const activeForPrefetch = readerTtsPrefetch?.chapterId === Number(meta.chapter.id) && readerTtsPrefetch?.profileKey === metaProfileKey;
      if (activeForCurrent || activeForPrefetch) await pruneReaderTtsDeviceCache(meta);
      return true;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await wait(1000 * attempt);
    } finally {
      clearTimeout(timer);
    }
  }
  if (lastError) throw lastError;
  return false;
}

function releaseReaderTtsObjectUrl() {
  if (!readerTtsObjectUrl) return;
  try { URL.revokeObjectURL(readerTtsObjectUrl); } catch (_) {}
  readerTtsObjectUrl = '';
}

async function readerTtsSource(meta) {
  const cached = await getCachedReaderTtsAudio(meta);
  if (!cached) return { src: meta.audio_url, cached: false };
  const blob = await cached.blob();
  releaseReaderTtsObjectUrl();
  readerTtsObjectUrl = URL.createObjectURL(blob);
  return { src: readerTtsObjectUrl, cached: true };
}

async function requestReaderTtsMeta(chapterId, profile = readerTtsCurrentProfile(), allowDeviceFallback = true) {
  const { voice, rate, style } = profile;
  const url = `/api/reader/${Number(chapterId)}/tts?voice=${encodeURIComponent(voice)}&rate=${encodeURIComponent(rate)}&style=${encodeURIComponent(style)}`;
  try {
    const meta = await apiFetchWithRetry(url, {}, 3, 900000);
    await cacheReaderTtsMetadata(meta);
    return meta;
  } catch (error) {
    const status = Number(error?.status || 0);
    const temporaryFailure = !status || status === 408 || status === 429 || status >= 500;
    if (allowDeviceFallback && temporaryFailure) {
      const cachedMeta = await getCachedReaderTtsMetadata(chapterId, profile);
      if (cachedMeta && await getCachedReaderTtsAudio(cachedMeta)) return cachedMeta;
    }
    throw error;
  }
}

function scheduleCurrentReaderTtsCache(meta) {
  const player = readerTtsPlayer();
  clearTimeout(readerTtsCacheTimer);
  if (player && readerTtsCacheListener) player.removeEventListener('canplaythrough', readerTtsCacheListener);
  readerTtsCacheListener = null;
  const cacheNow = () => {
    clearTimeout(readerTtsCacheTimer);
    if (player) player.removeEventListener('canplaythrough', cacheNow);
    readerTtsCacheListener = null;
    cacheReaderTtsAudio(meta).catch(() => {});
  };
  if (player) {
    readerTtsCacheListener = cacheNow;
    player.addEventListener('canplaythrough', cacheNow, { once: true });
  }
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  if (connection?.saveData || ['slow-2g', '2g'].includes(connection?.effectiveType)) return;
  // Запасной запуск только через минуту, чтобы не конкурировать с первым потоковым воспроизведением.
  readerTtsCacheTimer = setTimeout(cacheNow, 60000);
}

function startNextReaderTtsPrefetch(meta) {
  const nextId = Number(meta?.navigation?.next?.id || 0);
  if (!getPrefs().ttsAutoNext || !nextId) return null;
  const profile = { voice: meta.voice, rate: Number(meta.rate || 1), style: meta.style };
  const profileKey = readerTtsProfileKey(profile);
  if (readerTtsPrefetch?.chapterId === nextId && readerTtsPrefetch?.profileKey === profileKey) return readerTtsPrefetch.promise;
  const promise = (async () => {
    const nextMeta = await requestReaderTtsMeta(nextId, profile, true);
    await cacheReaderTtsAudio(nextMeta, 3);
    return nextMeta;
  })().catch(() => null);
  readerTtsPrefetch = { chapterId: nextId, profileKey, promise };
  return promise;
}

async function recoverReaderTtsPlayback() {
  const player = readerTtsPlayer();
  const meta = readerTtsMeta;
  if (!player || !meta || readerTtsLoading) return;
  const attempts = Number(player.dataset.recoveryAttempts || 0);
  if (attempts >= 2) {
    updateReaderTtsStatus('Связь прервалась. Нажмите «Озвучить», чтобы продолжить.');
    return;
  }
  player.dataset.recoveryAttempts = String(attempts + 1);
  const position = Math.max(0, Number(player.currentTime || 0));
  updateReaderTtsStatus('Восстанавливаем озвучивание…');
  try {
    let freshMeta = meta;
    const cached = await getCachedReaderTtsAudio(meta);
    if (!cached) freshMeta = await requestReaderTtsMeta(meta.chapter.id, { voice: meta.voice, rate: meta.rate, style: meta.style }, true);
    const source = await readerTtsSource(freshMeta);
    player._voxStartPosition = position;
    player.src = source.src;
    player.load();
    await player.play();
  } catch (_) {
    updateReaderTtsStatus('Не удалось восстановить связь. Попробуйте ещё раз.');
  }
}

async function applyReaderTtsMeta(meta, autoPlay = false) {
  const panel = document.getElementById('readerTtsPanel');
  const player = readerTtsPlayer();
  if (!panel || !player || !meta?.chapter) return;
  readerTtsMeta = meta;
  setPref('ttsVoice', meta.voice || getPrefs().ttsVoice);
  setPref('ttsRate', Number(meta.rate || getPrefs().ttsRate));
  setPref('ttsStyle', meta.style || getPrefs().ttsStyle);
  updateReaderPageForTts(meta);
  updateReaderTtsMediaSession(meta);
  player.pause();
  const profile = { voice: meta.voice, rate: Number(meta.rate || 1), style: meta.style };
  const localProgress = getLocalReaderTtsProgress(meta.chapter.id, profile);
  const source = await readerTtsSource(meta);
  player.dataset.chapterId = String(meta.chapter.id);
  player.dataset.voice = meta.voice || profile.voice;
  player.dataset.rate = String(meta.rate || profile.rate);
  player.dataset.style = meta.style || profile.style;
  player.dataset.cacheKey = String(meta.cache_key || '');
  player.dataset.recoveryAttempts = '0';
  player._voxStartPosition = Math.max(0, Number(meta.progress_seconds || 0), localProgress);
  player.src = source.src;
  player.hidden = false;
  panel.classList.add('has-audio');
  document.getElementById('readerTtsControls')?.removeAttribute('hidden');
  document.getElementById('readerTtsSleep')?.removeAttribute('hidden');
  player.load();
  const place = source.cached || meta._fromDevice ? ' · сохранено на устройстве' : '';
  updateReaderTtsStatus(`Глава ${meta.chapter.number} готова${place}`);
  if (!source.cached) scheduleCurrentReaderTtsCache(meta);
  if (autoPlay) {
    try { await player.play(); }
    catch (_) { updateReaderTtsStatus('Озвучивание готово. Нажмите Play в плеере.'); }
  }
}

async function loadReaderTtsChapter(chapterId, autoPlay = false, preparedMeta = null) {
  if (readerTtsLoading || !chapterId) return;
  const panel = document.getElementById('readerTtsPanel');
  const player = readerTtsPlayer();
  if (!panel || !player) return;
  readerTtsLoading = true;
  panel.classList.add('is-generating');
  const startButton = document.getElementById('readerTtsStart');
  if (startButton) startButton.textContent = 'Подготавливаем…';
  updateReaderTtsStatus(navigator.onLine ? 'Готовим озвучивание главы…' : 'Проверяем сохранённую озвучку…');
  const profile = readerTtsCurrentProfile();
  try {
    const meta = preparedMeta || await requestReaderTtsMeta(chapterId, profile, true);
    await applyReaderTtsMeta(meta, autoPlay);
  } catch (error) {
    updateReaderTtsStatus(error.message || 'Не удалось подготовить озвучивание');
    notify(error.message || 'Озвучивание недоступно');
  } finally {
    readerTtsLoading = false;
    panel.classList.remove('is-generating');
    if (startButton) startButton.textContent = '▶ Озвучить';
  }
}

async function initReaderTts() {
  const panel = document.getElementById('readerTtsPanel');
  const player = readerTtsPlayer();
  if (!panel || !player) return;
  applySettings();
  setReaderTtsOptionsExpanded(false);
  bindReaderTtsMediaActions();
  if (!tgInitData()) {
    updateReaderTtsStatus('Откройте книгу внутри Telegram, чтобы включить озвучивание.');
    return;
  }
  try {
    const data = await apiFetchWithRetry('/api/reader/tts/voices', {}, 3, 30000);
    if (!data.enabled) {
      updateReaderTtsStatus(data.message || 'Локальное озвучивание пока недоступно');
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
  } catch (error) {
    const cachedMeta = await getCachedReaderTtsMetadata(readerTtsChapterId(), readerTtsCurrentProfile());
    if (cachedMeta && await getCachedReaderTtsAudio(cachedMeta)) updateReaderTtsStatus('Доступна сохранённая озвучка');
    else updateReaderTtsStatus(error.message || 'Не удалось проверить озвучивание');
  }

  player.addEventListener('loadedmetadata', () => {
    player.playbackRate = 1;
    const start = Number(player._voxStartPosition || 0);
    if (start > 0 && Number.isFinite(player.duration) && start < player.duration - 3) player.currentTime = start;
    player._voxStartPosition = 0;
    updateReaderTtsPositionState();
  });
  player.addEventListener('play', () => {
    panel.classList.add('is-playing');
    try { navigator.mediaSession.playbackState = 'playing'; } catch (_) {}
    startNextReaderTtsPrefetch(readerTtsMeta);
  });
  player.addEventListener('pause', () => {
    panel.classList.remove('is-playing');
    try { navigator.mediaSession.playbackState = 'paused'; } catch (_) {}
    saveReaderTtsProgress().catch(() => {});
  });
  player.addEventListener('timeupdate', () => {
    if (readerTtsMeta?.chapter) updateReaderTtsStatus(`Глава ${readerTtsMeta.chapter.number} · ${readerTtsTime(player.currentTime)} из ${readerTtsTime(player.duration)}`);
    updateReaderTtsPositionState();
    clearTimeout(readerTtsProgressTimer);
    readerTtsProgressTimer = setTimeout(() => saveReaderTtsProgress().catch(() => {}), 3000);
  });
  player.addEventListener('stalled', () => {
    if (!player.paused) updateReaderTtsStatus('Связь нестабильна, продолжаем загрузку…');
  });
  player.addEventListener('error', () => { recoverReaderTtsPlayback().catch(() => {}); });
  player.addEventListener('ended', async () => {
    panel.classList.remove('is-playing');
    clearTimeout(readerTtsCacheTimer);
    cacheReaderTtsAudio(readerTtsMeta).catch(() => {});
    try { await saveReaderProgress(100); } catch (_) {}
    try { await saveReaderTtsProgress(); } catch (_) {}
    const nextId = Number(readerTtsMeta?.navigation?.next?.id || 0);
    if (getPrefs().ttsAutoNext && nextId) {
      let nextMeta = null;
      const expectedProfile = readerTtsProfileKey({ voice: readerTtsMeta.voice, rate: readerTtsMeta.rate, style: readerTtsMeta.style });
      if (readerTtsPrefetch?.chapterId === nextId && readerTtsPrefetch?.profileKey === expectedProfile) {
        updateReaderTtsStatus('Переключаем на подготовленную главу…');
        nextMeta = await readerTtsPrefetch.promise;
      }
      readerTtsPrefetch = null;
      if (nextMeta) await loadReaderTtsChapter(nextId, true, nextMeta);
      else await loadReaderTtsChapter(nextId, true);
    } else {
      updateReaderTtsStatus(nextId ? 'Глава закончена' : 'Книга закончена');
    }
  });
  player.addEventListener('ratechange', updateReaderTtsPositionState);
  window.addEventListener('online', () => {
    if (player.src) saveReaderTtsProgress().catch(() => {});
    if (readerTtsMeta && !player.paused) startNextReaderTtsPrefetch(readerTtsMeta);
  });
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
      readerTtsPrefetch = null;
      setPref('ttsVoice', event.target.value);
      const player = readerTtsPlayer();
      if (player?.src) await loadReaderTtsChapter(readerTtsChapterId(), true);
      return;
    }
    if (event.target.id === 'readerTtsStyle') {
      readerTtsPrefetch = null;
      setPref('ttsStyle', event.target.value);
      const player = readerTtsPlayer();
      if (player?.src) await loadReaderTtsChapter(readerTtsChapterId(), true);
      return;
    }
    if (event.target.id === 'readerTtsRate') {
      readerTtsPrefetch = null;
      const rate = Number(event.target.value) || 1;
      setPref('ttsRate', rate);
      const player = readerTtsPlayer();
      if (player?.src) await loadReaderTtsChapter(readerTtsChapterId(), true);
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
    if (readerTtsPlayer()?.src) saveReaderTtsProgress().catch(() => {});
    if (document.getElementById('audioPage')) saveAudioProgress().catch(() => {});
    releaseReaderTtsObjectUrl();
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
  initReaderTts();
  initAudioPage();
  initBookPage();
  initLibrary();
});
