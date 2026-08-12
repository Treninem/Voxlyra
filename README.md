# VoxLyra

VoxLyra — единая платформа для книг, комиксов/манги/манхвы/вебтунов и аудиокниг с Telegram-ботом, Telegram Mini App, VK Mini App, публикацией, модерацией и общей библиотекой.

## Текущая разработка — v1.16.0

GitHub-импорт находится в `main`. GitHub используется только как источник: проверенный пакет передаётся существующему импортёру VoxLyra и использует существующие БД, хранилище, читалку, публикацию и пользовательские связи. Источник по умолчанию: `Treninem/bookvoxlyra`; репозиторий, ветка и корневой путь задаются env.

### Уже реализовано

- непередаваемый `SYSTEM_OWNER_ID` и скрытый owner-only GitHub Import;
- серверный запрет доступа остальным;
- manifest/SHA-256/path validation;
- потоковая загрузка пакета без clone, лимиты диска/размера и cleanup;
- импорт книг/комиксов через существующий `import_library_zip`;
- история package/version/commit SHA/status/size/VoxLyra ID/error;
- идемпотентность, обнаружение обновлений, массовый импорт новых книг/комиксов и retry;
- аудио пока намеренно исключено из массового GitHub-импорта;
- существующий duplicate fingerprint и replacement-backup/restore используются вместо параллельной системы;
- GitHub Actions CI: compileall → целевые v1.16.0 tests → полный pytest;
- `.env.example` содержит `SYSTEM_OWNER_ID`, GitHub Import и используемые Vosk/TTS параметры.

### Большая чистка репозитория

В `main` выполнен отдельный массовый cleanup commit `591badad80ea495032c48c6c7c9fe09a9673b4fc`.

Удалено более 100 устаревших, ошибочно названных и дублирующих файлов: старые STATUS/RELEASE_CHECK/FINAL_TEST_REPORT, chat-memory/transfer документы, старые VK deploy checklists, дубли тестов из корня, повреждённые файлы с mojibake/китайскими именами, ложные `.png/.webp/.css/.html/.py`, внутри которых фактически лежал Markdown/CSS/другой несоответствующий контент, старый `UPDATE_MANIFEST` и устаревшие инструкции обновления. Канонические runtime-файлы, `tests/`, актуальная документация, юридические документы и реальные статические ресурсы сохранены.

Примеры обнаруженного мусора: корневой `style.css` фактически содержал Markdown-статус v1.11.6; `author.html` — текст отчёта v1.11.6; `bot_avatar.png` — Markdown-статус v1.8.1; `env.example` — Python-код; `deploy_check.py` — Markdown-релиз; корневые `test_v1100...`/`test_v1111...` содержали CSS вместо Python. Их рабочие аналоги находятся в правильных каталогах.

### CI — фактическое состояние

Целевой набор v1.16.0 на последнем проверенном прогоне: **17/17 passed**. Полный regression до текущей большой чистки: **222 passed / 37 failed**. Следующий CI после cleanup используется как новая фактическая база; устаревшие snapshot/contract проверки не должны заставлять откатывать намеренный контракт v1.16.0.

### До production-ready

1. получить и разобрать новый полный CI после массовой чистки;
2. закрыть реальные 503/TestClient проблемы без ослабления production readiness guard;
3. аварийные GitHub tests: missing file, interrupted download, low disk, cleanup/rollback;
4. подтвердить сохранение постоянного book_id и покупок/прогресса/закладок/отзывов при replacement;
5. проверить обновление комиксов/глав и diff версии;
6. LICENSE/SOURCES + модерация end-to-end;
7. большая библиотека/rate-limit;
8. Bothost E2E: BookVoxLyra → импорт → библиотека/читалка → Telegram/VK публикация → Stars/Votes.

## Архитектурные правила

- Не переносить текущую SQLite БД и существующую библиотеку с Bothost в рамках этой задачи.
- Не создавать отдельное постоянное GitHub-хранилище.
- Не заменять существующие EPUB/FB2/TXT/PDF/CBZ/CBR/7Z/изображения и массовый импорт.
- Не хранить GitHub token в коде, UI, логах или traceback.
- Telegram и VK используют общую библиотеку, но Telegram Stars и VK Votes разделены по платформе.
- Публикация использует Telegram Mini App ссылку в Telegram и VK Mini App ссылку в VK.

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
- `tests/` — канонические автоматические тесты.
- `scripts/` — эксплуатационные и QA-скрипты.
- `docs/` — актуальная техническая документация.
- `data/` — постоянные runtime-данные; реальные пользовательские данные не коммитятся.
- `storage/` — runtime/legal ресурсы согласно конфигурации.

Последнее обновление README: 2026-08-12 — массово очищен `main` от более 100 старых/дублирующих/повреждённых файлов; следующий этап — новый CI и дальнейший production hardening.
