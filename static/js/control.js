(() => {
  const state = { permissions: new Set(), role: '', dashboard: null, active: '', accessUser: null, accessBooks: [], libraryBatchId: null, libraryRefreshTimer: null, bookQuery: '', moderationBookId: null };
  const $ = (id) => document.getElementById(id);
  const can = (code) => state.role === 'owner' || state.permissions.has(code);
  const esc = (value) => escapeHtml(value ?? '');
  const dateText = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '';
  const rubText = (minor) => `${(Number(minor || 0) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;

  const actionButton = (label, action, kind = '') =>
    `<button class="control-action ${kind}" type="button" data-action="${esc(action)}">${esc(label)}</button>`;
  const actionLink = (label, href, kind = '') =>
    `<a class="control-action ${kind}" href="${esc(href)}">${esc(label)}</a>`;

  async function downloadLibraryBatchReport(batchId, format) {
    const response = await fetch(`/api/control/library-import/batch/${Number(batchId)}/report?format=${encodeURIComponent(format)}`, {
      headers: { 'X-Telegram-Init-Data': tgInitData() },
      cache: 'no-store',
    });
    if (!response.ok) {
      let message = 'Не удалось скачать отчёт.';
      try { const data = await response.json(); message = data.detail || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const disposition = String(response.headers.get('Content-Disposition') || '');
    const match = disposition.match(/filename="?([^";]+)"?/i);
    const filename = match?.[1] || `voxlyra_import_batch_${Number(batchId)}.${format}`;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function showError(message) {
    $('controlLoading').hidden = true;
    $('controlDashboard').hidden = true;
    $('controlError').hidden = false;
    $('controlErrorText').textContent = message || 'Откройте панель из меню бота.';
  }

  function statCard(label, value, hint = '') {
    return `<article class="control-stat-card"><span>${esc(label)}</span><b>${esc(value)}</b>${hint ? `<small>${esc(hint)}</small>` : ''}</article>`;
  }

  function sectionButton(code, title, subtitle, count = 0) {
    return `<button class="control-section-card" type="button" data-section="${esc(code)}">
      <div><strong>${esc(title)}</strong><span>${esc(subtitle)}</span></div>
      <b>${Number(count || 0)}</b>
    </button>`;
  }

  function renderDashboard(data) {
    state.dashboard = data;
    state.role = data.role;
    state.permissions = new Set(data.permissions || []);
    $('controlRole').textContent = data.role === 'owner' ? 'Панель владельца' : 'Панель модератора';
    $('controlGreeting').textContent = `${data.name || 'Пользователь'}, здесь собраны только доступные вам действия.`;

    const q = data.queues || {};
    const today = data.today || {};
    const finance = data.finance || {};
    const stats = [];
    if (data.role === 'owner' || can('stats')) {
      stats.push(statCard('Новые читатели', today.new_users || 0, 'сегодня'));
      stats.push(statCard('Покупки', today.purchases || 0, `${today.stars || 0} Stars`));
      stats.push(statCard('Книги на проверке', q.books_review || 0));
      stats.push(statCard('Жалобы', q.complaints_new || 0));
    }
    if (data.finance) {
      stats.push(statCard('Принято от Telegram', finance.paid_gross || 0, 'Stars'));
      stats.push(statCard('Продажи контента', finance.content_sales_stars || 0, 'Stars'));
      stats.push(statCard('Доля платформы', finance.platform_commission || 0, 'Stars'));
      stats.push(statCard('Бонусный фонд', finance.bonus_pool_stars || 0, 'Stars'));
      stats.push(statCard('На балансах читателей', finance.wallet_liability_stars || 0, 'Stars'));
      stats.push(statCard('Удерживается авторам', finance.held_authors || 0, 'Stars'));
      stats.push(statCard('К выплате', finance.available_authors || 0, 'Stars'));
    }
    if (data.role === 'owner' && data.premium) {
      stats.push(statCard('Premium', data.premium.active_users || 0, 'активных'));
      stats.push(statCard('Premium-оборот', data.premium.gross_stars || 0, 'Stars'));
    }
    $('controlStats').innerHTML = stats.join('');

    const sections = [];
    if (can('mod_books')) {
      sections.push(sectionButton('books', data.role === 'owner' ? 'Управление книгами' : 'Книги', data.role === 'owner' ? 'Поиск, проверка и публикация в канал' : 'Проверка и публикация', q.books_review));
      sections.push(sectionButton('graphic_pages', 'Страницы комиксов', 'Жалобы и постраничная проверка', q.graphic_page_reports));
    }
    if (can('mod_comments')) sections.push(sectionButton('comments', 'Отзывы и комментарии', 'Скрытие нарушений', (q.comments || 0) + (q.reviews || 0) + (q.graphic_page_comments || 0)));
    if (can('complaints')) sections.push(sectionButton('complaints', 'Жалобы', 'Рассмотрение обращений', q.complaints_new));
    if (can('refunds')) sections.push(sectionButton('refunds', 'Возвраты', 'Проверка покупок', q.refunds_new));
    if (can('payouts')) {
      sections.push(sectionButton('payouts', 'Выплаты авторам', 'Stars и точная сумма в рублях', (q.payouts_new || 0) + (q.payouts_approved || 0)));
    }
    if (can('grant_access')) sections.push(sectionButton('access', 'Выдать доступ', 'Главы и Premium по ID или username', 0));
    if (can('library_bulk_import') || can('library_import_manage')) {
      sections.push(sectionButton('library_import', 'Импорт библиотеки', 'ZIP, замена версий и автомодерация', 0));
    }
    if (data.role === 'owner') {
      sections.push(sectionButton('tts', 'Озвучивание', 'Движки, голоса и очередь', 0));
      sections.push(sectionButton('payments', 'Stars и курсы', 'Оплата, расчёты автора и защита', 0));
      sections.push(sectionButton('premium', 'Premium', 'Цена, включение и статистика подписки', data.premium?.active_users || 0));
      sections.push(sectionButton('achievements', 'Награды и уровни', 'Очки, особые награды и сезонные марафоны', 0));
      sections.push(sectionButton('catalog_promotions', 'Топ каталога', 'Честная ротация: ты бесплатно, авторы за Stars', data.catalog_promotions_active || 0));
    }
    $('controlSections').innerHTML = sections.length ? sections.join('') : '<article class="empty-card"><h3>Нет доступных действий</h3><p>Права можно изменить в меню владельца.</p></article>';

    $('controlLoading').hidden = true;
    $('controlError').hidden = true;
    $('controlDashboard').hidden = false;
  }

  function openWorkspace(title, subtitle, eyebrow = 'Очередь') {
    $('workspaceTitle').textContent = title;
    $('workspaceSubtitle').textContent = subtitle;
    $('workspaceEyebrow').textContent = eyebrow;
    $('controlWorkspace').hidden = false;
    $('controlWorkspace').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function emptyList(title, text) {
    $('workspaceList').innerHTML = `<article class="empty-card premium-empty"><div class="empty-icon">✓</div><h3>${esc(title)}</h3><p>${esc(text)}</p></article>`;
  }

  async function loadBooks(query = state.bookQuery) {
    const ownerMode = state.role === 'owner';
    state.bookQuery = String(query || '').trim();
    openWorkspace(ownerMode ? 'Управление книгами' : 'Книги на проверке', ownerMode ? 'Ищите по названию, ID, описанию или автору и управляйте публикацией.' : 'Публикуйте готовые книги или возвращайте автору на доработку.');
    if (ownerMode) {
      $('workspaceTabs').innerHTML = `<div class="control-book-search"><input id="controlBookSearch" type="search" value="${esc(state.bookQuery)}" placeholder="Название, ID или автор"><button type="button" id="controlBookSearchButton">Найти</button></div>`;
      const input = $('controlBookSearch');
      const run = () => loadBooks(String(input?.value || '')).catch(handleError);
      $('controlBookSearchButton')?.addEventListener('click', run);
      input?.addEventListener('keydown', (event) => { if (event.key === 'Enter') run(); });
    } else {
      $('workspaceTabs').innerHTML = '';
    }
    const suffix = ownerMode && state.bookQuery ? `?q=${encodeURIComponent(state.bookQuery)}` : '';
    const data = await apiFetch(`/api/control/books${suffix}`);
    const items = data.items || [];
    if (!items.length) return emptyList(ownerMode ? 'Книги не найдены' : 'Очередь пуста', ownerMode ? 'Проверьте имя автора или попробуйте часть названия.' : 'Новых книг на проверке нет.');
    $('workspaceList').innerHTML = items.map((item) => {
      const status = String(item.publication_status || 'draft');
      const preview = item.first_graphic_chapter_id
        ? actionLink('Открыть страницы', `/comic/${Number(item.first_graphic_chapter_id)}?moderation=1`)
        : item.first_chapter_id
          ? actionLink('Открыть текст', `/reader/${Number(item.first_chapter_id)}?moderation=1`)
          : status === 'published' ? actionLink('Открыть книгу', `/book/${Number(item.id)}`) : '';
      let actions = preview;
      if (status === 'review') actions += `${actionButton('Проверка и замечания', `book:details:${item.id}`)}${actionButton('Опубликовать', `book:publish:${item.id}`, 'approve')}${actionButton('На доработку', `book:reject:${item.id}`, 'danger')}`;
      if (ownerMode && status === 'published') actions += actionButton('Выложить в канал', `book:repost:${item.id}`, 'approve');
      if (ownerMode && status === 'blocked') actions += actionButton('Перевести в скрытые', `book:hide:${item.id}`);
      else if (ownerMode) actions += actionButton('Заблокировать', `book:block:${item.id}`, 'danger');
      return `<article class="control-item" data-id="${Number(item.id)}">
        <div class="control-item-main"><span>Книга #${Number(item.id)} · ${esc(status)}</span><h3>${esc(item.title)}</h3><p>${esc(item.pen_name || item.source_author_name || 'Автор не указан')} · ${esc(item.age_limit || '')}</p><small>${esc((item.description || '').slice(0, 240))}</small></div>
        <div class="control-actions">${actions}</div>
      </article>`;
    }).join('');
  }

  function moderationLocation(item) {
    if (String(item.source_type || '') === 'metadata') {
      const labels = { title: 'Название', description: 'Описание', age_limit: 'Возрастной рейтинг', cover: 'Обложка', content_type: 'Тип произведения', license: 'Права и источник', structure: 'Структура произведения' };
      return labels[String(item.field_name || '')] || 'Метаданные';
    }
    if (item.chapter_number !== null && item.chapter_number !== undefined) {
      return `Глава ${Number(item.chapter_number)}${item.chapter_title ? ` — ${esc(item.chapter_title)}` : ''}`;
    }
    return String(item.source_type || '') === 'graphic' ? 'Графическая глава' : 'Произведение';
  }

  async function rejectBookFromModeration(bookId) {
    const checked = Array.from(document.querySelectorAll('[data-moderation-finding]:checked')).map((input) => Number(input.value)).filter(Boolean);
    const reason = window.prompt('Что автору нужно исправить? Выбранные точные места будут добавлены к сообщению автоматически.');
    if (reason === null) return;
    if (reason.trim().length < 8) { notify('Причина слишком короткая'); return; }
    if (!checked.length && !window.confirm('Точные замечания не выбраны. Возврат потребует ручного подтверждения после исправлений. Продолжить?')) return;
    await apiFetch(`/api/control/book/${Number(bookId)}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason: reason.trim(), finding_ids: checked }),
    });
    notify('Книга возвращена автору с точными замечаниями');
    await loadBooks(state.bookQuery);
    await refreshDashboard();
  }

  async function openBookModeration(bookId) {
    state.moderationBookId = Number(bookId);
    const data = await apiFetch(`/api/control/book/${Number(bookId)}/moderation`);
    const book = data.book || {};
    const findings = Array.isArray(data.findings) ? data.findings : [];
    const queue = data.queue || {};
    openWorkspace(`Проверка: ${book.title || `книга #${Number(bookId)}`}`, 'Выберите конкретные замечания, которые автор должен исправить. Номера строк и фрагменты попадут в уведомление.', 'Точная модерация');
    $('workspaceTabs').innerHTML = '<button type="button" id="moderationBackToBooks">← К книгам</button><button type="button" id="moderationRefreshBook">Обновить</button>';
    const summary = data.changes
      ? `<p><b>Изменено после возврата:</b> метаданные — ${Number(data.changes.metadata || 0)}, текстовые главы — ${Number(data.changes.text_chapters || 0)}, графические главы — ${Number(data.changes.graphic_chapters || 0)}, удалено — ${Number(data.changes.deleted || 0)}.</p>`
      : '';
    const reason = String(queue.reasons || data.revision?.reason || '').trim();
    const findingCards = findings.length ? findings.map((item) => {
      const chapterLink = item.chapter_id ? `<a class="button-link secondary compact-button" href="/reader/${Number(item.chapter_id)}?moderation=1">Открыть главу</a>` : '';
      const fragment = String(item.matched_text || item.context || '').trim();
      return `<article class="control-item ${String(item.severity || '') === 'block' ? 'danger-card' : ''}">
        <label class="control-item-main moderation-finding-select">
          <span>${esc(String(item.severity || 'review') === 'block' ? 'Блокирующее замечание' : 'Нужна проверка')} · ${moderationLocation(item)}</span>
          <h3><input type="checkbox" data-moderation-finding value="${Number(item.id)}" checked> ${esc(item.reason || 'Требуется исправление')}</h3>
          <p>Строка ${Number(item.line_number || 1)} · позиция ${Number(item.character_offset || 0)}</p>
          <small>${esc(fragment.slice(0, 900) || 'Точный фрагмент не задан — проверьте указанную область.')}</small>
        </label><div class="control-actions">${chapterLink}</div>
      </article>`;
    }).join('') : '<article class="empty-card"><h3>Автоматических совпадений нет</h3><p>Модератор может оставить ручную инструкцию. После исправления потребуется повторное ручное подтверждение.</p></article>';
    $('workspaceList').innerHTML = `<section class="panel-card">
      <div class="section-title slim"><span class="eyebrow">Книга #${Number(book.id || bookId)}</span><h2>${esc(book.title || '')}</h2><p>${esc(book.pen_name || book.source_author_name || 'Автор не указан')} · ${esc(book.age_limit || '')}</p></div>
      ${reason ? `<div class="package-rule-note"><b>Текущая причина очереди:</b><br>${esc(reason)}</div>` : ''}${summary}
      <div class="inline-actions"><button type="button" id="moderationSelectAll">Выбрать всё</button><button type="button" id="moderationSelectNone" class="secondary">Снять всё</button></div>
    </section>${findingCards}<section class="panel-card"><div class="form-actions"><button type="button" id="moderationPublishBook" class="approve">Опубликовать</button><button type="button" id="moderationRejectBook" class="danger">Вернуть выбранное на доработку</button></div></section>`;
    $('moderationBackToBooks')?.addEventListener('click', () => loadBooks(state.bookQuery).catch(handleError));
    $('moderationRefreshBook')?.addEventListener('click', () => openBookModeration(bookId).catch(handleError));
    $('moderationSelectAll')?.addEventListener('click', () => document.querySelectorAll('[data-moderation-finding]').forEach((input) => { input.checked = true; }));
    $('moderationSelectNone')?.addEventListener('click', () => document.querySelectorAll('[data-moderation-finding]').forEach((input) => { input.checked = false; }));
    $('moderationRejectBook')?.addEventListener('click', () => rejectBookFromModeration(bookId).catch(handleError));
    $('moderationPublishBook')?.addEventListener('click', async () => {
      if (!window.confirm('Опубликовать книгу после ручной проверки?')) return;
      await apiFetch(`/api/control/book/${Number(bookId)}/publish`, { method: 'POST' });
      notify('Книга опубликована');
      await loadBooks(state.bookQuery);
      await refreshDashboard();
    });
  }

  const libraryJobStatusLabels = {
    queued: 'Ожидает', processing: 'Импортируется', cancelling: 'Останавливается', completed: 'Завершён',
    failed: 'Ошибка', cancelled: 'Остановлен',
  };
  const libraryBatchStatusLabels = {
    processing: 'Обрабатывается', completed: 'Готов', published: 'Опубликован',
    failed: 'Ошибка', rolled_back: 'Откатан',
  };
  const libraryPhaseLabels = {
    0: 'Подготовка', 1: 'Проверка структуры', 2: 'Импорт книг', 3: 'Завершение',
  };

  function libraryJobCard(item, data) {
    const status = String(item.status || '');
    const percent = Math.max(0, Math.min(100, Number(item.progress_percent || 0)));
    const ownsJob = Boolean(data.can_manage) || Number(item.actor_user_id) === Number(data.app_user_id);
    const canCancel = Boolean(item.can_cancel) && ownsJob;
    const canRetry = Boolean(item.can_retry) && ownsJob;
    const currentItem = String(item.current_title || item.current_folder || '').trim();
    const progress = ['processing', 'cancelling'].includes(status)
      ? `<progress max="100" value="${percent}">${percent}%</progress><small>${status === 'cancelling' ? 'Завершается текущая операция' : esc(libraryPhaseLabels[Number(item.phase || 0)] || 'Импорт')} · ${Number(item.processed || 0)} из ${Number(item.total || 0) || '…'} · ${percent}%${currentItem ? ` · сейчас: ${esc(currentItem)}` : ''}</small>`
      : status === 'queued'
        ? `<small>Позиция в очереди: ${Number(item.queue_position || 1)}</small>`
        : `<small>${esc(dateText(item.completed_at || item.heartbeat_at || item.created_at))}</small>`;
    const error = item.last_error && !['cancelled', 'cancelling'].includes(status)
      ? `<p class="danger-text">${esc(String(item.last_error).slice(0, 700))}</p>` : '';
    const stopInfo = ['cancelled', 'cancelling'].includes(status) && item.last_error
      ? `<p>${esc(String(item.last_error).slice(0, 700))}</p>` : '';
    const restartInfo = Number(item.restart_count || 0)
      ? `<small>Автовосстановлений после перезапуска: ${Number(item.restart_count || 0)}</small>` : '';
    const queueMode = String((data.queue_control || {}).mode || 'running');
    const worker = data.queue_worker || {};
    const workerOffline = worker.alive === false || ['failed', 'error_retrying'].includes(String(worker.status || ''));
    const queueWarning = status === 'queued' && queueMode !== 'running'
      ? '<p class="danger-text">Очередь приостановлена. Нажмите «Запустить очередь» — ZIP повторно загружать не нужно.</p>'
      : status === 'queued' && workerOffline
        ? '<p class="danger-text">Обработчик очереди перезапускается. Нажмите «Запустить очередь», если ожидание продолжается.</p>'
        : '';
    const retryInfo = ['failed', 'cancelled'].includes(status)
      ? (canRetry
        ? `<small>ZIP сохранён до ${esc(dateText(item.archive_expires_at))} · повторов: ${Number(item.retry_count || 0)}</small>`
        : '<small>Исходный ZIP для повтора уже недоступен. Загрузите архив заново.</small>')
      : (Number(item.retry_count || 0) ? `<small>Повторных запусков: ${Number(item.retry_count || 0)}</small>` : '');
    return `<article class="control-item">
      <div class="control-item-main">
        <span>Задание #${Number(item.id)} · ${esc(libraryJobStatusLabels[status] || status)}</span>
        <h3>${esc(item.archive_name || 'Архив библиотеки')}</h3>
        <p>Добавлено: ${Number(item.added || 0)} · заменено: ${Number(item.replaced_count || 0)} · перенумеровано: ${Number(item.renumbered_count || 0)} · ошибок: ${Number(item.error_count || 0)}</p>
        ${progress}${queueWarning}${error}${stopInfo}${retryInfo}${restartInfo}
      </div>
      <div class="control-actions">${status === 'queued' && Boolean(data.can_control_queue) ? '<button type="button" class="control-action approve" data-library-queue-kick>Запустить очередь</button>' : ''}${item.batch_id ? actionButton('Открыть пакет', `librarybatch:details:${Number(item.batch_id)}`) : ''}${canRetry ? actionButton('Повторить', `libraryjob:retry:${Number(item.id)}`) : ''}${canCancel ? actionButton(status === 'processing' ? 'Остановить' : 'Отменить', `libraryjob:cancel:${Number(item.id)}`, 'danger') : ''}</div>
    </article>`;
  }

  function libraryBatchCard(item, canManage) {
    const status = String(item.status || '');
    return `<article class="control-item">
      <div class="control-item-main">
        <span>Пакет #${Number(item.id)} · ${esc(libraryBatchStatusLabels[status] || status)}</span>
        <h3>${esc(item.archive_name || 'Архив библиотеки')}</h3>
        <p>Добавлено: ${Number(item.imported_count || 0)} · заменено: ${Number(item.replaced_count || 0)} · перенумеровано: ${Number(item.renumbered_count || 0)}</p>
        <small>Дублей: ${Number(item.duplicate_count || 0)} · ошибок: ${Number(item.error_count || 0)} · ${esc(dateText(item.completed_at || item.created_at))}</small>
      </div>
      <div class="control-actions">${actionButton(canManage ? 'Проверить пакет' : 'Подробнее', `librarybatch:details:${Number(item.id)}`)}</div>
    </article>`;
  }

  function renderLibraryEvidence(items) {
    if (!Array.isArray(items) || !items.length) return '';
    return `<div class="library-evidence"><strong>Где именно найдено:</strong>${items.map((item) => `<div><span>Глава ${Number(item.chapter || 0)} · ${esc(item.label || 'Фрагмент')}</span>${item.excerpt ? `<code>${esc(item.excerpt)}</code>` : ''}</div>`).join('')}</div>`;
  }

  async function openLibraryBatch(batchId) {
    if (state.libraryRefreshTimer) {
      window.clearTimeout(state.libraryRefreshTimer);
      state.libraryRefreshTimer = null;
    }
    state.libraryBatchId = Number(batchId);
    openWorkspace(`Пакет импорта #${Number(batchId)}`, 'Проверка, причины блокировок, дубли и безопасная публикация.', 'Библиотека');
    $('workspaceTabs').innerHTML = `<button type="button" data-action="librarybatch:back:${Number(batchId)}">← К истории</button><button type="button" data-action="librarybatch:details:${Number(batchId)}">Обновить</button>`;
    $('workspaceList').innerHTML = '<article class="empty-card"><h3>Проверяем пакет…</h3><p>Читаем ошибки, дубли и состояние книг.</p></article>';
    const data = await apiFetch(`/api/control/library-import/batch/${Number(batchId)}`);
    const batch = data.batch || {};
    const audit = data.audit || {};
    const statuses = data.book_statuses || {};
    const errors = Array.isArray(data.errors) ? data.errors : [];
    const errorsTotal = Number(data.errors_total || errors.length);
    const duplicates = Array.isArray(data.duplicates) ? data.duplicates : [];
    const pendingDuplicates = duplicates.filter((item) => String(item.status || '') === 'pending');
    const blocked = Array.isArray(audit.blocked_items) ? audit.blocked_items : [];
    const checked = Array.isArray(audit.checked_items) ? audit.checked_items : [];
    const warnings = checked.filter((item) => Array.isArray(item.warnings) && item.warnings.length);

    const errorCards = errors.length ? errors.map((item) => `<article class="control-item danger-card">
      <div class="control-item-main"><span>${esc(item.folder || 'Папка не определена')}</span><h3>${esc(item.title || 'Без названия')}</h3><p>${esc((item.reasons || []).join('; ') || 'Причина не записана')}</p></div>
    </article>`).join('') : '<article class="empty-card"><h3>Ошибок структуры нет</h3><p>Все найденные папки были обработаны.</p></article>';

    const blockedCards = blocked.length ? blocked.map((item) => `<article class="control-item danger-card">
      <div class="control-item-main"><span>Книга #${Number(item.book_id)} · качество ${Number(item.quality_score || 0)}%</span><h3>${esc(item.title || 'Без названия')}</h3><p>${esc((item.reasons || []).join('; '))}</p>${Array.isArray(item.warnings) && item.warnings.length ? `<small>Предупреждения: ${esc(item.warnings.join('; '))}</small>` : ''}${renderLibraryEvidence(item.evidence)}</div>
    </article>`).join('') : '<article class="empty-card premium-empty"><div class="empty-icon">✓</div><h3>Блокирующих проблем нет</h3><p>Все оставшиеся черновики можно публиковать.</p></article>';

    const warningCards = warnings.length ? warnings.map((item) => `<article class="control-item">
      <div class="control-item-main"><span>Книга #${Number(item.book_id)} · качество ${Number(item.quality_score || 0)}%</span><h3>${esc(item.title || 'Без названия')}</h3><p>${esc(item.warnings.join('; '))}</p>${renderLibraryEvidence(item.evidence)}</div>
    </article>`).join('') : '<p class="muted">Отдельных предупреждений нет.</p>';

    const duplicateCards = pendingDuplicates.length ? pendingDuplicates.map((item) => `<article class="control-item">
      <div class="control-item-main"><span>Кандидат на замену</span><h3>${esc(item.title || 'Без названия')}</h3><p>${esc(item.author || 'Автор не указан')} · существующая книга ID ${Number(item.existing_book_id || 0)}</p><small>Замена сохранит ID книги, покупки и прогресс читателей.</small></div>
      <div class="control-actions">${data.can_manage ? `${actionButton('Пропустить', `libraryduplicate:skip:${Number(item.id)}`)}${actionButton('Заменить книгу', `libraryduplicate:replace:${Number(item.id)}`, 'danger')}` : ''}</div>
    </article>`).join('') : '<article class="empty-card"><h3>Неразобранных дублей нет</h3><p>Все совпадения уже обработаны.</p></article>';

    const manageActions = data.can_manage
      ? `${actionButton('Повторить проверку', `librarybatch:audit:${Number(batchId)}`)}${Number(audit.ready || 0) ? actionButton(`Опубликовать готовые: ${Number(audit.ready)}`, `librarybatch:publish:${Number(batchId)}`, 'approve') : ''}${Number(statuses.draft || 0) ? actionButton('Откатить черновики', `librarybatch:rollback:${Number(batchId)}`, 'danger') : ''}`
      : '';
    const actions = `<article class="control-item payment-settings-card">
      <div class="control-item-main"><span>Отчёт и действия</span><h3>${esc(batch.archive_name || 'Архив')}</h3><p>Полный отчёт содержит все причины брака без ограничения первыми 200 строками. Публикация затрагивает только готовые книги, откат — только новые неопубликованные книги пакета.</p></div>
      <div class="control-actions"><button type="button" class="control-action" id="libraryBatchReportJson">Скачать JSON</button><button type="button" class="control-action" id="libraryBatchReportCsv">Скачать CSV</button>${manageActions}</div>
    </article>`;

    $('workspaceList').innerHTML = `<div class="control-stat-grid">
      ${statCard('Найдено папок', Number(batch.total_found || 0))}
      ${statCard('Черновиков', Number(statuses.draft || 0))}
      ${statCard('Готовы', Number(audit.ready || 0))}
      ${statCard('Заблокированы', Number(audit.blocked || 0))}
      ${statCard('Опубликовано', Number(statuses.published || 0))}
      ${statCard('Среднее качество', `${Number(audit.average_score || 0)}%`)}
    </div>${actions}
    <div class="section-title"><div><span class="eyebrow">Проверка публикации</span><h2>Что мешает публикации</h2><p>Показываются причины и найденные фрагменты, чтобы не искать проблему во всём тексте.</p></div></div>${blockedCards}
    <div class="section-title"><div><span class="eyebrow">Предупреждения</span><h2>Не блокируют публикацию</h2></div></div>${warningCards}
    <div class="section-title"><div><span class="eyebrow">Совпадения</span><h2>Дубли книг</h2></div></div>${duplicateCards}
    <div class="section-title"><div><span class="eyebrow">Импорт</span><h2>Ошибки папок и файлов</h2>${errorsTotal > errors.length ? `<p>В интерфейсе показаны первые ${errors.length} из ${errorsTotal}. Полный список доступен в JSON/CSV.</p>` : ''}</div></div>${errorCards}`;
    $('libraryBatchReportJson')?.addEventListener('click', () => downloadLibraryBatchReport(batchId, 'json').catch(handleError));
    $('libraryBatchReportCsv')?.addEventListener('click', () => downloadLibraryBatchReport(batchId, 'csv').catch(handleError));
  }

  async function loadLibraryImport(silent = false) {
    if (state.libraryRefreshTimer) {
      window.clearTimeout(state.libraryRefreshTimer);
      state.libraryRefreshTimer = null;
    }
    state.libraryBatchId = null;
    if (!silent) openWorkspace('Импорт библиотеки', 'Загружайте накопительные ZIP прямо из Mini App. Все книги остаются черновиками.', 'Библиотека');
    $('workspaceTabs').innerHTML = '<button type="button" data-section="library_import">Обновить</button>';
    const data = await apiFetch('/api/control/library-import');
    const settings = data.settings || {};
    const learning = data.learning || {};
    const queue = data.queue || {};
    const queueControl = data.queue_control || { mode: 'running' };
    const queueWorker = data.queue_worker || { alive: false, status: 'not_started' };
    const queueMode = String(queueControl.mode || 'running');
    const pendingHandoffs = Number(queueControl.pending_handoffs || 0);
    const workerStatus = String(queueWorker.status || 'not_started');
    const workerAlive = queueWorker.alive === true;
    const workerLabels = { not_started: 'Не запущен', starting: 'Запускается', idle: 'Готов', processing: 'Обрабатывает', error_retrying: 'Перезапускается', failed: 'Ошибка', stopped: 'Остановлен' };
    const queueModeLabels = { running: 'Работает', paused: 'Пауза', maintenance: 'Обслуживание' };
    const jobs = Array.isArray(data.jobs) ? data.jobs : [];
    const batches = Array.isArray(data.batches) ? data.batches : [];
    const queueCount = Number(queue.queued || 0) + Number(queue.processing || 0) + Number(queue.cancelling || 0);
    const activeJobs = jobs.filter((item) => ['queued', 'processing', 'cancelling'].includes(String(item.status || '')));
    const recentJobs = jobs.filter((item) => !['queued', 'processing', 'cancelling'].includes(String(item.status || ''))).slice(0, 8);
    const activeHtml = activeJobs.length ? activeJobs.map((item) => libraryJobCard(item, data)).join('') : '<article class="empty-card premium-empty"><div class="empty-icon">✓</div><h3>Активных заданий нет</h3><p>Очередь импорта свободна.</p></article>';
    const recentHtml = recentJobs.length ? recentJobs.map((item) => libraryJobCard(item, data)).join('') : '<p class="muted">Завершённых заданий пока нет.</p>';
    const history = batches.length ? batches.map((item) => libraryBatchCard(item, data.can_manage)).join('') : '<article class="empty-card"><h3>История пока пуста</h3><p>Первый импорт появится здесь после завершения.</p></article>';
    const importButton = data.can_bulk_import
      ? '<button type="button" id="libraryImportStart" class="approve">Выбрать ZIP и импортировать</button>'
      : '';
    const moderationButton = data.can_manage
      ? `<button type="button" id="libraryAutoModerationToggle" class="${learning.enabled ? 'secondary' : 'approve'}">${learning.enabled ? 'Выключить автомодерацию' : 'Включить автомодерацию'}</button>`
      : '';
    const queueModeText = queueMode === 'running'
      ? 'Новые задания запускаются автоматически.'
      : queueMode === 'maintenance'
        ? (queueControl.draining ? 'Текущее задание завершится. Новые не запускаются до окончания обслуживания.' : 'Новые задания не запускаются до окончания обслуживания.')
        : (queueControl.draining ? 'Текущее задание завершится. Следующие останутся в очереди.' : 'Следующие задания останутся в очереди до продолжения.');
    const queueModeActions = data.can_control_queue
      ? `${queueCount > 0 ? '<button type="button" id="libraryQueueKick" class="approve">Запустить очередь сейчас</button>' : ''}${queueMode === 'running'
        ? '<button type="button" id="libraryQueuePause" class="secondary">Поставить на паузу</button><button type="button" id="libraryQueueMaintenance" class="danger">Режим обслуживания</button>'
        : `<button type="button" id="libraryQueueResume" class="approve">Продолжить очередь</button>${queueMode === 'paused' ? '<button type="button" id="libraryQueueMaintenance" class="danger">Режим обслуживания</button>' : '<button type="button" id="libraryQueuePause" class="secondary">Обычная пауза</button>'}`} `
      : '';
    $('workspaceList').innerHTML = `<div class="control-stat-grid">
      ${statCard('В очереди', queueCount)}
      ${statCard('Режим очереди', queueModeLabels[queueMode] || queueMode)}
      ${statCard('Обработчик', workerLabels[workerStatus] || workerStatus)}
      ${statCard('Принятые ZIP', pendingHandoffs)}
      ${statCard('Автомодерация', learning.enabled ? 'Включена' : 'Выключена')}
      ${statCard('Решений для обучения', Number(learning.approved || 0) + Number(learning.rejected || 0))}
      ${statCard('Доверенных категорий', (learning.trusted_categories || []).length)}
    </div>
    <article class="control-item payment-settings-card">
      <div class="control-item-main"><span>Управление очередью</span><h3>${esc(queueModeLabels[queueMode] || queueMode)} · ${esc(workerLabels[workerStatus] || workerStatus)}</h3><p>${esc(queueModeText)}</p>${queueWorker.last_error ? `<p class="danger-text">Последняя ошибка обработчика: ${esc(String(queueWorker.last_error).slice(0, 300))}</p>` : ''}${pendingHandoffs ? `<p class="warning-text">${pendingHandoffs} полностью принятый ZIP ожидает автоматического восстановления передачи в очередь.</p>` : ''}<small>Пауза и обслуживание сохраняются после перезапуска сервера. Уже выполняющееся задание не обрывается.</small></div>
      <div class="control-actions">${queueModeActions}</div>
    </article>
    <article class="control-item payment-settings-card">
      <div class="control-item-main"><span>Массовый импорт</span><h3>Books/001/...</h3><p>Изменённая книга заменяет старую версию автоматически. При занятом ID новая книга получает первый свободный номер.</p><small>Лимит: ${Number(settings.max_books || 0) ? `до ${Number(settings.max_books)} книг` : 'без ограничения по количеству'} · ZIP до ${Number(settings.max_archive_mb || 0)} МБ. Публикация после импорта не выполняется.${queueMode !== 'running' ? ' Архив можно загрузить, но обработка начнётся только после продолжения очереди.' : ''}</small></div>
      <div class="control-actions">${importButton}${moderationButton}</div>
    </article>
    <div class="section-title"><div><span class="eyebrow">Сейчас</span><h2>Очередь и прогресс</h2></div></div>${activeHtml}
    <div class="section-title"><div><span class="eyebrow">Недавние задания</span><h2>Результаты обработки ZIP</h2></div></div>${recentHtml}
    <div class="section-title"><div><span class="eyebrow">Пакеты</span><h2>Проверка и публикация</h2></div></div>${history}`;
    $('libraryImportStart')?.addEventListener('click', async () => {
      const button = $('libraryImportStart');
      button.disabled = true;
      button.textContent = 'Открываем загрузку…';
      try {
        const session = await apiFetch('/api/control/library-import/session', { method: 'POST' });
        window.location.assign(session.upload_url);
      } catch (error) {
        button.disabled = false;
        button.textContent = 'Выбрать ZIP и импортировать';
        throw error;
      }
    });
    $('libraryAutoModerationToggle')?.addEventListener('click', async () => {
      await apiFetch('/api/control/library-import/auto-moderation', {
        method: 'PATCH',
        body: JSON.stringify({ enabled: !Boolean(learning.enabled) }),
      });
      notify(!learning.enabled ? 'Автомодерация включена' : 'Автомодерация выключена');
      await loadLibraryImport();
    });
    const changeQueueMode = async (mode) => {
      await apiFetch('/api/control/library-import/queue-mode', {
        method: 'PATCH',
        body: JSON.stringify({ mode }),
      });
      notify(mode === 'running' ? 'Очередь продолжена' : mode === 'maintenance' ? 'Включён режим обслуживания' : 'Очередь поставлена на паузу');
      await loadLibraryImport();
    };
    $('libraryQueuePause')?.addEventListener('click', () => changeQueueMode('paused').catch(handleError));
    $('libraryQueueMaintenance')?.addEventListener('click', () => changeQueueMode('maintenance').catch(handleError));
    $('libraryQueueResume')?.addEventListener('click', () => changeQueueMode('running').catch(handleError));
    const kickQueue = async () => {
      await apiFetch('/api/control/library-import/queue-kick', { method: 'POST' });
      notify('Очередь запущена');
      await loadLibraryImport();
    };
    $('libraryQueueKick')?.addEventListener('click', () => kickQueue().catch(handleError));
    document.querySelectorAll('[data-library-queue-kick]').forEach((button) => button.addEventListener('click', () => kickQueue().catch(handleError)));
    if (activeJobs.length && state.active === 'library_import') {
      state.libraryRefreshTimer = window.setTimeout(() => {
        state.libraryRefreshTimer = null;
        if (state.active === 'library_import' && !state.libraryBatchId) {
          loadLibraryImport(true).catch(handleError);
        }
      }, 4000);
    }
  }

  async function loadGraphicPages(status = 'new') {
    openWorkspace('Страницы комиксов', 'Проверяйте жалобы читателей и скрывайте только проблемные страницы.', 'Графическая модерация');
    $('workspaceTabs').innerHTML = '<button type="button" data-status="new">Новые</button><button type="button" data-status="pending">В работе</button><button type="button" data-status="closed">Закрытые</button><button type="button" data-status="rejected">Отклонённые</button>';
    $('workspaceTabs').querySelector(`[data-status="${status}"]`)?.classList.add('active');
    const data = await apiFetch(`/api/control/graphic-page-reports?status=${encodeURIComponent(status)}`);
    const items = data.items || [];
    if (!items.length) emptyList('Страниц на проверке нет', 'В этой очереди пока ничего нет.');
    else $('workspaceList').innerHTML = items.map((item) => {
      const reader = `/comic/${Number(item.graphic_chapter_id)}?moderation=1#page-${Number(item.page_number)}`;
      const actions = ['new', 'pending'].includes(status)
        ? `${actionLink('Открыть страницу', reader)}${actionButton('Оставить', `graphicpage:approve:${item.graphic_page_id}`, 'approve')}${actionButton('Скрыть страницу', `graphicpage:reject:${item.graphic_page_id}`, 'danger')}`
        : actionLink('Открыть страницу', reader);
      return `<article class="control-item" data-id="${item.id}">
        <div class="control-item-main"><span>Страница ${Number(item.page_number)} · том ${Number(item.volume_number || 1)}</span><h3>${esc(item.book_title)}</h3><p>${esc(item.chapter_title)} · ${esc(item.username ? '@' + item.username : item.full_name || 'Читатель')}</p><small>${esc(item.reason || 'Причина не указана')} · ${dateText(item.created_at)}</small></div>
        <div class="control-actions">${actions}</div>
      </article>`;
    }).join('');
    $('workspaceTabs').onclick = (event) => {
      const button = event.target.closest('[data-status]');
      if (button) loadGraphicPages(button.dataset.status).catch(handleError);
    };
  }

  async function loadComments() {
    openWorkspace('Отзывы и комментарии', 'Публикуйте комментарии к страницам после проверки и скрывайте нарушения.');
    $('workspaceTabs').innerHTML = '<button class="active" type="button" data-content-tab="comments">Комментарии</button><button type="button" data-content-tab="reviews">Отзывы</button><button type="button" data-content-tab="graphic">К страницам</button>';
    const data = await apiFetch('/api/control/comments');
    const render = (kind) => {
      const items = kind === 'comments' ? data.comments || [] : kind === 'reviews' ? data.reviews || [] : data.graphic_comments || [];
      const emptyText = kind === 'comments' ? 'Новых комментариев нет.' : kind === 'reviews' ? 'Новых отзывов нет.' : 'Комментариев к страницам на проверке нет.';
      if (!items.length) return emptyList('Здесь спокойно', emptyText);
      $('workspaceList').innerHTML = items.map((item) => {
        if (kind === 'graphic') {
          const reader = `/comic/${Number(item.graphic_chapter_id)}?moderation=1#page-${Number(item.page_number || 1)}`;
          return `<article class="control-item" data-id="${item.id}">
            <div class="control-item-main"><span>Комментарий к странице ${Number(item.page_number || 1)} · #${item.id}</span><h3>${esc(item.book_title || 'Графическое произведение')}</h3><p>${esc(item.chapter_title || 'Глава')} · ${esc(item.username ? '@' + item.username : item.full_name || 'Читатель')}</p><small>${esc(item.text || 'Без текста')}</small></div>
            <div class="control-actions">${actionLink('Открыть страницу', reader)}${actionButton('Опубликовать', `graphiccomment:publish:${item.id}`, 'approve')}${actionButton('Скрыть', `graphiccomment:hide:${item.id}`, 'danger')}</div>
          </article>`;
        }
        return `<article class="control-item" data-id="${item.id}">
          <div class="control-item-main"><span>${kind === 'comments' ? 'Комментарий' : 'Отзыв'} #${item.id}</span><h3>${esc(item.book_title || 'Книга')}</h3><p>${esc(item.username ? '@' + item.username : item.full_name || 'Читатель')}${kind === 'reviews' ? ` · ${Number(item.rating || 0)}★` : ''}</p><small>${esc(item.text || 'Без текста')}</small></div>
          <div class="control-actions">${actionButton('Скрыть', `${kind === 'comments' ? 'comment' : 'review'}:hide:${item.id}`, 'danger')}</div>
        </article>`;
      }).join('');
    };
    render('comments');
    $('workspaceTabs').onclick = (event) => {
      const button = event.target.closest('[data-content-tab]');
      if (!button) return;
      $('workspaceTabs').querySelectorAll('button').forEach((b) => b.classList.toggle('active', b === button));
      render(button.dataset.contentTab);
    };
  }

  async function loadComplaints(status = 'new') {
    openWorkspace('Жалобы', 'Переводите обращение в работу или закрывайте после решения.');
    $('workspaceTabs').innerHTML = '<button type="button" data-status="new">Новые</button><button type="button" data-status="pending">В работе</button><button type="button" data-status="closed">Закрытые</button>';
    $('workspaceTabs').querySelector(`[data-status="${status}"]`)?.classList.add('active');
    const data = await apiFetch(`/api/control/complaints?status=${encodeURIComponent(status)}`);
    const items = data.items || [];
    if (!items.length) emptyList('Обращений нет', 'В этой очереди пока ничего нет.');
    else $('workspaceList').innerHTML = items.map((item) => `<article class="control-item" data-id="${item.id}">
      <div class="control-item-main"><span>Жалоба #${item.id}</span><h3>${esc(item.target_type)} #${esc(item.target_id)}</h3><p>${esc(item.username ? '@' + item.username : item.full_name || 'Пользователь')} · ${dateText(item.created_at)}</p><small>${esc(item.reason)}</small></div>
      ${status !== 'closed' ? `<div class="control-actions">${status === 'new' ? actionButton('В работу', `complaint:pending:${item.id}`) : ''}${actionButton('Закрыть', `complaint:closed:${item.id}`, 'approve')}</div>` : ''}
    </article>`).join('');
    $('workspaceTabs').onclick = (event) => {
      const button = event.target.closest('[data-status]');
      if (button) loadComplaints(button.dataset.status).catch(handleError);
    };
  }

  function refundTarget(item) {
    return item.book_title || item.chapter_title || item.audio_title || 'Покупка';
  }

  async function loadRefunds(status = 'new') {
    openWorkspace('Возвраты', 'Возврат Stars выполняется только после подтверждения Telegram.');
    $('workspaceTabs').innerHTML = '<button type="button" data-status="new">Новые</button><button type="button" data-status="refunded">Возвращено</button><button type="button" data-status="rejected">Отклонено</button>';
    $('workspaceTabs').querySelector(`[data-status="${status}"]`)?.classList.add('active');
    const data = await apiFetch(`/api/control/refunds?status=${encodeURIComponent(status)}`);
    const items = data.items || [];
    if (!items.length) emptyList('Запросов нет', 'В этой очереди пока ничего нет.');
    else $('workspaceList').innerHTML = items.map((item) => `<article class="control-item" data-id="${item.id}">
      <div class="control-item-main"><span>Возврат #${item.id}</span><h3>${esc(refundTarget(item))}</h3><p>${Number(item.amount_stars || 0)} Stars · ${esc(item.username ? '@' + item.username : item.full_name || item.telegram_id)}</p><small>${esc(item.reason)}</small></div>
      ${status === 'new' ? `<div class="control-actions">${actionButton('Вернуть Stars', `refund:approve:${item.id}`, 'approve')}${actionButton('Отклонить', `refund:reject:${item.id}`, 'danger')}</div>` : ''}
    </article>`).join('');
    $('workspaceTabs').onclick = (event) => {
      const button = event.target.closest('[data-status]');
      if (button) loadRefunds(button.dataset.status).catch(handleError);
    };
  }

  async function loadPayouts(status = 'new') {
    openWorkspace('Выплаты авторам', 'Сначала одобрите заявку, затем отметьте её выплаченной после реального перевода.');
    $('workspaceTabs').innerHTML = '<button type="button" data-status="new">Новые</button><button type="button" data-status="approved">Одобрено</button><button type="button" data-status="frozen">Заморожено</button><button type="button" data-status="paid">Выплачено</button>';
    $('workspaceTabs').querySelector(`[data-status="${status}"]`)?.classList.add('active');
    const data = await apiFetch(`/api/control/payouts?status=${encodeURIComponent(status)}`);
    const items = data.items || [];
    if (!items.length) emptyList('Заявок нет', 'В этой очереди пока ничего нет.');
    else $('workspaceList').innerHTML = items.map((item) => {
      let buttons = '';
      if (status === 'new') buttons = actionButton('Одобрить', `payout:approve:${item.id}`, 'approve') + actionButton('Заморозить', `payout:freeze:${item.id}`, 'danger') + actionButton('Отклонить', `payout:reject:${item.id}`, 'danger');
      if (status === 'approved') buttons = actionButton('Выплачено', `payout:paid:${item.id}`, 'approve') + actionButton('Заморозить', `payout:freeze:${item.id}`, 'danger');
      if (status === 'frozen') buttons = actionButton('Разморозить', `payout:unfreeze:${item.id}`) + actionButton('Отклонить', `payout:reject:${item.id}`, 'danger');
      return `<article class="control-item" data-id="${item.id}">
        <div class="control-item-main"><span>Выплата #${item.id}</span><h3>${esc(item.pen_name || item.username || item.telegram_id)}</h3><p>${Number(item.amount_stars || 0)} Stars · ${esc(item.method_type || '')}</p><small>${status === 'paid' ? `Выплачено ${dateText(item.paid_at)}` : `Заявка ${dateText(item.requested_at)}`}</small></div>
        ${buttons ? `<div class="control-actions">${buttons}</div>` : ''}
      </article>`;
    }).join('');
    $('workspaceTabs').onclick = (event) => {
      const button = event.target.closest('[data-status]');
      if (button) loadPayouts(button.dataset.status).catch(handleError);
    };
  }

  async function loadRubProfiles(status = 'pending') {
    openWorkspace('Платёжные профили авторов', 'Проверяйте статус, ФИО/наименование, ИНН и реквизиты СБП. Полный номер карты никогда не запрашивается.', 'Рубли');
    $('workspaceTabs').innerHTML = '<button type="button" data-status="pending">На проверке</button><button type="button" data-status="verified">Подтверждено</button><button type="button" data-status="rejected">Отклонено</button><button type="button" data-status="blocked">Заблокировано</button>';
    $('workspaceTabs').querySelector(`[data-status="${status}"]`)?.classList.add('active');
    const data = await apiFetch(`/api/control/rub-profiles?status=${encodeURIComponent(status)}`);
    const items = data.items || [];
    if (!items.length) emptyList('Профилей нет', 'В этой очереди пока ничего нет.');
    else $('workspaceList').innerHTML = items.map((item) => {
      let buttons = '';
      if (status === 'pending') buttons = actionButton('Подтвердить', `rubprofile:approve:${item.id}`, 'approve') + actionButton('Отклонить', `rubprofile:reject:${item.id}`, 'danger');
      if (status === 'verified') buttons = actionButton('Заблокировать', `rubprofile:block:${item.id}`, 'danger');
      return `<article class="control-item" data-id="${item.id}">
        <div class="control-item-main"><span>Профиль #${item.id} · ${esc(item.legal_status)}</span><h3>${esc(item.legal_name || item.pen_name || 'Автор')}</h3><p>ИНН ${esc(item.inn || 'не указан')} · ${esc(item.sbp_bank_name || 'банк не указан')} · ${esc(item.sbp_phone_masked || '')}</p><small>${esc(item.username ? '@' + item.username : item.full_name || item.telegram_id)}${item.rejection_reason ? ` · ${esc(item.rejection_reason)}` : ''}</small></div>
        ${buttons ? `<div class="control-actions">${buttons}</div>` : ''}
      </article>`;
    }).join('');
    $('workspaceTabs').onclick = (event) => {
      const button = event.target.closest('[data-status]');
      if (button) loadRubProfiles(button.dataset.status).catch(handleError);
    };
  }

  async function loadRubPayouts(status = 'new') {
    openWorkspace('Выплаты авторам в рублях', 'Заявки отправляются через ЮKassa по СБП только после проверки профиля и окончания удержания.', 'Рубли');
    $('workspaceTabs').innerHTML = '<button type="button" data-status="new">Новые</button><button type="button" data-status="processing">В обработке</button><button type="button" data-status="succeeded">Выплачено</button><button type="button" data-status="failed">Ошибка</button><button type="button" data-status="canceled">Отменено</button>';
    $('workspaceTabs').querySelector(`[data-status="${status}"]`)?.classList.add('active');
    const data = await apiFetch(`/api/control/rub-payouts?status=${encodeURIComponent(status)}`);
    const items = data.items || [];
    if (!items.length) emptyList('Заявок нет', 'В этой очереди пока ничего нет.');
    else $('workspaceList').innerHTML = items.map((item) => {
      const canExecute = ['new', 'failed'].includes(status) && data.provider_ready;
      const buttons = canExecute ? actionButton('Отправить через ЮKassa', `rubpayout:execute:${item.id}`, 'approve') : '';
      return `<article class="control-item" data-id="${item.id}">
        <div class="control-item-main"><span>Рублёвая выплата #${item.id}</span><h3>${esc(item.pen_name || item.username || item.telegram_id)}</h3><p>${rubText(item.amount_minor)} · ${esc(item.bank_name || 'СБП')}</p><small>${status === 'succeeded' ? `Выплачено ${dateText(item.paid_at)}` : `Заявка ${dateText(item.requested_at)}`}${item.failure_reason ? ` · ${esc(item.failure_reason)}` : ''}</small></div>
        ${buttons ? `<div class="control-actions">${buttons}</div>` : ''}
      </article>`;
    }).join('');
    if (!data.provider_ready && ['new', 'failed'].includes(status) && items.length) notify('ЮKassa ещё не подключена: заполните ключи выплат.');
    $('workspaceTabs').onclick = (event) => {
      const button = event.target.closest('[data-status]');
      if (button) loadRubPayouts(button.dataset.status).catch(handleError);
    };
  }

  function paymentToggle(name, label, checked, hint = '') {
    return `<label class="payment-setting-toggle"><span><b>${esc(label)}</b>${hint ? `<small>${esc(hint)}</small>` : ''}</span><input type="checkbox" name="${esc(name)}" ${checked ? 'checked' : ''}></label>`;
  }

  function secretInput(name, label, masked, hint = '') {
    return `<label class="payment-secret-field"><span>${esc(label)}</span><input type="password" name="${esc(name)}" autocomplete="new-password" placeholder="${esc(masked || 'не задан')}">${hint ? `<small>${esc(hint)}</small>` : ''}</label>`;
  }

  function providerBadge(provider) {
    const mark = provider.available ? '✅' : '⚠️';
    const warm = provider.warmed ? ' · прогрет' : '';
    return `<article class="control-item"><div class="control-item-main"><span>${mark} ${esc(provider.name)}</span><h3>${esc(provider.message || 'Нет данных')}</h3><p>${provider.available ? 'Доступен' : 'Недоступен'}${warm}</p><small>${esc(JSON.stringify(provider.details || {}))}</small></div></article>`;
  }

  async function playTtsSample(speakerId) {
    const response = await apiFetch(`/api/control/tts-vosk/sample/${Number(speakerId)}`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.addEventListener('ended', () => URL.revokeObjectURL(url), { once: true });
    audio.addEventListener('error', () => URL.revokeObjectURL(url), { once: true });
    await audio.play();
  }

  async function loadTtsDiagnostics() {
    openWorkspace('Диагностика озвучивания', 'Показывается фактический движок, выбранные русские голоса и состояние очереди.', 'Владелец');
    $('workspaceTabs').innerHTML = '';
    const data = await apiFetch('/api/control/tts-diagnostics');
    const profile = data.vosk_profile || {};
    const selected = profile.selected || {};
    const benchmark = profile.benchmark || {};
    const candidates = benchmark.candidates || [];
    const queue = data.queue || {};
    const activeSessions = data.active_sessions || [];
    const recentEvents = data.recent_events || [];
    const sampleCards = candidates.length ? candidates.map((item) => {
      const speaker = Number(item.speaker_id);
      const group = item.gender_group === 'male' ? 'мужская группа' : 'женская группа';
      const current = selected.female === speaker ? ' · выбран женским' : selected.male === speaker ? ' · выбран мужским' : '';
      const score = Number(item.score || 0).toFixed(1);
      return `<article class="control-item" data-speaker="${speaker}"><div class="control-item-main"><span>Голос ${speaker} · ${esc(group)}${esc(current)}</span><h3>Оценка стабильности: ${esc(score)}</h3><p>${item.passed ? 'Техническая проверка пройдена' : 'Есть замечания'}</p><small>${esc((item.issues || []).join(', ') || 'Без технических замечаний')}</small></div><div class="control-actions">${item.sample_ready ? actionButton('Прослушать', `ttssample:play:${speaker}`) : ''}${actionButton('Выбрать женским', `ttsvoice:female:${speaker}`, selected.female === speaker ? 'approve' : '')}${actionButton('Выбрать мужским', `ttsvoice:male:${speaker}`, selected.male === speaker ? 'approve' : '')}</div></article>`;
    }).join('') : '<article class="empty-card"><h3>Образцы ещё не созданы</h3><p>Запустите автоматическую проверку. Она создаст пять локальных образцов и выберет стабильную пару.</p></article>';
    const activeCards = activeSessions.length ? activeSessions.map((item) => {
      const last = item.last_event || {};
      const counters = item.counters || {};
      return `<article class="control-item"><div class="control-item-main"><span>Глава ${Number(item.chapter_id || 0)} · ${esc(item.voice || '')}</span><h3>${esc(last.event || 'сессия активна')}</h3><p>Плеер: ${esc(item.player_version || 'не сообщил версию')} · переходов: ${Number(counters.segment_transition_complete || 0)} · восстановлений: ${Number(counters.player_recovered || 0)}</p><small>${esc(JSON.stringify(last.details || {}))}</small></div></article>`;
    }).join('') : '<article class="empty-card"><h3>Активных сессий нет</h3><p>События появятся после запуска озвучивания читателем.</p></article>';
    const eventCards = recentEvents.slice(0, 12).map((item) => `<article class="control-item"><div class="control-item-main"><span>${esc(item.event || '')}</span><h3>Глава ${Number(item.chapter_id || 0)} · фрагмент ${Number(item.segment_index ?? 0) + 1}</h3><p>${new Date(Number(item.at_ms || 0)).toLocaleString('ru-RU')}</p><small>${esc(JSON.stringify(item.details || {}))}</small></div></article>`).join('') || '<article class="empty-card"><p>Событий плеера пока нет.</p></article>';
    $('workspaceList').innerHTML = `<article class="control-item payment-settings-card"><div class="control-item-main"><span>Локальный Vosk</span><h3>Женский: ${esc(selected.female ?? '—')} · Мужской: ${esc(selected.male ?? '—')}</h3><p>Источник выбора: ${esc(profile.source || 'по умолчанию')} · проверка: ${esc(benchmark.status || 'ожидается')}</p><small>${esc(benchmark.error || 'URL и API-ключ не требуются.')}</small></div><div class="control-actions">${actionButton('Проверить все голоса заново', 'ttsbenchmark:run:1', 'approve')}</div></article><article class="control-item payment-settings-card"><div class="control-item-main"><span>Очередь</span><h3>${Number(queue.running || 0)} выполняется · ${Number(queue.queued || 0)} ожидает</h3><p>Готово: ${Number(queue.completed || 0)} · ошибок: ${Number(queue.failed || 0)} · повторно использовано: ${Number(queue.deduplicated || 0)}</p><small>Рабочих процессов: ${Number(queue.workers || 0)} · контракт плеера: ${esc(data.player_contract_version || '—')}</small></div></article>${(data.providers || []).map(providerBadge).join('')}<div class="section-title"><div><span class="eyebrow">Живые сессии</span><h2>Переходы и восстановления</h2><p>Здесь видно, остановился ли плеер, был ли переход между главами и сработало ли автоматическое восстановление.</p></div></div>${activeCards}<div class="section-title"><div><span class="eyebrow">Последние события</span><h2>Журнал плеера</h2></div></div>${eventCards}<div class="section-title"><div><span class="eyebrow">Образцы</span><h2>Пять русских голосов</h2><p>Автовыбор оценивает стабильность. Окончательный тембр можно выбрать после прослушивания.</p></div></div>${sampleCards}`;
  }

  async function loadPaymentSettings() {
    openWorkspace('Stars и курсы', 'ЮKassa отключена. Все цифровые покупки принимаются только в Telegram Stars.', 'Владелец');
    $('workspaceTabs').innerHTML = '';
    const data = await apiFetch('/api/control/payment-settings');
    const cfg = data.settings || {};
    $('workspaceList').innerHTML = `<form id="paymentSettingsForm" class="payment-settings-form">
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Telegram Stars</span><h3>Единственный способ оплаты</h3><p>Покупатель оплачивает точное количество Stars. Рубли рядом показываются только как понятный ориентир и не являются отдельной кассой.</p></div>
        <div class="payment-settings-stack">
          ${paymentToggle('stars_enabled', 'Разрешить оплату Stars', cfg.stars_enabled, 'Можно временно остановить новые счета, не отнимая уже купленный доступ.')}
          <label class="payment-secret-field"><span>Ориентир покупателя, копеек за 1 Star</span><input type="number" min="2" max="100000" step="1" name="buyer_star_rate_minor" value="${Number(cfg.buyer_star_rate_minor || 145)}"><small>Например, 145 = примерно 1,45 ₽. Фактическую цену Stars определяет Telegram.</small></label>
          <label class="payment-secret-field"><span>Расчётный курс автора, копеек за 1 Star</span><input type="number" min="1" max="99999" step="1" name="author_star_rate_minor" value="${Number(cfg.author_star_rate_minor || 100)}"><small>Например, 100 = 1,00 ₽. Сначала удерживается комиссия платформы, затем чистые Stars автора фиксируются по этому курсу.</small></label>
          <label class="payment-secret-field"><span>Быстрая отмена неиспользованной покупки, минут</span><input type="number" min="1" max="120" step="1" name="purchase_cancel_minutes" value="${Number(cfg.purchase_cancel_minutes || 15)}"><small>В течение этого времени читатель может вернуть Stars автоматически, пока не начал читать, слушать или расходовать пакет.</small></label>
          <div class="payment-rate-example"><b>Распределение каждой новой продажи</b><p>Сумма трёх долей всегда должна быть ровно 100%. Для небольших цен используются только целые Stars и ближайшее целое распределение без потери общей суммы.</p></div>
          <label class="payment-secret-field"><span>Автору, %</span><input type="number" min="50" max="99" step="1" name="author_percent" value="${Number(cfg.author_percent || 80)}"><small>Минимум 50%. Начисляется автору целыми Stars.</small></label>
          <label class="payment-secret-field"><span>Платформе, %</span><input type="number" min="0" max="99" step="1" name="platform_percent" value="${Number(cfg.platform_percent ?? 19)}"><small>Доход и расходы платформы.</small></label>
          <label class="payment-secret-field"><span>В бонусный фонд, %</span><input type="number" min="0" max="25" step="1" name="bonus_percent" value="${Number(cfg.bonus_percent ?? 1)}"><small>Из этой доли обеспечиваются кешбэк и реферальные бонусы.</small></label>
          <div class="payment-rate-example"><b>Фиксированный курс бонусов</b><p>${Number(cfg.points_per_star || 100)} бонусов = 1 Star скидки. Курс не меняется, чтобы уже накопленные баллы не обесценивались.</p></div>
          <div class="payment-rate-example"><b>Реферальное распределение</b><p>${Number(cfg.referral_percent_of_bonus ?? 30)}% бонусного начисления получает пригласивший, ${Number(cfg.buyer_percent_of_bonus ?? 70)}% — пополнивший баланс. Без реферала покупатель получает всё начисление.</p></div>
          <label class="payment-secret-field"><span>Пакеты пополнения, Stars</span><input type="text" name="topup_packages" value="${esc((cfg.topup_packages || [50,100,250,500,1000]).join(','))}"><small>Целые числа через запятую.</small></label>
          <div class="payment-rate-example"><b>Проверочный расчёт</b><p id="paymentRateExample"></p></div>
        </div>
      </article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Защита</span><h3>Копирование и распространение</h3><p>Абсолютно запретить системный скриншот в Mini App нельзя, поэтому используются блокировка копирования, защищённые ссылки и персональный водяной знак.</p></div>
        <div class="payment-settings-stack">
          ${paymentToggle('content_protection_enabled', 'Защищать книги без разрешения на скачивание', cfg.content_protection_enabled)}
          ${paymentToggle('watermark_enabled', 'Показывать персональный водяной знак', cfg.watermark_enabled)}
        </div>
      </article>
      <div class="control-actions payment-settings-actions"><button type="submit" class="approve">Сохранить настройки</button></div>
    </form>`;

    const form = $('paymentSettingsForm');
    const integerSplit = (total, values) => {
      const keys = ['author', 'platform', 'bonus'];
      const tie = { bonus: 0, platform: 1, author: 2 };
      const result = {};
      const ranking = [];
      let assigned = 0;
      keys.forEach((key) => {
        const raw = total * Number(values[key] || 0);
        result[key] = Math.floor(raw / 100);
        assigned += result[key];
        ranking.push({ key, remainder: raw % 100, tie: tie[key] });
      });
      ranking.sort((a, b) => b.remainder - a.remainder || a.tie - b.tie);
      for (let i = 0; i < total - assigned; i += 1) result[ranking[i % ranking.length].key] += 1;
      return result;
    };
    const renderExample = () => {
      const buyer = Math.max(2, Number(form.elements.buyer_star_rate_minor.value || 145));
      const authorRate = Math.max(1, Number(form.elements.author_star_rate_minor.value || 100));
      const shares = {
        author: Number(form.elements.author_percent.value || 80),
        platform: Number(form.elements.platform_percent.value || 19),
        bonus: Number(form.elements.bonus_percent.value || 1),
      };
      const split = integerSplit(100, shares);
      const pointsRate = Math.max(1, Number(cfg.points_per_star || 100));
      const refPct = Math.max(0, Math.min(100, Number(cfg.referral_percent_of_bonus ?? 30)));
      const cashback = Math.floor(100 * shares.bonus * pointsRate / 100);
      const referrer = Math.floor(cashback * refPct / 100);
      const buyerPoints = cashback - referrer;
      const buyerRub = (100 * buyer / 100).toFixed(2);
      const authorRub = (split.author * authorRate / 100).toFixed(2);
      $('paymentRateExample').textContent = `Продажи на 100 Stars: автору ${split.author}, платформе ${split.platform}, в бонусный фонд ${split.bonus}; ориентир покупателя ${buyerRub} ₽, автору ${authorRub} ₽. Пополнение 100 Stars: ${cashback} бонусов; при реферале ${buyerPoints} покупателю и ${referrer} пригласившему.`;
    };
    form?.addEventListener('input', renderExample);
    renderExample();
    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const buyer = Math.max(2, Number(form.elements.buyer_star_rate_minor.value || 145));
      const author = Math.max(1, Number(form.elements.author_star_rate_minor.value || 100));
      if (buyer <= author) { notify('Курс покупателя должен быть выше курса автора'); return; }
      const shares = {
        author_percent: Math.max(50, Math.min(99, Number(form.elements.author_percent.value || 80))),
        platform_percent: Math.max(0, Math.min(99, Number(form.elements.platform_percent.value || 19))),
        bonus_percent: Math.max(0, Math.min(25, Number(form.elements.bonus_percent.value || 1))),
      };
      if (shares.author_percent + shares.platform_percent + shares.bonus_percent !== 100) {
        notify('Доли автора, платформы и бонусов должны давать ровно 100%'); return;
      }
      const packages = String(form.elements.topup_packages.value || '').split(',').map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0 && value <= 10000);
      if (!packages.length) { notify('Укажите хотя бы один пакет пополнения'); return; }
      const payload = {
        stars_enabled: Boolean(form.elements.stars_enabled.checked),
        content_protection_enabled: Boolean(form.elements.content_protection_enabled.checked),
        watermark_enabled: Boolean(form.elements.watermark_enabled.checked),
        buyer_star_rate_minor: buyer,
        author_star_rate_minor: author,
        purchase_cancel_minutes: Math.max(1, Math.min(120, Number(form.elements.purchase_cancel_minutes.value || 15))),
        ...shares,
        topup_packages: packages,
      };
      await apiFetch('/api/control/payment-settings', { method: 'PATCH', body: JSON.stringify(payload) });
      notify('Настройки Stars сохранены');
      await loadPaymentSettings();
    });
  }

  async function loadPremiumSettings() {
    openWorkspace('VoxLyra Premium', 'Premium добавляет комфорт и оформление, но не закрывает базовое чтение и стандартные функции.', 'Подписка');
    $('workspaceTabs').innerHTML = '';
    const data = await apiFetch('/api/control/premium');
    const plan = (data.plans || [])[0] || {};
    const summary = data.summary || {};
    $('workspaceList').innerHTML = `<form id="premiumSettingsForm" class="payment-settings-form">
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Тариф</span><h3>${esc(plan.title || 'VoxLyra Premium')}</h3><p>${esc(plan.description || '')}</p></div>
        <div class="payment-settings-stack">
          ${paymentToggle('enabled', 'Разрешить новые подписки Premium', Number(plan.is_active || 0) === 1, 'Отключение не отнимает уже оплаченный период у действующих пользователей.')}
          <label class="payment-secret-field"><span>Цена за 30 дней, Stars</span><input type="number" min="1" max="10000" step="1" name="price_stars" value="${Number(plan.price_stars || 99)}"><small>Цена книги и глав не связана с Premium и не меняется.</small></label>
          <label class="payment-secret-field"><span>Фонд авторов с каждой оплаты, %</span><input type="number" min="1" max="95" step="1" name="author_pool_percent" value="${Number(summary.author_pool_percent || 70)}"><small>Распределяется только целыми Stars по реальному чтению. Изменение действует для новых оплат.</small></label>
        </div>
      </article>
      <div class="control-stat-grid premium-control-summary">
        ${statCard('Активные', summary.active_users || 0, 'пользователей')}
        ${statCard('Автопродление', summary.auto_renew || 0, 'подписок')}
        ${statCard('Оплаты', summary.payments || 0)}
        ${statCard('Оборот', summary.gross_stars || 0, 'Stars')}
        ${statCard('Авторам', summary.author_allocated_stars || 0, 'Stars начислено')}
        ${statCard('Ожидают расчёта', summary.pending_pools || 0, 'периодов')}
        ${statCard('Без чтения', summary.no_activity_pools || 0, 'периодов')}
        ${statCard('Нераспределено', summary.unallocated_stars || 0, 'Stars')}
      </div>
      <div class="control-actions payment-settings-actions"><button type="submit" class="approve">Сохранить Premium</button></div>
    </form>`;
    const form = $('premiumSettingsForm');
    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        enabled: Boolean(form.elements.enabled.checked),
        price_stars: Math.max(1, Math.min(10000, Number(form.elements.price_stars.value || 99))),
        author_pool_percent: Math.max(1, Math.min(95, Number(form.elements.author_pool_percent.value || 70))),
      };
      await apiFetch('/api/control/premium', { method: 'PATCH', body: JSON.stringify(payload) });
      notify('Настройки Premium сохранены');
      await loadPremiumSettings();
      await refreshDashboard();
    });
  }


  function manualAchievementDefinitionCard(item) {
    const active = item.active !== false;
    return `<article class="manual-achievement-definition${active ? '' : ' inactive'}">
      <div><span>${item.group === 'author' ? 'Авторская' : 'Читательская'}</span><h4>${esc(item.title || item.code)}</h4><p>${esc(item.description || '')}</p><small>${esc(item.code)} · ${esc(item.rarity || 'epic')} · ${Number(item.custom_points || 0) || 'по редкости'} очков · ${Number(item.awarded || 0)} выдач</small></div>
      <button type="button" class="control-action ${active ? 'danger' : 'approve'}" data-manual-toggle="${esc(item.code)}" data-manual-active="${active ? '0' : '1'}">${active ? 'Отключить' : 'Включить'}</button>
    </article>`;
  }

  function manualAchievementEventCard(item) {
    let payload = {};
    try { payload = JSON.parse(item.after_value || '{}'); } catch (_) {}
    const labels = {
      manual_achievement_granted: 'Выдана',
      manual_achievement_revoked: 'Отозвана',
      manual_achievement_created: 'Создана',
    };
    const actor = item.username ? `@${item.username}` : (item.full_name || `ID ${item.actor_user_id || 0}`);
    return `<article class="manual-achievement-event"><div><b>${esc(labels[item.action] || item.action || 'Действие')}</b><span>${esc(payload.code || item.target_id || '')}</span></div><small>${esc(actor)} · ${esc(dateText(item.created_at))}</small></article>`;
  }

  async function loadAchievementSettings() {
    openWorkspace('Награды и уровни', 'Управляйте ценностью наград, порогами коллекционера и сезонными событиями без обновления кода.', 'Владелец');
    $('workspaceTabs').innerHTML = '';
    const data = await apiFetch('/api/control/achievements');
    const cfg = data.settings || {};
    const points = cfg.points || {};
    const levels = Array.isArray(cfg.levels) ? cfg.levels : [];
    const rare = cfg.rare || {};
    const season = cfg.season || {};
    const summary = data.summary || {};
    const levelFields = [0,1,2,3,4].map((index) => {
      const item = levels[index] || { threshold: index ? index * 100 : 0, name: `Уровень ${index + 1}` };
      return `<div class="achievement-admin-level"><label class="payment-secret-field"><span>Название уровня ${index + 1}</span><input type="text" maxlength="48" name="level_name_${index}" value="${esc(item.name || '')}"></label><label class="payment-secret-field"><span>Порог очков</span><input type="number" min="${index ? 1 : 0}" max="10000000" step="1" name="level_threshold_${index}" value="${Number(item.threshold || 0)}" ${index === 0 ? 'readonly' : ''}></label></div>`;
    }).join('');
    const popular = (summary.popular || []).map((item) => `<span class="achievement-admin-popular"><b>${esc(item.code || '')}</b><small>${Number(item.awarded || 0)} выдач</small></span>`).join('') || '<span class="muted">Награды ещё не выдавались.</span>';
    const manual = data.manual || {};
    const manualDefinitions = Array.isArray(manual.definitions) ? manual.definitions : [];
    const activeManualDefinitions = manualDefinitions.filter((item) => item.active !== false);
    const manualOptions = activeManualDefinitions.map((item) => `<option value="${esc(item.code)}">${esc(item.title)} · ${esc(item.rarity)}</option>`).join('');
    const manualList = manualDefinitions.map(manualAchievementDefinitionCard).join('') || '<p class="muted">Особых наград пока нет.</p>';
    const manualEvents = (manual.events || []).map(manualAchievementEventCard).join('') || '<p class="muted">Журнал выдач пока пуст.</p>';
    $('workspaceList').innerHTML = `<form id="achievementSettingsForm" class="payment-settings-form achievement-admin-form">
      <div class="control-stat-grid achievement-admin-summary">
        ${statCard('Выдано наград', summary.awards_total || 0)}
        ${statCard('Коллекционеров', summary.users_with_awards || 0, 'с наградами')}
        ${statCard('Витрины', summary.showcase_users || 0, 'профилей')}
        ${statCard('Автоматических', `${Number(summary.automatic_total || 0)} / ${Number(summary.planned_target || 100)}`, 'план наград')}
        ${statCard('Активный сезон', season.enabled ? (season.title || 'Включён') : 'Выключен')}
      </div>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Очки</span><h3>Ценность каждого уровня</h3><p>Изменение пересчитывает уровень всех пользователей по уже полученным наградам. Сами награды не удаляются.</p></div><div class="payment-settings-stack achievement-points-admin">
        <label class="payment-secret-field"><span>Бронзовая</span><input type="number" min="1" max="10000" name="points_common" value="${Number(points.common || 10)}"></label>
        <label class="payment-secret-field"><span>Серебряная</span><input type="number" min="1" max="10000" name="points_rare" value="${Number(points.rare || 25)}"></label>
        <label class="payment-secret-field"><span>Золотая</span><input type="number" min="1" max="10000" name="points_epic" value="${Number(points.epic || 60)}"></label>
        <label class="payment-secret-field"><span>Платиновая</span><input type="number" min="1" max="10000" name="points_legendary" value="${Number(points.legendary || 150)}"></label>
        <label class="payment-secret-field"><span>Легенда</span><input type="number" min="1" max="10000" name="points_mythic" value="${Number(points.mythic || 300)}"></label>
      </div></article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Коллекционер</span><h3>Названия и пороги уровней</h3><p>Первый уровень всегда начинается с нуля. Каждый следующий порог должен быть больше предыдущего.</p></div><div class="payment-settings-stack achievement-level-admin">${levelFields}</div></article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Редкие награды</span><h3>Особые знаки сообщества</h3><p>Отключение скрывает ещё не полученную награду, но не отнимает её у уже награждённых пользователей.</p></div><div class="payment-settings-stack">
        ${paymentToggle('founding_member_enabled', '«Первые хранители»', rare.founding_member_enabled !== false, 'Автоматически выдаётся пользователям, зарегистрированным не позже контрольной даты.')}
        <label class="payment-secret-field"><span>Контрольная дата раннего сообщества</span><input type="date" name="founding_cutoff_date" value="${esc(rare.founding_cutoff_date || '2026-07-23')}"><small>Дата учитывается включительно.</small></label>
        ${paymentToggle('all_rounder_enabled', '«Голос всех миров»', rare.all_rounder_enabled !== false, 'Чтение, 60 минут аудио, 100 страниц комиксов и участие в обсуждении.')}
      </div></article>
      <article class="control-item payment-settings-card season-achievement-card"><div class="control-item-main"><span>Сезонная награда</span><h3>${esc(season.title || 'Сезон VoxLyra')}</h3><p>Код сезона нельзя повторять для другой кампании: новый код создаёт отдельную коллекционную награду.</p></div><div class="payment-settings-stack">
        ${paymentToggle('season_enabled', 'Сезон активен', Boolean(season.enabled), 'Прогресс считается по завершённым главам в выбранном диапазоне дат.')}
        <label class="payment-secret-field"><span>Код сезона</span><input type="text" maxlength="40" name="season_code" value="${esc(season.code || 'season')}"><small>Латиница, цифры и подчёркивание. После первых выдач лучше не менять.</small></label>
        <label class="payment-secret-field"><span>Название награды</span><input type="text" maxlength="80" name="season_title" value="${esc(season.title || '')}"></label>
        <label class="payment-secret-field"><span>Описание</span><input type="text" maxlength="240" name="season_description" value="${esc(season.description || '')}"></label>
        <div class="achievement-admin-dates"><label class="payment-secret-field"><span>Начало</span><input type="date" name="season_start_date" value="${esc(season.start_date || '')}"></label><label class="payment-secret-field"><span>Окончание</span><input type="date" name="season_end_date" value="${esc(season.end_date || '')}"></label></div>
        <div class="achievement-admin-dates"><label class="payment-secret-field"><span>Цель: завершённых глав</span><input type="number" min="1" max="1000000" name="season_goal" value="${Number(season.goal || 30)}"></label><label class="payment-secret-field"><span>Очки сезона</span><input type="number" min="0" max="10000" name="season_custom_points" value="${Number(season.custom_points || 0)}"><small>0 — использовать очки выбранного уровня.</small></label></div>
        <label class="payment-secret-field"><span>Уровень награды</span><select name="season_rarity"><option value="common" ${season.rarity === 'common' ? 'selected' : ''}>Бронза</option><option value="rare" ${season.rarity === 'rare' ? 'selected' : ''}>Серебро</option><option value="epic" ${season.rarity === 'epic' ? 'selected' : ''}>Золото</option><option value="legendary" ${season.rarity === 'legendary' ? 'selected' : ''}>Платина</option><option value="mythic" ${season.rarity === 'mythic' ? 'selected' : ''}>Легенда</option></select></label>
      </div></article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Популярность</span><h3>Чаще всего полученные награды</h3><p>Служебные коды показываются владельцу для контроля начислений.</p></div><div class="achievement-admin-popular-list">${popular}</div></article>
      <div class="control-actions payment-settings-actions"><button type="submit" class="approve">Сохранить систему наград</button></div>
    </form>
    <section class="manual-achievement-admin">
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Особые награды</span><h3>Создать награду владельца</h3><p>Такие награды выдаются и отзываются только вручную. Они отображаются в профиле и участвуют в уровне коллекционера.</p></div>
        <form id="manualAchievementCreateForm" class="manual-achievement-form">
          <label class="payment-secret-field"><span>Название</span><input name="title" maxlength="80" required placeholder="Например, Легенда VoxLyra"></label>
          <label class="payment-secret-field"><span>Описание</span><input name="description" maxlength="240" required placeholder="За особый вклад в сообщество"></label>
          <label class="payment-secret-field"><span>Раздел</span><select name="group"><option value="reader" selected>Читательская</option><option value="author">Авторская</option></select></label>
          <label class="payment-secret-field"><span>Уровень награды</span><select name="rarity"><option value="common">Бронза</option><option value="rare">Серебро</option><option value="epic" selected>Золото</option><option value="legendary">Платина</option><option value="mythic">Легенда</option></select></label>
          <label class="payment-secret-field"><span>Собственные очки</span><input type="number" min="0" max="10000" name="custom_points" value="0"><small>0 — использовать очки выбранного уровня.</small></label>
          <button type="submit" class="control-action approve">Создать особую награду</button>
        </form>
      </article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Выдача</span><h3>Наградить пользователя</h3><p>Найдите пользователя по ID, username, имени или псевдониму автора.</p></div>
        <div class="manual-achievement-award">
          <div class="manual-achievement-search"><input id="manualAchievementUserQuery" type="search" placeholder="ID, @username или имя"><button id="manualAchievementUserSearch" type="button" class="control-action">Найти</button></div>
          <div id="manualAchievementUserResults" class="manual-achievement-user-results"><p class="muted">Пользователь не выбран.</p></div>
          <label class="payment-secret-field"><span>Особая награда</span><select id="manualAchievementCode">${manualOptions || '<option value="">Сначала создайте награду</option>'}</select></label>
          <label class="payment-secret-field"><span>Причина или комментарий</span><input id="manualAchievementReason" maxlength="300" placeholder="Будет сохранено в журнале"></label>
          <div class="control-actions"><button id="manualAchievementGrant" type="button" class="approve">Выдать</button><button id="manualAchievementRevoke" type="button" class="danger">Отозвать</button></div>
        </div>
      </article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Каталог особых наград</span><h3>${manualDefinitions.length} наград</h3><p>Отключённую награду нельзя выдавать заново, но уже полученные экземпляры сохраняются.</p></div><div class="manual-achievement-definition-list">${manualList}</div></article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Журнал</span><h3>Последние действия</h3><p>Выдача, отзыв и создание особых наград.</p></div><div class="manual-achievement-event-list">${manualEvents}</div></article>
      <article class="control-item payment-settings-card achievement-artwork-entry"><div class="control-item-main"><span>Изображения</span><h3>Финальные PNG наград</h3><p>Загружайте свои изображения по одному или ZIP-архивом. Система проверит имя, PNG и точный размер 1024×1024 px. Сорок утверждённых изображений защищены от замены.</p></div><button id="openAchievementArtwork" type="button" class="control-action approve">Управление изображениями</button></article>
    </section>`;
    const form = $('achievementSettingsForm');
    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const configuredLevels = [0,1,2,3,4].map((index) => ({
        name: String(form.elements[`level_name_${index}`].value || '').trim(),
        threshold: index === 0 ? 0 : Number(form.elements[`level_threshold_${index}`].value || 0),
      }));
      for (let index = 1; index < configuredLevels.length; index += 1) {
        if (configuredLevels[index].threshold <= configuredLevels[index - 1].threshold) {
          notify('Пороги уровней должны строго возрастать'); return;
        }
      }
      const payload = {
        points: {
          common: Number(form.elements.points_common.value || 10),
          rare: Number(form.elements.points_rare.value || 25),
          epic: Number(form.elements.points_epic.value || 60),
          legendary: Number(form.elements.points_legendary.value || 150),
          mythic: Number(form.elements.points_mythic.value || 300),
        },
        levels: configuredLevels,
        rare: {
          founding_member_enabled: Boolean(form.elements.founding_member_enabled.checked),
          founding_cutoff_date: String(form.elements.founding_cutoff_date.value || ''),
          all_rounder_enabled: Boolean(form.elements.all_rounder_enabled.checked),
        },
        season: {
          enabled: Boolean(form.elements.season_enabled.checked),
          code: String(form.elements.season_code.value || '').trim(),
          title: String(form.elements.season_title.value || '').trim(),
          description: String(form.elements.season_description.value || '').trim(),
          start_date: String(form.elements.season_start_date.value || ''),
          end_date: String(form.elements.season_end_date.value || ''),
          goal: Number(form.elements.season_goal.value || 1),
          rarity: String(form.elements.season_rarity.value || 'epic'),
          custom_points: Number(form.elements.season_custom_points.value || 0),
        },
      };
      await apiFetch('/api/control/achievements', { method: 'PATCH', body: JSON.stringify(payload) });
      notify('Система наград сохранена');
      await loadAchievementSettings();
    });

    $('manualAchievementCreateForm')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const createForm = event.currentTarget;
      await apiFetch('/api/control/achievements/manual/definition', {
        method: 'POST',
        body: JSON.stringify({
          title: String(createForm.elements.title.value || '').trim(),
          description: String(createForm.elements.description.value || '').trim(),
          group: String(createForm.elements.group.value || 'Особые').trim(),
          rarity: String(createForm.elements.rarity.value || 'epic'),
          custom_points: Number(createForm.elements.custom_points.value || 0),
        }),
      });
      notify('Особая награда создана');
      await loadAchievementSettings();
    });

    document.querySelectorAll('[data-manual-toggle]').forEach((button) => button.addEventListener('click', async () => {
      await apiFetch(`/api/control/achievements/manual/definition/${encodeURIComponent(button.dataset.manualToggle)}`, {
        method: 'PATCH',
        body: JSON.stringify({ active: button.dataset.manualActive === '1' }),
      });
      notify(button.dataset.manualActive === '1' ? 'Награда включена' : 'Награда отключена');
      await loadAchievementSettings();
    }));

    $('manualAchievementUserSearch')?.addEventListener('click', async () => {
      const query = String($('manualAchievementUserQuery')?.value || '').trim();
      if (!query) { notify('Введите ID, username или имя'); return; }
      const result = await apiFetch(`/api/control/achievements/users?q=${encodeURIComponent(query)}`);
      const items = result.items || [];
      $('manualAchievementUserResults').innerHTML = items.length ? items.map((user) => `<button type="button" class="manual-achievement-user" data-manual-user="${Number(user.id)}"><b>${esc(user.full_name || user.pen_name || user.username || `ID ${user.telegram_id}`)}</b><span>${user.username ? `@${esc(user.username)}` : `Telegram ID ${Number(user.telegram_id)}`}${user.pen_name ? ` · ${esc(user.pen_name)}` : ''}</span></button>`).join('') : '<p class="muted">Пользователь не найден.</p>';
      document.querySelectorAll('[data-manual-user]').forEach((button) => button.addEventListener('click', () => {
        state.manualAchievementUser = Number(button.dataset.manualUser);
        document.querySelectorAll('[data-manual-user]').forEach((item) => item.classList.toggle('selected', item === button));
        notify(`Пользователь выбран: ID ${state.manualAchievementUser}`);
      }));
    });

    const changeManualAward = async (mode) => {
      const userId = Number(state.manualAchievementUser || 0);
      const code = String($('manualAchievementCode')?.value || '');
      if (!userId) { notify('Сначала выберите пользователя'); return; }
      if (!code) { notify('Выберите особую награду'); return; }
      await apiFetch(`/api/control/achievements/manual/${mode}`, {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, code, reason: String($('manualAchievementReason')?.value || '').trim() }),
      });
      notify(mode === 'grant' ? 'Награда выдана' : 'Награда отозвана');
      await loadAchievementSettings();
    };
    $('manualAchievementGrant')?.addEventListener('click', () => changeManualAward('grant').catch(handleError));
    $('manualAchievementRevoke')?.addEventListener('click', () => changeManualAward('revoke').catch(handleError));
    $('openAchievementArtwork')?.addEventListener('click', () => loadAchievementArtwork().catch(handleError));
  }


  function achievementArtworkStatusLabel(status) {
    return ({ protected: 'Утверждено и защищено', awaiting: 'Нужен финальный PNG', custom: 'Финальный PNG установлен', missing: 'Файл отсутствует' })[status] || status || '—';
  }

  function achievementArtworkCard(item) {
    const status = String(item.status || 'awaiting');
    const canReplace = Boolean(item.replaceable);
    const actions = canReplace
      ? `<div class="achievement-artwork-actions"><input type="file" accept="image/png,.png" hidden data-art-file="${esc(item.code)}"><button type="button" class="control-action approve" data-art-select="${esc(item.code)}">${item.overridden ? 'Заменить PNG' : 'Загрузить PNG'}</button>${item.overridden ? `<button type="button" class="control-action danger" data-art-reset="${esc(item.code)}">Вернуть заглушку</button>` : ''}</div>`
      : '<span class="achievement-artwork-locked">Защищено</span>';
    const image = item.url
      ? `<img src="${esc(item.url)}" alt="${esc(item.title || '')}" loading="lazy">`
      : '<div class="achievement-artwork-missing">PNG</div>';
    return `<article class="achievement-artwork-card status-${esc(status)}" data-art-status="${esc(status)}" data-art-search="${esc(`${item.title || ''} ${item.code || ''} ${item.filename || ''} ${item.tier || ''}`.toLowerCase())}">
      <div class="achievement-artwork-preview">${image}<span>${esc(item.tier || '')}</span></div>
      <div class="achievement-artwork-info"><div class="achievement-artwork-status">${esc(achievementArtworkStatusLabel(status))}</div><h4>${esc(item.title || item.code)}</h4><code>${esc(item.filename || '')}</code><p>${esc(item.condition || item.description || '')}</p>${item.composition ? `<small><b>Композиция:</b> ${esc(item.composition)}</small>` : ''}</div>
      ${actions}
    </article>`;
  }

  async function downloadAchievementManifest(format) {
    const selected = format === 'json' ? 'json' : 'md';
    const response = await fetch(`/api/control/achievement-artwork/manifest?format=${selected}`, {
      headers: { 'X-Telegram-Init-Data': tgInitData() }, cache: 'no-store',
    });
    if (!response.ok) {
      let message = 'Не удалось скачать манифест.';
      try { message = (await response.json()).detail || message; } catch (_) {}
      throw new Error(message);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `VoxLyra_ACHIEVEMENTS_ART_MANIFEST_100.${selected}`;
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function loadAchievementArtwork() {
    openWorkspace('Изображения наград', 'Финальные файлы хранятся отдельно от кода и переживают Redeploy. Утверждённые изображения защищены.', 'Владелец');
    $('workspaceTabs').innerHTML = '';
    const data = await apiFetch('/api/control/achievement-artwork');
    const summary = data.summary || {};
    const items = Array.isArray(data.items) ? data.items : [];
    const spec = data.spec || {};
    $('workspaceList').innerHTML = `<section class="achievement-artwork-admin">
      <div class="control-stat-grid achievement-artwork-summary">
        ${statCard('Всего', summary.total || 0, 'автоматических наград')}
        ${statCard('Защищено', summary.protected || 0, 'утверждённых PNG')}
        ${statCard('Установлено', summary.custom || 0, 'твоих финальных PNG')}
        ${statCard('Ожидают', summary.awaiting || 0, 'заглушек для замены')}
        ${statCard('Готовность', `${Number(summary.ready || 0)} / ${Number(summary.total || 0)}`)}
      </div>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Правила файла</span><h3>${Number(spec.width || 1024)}×${Number(spec.height || 1024)} px · PNG · sRGB</h3><p>Имя должно полностью совпадать с манифестом. Один файл — одна награда. Старый пользовательский PNG автоматически сохраняется в резерв.</p></div><div class="control-actions"><button id="achievementManifestMd" type="button">Манифест MD</button><button id="achievementManifestJson" type="button">Манифест JSON</button><button id="achievementArtworkBack" type="button">Назад к наградам</button></div></article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Массовая загрузка</span><h3>Импорт ZIP</h3><p>В корне ZIP могут находиться готовые PNG с именами из манифеста. Защищённые и посторонние файлы будут пропущены и показаны в отчёте.</p></div><form id="achievementArtworkZipForm" class="achievement-artwork-zip"><input id="achievementArtworkZip" type="file" accept="application/zip,.zip" required><button type="submit" class="approve">Проверить и установить ZIP</button><div id="achievementArtworkImportResult"></div></form></article>
      <div class="achievement-artwork-tools"><input id="achievementArtworkSearch" type="search" placeholder="Название, код или имя PNG"><select id="achievementArtworkFilter"><option value="replaceable">Требуют твоих изображений</option><option value="awaiting">Только заглушки</option><option value="custom">Установленные тобой</option><option value="protected">Утверждённые и защищённые</option><option value="all">Все 100 наград</option></select></div>
      <div id="achievementArtworkList" class="achievement-artwork-list"></div>
    </section>`;

    const renderItems = () => {
      const query = String($('achievementArtworkSearch')?.value || '').trim().toLowerCase();
      const filter = String($('achievementArtworkFilter')?.value || 'replaceable');
      const filtered = items.filter((item) => {
        const status = String(item.status || '');
        const filterOk = filter === 'all' || (filter === 'replaceable' ? Boolean(item.replaceable) : status === filter);
        const haystack = `${item.title || ''} ${item.code || ''} ${item.filename || ''} ${item.tier || ''}`.toLowerCase();
        return filterOk && (!query || haystack.includes(query));
      });
      $('achievementArtworkList').innerHTML = filtered.length ? filtered.map(achievementArtworkCard).join('') : '<article class="empty-card"><h3>Награды не найдены</h3><p>Измените фильтр или поисковую строку.</p></article>';
      document.querySelectorAll('[data-art-select]').forEach((button) => button.addEventListener('click', () => {
        document.querySelector(`[data-art-file="${button.dataset.artSelect}"]`)?.click();
      }));
      document.querySelectorAll('[data-art-file]').forEach((input) => input.addEventListener('change', async () => {
        const file = input.files?.[0];
        const code = String(input.dataset.artFile || '');
        const item = items.find((entry) => String(entry.code) === code);
        if (!file || !item) return;
        if (file.name !== item.filename) { notify(`Имя должно быть строго ${item.filename}`); input.value = ''; return; }
        const form = new FormData(); form.append('file', file, file.name);
        await apiFetch(`/api/control/achievement-artwork/${encodeURIComponent(code)}`, { method: 'POST', body: form });
        notify(`PNG установлен: ${item.title}`);
        await loadAchievementArtwork();
      }));
      document.querySelectorAll('[data-art-reset]').forEach((button) => button.addEventListener('click', async () => {
        if (!window.confirm('Вернуть временную заглушку? Текущий PNG сохранится в резервной копии.')) return;
        await apiFetch(`/api/control/achievement-artwork/${encodeURIComponent(button.dataset.artReset)}`, { method: 'DELETE' });
        notify('Возвращена временная заглушка');
        await loadAchievementArtwork();
      }));
    };
    $('achievementArtworkSearch')?.addEventListener('input', renderItems);
    $('achievementArtworkFilter')?.addEventListener('change', renderItems);
    $('achievementArtworkBack')?.addEventListener('click', () => loadAchievementSettings().catch(handleError));
    $('achievementManifestMd')?.addEventListener('click', () => downloadAchievementManifest('md').catch(handleError));
    $('achievementManifestJson')?.addEventListener('click', () => downloadAchievementManifest('json').catch(handleError));
    $('achievementArtworkZipForm')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const file = $('achievementArtworkZip')?.files?.[0];
      if (!file) { notify('Выберите ZIP-архив'); return; }
      const form = new FormData(); form.append('file', file, file.name);
      const result = await apiFetch('/api/control/achievement-artwork/import', { method: 'POST', body: form });
      const report = result.result || {};
      const errors = Array.isArray(report.errors) ? report.errors : [];
      $('achievementArtworkImportResult').innerHTML = `<p><b>Установлено: ${Number(report.installed_count || 0)}</b> · ошибок/пропусков: ${Number(report.error_count || 0)}</p>${errors.length ? `<details><summary>Показать отчёт</summary>${errors.slice(0, 100).map((item) => `<div><code>${esc(item.filename || '')}</code> — ${esc(item.error || '')}</div>`).join('')}</details>` : ''}<button id="achievementArtworkRefreshAfterImport" type="button" class="control-action">Обновить список</button>`;
      $('achievementArtworkRefreshAfterImport')?.addEventListener('click', () => loadAchievementArtwork().catch(handleError));
      notify(`ZIP обработан: ${Number(report.installed_count || 0)} PNG`);
    });
    renderItems();
  }


  function catalogPromotionStatus(item) {
    const labels = { active: 'В ротации', invoice: 'Ожидает оплаты', expired: 'Завершено', canceled: 'Остановлено' };
    return labels[item.status] || item.status || '—';
  }

  async function loadCatalogPromotions(query = '') {
    openWorkspace('Топ каталога', 'Продвигаемые книги получают ограниченные места в общей ротации и не вытесняют органический рейтинг.', 'Владелец');
    $('workspaceTabs').innerHTML = '';
    const clean = String(query || '').trim();
    const data = await apiFetch(`/api/control/catalog-promotions?q=${encodeURIComponent(clean)}`);
    const settings = data.settings || {};
    const books = data.books || [];
    const items = data.items || [];
    const activeCount = items.filter((item) => item.status === 'active').length;
    const bookRows = books.map((book) => `<article class="catalog-promotion-book${Number(book.promoted || 0) ? ' active' : ''}"><div><span>Книга №${Number(book.id)}</span><h4>${esc(book.title || '')}</h4><small>${esc(book.pen_name || 'Автор не указан')}${Number(book.promoted || 0) ? ' · уже в ротации' : ''}</small></div>${Number(book.promoted || 0) ? '<b>Активно</b>' : `<button type="button" class="control-action approve" data-owner-promote-book="${Number(book.id)}">В топ бесплатно</button>`}</article>`).join('') || '<p class="muted">Опубликованные книги не найдены.</p>';
    const promotionRows = items.map((item) => `<article class="catalog-promotion-history ${esc(item.status || '')}"><div><span>${esc(catalogPromotionStatus(item))} · ${item.source === 'owner' ? 'владелец' : `${Number(item.amount_stars || 0)} Stars`}</span><h4>${esc(item.book_title || `Книга №${item.book_id}`)}</h4><small>${esc(item.pen_name || 'Автор не указан')} · до ${esc(dateText(item.expires_at))} UTC · показы ${Number(item.impressions || 0)} · переходы ${Number(item.clicks || 0)}</small></div>${item.status === 'active' || item.status === 'invoice' ? `<button type="button" class="control-action danger" data-cancel-promotion="${Number(item.id)}">Остановить</button>` : ''}</article>`).join('') || '<p class="muted">Продвижений пока не было.</p>';
    $('workspaceList').innerHTML = `<section class="catalog-promotion-admin">
      <div class="control-stat-grid">${statCard('Активно', activeCount, 'книг в ротации')}${statCard('Цена для авторов', settings.price_stars || 30, 'Stars')}${statCard('Срок', settings.duration_hours || 24, 'часов')}${statCard('Мест на первой странице', settings.slots_first_page || 2, 'остальные — органика')}</div>
      <form id="catalogPromotionSettingsForm" class="control-item payment-settings-card catalog-promotion-settings"><div class="control-item-main"><span>Правила</span><h3>Общая честная ротация</h3><p>Ты можешь бесплатно выбрать любую опубликованную книгу. Авторы платят Stars и могут продвигать только свои книги.</p></div><div class="catalog-promotion-settings-grid">
        <label class="payment-secret-field"><span>Цена, Stars</span><input type="number" min="1" max="10000" name="price_stars" value="${Number(settings.price_stars || 30)}"></label>
        <label class="payment-secret-field"><span>Срок, часов</span><input type="number" min="1" max="720" name="duration_hours" value="${Number(settings.duration_hours || 24)}"></label>
        <label class="payment-secret-field"><span>Лимит автора</span><input type="number" min="1" max="20" name="max_active_per_author" value="${Number(settings.max_active_per_author || 3)}"></label>
        <label class="payment-secret-field"><span>Промо-мест на странице</span><input type="number" min="1" max="6" name="slots_first_page" value="${Number(settings.slots_first_page || 2)}"></label>
      </div><div class="control-actions"><button type="submit" class="approve">Сохранить правила</button></div></form>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>Любая опубликованная книга</span><h3>Бесплатное продвижение владельцем</h3><p>Книга получает не фиксированное первое место, а очередь показов среди других продвигаемых книг.</p></div><div class="catalog-promotion-search"><input id="catalogPromotionQuery" type="search" value="${esc(clean)}" placeholder="Название, автор или ID"><button id="catalogPromotionSearch" type="button" class="control-action">Найти</button><label class="payment-secret-field compact"><span>Срок, часов</span><input id="ownerPromotionDuration" type="number" min="1" max="720" value="${Number(settings.duration_hours || 24)}"></label></div><div class="catalog-promotion-book-list">${bookRows}</div></article>
      <article class="control-item payment-settings-card"><div class="control-item-main"><span>История и статистика</span><h3>Все продвижения</h3><p>Показы распределяются по наименьшему числу показов и давности последнего выхода в каталог.</p></div><div class="catalog-promotion-history-list">${promotionRows}</div></article>
    </section>`;

    $('catalogPromotionSettingsForm')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const settingsForm = event.currentTarget;
      await apiFetch('/api/control/catalog-promotions', { method: 'PATCH', body: JSON.stringify({
        price_stars: Number(settingsForm.elements.price_stars.value || 30),
        duration_hours: Number(settingsForm.elements.duration_hours.value || 24),
        max_active_per_author: Number(settingsForm.elements.max_active_per_author.value || 3),
        slots_first_page: Number(settingsForm.elements.slots_first_page.value || 2),
      }) });
      notify('Правила продвижения сохранены');
      await loadCatalogPromotions(clean);
      await refreshDashboard();
    });
    $('catalogPromotionSearch')?.addEventListener('click', () => loadCatalogPromotions($('catalogPromotionQuery')?.value || '').catch(handleError));
    $('catalogPromotionQuery')?.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); loadCatalogPromotions(event.currentTarget.value).catch(handleError); } });
    document.querySelectorAll('[data-owner-promote-book]').forEach((button) => button.addEventListener('click', async () => {
      await apiFetch('/api/control/catalog-promotions', { method: 'POST', body: JSON.stringify({ book_id: Number(button.dataset.ownerPromoteBook), duration_hours: Number($('ownerPromotionDuration')?.value || settings.duration_hours || 24) }) });
      notify('Книга добавлена в честную ротацию каталога');
      await loadCatalogPromotions(clean);
      await refreshDashboard();
    }));
    document.querySelectorAll('[data-cancel-promotion]').forEach((button) => button.addEventListener('click', async () => {
      await apiFetch(`/api/control/catalog-promotions/${Number(button.dataset.cancelPromotion)}/cancel`, { method: 'POST' });
      notify('Продвижение остановлено');
      await loadCatalogPromotions(clean);
      await refreshDashboard();
    }));
  }


  function accessUserTitle(user) {
    if (!user) return 'Пользователь не выбран';
    const name = user.full_name || user.pen_name || (user.username ? `@${user.username}` : `ID ${user.telegram_id}`);
    return `${name}${user.username && name !== `@${user.username}` ? ` · @${user.username}` : ''}`;
  }

  function accessExpiryLabel(value) {
    if (!value) return 'без срока';
    return `до ${dateText(value)} UTC`;
  }

  async function renderAccessHistory() {
    const box = $('accessHistory');
    const user = state.accessUser;
    if (!box || !user) return;
    box.innerHTML = '<p class="muted">Загружаем выданные доступы…</p>';
    const data = await apiFetch(`/api/control/access/grants?user_id=${Number(user.id)}`);
    const chapters = data.grants?.chapters || [];
    const premium = data.grants?.premium || [];
    const activePremium = data.premium?.active ? `<div class="access-current-premium"><b>Premium активен</b><span>${accessExpiryLabel(data.premium.expires_at)}${data.premium.source === 'manual' ? ' · выдан вручную' : ' · оплачен пользователем'}</span></div>` : '<div class="access-current-premium inactive"><b>Premium не активен</b></div>';
    const chapterHtml = chapters.length ? chapters.map((item) => {
      const active = item.status === 'active' && (!item.expires_at || new Date(item.expires_at).getTime() > Date.now());
      return `<article class="access-grant-row${active ? '' : ' inactive'}"><div><span>${esc(item.book_title)}</span><b>Глава ${Number(item.chapter_number)} · ${esc(item.chapter_title)}</b><small>${active ? accessExpiryLabel(item.expires_at) : 'доступ завершён'}${item.note ? ` · ${esc(item.note)}` : ''}</small></div>${active ? `<button type="button" class="danger control-action" data-access-revoke="chapter:${Number(item.id)}">Отозвать</button>` : ''}</article>`;
    }).join('') : '<p class="muted">Ручных доступов к главам пока нет.</p>';
    const premiumHtml = premium.length ? premium.map((item) => {
      const active = ['active', 'canceled'].includes(item.status) && (!item.expires_at || new Date(item.expires_at).getTime() > Date.now());
      return `<article class="access-grant-row${active ? '' : ' inactive'}"><div><span>VoxLyra Premium</span><b>${active ? 'Действует' : 'Завершён'}</b><small>${accessExpiryLabel(item.expires_at)}${item.grant_note ? ` · ${esc(item.grant_note)}` : ''}</small></div>${active ? `<button type="button" class="danger control-action" data-access-revoke="premium:${Number(item.id)}">Отозвать</button>` : ''}</article>`;
    }).join('') : '<p class="muted">Premium вручную этому пользователю не выдавался.</p>';
    box.innerHTML = `${activePremium}<div class="access-history-group"><h4>Открытые главы</h4>${chapterHtml}</div><div class="access-history-group"><h4>Выданный Premium</h4>${premiumHtml}</div>`;
    box.querySelectorAll('[data-access-revoke]').forEach((button) => button.addEventListener('click', async () => {
      const [kind, id] = button.dataset.accessRevoke.split(':');
      if (!window.confirm('Отозвать этот доступ? Уже совершённые покупки пользователя не изменятся.')) return;
      button.disabled = true;
      await apiFetch(`/api/control/access/revoke/${kind}/${id}`, { method: 'POST' });
      notify('Доступ отозван');
      await renderAccessHistory();
    }));
  }

  function selectAccessUser(user) {
    state.accessUser = user;
    const card = $('accessSelectedUser');
    if (!card) return;
    card.hidden = false;
    card.innerHTML = `<div><span>Получатель</span><h3>${esc(accessUserTitle(user))}</h3><p>Telegram ID: ${Number(user.telegram_id)}${user.premium?.active ? ` · Premium до ${dateText(user.premium.expires_at)}` : ''}</p></div><button type="button" class="secondary compact-button" id="accessChangeUser">Изменить</button>`;
    $('accessUserResults').innerHTML = '';
    $('accessUserQuery').value = user.username ? `@${user.username}` : String(user.telegram_id);
    document.querySelectorAll('[data-access-needs-user]').forEach((node) => { node.hidden = false; });
    $('accessChangeUser')?.addEventListener('click', () => {
      state.accessUser = null;
      card.hidden = true;
      document.querySelectorAll('[data-access-needs-user]').forEach((node) => { node.hidden = true; });
      $('accessUserQuery')?.focus();
    });
    renderAccessHistory().catch(handleError);
  }

  function renderAccessUserResults(items) {
    const box = $('accessUserResults');
    if (!box) return;
    if (!items.length) {
      box.innerHTML = '<article class="empty-card compact-empty"><h3>Пользователь не найден</h3><p>Он должен хотя бы один раз запустить бота.</p></article>';
      return;
    }
    box.innerHTML = items.map((user) => `<button type="button" class="access-user-result" data-access-user='${esc(JSON.stringify(user))}'><span class="access-user-avatar">${esc((user.full_name || user.username || 'V').slice(0, 1))}</span><div><b>${esc(accessUserTitle(user))}</b><small>ID ${Number(user.telegram_id)}${user.is_blocked ? ' · заблокирован' : ''}${user.premium?.active ? ' · Premium активен' : ''}</small></div><i>Выбрать</i></button>`).join('');
    box.querySelectorAll('[data-access-user]').forEach((button) => button.addEventListener('click', () => selectAccessUser(JSON.parse(button.dataset.accessUser))));
  }

  async function searchAccessUsers() {
    const query = String($('accessUserQuery')?.value || '').trim();
    if (query.length < 2) { notify('Введите Telegram ID, username или имя'); return; }
    const data = await apiFetch(`/api/control/access/users?q=${encodeURIComponent(query)}`);
    renderAccessUserResults(data.items || []);
  }

  function normalizeAccessBookText(value) {
    return String(value || '').toLocaleLowerCase('ru-RU').replaceAll('ё', 'е')
      .normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
  }

  function accessBookSearchText(book) {
    return normalizeAccessBookText(`${book.id || ''} ${book.title || ''} ${book.pen_name || ''} ${book.source_author_name || ''}`);
  }

  function accessBookMatches(book, query) {
    const needle = normalizeAccessBookText(query);
    if (!needle) return true;
    if (/^\d+$/.test(needle) && Number(needle) === Number(book.id || 0)) return true;
    const haystack = accessBookSearchText(book);
    if (haystack.includes(needle)) return true;
    return needle.split(' ').filter(Boolean).every((word) => haystack.split(' ').some((candidate) => candidate === word || candidate.startsWith(word) || (word.length >= 4 && candidate.includes(word))));
  }

  function renderAccessBookOptions(query = '') {
    const select = $('accessBookSelect');
    if (!select) return;
    const needle = normalizeAccessBookText(query);
    const current = String(select.value || '');
    const items = needle
      ? state.accessBooks.filter((book) => accessBookMatches(book, needle))
      : state.accessBooks;
    select.innerHTML = '<option value="">Выберите книгу</option>' + items.map((book) => `<option value="${Number(book.id)}">#${Number(book.id)} · ${esc(book.title)} · ${Number(book.chapters_count || 0)} глав${book.pen_name ? ` · ${esc(book.pen_name)}` : ''}</option>`).join('');
    if (current && items.some((book) => String(book.id) === current)) select.value = current;
    if (needle && items.length === 1) select.value = String(items[0].id);
    const count = $('accessBookCount');
    if (count) count.textContent = needle
      ? `Найдено: ${items.length} из ${state.accessBooks.length}`
      : `Доступны все книги: ${state.accessBooks.length}`;
  }

  async function loadAccessBooks() {
    const data = await apiFetch('/api/control/access/books');
    state.accessBooks = data.items || [];
    renderAccessBookOptions($('accessBookQuery')?.value || '');
  }

  async function previewChapterGrant() {
    if (!state.accessUser) { notify('Сначала выберите пользователя'); return; }
    const bookId = Number($('accessBookSelect')?.value || 0);
    const chapterSpec = String($('accessChapterSpec')?.value || '').trim();
    if (!bookId || !chapterSpec) { notify('Выберите книгу и укажите главы'); return; }
    const data = await apiFetch('/api/control/access/chapters/preview', { method: 'POST', body: JSON.stringify({ user_id: state.accessUser.id, book_id: bookId, chapter_spec: chapterSpec }) });
    const preview = $('accessChapterPreview');
    preview.hidden = false;
    const sample = (data.chapters || []).slice(0, 12).map((chapter) => `${Number(chapter.number)}. ${esc(chapter.title)}`).join('<br>');
    preview.innerHTML = `<b>Будет открыто: ${Number(data.found_count)} глав</b><span>${esc(data.book?.title || '')} · ${esc(data.normalized)}</span>${sample ? `<small>${sample}${data.found_count > 12 ? '<br>…' : ''}</small>` : ''}${data.missing?.length ? `<em>Не найдены: ${data.missing.join(', ')}</em>` : ''}`;
    if (data.missing?.length) throw new Error('В книге отсутствуют некоторые указанные главы. Исправьте список.');
    return data;
  }

  async function grantChapters(event) {
    event.preventDefault();
    const preview = await previewChapterGrant();
    if (!preview || !state.accessUser) return;
    const form = event.currentTarget;
    const payload = {
      user_id: state.accessUser.id,
      book_id: Number(form.elements.book_id.value),
      chapter_spec: String(form.elements.chapter_spec.value || ''),
      duration_days: form.elements.duration_days.value,
      note: String(form.elements.note.value || '').trim(),
    };
    if (!window.confirm(`Открыть пользователю ${accessUserTitle(state.accessUser)} главы ${preview.normalized} книги «${preview.book.title}»?`)) return;
    const result = await apiFetch('/api/control/access/chapters/grant', { method: 'POST', body: JSON.stringify(payload) });
    notify(`Открыто глав: ${Number(result.granted || 0)}`);
    form.elements.chapter_spec.value = '';
    $('accessChapterPreview').hidden = true;
    await renderAccessHistory();
  }

  async function grantPremium(event) {
    event.preventDefault();
    if (!state.accessUser) { notify('Сначала выберите пользователя'); return; }
    const form = event.currentTarget;
    const days = Number(form.elements.duration_days.value || 0);
    if (!Number.isInteger(days) || days < 1 || days > 3650) { notify('Укажите срок от 1 до 3650 дней'); return; }
    if (!window.confirm(`Выдать ${accessUserTitle(state.accessUser)} Premium на ${days} дней? Срок добавится к уже активному Premium.`)) return;
    const result = await apiFetch('/api/control/access/premium/grant', { method: 'POST', body: JSON.stringify({ user_id: state.accessUser.id, duration_days: days, note: String(form.elements.note.value || '').trim() }) });
    notify(`Premium действует до ${dateText(result.expires_at)}`);
    state.accessUser.premium = { active: true, expires_at: result.expires_at };
    selectAccessUser(state.accessUser);
  }

  async function loadAccessManagement() {
    openWorkspace('Выдача доступа', 'Откройте платные главы при сбое или выдайте Premium. Все действия записываются в журнал.', 'Только с отдельным правом');
    $('workspaceTabs').innerHTML = '<span class="control-access-security">🔐 Доступно владельцу и назначенным сотрудникам</span>';
    $('workspaceList').innerHTML = `<section class="access-control-shell">
      <article class="access-step-card">
        <span class="access-step-number">1</span><div><h3>Найдите пользователя</h3><p>Введите Telegram ID, @username, имя или псевдоним. Пользователь должен ранее запустить бота.</p></div>
        <div class="access-search-row"><input id="accessUserQuery" type="search" placeholder="Например: 123456789 или @username" autocomplete="off"><button type="button" id="accessUserSearch">Найти</button></div>
        <div id="accessUserResults" class="access-user-results"></div>
        <div id="accessSelectedUser" class="access-selected-user" hidden></div>
      </article>
      <section class="access-grant-grid" data-access-needs-user hidden>
        <form id="accessChapterForm" class="access-step-card access-grant-card">
          <span class="access-step-number">2</span><div><span class="eyebrow">Платные главы</span><h3>Открыть главы вручную</h3><p>Поддерживаются отдельные номера и диапазоны: <b>33</b>, <b>56-67</b>, <b>98 34,36,38</b>, <b>1-100</b>.</p></div>
          <label>Поиск книги<input id="accessBookQuery" type="search" placeholder="Название, автор или ID книги" autocomplete="off"></label>
          <small id="accessBookCount" class="access-book-count">Загружаем полный список книг…</small>
          <label>Книга<select id="accessBookSelect" name="book_id" required><option value="">Загружаем книги…</option></select></label>
          <label>Главы<input id="accessChapterSpec" name="chapter_spec" required placeholder="33, 56-67, 98 34 36 38"></label>
          <div class="form-grid two"><label>Срок<select name="duration_days"><option value="0">Без ограничения</option><option value="1">1 день</option><option value="7">7 дней</option><option value="30">30 дней</option><option value="90">90 дней</option><option value="365">1 год</option></select></label><label>Причина<input name="note" maxlength="500" placeholder="Например: восстановление после сбоя"></label></div>
          <div id="accessChapterPreview" class="access-chapter-preview" hidden></div>
          <div class="form-actions"><button type="button" class="secondary" id="accessPreviewChapters">Проверить список</button><button type="submit" class="approve">Открыть главы</button></div>
        </form>
        <form id="accessPremiumForm" class="access-step-card access-grant-card premium-grant-card">
          <span class="access-step-number">3</span><div><span class="eyebrow">VoxLyra Premium</span><h3>Выдать или продлить Premium</h3><p>Новый срок добавляется к действующей подписке. Оплаченные пользователем периоды не удаляются.</p></div>
          <label>Срок Premium<select name="duration_days"><option value="1">1 день</option><option value="7">7 дней</option><option value="30" selected>30 дней</option><option value="90">90 дней</option><option value="180">180 дней</option><option value="365">1 год</option></select></label>
          <label>Причина<input name="note" maxlength="500" placeholder="Подарок, компенсация или технический сбой"></label>
          <button type="submit" class="premium-subscription-button">Выдать Premium</button>
        </form>
      </section>
      <article class="access-step-card access-history-card" data-access-needs-user hidden><div><span class="eyebrow">История</span><h3>Активные и завершённые выдачи</h3><p>Ручной доступ можно отозвать, не затрагивая обычные покупки.</p></div><div id="accessHistory"></div></article>
    </section>`;
    $('accessUserSearch')?.addEventListener('click', () => searchAccessUsers().catch(handleError));
    $('accessUserQuery')?.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); searchAccessUsers().catch(handleError); } });
    $('accessBookQuery')?.addEventListener('input', (event) => renderAccessBookOptions(event.currentTarget.value));
    $('accessBookQuery')?.addEventListener('search', (event) => renderAccessBookOptions(event.currentTarget.value));
    $('accessPreviewChapters')?.addEventListener('click', () => previewChapterGrant().catch(handleError));
    $('accessChapterForm')?.addEventListener('submit', (event) => grantChapters(event).catch(handleError));
    $('accessPremiumForm')?.addEventListener('submit', (event) => grantPremium(event).catch(handleError));
    await loadAccessBooks();
  }

  async function refreshDashboard() {
    state.dashboard = await apiFetch('/api/control/dashboard');
    renderDashboard(state.dashboard);
  }

  async function performAction(action) {
    const [kind, verb, id] = action.split(':');
    if (!kind || !verb || !id) return;
    if (kind === 'ttssample' && verb === 'play') {
      await playTtsSample(id);
      return;
    }
    if (kind === 'ttsbenchmark' && verb === 'run') {
      notify('Проверяем пять голосов. Это может занять несколько минут.');
      await apiFetch('/api/control/tts-vosk/benchmark', { method: 'POST' });
      notify('Проверка голосов завершена');
      await loadTtsDiagnostics();
      return;
    }
    if (kind === 'ttsvoice' && ['female', 'male'].includes(verb)) {
      await apiFetch('/api/control/tts-vosk/selection', { method: 'PATCH', body: JSON.stringify({ gender: verb, speaker_id: Number(id) }) });
      notify(verb === 'female' ? 'Женский голос сохранён' : 'Мужской голос сохранён');
      await loadTtsDiagnostics();
      return;
    }
    if (kind === 'book' && verb === 'details') {
      await openBookModeration(Number(id));
      return;
    }
    if (kind === 'librarybatch' && verb === 'details') {
      await openLibraryBatch(Number(id));
      return;
    }
    if (kind === 'librarybatch' && verb === 'back') {
      await loadLibraryImport();
      return;
    }
    let url = '';
    let body = undefined;
    if (kind === 'book') {
      url = `/api/control/book/${id}/${verb}`;
      if (verb === 'reject') {
        const reason = window.prompt('Что автору нужно исправить? Напишите понятную причину возврата на доработку.');
        if (reason === null) return;
        if (reason.trim().length < 8) { notify('Причина слишком короткая'); return; }
        body = JSON.stringify({ reason: reason.trim() });
      }
    }
    if (kind === 'comment' || kind === 'review') url = `/api/control/${kind}/${id}/hide`;
    if (kind === 'graphiccomment') url = `/api/control/graphic-comment/${id}/${verb}`;
    if (kind === 'graphicpage') {
      url = `/api/control/graphic-page/${id}/${verb}`;
      if (verb === 'reject') {
        const note = window.prompt('Почему страницу нужно скрыть? Причина будет сохранена для проверки автора.');
        if (note === null) return;
        if (note.trim().length < 5) { notify('Причина слишком короткая'); return; }
        body = JSON.stringify({ note: note.trim() });
      }
    }
    if (kind === 'complaint') url = `/api/control/complaint/${id}/${verb}`;
    if (kind === 'refund') {
      url = `/api/control/refund/${id}/${verb}`;
      if (verb === 'reject') body = JSON.stringify({ note: 'Отклонено после проверки' });
    }
    if (kind === 'payout') url = `/api/control/payout/${id}/${verb}`;
    if (kind === 'rubprofile') {
      url = `/api/control/rub-profile/${id}/${verb}`;
      if (['reject', 'block'].includes(verb)) {
        const reason = window.prompt('Укажите понятную причину для автора.');
        if (reason === null) return;
        if (reason.trim().length < 8) { notify('Причина слишком короткая'); return; }
        body = JSON.stringify({ reason: reason.trim() });
      }
    }
    if (kind === 'rubpayout' && verb === 'execute') url = `/api/control/rub-payout/${id}/execute`;
    if (kind === 'libraryjob' && ['cancel', 'retry'].includes(verb)) url = `/api/control/library-import/job/${id}/${verb}`;
    if (kind === 'librarybatch' && ['audit', 'publish', 'rollback'].includes(verb)) {
      url = `/api/control/library-import/batch/${id}/${verb}`;
      if (verb === 'rollback') body = JSON.stringify({ confirm: true });
    }
    if (kind === 'libraryduplicate' && ['skip', 'replace'].includes(verb)) {
      url = `/api/control/library-import/duplicate/${id}`;
      body = JSON.stringify({ action: verb });
    }
    if (!url) return;

    const dangerous = ['reject', 'freeze', 'approve', 'paid', 'publish', 'closed', 'hide', 'block', 'execute', 'rollback', 'replace', 'cancel'].includes(verb);
    const tg = window.Telegram?.WebApp;
    const proceed = async () => {
      await apiFetch(url, { method: 'POST', body });
      notify('Готово');
      if (state.active === 'books') await loadBooks(state.bookQuery);
      if (state.active === 'graphic_pages') await loadGraphicPages();
      if (state.active === 'comments') await loadComments();
      if (state.active === 'complaints') await loadComplaints();
      if (state.active === 'refunds') await loadRefunds();
      if (state.active === 'payouts') await loadPayouts();
      if (state.active === 'rub_profiles') await loadRubProfiles();
      if (state.active === 'rub_payouts') await loadRubPayouts();
      if (state.active === 'library_import') {
        if (kind === 'librarybatch') await openLibraryBatch(Number(id));
        else if (kind === 'libraryduplicate' && state.libraryBatchId) await openLibraryBatch(state.libraryBatchId);
        else await loadLibraryImport();
      }
      await refreshDashboard();
    };
    if (dangerous && tg?.showConfirm) {
      tg.showConfirm('Подтвердить действие?', (ok) => { if (ok) proceed().catch(handleError); });
    } else if (!dangerous || window.confirm('Подтвердить действие?')) {
      await proceed();
    }
  }

  function handleError(error) {
    notify(error?.message || 'Не удалось выполнить действие');
  }

  document.addEventListener('click', (event) => {
    const section = event.target.closest('[data-section]');
    if (section) {
      state.active = section.dataset.section;
      const loaders = { books: loadBooks, graphic_pages: loadGraphicPages, comments: loadComments, complaints: loadComplaints, refunds: loadRefunds, payouts: loadPayouts, rub_profiles: loadRubProfiles, rub_payouts: loadRubPayouts, tts: loadTtsDiagnostics, payments: loadPaymentSettings, premium: loadPremiumSettings, achievements: loadAchievementSettings, catalog_promotions: loadCatalogPromotions, access: loadAccessManagement, library_import: loadLibraryImport };
      loaders[state.active]?.().catch(handleError);
      return;
    }
    const action = event.target.closest('[data-action]');
    if (action) performAction(action.dataset.action).catch(handleError);
  });

  $('closeWorkspace')?.addEventListener('click', () => {
    if (state.libraryRefreshTimer) {
      window.clearTimeout(state.libraryRefreshTimer);
      state.libraryRefreshTimer = null;
    }
    $('controlWorkspace').hidden = true;
    state.active = '';
  });

  apiFetch('/api/control/dashboard').then((data) => {
    renderDashboard(data);
    const requested = new URLSearchParams(window.location.search).get('section');
    if (requested === 'books' && (data.role === 'owner' || can('mod_books'))) {
      state.active = 'books';
      loadBooks().catch(handleError);
    }
    if (requested === 'payments' && data.role === 'owner') {
      state.active = 'payments';
      loadPaymentSettings().catch(handleError);
    }
    if (requested === 'tts' && data.role === 'owner') {
      state.active = 'tts';
      loadTtsDiagnostics().catch(handleError);
    }
    if (requested === 'premium' && data.role === 'owner') {
      state.active = 'premium';
      loadPremiumSettings().catch(handleError);
    }
    if (requested === 'achievements' && data.role === 'owner') {
      state.active = 'achievements';
      loadAchievementSettings().catch(handleError);
    }
    if (requested === 'catalog_promotions' && data.role === 'owner') {
      state.active = 'catalog_promotions';
      loadCatalogPromotions().catch(handleError);
    }
    if (requested === 'access' && can('grant_access')) {
      state.active = 'access';
      loadAccessManagement().catch(handleError);
    }
    if (requested === 'library_import' && (can('library_bulk_import') || can('library_import_manage'))) {
      state.active = 'library_import';
      loadLibraryImport().catch(handleError);
    }
  }).catch((error) => showError(error.message));
})();
