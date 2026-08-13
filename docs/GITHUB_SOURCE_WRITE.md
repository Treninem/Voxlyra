# VoxLyra GitHub Source Write

Актуально для VoxLyra `v1.16.2`.

## Назначение

`Treninem/bookvoxlyra` — source-репозиторий контента. Обычный GitHub Import работает на чтение, поэтому бинарные EPUB/cover/страницы публикуются отдельным непередаваемым `SYSTEM_OWNER_ID` потоком.

Каноническая цепочка:

`SYSTEM_OWNER_ID → source-ready ZIP → local validation → Git blobs → Git tree → Git commit → fast-forward ref → enabled package`.

Начиная с `v1.16.2` ZIP может прийти двумя способами: Telegram document до 20 МБ либо защищённый direct web upload для больших пакетов. Оба пути вызывают один `publish_source_package_zip()` и не создают вторую бизнес-логику.

## Включение

```env
WEBAPP_URL=https://<public-voxlyra-host>
GITHUB_SOURCE_WRITE_ENABLED=false
GITHUB_SOURCE_WRITE_TOKEN=
GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB=512
GITHUB_SOURCE_WRITE_MAX_FILE_MB=50
```

Для production:

1. создать отдельный fine-grained GitHub token;
2. ограничить его репозиторием `Treninem/bookvoxlyra`;
3. дать Repository permissions → Contents: Read and write;
4. сохранить token только в защищённых env Bothost;
5. задать публичный HTTPS `WEBAPP_URL`;
6. установить `GITHUB_SOURCE_WRITE_ENABLED=true`;
7. redeploy/restart VoxLyra.

`GITHUB_IMPORT_TOKEN` не переиспользуется. Write-token не передаётся browser upload странице и не выводится в UI/логи/историю.

## Доступ

Source Write доступен только `SYSTEM_OWNER_ID`. Он не наследуется из `OWNER_IDS`, admin permissions или VK/Telegram модераторских ролей.

В скрытых системных инструментах доступны:

- `⬆️ Source ZIP → GitHub`;
- `/github_source_publish` как аварийный вход;
- `🌐 Загрузить ZIP напрямую` при настроенном `WEBAPP_URL`.

Для остальных пользователей команда молчит, callback отклоняется, а web-link без корректной owner-bound HMAC подписи не работает.

## Direct upload v1.16.2

Direct uploader нужен прежде всего для comic/webtoon и других ZIP, превышающих cloud Bot API лимит 20 МБ.

- URL содержит короткоживущий HMAC-SHA256 token;
- token имеет `purpose=github_source_publish`, `telegram_id`, `chat_id`, expiry и случайный nonce;
- token создаётся только для `SYSTEM_OWNER_ID`;
- API дополнительно принимает token в `X-Vox-Source-Token`;
- session привязана к nonce, поэтому новая ссылка не может захватить старую upload session;
- браузер режет файл на 1 MiB chunks;
- уже принятые части можно пропустить после обновления страницы/краткого сетевого сбоя;
- каждая часть проверяется по ожидаемой длине и номеру;
- итоговый ZIP собирается только при наличии всех частей и проверяется по полной длине;
- свободное место проверяется до session/chunk/assembly;
- stale sessions удаляются через runtime maintenance;
- finish имеет файловый lock;
- конкурентный второй finish получает 409 и не может снять lock первого;
- после успешной публикации временная session удаляется.

Direct uploader не обходит `GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB`: он снимает только транспортное ограничение Telegram 20 МБ.

## Требования к source-ready ZIP

В одном ZIP допускается один canonical package, например:

```text
books/<package_id>/
  manifest.json
  metadata.json
  description.txt
  cover.png
  LICENSE.txt
  SOURCES.txt
  book.epub
```

Также поддерживается соответствующая структура `comics/` / `audiobooks/`.

Проверяются:

- один package root;
- отсутствие `..`, absolute paths, symlinks и encrypted entries;
- limits ZIP/unpacked/individual file;
- package_id и content_type;
- обязательные `LICENSE.txt` и `SOURCES.txt`;
- наличие реального content payload;
- точное соответствие ZIP ↔ manifest files;
- SHA-256 каждого declared файла;
- UTF-8 и непустые rights/source evidence.

После GitHub Import существующий `library_manager` выполняет второй слой проверки metadata/license/commercial/derivative permissions.

## Атомарная публикация

Publisher читает текущий branch ref, base tree и `manifests/import_index.json`, после чего:

1. создаёт blob каждого нового файла;
2. создаёт blob нового import index;
3. строит tree поверх текущего base tree;
4. удаляет stale paths предыдущей canonical revision;
5. создаёт один commit;
6. выполняет `PATCH refs/heads/<branch>` с `force=false`.

До успешного последнего шага `enabled=true` не виден в ветке. Если ветка изменилась конкурентно, force push не применяется: операция завершается ошибкой и повторяется на свежей revision.

## Подготовленные canonical книги

- `gran-realnosti-final` — **«Грань реальности»**, 1020 глав. «Между двумя ответами» — прежнее название той же книги, не отдельный package.
- `schastye-vo-mne-final` — **«Счастье во мне»**, 170 глав.

Их staging records находятся в `Treninem/bookvoxlyra`; `enabled=false` сохраняется до фактической публикации точных binary payload bytes.

## Тесты

`tests/test_v1161_github_source_publish.py` продолжает проверять core atomic publisher.

`tests/test_v1162_github_source_upload.py` проверяет:

- owner-only signed/purpose-bound token;
- tamper rejection;
- nonce isolation;
- exact chunk reassembly;
- invalid chunk rejection;
- web start/chunk/finish flow;
- обход только Telegram transport limit, но не package size limit;
- finish-lock ownership;
- runtime stale-session cleanup;
- runtime router mounting.

`tests/test_v1162_current_release_contract.py` закрепляет весь текущий release contract.
