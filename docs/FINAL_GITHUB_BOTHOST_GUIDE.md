# Финальная инструкция GitHub → Bothost

## 1. BotFather

1. Откройте Telegram.
2. Найдите @BotFather.
3. Нажмите Start.
4. Введите /newbot.
5. Укажите имя бота: Вокслира.
6. Укажите username бота, например VoxlyraBot.
7. Скопируйте токен — это `BOT_TOKEN`.

## 2. Telegram ID владельца

1. Найдите @userinfobot или аналогичного бота.
2. Нажмите Start.
3. Скопируйте числовой ID — это `OWNER_IDS`.

## 3. Канал

1. Создайте Telegram-канал.
2. Добавьте бота в администраторы канала.
3. Разрешите публикацию сообщений.
4. В `CHANNEL_ID` укажите `@username_канала`.

## 4. GitHub

1. Создайте новый репозиторий.
2. Распакуйте архив `voxlyra_bot`.
3. Загрузите содержимое папки в репозиторий.
4. Не загружайте `.env`, используйте `.env.example`.

## 5. Bothost

1. Создайте проект.
2. Подключите GitHub-репозиторий.
3. Укажите стартовый файл `main.py`, если платформа попросит.
4. Укажите переменные окружения.
5. Запустите деплой.
6. После запуска скопируйте публичный HTTPS-адрес проекта — это `WEBAPP_URL`.

## 6. Переменные окружения

```env
BOT_TOKEN=токен_от_BotFather
BOT_USERNAME=username_бота_без_@
OWNER_IDS=ваш_telegram_id
DATABASE_PATH=data/voxlyra.sqlite3
RUN_WEBAPP=true
PORT=8080
WEBAPP_URL=https://адрес_проекта_на_bothost
CHANNEL_ID=@username_канала
```

`WEBAPP_URL` берётся в панели Bothost после успешного запуска веб-части проекта. Это не GitHub и не ссылка на Telegram-бота.
