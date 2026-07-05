# Voxlyra v1.6.1 — Bothost Bad Gateway fix

Исправление для случаев, когда приложение внутри контейнера запущено на `0.0.0.0:8080`, но внешний домен Bothost показывает `Bad Gateway`.

Что изменено:
- в `Dockerfile` добавлено `EXPOSE 8080`;
- служебная версия обновлена до `v1.6.1-bothost-proxy-fix`;
- код приложения не менял бизнес-логику.

Как ставить:
1. Залить полный архив в GitHub поверх старой версии.
2. На Bothost оставить:
   - Использовать собственный Dockerfile: включено;
   - Использовать домен: включено;
   - Порт веб-приложения: 8080;
   - Кастомный домен: `voxlyra.bothost.tech`.
3. В переменных окружения:
   - `PORT=8080`;
   - `RUN_WEBAPP=true`;
   - `WEBAPP_URL=https://voxlyra.bothost.tech`;
   - `PROJECT_VERSION=v1.6.1-bothost-proxy-fix`.
4. Сделать именно Redeploy / Пересобрать.
5. Проверить `https://voxlyra.bothost.tech/health`.
