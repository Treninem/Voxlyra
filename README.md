# VoxLyra

VoxLyra — единая платформа для книг, комиксов/манги/манхвы/вебтунов и аудиокниг с Telegram-ботом, Telegram Mini App, VK Mini App, публикацией, модерацией и общей библиотекой.

## Текущая разработка — v1.16.0

Основная реализация GitHub-импорта уже в `main`. GitHub используется только как источник: проверенный пакет передаётся существующему импортёру VoxLyra и использует существующие БД, хранилище, читалку, публикацию и пользовательские связи. Источник по умолчанию: `Treninem/bookvoxlyra`; репозиторий, ветка и корневой путь задаются env.

### Уже в main

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
- `.env.example` дополнен `SYSTEM_OWNER_ID`, полным блоком GitHub Import и фактически используемыми Vosk/TTS параметрами;
- удалены устаревшие release/status/inventory и chat-only артефакты.

### CI — фактическое состояние

Целевой набор v1.16.0: **17/17 passed**. Полный regression на предыдущем прогоне: **222 passed / 37 failed**.

Проведена первичная классификация полного лога. Значительная часть падений — устаревшие snapshot/contract проверки, жёстко требующие `v1.11.12`, старый минимальный `/readiness` ответ или прежнюю форму расширенных API-результатов. Это не повод откатывать v1.16.0. Реальные группы для исправления: тестовая изоляция runtime readiness/БД (несколько 503), недостающие env-параметры Vosk, уведомления/модерация, публикация и отдельные UI-контракты. Vosk/env группа уже исправлена в `main` commit `ee5a858bbf4356d5728523b8e309ffaa1e97fb7c`.

### До production-ready

1. довести полный regression до зелёного состояния, обновляя устаревшие тесты только там, где новый контракт намеренный;
2. закрыть 503 в TestClient через корректную изоляцию runtime readiness, не ослабляя production readiness guard;
3. аварийные GitHub tests: missing file, interrupted download, low disk, cleanup/rollback;
4. подтвердить сохранение постоянного book_id и пользовательских покупок/прогресса/закладок/отзывов при replacement;
5. проверить обновление комиксов/глав и diff версии;
6. LICENSE/SOURCES + модерация end-to-end;
7. большая библиотека/rate-limit;
8. Bothost E2E: BookVoxLyra → импорт → библиотека/читалка → Telegram/VK публикация → Stars/Votes.

До выполнения этих пунктов проект не помечается как полностью production-проверенный.

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
- `tests/` — автоматические тесты.
- `scripts/` — эксплуатационные и проверочные скрипты.
- `docs/` — актуальная техническая документация.
- `data/` — постоянные runtime-данные; реальные пользовательские данные не коммитятся.
- `storage/` — временные/runtime-ресурсы согласно конфигурации.

Последнее обновление README: 2026-08-12 — разобран полный CI-лог, выделены реальные группы дефектов/устаревших контрактов и закрыта недостающая конфигурация GitHub Import + Vosk/TTS в `.env.example`.
