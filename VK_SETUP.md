# VoxLyra — подключение VK к тому же репозиторию

Один и тот же URL backend используется Telegram Mini App и VK Mini App.

Переменные окружения Bothost:

```env
VK_ENABLED=true
VK_APP_ID=
VK_APP_SECRET=
VK_SERVICE_TOKEN=
VK_GROUP_ID=
VK_GROUP_TOKEN=
VK_OWNER_IDS=
VK_API_VERSION=5.199
```

- `VK_APP_ID` и `VK_APP_SECRET` — приложение VK Mini Apps.
- `VK_SERVICE_TOKEN` — необязателен, нужен для серверного получения имени/аватара.
- `VK_GROUP_ID` и `VK_GROUP_TOKEN` — необязательны для самого Mini App, нужны для бота сообщества и уведомлений.
- В настройках VK Mini App укажите тот же HTTPS URL, который используется как `WEBAPP_URL` Telegram.

Telegram-переменные менять не нужно. При запуске из Telegram используется Telegram initData; при запуске из VK — подписанные launch-параметры VK.
