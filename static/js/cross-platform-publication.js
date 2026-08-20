(() => {
  'use strict';

  function statusText(platform, status) {
    const value = String(status || 'failed');
    if (value === 'sent') return `${platform}: опубликовано`;
    if (value === 'already_sent') return `${platform}: уже опубликовано`;
    if (value === 'not_configured') return `${platform}: не настроено`;
    if (value === 'skipped') return `${platform}: не выбрано`;
    if (value === 'failed') return `${platform}: ошибка`;
    return `${platform}: ${value}`;
  }

  function ensurePickerStyles() {
    if (document.getElementById('crossPlatformRepostStyles')) return;
    const style = document.createElement('style');
    style.id = 'crossPlatformRepostStyles';
    style.textContent = `
      .cross-platform-repost-overlay{position:fixed;inset:0;z-index:10050;display:flex;align-items:center;justify-content:center;padding:20px;background:rgba(0,0,0,.58);backdrop-filter:blur(5px)}
      .cross-platform-repost-card{width:min(390px,100%);padding:20px;border-radius:18px;background:var(--card,#17191f);box-shadow:0 22px 70px rgba(0,0,0,.42);color:var(--text,#fff)}
      .cross-platform-repost-title{margin:0 0 6px;font-size:18px;font-weight:750}
      .cross-platform-repost-hint{margin:0 0 16px;opacity:.72;font-size:13px;line-height:1.4}
      .cross-platform-repost-actions{display:grid;gap:9px}
      .cross-platform-repost-choice{width:100%;min-height:44px;border:0;border-radius:12px;padding:10px 14px;font:inherit;font-weight:650;cursor:pointer;background:rgba(255,255,255,.1);color:inherit}
      .cross-platform-repost-choice:hover,.cross-platform-repost-choice:focus{outline:2px solid currentColor;outline-offset:1px}
      .cross-platform-repost-choice[data-target="both"]{background:var(--accent,#7c5cff);color:#fff}
      .cross-platform-repost-cancel{margin-top:4px;opacity:.72}
    `;
    document.head.appendChild(style);
  }

  function chooseRepostTarget() {
    ensurePickerStyles();
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'cross-platform-repost-overlay';
      overlay.setAttribute('role', 'presentation');

      const card = document.createElement('div');
      card.className = 'cross-platform-repost-card';
      card.setAttribute('role', 'dialog');
      card.setAttribute('aria-modal', 'true');
      card.setAttribute('aria-label', 'Куда переопубликовать книгу');

      const title = document.createElement('p');
      title.className = 'cross-platform-repost-title';
      title.textContent = 'Куда переопубликовать?';
      const hint = document.createElement('p');
      hint.className = 'cross-platform-repost-hint';
      hint.textContent = 'По умолчанию выбраны обе платформы.';
      const actions = document.createElement('div');
      actions.className = 'cross-platform-repost-actions';

      const choices = [
        ['both', 'Telegram + VK'],
        ['telegram', 'Только Telegram'],
        ['vk', 'Только VK'],
        ['', 'Отмена'],
      ];
      let defaultButton = null;

      function finish(target) {
        document.removeEventListener('keydown', onKeyDown, true);
        overlay.remove();
        resolve(target || null);
      }

      function onKeyDown(event) {
        if (event.key === 'Escape') {
          event.preventDefault();
          finish(null);
        }
      }

      choices.forEach(([target, label]) => {
        const choice = document.createElement('button');
        choice.type = 'button';
        choice.className = `cross-platform-repost-choice${target ? '' : ' cross-platform-repost-cancel'}`;
        choice.dataset.target = target;
        choice.textContent = label;
        choice.addEventListener('click', () => finish(target));
        actions.appendChild(choice);
        if (target === 'both') defaultButton = choice;
      });

      card.append(title, hint, actions);
      overlay.appendChild(card);
      overlay.addEventListener('click', (event) => {
        if (event.target === overlay) finish(null);
      });
      document.addEventListener('keydown', onKeyDown, true);
      document.body.appendChild(overlay);
      defaultButton?.focus();
    });
  }

  document.addEventListener('click', async (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest('[data-action^="book:repost:"]');
    if (!button) return;

    // The legacy owner handler posts only to Telegram. Capture the click before
    // that handler and route it through the platform-aware repost endpoint.
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();

    const parts = String(button.dataset.action || '').split(':');
    const bookId = Number(parts[2] || 0);
    if (!Number.isInteger(bookId) || bookId <= 0) {
      window.notify?.('Не удалось определить книгу');
      return;
    }

    const target = await chooseRepostTarget();
    if (!target) return;

    const oldDisabled = button.disabled;
    button.disabled = true;
    try {
      const result = await window.apiFetch(`/api/control/repost-platforms/${bookId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target }),
      });
      const parts = [];
      if (result?.telegram?.status !== 'skipped') parts.push(statusText('Telegram', result?.telegram?.status));
      if (result?.vk?.status !== 'skipped') parts.push(statusText('VK', result?.vk?.status));
      window.notify?.(parts.join(' · ') || 'Публикация не выполнена');
    } catch (error) {
      window.notify?.(error?.message || 'Не удалось выполнить переопубликацию');
    } finally {
      button.disabled = oldDisabled;
    }
  }, true);
})();
