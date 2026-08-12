# VoxLyra

VoxLyra — единая платформа для книг, комиксов/манги/манхвы/вебтунов и аудиокниг с Telegram-ботом, Telegram Mini App, VK Mini App, публикацией, модерацией и общей библиотекой.

## Текущая версия — v1.16.1

`v1.16.1` — hardening-релиз GitHub Import и кроссплатформенного контура Telegram/VK. Основная задача релиза — не создавать вторую библиотеку или вторую бизнес-логику, а безопасно подключить дополнительный источник контента к существующей VoxLyra.

GitHub используется только как источник. Проверенный пакет передаётся существующему импортёру VoxLyra и использует существующие БД, хранилище, читалку, публикацию, модерацию, покупки и пользовательские связи. Источник по умолчанию: `Treninem/bookvoxlyra`.

### GitHub Import

- непередаваемый `SYSTEM_OWNER_ID` и скрытый owner-only раздел;
- для системного владельца обычная кнопка `🧩 Система` открывает отдельные системные инструменты с `📦 GitHub Import` и диагностикой;
- другие владельцы и администраторы продолжают видеть прежнюю системную диагностику и не получают даже пункта GitHub Import;
- аварийный вход `/github_import` отвечает только системному владельцу и молчит для остальных;
- сетевые/TLS/HTTP ошибки owner-callback отображаются в самом боте вместо зависшего Telegram spinner;
- серверный запрет доступа остальным пользователям и администраторам;
- строгая проверка manifest, SHA-256 и safe-path;
- `checksums` обязан точно соответствовать `files`;
- `package_id` ограничен безопасной длиной для Telegram `callback_data`;
- до 20 000 файлов на пакет и до 5000 обнаруженных пакетов как защитные пределы;
- скачиваются только файлы выбранного пакета, без clone репозитория;
- публичный `bookvoxlyra` скачивается по commit-pinned `raw.githubusercontent.com`, без одного Contents API запроса на каждую страницу комикса;
- при приватном источнике с токеном сохраняется совместимый Contents API путь;
- массовый импорт и retry используют один task-local снимок inventory вместо повторного сканирования репозитория для каждой книги;
- лимит `max_packages` соблюдается точно, а `0` не делает сетевых запросов;
- GitHub rate-limit превращается в понятную ошибку импорта;
- проверяется свободное место не только перед загрузкой, но и перед созданием второй временной копии в `.voxlyra.zip`;
- временные каталоги и ZIP очищаются при успехе, ошибке и оборванной загрузке;
- книги/комиксы импортируются через существующий `import_library_zip`;
- история хранит package/version/commit SHA/status/size/VoxLyra ID/error и снимок manifest;
- обновление требует явного подтверждения владельца и показывает diff файлов;
- `Повторить неудачные` автоматически повторяет только ту же неудачную version+commit; более новый source revision снова требует ручного diff/подтверждения;
- replacement обновляет существующую книгу с сохранением постоянного `book_id`;
- покупки, прогресс, закладки и отзывы не удаляются replacement-потоком;
- повреждённый пакет откатывается отдельно, независимые успешно импортированные пакеты не отменяются;
- аудиокниги пока намеренно исключены из массового GitHub-импорта.

Актуальная техническая документация: `docs/GITHUB_IMPORT.md`.

### Telegram + VK

- Telegram-публикация открывает Telegram Mini App;
- VK-публикация открывает VK Mini App;
- цена VK-поста использует тот же `votes_for_stars`, что и реальный VK checkout;
- Telegram Stars и VK Votes остаются платформенными способами оплаты поверх общего внутреннего доступа VoxLyra;
- первая публикация книги сходится в общий workflow независимо от источника загрузки;
- успешный VK wall post идемпотентен и не дублируется;
- если первая попытка VK wall реально завершилась ошибкой, следующая публикационная обработка повторит именно этот неудачный пост;
- исторические книги без VK audit-состояния автоматически задним числом не публикуются — это защищает сообщество от массового спама старым каталогом.

### Regression hardening v1.16.1

`tests/conftest.py` изолирует изменяемые settings между тестами и не позволяет старым release-snapshot тестам требовать откат актуального runtime.

Актуальные контракты проверяют:

- синхронизацию `app/build_info.py`, `settings.PROJECT_VERSION`, `.env.example` и `RELEASE_MANIFEST.json`;
- canonical assets;
- Telegram/VK launch routes;
- платформенную коммерцию;
- owner-only GitHub Import и порядок router-ов, необходимый для скрытого system-owner меню;
- handler resilience и отсутствие доступа у non-owner;
- callback-safe manifests и resource limits;
- один inventory на bulk/retry;
- raw public downloads и GitHub rate-limit handling;
- low disk, missing file, interrupted stream, cleanup, rollback/finalize;
- exact-revision retry после ошибки;
- постоянный `book_id` при replacement;
- VK checkout/publication price parity;
- безопасный retry неудавшейся VK wall публикации без back-post исторического каталога.

### CI

GitHub Actions run `31639127073` успешно прошёл целевой набор `v1.16.1` и полный maintained regression suite после добавления скрытого system-owner GitHub Import меню и его тестов. Ранее `31637903158` подтвердил exact-revision retry, `31637170402` — безопасный VK retry, `31636870533` — масштабированный GitHub discovery/bulk import.

Каждый следующий commit в `main` снова запускает тот же CI. `RELEASE_MANIFEST.json` дополнительно проверяется текущим release-contract.

### До полного production-ready

В коде и автоматике закрыты owner security, rollback/cleanup, resource limits, большие inventory, API-amplification, update confirmation/diff, сохранение пользовательских связей, Telegram/VK deep links, безопасный retry публикации и скрытый системный интерфейс импорта.

Остаются только проверки, которым нужны реальные внешние данные/аккаунты:

1. добавить настоящий импортируемый payload в `Treninem/bookvoxlyra` — текущие известные пакеты в `manifests/import_index.json` отключены, потому что `payload_present=false`;
2. проверить обновление реального комикса с добавлением/заменой главы и фактический diff перед подтверждением;
3. проверить `LICENSE.txt`/`SOURCES.txt` + модерацию end-to-end на настоящем импортируемом пакете;
4. проверить большую реальную библиотеку, сетевые таймауты и фактический GitHub rate-limit;
5. Bothost E2E: `BookVoxLyra → импорт → библиотека/читалка → Telegram/VK публикация → Stars/Votes`.

До этих проверок `RELEASE_MANIFEST.json` намеренно оставляет `live_bothost_redeploy`, `live_telegram_flow`, `live_vk_flow` и `production_payment_flow` равными `false`.

## Архитектурные правила

- Не переносить текущую SQLite БД и существующую библиотеку с Bothost в рамках этой задачи.
- Не создавать отдельное постоянное GitHub-хранилище runtime-данных.
- Не заменять существующие EPUB/FB2/TXT/PDF/CBZ/CBR/7Z/изображения и массовый импорт.
- Не хранить GitHub token в коде, UI, логах или traceback.
- Telegram и VK используют общую библиотеку, но платформенные платежные интерфейсы разделены.
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

Последнее обновление README: 2026-08-12 — GitHub Import вынесен в скрытые system-owner инструменты, добавлен аварийный `/github_import`, handler resilience, exact-revision retry, масштабирование public raw downloads и подтверждён зелёный полный CI.
