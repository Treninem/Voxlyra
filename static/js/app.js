(function initTelegram() {
  const tg = window.Telegram?.WebApp;
  if (!tg) return;
  try {
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.('#0b0d19');
    tg.setBackgroundColor?.('#080a16');
  } catch (_) {}
})();

const root = document.documentElement;
const DEFAULTS = {
  theme: 'system',
  fontSize: 18,
  lineHeight: 1.78,
  readerWidth: 'normal',
  audioRate: 1,
  rewindStep: 15,
  autoplayNext: false,
  saveOnPause: true,
  notifications: true,
  contrast: 'normal',
};

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
    contrast: localStorage.getItem('voxContrast') || DEFAULTS.contrast,
  };
}

function setPref(key, value) {
  const map = {
    theme: 'voxTheme',
    fontSize: 'readerFontSize',
    lineHeight: 'readerLineHeight',
    readerWidth: 'readerWidth',
    audioRate: 'voxAudioRate',
    rewindStep: 'voxRewindStep',
    autoplayNext: 'voxAutoplayNext',
    saveOnPause: 'voxSaveOnPause',
    notifications: 'voxNotifications',
    contrast: 'voxContrast',
  };
  localStorage.setItem(map[key], typeof value === 'boolean' ? (value ? '1' : '0') : String(value));
  applySettings();
}

function notify(message) {
  const tg = window.Telegram?.WebApp;
  try {
    if (tg?.showPopup) return tg.showPopup({ message });
    if (tg?.HapticFeedback) tg.HapticFeedback.impactOccurred('light');
  } catch (_) {}
  const toast = document.getElementById('toast');
  if (toast) {
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 1800);
  } else {
    alert(message);
  }
}

function applyTheme(theme = getPrefs().theme) {
  document.body.classList.remove('light-theme', 'dark-theme', 'high-contrast');
  const tg = window.Telegram?.WebApp;
  const isLight = theme === 'light' || (theme === 'system' && tg?.colorScheme === 'light');
  document.body.classList.add(isLight ? 'light-theme' : 'dark-theme');
  localStorage.setItem('voxTheme', theme);
}

function applySettings() {
  const prefs = getPrefs();
  applyTheme(prefs.theme);
  root.style.setProperty('--reader-font-size', `${Math.max(14, Math.min(28, prefs.fontSize))}px`);
  root.style.setProperty('--reader-line-height', String(Math.max(1.45, Math.min(2.15, prefs.lineHeight))));
  document.body.classList.toggle('reader-wide', prefs.readerWidth === 'wide');
  document.body.classList.toggle('high-contrast', prefs.contrast === 'high');

  document.querySelectorAll('[data-theme]').forEach(btn => btn.classList.toggle('active', btn.dataset.theme === prefs.theme));
  document.querySelectorAll('[data-line-height]').forEach(btn => btn.classList.toggle('active', Number(btn.dataset.lineHeight) === prefs.lineHeight));
  document.querySelectorAll('[data-reader-width]').forEach(btn => btn.classList.toggle('active', btn.dataset.readerWidth === prefs.readerWidth));
  document.querySelectorAll('[data-rate]').forEach(btn => btn.classList.toggle('active-rate', Number(btn.dataset.rate) === prefs.audioRate));
  document.querySelectorAll('[data-rewind]').forEach(btn => btn.classList.toggle('active', Number(btn.dataset.rewind) === prefs.rewindStep));
  document.querySelectorAll('[data-contrast]').forEach(btn => btn.classList.toggle('active', btn.dataset.contrast === prefs.contrast));
  document.querySelectorAll('[data-toggle]').forEach(btn => {
    const key = btn.dataset.toggle;
    btn.classList.toggle('active', Boolean(prefs[key]));
    const label = btn.querySelector('span:last-child');
    if (label) label.textContent = Boolean(prefs[key]) ? 'Включено' : 'Выключено';
  });

  const fontValue = document.getElementById('fontValue');
  if (fontValue) fontValue.textContent = `${prefs.fontSize}px`;
  const lineValue = document.getElementById('lineHeightValue');
  if (lineValue) lineValue.textContent = prefs.lineHeight === 1.58 ? 'Компактно' : prefs.lineHeight === 1.95 ? 'Просторно' : 'Обычно';
  const rewindValue = document.getElementById('rewindValue');
  if (rewindValue) rewindValue.textContent = `${prefs.rewindStep} сек.`;
  const preview = document.getElementById('settingsPreviewText');
  if (preview) {
    preview.style.fontSize = `${prefs.fontSize}px`;
    preview.style.lineHeight = String(prefs.lineHeight);
  }

  const player = document.getElementById('voxPlayer');
  if (player) player.playbackRate = prefs.audioRate;
}

function changeFont(delta) {
  const next = Math.max(14, Math.min(28, getPrefs().fontSize + delta));
  setPref('fontSize', next);
}

function resetSettings() {
  [
    'voxTheme', 'readerFontSize', 'readerLineHeight', 'readerWidth', 'voxAudioRate', 'voxRewindStep',
    'voxAutoplayNext', 'voxSaveOnPause', 'voxNotifications', 'voxContrast'
  ].forEach(key => localStorage.removeItem(key));
  applySettings();
  notify('Настройки сброшены');
}

function seekAudio(seconds) {
  const player = document.getElementById('voxPlayer');
  if (!player || !Number.isFinite(player.duration)) return;
  player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + seconds));
}
window.seekAudio = seekAudio;

function setRate(rate) {
  setPref('audioRate', Number(rate));
  const player = document.getElementById('voxPlayer');
  if (player) player.playbackRate = Number(rate);
}
window.setRate = setRate;

function tgInitData() {
  return window.Telegram?.WebApp?.initData || '';
}

async function apiFetch(url, options = {}) {
  const headers = Object.assign({}, options.headers || {}, { 'X-Telegram-Init-Data': tgInitData() });
  if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, Object.assign({}, options, { headers }));
  if (!response.ok) {
    let message = `Ошибка ${response.status}`;
    try { message = (await response.json()).detail || message; } catch (_) { try { message = await response.text(); } catch (_) {} }
    throw new Error(message);
  }
  const ct = response.headers.get('content-type') || '';
  return ct.includes('application/json') ? response.json() : response;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function renderComments(comments) {
  const box = document.getElementById('commentsList');
  if (!box) return;
  if (!comments || comments.length === 0) {
    box.innerHTML = '<p class="muted">Комментариев пока нет. Будьте первым, кто оставит впечатление.</p>';
    return;
  }
  box.innerHTML = comments.map(item => {
    const name = item.username ? '@' + item.username : (item.full_name || 'Читатель');
    return `<article class="comment-card"><b>${escapeHtml(name)}</b><p>${escapeHtml(item.text)}</p></article>`;
  }).join('');
}

function renderReviews(reviews) {
  const box = document.getElementById('reviewsList');
  if (!box) return;
  if (!reviews || reviews.length === 0) {
    box.innerHTML = '<p class="muted">Отзывов пока нет. Оценка появится после первых читателей.</p>';
    return;
  }
  box.innerHTML = reviews.map(item => {
    const name = item.username ? '@' + item.username : (item.full_name || 'Читатель');
    const stars = '★'.repeat(Math.max(1, Math.min(5, Number(item.rating || 5))));
    return `<article class="comment-card"><b>${stars} ${escapeHtml(name)}</b><p>${escapeHtml(item.text || 'Без текста')}</p></article>`;
  }).join('');
}

function calcReadingPercent() {
  const scrollTop = window.scrollY || document.documentElement.scrollTop || 0;
  const height = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
  return Math.max(0, Math.min(100, Math.round(scrollTop / height * 100)));
}

async function initReader() {
  const reader = document.getElementById('readerText');
  if (!reader) return;
  const chapterId = reader.dataset.chapterId;
  const status = document.getElementById('readerStatus');
  const paragraphs = document.getElementById('readerParagraphs');
  try {
    const data = await apiFetch(`/api/reader/${chapterId}`);
    if (!data.allowed) {
      if (status) status.textContent = 'Глава закрыта. Купите доступ в боте или откройте страницу после покупки.';
      if (paragraphs && data.purchase_url) {
        paragraphs.innerHTML = `<section class="empty-card paywall-card"><p>Эта глава платная.</p><p><b>${data.chapter.price_stars} Stars</b></p><a class="button-link" href="${data.purchase_url}">💫 Купить в боте</a></section>`;
      }
      return;
    }
    if (status) status.textContent = data.progress_percent ? `Продолжить с места: ${data.progress_percent}%` : 'Доступ открыт.';
    if (paragraphs && data.chapter.text) {
      paragraphs.innerHTML = data.chapter.text.split('\n').filter(p => p.trim()).map(p => `<p>${escapeHtml(p)}</p>`).join('');
      if (data.progress_percent > 0) {
        setTimeout(() => window.scrollTo({ top: document.documentElement.scrollHeight * data.progress_percent / 100, behavior: 'smooth' }), 300);
      }
    }
    renderComments(data.comments);
  } catch (error) {
    if (status) status.textContent = tgInitData() ? `Не удалось проверить доступ: ${error.message}` : 'Откройте эту страницу внутри Telegram, чтобы проверить доступ и сохранить прогресс.';
  }
}

async function saveReaderProgress() {
  const reader = document.getElementById('readerText');
  if (!reader) return;
  const percent = calcReadingPercent();
  await apiFetch(`/api/reader/${reader.dataset.chapterId}/progress`, {
    method: 'POST',
    body: JSON.stringify({ position_percent: percent }),
  });
  const label = document.getElementById('progressLabel');
  if (label) label.textContent = `Сохранено: ${percent}%`;
}

let autoProgressTimer = null;
window.addEventListener('scroll', () => {
  if (!document.getElementById('readerText')) return;
  clearTimeout(autoProgressTimer);
  autoProgressTimer = setTimeout(() => saveReaderProgress().catch(() => {}), 1200);
});

async function initAudioPage() {
  const page = document.getElementById('audioPage');
  if (!page) return;
  const audioId = page.dataset.audioId;
  const status = document.getElementById('audioStatus');
  const paywall = document.getElementById('audioPaywall');
  const player = document.getElementById('voxPlayer');
  try {
    const meta = await apiFetch(`/api/audio/${audioId}/meta`);
    if (!meta.allowed) {
      if (status) status.textContent = 'Аудио закрыто. Купите доступ в боте или откройте после покупки.';
      if (paywall) paywall.style.display = 'block';
      if (player) player.style.display = 'none';
      return;
    }
    if (status) status.textContent = meta.progress_seconds ? `Продолжить с ${Math.floor(meta.progress_seconds / 60)} мин.` : 'Доступ открыт. Нажмите Play.';
    const response = await apiFetch(`/api/audio/${audioId}/file`);
    const blob = await response.blob();
    if (player) {
      player.src = URL.createObjectURL(blob);
      player.style.display = 'block';
      player.addEventListener('loadedmetadata', () => {
        const prefs = getPrefs();
        player.playbackRate = prefs.audioRate;
        if (meta.progress_seconds > 0 && meta.progress_seconds < player.duration) player.currentTime = meta.progress_seconds;
      });
      player.addEventListener('timeupdate', () => {
        const label = document.getElementById('audioProgressLabel');
        const seconds = Math.floor(player.currentTime || 0);
        const min = Math.floor(seconds / 60);
        const sec = String(seconds % 60).padStart(2, '0');
        if (label) label.textContent = `${min}:${sec}`;
      });
      player.addEventListener('pause', () => {
        if (getPrefs().saveOnPause) saveAudioProgress().catch(() => {});
      });
      player.addEventListener('ended', () => {
        if (getPrefs().autoplayNext) notify('Аудиоглава завершена. Следующая появится в списке книги.');
      });
    }
  } catch (error) {
    if (status) status.textContent = tgInitData() ? `Не удалось загрузить аудио: ${error.message}` : 'Откройте аудио внутри Telegram, чтобы проверить доступ.';
  }
}

async function saveAudioProgress() {
  const page = document.getElementById('audioPage');
  const player = document.getElementById('voxPlayer');
  if (!page || !player || !player.src) return;
  await apiFetch(`/api/audio/${page.dataset.audioId}/progress`, {
    method: 'POST',
    body: JSON.stringify({ position_seconds: Math.floor(player.currentTime || 0) }),
  });
}

async function initBookPage() {
  const page = document.getElementById('bookPage');
  if (!page) return;
  const bookId = page.dataset.bookId;
  try {
    const state = await apiFetch(`/api/book/${bookId}/state`);
    renderReviews(state.reviews);
  } catch (_) {}
}

function bindEvents() {
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
    if (target.matches('[data-toggle]')) { event.preventDefault(); const key = target.dataset.toggle; setPref(key, !getPrefs()[key]); return; }
    if (target.id === 'resetLocalSettings') { event.preventDefault(); resetSettings(); return; }
    if (target.id === 'themeToggle') { event.preventDefault(); setPref('theme', getPrefs().theme === 'light' ? 'dark' : 'light'); return; }
    if (target.id === 'saveReadingProgress') { event.preventDefault(); saveReaderProgress().then(() => notify('Место сохранено')).catch(() => notify('Не удалось сохранить место. Откройте главу внутри Telegram.')); return; }
    if (target.id === 'saveAudioProgress') { event.preventDefault(); saveAudioProgress().then(() => notify('Место сохранено')).catch(() => notify('Не удалось сохранить место')); return; }
    if (target.id === 'audioBack') { event.preventDefault(); seekAudio(-getPrefs().rewindStep); return; }
    if (target.id === 'audioForward') { event.preventDefault(); seekAudio(getPrefs().rewindStep); return; }
    if (target.id === 'bookmarkReading' || target.id === 'bookmarkFavorite') {
      event.preventDefault();
      const page = document.getElementById('bookPage');
      if (!page) return;
      const status = target.id === 'bookmarkFavorite' ? 'favorite' : 'reading';
      try { await apiFetch(`/api/book/${page.dataset.bookId}/bookmark`, { method: 'POST', body: JSON.stringify({ status }) }); notify(status === 'favorite' ? 'Добавлено в любимое' : 'Добавлено в закладки'); }
      catch (_) { notify('Откройте внутри Telegram, чтобы сохранить закладку.'); }
      return;
    }
    if (target.id === 'sendReview') {
      event.preventDefault();
      const page = document.getElementById('bookPage');
      if (!page) return;
      const rating = document.getElementById('reviewRating')?.value || 5;
      const text = document.getElementById('reviewText')?.value || '';
      try {
        const data = await apiFetch(`/api/book/${page.datasetBookId || page.dataset.bookId}/review`, { method: 'POST', body: JSON.stringify({ rating, text }) });
        renderReviews(data.reviews);
        const input = document.getElementById('reviewText');
        if (input) input.value = '';
        notify('Отзыв сохранён');
      } catch (_) { notify('Не удалось сохранить отзыв. Откройте внутри Telegram.'); }
      return;
    }
    if (target.id === 'sendComment') {
      event.preventDefault();
      const reader = document.getElementById('readerText');
      const field = document.getElementById('commentText');
      if (!reader || !field) return;
      try {
        const data = await apiFetch(`/api/reader/${reader.dataset.chapterId}/comments`, { method: 'POST', body: JSON.stringify({ text: field.value }) });
        field.value = '';
        renderComments(data.comments);
        notify('Комментарий опубликован');
      } catch (error) { notify('Комментарий не отправлен: ' + error.message); }
      return;
    }
  });

  document.querySelectorAll('.reader-ad-card').forEach((link) => {
    link.addEventListener('click', () => {
      apiFetch('/api/reader/ad-click', {
        method: 'POST',
        body: JSON.stringify({
          promoted_book_id: link.dataset.promotedBookId,
          campaign_id: link.dataset.campaignId || null,
          source_book_id: link.dataset.sourceBookId || null,
          source_chapter_id: link.dataset.sourceChapterId || null,
        }),
      }).catch(() => {});
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  applySettings();
  bindEvents();
  initReader();
  initAudioPage();
  initBookPage();
});
