(() => {
  const state = { permissions: new Set(), role: '', dashboard: null, active: '', accessUser: null, accessBooks: [] };
  const $ = (id) => document.getElementById(id);
  const can = (code) => state.role === 'owner' || state.permissions.has(code);
  const esc = (value) => escapeHtml(value ?? '');
  const dateText = (value) => value ? String(value).replace('T', ' ').slice(0, 16) : '';
  const rubText = (minor) => `${(Number(minor || 0) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`;

  const actionButton = (label, action, kind = '') =>
    `<button class="control-action ${kind}" type="button" data-action="${esc(action)}">${esc(label)}</button>`;
  const actionLink = (label, href, kind = '') =>
    `<a class="control-action ${kind}" href="${esc(href)}">${esc(label)}</a>`;

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
      sections.push(sectionButton('books', 'Книги', 'Проверка и публикация', q.books_review));
      sections.push(sectionButton('graphic_pages', 'Страницы комиксов', 'Жалобы и постраничная проверка', q.graphic_page_reports));
    }
    if (can('mod_comments')) sections.push(sectionButton('comments', 'Отзывы и комментарии', 'Скрытие нарушений', (q.comments || 0) + (q.reviews || 0) + (q.graphic_page_comments || 0)));
    if (can('complaints')) sections.push(sectionButton('complaints', 'Жалобы', 'Рассмотрение обращений', q.complaints_new));
    if (can('refunds')) sections.push(sectionButton('refunds', 'Возвраты', 'Проверка покупок', q.refunds_new));
    if (can('payouts')) {
      sections.push(sectionButton('payouts', 'Выплаты авторам', 'Stars и точная сумма в рублях', (q.payouts_new || 0) + (q.payouts_approved || 0)));
    }
    if (can('grant_access')) sections.push(sectionButton('access', 'Выдать доступ', 'Главы и Premium по ID или username', 0));
    if (data.role === 'owner') {
      sections.push(sectionButton('tts', 'Озвучивание', 'Движки, голоса и очередь', 0));
      sections.push(sectionButton('payments', 'Stars и курсы', 'Оплата, расчёты автора и защита', 0));
      sections.push(sectionButton('premium', 'Premium', 'Цена, включение и статистика подписки', data.premium?.active_users || 0));
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

  async function loadBooks() {
    openWorkspace('Книги на проверке', 'Публикуйте готовые книги или возвращайте автору на доработку.');
    $('workspaceTabs').innerHTML = '';
    const data = await apiFetch('/api/control/books');
    const items = data.items || [];
    if (!items.length) return emptyList('Очередь пуста', 'Новых книг на проверке нет.');
    $('workspaceList').innerHTML = items.map((item) => `<article class="control-item" data-id="${item.id}">
      <div class="control-item-main"><span>${item.content_type && item.content_type !== 'book' ? 'Графическое произведение' : 'Книга'} #${item.id}</span><h3>${esc(item.title)}</h3><p>${esc(item.pen_name || 'Автор')} · ${esc(item.age_limit || '')}</p><small>${esc((item.description || '').slice(0, 240))}</small></div>
      <div class="control-actions">${item.first_graphic_chapter_id ? actionLink('Проверить страницы', `/comic/${Number(item.first_graphic_chapter_id)}?moderation=1`) : item.first_chapter_id ? actionLink('Читать книгу', `/reader/${Number(item.first_chapter_id)}?moderation=1`) : ''}${actionButton('Опубликовать', `book:publish:${item.id}`, 'approve')}${actionButton('На доработку', `book:reject:${item.id}`, 'danger')}</div>
    </article>`).join('');
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

  async function loadAccessBooks(query = '') {
    const data = await apiFetch(`/api/control/access/books?q=${encodeURIComponent(query)}`);
    state.accessBooks = data.items || [];
    const select = $('accessBookSelect');
    if (!select) return;
    select.innerHTML = '<option value="">Выберите книгу</option>' + state.accessBooks.map((book) => `<option value="${Number(book.id)}">${esc(book.title)} · ${Number(book.chapters_count || 0)} глав${book.pen_name ? ` · ${esc(book.pen_name)}` : ''}</option>`).join('');
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
    if (!url) return;

    const dangerous = ['reject', 'freeze', 'approve', 'paid', 'publish', 'closed', 'hide', 'block', 'execute'].includes(verb);
    const tg = window.Telegram?.WebApp;
    const proceed = async () => {
      await apiFetch(url, { method: 'POST', body });
      notify('Готово');
      if (state.active === 'books') await loadBooks();
      if (state.active === 'graphic_pages') await loadGraphicPages();
      if (state.active === 'comments') await loadComments();
      if (state.active === 'complaints') await loadComplaints();
      if (state.active === 'refunds') await loadRefunds();
      if (state.active === 'payouts') await loadPayouts();
      if (state.active === 'rub_profiles') await loadRubProfiles();
      if (state.active === 'rub_payouts') await loadRubPayouts();
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
      const loaders = { books: loadBooks, graphic_pages: loadGraphicPages, comments: loadComments, complaints: loadComplaints, refunds: loadRefunds, payouts: loadPayouts, rub_profiles: loadRubProfiles, rub_payouts: loadRubPayouts, tts: loadTtsDiagnostics, payments: loadPaymentSettings, premium: loadPremiumSettings, access: loadAccessManagement };
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

  apiFetch('/api/control/dashboard').then((data) => {
    renderDashboard(data);
    const requested = new URLSearchParams(window.location.search).get('section');
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
    if (requested === 'access' && can('grant_access')) {
      state.active = 'access';
      loadAccessManagement().catch(handleError);
    }
  }).catch((error) => showError(error.message));
})();
