# VoxLyra

VoxLyra — единая платформа для книг, комиксов/манги/манхвы/вебтунов и аудиокниг с Telegram-ботом, Telegram Mini App, VK Mini App, публикацией, модерацией и общей библиотекой.

## Текущая версия — v1.16.1

`v1.16.1` — hardening-релиз GitHub Import и кроссплатформенного контура Telegram/VK. GitHub остаётся только дополнительным источником: проверенный пакет передаётся существующему импортёру VoxLyra и использует существующие БД, хранилище, читалку, публикацию, модерацию, покупки и пользовательские связи. Источник по умолчанию: `Treninem/bookvoxlyra`.

### GitHub Import

- непередаваемый `SYSTEM_OWNER_ID` и скрытый owner-only раздел;
- другие владельцы и администраторы не видят GitHub Import и source-write инструменты;
- аварийный `/github_import` отвечает только системному владельцу;
- `GITHUB_IMPORT_ENABLED=false` скрывает и серверно блокирует GitHub Import;
- manifest проверяется до payload download: safe-path, обязательные поля, точные SHA-256, `LICENSE.txt`, `SOURCES.txt` и наличие файла/страниц самого произведения;
- после скачивания существующий библиотечный импортёр дополнительно проверяет metadata/лицензию, коммерческое использование, производные работы и evidence-файлы;
- публичный `bookvoxlyra` скачивается по commit-pinned raw URL без Contents API запроса на каждую страницу;
- bulk/retry используют один task-local remote inventory;
- одинаковый `package_id` сериализуется package-lock на полный import/rollback/cleanup;
- после ожидания lock повторно применяется свежая локальная import history, поэтому overlapping bulk-run не импортирует тот же пакет повторно;
- разные package ID остаются параллельно обрабатываемыми;
- explicit update confirmation + файловый diff;
- retry повторяет только ту же failed version+commit; новая source revision требует нового подтверждения;
- replacement сохраняет постоянный `book_id`, покупки, прогресс, закладки и отзывы;
- временные каталоги/ZIP очищаются при успехе, ошибке и оборванной загрузке;
- аудиокниги пока намеренно исключены из массового GitHub-импорта.

### Source ZIP → GitHub

В `v1.16.1` добавлен закрытый мост, который снимает ограничение ручной загрузки бинарных EPUB/cover в `bookvoxlyra`.

- доступ только `SYSTEM_OWNER_ID`;
- по умолчанию полностью выключен: `GITHUB_SOURCE_WRITE_ENABLED=false`;
- используется отдельный `GITHUB_SOURCE_WRITE_TOKEN`, read-token импорта не переиспользуется;
- target — тот же `GITHUB_IMPORT_REPOSITORY`/branch/root;
- ZIP проходит проверку структуры, manifest, SHA-256, `LICENSE.txt`, `SOURCES.txt` и фактического content payload;
- создаются Git blobs, затем новый tree и commit;
- старые файлы заменяемого canonical package удаляются из нового tree;
- `manifests/import_index.json` переключается на `enabled=true` в том же commit;
- branch ref двигается только fast-forward и без `force`, поэтому частично загруженный пакет не становится видимым импорту;
- в Telegram системному владельцу доступна кнопка `⬆️ Source ZIP → GitHub` и аварийная команда `/github_source_publish`;
- текущий Telegram Bot API путь рассчитан на ZIP до 20 МБ; большие source-пакеты не включаются частично и требуют отдельного direct-upload расширения.

Для fine-grained token достаточно ограничить доступ репозиторием `Treninem/bookvoxlyra` и правом Repository contents: Read and write. Секрет не выводится в UI, историю или документацию.

### Telegram + VK

- Telegram-публикация открывает Telegram Mini App, VK-публикация — VK Mini App;
- Telegram Stars и VK Votes используют общую внутреннюю модель доступа, но платформенные checkout разделены;
- VK-публичная цена использует тот же `votes_for_stars`, что и реальный checkout;
- successful VK wall post идемпотентен;
- failed first wall post безопасно повторяется;
- исторические книги без VK audit-state автоматически задним числом не постятся;
- canonical VK publication/retry/pricing logic находится в `cross_platform_publication.py`;
- `vk_publication.py` — только compatibility re-export, второй fork бизнес-логики отсутствует.

### Regression hardening v1.16.1

Актуальные тесты проверяют owner security, router order, env kill switches, source ZIP validation/atomic publish, exact SHA-256, rights evidence, real content payload, large inventory, raw downloads, rate-limit/low-disk/interrupted-stream cleanup, same-package concurrency/fresh-history, replacement с постоянным `book_id`, Telegram/VK deep links, native payments и единый VK publication service.

`tests/conftest.py` изолирует изменяемые settings между тестами и не заставляет runtime откатываться к устаревшим release-snapshot контрактам.

### CI

После внедрения source-write bridge GitHub Actions run `31646010085` успешно прошёл targeted `v1.16.1` contracts и полный maintained regression suite. Следующие commits продолжают запускать тот же CI; `RELEASE_MANIFEST.json` дополнительно закреплён текущим release-contract.

В `Treninem/bookvoxlyra` отдельный source-side validator требует для каждого enabled package реальный payload, точные SHA-256, непустые UTF-8 `LICENSE.txt`/`SOURCES.txt` и отсутствие undeclared files. Наличие evidence-файлов само по себе не создаёт права — сведения в них должны быть настоящими.

### Реальные подготовленные пакеты

Из файлов проекта восстановлены и проверены финальные архивы:

- `Между двумя ответами` — 1020 последовательных глав;
- `Счастье во мне` — 170 последовательных глав.

Для обоих уже подготовлены source-ready EPUB packages, metadata, description, original cover, `LICENSE.txt`, `SOURCES.txt` и canonical manifests. В `bookvoxlyra` staging metadata/provenance уже сохранены, а import-index намеренно остаётся disabled до физической публикации binary payload. Новый Source ZIP → GitHub bridge предназначен именно для безопасного завершения этого шага после включения write-token на deployment.

### До полного production-ready

Остаются только account/external-data проверки:

1. включить source-write bridge на deployment отдельным fine-grained token и опубликовать два уже подготовленных source-ready ZIP;
2. выполнить реальный GitHub → VoxLyra import и проверить библиотеку/читалку;
3. проверить реальное comic update с добавлением/заменой главы и фактическим diff;
4. пройти `LICENSE.txt`/`SOURCES.txt` + moderation E2E на настоящем enabled package;
5. Bothost E2E: `BookVoxLyra → импорт → библиотека/читалка → Telegram/VK публикация → Stars/Votes`.

До этих проверок `RELEASE_MANIFEST.json` намеренно оставляет `live_bothost_redeploy`, `live_telegram_flow`, `live_vk_flow` и `production_payment_flow` равными `false`.

## Архитектурные правила

- Не переносить текущую SQLite БД и существующую библиотеку с Bothost в GitHub.
- GitHub хранит source content/code/releases, а не runtime БД.
- Не создавать вторую библиотеку или отдельную business logic для GitHub.
- Не хранить GitHub tokens в коде, UI, логах или traceback.
- Telegram и VK используют общую библиотеку, но платформенные платежные интерфейсы разделены.

## GitHub env

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

GITHUB_SOURCE_WRITE_ENABLED=false
GITHUB_SOURCE_WRITE_TOKEN=
GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB=512
GITHUB_SOURCE_WRITE_MAX_FILE_MB=50
```

## Основные каталоги

- `app/` — backend, Telegram/VK, сервисы, БД и бизнес-логика.
- `static/`, `templates/` — Mini App/web UI.
- `tests/` — канонические автоматические тесты.
- `scripts/` — эксплуатационные и QA-скрипты.
- `docs/` — техническая документация.
- `data/` — постоянные runtime-данные; реальные пользовательские данные не коммитятся.
- `storage/` — временные/runtime/legal ресурсы согласно конфигурации.

Последнее обновление README: 2026-08-13 — добавлен атомарный owner-only Source ZIP → GitHub bridge с отдельным write-token и зелёным полным CI.
