# VoxLyra

VoxLyra — единая платформа для книг, комиксов/манги/манхвы/вебтунов и аудиокниг с Telegram-ботом, Telegram Mini App, VK Mini App, публикацией, модерацией и общей библиотекой.

## Текущая версия — v1.16.1

`v1.16.1` — крупный hardening-релиз после внедрения GitHub Import: чистка репозитория, стабилизация regression-тестов, актуализация контрактов Telegram/VK и подготовка к финальному Bothost E2E.

GitHub используется только как источник. Проверенный пакет передаётся существующему импортёру VoxLyra и использует существующие БД, хранилище, читалку, публикацию и пользовательские связи. Источник по умолчанию: `Treninem/bookvoxlyra`.

### GitHub Import

- непередаваемый `SYSTEM_OWNER_ID` и скрытый owner-only раздел;
- серверный запрет доступа остальным;
- manifest / SHA-256 / safe-path validation;
- потоковая загрузка выбранного пакета без clone;
- лимиты размера и свободного диска + гарантированный cleanup;
- книги/комиксы импортируются через существующий `import_library_zip`;
- история package/version/commit SHA/status/size/VoxLyra ID/error;
- идемпотентность, обнаружение обновлений, массовый импорт новых книг/комиксов и retry;
- аудио пока намеренно исключено из массового GitHub-импорта;
- существующий duplicate fingerprint и replacement-backup/restore используются вместо параллельной системы.

Актуальная документация объединена в `docs/GITHUB_IMPORT.md`; старый versioned progress-файл удалён.

### Чистка репозитория

Из `main` уже удалено более 100 устаревших, повреждённых и дублирующих файлов: старые STATUS/RELEASE_CHECK/FINAL_TEST_REPORT, chat-memory/transfer документы, дубли тестов из корня, повреждённые файлы с неверными расширениями и mojibake-именами, старые update/deploy артефакты. Канонический runtime-код, юридические документы и реальные ресурсы сохранены.

В `v1.16.1` дополнительно удалён устаревший `tests/test_release_assets.py`, который требовал дублировать аватары в корне. Проверка реальных canonical assets перенесена в поддерживаемый `tests/test_v1161_current_release_contract.py`.

### Regression hardening v1.16.1

Добавлен `tests/conftest.py`:

- изолирует изменяемые `settings` между тестами, чтобы один тест не загрязнял БД/env следующего;
- повторяет только специальный startup-503 VoxLyra при TestClient, не ослабляя production readiness guard;
- исторические release-snapshot проверки старых строк `v1.9–v1.11`, старого readiness payload и старых UI-литералов отмечаются как legacy xfail вместо требования откатить актуальный код.

Добавлен актуальный release-contract `tests/test_v1161_current_release_contract.py`: версия, canonical аватары, Telegram/VK launch routes, платформенная коммерция и owner-only GitHub Import.

### Версионирование

- мелкий фикс: `1.16.0 → 1.16.0.1 → 1.16.0.2`;
- заметное обновление: `1.16.0 → 1.16.1 → 1.16.2`;
- глобальное изменение платформы: следующий major-minor, например `1.17.0`.

`app/build_info.py` и `settings.PROJECT_VERSION` должны всегда совпадать.

### CI

До v1.16.1 целевой набор GitHub Import стабильно проходил **17/17**, полный старый regression давал **222 passed / 37 failed**. Значительная часть 37 падений оказалась историческими snapshot-контрактами либо загрязнением глобальных settings/TestClient startup race. Новый CI после v1.16.1 используется как новая фактическая база.

### До полного production-ready

1. получить новый полный CI после regression hardening и исправить оставшиеся реальные падения;
2. аварийные GitHub tests: missing file, interrupted download, low disk, cleanup/rollback;
3. подтвердить сохранение постоянного `book_id` и покупок/прогресса/закладок/отзывов при replacement;
4. проверить обновление комиксов/глав и diff версии;
5. LICENSE/SOURCES + модерация end-to-end;
6. большая библиотека / GitHub rate-limit;
7. Bothost E2E: `BookVoxLyra → импорт → библиотека/читалка → Telegram/VK публикация → Stars/Votes`.

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

Последнее обновление README: 2026-08-12 — версия поднята до `v1.16.1`, добавлена изоляция regression-тестов, maintained current release contract, удалён ещё один устаревший дублирующий test-файл и объединена документация GitHub Import.
