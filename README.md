# VoxLyra

VoxLyra — единая платформа для книг, комиксов/манги/манхвы/вебтунов и аудиокниг с Telegram-ботом, Telegram Mini App, VK Mini App, публикацией, модерацией и общей библиотекой.

## Текущая версия — v1.16.2

`v1.16.2` завершает крупный блок GitHub Import / Source Publish: системный владелец теперь может безопасно передавать source-ready ZIP не только документом Telegram, но и напрямую через защищённую веб-загрузку для больших пакетов. GitHub остаётся дополнительным источником, а фактический импорт по-прежнему использует существующие БД, хранилище, читалку, модерацию, покупки и пользовательские связи VoxLyra.

### GitHub Import

- отдельный непередаваемый `SYSTEM_OWNER_ID` и скрытый owner-only интерфейс;
- другие владельцы и администраторы не видят GitHub Import / Source Publish и не могут вызвать их crafted callback;
- `GITHUB_IMPORT_ENABLED=false` серверно блокирует импорт;
- manifest проверяется до payload download: safe-path, обязательные поля, exact SHA-256, `LICENSE.txt`, `SOURCES.txt` и наличие самого произведения;
- существующий library importer выполняет второй слой проверки metadata, лицензии, коммерческого использования и производных работ;
- публичный `bookvoxlyra` загружается по commit-pinned raw URL;
- bulk/retry используют один task-local remote inventory;
- одинаковый `package_id` сериализуется одним lock, после ожидания lock локальная import history перечитывается заново;
- update требует явного подтверждения и показывает файловый diff;
- retry повторяет только тот же failed version+commit;
- replacement сохраняет постоянный `book_id`, покупки, прогресс, закладки и отзывы;
- временные GitHub download/work файлы очищаются при успехе, ошибке и оборванной загрузке.

### Source ZIP → GitHub

Source Write по умолчанию выключен и требует отдельный `GITHUB_SOURCE_WRITE_TOKEN` с `Contents: Read and write` только для source-репозитория.

Два входных пути сходятся в один и тот же атомарный publisher:

- ZIP до 20 МБ можно отправить боту документом;
- большие ZIP до `GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB` загружаются через кнопку `🌐 Загрузить ZIP напрямую`;
- прямая ссылка короткоживущая, HMAC-подписанная, purpose-bound и привязана к `SYSTEM_OWNER_ID` + chat;
- write-token GitHub никогда не передаётся браузеру;
- browser upload идёт возобновляемыми частями по 1 MiB с повторными попытками;
- session привязана к nonce токена, чужая/новая ссылка не может продолжить старую session;
- проверяются размер пакета, размер каждой части, свободное место и точная итоговая длина;
- незавершённые source-upload sessions очищаются runtime maintenance;
- finish защищён lock; конкурентный второй finish не может снять lock первого;
- после сборки ZIP вызывается канонический `publish_source_package_zip()`;
- ZIP проходит structure/manifest/SHA-256/rights/content validation;
- Git blobs, tree и commit подготавливаются до изменения ветки;
- старые файлы canonical package удаляются из нового tree;
- package и `manifests/import_index.json: enabled=true` появляются одним commit;
- ref двигается fast-forward с `force=false`, поэтому half-package не становится видимым импорту.

### Telegram + VK

- Telegram publication открывает Telegram Mini App, VK publication — VK Mini App;
- Telegram Stars и VK Votes используют общую модель доступа, но разные native checkout;
- цена VK-поста использует тот же `votes_for_stars`, что и реальный checkout;
- успешный VK wall post идемпотентен;
- неудачный первый wall post можно безопасно повторить;
- старый каталог без VK audit-state не публикуется задним числом массово;
- вся VK publication/retry/pricing логика канонически находится в `cross_platform_publication.py`, а `vk_publication.py` остаётся compatibility re-export.

### Реальные подготовленные пакеты

В `Treninem/bookvoxlyra` подготовлены canonical staging records:

- **«Грань реальности»** — 1020 последовательных глав; прежнее название «Между двумя ответами» считается только историческим названием той же книги;
- **«Счастье во мне»** — 170 последовательных глав.

Их index остаётся `enabled=false`, пока точные binary EPUB/cover bytes фактически не будут опубликованы через source-write поток. Это намеренная защита от неполного пакета.

### Regression / CI

`v1.16.2` имеет отдельные current-release contracts. Они проверяют owner security, GitHub Import hardening, atomic Source Write, signed direct upload, chunk reassembly/resume, lock ownership, stale cleanup, сохранение `book_id`, Telegram/VK deep links, native payments и единственную каноническую VK publication logic.

Устаревший `test_v1161_current_release_contract.py` удалён после перехода на `test_v1162_current_release_contract.py`; функциональные v1.16.0/v1.16.1 regression-тесты сохранены.

### Что ещё требует реального deployment/E2E

1. на Bothost задать `WEBAPP_URL`, `GITHUB_SOURCE_WRITE_ENABLED=true` и отдельный `GITHUB_SOURCE_WRITE_TOKEN`;
2. redeploy v1.16.2 и проверить owner-only кнопку прямой source upload;
3. опубликовать подготовленные canonical ZIP в `Treninem/bookvoxlyra` и убедиться, что index включился только после полного commit;
4. пройти реальный `BookVoxLyra → GitHub Import → библиотека/читалка → Telegram/VK publication`;
5. проверить production Stars/Votes отдельно на соответствующих платформах.

До этих внешних проверок `RELEASE_MANIFEST.json` честно оставляет `live_bothost_redeploy`, `live_telegram_flow`, `live_vk_flow` и `production_payment_flow` равными `false`.

## Архитектурные правила

- SQLite БД и runtime user data остаются на runtime-хранилище, а не переносятся в GitHub.
- GitHub используется как source/code/release storage, а не как runtime database.
- GitHub Import не создаёт вторую библиотеку или отдельную бизнес-логику.
- Секретные tokens никогда не коммитятся и не выводятся в UI/логи/traceback.
- Telegram и VK используют общую библиотеку с платформенно-раздельной оплатой.

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

`WEBAPP_URL` должен быть публичным HTTPS base URL VoxLyra, чтобы бот мог сформировать защищённую кнопку прямой source upload.

## Основные каталоги

- `app/` — backend, Telegram/VK, сервисы, БД и бизнес-логика;
- `static/`, `templates/` — Mini App/web UI;
- `tests/` — maintained regression и current-release contracts;
- `scripts/` — эксплуатационные и QA-скрипты;
- `docs/` — техническая документация;
- `data/` — persistent runtime data;
- `storage/` — временные рабочие данные.

Последнее обновление README: 2026-08-13 — v1.16.2: signed/resumable direct Source ZIP upload, concurrency-safe finish, periodic stale cleanup и актуальный current-release contract.
