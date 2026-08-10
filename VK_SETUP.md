# VoxLyra — подключение VK к тому же репозиторию

Один и тот же URL backend используется Telegram Mini App и VK Mini App.

Переменные окружения Bothost:

```env
VK_ENABLED=true
VK_APP_ID=54713417
VK_APP_SECRET=
VK_SERVICE_TOKEN=
VK_GROUP_ID=240755410
VK_GROUP_TOKEN=
VK_OWNER_IDS=224402322
VK_API_VERSION=5.199
VK_VOTES_PER_STAR=1.0
DATABASE_PATH=/app/data/voxlyra.sqlite3
```

- `VK_APP_ID` и `VK_APP_SECRET` — приложение VK Mini Apps.
- `VK_SERVICE_TOKEN` — необязателен, нужен для серверного получения имени/аватара.
- `VK_GROUP_ID` и `VK_GROUP_TOKEN` — необязательны для самого Mini App, нужны для бота сообщества и уведомлений.
- В настройках VK Mini App укажите тот же HTTPS URL, который используется как `WEBAPP_URL` Telegram.
- В разделе размещения VK обязательно включите приложение и задайте URL запуска `https://voxlyra.bothost.tech/`. Пока эти два пункта красные, VK показывает бесконечную загрузку/тайм-аут независимо от кода Bothost.
- `VK_VOTES_PER_STAR` переводит внутреннюю цену Stars в голоса только вверх (`ceil`). Поэтому дробная конвертация не уменьшает сумму, которая затем распределяется автору, платформе и бонусному фонду по старой логике.

Telegram-переменные менять не нужно. При запуске из Telegram используется Telegram initData; при запуске из VK — подписанные launch-параметры VK.
