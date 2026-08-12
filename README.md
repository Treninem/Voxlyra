# VoxLyra

VoxLyra — единая платформа для книг, комиксов/манги/манхвы/вебтунов и аудиокниг с Telegram-ботом, Telegram Mini App, VK Mini App, публикацией, модерацией и общей библиотекой.

## Текущая разработка — v1.16.0

Основная реализация GitHub-импорта уже в `main`. GitHub используется только как источник: проверенный пакет передаётся существующему импортёру VoxLyra и использует существующие БД, хранилище, читалку, публикацию и пользовательские связи.

Источник по умолчанию: `Treninem/bookvoxlyra`. Репозиторий, ветка и корневой путь настраиваются через env.

### Уже в main

- отдельное непередаваемое право `SYSTEM_OWNER_ID`;
- скрытый раздел владельца `Контент → Импорт → GitHub` и серверный запрет для остальных;
- проверка репозитория, обнаружение пакетов и пагинация;
- manifest, SHA-256 и защита от небезопасных путей;
- потоковая загрузка выбранного пакета без clone;
- лимиты диска/размера и гарантированная очистка временных файлов;
- книги и комиксы идут через существующий `import_library_zip`;
- история package/version/commit SHA/status/размер/VoxLyra ID/error;
- идемпотентность успешного package/version/commit;
- обнаружение обновлений без тихой автозамены;
- массовый импорт новых книг/комиксов и повтор неудачных;
- аудио намеренно исключено из массового GitHub-импорта;
- основной импортёр проверяет логические дубли и `import_file_hash/source_file_hash`, replacement обновляет `books` по существующему `id`;
- replacement-backup/restore используется для отката неудачной замены;
- GitHub Actions CI выполняет compileall, целевые v1.16.0 тесты и полный pytest;
- удалены устаревшие release/status/inventory и chat-only артефакты.

### CI — фактическое состояние

Первый реальный CI-прогон выявил ошибку именно в конфигурации workflow, а не падение приложения: workflow ссылался на отсутствующий `tests/test_v1160_github_import.py`. Фактически в `main` находятся `test_v1160_github_import_security.py`, `test_v1160_github_bulk_import.py`, `test_v1160_import_rights.py` и `test_v1160_cross_platform_publication.py`. Цели workflow исправлены commit `ffe05add40f57426965e80a35483a93c2f966d42`. Следующий CI должен выполнить реальные тесты, а не завершиться на неверном имени файла.

### Что ещё требует фактической проверки перед отметкой «полностью готово»

1. получить успешный реальный CI после исправления целей тестов и затем полный regression pytest;
2. добавить/подтвердить аварийные тесты missing file, download interruption, low disk и cleanup;
3. проверить на реальной БД, что replacement сохраняет покупки, прогресс, закладки, отзывы и рейтинги;
4. проверить обновление комиксов/глав и отображение сравнения версии;
5. проверить LICENSE/SOURCES и модерацию end-to-end;
6. проверить большую библиотеку и GitHub rate limit;
7. финальный E2E на Bothost: BookVoxLyra → импорт → библиотека → чтение → публикация Telegram/VK → Telegram Stars/VK Votes.

До выполнения этих проверок README не утверждает, что production-проверка завершена.

## Архитектурные правила

- Не переносить текущую SQLite БД с Bothost в рамках v1.16.0.
- Не переносить существующую библиотеку.
- Не создавать отдельное постоянное хранилище GitHub-контента.
- Не заменять существующие EPUB/FB2/TXT/PDF/CBZ/CBR/7Z/изображения и массовый импорт.
- Не хранить GitHub token в коде, UI, логах или traceback.
- Telegram и VK используют общую библиотеку, но платформенную оплату: Stars в Telegram и Votes в VK.
- Публикация использует платформенную ссылку: Telegram Mini App в Telegram и VK Mini App в VK.

## GitHub Import env

```env
GITHUB_IMPORT_ENABLED=false
GITHUB_IMPORT_REPOSITORY=Treninem/bookvoxlyra
GITHUB_IMPORT_BRANCH=main
GITHUB_IMPORT_ROOT=
GITHUB_IMPORT_TOKEN=
GITHUB_IMPORT_TEMP_ROOT=storage/github_import
GITHUB_IMPORT_MAX_PACKAGE_MB=2048
GITHUB_IMPORT_MIN_FREE_DISK_MB=256
GITHUB_IMPORT_PAGE_SIZE=50
```

## Основные каталоги

- `app/` — backend, Telegram/VK, сервисы, БД и бизнес-логика.
- `static/`, `templates/` — Mini App/web UI.
- `tests/` — автоматические тесты.
- `scripts/` — эксплуатационные и проверочные скрипты.
- `docs/` — актуальная техническая документация.
- `data/` — постоянные runtime-данные; реальные пользовательские данные не коммитятся.
- `storage/` — временные/runtime-ресурсы согласно конфигурации.

Последнее обновление README: 2026-08-12 — получен первый реальный CI-результат, обнаружена и исправлена неверная ссылка workflow на отсутствующий тест; ожидается повторный фактический прогон целевых тестов и полного regression suite.
