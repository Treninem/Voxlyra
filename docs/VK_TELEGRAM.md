# VoxLyra — Telegram + VK

Актуально для `v1.16.1`.

## Одна система, один контейнер

Один процесс Bothost одновременно обслуживает FastAPI/Mini App, Telegram polling и VK Community Long Poll. Telegram и VK работают с одной SQLite-базой и одним постоянным `data/`.

Не запускайте второй контейнер с той же SQLite-базой и теми же polling-токенами.

## Общие данные и аккаунт

Каталог, книги, главы, аудио, комиксы и публичные данные общие. После привязки Telegram и VK обе внешние учётные записи разрешаются в один внутренний `users.id`, поэтому библиотека, прогресс, покупки, награды и авторский кабинет синхронизируются.

## Переменные VK

```env
VK_ENABLED=true
VK_APP_ID=
VK_APP_SECRET=
VK_SECURE_KEY=
VK_SERVICE_TOKEN=
VK_GROUP_ID=
VK_GROUP_TOKEN=
VK_OWNER_IDS=
VK_API_VERSION=5.199
VK_PAYMENT_SECRET=
VK_VOTES_PER_STAR=1.0
VK_PAYMENT_TEST_MODE=false
```

`VK_APP_SECRET`/`VK_SECURE_KEY` используются для серверной проверки launch-параметров. `VK_SERVICE_TOKEN` нужен только для отдельных серверных API. `VK_GROUP_ID` и `VK_GROUP_TOKEN` нужны для Community Long Poll, уведомлений и публикации книг на стене сообщества.

В настройках VK Mini App укажите тот же HTTPS backend, который используется VoxLyra. Приложение должно быть опубликовано/разрешено к запуску в настройках VK.

## Платежи

- Telegram: цифровой контент оплачивается только Telegram Stars.
- VK: цифровой контент оплачивается только голосами VK через `VKWebAppShowOrderBox`.
- Сервер повторно проверяет цену; клиент не является источником истины.
- `VK_VOTES_PER_STAR` используется только как коэффициент представления существующей канонической цены в VK.

Callback VK:

`<WEBAPP_URL>/api/vk/payments/callback`

## Публикация книг

Первичная публикация книги проходит через общий workflow независимо от источника загрузки: Telegram, VK, Library Manager или GitHub Import.

- Telegram-публикация использует ссылку `https://t.me/<BOT_USERNAME>?startapp=book_<id>` и открывает Telegram Mini App.
- VK-публикация вызывает `wall.post` от имени сообщества и содержит `https://vk.com/app<VK_APP_ID>#book_<id>`.
- В VK отображается цена только в голосах; Stars в текст VK-поста не попадают.
- При наличии локальной обложки VoxLyra пытается загрузить её как wall photo. Ошибка обложки не отменяет текстовый пост.
- Отправка на стену идемпотентна по `audit_logs`: повторный workflow не создаёт дубликат поста без `force`.
- Ошибка VK записывается в audit, но не откатывает уже одобренную книгу и не блокирует Telegram.

## Безопасность

Все секреты задаются через env. Токены VK и Telegram не должны попадать в код, README, клиентский JavaScript, API-ответы или traceback, доступные пользователю.
