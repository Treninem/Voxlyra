(() => {
  'use strict';

  const panel = document.getElementById('crossPlatformAccountPanel');
  const statusNode = document.getElementById('crossPlatformStatus');
  const form = document.getElementById('smartAccountLinkForm');
  const input = document.getElementById('smartAccountLinkTarget');
  const submit = document.getElementById('smartAccountLinkSubmit');
  const stateNode = document.getElementById('smartAccountLinkState');
  const incomingNode = document.getElementById('smartAccountLinkIncoming');
  const refreshButton = document.getElementById('crossPlatformRefresh');
  const hint = document.getElementById('smartAccountLinkHint');
  if (!panel || !statusNode || !form || !input || !submit || !stateNode || !incomingNode || typeof window.apiFetch !== 'function') return;

  const STORAGE_KEY = 'voxlyra:smart-account-link:v2';
  let polling = false;
  let timer = 0;

  function esc(value) {
    const node = document.createElement('div');
    node.textContent = String(value ?? '');
    return node.innerHTML;
  }

  function platform() {
    try { return typeof window.voxPlatform === 'function' ? window.voxPlatform() : 'web'; }
    catch (_) { return 'web'; }
  }

  function platformName(value) {
    return value === 'vk' ? 'VK' : 'Telegram';
  }

  function otherPlatform() {
    return platform() === 'vk' ? 'telegram' : 'vk';
  }

  function readSaved() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      return value && typeof value === 'object' ? value : {};
    } catch (_) { return {}; }
  }

  function saveRequest(request) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({
        token: String(request?.token || ''),
        sourcePlatform: platform(),
        targetPlatform: String(request?.target_platform || otherPlatform()),
        targetLabel: String(request?.target_label || ''),
      }));
    } catch (_) {}
  }

  function clearSaved() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
  }

  function setState(html, hidden = false) {
    stateNode.hidden = Boolean(hidden);
    stateNode.innerHTML = hidden ? '' : html;
  }

  function configureInput() {
    const target = otherPlatform();
    if (target === 'vk') {
      input.placeholder = 'VK: @username, id123 или числовой ID';
      if (hint) hint.textContent = 'Введите свой VK @username или ID. В сообщения сообщества VK придёт запрос на подтверждение.';
    } else {
      input.placeholder = 'Telegram: @username или числовой ID';
      if (hint) hint.textContent = 'Введите свой Telegram @username или ID. В Telegram-бот VoxLyra придёт запрос на подтверждение.';
    }
  }

  async function refreshStatus() {
    try {
      const data = await window.apiFetch('/api/account-link/status');
      const tg = Boolean(data.telegram);
      const vk = Boolean(data.vk);
      if (data.linked) {
        statusNode.innerHTML = '<p><b>✅ Telegram и VK объединены.</b><br><span class="muted">Используется один профиль, одна библиотека, покупки и общий прогресс.</span></p>';
        form.hidden = true;
        clearSaved();
      } else {
        form.hidden = false;
        statusNode.innerHTML = `<p><b>Текущая платформа: ${esc(platformName(data.platform))}</b><br><span class="muted">Telegram: ${tg ? 'подключён' : 'не подключён'} · VK: ${vk ? 'подключён' : 'не подключён'}</span></p>`;
      }
      return Boolean(data.linked);
    } catch (error) {
      statusNode.innerHTML = `<p class="muted">${esc(error?.message || 'Не удалось проверить привязку')}</p>`;
      return false;
    }
  }

  function renderOutgoing(request) {
    const label = request.target_label || `${platformName(request.target_platform)} ID ${request.target_external_id || ''}`;
    const delivery = request.delivery_status === 'sent'
      ? `Сообщение отправлено в ${platformName(request.target_platform)}.`
      : 'Не удалось доставить сообщение автоматически. Запрос всё равно сохранён: откройте VoxLyra на втором аккаунте и раздел «Настройки».';
    setState(
      `<article class="setting-card"><h3>⏳ Ждём подтверждение</h3><p>Аккаунт: <b>${esc(label)}</b></p><p>${esc(delivery)}</p><p class="muted">После подтверждения VoxLyra объединит данные автоматически. Запрос действует 10 минут.</p><button type="button" class="secondary full" data-smart-link-cancel="${esc(request.token)}">Отменить запрос</button></article>`
    );
    stateNode.querySelector('[data-smart-link-cancel]')?.addEventListener('click', cancelOutgoing);
  }

  async function cancelOutgoing(event) {
    const button = event.currentTarget;
    const token = String(button?.dataset?.smartLinkCancel || '');
    if (!token) return;
    button.disabled = true;
    try {
      await window.apiFetch(`/api/account-link/request/${encodeURIComponent(token)}/cancel`, { method: 'POST' });
      clearSaved();
      setState('<article class="setting-card"><p>Запрос отменён. Данные аккаунтов не изменялись.</p></article>');
    } catch (error) {
      window.notify?.(error?.message || 'Не удалось отменить запрос');
      button.disabled = false;
    }
  }

  async function pollOutgoing(token) {
    if (!token || polling || document.hidden) return;
    polling = true;
    try {
      const request = await window.apiFetch(`/api/account-link/request/${encodeURIComponent(token)}`);
      if (request.status === 'pending') {
        renderOutgoing(request);
        return;
      }
      clearSaved();
      if (request.status === 'confirmed') {
        setState('<article class="setting-card"><h3>✅ Аккаунты объединены</h3><p>Telegram и VK теперь используют один профиль VoxLyra.</p></article>');
        window.notify?.('Telegram и VK объединены');
        await refreshStatus();
      } else if (request.status === 'rejected') {
        setState('<article class="setting-card"><h3>❌ Запрос отклонён</h3><p>Никакие данные не изменены.</p></article>');
      } else if (request.status === 'expired') {
        setState('<article class="setting-card"><h3>⌛ Запрос истёк</h3><p>Отправьте новый запрос, если хотите объединить аккаунты.</p></article>');
      } else {
        setState(`<article class="setting-card"><p>Запрос завершён: ${esc(request.status || 'неизвестно')}.</p></article>`);
      }
    } catch (error) {
      if (Number(error?.status || 0) === 404) clearSaved();
    } finally {
      polling = false;
    }
  }

  function renderIncoming(request) {
    if (!request || request.status !== 'pending') {
      incomingNode.hidden = true;
      incomingNode.innerHTML = '';
      return;
    }
    incomingNode.hidden = false;
    incomingNode.innerHTML = `
      <article class="setting-card">
        <span class="eyebrow">Подтверждение второго аккаунта</span>
        <h3>🔗 Объединить с ${esc(request.source_label || platformName(request.source_platform))}?</h3>
        <p>Запрос пришёл из ${esc(platformName(request.source_platform))}. Подтверждайте только если это действительно ваш второй аккаунт.</p>
        <p class="muted">После подтверждения покупки и баланс сохранятся, одинаковые позиции чтения будут объединены по максимальному прогрессу, повторно платить не нужно.</p>
        <div class="segmented two">
          <button type="button" data-smart-link-confirm="${esc(request.token)}">✅ Подтвердить</button>
          <button type="button" class="secondary" data-smart-link-reject="${esc(request.token)}">Отклонить</button>
        </div>
      </article>`;
    incomingNode.querySelector('[data-smart-link-confirm]')?.addEventListener('click', decideIncoming);
    incomingNode.querySelector('[data-smart-link-reject]')?.addEventListener('click', decideIncoming);
  }

  async function decideIncoming(event) {
    const button = event.currentTarget;
    const token = String(button?.dataset?.smartLinkConfirm || button?.dataset?.smartLinkReject || '');
    const action = button?.dataset?.smartLinkConfirm ? 'confirm' : 'reject';
    if (!token) return;
    incomingNode.querySelectorAll('button').forEach((item) => { item.disabled = true; });
    try {
      await window.apiFetch(`/api/account-link/request/${encodeURIComponent(token)}/${action}`, { method: 'POST' });
      if (action === 'confirm') {
        clearSaved();
        window.notify?.('Telegram и VK успешно объединены');
        incomingNode.innerHTML = '<article class="setting-card"><h3>✅ Готово</h3><p>Оба входа теперь ведут в один профиль VoxLyra.</p></article>';
        window.setTimeout(() => window.location.reload(), 700);
      } else {
        window.notify?.('Запрос отклонён');
        incomingNode.hidden = true;
        incomingNode.innerHTML = '';
      }
    } catch (error) {
      window.notify?.(error?.message || 'Не удалось обработать запрос');
      incomingNode.querySelectorAll('button').forEach((item) => { item.disabled = false; });
    }
  }

  async function refreshIncoming() {
    try {
      const data = await window.apiFetch('/api/account-link/incoming');
      renderIncoming(data?.request || null);
    } catch (_) {
      renderIncoming(null);
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const target = String(input.value || '').trim();
    if (!target) {
      window.notify?.('Введите @username или ID аккаунта на второй платформе');
      return;
    }
    submit.disabled = true;
    input.disabled = true;
    try {
      const result = await window.apiFetch('/api/account-link/request', {
        method: 'POST',
        body: JSON.stringify({ target }),
      });
      if (result.already_linked) {
        window.notify?.('Эти аккаунты уже объединены');
        await refreshStatus();
        return;
      }
      saveRequest(result);
      renderOutgoing(result);
      input.value = '';
      window.notify?.(`Запрос отправлен в ${platformName(result.target_platform)}`);
    } catch (error) {
      window.notify?.(error?.message || 'Не удалось отправить запрос на объединение');
    } finally {
      submit.disabled = false;
      input.disabled = false;
    }
  });

  refreshButton?.addEventListener('click', async () => {
    refreshButton.disabled = true;
    try {
      await Promise.all([refreshStatus(), refreshIncoming()]);
      const saved = readSaved();
      if (saved.token && saved.sourcePlatform === platform()) await pollOutgoing(saved.token);
    } finally {
      refreshButton.disabled = false;
    }
  });

  configureInput();
  Promise.all([refreshStatus(), refreshIncoming()]).then(() => {
    const saved = readSaved();
    if (saved.token && saved.sourcePlatform === platform()) pollOutgoing(saved.token);
  });

  timer = window.setInterval(() => {
    const saved = readSaved();
    if (saved.token && saved.sourcePlatform === platform()) pollOutgoing(saved.token);
    refreshIncoming();
  }, 3000);
  window.addEventListener('beforeunload', () => window.clearInterval(timer), { once: true });
})();
