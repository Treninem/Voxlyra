let authorState = { dashboard: null, book: null, previewToken: null, duplicateMatches: [] };

const statusLabels = { draft: 'Черновик', review: 'На проверке', published: 'Опубликована', rejected: 'Нужны изменения' };
const writingLabels = { writing: 'Пишется', finished: 'Завершена', frozen: 'Заморожена' };

function formatStars(value) { return `${Number(value || 0).toLocaleString('ru-RU')} Stars`; }

function setAuthorLoading(show) {
  const loading = document.getElementById('authorLoading');
  if (loading) loading.hidden = !show;
}

function showAuthorError(message) {
  setAuthorLoading(false);
  document.getElementById('authorDashboard')?.setAttribute('hidden', '');
  const box = document.getElementById('authorError');
  const text = document.getElementById('authorErrorText');
  if (text) text.textContent = message || 'Откройте этот раздел из Telegram.';
  if (box) box.hidden = false;
}

function renderAuthorDashboard(data) {
  authorState.dashboard = data;
  setAuthorLoading(false);
  document.getElementById('authorError')?.setAttribute('hidden', '');
  const dashboard = document.getElementById('authorDashboard');
  if (dashboard) dashboard.hidden = false;
  document.getElementById('authorPenName').textContent = data.profile.pen_name || 'Автор';
  document.getElementById('authorSummary').textContent = data.profile.bio || 'Управляйте книгами и публикациями в своей студии.';

  const stats = data.stats || {};
  const finance = data.finance || {};
  const cards = [
    ['Книги', stats.books_total || 0],
    ['Опубликовано', stats.books_published || 0],
    ['На проверке', stats.books_review || 0],
    ['Главы', stats.chapters || 0],
    ['Доступно', formatStars(finance.available)],
    ['В удержании', formatStars(finance.held)],
  ];
  document.getElementById('authorStats').innerHTML = cards.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join('');
  renderAuthorBooks(data.books || []);
}

function releaseAuthorCoverUrl(image) {
  if (image?._authorCoverUrl) {
    URL.revokeObjectURL(image._authorCoverUrl);
    image._authorCoverUrl = '';
  }
}

async function loadAuthorCover(image) {
  const bookId = Number(image?.dataset.authorCoverId || 0);
  if (!bookId || !tgInitData()) return;
  try {
    const response = await fetch(`/api/author/book/${bookId}/cover`, {
      headers: { 'X-Telegram-Init-Data': tgInitData() },
      cache: 'no-store',
    });
    if (!response.ok) return;
    const blob = await response.blob();
    if (!blob.size) return;
    releaseAuthorCoverUrl(image);
    const objectUrl = URL.createObjectURL(blob);
    image._authorCoverUrl = objectUrl;
    image.src = objectUrl;
    image.hidden = false;
    image.closest('.author-cover-shell')?.querySelector('.author-book-letter')?.setAttribute('hidden', '');
  } catch (_) {}
}

function loadAuthorCovers(scope = document) {
  scope.querySelectorAll('img[data-author-cover-id]').forEach((image) => {
    if (!image.dataset.coverLoading) {
      image.dataset.coverLoading = '1';
      loadAuthorCover(image);
    }
  });
}

function renderAuthorBooks(books) {
  const box = document.getElementById('authorBooks');
  box?.querySelectorAll('img[data-author-cover-id]').forEach(releaseAuthorCoverUrl);
  if (!books.length) {
    box.innerHTML = '<article class="empty-card premium-empty"><div class="empty-icon">✦</div><h3>Книг пока нет</h3><p>Создайте первую книгу через раздел «Автору» в боте, затем редактируйте её здесь.</p></article>';
    return;
  }
  box.innerHTML = books.map((book) => `<button class="author-book-card" type="button" data-author-book-id="${book.id}">
    <div class="author-book-cover author-cover-shell">
      ${book.cover_path ? `<img class="author-book-cover-image" data-author-cover-id="${Number(book.id)}" alt="Обложка книги ${escapeHtml(book.title)}" hidden>` : ''}
      <div class="author-book-letter">${escapeHtml((book.title || 'В').slice(0,1))}</div>
    </div>
    <div><span>${escapeHtml(statusLabels[book.publication_status] || book.publication_status)}</span><h3>${escapeHtml(book.title)}</h3><p>${Number(book.chapters_count || 0)} глав · ${Number(book.audio_count || 0)} аудио</p></div>
    <b>›</b>
  </button>`).join('');
  loadAuthorCovers(box);
}

function fillBookEditor(data) {
  authorState.book = data;
  const book = data.book;
  const editor = document.getElementById('authorBookEditor');
  editor.hidden = false;
  document.getElementById('editorBookTitle').textContent = book.title;
  document.getElementById('editorBookStatus').textContent = `${statusLabels[book.publication_status] || book.publication_status} · ${writingLabels[book.writing_status] || book.writing_status}`;
  const editorCover = document.getElementById('editorBookCoverImage');
  const editorLetter = document.getElementById('editorBookCoverLetter');
  if (editorCover && editorLetter) {
    releaseAuthorCoverUrl(editorCover);
    editorCover.hidden = true;
    editorCover.removeAttribute('src');
    delete editorCover.dataset.coverLoading;
    editorLetter.hidden = false;
    editorLetter.textContent = (book.title || 'В').slice(0, 1);
    if (book.cover_path) {
      editorCover.dataset.authorCoverId = String(book.id);
      editorCover.alt = `Обложка книги ${book.title || ''}`;
      loadAuthorCovers(document.getElementById('editorBookCover'));
    } else {
      delete editorCover.dataset.authorCoverId;
    }
  }
  document.getElementById('bookTitleInput').value = book.title || '';
  document.getElementById('bookDescriptionInput').value = book.description || '';
  document.getElementById('bookAgeInput').value = book.age_limit || '16+';
  document.getElementById('bookWritingInput').value = book.writing_status || 'writing';
  document.getElementById('bookPricingInput').value = book.pricing_type || 'free';
  document.getElementById('bookPriceInput').value = Number(book.price_stars || 0);
  document.getElementById('bookDownloadInput').checked = Boolean(Number(book.allow_download || 0));
  renderAuthorChapters(data.chapters || []);
  resetChapterForm();
  document.getElementById('importPreview').hidden = true;
  authorState.previewToken = null;
  editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderAuthorChapters(chapters) {
  const box = document.getElementById('authorChapters');
  if (!chapters.length) {
    box.innerHTML = '<article class="empty-card"><h3>Глав пока нет</h3><p>Добавьте главу вручную или импортируйте файл книги.</p></article>';
    return;
  }
  box.innerHTML = chapters.map((chapter) => `<button class="author-chapter-row" type="button" data-edit-chapter="${chapter.id}">
    <div><span>Глава ${chapter.number}</span><strong>${escapeHtml(chapter.title)}</strong><small>${chapter.is_free ? 'Бесплатно' : formatStars(chapter.price_stars)} · ${escapeHtml(statusLabels[chapter.status] || chapter.status)}</small></div><b>Изменить</b>
  </button>`).join('');
}

async function loadAuthorDashboard() {
  if (!tgInitData()) { showAuthorError('Откройте Mini App через бота, чтобы войти в кабинет автора.'); return; }
  try {
    renderAuthorDashboard(await apiFetch('/api/author/dashboard'));
    const params = new URLSearchParams(window.location.search);
    const requestedBook = Number(params.get('book_id') || 0);
    if (requestedBook > 0) {
      await openAuthorBook(requestedBook);
      if (params.get('upload') === '1') {
        const input = document.getElementById('bookFileInput');
        input?.closest('.import-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => input?.focus(), 350);
        notify('Выберите файл, затем нажмите «Загрузить и проверить»');
      }
    }
  } catch (error) { showAuthorError(error.message); }
}

async function openAuthorBook(bookId) {
  try { fillBookEditor(await apiFetch(`/api/author/book/${bookId}`)); }
  catch (error) { notify(error.message || 'Не удалось открыть книгу'); }
}

async function refreshAuthorDashboard(reopenBook = true) {
  const currentId = reopenBook ? authorState.book?.book?.id : null;
  const data = await apiFetch('/api/author/dashboard');
  renderAuthorDashboard(data);
  if (currentId) await openAuthorBook(currentId);
}

function resetChapterForm() {
  const form = document.getElementById('chapterForm');
  form.hidden = true;
  document.getElementById('chapterIdInput').value = '';
  document.getElementById('chapterTitleInput').value = '';
  document.getElementById('chapterTextInput').value = '';
  document.getElementById('chapterPriceInput').value = '0';
  document.getElementById('deleteChapterButton').hidden = true;
  document.getElementById('deleteChapterButton').dataset.confirm = '';
}

function editChapter(chapterId) {
  const chapter = (authorState.book?.chapters || []).find((item) => Number(item.id) === Number(chapterId));
  if (!chapter) return;
  const form = document.getElementById('chapterForm');
  form.hidden = false;
  document.getElementById('chapterIdInput').value = chapter.id;
  document.getElementById('chapterTitleInput').value = chapter.title || '';
  document.getElementById('chapterTextInput').value = chapter.text || '';
  document.getElementById('chapterPriceInput').value = chapter.is_free ? 0 : Number(chapter.price_stars || 0);
  document.getElementById('deleteChapterButton').hidden = false;
  form.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function setUploadProgress(percent, label = '') {
  const box = document.getElementById('uploadProgress');
  box.hidden = false;
  box.querySelector('i').style.width = `${Math.max(0, Math.min(100, percent))}%`;
  box.querySelector('span').textContent = label || `${Math.round(percent)}%`;
}

async function uploadBookFile() {
  const file = document.getElementById('bookFileInput').files?.[0];
  const bookId = authorState.book?.book?.id;
  if (!file || !bookId) { notify('Сначала выберите файл'); return; }
  const button = document.getElementById('startBookUpload');
  button.disabled = true;
  try {
    const start = await apiFetch(`/api/author/book/${bookId}/upload/start`, { method: 'POST', body: JSON.stringify({ filename: file.name, size: file.size }) });
    const chunkSize = Number(start.chunk_size || 6 * 1024 * 1024);
    const totalChunks = Math.ceil(file.size / chunkSize);
    for (let index = 0; index < totalChunks; index += 1) {
      const form = new FormData();
      form.append('index', String(index));
      form.append('total_chunks', String(totalChunks));
      form.append('chunk', file.slice(index * chunkSize, Math.min(file.size, (index + 1) * chunkSize)), `${file.name}.part`);
      await apiFetch(`/api/author/book/${bookId}/upload/${start.upload_id}/chunk`, { method: 'POST', body: form });
      setUploadProgress(((index + 1) / totalChunks) * 82, `Загружено ${index + 1} из ${totalChunks}`);
    }
    setUploadProgress(88, 'Проверяем главы…');
    const result = await apiFetch(`/api/author/book/${bookId}/upload/${start.upload_id}/finish`, { method: 'POST', body: JSON.stringify({ total_chunks: totalChunks }) });
    authorState.previewToken = result.preview_token;
    renderImportPreview(result);
    setUploadProgress(100, 'Файл проверен');
  } catch (error) {
    notify(error.message || 'Не удалось загрузить файл');
    setUploadProgress(0, 'Загрузка не завершена');
  } finally { button.disabled = false; }
}

function renderImportPreview(result) {
  const report = result.report || {};
  const box = document.getElementById('importPreview');
  const problems = (report.problems || []).length ? `<ul>${report.problems.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '<p class="success-text">Явных проблем не найдено.</p>';
  const preview = (report.preview || []).map((item) => `<li><b>${item.number}. ${escapeHtml(item.title)}</b><span>${Number(item.chars || 0).toLocaleString('ru-RU')} знаков</span></li>`).join('');
  authorState.duplicateMatches = Array.isArray(result.duplicates) ? result.duplicates : [];
  const duplicateBlock = authorState.duplicateMatches.length ? `<div class="import-warning"><h4>Похоже, такая книга уже есть</h4><p>Проверьте совпадения перед сохранением:</p><ul>${authorState.duplicateMatches.slice(0, 6).map((item) => `<li><b>${escapeHtml(item.title || 'Книга')}</b> — ${escapeHtml(item.reason || 'похожее название')}</li>`).join('')}</ul><label class="switch-row"><input id="allowDuplicateImport" type="checkbox"><span>Это другая книга или новая редакция</span></label></div>` : '';
  box.innerHTML = `<h3>Предпросмотр импорта</h3><p><b>${escapeHtml(result.filename)}</b></p><div class="import-numbers"><span>${Number(report.chapters_count || 0)} глав</span><span>${Number(report.total_chars || 0).toLocaleString('ru-RU')} знаков</span></div>${problems}${duplicateBlock}<ol>${preview}</ol><button class="button-link" id="confirmBookImport" type="button">Сохранить главы</button>`;
  box.hidden = false;
}

async function confirmBookImport() {
  const bookId = authorState.book?.book?.id;
  if (!bookId || !authorState.previewToken) return;
  try {
    const allowDuplicate = Boolean(document.getElementById('allowDuplicateImport')?.checked);
    if (authorState.duplicateMatches.length && !allowDuplicate) {
      notify('Подтвердите, что это другая книга или новая редакция');
      document.getElementById('allowDuplicateImport')?.focus();
      return;
    }
    const result = await apiFetch(`/api/author/book/${bookId}/import-confirm`, { method: 'POST', body: JSON.stringify({
      preview_token: authorState.previewToken,
      first_free: Number(document.getElementById('importFirstFree').value || 0),
      default_price_stars: Number(document.getElementById('importPrice').value || 0),
      allow_duplicate: allowDuplicate,
    }) });
    const workflow = result.workflow || {};
    if (workflow.status === 'published') notify(`Сохранено глав: ${result.saved}. Книга опубликована в каталоге.`);
    else if (workflow.status === 'review') notify(`Сохранено глав: ${result.saved}. Книга отправлена на проверку.`);
    else notify(`Сохранено глав: ${result.saved}`);
    authorState.previewToken = null;
    authorState.duplicateMatches = [];
    await openAuthorBook(bookId);
  } catch (error) { notify(error.message || 'Не удалось сохранить главы'); }
}

function armDelete(button, message) {
  if (button.dataset.confirm === 'yes') return true;
  button.dataset.confirm = 'yes';
  button.textContent = message;
  setTimeout(() => { button.dataset.confirm = ''; button.textContent = button.id === 'deleteBookButton' ? 'Удалить книгу' : 'Удалить главу'; }, 4500);
  return false;
}

function bindAuthorEvents() {
  document.addEventListener('click', async (event) => {
    const target = event.target.closest('button');
    if (!target || !document.getElementById('authorStudio')) return;
    if (target.dataset.authorBookId) { await openAuthorBook(target.dataset.authorBookId); return; }
    if (target.id === 'closeBookEditor') { document.getElementById('authorBookEditor').hidden = true; authorState.book = null; return; }
    if (target.id === 'newChapterButton') { resetChapterForm(); document.getElementById('chapterForm').hidden = false; return; }
    if (target.dataset.editChapter) { editChapter(target.dataset.editChapter); return; }
    if (target.id === 'cancelChapterEdit') { resetChapterForm(); return; }
    if (target.id === 'startBookUpload') { await uploadBookFile(); return; }
    if (target.id === 'confirmBookImport') { await confirmBookImport(); return; }
    if (target.id === 'submitBookReview') {
      try { await apiFetch(`/api/author/book/${authorState.book.book.id}/submit`, { method: 'POST' }); notify('Книга отправлена на проверку'); await refreshAuthorDashboard(); }
      catch (error) { notify(error.message); }
      return;
    }
    if (target.id === 'deleteBookButton') {
      if (!armDelete(target, 'Нажмите ещё раз — удалить книгу')) return;
      try { await apiFetch(`/api/author/book/${authorState.book.book.id}`, { method: 'DELETE' }); notify('Книга удалена'); document.getElementById('authorBookEditor').hidden = true; authorState.book = null; await refreshAuthorDashboard(false); }
      catch (error) { notify(error.message); }
      return;
    }
    if (target.id === 'deleteChapterButton') {
      if (!armDelete(target, 'Нажмите ещё раз — удалить главу')) return;
      const chapterId = document.getElementById('chapterIdInput').value;
      try { await apiFetch(`/api/author/chapter/${chapterId}`, { method: 'DELETE' }); notify('Глава удалена'); await openAuthorBook(authorState.book.book.id); }
      catch (error) { notify(error.message); }
    }
  });

  document.getElementById('bookFileInput')?.addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    document.getElementById('bookFileName').textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} МБ` : 'Файл не выбран';
  });

  document.getElementById('bookEditForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const bookId = authorState.book?.book?.id;
    if (!bookId) return;
    try {
      await apiFetch(`/api/author/book/${bookId}`, { method: 'PATCH', body: JSON.stringify({
        title: document.getElementById('bookTitleInput').value,
        description: document.getElementById('bookDescriptionInput').value,
        age_limit: document.getElementById('bookAgeInput').value,
        writing_status: document.getElementById('bookWritingInput').value,
        pricing_type: document.getElementById('bookPricingInput').value,
        price_stars: Number(document.getElementById('bookPriceInput').value || 0),
        allow_download: document.getElementById('bookDownloadInput').checked,
      }) });
      notify('Книга сохранена');
      await refreshAuthorDashboard();
    } catch (error) { notify(error.message || 'Не удалось сохранить книгу'); }
  });

  document.getElementById('chapterForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const bookId = authorState.book?.book?.id;
    const chapterId = document.getElementById('chapterIdInput').value;
    const payload = {
      title: document.getElementById('chapterTitleInput').value,
      text: document.getElementById('chapterTextInput').value,
      price_stars: Number(document.getElementById('chapterPriceInput').value || 0),
    };
    try {
      if (chapterId) await apiFetch(`/api/author/chapter/${chapterId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      else await apiFetch(`/api/author/book/${bookId}/chapters`, { method: 'POST', body: JSON.stringify(payload) });
      notify(chapterId ? 'Глава обновлена' : 'Глава добавлена');
      await openAuthorBook(bookId);
    } catch (error) { notify(error.message || 'Не удалось сохранить главу'); }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('authorStudio')) return;
  bindAuthorEvents();
  loadAuthorDashboard();
});
