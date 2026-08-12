# VoxLyra GitHub Source Write

Актуально для VoxLyra `v1.16.1`.

## Зачем нужен этот мост

`Treninem/bookvoxlyra` хранит source-пакеты, но обычный GitHub Import VoxLyra имеет read-only назначение. Для бинарных EPUB/cover/страниц нужен отдельный путь публикации, который не выдаёт право обычным администраторам и не включает пакет до завершения загрузки.

Source Write решает это отдельным owner-only потоком:

`SYSTEM_OWNER_ID → source-ready ZIP → локальная проверка → Git blobs → Git tree → Git commit → fast-forward ref → enabled package`.

## Включение

По умолчанию функция выключена.

```env
GITHUB_SOURCE_WRITE_ENABLED=false
GITHUB_SOURCE_WRITE_TOKEN=
GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB=512
GITHUB_SOURCE_WRITE_MAX_FILE_MB=50
```

Для production:

1. создать отдельный fine-grained GitHub token;
2. ограничить его только репозиторием `Treninem/bookvoxlyra`;
3. дать Repository permissions → Contents: Read and write;
4. сохранить token только в защищённых env Bothost;
5. установить `GITHUB_SOURCE_WRITE_ENABLED=true`;
6. redeploy/restart VoxLyra.

`GITHUB_IMPORT_TOKEN` не переиспользуется. Source-write token не выводится в UI, историю импорта или документацию.

## Доступ

Доступ принадлежит только `SYSTEM_OWNER_ID` и не наследуется из `OWNER_IDS`, ролей модератора или permissions БД.

После включения в скрытом `🧩 Система` появляется:

`⬆️ Source ZIP → GitHub`.

Есть аварийная команда:

`/github_source_publish`

Для остальных пользователей команда молчит, а callback отклоняется.

## Требования к ZIP

В одном ZIP допускается ровно один canonical package:

```text
books/<package_id>/
  manifest.json
  metadata.json
  description.txt
  cover.jpg
  LICENSE.txt
  SOURCES.txt
  book.epub
```

или аналогичная структура под `comics/` / `audiobooks/`.

Проверяется:

- один package root;
- отсутствие `..`, absolute paths, symlinks и encrypted entries;
- limits по ZIP, unpacked size и одному файлу;
- package_id и content_type;
- обязательные `LICENSE.txt` и `SOURCES.txt`;
- наличие реального content payload, а не только metadata/cover/evidence;
- точное соответствие ZIP ↔ manifest files;
- SHA-256 каждого declared файла;
- UTF-8 и непустое содержимое rights/source evidence.

После обычного GitHub Import остаётся второй, более глубокий слой проверки существующего `library_manager`: metadata, license class, commercial/derivative permissions и остальные правила VoxLyra.

## Атомарная публикация

Publisher сначала читает текущий branch ref, base tree и `manifests/import_index.json`.

Далее:

1. создаёт Git blob для каждого файла нового source package;
2. создаёт blob обновлённого import index;
3. формирует новый tree поверх текущего base tree;
4. удаляет stale blob paths старой canonical revision, которых нет в новом ZIP;
5. создаёт один commit;
6. только после этого выполняет `PATCH refs/heads/<branch>` с `force=false`.

Таким образом blob-ы могут физически существовать в Git object store после неудачной попытки, но ни package tree, ни `enabled=true` не становятся видимыми на ветке до финального fast-forward.

Если branch изменился параллельно и GitHub не принимает fast-forward, публикация завершается ошибкой и должна быть повторена с новой актуальной base revision. Force push не используется.

## Telegram limit

Текущий Telegram handler загружает document через cloud Bot API и поэтому ограничивает этот UI-путь 20 МБ. Два подготовленных book source-ready ZIP меньше этого лимита.

Для будущих крупных comic/webtoon source packages нужен direct upload endpoint поверх того же `publish_source_package_zip()` сервиса. До его появления ZIP >20 МБ не публикуется частично и import index не переключается.

## Уже подготовленные реальные книги

В файловой библиотеке проекта сохранены source-ready packages:

- `mezhdu-dvumya-otvetami-final.source-ready.zip` — 1020 глав;
- `schastye-vo-mne-final.source-ready.zip` — 170 глав.

Их staging metadata/rights provenance уже находятся в `Treninem/bookvoxlyra`, но index остаётся `enabled=false` до фактической бинарной публикации через этот мост.

## Тесты

`tests/test_v1161_github_source_publish.py` проверяет:

- valid package inspection;
- folder/package_id mismatch;
- undeclared file rejection;
- import-index update без мутации исходного объекта;
- atomic tree/commit/ref flow;
- stale package file deletion;
- отдельный owner-only/disabled-by-default gate.

Source publisher включён в targeted GitHub Actions contract suite и в полный regression suite.
