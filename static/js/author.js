let authorState = {
  dashboard: null,
  book: null,
  previewToken: null,
  duplicateMatches: [],
  graphicUploadTab: 'file',
  graphicPages: [],
  graphicPageChapterId: null,
  graphicPageReplacementId: null,
  graphicDraggedPageId: null,
  graphicAdvancedPageId: null,
  sbpBanksLoaded: false,
  chapterPage: 1,
};

const AUTHOR_CHAPTERS_PER_PAGE = 100;

const statusLabels = {
  draft: 'Черновик', review: 'На проверке', published: 'Опубликовано', rejected: 'Нужны изменения', deleted: 'Удалено',
};
const writingLabels = { writing: 'Пишется', finished: 'Завершено', frozen: 'Заморожено' };
const contentTypeLabels = {
  book: 'Книга', comic: 'Комикс', manga: 'Манга', manhwa: 'Манхва', webtoon: 'Вебтун', graphic_novel: 'Графический роман',
};
const readingModeLabels = {
  inherit: 'Как у произведения', ltr: 'Слева направо', rtl: 'Справа налево', vertical: 'Вертикальная лента', single: 'Одна страница', spread: 'Разворот',
};
const graphicTypes = new Set(['comic', 'manga', 'manhwa', 'webtoon', 'graphic_novel']);

function formatStars(value) { return `${Number(value || 0).toLocaleString('ru-RU')} Stars`; }
function formatRubMinor(value) { return `${(Number(value || 0) / 100).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₽`; }
function isGraphicType(value) { return graphicTypes.has(String(value || 'book')); }
function defaultReadingMode(contentType) {
  if (contentType === 'manga') return 'rtl';
  if (contentType === 'manhwa' || contentType === 'webtoon') return 'vertical';
  return 'ltr';
}

function currentTextPricingMode(data = authorState.book) {
  const book = data?.book || {};
  const declared = String(data?.pricing?.mode || book.pricing_type || 'free');
  if (declared === 'premium') return 'premium';
  if (declared === 'chapters' && Number(book.price_stars || 0) > 0) return 'chapters';
  if (declared === 'whole_book' && Number(book.price_stars || 0) > 0) return 'whole_book';
  return 'free';
}

function chapterAccessMode(chapter, mode = currentTextPricingMode()) {
  if (mode === 'free' || Number(chapter?.is_free || 0) === 1) return 'free';
  if (mode === 'premium') return 'premium';
  if (mode === 'chapters' && Number(chapter?.price_stars || 0) > 0) return 'chapter';
  return 'book';
}

function syncChapterAccessInputs() {
  const mode = currentTextPricingMode();
  const bulkAccess = document.getElementById('chapterBulkAccessInput');
  const singleAccess = document.getElementById('chapterAccessInput');
  [bulkAccess, singleAccess].forEach((select) => {
    if (!select) return;
    const bookOption = select.querySelector('option[value="book"]');
    const chapterOption = select.querySelector('option[value="chapter"]');
    const premiumOption = select.querySelector('option[value="premium"]');
    if (bookOption) bookOption.disabled = !['whole_book', 'chapters'].includes(mode);
    if (chapterOption) chapterOption.disabled = mode !== 'chapters';
    if (premiumOption) premiumOption.disabled = mode !== 'premium';
    if (mode === 'premium' && !['free', 'premium'].includes(select.value)) select.value = 'premium';
    if (mode === 'whole_book' && !['free', 'book'].includes(select.value)) select.value = 'book';
    if (mode === 'chapters' && !['free', 'book', 'chapter'].includes(select.value)) select.value = 'book';
    if (mode === 'free') select.value = 'free';
  });
  const bulkPrice = document.getElementById('chapterBulkPriceLabel');
  const singlePrice = document.getElementById('chapterPriceLabel');
  if (bulkPrice) bulkPrice.hidden = !bulkAccess || bulkAccess.value !== 'chapter' || mode !== 'chapters';
  if (singlePrice) singlePrice.hidden = !singleAccess || singleAccess.value !== 'chapter' || mode !== 'chapters';
}

function syncTextPricingControls() {
  const data = authorState.book || {};
  const book = data.book || {};
  const graphic = isGraphicType(String(book.content_type || 'book'));
  const price = Math.max(0, Number(book.price_stars || 0));
  const mode = currentTextPricingMode(data);
  const modeInput = document.getElementById('bookPricingModeInput');
  const priceLabel = document.getElementById('bookPriceLabel');
  const paidOptions = document.getElementById('bookPaidPricingOptions');
  const summary = document.getElementById('bookPricingSummary');
  const hint = document.getElementById('bookPricingModeHint');
  const restore = document.getElementById('restoreChapterPricesButton');
  if (modeInput) modeInput.value = mode;
  if (priceLabel) priceLabel.hidden = graphic || !['whole_book', 'chapters'].includes(mode);
  if (paidOptions) paidOptions.hidden = graphic;
  if (hint) hint.textContent = mode === 'premium'
    ? 'Первые ознакомительные главы можно оставить бесплатными. Остальные откроются только при активной подписке Premium; отдельной покупки нет.'
    : mode === 'chapters'
      ? 'На карточке показывается цена всей книги, а цена отдельной главы — только возле этой главы.'
      : mode === 'whole_book'
        ? 'Закрытые главы открываются после покупки всей книги и не продаются отдельно.'
        : 'Вся книга и все её главы открыты без оплаты.';
  if (summary) summary.textContent = mode === 'premium'
    ? 'Книга доступна по VoxLyra Premium. Бесплатные ознакомительные главы отмечаются отдельно.'
    : mode === 'chapters'
      ? `Вся книга: ${formatStars(price)}. Выбранные главы можно продавать отдельно.`
      : mode === 'whole_book'
        ? `Вся книга: ${formatStars(price)}. Отдельная продажа глав выключена.`
        : 'Книга и все главы бесплатны.';
  if (restore) restore.hidden = graphic || mode !== 'chapters' || Number(data.pricing?.saved_prices_count || 0) <= 0;

  const freeNotice = document.getElementById('freeBookChapterNotice');
  const bulkForm = document.getElementById('chapterBulkPriceForm');
  const description = document.getElementById('textChapterPricingDescription');
  if (freeNotice) freeNotice.hidden = graphic || mode !== 'free';
  if (bulkForm) bulkForm.hidden = graphic || mode === 'free';
  if (description) description.textContent = mode === 'premium'
    ? 'Главы можно оставить ознакомительными или открыть по активной подписке Premium.'
    : mode === 'free'
      ? 'Все главы бесплатны. Настройки доступа не требуются.'
      : mode === 'chapters'
        ? 'Главы можно открыть бесплатно, после покупки всей книги или продавать отдельно.'
        : 'Главы можно открыть бесплатно для ознакомления либо после покупки всей книги.';

  const importTitle = document.getElementById('textImportPricingTitle');
  const importText = document.getElementById('textImportPricingText');
  if (importTitle && importText) {
    if (mode === 'free') {
      importTitle.textContent = 'После импорта все главы будут бесплатными';
      importText.textContent = 'Дополнительные настройки доступа не требуются.';
    } else if (mode === 'premium') {
      importTitle.textContent = 'После импорта первые 3 главы будут ознакомительными';
      importText.textContent = 'Остальные главы будут доступны читателям с активной подпиской VoxLyra Premium.';
    } else {
      importTitle.textContent = 'После импорта первые 3 главы будут ознакомительными';
      importText.textContent = mode === 'chapters'
        ? 'Остальные главы сначала откроются после покупки всей книги. Отдельные цены назначаются затем для одной главы или диапазона.'
        : 'Остальные главы будут доступны после покупки всей книги и не станут продаваться отдельно.';
    }
  }

  const packageManager = document.getElementById('chapterPackageManager');
  if (packageManager) packageManager.hidden = graphic ? false : mode !== 'chapters';
  syncChapterAccessInputs();
}

function syncBookPricingDraftControls() {
  const priceInput = document.getElementById('bookPriceInput');
  const modeInput = document.getElementById('bookPricingModeInput');
  const priceLabel = document.getElementById('bookPriceLabel');
  const summary = document.getElementById('bookPricingSummary');
  const hint = document.getElementById('bookPricingModeHint');
  if (!priceInput || !modeInput) return;
  const mode = modeInput.value || 'free';
  const price = Math.max(1, Number(priceInput.value || 1));
  const graphic = isGraphicType(document.getElementById('bookContentTypeInput')?.value || authorState.book?.book?.content_type);
  if (priceLabel) priceLabel.hidden = graphic || !['whole_book', 'chapters'].includes(mode);
  if (summary) summary.textContent = mode === 'premium'
    ? 'Книга будет доступна по VoxLyra Premium без отдельной покупки.'
    : mode === 'free'
      ? 'Книга и все главы будут бесплатны.'
      : mode === 'chapters'
        ? `Вся книга: ${formatStars(price)}. Можно назначить отдельные цены выбранным главам.`
        : `Вся книга: ${formatStars(price)}. Главы отдельно не продаются.`;
  if (hint) hint.textContent = mode === 'premium'
    ? 'Ознакомительные главы можно оставить бесплатными, остальные будут отмечены значком Premium.'
    : mode === 'chapters'
      ? 'Цена всей книги показывается на карточке, цена главы — только возле этой главы.'
      : mode === 'whole_book'
        ? 'Закрытые главы открываются только после покупки всей книги.'
        : 'Никаких платёжных кнопок у книги и глав не будет.';
}

const financeStatusLabels = {
  pending: 'На проверке', verified: 'Проверен', rejected: 'Нужны исправления', blocked: 'Заблокирован', missing: 'Не заполнен',
};

function setFinanceStatus(status) {
  const badge = document.getElementById('authorFinancialProfileStatus');
  if (!badge) return;
  const value = financeStatusLabels[status] ? status : 'missing';
  badge.textContent = financeStatusLabels[value];
  badge.className = `finance-status ${value}`;
}

async function loadAuthorSbpBanks(savedBankId = '', savedBankName = '', configured = false) {
  const select = document.getElementById('authorSbpBank');
  const hiddenName = document.getElementById('authorSbpBankName');
  if (!select) return;
  const preserved = savedBankId ? `<option value="${escapeHtml(savedBankId)}" selected>${escapeHtml(savedBankName || 'Сохранённый банк')}</option>` : '';
  if (!configured) {
    select.innerHTML = `${preserved}<option value="">ЮKassa ещё не подключена владельцем</option>`;
    select.disabled = true;
    if (hiddenName) hiddenName.value = savedBankName || '';
    return;
  }
  select.disabled = true;
  select.innerHTML = `${preserved}<option value="">Загружаем участников СБП…</option>`;
  try {
    const response = await apiFetch('/api/author/sbp-banks');
    const items = Array.isArray(response.items) ? response.items : [];
    const seen = new Set();
    const options = [];
    if (savedBankId) {
      seen.add(String(savedBankId));
      options.push(`<option value="${escapeHtml(savedBankId)}" selected>${escapeHtml(savedBankName || 'Сохранённый банк')}</option>`);
    }
    items.forEach((bank) => {
      const id = String(bank.bank_id || '');
      if (!id || seen.has(id)) return;
      seen.add(id);
      options.push(`<option value="${escapeHtml(id)}">${escapeHtml(bank.name || id)}</option>`);
    });
    select.innerHTML = `<option value="">Выберите банк или платёжный сервис</option>${options.join('')}`;
    if (savedBankId) select.value = String(savedBankId);
    select.disabled = false;
    const chosen = select.options[select.selectedIndex];
    if (hiddenName) hiddenName.value = chosen?.value ? chosen.textContent.trim() : (savedBankName || '');
    authorState.sbpBanksLoaded = true;
  } catch (error) {
    select.innerHTML = `${preserved}<option value="">Не удалось загрузить банки</option>`;
    select.disabled = !savedBankId;
    notify(error.message || 'Не удалось загрузить список банков СБП');
  }
}

function renderAuthorFinancialArea(data) {
  const profile = data.financial_profile || null;
  const finance = data.rub_finance || {};
  const policy = data.pricing_policy || {};
  const status = profile?.verification_status || 'missing';
  setFinanceStatus(status);

  const legalStatus = document.getElementById('authorLegalStatus');
  const legalName = document.getElementById('authorLegalName');
  const inn = document.getElementById('authorInn');
  const ogrn = document.getElementById('authorOgrn');
  const phone = document.getElementById('authorSbpPhone');
  if (legalStatus) legalStatus.value = profile?.legal_status || '';
  if (legalName) legalName.value = profile?.legal_name || '';
  if (inn) inn.value = profile?.inn || '';
  if (ogrn) ogrn.value = profile?.ogrn || '';
  if (phone) {
    phone.value = '';
    phone.required = !profile;
    phone.placeholder = profile?.sbp_phone_masked ? `Сохранён: ${profile.sbp_phone_masked}` : '+7 900 000-00-00';
  }
  const own = document.getElementById('authorOwnsSbpAccount');
  if (own) own.checked = false;
  const hint = document.getElementById('authorFinancialProfileHint');
  if (hint) {
    if (status === 'verified') hint.textContent = 'Профиль проверен. Любое изменение реквизитов отправит его на повторную проверку.';
    else if (status === 'rejected') hint.textContent = `Нужно исправить профиль${profile?.rejection_reason ? `: ${profile.rejection_reason}` : '.'}`;
    else if (status === 'blocked') hint.textContent = 'Платёжный профиль заблокирован. Обратитесь в поддержку.';
    else if (status === 'pending') hint.textContent = 'Профиль отправлен на проверку. Выплата станет доступна после подтверждения.';
    else hint.textContent = 'Заполните профиль. Перед выплатой владелец или уполномоченный сотрудник проверит сведения.';
  }
  loadAuthorSbpBanks(profile?.sbp_bank_id || '', profile?.sbp_bank_name || '', Boolean(policy.yookassa_payouts_configured));

  const available = Number(finance.available_minor || 0);
  const pending = Number(finance.pending_minor || 0);
  const minimum = Number(policy.payout_min_minor || 10000);
  document.getElementById('authorPayoutAvailable').textContent = formatRubMinor(available);
  document.getElementById('authorPayoutPending').textContent = `В обработке: ${formatRubMinor(pending)}`;
  const amountInput = document.getElementById('authorPayoutAmount');
  if (amountInput) {
    amountInput.value = (available / 100).toFixed(2);
    amountInput.max = (available / 100).toFixed(2);
    amountInput.readOnly = true;
  }
  const payoutButton = document.getElementById('authorRequestPayout');
  const canRequest = Boolean(policy.yookassa_payouts_configured && status === 'verified' && available >= minimum);
  if (payoutButton) payoutButton.disabled = !canRequest;
  const payoutHint = document.getElementById('authorPayoutHint');
  if (payoutHint) {
    if (!policy.yookassa_payouts_configured) payoutHint.textContent = 'ЮKassa ещё не подключена владельцем. Реквизиты и рублёвые начисления сохранятся, но отправка выплаты выключена.';
    else if (status !== 'verified') payoutHint.textContent = 'Для вывода нужен проверенный платёжный профиль.';
    else if (available < minimum) payoutHint.textContent = `Минимальная заявка — ${formatRubMinor(minimum)}. Накопленная сумма не сгорает.`;
    else payoutHint.textContent = 'Заявка создаётся на всю доступную сумму, чтобы каждое начисление оставалось полностью прослеживаемым.';
  }
}

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


function achievementCard(item) {
  const rarityLabels = { common: 'Бронзовая', rare: 'Серебряная', epic: 'Золотая', legendary: 'Платиновая', mythic: 'Легендарная' };
  const tierByRarity = { common: 'bronze', rare: 'silver', epic: 'gold', legendary: 'platinum', mythic: 'legend' };
  const tierLabels = { bronze: 'Бронза', silver: 'Серебро', gold: 'Золото', platinum: 'Платина', legend: 'Легенда' };
  const rarity = ['common', 'rare', 'epic', 'legendary', 'mythic'].includes(item?.rarity) ? item.rarity : 'common';
  const tier = ['bronze', 'silver', 'gold', 'platinum', 'legend'].includes(item?.tier) ? item.tier : tierByRarity[rarity];
  const image = item?.icon_asset
    ? `<span class="achievement-icon achievement-icon-image"><img src="${escapeHtml(item.icon_asset)}" alt="${escapeHtml(item.title || 'Достижение')}" loading="lazy"></span>`
    : `<span class="achievement-icon">${escapeHtml(item.icon || '✦')}</span>`;
  return `<article class="achievement-card rarity-${rarity} tier-${tier} is-unlocked"><div class="achievement-medal">${image}<span class="achievement-check">✓</span></div><div class="achievement-copy"><div class="achievement-title-row"><strong>${escapeHtml(item.title || 'Достижение')}</strong><span class="achievement-tier">${tierLabels[tier]}</span></div><p>${escapeHtml(item.description || '')}</p><small class="achievement-rarity-note">${rarityLabels[rarity]} награда</small></div></article>`;
}

function renderAuthorAchievements(payload) {
  const panel = document.getElementById('authorAchievementPanel');
  const grid = document.getElementById('authorAchievements');
  if (!panel || !grid) return;
  const items = (payload?.items || []).filter((item) => item.group === 'author');
  panel.hidden = !items.length;
  grid.innerHTML = items.map(achievementCard).join('');
  window.showAchievementUnlockSequence?.((payload?.new || []).filter((item) => item.group === 'author'));
}

function renderAuthorAnalytics(analytics) {
  const data = analytics || {};
  const summary = data.summary || {};
  const summaryBox = document.getElementById('authorAnalyticsSummary');
  if (summaryBox) {
    const cards = [
      ['Уникальные читатели', summary.unique_readers || 0],
      ['Дочитано глав', summary.completed_chapters || 0],
      ['Добавили в библиотеку', summary.library_additions || 0],
      ['Средняя оценка', Number(summary.average_rating || 0).toFixed(1)],
      ['Продажи', summary.sales_count || 0],
      ['Доход', formatStars(summary.revenue_stars || 0)],
      ['Premium-читатели', summary.premium_readers || 0],
      ['Premium-дочитывания', summary.premium_completions || 0],
      ['Доход от Premium', formatStars(summary.premium_income_stars || 0)],
      ['Комментарии', summary.comments_count || 0],
      ['Реакции', summary.reactions_count || 0],
    ];
    summaryBox.innerHTML = cards.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join('');
  }
  const booksBox = document.getElementById('authorAnalyticsBooks');
  const books = data.books || [];
  if (booksBox) booksBox.innerHTML = books.length ? books.slice(0, 8).map((item) => `
    <article class="analytics-row"><div><strong>${escapeHtml(item.title || 'Книга')}</strong><span>${Number(item.readers || 0)} читателей · ${Number(item.saved || 0)} сохранили · рейтинг ${Number(item.rating || 0).toFixed(1)}</span></div><b>${Number(item.sales || 0)} продаж</b></article>`).join('') : '<p class="muted">Данных пока недостаточно.</p>';
  const dropBox = document.getElementById('authorAnalyticsDropoff');
  const dropoff = [...(data.dropoff || [])].sort((a, b) => Number(a.completion_rate || 0) - Number(b.completion_rate || 0)).slice(0, 8);
  if (dropBox) dropBox.innerHTML = dropoff.length ? dropoff.map((item) => `
    <article class="analytics-row ${Number(item.completion_rate || 0) < 45 ? 'attention' : ''}"><div><strong>${escapeHtml(item.book_title || 'Книга')} · глава ${Number(item.number || 0)}</strong><span>${escapeHtml(item.title || '')} · начали ${Number(item.started || 0)}, дочитали ${Number(item.completed || 0)}</span></div><b>${Number(item.completion_rate || 0).toFixed(0)}%</b></article>`).join('') : '<p class="muted">Появится после чтения опубликованных глав.</p>';
  const dailyBox = document.getElementById('authorAnalyticsDaily');
  const daily = data.daily || [];
  if (dailyBox) {
    const max = Math.max(1, ...daily.map((item) => Number(item.readers || 0)));
    dailyBox.innerHTML = daily.map((item) => `<span class="analytics-bar" style="--bar:${Math.max(5, Math.round(Number(item.readers || 0) / max * 100))}%" title="${escapeHtml(item.day)} · ${Number(item.readers || 0)}"><i></i><small>${escapeHtml(String(item.day || '').slice(5))}</small></span>`).join('');
  }
}

async function loadAuthorAnalytics(days) {
  try {
    const result = await apiFetch(`/api/author/analytics?days=${Math.max(7, Number(days || 30))}`);
    renderAuthorAnalytics(result.analytics);
    renderAuthorAchievements(result.achievements);
  } catch (error) {
    notify(error.message || 'Не удалось обновить аналитику');
  }
}

function renderAuthorDashboard(data) {
  authorState.dashboard = data;
  setAuthorLoading(false);
  document.getElementById('authorError')?.setAttribute('hidden', '');
  const dashboard = document.getElementById('authorDashboard');
  if (dashboard) dashboard.hidden = false;
  document.getElementById('authorPenName').textContent = data.profile.pen_name || 'Автор';
  document.getElementById('authorSummary').textContent = data.profile.bio || 'Управляйте произведениями и публикациями в своей студии.';

  const stats = data.stats || {};
  const finance = data.finance || {};
  const cards = [
    ['Произведения', stats.books_total || 0],
    ['Опубликовано', stats.books_published || 0],
    ['На проверке', stats.books_review || 0],
    ['Текстовые главы', stats.text_chapters || 0],
    ['Графические главы', stats.graphic_chapters || 0],
    ['Страницы', stats.graphic_pages || 0],
    ['Доступно', formatStars(finance.available)],
    ['В удержании', formatStars(finance.held)],
    ['Доход Premium', formatStars(finance.premium_total || 0)],
    ['Premium в удержании', formatStars(finance.premium_held || 0)],
  ];
  document.getElementById('authorStats').innerHTML = cards.map(([label, value]) => `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join('');
  renderAuthorAnalytics(data.analytics);
  renderAuthorAchievements(data.achievements);
  const rubFinance = data.rub_finance || {};
  const policy = data.pricing_policy || {};
  document.getElementById('authorAuthorPercent').textContent = `${Number(policy.author_percent || 80)}%`;
  document.getElementById('authorPlatformPercent').textContent = `${Number(policy.platform_percent ?? 19)}%`;
  document.getElementById('authorBonusPercent').textContent = `${Number(policy.bonus_percent ?? 1)}%`;
  document.getElementById('authorCommissionPercent').textContent = `${Number(policy.commission_percent || 20)}%`;
  document.getElementById('authorHoldDays').textContent = `${Number(policy.hold_days || 14)} дней`;
  document.getElementById('authorRubAvailable').textContent = formatRubMinor(rubFinance.available_minor);
  document.getElementById('authorRubHeld').textContent = formatRubMinor(rubFinance.held_minor);
  const example = policy.example || {};
  const grossStars = Number(example.gross_stars || 10);
  const buyerEstimate = Number(example.buyer_estimate_minor || 1450);
  const commissionStars = Number(example.commission_stars || 2);
  const netStars = Number(example.net_stars || 8);
  const authorNet = Number(example.author_net_minor || 800);
  document.getElementById('authorPriceExample').textContent = `При цене ${grossStars} Stars: покупателю показывается ориентир ${formatRubMinor(buyerEstimate)}, платформенная и бонусная части вместе ${commissionStars} Stars, автору ${netStars} Stars = ${formatRubMinor(authorNet)}. Начисления всегда целые.`;
  document.getElementById('authorPayoutState').textContent = 'Продажи принимаются только в Stars. Рублёвая сумма автора фиксируется для каждой продажи, а выплата подтверждается владельцем вручную.';
  const desired = document.getElementById('authorDesiredNet');
  const suggested = document.getElementById('authorSuggestedFinal');
  const recalc = () => {
    const priceStars = Math.max(0, Math.round(Number(desired?.value || 0)));
    const percent = Math.max(0, Math.min(100, Number(policy.commission_percent || 20)));
    const commission = Math.round(priceStars * percent / 100);
    const net = Math.max(0, priceStars - commission);
    const rate = Number(example.author_rate_minor || 100);
    const payout = net * rate / 100;
    if (suggested) suggested.value = payout.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  desired?.addEventListener('input', recalc);
  recalc();
  renderAuthorFinancialArea(data);
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
      headers: { 'X-Telegram-Init-Data': tgInitData() }, cache: 'no-store',
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
  if (!box) return;
  box.querySelectorAll('img[data-author-cover-id]').forEach(releaseAuthorCoverUrl);
  if (!books.length) {
    box.innerHTML = '<article class="empty-card premium-empty"><div class="empty-icon">✦</div><h3>Произведений пока нет</h3><p>Создайте первую книгу, комикс, мангу, манхву или вебтун прямо здесь.</p></article>';
    return;
  }
  box.innerHTML = books.map((book) => {
    const type = String(book.content_type || 'book');
    const graphic = isGraphicType(type);
    const countLine = graphic
      ? `${Number(book.graphic_chapters_count || 0)} глав · ${Number(book.graphic_pages_count || 0)} страниц`
      : `${Number(book.text_chapters_count ?? book.chapters_count ?? 0)} глав · ${Number(book.audio_count || 0)} аудио`;
    return `<button class="author-book-card${graphic ? ' graphic-project-card' : ''}" type="button" data-author-book-id="${book.id}">
      <div class="author-book-cover author-cover-shell">
        <img class="author-book-cover-image" data-author-cover-id="${Number(book.id)}" alt="Обложка произведения ${escapeHtml(book.title)}" hidden>
        <div class="author-book-letter">${escapeHtml((book.title || 'В').slice(0, 1))}</div>
      </div>
      <div><span>${escapeHtml(contentTypeLabels[type] || 'Произведение')} · ${escapeHtml(statusLabels[book.publication_status] || book.publication_status)}</span><h3>${escapeHtml(book.title)}</h3><p>${countLine}</p></div>
      <b>›</b>
    </button>`;
  }).join('');
  loadAuthorCovers(box);
}

function toggleProjectPanels(contentType) {
  const graphic = isGraphicType(contentType);
  const textImport = document.getElementById('textImportPanel');
  const textManager = document.getElementById('textChapterManager');
  const graphicImport = document.getElementById('graphicImportPanel');
  const graphicManager = document.getElementById('graphicChapterManager');
  if (textImport) textImport.hidden = graphic;
  if (textManager) textManager.hidden = graphic;
  if (graphicImport) graphicImport.hidden = !graphic;
  if (graphicManager) graphicManager.hidden = !graphic;
  const label = document.getElementById('graphicProjectTypeLabel');
  if (label) label.textContent = contentTypeLabels[contentType] || 'Графическое произведение';
}

function moderationFieldLabel(value) {
  return {
    title: 'Название', description: 'Описание', age_limit: 'Возрастной рейтинг', cover: 'Обложка',
    content_type: 'Тип произведения', license: 'Права и источник', structure: 'Структура произведения',
  }[String(value || '')] || 'Метаданные';
}

function renderAuthorModeration(data) {
  const panel = document.getElementById('authorModerationPanel');
  if (!panel) return;
  const moderation = data.moderation || {};
  const queue = moderation.queue || null;
  const revision = moderation.revision || null;
  const findings = Array.isArray(moderation.findings) ? moderation.findings : [];
  const bookStatus = String(data.book?.publication_status || '');
  const visible = Boolean(revision || findings.length || (queue && String(queue.status || '') === 'pending'));
  panel.hidden = !visible;
  const submit = document.getElementById('submitBookReview');
  if (submit) {
    submit.disabled = bookStatus === 'review';
    submit.textContent = bookStatus === 'review'
      ? 'Уже на проверке'
      : revision
        ? 'Отправить исправления на повторную проверку'
        : 'Отправить на проверку';
  }
  if (!visible) return;
  const status = document.getElementById('authorModerationStatus');
  const title = document.getElementById('authorModerationTitle');
  const summary = document.getElementById('authorModerationSummary');
  if (bookStatus === 'review') {
    status.textContent = 'На проверке';
    status.className = 'finance-status pending';
    title.textContent = revision ? 'Исправления отправлены' : 'Произведение проверяется';
    summary.textContent = 'Модератор увидит только изменённые части и нерешённые замечания к остальному тексту.';
  } else {
    status.textContent = 'Нужны исправления';
    status.className = 'finance-status rejected';
    title.textContent = 'Что нужно исправить';
    summary.textContent = 'После исправления повторно отправьте произведение. Неизменённые главы повторно не сканируются.';
  }
  const reason = String(revision?.reason || queue?.moderator_note || queue?.reasons || '').trim();
  const reasonBox = document.getElementById('authorModerationReason');
  if (reasonBox) {
    reasonBox.hidden = !reason;
    reasonBox.textContent = reason;
  }
  const changes = moderation.changes || null;
  const changesBox = document.getElementById('authorModerationChanges');
  if (changesBox) {
    changesBox.textContent = changes
      ? `После возврата изменено: метаданные — ${Number(changes.metadata || 0)}, текстовые главы — ${Number(changes.text_chapters || 0)}, графические главы — ${Number(changes.graphic_chapters || 0)}, удалено — ${Number(changes.deleted || 0)}.`
      : 'Изменения будут отслеживаться автоматически после возврата на доработку.';
  }
  const box = document.getElementById('authorModerationFindings');
  if (!box) return;
  if (!findings.length) {
    box.innerHTML = '<article class="empty-card"><h3>Точных автоматических совпадений нет</h3><p>Следуйте текстовой инструкции модератора. После повторной отправки выполнение проверит сотрудник.</p></article>';
    return;
  }
  box.innerHTML = findings.map((item) => {
    const metadata = String(item.source_type || '') === 'metadata';
    const location = metadata
      ? moderationFieldLabel(item.field_name)
      : item.chapter_number !== null && item.chapter_number !== undefined
        ? `Глава ${Number(item.chapter_number)}${item.chapter_title ? ` — ${escapeHtml(item.chapter_title)}` : ''}`
        : 'Произведение';
    const fragment = escapeHtml(String(item.matched_text || item.context || '').trim().slice(0, 650));
    const action = item.chapter_id
      ? `<button type="button" class="button-link secondary compact-button" data-edit-chapter="${Number(item.chapter_id)}">Открыть главу</button>`
      : '';
    return `<article class="author-chapter-row moderation-author-finding">
      <div><span>${escapeHtml(String(item.severity || '') === 'block' ? 'Обязательно исправить' : 'Проверить')} · ${location}</span><strong>${escapeHtml(item.reason || 'Требуется исправление')}</strong><small>Строка ${Number(item.line_number || 1)} · позиция ${Number(item.character_offset || 0)}${fragment ? ` · ${fragment}` : ''}</small></div>${action}
    </article>`;
  }).join('');
}

function fillBookEditor(data) {
  authorState.book = data;
  const book = data.book;
  const editor = document.getElementById('authorBookEditor');
  editor.hidden = false;
  const type = String(book.content_type || 'book');
  document.getElementById('editorBookTitle').textContent = book.title;
  document.getElementById('editorBookStatus').textContent = `${contentTypeLabels[type] || 'Произведение'} · ${statusLabels[book.publication_status] || book.publication_status} · ${writingLabels[book.writing_status] || book.writing_status}`;
  renderAuthorModeration(data);
  const openPublishedBook = document.getElementById('openPublishedBook');
  if (openPublishedBook) {
    const published = String(book.publication_status || '') === 'published';
    openPublishedBook.hidden = !published;
    openPublishedBook.href = published ? `/book/${Number(book.id)}` : '#';
    openPublishedBook.textContent = isGraphicType(type) ? 'Открыть произведение' : 'Открыть книгу';
  }
  const editorCover = document.getElementById('editorBookCoverImage');
  const editorLetter = document.getElementById('editorBookCoverLetter');
  if (editorCover && editorLetter) {
    releaseAuthorCoverUrl(editorCover);
    editorCover.hidden = true;
    editorCover.removeAttribute('src');
    delete editorCover.dataset.coverLoading;
    editorLetter.hidden = false;
    editorLetter.textContent = (book.title || 'В').slice(0, 1);
    editorCover.dataset.authorCoverId = String(book.id);
    editorCover.alt = `Обложка произведения ${book.title || ''}`;
    loadAuthorCovers(document.getElementById('editorBookCover'));
  }
  document.getElementById('bookTitleInput').value = book.title || '';
  document.getElementById('bookDescriptionInput').value = book.description || '';
  document.getElementById('bookAgeInput').value = book.age_limit || '16+';
  document.getElementById('bookWritingInput').value = book.writing_status || 'writing';
  document.getElementById('bookPriceInput').value = Number(book.price_stars || 0) > 0 ? Number(book.price_stars) : 10;
  const pricingModeInput = document.getElementById('bookPricingModeInput');
  if (pricingModeInput) pricingModeInput.value = currentTextPricingMode(data);
  document.getElementById('bookDownloadInput').checked = Boolean(Number(book.allow_download || 0));
  document.getElementById('bookContentTypeInput').value = type;
  document.getElementById('bookReadingModeInput').value = book.reading_mode || defaultReadingMode(type);
  const splitLongInput = document.getElementById('graphicSplitLongInput');
  if (splitLongInput) splitLongInput.checked = type === 'webtoon' || type === 'manhwa';
  syncTextPricingControls();
  authorState.chapterPage = 1;
  renderAuthorChapters(data.chapters || []);
  renderChapterPackages(data.chapter_packages || []);
  renderGraphicVolumes(data.graphic_volumes || []);
  renderGraphicChapters(data.graphic_chapters || []);
  toggleProjectPanels(type);
  resetChapterForm();
  resetChapterPackageForm();
  resetGraphicChapterForm();
  const preview = document.getElementById('importPreview');
  if (preview) preview.hidden = true;
  const graphicResult = document.getElementById('graphicUploadResult');
  if (graphicResult) graphicResult.hidden = true;
  authorState.previewToken = null;
  editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function renderAuthorChapters(chapters, requestedPage = authorState.chapterPage) {
  const box = document.getElementById('authorChapters');
  if (!box) return;
  if (!chapters.length) {
    box.innerHTML = '<article class="empty-card"><h3>Текстовых глав пока нет</h3><p>Добавьте главу вручную или импортируйте файл книги.</p></article>';
    return;
  }
  const mode = currentTextPricingMode();
  const pages = Math.max(1, Math.ceil(chapters.length / AUTHOR_CHAPTERS_PER_PAGE));
  const page = Math.max(1, Math.min(pages, Number(requestedPage || 1)));
  authorState.chapterPage = page;
  const start = (page - 1) * AUTHOR_CHAPTERS_PER_PAGE;
  const visible = chapters.slice(start, start + AUTHOR_CHAPTERS_PER_PAGE);
  const rows = visible.map((chapter) => {
    const access = chapterAccessMode(chapter, mode);
    const accessText = access === 'free'
      ? 'Бесплатная глава'
      : access === 'chapter'
        ? `Цена этой главы: ${formatStars(chapter.price_stars)}`
        : access === 'premium'
          ? 'Доступ по VoxLyra Premium'
          : 'Доступ после покупки всей книги';
    return `<button class="author-chapter-row" type="button" data-edit-chapter="${chapter.id}">
      <div><span>Глава ${chapter.number}</span><strong>${escapeHtml(chapter.title)}</strong><small>${accessText} · ${escapeHtml(statusLabels[chapter.status] || chapter.status)}</small></div><b>Изменить</b>
    </button>`;
  }).join('');
  const from = start + 1;
  const to = Math.min(chapters.length, start + visible.length);
  const pager = pages > 1 ? `<nav class="author-chapter-pager" aria-label="Страницы списка глав">
    <button type="button" class="button-link secondary" data-author-chapter-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>← Предыдущие</button>
    <span>Главы ${from}–${to} из ${chapters.length} · страница ${page} из ${pages}</span>
    <button type="button" class="button-link secondary" data-author-chapter-page="${page + 1}" ${page >= pages ? 'disabled' : ''}>Следующие →</button>
  </nav>` : `<p class="author-chapter-count">Глав: ${chapters.length}</p>`;
  box.innerHTML = `${pager}${rows}${pages > 1 ? pager : ''}`;
}

function chapterPackageScopeLabel(scope) {
  return { text: 'Текстовые главы', graphic: 'Графические главы', all: 'Любые главы' }[scope] || 'Текстовые главы';
}

function updateChapterPackagePreview() {
  const count = Math.max(1, Number(document.getElementById('chapterPackageCountInput')?.value || 1));
  const price = Math.max(1, Number(document.getElementById('chapterPackagePriceInput')?.value || 1));
  const perChapter = price / count;
  const box = document.getElementById('chapterPackagePreview');
  if (box) box.textContent = `${count} глав за ${price} Stars · ${perChapter.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Stars за главу`;
}

function resetChapterPackageForm() {
  const form = document.getElementById('chapterPackageForm');
  if (!form) return;
  form.hidden = true;
  document.getElementById('chapterPackageIdInput').value = '';
  document.getElementById('chapterPackageTitleInput').value = '';
  document.getElementById('chapterPackageCountInput').value = '20';
  document.getElementById('chapterPackagePriceInput').value = '99';
  const type = String(authorState.book?.book?.content_type || 'book');
  document.getElementById('chapterPackageScopeInput').value = isGraphicType(type) ? 'graphic' : 'text';
  document.getElementById('chapterPackageActiveInput').checked = true;
  const deleteButton = document.getElementById('deleteChapterPackageButton');
  if (deleteButton) { deleteButton.hidden = true; deleteButton.dataset.confirm = ''; deleteButton.textContent = 'Убрать пакет'; }
  updateChapterPackagePreview();
}

function renderChapterPackages(packages) {
  const box = document.getElementById('authorChapterPackages');
  if (!box) return;
  if (!packages.length) {
    box.innerHTML = '<article class="empty-card"><h3>Пакетов пока нет</h3><p>Создайте несколько вариантов: читатель сам выберет выгодный объём.</p></article>';
    return;
  }
  box.innerHTML = packages.map((item) => {
    const count = Number(item.chapters_count || 0);
    const price = Number(item.price_stars || 0);
    const unit = count > 0 ? price / count : 0;
    const active = Boolean(Number(item.is_active || 0));
    return `<button class="chapter-package-author-card${active ? '' : ' inactive'}" type="button" data-edit-chapter-package="${Number(item.id)}">
      <div><span>${escapeHtml(chapterPackageScopeLabel(item.content_scope))} · ${active ? 'показывается' : 'скрыт'}</span><strong>${escapeHtml(item.title || `Пакет на ${count} глав`)}</strong><small>${count} глав · ${price} Stars · ${unit.toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} Stars за главу</small></div><b>Изменить</b>
    </button>`;
  }).join('');
}

function editChapterPackage(packageId) {
  const item = (authorState.book?.chapter_packages || []).find((entry) => Number(entry.id) === Number(packageId));
  if (!item) return;
  const form = document.getElementById('chapterPackageForm');
  form.hidden = false;
  document.getElementById('chapterPackageIdInput').value = String(item.id);
  document.getElementById('chapterPackageTitleInput').value = item.title || '';
  document.getElementById('chapterPackageCountInput').value = String(Number(item.chapters_count || 1));
  document.getElementById('chapterPackagePriceInput').value = String(Number(item.price_stars || 1));
  document.getElementById('chapterPackageScopeInput').value = item.content_scope || 'text';
  document.getElementById('chapterPackageActiveInput').checked = Boolean(Number(item.is_active || 0));
  const deleteButton = document.getElementById('deleteChapterPackageButton');
  if (deleteButton) { deleteButton.hidden = false; deleteButton.dataset.confirm = ''; deleteButton.textContent = 'Убрать пакет'; }
  updateChapterPackagePreview();
  form.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function renderGraphicVolumes(volumes) {
  const box = document.getElementById('authorGraphicVolumes');
  if (!box) return;
  if (!volumes.length) {
    box.innerHTML = '<p class="muted">Тома появятся после загрузки первой графической главы.</p>';
    return;
  }
  box.innerHTML = volumes.map((volume) => `<article class="graphic-volume-price-card" data-volume-card="${Number(volume.volume_number || 1)}">
    <label>Название тома<input data-volume-title value="${escapeHtml(volume.title || '')}" maxlength="120" placeholder="Том ${Number(volume.volume_number || 1)}"></label>
    <label>Цена, Stars<input data-volume-price type="number" min="0" max="100000" value="${Number(volume.is_free ? 0 : volume.price_stars || 0)}"></label>
    <button class="button-link compact-button volume-save" type="button" data-save-graphic-volume="${Number(volume.volume_number || 1)}">Сохранить том ${Number(volume.volume_number || 1)}</button>
    <small>${Number(volume.chapters_count || 0)} глав · ${Number(volume.pages_count || 0)} страниц. Нулевая цена означает бесплатный том.</small>
  </article>`).join('');
}

function renderGraphicChapters(chapters) {
  const box = document.getElementById('authorGraphicChapters');
  if (!box) return;
  if (!chapters.length) {
    box.innerHTML = '<article class="empty-card"><h3>Графических глав пока нет</h3><p>Загрузите PDF, CBZ/ZIP или набор изображений выше.</p></article>';
    return;
  }
  box.innerHTML = chapters.map((chapter) => {
    const pages = Number(chapter.actual_pages_count ?? chapter.pages_count ?? 0);
    const mode = readingModeLabels[chapter.reading_mode] || readingModeLabels.inherit;
    return `<button class="author-chapter-row graphic-chapter-row" type="button" data-edit-graphic-chapter="${chapter.id}">
      <div><span>Том ${Number(chapter.volume_number || 1)} · Глава ${chapter.number} · ${pages} стр.</span><strong>${escapeHtml(chapter.title)}</strong><small>${escapeHtml(mode)} · ${chapter.is_free ? 'Бесплатно' : formatStars(chapter.price_stars)} · предпросмотр ${Number(chapter.preview_pages || 0)} стр. · ${escapeHtml(statusLabels[chapter.status] || chapter.status)}</small></div><b>Изменить</b>
    </button>`;
  }).join('');
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
        const graphic = isGraphicType(authorState.book?.book?.content_type);
        const input = document.getElementById(graphic ? 'graphicFileInput' : 'bookFileInput');
        input?.closest('.panel-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => input?.focus(), 350);
        notify(graphic ? 'Выберите PDF, архив или изображения страниц' : 'Выберите файл, затем нажмите «Загрузить и проверить»');
      }
    }
  } catch (error) { showAuthorError(error.message); }
}

async function openAuthorBook(bookId) {
  try { fillBookEditor(await apiFetch(`/api/author/book/${bookId}`)); }
  catch (error) { notify(error.message || 'Не удалось открыть произведение'); }
}

async function refreshAuthorDashboard(reopenBook = true) {
  const currentId = reopenBook ? authorState.book?.book?.id : null;
  const data = await apiFetch('/api/author/dashboard');
  renderAuthorDashboard(data);
  if (currentId) await openAuthorBook(currentId);
}

function resetChapterForm() {
  const form = document.getElementById('chapterForm');
  if (!form) return;
  form.hidden = true;
  document.getElementById('chapterIdInput').value = '';
  document.getElementById('chapterTitleInput').value = '';
  document.getElementById('chapterTextInput').value = '';
  const mode = currentTextPricingMode();
  document.getElementById('chapterAccessInput').value = mode === 'free' ? 'free' : mode === 'premium' ? 'premium' : 'book';
  document.getElementById('chapterPriceInput').value = '3';
  document.getElementById('deleteChapterButton').hidden = true;
  document.getElementById('deleteChapterButton').dataset.confirm = '';
  syncChapterAccessInputs();
}

async function editChapter(chapterId) {
  const chapters = authorState.book?.chapters || [];
  const index = chapters.findIndex((item) => Number(item.id) === Number(chapterId));
  if (index < 0) return;
  let chapter = chapters[index];
  const form = document.getElementById('chapterForm');
  form.hidden = false;
  document.getElementById('chapterIdInput').value = chapter.id;
  document.getElementById('chapterTitleInput').value = chapter.title || '';
  const textInput = document.getElementById('chapterTextInput');
  textInput.value = '';
  textInput.placeholder = 'Загружаем текст главы…';
  textInput.disabled = true;
  form.scrollIntoView({ behavior: 'smooth', block: 'center' });
  try {
    if (!Object.prototype.hasOwnProperty.call(chapter, 'text')) {
      const payload = await apiFetch(`/api/author/chapter/${Number(chapter.id)}`);
      chapter = { ...chapter, ...(payload.chapter || {}) };
      chapters[index] = chapter;
    }
    document.getElementById('chapterTitleInput').value = chapter.title || '';
    textInput.value = chapter.text || '';
    const access = chapterAccessMode(chapter);
    document.getElementById('chapterAccessInput').value = access;
    document.getElementById('chapterPriceInput').value = access === 'chapter' ? Number(chapter.price_stars || 3) : 3;
    document.getElementById('deleteChapterButton').hidden = false;
    syncChapterAccessInputs();
  } catch (error) {
    form.hidden = true;
    notify(error.message || 'Не удалось загрузить текст главы');
  } finally {
    textInput.disabled = false;
    textInput.placeholder = '';
  }
}

function resetGraphicChapterForm() {
  const form = document.getElementById('graphicChapterEditForm');
  if (!form) return;
  form.hidden = true;
  document.getElementById('graphicChapterIdInput').value = '';
  document.getElementById('graphicChapterEditTitle').value = '';
  document.getElementById('graphicChapterEditMode').value = 'inherit';
  document.getElementById('graphicChapterEditPrice').value = '0';
  document.getElementById('graphicChapterEditVolumeNumber').value = '1';
  document.getElementById('graphicChapterEditVolumeTitle').value = '';
  document.getElementById('graphicChapterEditPreview').value = '3';
  const button = document.getElementById('deleteGraphicChapterButton');
  if (button) { button.dataset.confirm = ''; button.textContent = 'Удалить главу'; }
  const pageEditor = document.getElementById('graphicPageEditor');
  if (pageEditor) pageEditor.hidden = true;
  authorState.graphicPages = [];
  authorState.graphicPageChapterId = null;
  authorState.graphicPageReplacementId = null;
}

function editGraphicChapter(chapterId) {
  const chapter = (authorState.book?.graphic_chapters || []).find((item) => Number(item.id) === Number(chapterId));
  if (!chapter) return;
  const form = document.getElementById('graphicChapterEditForm');
  form.hidden = false;
  document.getElementById('graphicChapterIdInput').value = chapter.id;
  document.getElementById('graphicChapterEditTitle').value = chapter.title || '';
  document.getElementById('graphicChapterEditMode').value = chapter.reading_mode || 'inherit';
  document.getElementById('graphicChapterEditPrice').value = chapter.is_free ? 0 : Number(chapter.price_stars || 0);
  document.getElementById('graphicChapterEditVolumeNumber').value = Number(chapter.volume_number || 1);
  document.getElementById('graphicChapterEditVolumeTitle').value = chapter.volume_title || '';
  document.getElementById('graphicChapterEditPreview').value = Number(chapter.preview_pages ?? 3);
  document.getElementById('deleteGraphicChapterButton').dataset.confirm = '';
  document.getElementById('deleteGraphicChapterButton').textContent = 'Удалить главу';
  form.scrollIntoView({ behavior: 'smooth', block: 'center' });
  loadGraphicPageEditor(chapter.id);
}

function setGraphicPageEditorStatus(message = '', isError = false) {
  const box = document.getElementById('graphicPageEditorStatus');
  if (!box) return;
  box.hidden = !message;
  box.textContent = message;
  box.classList.toggle('error', Boolean(isError));
}

function renderGraphicPageEditor(pages) {
  authorState.graphicPages = Array.isArray(pages) ? pages : [];
  const grid = document.getElementById('graphicPageGrid');
  if (!grid) return;
  if (!authorState.graphicPages.length) {
    grid.innerHTML = '<article class="empty-card"><h3>Страниц нет</h3><p>Загрузите страницы заново или удалите пустую главу.</p></article>';
    return;
  }
  grid.innerHTML = authorState.graphicPages.map((page, index) => `<article class="graphic-page-card" draggable="true" data-graphic-page-card="${Number(page.id)}">
    <div class="graphic-page-preview"><img src="${escapeHtml(page.url || '')}" alt="Страница ${Number(page.number || index + 1)}" loading="lazy"></div>
    <div class="graphic-page-meta"><strong>Страница ${Number(page.number || index + 1)}</strong><span>${Number(page.width || 0)} × ${Number(page.height || 0)} · ${(Number(page.file_size || 0) / 1024).toFixed(0)} КБ</span><small>${escapeHtml(page.source_filename || '')}</small></div>
    <div class="graphic-page-actions">
      <button type="button" class="secondary" data-page-move="-1" data-page-id="${Number(page.id)}" aria-label="Переместить выше">↑</button>
      <button type="button" class="secondary" data-page-move="1" data-page-id="${Number(page.id)}" aria-label="Переместить ниже">↓</button>
      <button type="button" class="secondary" data-page-rotate="270" data-page-id="${Number(page.id)}" aria-label="Повернуть влево">↺</button>
      <button type="button" class="secondary" data-page-rotate="90" data-page-id="${Number(page.id)}" aria-label="Повернуть вправо">↻</button>
      <button type="button" class="secondary page-wide-action" data-page-advanced="${Number(page.id)}">Текст и кадры</button>
      <button type="button" class="secondary page-wide-action" data-page-replace="${Number(page.id)}">Заменить</button>
      <button type="button" class="danger-button page-wide-action" data-page-delete="${Number(page.id)}">Удалить</button>
    </div>
  </article>`).join('');
}


function graphicCoordinateRow(item = {}, type = 'frame') {
  const textField = type === 'translation' ? `<textarea data-coordinate-text maxlength="5000" placeholder="Текст перевода">${escapeHtml(item.text || '')}</textarea>` : '';
  return `<div class="graphic-coordinate-row" data-coordinate-row>
    <label>X<input data-coordinate-x type="number" min="0" max="1" step="0.001" value="${Number(item.x || 0).toFixed(3)}"></label>
    <label>Y<input data-coordinate-y type="number" min="0" max="1" step="0.001" value="${Number(item.y || 0).toFixed(3)}"></label>
    <label>Ширина<input data-coordinate-width type="number" min="0.01" max="1" step="0.001" value="${Number(item.width || 1).toFixed(3)}"></label>
    <label>Высота<input data-coordinate-height type="number" min="0.01" max="1" step="0.001" value="${Number(item.height || 1).toFixed(3)}"></label>
    ${textField}<button type="button" class="danger-button compact-button" data-remove-coordinate>Удалить</button>
  </div>`;
}

function renderGraphicFramesEditor(frames = []) {
  const box = document.getElementById('graphicFramesEditor');
  if (!box) return;
  box.innerHTML = frames.length ? frames.map((item) => graphicCoordinateRow(item, 'frame')).join('') : '<p class="muted-text">Кадры не заданы. Можно определить автоматически или добавить вручную.</p>';
}

function renderGraphicTranslationsEditor(regions = []) {
  const box = document.getElementById('graphicTranslationsEditor');
  if (!box) return;
  box.innerHTML = regions.length ? regions.map((item) => graphicCoordinateRow(item, 'translation')).join('') : '<p class="muted-text">Переводных областей пока нет.</p>';
}

function coordinateRows(containerId, withText = false) {
  return Array.from(document.querySelectorAll(`#${containerId} [data-coordinate-row]`)).map((row) => ({
    x: Number(row.querySelector('[data-coordinate-x]')?.value || 0),
    y: Number(row.querySelector('[data-coordinate-y]')?.value || 0),
    width: Number(row.querySelector('[data-coordinate-width]')?.value || 1),
    height: Number(row.querySelector('[data-coordinate-height]')?.value || 1),
    ...(withText ? { text: String(row.querySelector('[data-coordinate-text]')?.value || ''), style: 'bubble' } : {}),
  }));
}

function openGraphicAdvancedEditor(pageId) {
  const page = authorState.graphicPages.find((item) => Number(item.id) === Number(pageId));
  if (!page) return;
  authorState.graphicAdvancedPageId = Number(pageId);
  document.getElementById('graphicAdvancedPageId').value = String(pageId);
  document.getElementById('graphicAdvancedEditorTitle').textContent = `Страница ${Number(page.number || 0)}`;
  document.getElementById('graphicAdvancedPreview').src = page.url || '';
  const ocr = (page.texts || []).find((item) => item.text_kind === 'ocr') || (page.texts || [])[0];
  document.getElementById('graphicOcrText').value = ocr?.text || '';
  document.getElementById('graphicOcrStatus').textContent = ocr?.confidence ? `Точность около ${Number(ocr.confidence).toFixed(0)}%` : '';
  renderGraphicFramesEditor(page.frames || []);
  renderGraphicTranslationsEditor(page.translations || []);
  const editor = document.getElementById('graphicAdvancedEditor');
  editor.hidden = false;
  editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function runGraphicOcr() {
  const pageId = authorState.graphicAdvancedPageId;
  if (!pageId) return;
  const button = document.getElementById('graphicRunOcr');
  button.disabled = true;
  document.getElementById('graphicOcrStatus').textContent = 'Распознаём…';
  try {
    const result = await apiFetch(`/api/author/graphic-page/${pageId}/ocr`, { method: 'POST', body: JSON.stringify({ language: 'rus+eng' }) });
    document.getElementById('graphicOcrText').value = result.text || '';
    document.getElementById('graphicOcrStatus').textContent = `Точность около ${Number(result.confidence || 0).toFixed(0)}%`;
    notify('Текст распознан и сохранён');
  } catch (error) { document.getElementById('graphicOcrStatus').textContent = error.message || 'OCR не выполнен'; }
  finally { button.disabled = false; }
}

async function saveGraphicOcrText() {
  const pageId = authorState.graphicAdvancedPageId;
  if (!pageId) return;
  try {
    await apiFetch(`/api/author/graphic-page/${pageId}/text`, { method: 'PUT', body: JSON.stringify({ language_code: 'ru', text_kind: 'ocr', text: document.getElementById('graphicOcrText').value, confidence: 100 }) });
    notify('Текст страницы сохранён');
    await loadGraphicPageEditor(authorState.graphicPageChapterId, { keepPosition: true });
  } catch (error) { notify(error.message || 'Не удалось сохранить текст'); }
}

async function autoGraphicFrames() {
  const pageId = authorState.graphicAdvancedPageId;
  if (!pageId) return;
  try {
    const result = await apiFetch(`/api/author/graphic-page/${pageId}/frames/auto`, { method: 'POST' });
    renderGraphicFramesEditor(result.frames || []);
    notify(`Определено кадров: ${(result.frames || []).length}`);
  } catch (error) { notify(error.message || 'Не удалось определить кадры'); }
}

async function saveGraphicFrames() {
  const pageId = authorState.graphicAdvancedPageId;
  if (!pageId) return;
  try {
    const result = await apiFetch(`/api/author/graphic-page/${pageId}/frames`, { method: 'PUT', body: JSON.stringify({ frames: coordinateRows('graphicFramesEditor') }) });
    renderGraphicFramesEditor(result.frames || []);
    notify('Кадры сохранены');
  } catch (error) { notify(error.message || 'Не удалось сохранить кадры'); }
}

async function saveGraphicTranslations() {
  const pageId = authorState.graphicAdvancedPageId;
  if (!pageId) return;
  const language = document.getElementById('graphicTranslationEditorLanguage').value || 'ru';
  try {
    const result = await apiFetch(`/api/author/graphic-page/${pageId}/translations`, { method: 'PUT', body: JSON.stringify({ language_code: language, regions: coordinateRows('graphicTranslationsEditor', true) }) });
    renderGraphicTranslationsEditor(result.regions || []);
    notify('Переводной слой сохранён');
  } catch (error) { notify(error.message || 'Не удалось сохранить перевод'); }
}

async function processGraphicChapterPages(kind) {
  const pages = Array.isArray(authorState.graphicPages) ? authorState.graphicPages : [];
  if (!pages.length) { notify('В главе нет страниц'); return; }
  const isOcr = kind === 'ocr';
  const candidates = pages.filter((page) => isOcr ? !(page.texts || []).some((item) => item.text_kind === 'ocr' && String(item.text || '').trim()) : !(page.frames || []).length);
  if (!candidates.length) { notify(isOcr ? 'Текст уже распознан на всех страницах' : 'Кадры уже заданы на всех страницах'); return; }
  const button = document.getElementById(isOcr ? 'graphicProcessChapterOcr' : 'graphicProcessChapterFrames');
  if (button) button.disabled = true;
  let completed = 0;
  let failed = 0;
  try {
    for (const page of candidates) {
      setGraphicPageEditorStatus(`${isOcr ? 'Распознаём текст' : 'Определяем кадры'}: ${completed + failed + 1} из ${candidates.length}…`);
      try {
        if (isOcr) await apiFetch(`/api/author/graphic-page/${Number(page.id)}/ocr`, { method: 'POST', body: JSON.stringify({ language: 'rus+eng' }) });
        else await apiFetch(`/api/author/graphic-page/${Number(page.id)}/frames/auto`, { method: 'POST' });
        completed += 1;
      } catch (error) {
        failed += 1;
      }
    }
    setGraphicPageEditorStatus(`Готово: ${completed}. Ошибок: ${failed}.` , failed > 0);
    await loadGraphicPageEditor(authorState.graphicPageChapterId, { keepPosition: true });
    notify(`${isOcr ? 'OCR' : 'Кадры'}: обработано ${completed} страниц${failed ? `, ошибок ${failed}` : ''}`);
  } finally {
    if (button) button.disabled = false;
  }
}

async function showGraphicChapterStatistics() {
  const chapterId = authorState.graphicPageChapterId;
  if (!chapterId) return;
  try {
    const result = await apiFetch(`/api/author/graphic-chapter/${chapterId}/statistics`);
    const stat = result.statistics || {};
    const text = `Открытий: ${Number(stat.opens || 0)} · читателей: ${Number(stat.unique_openers || 0)} · просмотров страниц: ${Number(stat.page_views || 0)} · дочитали: ${Number(stat.completers || 0)} · покадровых просмотров: ${Number(stat.frame_views || 0)}`;
    setGraphicPageEditorStatus(text);
    notify(text);
  } catch (error) { notify(error.message || 'Не удалось получить статистику'); }
}

async function loadGraphicPageEditor(chapterId, { keepPosition = false } = {}) {
  const editor = document.getElementById('graphicPageEditor');
  if (!editor) return;
  authorState.graphicPageChapterId = Number(chapterId);
  editor.hidden = false;
  document.getElementById('graphicPageEditorTitle').textContent = 'Страницы главы';
  setGraphicPageEditorStatus('Загружаем страницы…');
  try {
    const result = await apiFetch(`/api/author/graphic-chapter/${chapterId}/pages`);
    renderGraphicPageEditor(result.pages || []);
    setGraphicPageEditorStatus('');
    if (!keepPosition) editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    setGraphicPageEditorStatus(error.message || 'Не удалось загрузить страницы', true);
  }
}

function graphicPageIdsFromGrid() {
  return Array.from(document.querySelectorAll('#graphicPageGrid [data-graphic-page-card]')).map((card) => Number(card.dataset.graphicPageCard));
}

async function saveGraphicPageOrder() {
  const chapterId = authorState.graphicPageChapterId;
  if (!chapterId) return;
  const pageIds = graphicPageIdsFromGrid();
  setGraphicPageEditorStatus('Сохраняем порядок…');
  try {
    const result = await apiFetch(`/api/author/graphic-chapter/${chapterId}/pages/reorder`, {
      method: 'POST', body: JSON.stringify({ page_ids: pageIds }),
    });
    renderGraphicPageEditor(result.pages || []);
    setGraphicPageEditorStatus('Порядок сохранён');
    setTimeout(() => setGraphicPageEditorStatus(''), 1300);
  } catch (error) {
    setGraphicPageEditorStatus(error.message || 'Не удалось сохранить порядок', true);
    await loadGraphicPageEditor(chapterId, { keepPosition: true });
  }
}

async function moveGraphicPage(pageId, direction) {
  const card = document.querySelector(`[data-graphic-page-card="${Number(pageId)}"]`);
  if (!card) return;
  const sibling = Number(direction) < 0 ? card.previousElementSibling : card.nextElementSibling;
  if (!sibling) return;
  if (Number(direction) < 0) card.parentElement.insertBefore(card, sibling);
  else card.parentElement.insertBefore(sibling, card);
  await saveGraphicPageOrder();
}

async function rotateGraphicPage(pageId, degrees) {
  setGraphicPageEditorStatus('Поворачиваем страницу…');
  try {
    await apiFetch(`/api/author/graphic-page/${pageId}/rotate`, { method: 'POST', body: JSON.stringify({ degrees: Number(degrees) }) });
    await loadGraphicPageEditor(authorState.graphicPageChapterId, { keepPosition: true });
    notify('Страница повёрнута');
  } catch (error) { setGraphicPageEditorStatus(error.message || 'Не удалось повернуть страницу', true); }
}

function chooseGraphicPageReplacement(pageId) {
  authorState.graphicPageReplacementId = Number(pageId);
  const input = document.getElementById('graphicPageReplacementInput');
  if (!input) return;
  input.value = '';
  input.click();
}

async function replaceGraphicPage(file) {
  const pageId = authorState.graphicPageReplacementId;
  if (!pageId || !file) return;
  const form = new FormData();
  form.append('file', file, file.name);
  setGraphicPageEditorStatus('Заменяем страницу…');
  try {
    await apiFetch(`/api/author/graphic-page/${pageId}/replace`, { method: 'POST', body: form });
    await loadGraphicPageEditor(authorState.graphicPageChapterId, { keepPosition: true });
    notify('Страница заменена');
  } catch (error) { setGraphicPageEditorStatus(error.message || 'Не удалось заменить страницу', true); }
  finally { authorState.graphicPageReplacementId = null; }
}

async function deleteGraphicPage(button, pageId) {
  if (!armDelete(button, 'Удалить?', 'Удалить')) return;
  setGraphicPageEditorStatus('Удаляем страницу…');
  try {
    const result = await apiFetch(`/api/author/graphic-page/${pageId}`, { method: 'DELETE' });
    renderGraphicPageEditor(result.pages || []);
    const chapter = (authorState.book?.graphic_chapters || []).find((item) => Number(item.id) === Number(authorState.graphicPageChapterId));
    if (chapter) {
      chapter.pages_count = (result.pages || []).length;
      chapter.actual_pages_count = (result.pages || []).length;
      renderGraphicChapters(authorState.book.graphic_chapters || []);
    }
    setGraphicPageEditorStatus('');
    notify('Страница удалена');
  } catch (error) { setGraphicPageEditorStatus(error.message || 'Не удалось удалить страницу', true); }
}

function setProgress(elementId, percent, label = '') {
  const box = document.getElementById(elementId);
  if (!box) return;
  box.hidden = false;
  const bar = box.querySelector('i');
  const text = box.querySelector('span');
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  if (text) text.textContent = label || `${Math.round(percent)}%`;
}
function setUploadProgress(percent, label = '') { setProgress('uploadProgress', percent, label); }
function setGraphicUploadProgress(percent, label = '') { setProgress('graphicUploadProgress', percent, label); }

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
  const duplicateBlock = authorState.duplicateMatches.length ? `<div class="import-warning"><h4>Похоже, такое произведение уже есть</h4><p>Проверьте совпадения перед сохранением:</p><ul>${authorState.duplicateMatches.slice(0, 6).map((item) => `<li><b>${escapeHtml(item.title || 'Произведение')}</b> — ${escapeHtml(item.reason || 'похожее название')}</li>`).join('')}</ul><label class="switch-row"><input id="allowDuplicateImport" type="checkbox"><span>Это другое произведение или новая редакция</span></label></div>` : '';
  box.innerHTML = `<h3>Предпросмотр импорта</h3><p><b>${escapeHtml(result.filename)}</b></p><div class="import-numbers"><span>${Number(report.chapters_count || 0)} глав</span><span>${Number(report.total_chars || 0).toLocaleString('ru-RU')} знаков</span></div>${problems}${duplicateBlock}<ol>${preview}</ol><button class="button-link" id="confirmBookImport" type="button">Сохранить главы</button>`;
  box.hidden = false;
}

async function confirmBookImport() {
  const bookId = authorState.book?.book?.id;
  if (!bookId || !authorState.previewToken) return;
  try {
    const allowDuplicate = Boolean(document.getElementById('allowDuplicateImport')?.checked);
    if (authorState.duplicateMatches.length && !allowDuplicate) {
      notify('Подтвердите, что это другое произведение или новая редакция');
      document.getElementById('allowDuplicateImport')?.focus();
      return;
    }
    const pricingMode = currentTextPricingMode();
    const result = await apiFetch(`/api/author/book/${bookId}/import-confirm`, { method: 'POST', body: JSON.stringify({
      preview_token: authorState.previewToken,
      first_free: pricingMode === 'free' ? 100000 : 3,
      default_price_stars: 0,
      allow_duplicate: allowDuplicate,
    }) });
    const workflow = result.workflow || {};
    if (workflow.status === 'published') notify(`Сохранено глав: ${result.saved}. Произведение опубликовано.`);
    else if (workflow.status === 'review') notify(`Сохранено глав: ${result.saved}. Произведение отправлено на проверку.`);
    else notify(`Сохранено глав: ${result.saved}`);
    authorState.previewToken = null;
    authorState.duplicateMatches = [];
    await openAuthorBook(bookId);
  } catch (error) { notify(error.message || 'Не удалось сохранить главы'); }
}

function graphicUploadValues() {
  return {
    title: document.getElementById('graphicChapterTitle').value.trim(),
    readingMode: document.getElementById('graphicChapterMode').value || 'inherit',
    price: Number(document.getElementById('graphicChapterPrice').value || 0),
    volumeNumber: Math.max(1, Number(document.getElementById('graphicVolumeNumber')?.value || 1)),
    volumeTitle: String(document.getElementById('graphicVolumeTitle')?.value || '').trim(),
    previewPages: Math.max(0, Math.min(20, Number(document.getElementById('graphicPreviewPages')?.value || 0))),
    splitLongPages: Boolean(document.getElementById('graphicSplitLongInput')?.checked),
  };
}

function renderGraphicUploadResult(result) {
  const report = result.report || {};
  const workflow = result.workflow || {};
  const box = document.getElementById('graphicUploadResult');
  if (!box) return;
  let statusText = 'Глава сохранена в черновике.';
  if (workflow.status === 'published') statusText = 'Глава сохранена, произведение опубликовано.';
  if (workflow.status === 'review') statusText = 'Глава сохранена, произведение отправлено на проверку.';
  const fragments = Number(report.webtoon_fragments || 0);
  box.innerHTML = `<h3>Графическая глава готова</h3><div class="import-numbers"><span>${Number(report.pages_count || 0)} страниц</span><span>${(Number(report.optimized_bytes || 0) / 1024 / 1024).toFixed(1)} МБ</span></div>${fragments ? `<p class="muted">Длинные страницы разделены на ${fragments} лёгких фрагментов.</p>` : ''}<p class="success-text">${escapeHtml(statusText)}</p>`;
  box.hidden = false;
}

function graphicUploadResumeKey(bookId, file) {
  return `voxGraphicUpload:${Number(bookId)}:${file.name}:${file.size}:${file.lastModified || 0}`;
}

async function sendGraphicChunkWithRetry({ bookId, uploadId, index, totalChunks, chunkSize, file }, attempts = 5) {
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const form = new FormData();
      form.append('index', String(index));
      form.append('total_chunks', String(totalChunks));
      form.append('chunk', file.slice(index * chunkSize, Math.min(file.size, (index + 1) * chunkSize)), `${file.name}.part`);
      return await apiFetch(`/api/author/book/${bookId}/graphic/upload/${uploadId}/chunk`, { method: 'POST', body: form });
    } catch (error) {
      lastError = error;
      if (attempt >= attempts) break;
      setGraphicUploadProgress((index / Math.max(1, totalChunks)) * 82, `Связь прервалась. Повтор ${attempt} из ${attempts - 1}…`);
      await new Promise((resolve) => setTimeout(resolve, Math.min(6000, 700 * (2 ** (attempt - 1)))));
    }
  }
  throw lastError || new Error('Не удалось передать часть файла');
}

async function uploadGraphicFile() {
  const file = document.getElementById('graphicFileInput').files?.[0];
  const bookId = authorState.book?.book?.id;
  const values = graphicUploadValues();
  if (!bookId) return;
  if (values.title.length < 2) { notify('Введите название главы'); document.getElementById('graphicChapterTitle').focus(); return; }
  if (!file) { notify('Выберите PDF, архив, EPUB или изображение'); return; }
  const resumeKey = graphicUploadResumeKey(bookId, file);
  const resumeUploadId = localStorage.getItem(resumeKey) || '';
  const start = await apiFetch(`/api/author/book/${bookId}/graphic/upload/start`, {
    method: 'POST', body: JSON.stringify({ filename: file.name, size: file.size, resume_upload_id: resumeUploadId }),
  });
  const uploadId = String(start.upload_id || '');
  localStorage.setItem(resumeKey, uploadId);
  const chunkSize = Number(start.chunk_size || 6 * 1024 * 1024);
  const totalChunks = Math.ceil(file.size / chunkSize);
  const received = new Set((Array.isArray(start.received) ? start.received : []).map(Number));
  if (start.resumed && received.size) notify(`Продолжаем загрузку с ${received.size + 1}-й части`);
  for (let index = 0; index < totalChunks; index += 1) {
    if (!received.has(index)) {
      await sendGraphicChunkWithRetry({ bookId, uploadId, index, totalChunks, chunkSize, file });
      received.add(index);
    }
    setGraphicUploadProgress((received.size / totalChunks) * 82, `Загружено ${received.size} из ${totalChunks}`);
  }
  setGraphicUploadProgress(88, 'Готовим адаптивные страницы…');
  const result = await apiFetch(`/api/author/book/${bookId}/graphic/upload/${uploadId}/finish`, {
    method: 'POST',
    body: JSON.stringify({ total_chunks: totalChunks, title: values.title, reading_mode: values.readingMode, price_stars: values.price, volume_number: values.volumeNumber, volume_title: values.volumeTitle, preview_pages: values.previewPages, split_long_pages: values.splitLongPages }),
  });
  localStorage.removeItem(resumeKey);
  return result;
}


async function uploadGraphicImages() {
  const files = Array.from(document.getElementById('graphicImagesInput').files || []);
  const bookId = authorState.book?.book?.id;
  const values = graphicUploadValues();
  if (!bookId) return;
  if (values.title.length < 2) { notify('Введите название главы'); document.getElementById('graphicChapterTitle').focus(); return; }
  if (!files.length) { notify('Выберите изображения страниц'); return; }
  const form = new FormData();
  form.append('title', values.title);
  form.append('reading_mode', values.readingMode);
  form.append('price_stars', String(values.price));
  form.append('volume_number', String(values.volumeNumber));
  form.append('volume_title', values.volumeTitle);
  form.append('preview_pages', String(values.previewPages));
  form.append('split_long_pages', values.splitLongPages ? 'true' : 'false');
  files.forEach((file) => form.append('files', file, file.name));
  setGraphicUploadProgress(20, `Загружаем ${files.length} страниц…`);
  return apiFetch(`/api/author/book/${bookId}/graphic/images`, { method: 'POST', body: form });
}

async function startGraphicUpload() {
  const button = document.getElementById('startGraphicUpload');
  button.disabled = true;
  const box = document.getElementById('graphicUploadResult');
  if (box) box.hidden = true;
  try {
    const result = authorState.graphicUploadTab === 'images' ? await uploadGraphicImages() : await uploadGraphicFile();
    if (!result) return;
    setGraphicUploadProgress(100, 'Глава готова');
    renderGraphicUploadResult(result);
    notify(`Загружено страниц: ${Number(result.report?.pages_count || 0)}`);
    await refreshAuthorDashboard();
  } catch (error) {
    notify(error.message || 'Не удалось загрузить графическую главу');
    setGraphicUploadProgress(0, 'Загрузка не завершена');
  } finally { button.disabled = false; }
}

function setGraphicUploadTab(tab) {
  authorState.graphicUploadTab = tab === 'images' ? 'images' : 'file';
  document.querySelectorAll('[data-graphic-upload-tab]').forEach((button) => button.classList.toggle('active', button.dataset.graphicUploadTab === authorState.graphicUploadTab));
  const filePicker = document.getElementById('graphicFilePicker');
  const imagePicker = document.getElementById('graphicImagesPicker');
  if (filePicker) filePicker.hidden = authorState.graphicUploadTab !== 'file';
  if (imagePicker) imagePicker.hidden = authorState.graphicUploadTab !== 'images';
}

function armDelete(button, message, resetText) {
  if (button.dataset.confirm === 'yes') return true;
  button.dataset.confirm = 'yes';
  button.textContent = message;
  setTimeout(() => { button.dataset.confirm = ''; button.textContent = resetText; }, 4500);
  return false;
}


function openNewProjectForm(kind = 'book') {
  const form = document.getElementById('newProjectForm');
  const type = document.getElementById('newProjectType');
  const mode = document.getElementById('newProjectReadingMode');
  const heading = document.getElementById('newProjectFormTitle');
  const hint = document.getElementById('newProjectFormHint');
  if (!form || !type || !mode) return;
  const graphic = kind === 'graphic';
  type.value = graphic ? 'comic' : 'book';
  mode.value = defaultReadingMode(type.value);
  if (heading) heading.textContent = graphic ? 'Новое графическое произведение' : 'Новая книга';
  if (hint) hint.textContent = graphic
    ? 'Выберите комикс, мангу, манхву, вебтун или графический роман. После создания откроется загрузка страниц.'
    : 'Создайте текстовую книгу. После создания откроется добавление и импорт глав.';
  form.hidden = false;
  document.getElementById('newProjectTitle')?.focus();
  form.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function createAuthorProject(event) {
  event.preventDefault();
  const title = document.getElementById('newProjectTitle').value.trim();
  const contentType = document.getElementById('newProjectType').value;
  const readingMode = document.getElementById('newProjectReadingMode').value;
  try {
    const result = await apiFetch('/api/author/projects', {
      method: 'POST', body: JSON.stringify({ title, content_type: contentType, reading_mode: readingMode }),
    });
    notify(`${contentTypeLabels[contentType] || 'Произведение'} создано`);
    document.getElementById('newProjectForm').reset();
    document.getElementById('newProjectForm').hidden = true;
    await refreshAuthorDashboard(false);
    if (result.book?.id) await openAuthorBook(result.book.id);
  } catch (error) { notify(error.message || 'Не удалось создать произведение'); }
}

function setModerationSuccessVisible(visible) {
  const dialog = document.getElementById('moderationSuccess');
  if (!dialog) return;
  dialog.hidden = !visible;
  document.body.classList.toggle('dialog-open', Boolean(visible));
  if (visible) document.getElementById('closeModerationSuccess')?.focus();
}

function bindAuthorEvents() {
  document.getElementById('authorSbpBank')?.addEventListener('change', (event) => {
    const option = event.target.options[event.target.selectedIndex];
    const hidden = document.getElementById('authorSbpBankName');
    if (hidden) hidden.value = option?.value ? option.textContent.trim() : '';
  });

  document.getElementById('authorFinancialProfileForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const bank = document.getElementById('authorSbpBank');
    const bankOption = bank?.options[bank.selectedIndex];
    const button = document.getElementById('authorSaveFinancialProfile');
    if (button) button.disabled = true;
    try {
      await apiFetch('/api/author/financial-profile', {
        method: 'PUT',
        body: JSON.stringify({
          legal_status: document.getElementById('authorLegalStatus').value,
          legal_name: document.getElementById('authorLegalName').value.trim(),
          inn: document.getElementById('authorInn').value.trim(),
          ogrn: document.getElementById('authorOgrn').value.trim(),
          country: 'RU',
          sbp_phone: document.getElementById('authorSbpPhone').value.trim(),
          sbp_bank_id: bank?.value || '',
          sbp_bank_name: bankOption?.value ? bankOption.textContent.trim() : document.getElementById('authorSbpBankName').value,
        }),
      });
      notify('Платёжный профиль отправлен на проверку');
      await refreshAuthorDashboard(false);
    } catch (error) {
      notify(error.message || 'Не удалось сохранить платёжный профиль');
    } finally {
      if (button) button.disabled = false;
    }
  });

  document.getElementById('authorRequestPayout')?.addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const amountRub = Number(document.getElementById('authorPayoutAmount').value || 0);
    if (!Number.isFinite(amountRub) || amountRub < 100) {
      notify('Минимальная заявка на выплату — 100 рублей');
      return;
    }
    button.disabled = true;
    try {
      await apiFetch('/api/author/rub-payouts', {
        method: 'POST',
        body: JSON.stringify({ amount_minor: Math.round(amountRub * 100) }),
      });
      notify('Заявка на выплату создана');
      await refreshAuthorDashboard(false);
    } catch (error) {
      notify(error.message || 'Не удалось создать заявку на выплату');
    } finally {
      const policy = authorState.dashboard?.pricing_policy || {};
      const finance = authorState.dashboard?.rub_finance || {};
      const verified = authorState.dashboard?.financial_profile?.verification_status === 'verified';
      button.disabled = !(policy.yookassa_payouts_configured && verified && Number(finance.available_minor || 0) >= Number(policy.payout_min_minor || 10000));
    }
  });

  document.addEventListener('click', async (event) => {
    const target = event.target.closest('button');
    if (!target || !document.getElementById('authorStudio')) return;
    if (target.id === 'closeModerationSuccess') { setModerationSuccessVisible(false); return; }
    if (target.dataset.pageMove) { await moveGraphicPage(target.dataset.pageId, Number(target.dataset.pageMove)); return; }
    if (target.dataset.pageRotate) { await rotateGraphicPage(target.dataset.pageId, Number(target.dataset.pageRotate)); return; }
    if (target.dataset.pageAdvanced) { openGraphicAdvancedEditor(target.dataset.pageAdvanced); return; }
    if (target.dataset.pageReplace) { chooseGraphicPageReplacement(target.dataset.pageReplace); return; }
    if (target.dataset.pageDelete) { await deleteGraphicPage(target, target.dataset.pageDelete); return; }
    if (target.id === 'closeGraphicPageEditor') { document.getElementById('graphicPageEditor').hidden = true; return; }
    if (target.dataset.authorBookId) { await openAuthorBook(target.dataset.authorBookId); return; }
    if (target.dataset.authorChapterPage) { renderAuthorChapters(authorState.book?.chapters || [], Number(target.dataset.authorChapterPage)); document.getElementById('authorChapters')?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    if (target.id === 'newTextProject') { openNewProjectForm('book'); return; }
    if (target.id === 'newGraphicProject') { openNewProjectForm('graphic'); return; }
    if (target.id === 'cancelNewProject') { document.getElementById('newProjectForm').hidden = true; return; }
    if (target.id === 'closeBookEditor') { document.getElementById('authorBookEditor').hidden = true; authorState.book = null; return; }
    if (target.id === 'newChapterButton') { resetChapterForm(); document.getElementById('chapterForm').hidden = false; return; }
    if (target.id === 'newChapterPackageButton') { resetChapterPackageForm(); document.getElementById('chapterPackageForm').hidden = false; return; }
    if (target.dataset.editChapterPackage) { editChapterPackage(target.dataset.editChapterPackage); return; }
    if (target.id === 'cancelChapterPackageEdit') { resetChapterPackageForm(); return; }
    if (target.dataset.editChapter) { await editChapter(target.dataset.editChapter); return; }
    if (target.id === 'cancelChapterEdit') { resetChapterForm(); return; }
    if (target.dataset.saveGraphicVolume) {
      const card = target.closest('[data-volume-card]');
      const number = Number(target.dataset.saveGraphicVolume || 1);
      const price = Math.max(0, Number(card?.querySelector('[data-volume-price]')?.value || 0));
      const title = String(card?.querySelector('[data-volume-title]')?.value || '').trim();
      try {
        await apiFetch(`/api/author/book/${authorState.book.book.id}/graphic-volume/${number}`, { method: 'PATCH', body: JSON.stringify({ title, price_stars: price, is_free: price <= 0 }) });
        notify(`Настройки тома ${number} сохранены`);
        await openAuthorBook(authorState.book.book.id);
      } catch (error) { notify(error.message || 'Не удалось сохранить том'); }
      return;
    }
    if (target.dataset.editGraphicChapter) { editGraphicChapter(target.dataset.editGraphicChapter); return; }
    if (target.id === 'cancelGraphicChapterEdit') { resetGraphicChapterForm(); return; }
    if (target.dataset.graphicUploadTab) { setGraphicUploadTab(target.dataset.graphicUploadTab); return; }
    if (target.id === 'startBookUpload') { await uploadBookFile(); return; }
    if (target.id === 'startGraphicUpload') { await startGraphicUpload(); return; }
    if (target.id === 'confirmBookImport') { await confirmBookImport(); return; }
    if (target.id === 'submitBookReview') {
      try { await apiFetch(`/api/author/book/${authorState.book.book.id}/submit`, { method: 'POST' }); notify('Произведение отправлено на проверку'); setModerationSuccessVisible(true); await refreshAuthorDashboard(); }
      catch (error) { notify(error.message); }
      return;
    }
    if (target.id === 'deleteBookButton') {
      if (!armDelete(target, 'Нажмите ещё раз — удалить', 'Удалить книгу')) return;
      try { await apiFetch(`/api/author/book/${authorState.book.book.id}`, { method: 'DELETE' }); notify('Произведение удалено'); document.getElementById('authorBookEditor').hidden = true; authorState.book = null; await refreshAuthorDashboard(false); }
      catch (error) { notify(error.message); }
      return;
    }
    if (target.id === 'deleteChapterButton') {
      if (!armDelete(target, 'Нажмите ещё раз — удалить', 'Удалить главу')) return;
      const chapterId = document.getElementById('chapterIdInput').value;
      try { await apiFetch(`/api/author/chapter/${chapterId}`, { method: 'DELETE' }); notify('Глава удалена'); await openAuthorBook(authorState.book.book.id); }
      catch (error) { notify(error.message); }
      return;
    }
    if (target.id === 'deleteChapterPackageButton') {
      if (!armDelete(target, 'Нажмите ещё раз — убрать', 'Убрать пакет')) return;
      const packageId = document.getElementById('chapterPackageIdInput').value;
      try {
        await apiFetch(`/api/author/chapter-package/${packageId}`, { method: 'DELETE' });
        notify('Пакет скрыт. Уже купленные открытия сохранены у читателей.');
        resetChapterPackageForm();
        await openAuthorBook(authorState.book.book.id);
      } catch (error) { notify(error.message || 'Не удалось убрать пакет'); }
      return;
    }
    if (target.id === 'deleteGraphicChapterButton') {
      if (!armDelete(target, 'Нажмите ещё раз — удалить', 'Удалить главу')) return;
      const chapterId = document.getElementById('graphicChapterIdInput').value;
      try { await apiFetch(`/api/author/graphic-chapter/${chapterId}`, { method: 'DELETE' }); notify('Графическая глава удалена'); resetGraphicChapterForm(); await openAuthorBook(authorState.book.book.id); }
      catch (error) { notify(error.message); }
      return;
    }
  });

  document.getElementById('newProjectType')?.addEventListener('change', (event) => {
    document.getElementById('newProjectReadingMode').value = defaultReadingMode(event.target.value);
  });
  document.getElementById('newProjectForm')?.addEventListener('submit', createAuthorProject);

  document.getElementById('bookFileInput')?.addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    document.getElementById('bookFileName').textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} МБ` : 'Файл не выбран';
  });
  document.getElementById('graphicFileInput')?.addEventListener('change', (event) => {
    const file = event.target.files?.[0];
    document.getElementById('graphicFileName').textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} МБ` : 'Файл не выбран';
  });
  document.getElementById('graphicImagesInput')?.addEventListener('change', (event) => {
    const files = Array.from(event.target.files || []);
    const total = files.reduce((sum, file) => sum + file.size, 0);
    document.getElementById('graphicImagesName').textContent = files.length ? `${files.length} страниц · ${(total / 1024 / 1024).toFixed(1)} МБ` : 'Изображения не выбраны';
  });

  document.getElementById('graphicPageReplacementInput')?.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (file) await replaceGraphicPage(file);
  });

  document.getElementById('graphicChapterMode')?.addEventListener('change', (event) => {
    if (event.target.value === 'vertical') document.getElementById('graphicSplitLongInput').checked = true;
  });

  document.getElementById('bookContentTypeInput')?.addEventListener('change', (event) => {
    const type = event.target.value;
    toggleProjectPanels(type);
    document.getElementById('bookReadingModeInput').value = defaultReadingMode(type);
  });

  const graphicGrid = document.getElementById('graphicPageGrid');
  graphicGrid?.addEventListener('dragstart', (event) => {
    const card = event.target.closest('[data-graphic-page-card]');
    if (!card) return;
    authorState.graphicDraggedPageId = Number(card.dataset.graphicPageCard);
    card.classList.add('dragging');
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
  });
  graphicGrid?.addEventListener('dragover', (event) => {
    event.preventDefault();
    const dragged = graphicGrid.querySelector(`[data-graphic-page-card="${authorState.graphicDraggedPageId}"]`);
    const targetCard = event.target.closest('[data-graphic-page-card]');
    if (!dragged || !targetCard || dragged === targetCard) return;
    const rect = targetCard.getBoundingClientRect();
    const before = event.clientY < rect.top + rect.height / 2 || (Math.abs(event.clientY - (rect.top + rect.height / 2)) < rect.height / 3 && event.clientX < rect.left + rect.width / 2);
    graphicGrid.insertBefore(dragged, before ? targetCard : targetCard.nextElementSibling);
  });
  graphicGrid?.addEventListener('drop', async (event) => {
    event.preventDefault();
    if (authorState.graphicDraggedPageId) await saveGraphicPageOrder();
  });
  graphicGrid?.addEventListener('dragend', (event) => {
    event.target.closest('[data-graphic-page-card]')?.classList.remove('dragging');
    authorState.graphicDraggedPageId = null;
  });

  document.getElementById('graphicProcessChapterOcr')?.addEventListener('click', () => processGraphicChapterPages('ocr'));
  document.getElementById('graphicProcessChapterFrames')?.addEventListener('click', () => processGraphicChapterPages('frames'));
  document.getElementById('graphicChapterStatistics')?.addEventListener('click', showGraphicChapterStatistics);
  document.getElementById('graphicAdvancedEditorClose')?.addEventListener('click', () => { document.getElementById('graphicAdvancedEditor').hidden = true; });
  document.getElementById('graphicRunOcr')?.addEventListener('click', runGraphicOcr);
  document.getElementById('graphicSaveOcrText')?.addEventListener('click', saveGraphicOcrText);
  document.getElementById('graphicAutoFrames')?.addEventListener('click', autoGraphicFrames);
  document.getElementById('graphicSaveFrames')?.addEventListener('click', saveGraphicFrames);
  document.getElementById('graphicSaveTranslations')?.addEventListener('click', saveGraphicTranslations);
  document.getElementById('graphicAddFrame')?.addEventListener('click', () => {
    const box = document.getElementById('graphicFramesEditor');
    if (box.querySelector('.muted-text')) box.innerHTML = '';
    box.insertAdjacentHTML('beforeend', graphicCoordinateRow({ x: 0, y: 0, width: 1, height: 1 }, 'frame'));
  });
  document.getElementById('graphicAddTranslationRegion')?.addEventListener('click', () => {
    const box = document.getElementById('graphicTranslationsEditor');
    if (box.querySelector('.muted-text')) box.innerHTML = '';
    box.insertAdjacentHTML('beforeend', graphicCoordinateRow({ x: 0.1, y: 0.1, width: 0.4, height: 0.15, text: '' }, 'translation'));
  });
  document.getElementById('graphicAdvancedEditor')?.addEventListener('click', (event) => {
    const remove = event.target.closest('[data-remove-coordinate]');
    if (remove) remove.closest('[data-coordinate-row]')?.remove();
  });

  document.getElementById('bookEditForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const bookId = authorState.book?.book?.id;
    if (!bookId) return;
    const oldMode = currentTextPricingMode();
    const pricingType = document.getElementById('bookPricingModeInput').value || 'free';
    const price = ['whole_book', 'chapters'].includes(pricingType)
      ? Math.max(1, Number(document.getElementById('bookPriceInput').value || 1))
      : 0;
    let confirmMakeFree = false;
    if (oldMode !== 'free' && pricingType === 'free') {
      confirmMakeFree = window.confirm(
        'Сделать книгу полностью бесплатной? Все существующие и будущие главы станут бесплатными, а продажа и доступ по Premium отключатся.'
      );
      if (!confirmMakeFree) return;
    }
    try {
      await apiFetch(`/api/author/book/${bookId}`, { method: 'PATCH', body: JSON.stringify({
        title: document.getElementById('bookTitleInput').value,
        description: document.getElementById('bookDescriptionInput').value,
        age_limit: document.getElementById('bookAgeInput').value,
        writing_status: document.getElementById('bookWritingInput').value,
        price_stars: price,
        pricing_type: pricingType,
        confirm_make_free: confirmMakeFree,
        allow_download: document.getElementById('bookDownloadInput').checked,
        content_type: document.getElementById('bookContentTypeInput').value,
        reading_mode: document.getElementById('bookReadingModeInput').value,
      }) });
      notify(pricingType === 'free' ? 'Книга и все главы теперь бесплатны' : pricingType === 'premium' ? 'Книга доступна по VoxLyra Premium' : 'Произведение сохранено');
      await refreshAuthorDashboard();
    } catch (error) { notify(typeof error.message === 'string' ? error.message : 'Не удалось сохранить произведение'); }
  });

  ['bookPriceInput', 'bookPricingModeInput', 'bookContentTypeInput'].forEach((id) => {
    document.getElementById(id)?.addEventListener('input', syncBookPricingDraftControls);
    document.getElementById(id)?.addEventListener('change', syncBookPricingDraftControls);
  });
  document.getElementById('chapterBulkAccessInput')?.addEventListener('change', syncChapterAccessInputs);
  document.getElementById('chapterAccessInput')?.addEventListener('change', syncChapterAccessInputs);
  document.getElementById('restoreChapterPricesButton')?.addEventListener('click', async () => {
    const bookId = authorState.book?.book?.id;
    if (!bookId) return;
    if (!window.confirm('Восстановить сохранённые ранее цены глав? Бесплатные главы останутся бесплатными, а сохранённые платные цены снова начнут действовать.')) return;
    try {
      const result = await apiFetch(`/api/author/book/${bookId}/restore-chapter-prices`, { method: 'POST' });
      notify(`Восстановлено цен глав: ${Number(result.updated || 0)}`);
      await openAuthorBook(bookId);
    } catch (error) { notify(error.message || 'Не удалось восстановить цены глав'); }
  });

  ['chapterPackageCountInput', 'chapterPackagePriceInput'].forEach((id) => {
    document.getElementById(id)?.addEventListener('input', updateChapterPackagePreview);
  });

  document.getElementById('chapterPackageForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const bookId = authorState.book?.book?.id;
    if (!bookId) return;
    const packageId = document.getElementById('chapterPackageIdInput').value;
    const payload = {
      title: document.getElementById('chapterPackageTitleInput').value,
      chapters_count: Number(document.getElementById('chapterPackageCountInput').value || 0),
      price_stars: Number(document.getElementById('chapterPackagePriceInput').value || 0),
      content_scope: document.getElementById('chapterPackageScopeInput').value,
      is_active: document.getElementById('chapterPackageActiveInput').checked,
    };
    try {
      if (packageId) {
        await apiFetch(`/api/author/chapter-package/${packageId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      } else {
        await apiFetch(`/api/author/book/${bookId}/chapter-packages`, { method: 'POST', body: JSON.stringify(payload) });
      }
      notify(packageId ? 'Пакет обновлён' : 'Пакет создан');
      await openAuthorBook(bookId);
    } catch (error) { notify(error.message || 'Не удалось сохранить пакет'); }
  });

  document.getElementById('chapterBulkPriceForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const bookId = authorState.book?.book?.id;
    if (!bookId) return;
    let startNumber = Number(document.getElementById('chapterBulkStartInput').value || 0);
    let endNumber = Number(document.getElementById('chapterBulkEndInput').value || 0);
    const accessMode = document.getElementById('chapterBulkAccessInput').value;
    const priceStars = accessMode === 'chapter' ? Number(document.getElementById('chapterBulkPriceInput').value || 0) : 0;
    if (!Number.isInteger(startNumber) || !Number.isInteger(endNumber) || startNumber < 1 || endNumber < 1) {
      notify('Укажите корректные номера глав');
      return;
    }
    if (startNumber > endNumber) [startNumber, endNumber] = [endNumber, startNumber];
    if (accessMode === 'chapter' && (!Number.isInteger(priceStars) || priceStars < 1 || priceStars > 100000)) {
      notify('Для отдельной продажи укажите цену от 1 до 100 000 Stars');
      return;
    }
    try {
      const result = await apiFetch(`/api/author/book/${bookId}/chapter-prices`, {
        method: 'PATCH',
        body: JSON.stringify({ start_number: startNumber, end_number: endNumber, access_mode: accessMode, price_stars: priceStars }),
      });
      const updated = Number(result.updated || 0);
      const message = accessMode === 'free'
        ? `${updated} глав открыты бесплатно`
        : accessMode === 'premium'
          ? `${updated} глав доступны по VoxLyra Premium`
          : accessMode === 'book'
            ? `${updated} глав открываются после покупки всей книги`
            : `Для ${updated} глав установлена отдельная цена ${priceStars} Stars`;
      notify(message);
      await openAuthorBook(bookId);
    } catch (error) { notify(error.message || 'Не удалось изменить доступ к главам'); }
  });

  document.getElementById('chapterForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const bookId = authorState.book?.book?.id;
    const chapterId = document.getElementById('chapterIdInput').value;
    const accessMode = document.getElementById('chapterAccessInput').value;
    const payload = {
      title: document.getElementById('chapterTitleInput').value,
      text: document.getElementById('chapterTextInput').value,
      access_mode: accessMode,
      price_stars: accessMode === 'chapter' ? Number(document.getElementById('chapterPriceInput').value || 0) : 0,
    };
    try {
      if (chapterId) await apiFetch(`/api/author/chapter/${chapterId}`, { method: 'PATCH', body: JSON.stringify(payload) });
      else await apiFetch(`/api/author/book/${bookId}/chapters`, { method: 'POST', body: JSON.stringify(payload) });
      notify(chapterId ? 'Глава обновлена' : 'Глава добавлена');
      await openAuthorBook(bookId);
    } catch (error) { notify(error.message || 'Не удалось сохранить главу'); }
  });

  document.getElementById('graphicChapterEditForm')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const chapterId = document.getElementById('graphicChapterIdInput').value;
    if (!chapterId) return;
    try {
      await apiFetch(`/api/author/graphic-chapter/${chapterId}`, {
        method: 'PATCH', body: JSON.stringify({
          title: document.getElementById('graphicChapterEditTitle').value,
          reading_mode: document.getElementById('graphicChapterEditMode').value,
          price_stars: Number(document.getElementById('graphicChapterEditPrice').value || 0),
          volume_number: Number(document.getElementById('graphicChapterEditVolumeNumber').value || 1),
          volume_title: document.getElementById('graphicChapterEditVolumeTitle').value,
          preview_pages: Number(document.getElementById('graphicChapterEditPreview').value || 0),
        }),
      });
      notify('Графическая глава обновлена');
      await openAuthorBook(authorState.book.book.id);
    } catch (error) { notify(error.message || 'Не удалось сохранить главу'); }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  if (!document.getElementById('authorStudio')) return;
  document.querySelector('[data-close-moderation]')?.addEventListener('click', () => setModerationSuccessVisible(false));
  bindAuthorEvents();
  document.getElementById('authorAnalyticsPeriod')?.addEventListener('change', (event) => loadAuthorAnalytics(event.target.value));
  setGraphicUploadTab('file');
  loadAuthorDashboard();
  const newKind = new URLSearchParams(window.location.search).get('new');
  if (newKind === 'graphic') setTimeout(() => openNewProjectForm('graphic'), 250);
  else if (newKind === 'book') setTimeout(() => openNewProjectForm('book'), 250);
});
