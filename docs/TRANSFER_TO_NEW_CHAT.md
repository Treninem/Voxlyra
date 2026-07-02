# Перенос проекта Вокслира в новый чат

Проект: Вокслира / Voxlyra.

Формат: Telegram-бот + Mini App + SQLite + GitHub → Bothost.

Актуальная сборка: v1.4.0-stage14.

## Главная логика

- Читатели читают и слушают книги через Telegram Mini App.
- Авторы регистрируются один раз, затем добавляют книги, главы, аудио, промокоды и рекламу.
- Владелец имеет скрытое меню `👑 Управление`, видимое только ID из `OWNER_IDS`.
- Администрация добавляется владельцем и видит только выданные права.
- Версия проекта видна только владельцу в `👑 Управление → 🧩 Система`.
- Пользовательские команды не выставляются как основной способ управления, всё ведётся кнопками и Mini App.

## Важные переменные

```env
BOT_TOKEN=токен_от_BotFather
BOT_USERNAME=username_бота_без_@
OWNER_IDS=Telegram_ID_владельца
DATABASE_PATH=data/voxlyra.sqlite3
RUN_WEBAPP=true
PORT=8080
WEBAPP_URL=https://публичный_адрес_Bothost
CHANNEL_ID=@username_канала
PROJECT_NAME=Вокслира
PUBLIC_VERSION_VISIBLE=false
PROJECT_VERSION=v1.4.0-stage14
```

## Последние добавленные файлы

- `docs/WHERE_TO_GET_VALUES.md`
- `docs/BOTFATHER_SETUP.md`
- `docs/BOTHOST_DEPLOY_FULL.md`
- `scripts/deploy_check.py`
- endpoints `/health` и `/readiness` в Mini App.

## Готовность

98%. До 100% нужна реальная проверка на Bothost с настоящим токеном, адресом, каналом и Stars.


Последнее изменение: этап 14, улучшены аватары и добавлена инструкция `docs/AVATAR_SETUP.md`.
