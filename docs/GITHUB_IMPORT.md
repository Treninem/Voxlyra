# VoxLyra GitHub Import

Актуально для VoxLyra `v1.16.1`.

## Назначение

GitHub используется только как дополнительный источник контента. После проверки пакет передаётся существующему импортёру VoxLyra; отдельная библиотека, отдельная БД и постоянная копия GitHub-репозитория на Bothost не создаются.

Источник по умолчанию: `Treninem/bookvoxlyra`, ветка `main`. Репозиторий, ветка и корневой путь задаются через env.

## Безопасность

- доступ только непередаваемому `SYSTEM_OWNER_ID`;
- серверная проверка прав в сервисе и callbacks;
- GitHub token только в env и никогда не выводится в UI/историю;
- обязательные поля manifest, безопасные пути и SHA-256 проверяются до передачи импортёру;
- `checksums` должен точно соответствовать набору `files`, лишние и отсутствующие записи запрещены;
- `package_id` ASCII-only и ограничен 51 символом, чтобы самый длинный owner callback `ghimp:update:<package_id>` гарантированно помещался в Telegram `callback_data` 64 bytes;
- защитный предел одного manifest — 20 000 файлов;
- скачиваются только файлы выбранного пакета, без clone;
- размер пакета и свободный диск ограничиваются до и во время загрузки;
- перед созданием `.voxlyra.zip` отдельно резервируется место для второй временной копии плюс минимальный disk reserve;
- временные файлы очищаются и при успехе, и при ошибке;
- replacement использует существующие backup/restore/finalize механизмы VoxLyra.

## Поддерживаемый поток

`GitHub → inventory → пакет → manifest → SHA-256 → временная директория → временный .voxlyra.zip → существующий import_library_zip → существующее хранилище/БД → cleanup`.

Поддержан одиночный и массовый импорт книг/комиксов. Аудиокниги пока намеренно исключены из массового GitHub-импорта.

Массовый импорт использует **изоляцию ошибок по пакетам**, а не одну общую транзакцию на весь репозиторий: повреждённый пакет откатывается и записывается как ошибка, после чего независимые пакеты продолжают импортироваться. Уже успешно импортированные независимые произведения не отменяются.

## Discovery больших каталогов

При наличии `manifests/import_index.json` discovery использует индекс. Отключённая запись `enabled=false` пропускается без чтения её package manifest. Manifest с `import_enabled=false` или `payload_present=false` также не считается импортируемым контентом.

Защитный предел inventory — 5000 пакетов. `max_packages` массового owner import применяется отдельно и соблюдается точно: если задан `1`, импортируется максимум один новый пакет; `0` вообще не обращается к GitHub.

Bulk/retry используют task-local `ContextVar` inventory. Один запуск сканирует удалённый каталог один раз; переключение страниц и последующие `import_package()` внутри этого запуска не пересканируют весь репозиторий. После завершения контекст обязательно сбрасывается, поэтому параллельные async-запросы не получают чужой inventory.

История для страницы пакетов применяется через одну DB connection вместо открытия отдельного SQLite connection для каждой карточки.

### Public vs private downloads

Для публичного источника без `GITHUB_IMPORT_TOKEN` файл строится как commit-pinned raw URL:

`raw.githubusercontent.com/<owner>/<repo>/<commit>/<path>`

Это убирает старую схему «Contents API metadata request на каждый файл». Для комикса с сотнями/тысячами страниц такой подход критичен: GitHub API расходуется на discovery, а payload скачивается напрямую и всё равно проверяется по SHA-256 из manifest.

Если задан token, сохраняется Contents API metadata путь для совместимости с приватным репозиторием.

GitHub `429` и явный `403` с `X-RateLimit-Remaining: 0` преобразуются в понятный `GitHubImportError`, а не маскируются под повреждённый файл.

## Идемпотентность и обновления

История хранит `package_id`, версию, commit SHA, статус, размер, количество файлов, VoxLyra ID, ошибку и снимок manifest (`manifest_json`). Пакет считается полностью уже импортированным только при совпадении версии и Git commit SHA. Та же версия с другим commit показывается владельцу как обновление.

Для новой версии/commit строится файловый diff:

- `+ file` — добавлен файл/глава;
- `- file` — файл удалён;
- `~ file` — SHA-256 файла изменён.

Diff выводится в скрытом owner-only GitHub Import перед ручным обновлением. Первое обновление не скачивается до явного подтверждения владельца.

### Повтор неудачного импорта

Кнопка «Повторить неудачные» считается явным подтверждением только **той же самой неудачной ревизии**. Перед повтором сервис сравнивает текущие `version` и `commit_sha` с записью ошибки:

- если они совпадают — тот же пакет повторяется с `allow_update=True`, поэтому ранее подтверждённое обновление действительно может восстановиться после временной сетевой/дисковой ошибки;
- если source repo уже изменился — новый commit/version автоматически не применяется; запись попадает в результат как требующая ручной проверки текущего diff и нового подтверждения.

Это устраняет две противоположные ошибки: failed update больше не «зависает» в `update_available`, но кнопка retry не превращается в скрытое согласие на более новую ревизию, появившуюся после сбоя.

Исторические записи, созданные до manifest snapshot, сравниваются консервативно: текущие файлы показываются как новые, без выдумывания старого состояния.

Существующий импортёр VoxLyra использует duplicate fingerprint / `source_file_hash` и replacement-backup/restore; GitHub не создаёт параллельный механизм замены.

## Rollback и cleanup

При фатальной ошибке существующего импортёра replacement backup восстанавливается, finalize не выполняется, ошибка записывается в закрытую историю. После любого исхода временный каталог и временный `.voxlyra.zip` удаляются. Успешный импорт финализирует backup и сохраняет внутренний VoxLyra `book_id` в истории.

Replacement обновляет существующую строку `books` по постоянному `id`, а rollback восстанавливает её через общий `_restore_table_row`. Покупки, прогресс чтения, закладки и отзывы не удаляются replacement-потоком.

Оборванный HTTP stream, missing remote file, SHA mismatch, превышение размера и low disk также проходят через cleanup и не оставляют частично скачанный package directory.

## Связь с публикацией Telegram/VK

GitHub Import не публикует книгу отдельным обходным способом. После существующей модерации книга входит в общий publication workflow VoxLyra.

Первая публикация пытается отправить VK wall post. `vk_wall_post_sent` делает публикацию идемпотентной. Если реальная попытка была записана как `vk_wall_post_failed`, следующая публикационная обработка может повторить её. Книги без VK audit-состояния считаются историческими/pre-VK и автоматически задним числом не постятся — это исключает массовый back-post старого каталога.

VK-публичная цена и VK checkout используют один `votes_for_stars`; Telegram продолжает использовать Telegram Stars.

## Тестирование

Канонические тесты:

- `tests/test_v1160_github_import_security.py`;
- `tests/test_v1160_github_bulk_import.py`;
- `tests/test_v1160_import_rights.py`;
- `tests/test_v1160_cross_platform_publication.py`;
- `tests/test_v1161_github_import_hardening.py`;
- `tests/test_v1161_current_release_contract.py`.

Hardening `v1.16.1` проверяет:

- owner/non-owner access;
- strict manifest/checksum/path validation;
- Telegram callback-safe `package_id`;
- 20k file limit;
- exact bulk limit и zero-limit no-op;
- один inventory на bulk/pages и cleanup task-local context;
- commit-pinned raw public download без Contents API metadata request на файл;
- GitHub rate-limit error;
- low disk до сети и disk reserve перед ZIP;
- missing remote file и interrupted stream;
- explicit update confirmation и manifest diff;
- exact-revision retry и запрет silent retry более нового commit;
- rollback/finalize/cleanup;
- сохранение постоянного `book_id`;
- VK native pricing и безопасный retry неудавшейся wall-публикации.

GitHub Actions run `31637903158` успешно прошёл целевой набор `v1.16.1` и полный maintained regression suite после exact-revision retry. Run `31637170402` до него подтвердил безопасный VK retry, а `31636870533` — масштабированный GitHub inventory/bulk-import.

`RELEASE_MANIFEST.json` закреплён текущим release-contract и не должен расходиться с `app/build_info.py`/`settings.PROJECT_VERSION`.

## Состояние `Treninem/bookvoxlyra`

`manifests/import_index.json` уже существует. На 2026-08-12 известные записи в нём отключены (`enabled=false`) по причине `payload_present=false`. Поэтому код импорта готов и CI-зелёный, но реальный GitHub → VoxLyra E2E для этих пакетов невозможно считать пройденным, пока в source repo не появится настоящий payload с корректным manifest/checksums/правами.

## Остаток production-проверки

- настоящий payload в `bookvoxlyra`;
- обновление реального комикса с добавлением/заменой главы и проверкой фактического diff;
- `LICENSE.txt`/`SOURCES.txt` + модерация end-to-end;
- большая реальная библиотека, реальные сетевые таймауты/rate-limit;
- Bothost E2E: `BookVoxLyra → импорт → библиотека/читалка → Telegram/VK публикация → Stars/Votes`.
