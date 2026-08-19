(() => {
  'use strict';

  function statusText(platform, status) {
    const value = String(status || 'failed');
    if (value === 'sent') return `${platform}: опубликовано`;
    if (value === 'already_sent') return `${platform}: уже опубликовано`;
    if (value === 'not_configured') return `${platform}: не настроено`;
    if (value === 'failed') return `${platform}: ошибка`;
    return `${platform}: ${value}`;
  }

  document.addEventListener('click', async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest('[data-action^="book:repost:"]');
    if (!button) return;

    // The legacy owner handler posts only to Telegram. Capture the click before
    // that handler and send it through the cross-platform endpoint instead.
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const parts = String(button.dataset.action || '').split(':');
    const bookId = Number(parts[2] || 0);
    if (!Number.isInteger(bookId) || bookId <= 0) {
      window.notify?.('Не удалось определить книгу');
      return;
    }
    if (!window.confirm('Выложить книгу повторно одновременно в Telegram-канал и VK?')) return;

    const oldDisabled = button.disabled;
    button.disabled = true;
    try {
      const result = await window.apiFetch(`/api/control/book/${bookId}/repost-platforms`, { method: 'POST' });
      const tg = statusText('Telegram', result?.telegram?.status);
      const vk = statusText('VK', result?.vk?.status);
      window.notify?.(`${tg} · ${vk}`);
    } catch (error) {
      window.notify?.(error?.message || 'Не удалось выполнить публикацию в Telegram и VK');
    } finally {
      button.disabled = oldDisabled;
    }
  }, true);
})();
