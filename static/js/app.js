(function initTelegram() {
  const tg = window.Telegram?.WebApp;
  if (!tg) return;
  try {
    tg.ready();
    tg.expand();
  } catch (_) {}
})();

const root = document.documentElement;
function applyTheme(theme = localStorage.getItem('voxTheme') || 'system') {
  document.body.classList.remove('light-theme');
  const tg = window.Telegram?.WebApp;
  if (theme === 'light' || (theme === 'system' && tg?.colorScheme === 'light')) document.body.classList.add('light-theme');
  localStorage.setItem('voxTheme', theme);
  document.querySelectorAll('[data-theme]').forEach(btn => btn.classList.toggle('active', btn.dataset.theme === theme));
}
let fontSize = Number(localStorage.getItem('readerFontSize') || 18);
function applyFontSize() {
  root.style.setProperty('--reader-font-size', `${fontSize}px`);
  localStorage.setItem('readerFontSize', String(fontSize));
}
applyTheme();
applyFontSize();

document.getElementById('fontPlus')?.addEventListener('click', () => {
  fontSize = Math.min(28, fontSize + 1);
  applyFontSize();
});
document.getElementById('fontMinus')?.addEventListener('click', () => {
  fontSize = Math.max(14, fontSize - 1);
  applyFontSize();
});
document.getElementById('themeToggle')?.addEventListener('click', () => {
  const current = localStorage.getItem('voxTheme') || 'system';
  applyTheme(current === 'light' ? 'dark' : 'light');
});
document.querySelectorAll('[data-theme]').forEach(btn => btn.addEventListener('click', () => applyTheme(btn.dataset.theme)));
document.getElementById('fontReset')?.addEventListener('click', () => { fontSize = 18; applyFontSize(); });
document.getElementById('resetLocalSettings')?.addEventListener('click', () => {
  localStorage.removeItem('voxTheme');
  localStorage.removeItem('readerFontSize');
  fontSize = 18;
  applyTheme('system');
  applyFontSize();
  alert('Настройки сброшены');
});

function seekAudio(seconds) {
  const player = document.getElementById('voxPlayer');
  if (!player || !Number.isFinite(player.duration)) return;
  player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + seconds));
}
window.seekAudio = seekAudio;

function setRate(rate) {
  localStorage.setItem('voxAudioRate', String(rate));
  const player = document.getElementById('voxPlayer');
  if (!player) return;
  player.playbackRate = rate;
  document.querySelectorAll('[data-rate]').forEach(btn => btn.classList.toggle('active-rate', Number(btn.dataset.rate) === rate));
}
window.setRate = setRate;

function tgInitData() {
  return window.Telegram?.WebApp?.initData || '';
}

async function apiFetch(url, options = {}) {
  const headers = Object.assign({}, options.headers || {}, {
    'X-Telegram-Init-Data': tgInitData(),
  });
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
    box.innerHTML = '<p class="muted">Комментариев пока нет.</p>';
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
    box.innerHTML = '<p class="muted">Отзывов пока нет.</p>';
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

document.getElementById('saveReadingProgress')?.addEventListener('click', () => {
  saveReaderProgress().catch(() => alert('Не удалось сохранить место. Откройте главу внутри Telegram.'));
});

let autoProgressTimer = null;
window.addEventListener('scroll', () => {
  if (!document.getElementById('readerText')) return;
  clearTimeout(autoProgressTimer);
  autoProgressTimer = setTimeout(() => saveReaderProgress().catch(() => {}), 1200);
});

document.getElementById('sendComment')?.addEventListener('click', async () => {
  const reader = document.getElementById('readerText');
  const field = document.getElementById('commentText');
  if (!reader || !field) return;
  try {
    const data = await apiFetch(`/api/reader/${reader.dataset.chapterId}/comments`, {
      method: 'POST',
      body: JSON.stringify({ text: field.value }),
    });
    field.value = '';
    renderComments(data.comments);
  } catch (error) {
    alert('Комментарий не отправлен: ' + error.message);
  }
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
        const savedRate = Number(localStorage.getItem('voxAudioRate') || 1);
        if (savedRate) setRate(savedRate);
        if (meta.progress_seconds > 0 && meta.progress_seconds < player.duration) player.currentTime = meta.progress_seconds;
      });
      player.addEventListener('timeupdate', () => {
        const label = document.getElementById('audioProgressLabel');
        if (label) label.textContent = `${Math.floor(player.currentTime || 0)} сек.`;
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

document.getElementById('saveAudioProgress')?.addEventListener('click', () => {
  saveAudioProgress().then(() => alert('Место сохранено')).catch(() => alert('Не удалось сохранить место'));
});
setInterval(() => saveAudioProgress().catch(() => {}), 20000);

async function initBookPage() {
  const page = document.getElementById('bookPage');
  if (!page) return;
  const bookId = page.dataset.bookId;
  try {
    const state = await apiFetch(`/api/book/${bookId}/state`);
    renderReviews(state.reviews);
  } catch (_) {}

  document.getElementById('bookmarkReading')?.addEventListener('click', async () => {
    try {
      await apiFetch(`/api/book/${bookId}/bookmark`, { method: 'POST', body: JSON.stringify({ status: 'reading' }) });
      alert('Добавлено в закладки');
    } catch (error) { alert('Откройте внутри Telegram, чтобы сохранить закладку.'); }
  });
  document.getElementById('bookmarkFavorite')?.addEventListener('click', async () => {
    try {
      await apiFetch(`/api/book/${bookId}/bookmark`, { method: 'POST', body: JSON.stringify({ status: 'favorite' }) });
      alert('Добавлено в любимое');
    } catch (error) { alert('Откройте внутри Telegram, чтобы сохранить закладку.'); }
  });
  document.getElementById('sendReview')?.addEventListener('click', async () => {
    const rating = document.getElementById('reviewRating')?.value || 5;
    const text = document.getElementById('reviewText')?.value || '';
    try {
      const data = await apiFetch(`/api/book/${bookId}/review`, { method: 'POST', body: JSON.stringify({ rating, text }) });
      renderReviews(data.reviews);
      document.getElementById('reviewText').value = '';
    } catch (error) { alert('Не удалось сохранить отзыв. Откройте внутри Telegram.'); }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  applyTheme(localStorage.getItem('voxTheme') || 'system');
});

initReader();
initAudioPage();
initBookPage();

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
