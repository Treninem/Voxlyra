(() => {
  const state = { permissions: new Set(), role: '', dashboard: null, active: '' };
  const $ = (id) => document.getElementById(id);
  const can = (code) => state.role === 'owner' || state.permissions.has(code);
  const esc = (value) => escapeHtml(value ?? '');
  const dateText = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '';

  const actionButton = (label, action, kind = '') =>
    `<button class="control-action ${kind}" type="button" data-action="${esc(action)}">${esc(label)}</button>`;

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
      stats.push(statCard('Оборот', finance.paid_gross || 0, 'Stars'));
      stats.push(statCard('Комиссия платформы', finance.platform_commission || 0, 'Stars'));
      stats.push(statCard('Удерживается авторам', finance.held_authors || 0, 'Stars'));
      stats.push(statCard('К выплате', finance.available_authors || 0, 'Stars'));
    }
    $('controlStats').innerHTML = stats.join('');

    const sections = [];
    if (can('mod_books')) sections.push(sectionButton('books', 'Книги', 'Проверка и публикация', q.books_review));
    if (can('mod_comments')) sections.push(sectionButton('comments', 'Отзывы и комментарии', 'Скрытие нарушений', (q.comments || 0) + (q.reviews || 0)));
    if (can('complaints')) sections.push(sectionButton('complaints', 'Жалобы', 'Рассмотрение обращений', q.complaints_new));
    if (can('refunds')) sections.push(sectionButton('refunds', 'Возвраты', 'Проверка покупок', q.refunds_new));
    if (can('payouts')) sections.push(sectionButton('payouts', 'Выплаты авторам', 'Одобрение и завершение', (q.payouts_new || 0) + (q.payouts_approved || 0)));
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

  async function loadBooks() {
    openWorkspace('Книги на проверке', 'Публикуйте готовые книги или возвращайте автору на доработку.');
    $('workspaceTabs').innerHTML = '';
    const data = await apiFetch('/api/control/books');
    const items = data.items || [];
    if (!items.length) return emptyList('Очередь пуста', 'Новых книг на проверке нет.');
    $('workspaceList').innerHTML = items.map((item) => `<article class="control-item" data-id="${item.id}">
      <div class="control-item-main"><span>Книга #${item.id}</span><h3>${esc(item.title)}</h3><p>${esc(item.pen_name || 'Автор')} · ${esc(item.age_limit || '')}</p><small>${esc((item.description || '').slice(0, 240))}</small></div>
      <div class="control-actions">${actionButton('Опубликовать', `book:publish:${item.id}`, 'approve')}${actionButton('На доработку', `book:reject:${item.id}`, 'danger')}</div>
    </article>`).join('');
  }

  async function loadComments() {
    openWorkspace('Отзывы и комментарии', 'Скрывайте только материалы, нарушающие правила.');
    $('workspaceTabs').innerHTML = '<button class="active" type="button" data-content-tab="comments">Комментарии</button><button type="button" data-content-tab="reviews">Отзывы</button>';
    const data = await apiFetch('/api/control/comments');
    const render = (kind) => {
      const items = kind === 'comments' ? data.comments || [] : data.reviews || [];
      if (!items.length) return emptyList('Здесь спокойно', kind === 'comments' ? 'Новых комментариев нет.' : 'Новых отзывов нет.');
      $('workspaceList').innerHTML = items.map((item) => `<article class="control-item" data-id="${item.id}">
        <div class="control-item-main"><span>${kind === 'comments' ? 'Комментарий' : 'Отзыв'} #${item.id}</span><h3>${esc(item.book_title || 'Книга')}</h3><p>${esc(item.username ? '@' + item.username : item.full_name || 'Читатель')}${kind === 'reviews' ? ` · ${Number(item.rating || 0)}★` : ''}</p><small>${esc(item.text || 'Без текста')}</small></div>
        <div class="control-actions">${actionButton('Скрыть', `${kind === 'comments' ? 'comment' : 'review'}:hide:${item.id}`, 'danger')}</div>
      </article>`).join('');
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

  async function refreshDashboard() {
    state.dashboard = await apiFetch('/api/control/dashboard');
    renderDashboard(state.dashboard);
  }

  async function performAction(action) {
    const [kind, verb, id] = action.split(':');
    if (!kind || !verb || !id) return;
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
    if (kind === 'complaint') url = `/api/control/complaint/${id}/${verb}`;
    if (kind === 'refund') {
      url = `/api/control/refund/${id}/${verb}`;
      if (verb === 'reject') body = JSON.stringify({ note: 'Отклонено после проверки' });
    }
    if (kind === 'payout') url = `/api/control/payout/${id}/${verb}`;
    if (!url) return;

    const dangerous = ['reject', 'freeze', 'approve', 'paid', 'publish', 'closed', 'hide'].includes(verb);
    const tg = window.Telegram?.WebApp;
    const proceed = async () => {
      await apiFetch(url, { method: 'POST', body });
      notify('Готово');
      if (state.active === 'books') await loadBooks();
      if (state.active === 'comments') await loadComments();
      if (state.active === 'complaints') await loadComplaints();
      if (state.active === 'refunds') await loadRefunds();
      if (state.active === 'payouts') await loadPayouts();
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
      const loaders = { books: loadBooks, comments: loadComments, complaints: loadComplaints, refunds: loadRefunds, payouts: loadPayouts };
      loaders[state.active]?.().catch(handleError);
      return;
    }
    const action = event.target.closest('[data-action]');
    if (action) performAction(action.dataset.action).catch(handleError);
  });

  $('closeWorkspace')?.addEventListener('click', () => {
    $('controlWorkspace').hidden = true;
    state.active = '';
  });

  apiFetch('/api/control/dashboard').then(renderDashboard).catch((error) => showError(error.message));
})();
