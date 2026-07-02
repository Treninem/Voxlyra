# Hotfix v1.4.1

Исправлено:

- Mini App падал при открытии `/` из-за несовместимой формы `TemplateResponse` с текущей версией Starlette/FastAPI на хостинге.
- Все вызовы `TemplateResponse` переведены на новый формат: `TemplateResponse(request, template_name, context)`.
- Перед запуском long polling бот теперь вызывает `delete_webhook`, чтобы убрать возможный старый webhook.

Важно:

- Ошибка `TelegramConflictError: terminated by other getUpdates request` означает, что где-то запущен второй экземпляр этого же бота с тем же токеном. Нужно остановить старый проект/контейнер или перевыпустить токен.
- После обновления обязательно сделать Redeploy, а не только Restart.

Проверка после деплоя:

```
https://bot-1783015725-8670-treninem.bothost.tech/health
https://bot-1783015725-8670-treninem.bothost.tech/readiness
```
