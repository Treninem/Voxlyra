(() => {
  const state = { permissions: new Set(), role: '', dashboard: null, active: '' };
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
      stats.push(statCard('Оборот', finance.paid_gross || 0, 'Stars'));
      stats.push(statCard('Комиссия платформы', finance.platform_commission || 0, 'Stars'));
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
          <div class="payment-rate-example"><b>Пример для 10 Stars и комиссии 20%</b><p id="paymentRateExample"></p></div>
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
    const renderExample = () => {
      const buyer = Math.max(2, Number(form.elements.buyer_star_rate_minor.value || 145));
      const author = Math.max(1, Number(form.elements.author_star_rate_minor.value || 100));
      const buyerRub = (10 * buyer / 100).toFixed(2);
      const authorRub = (8 * author / 100).toFixed(2);
      $('paymentRateExample').textContent = `Покупателю показывается ориентир ${buyerRub} ₽. Комиссия — 2 Stars. Автору начисляется 8 Stars = ${authorRub} ₽.`;
    };
    form?.addEventListener('input', renderExample);
    renderExample();
    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const buyer = Math.max(2, Number(form.elements.buyer_star_rate_minor.value || 145));
      const author = Math.max(1, Number(form.elements.author_star_rate_minor.value || 100));
      if (buyer <= author) { notify('Курс покупателя должен быть выше курса автора'); return; }
      const payload = {
        stars_enabled: Boolean(form.elements.stars_enabled.checked),
        content_protection_enabled: Boolean(form.elements.content_protection_enabled.checked),
        watermark_enabled: Boolean(form.elements.watermark_enabled.checked),
        buyer_star_rate_minor: buyer,
        author_star_rate_minor: author,
        purchase_cancel_minutes: Math.max(1, Math.min(120, Number(form.elements.purchase_cancel_minutes.value || 15))),
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
        </div>
      </article>
      <div class="control-stat-grid premium-control-summary">
        ${statCard('Активные', summary.active_users || 0, 'пользователей')}
        ${statCard('Автопродление', summary.auto_renew || 0, 'подписок')}
        ${statCard('Оплаты', summary.payments || 0)}
        ${statCard('Оборот', summary.gross_stars || 0, 'Stars')}
      </div>
      <div class="control-actions payment-settings-actions"><button type="submit" class="approve">Сохранить Premium</button></div>
    </form>`;
    const form = $('premiumSettingsForm');
    form?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {
        enabled: Boolean(form.elements.enabled.checked),
        price_stars: Math.max(1, Math.min(10000, Number(form.elements.price_stars.value || 99))),
      };
      await apiFetch('/api/control/premium', { method: 'PATCH', body: JSON.stringify(payload) });
      notify('Настройки Premium сохранены');
      await loadPremiumSettings();
      await refreshDashboard();
    });
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
      const loaders = { books: loadBooks, graphic_pages: loadGraphicPages, comments: loadComments, complaints: loadComplaints, refunds: loadRefunds, payouts: loadPayouts, rub_profiles: loadRubProfiles, rub_payouts: loadRubPayouts, tts: loadTtsDiagnostics, payments: loadPaymentSettings, premium: loadPremiumSettings };
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
  }).catch((error) => showError(error.message));
})();
