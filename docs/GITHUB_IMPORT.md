# VoxLyra GitHub Import

Актуально для VoxLyra `v1.16.1`.

## Назначение

GitHub используется только как дополнительный источник контента. После проверки пакет передаётся существующему импортёру VoxLyra; отдельная библиотека, отдельная БД и постоянная копия GitHub-репозитория на Bothost не создаются.

Источник по умолчанию: `Treninem/bookvoxlyra`, ветка `main`. Репозиторий, ветка и корневой путь задаются через env.

## Безопасность

- доступ только непередаваемому `SYSTEM_OWNER_ID`;
- серверная проверка прав в сервисе и callbacks;
- GitHub token только в env и никогда не выводится в UI/историю;
- проверка manifest, обязательных файлов, безопасных путей и SHA-256;
- скачиваются только файлы выбранного пакета, без clone;
- ограничиваются размер пакета и минимальный свободный диск;
- временные файлы очищаются и при успехе, и при ошибке;
- replacement использует существующие backup/restore/finalize механизмы VoxLyra.

## Поддерживаемый поток

`GitHub → пакет → manifest → SHA-256 → временная директория → существующий import_library_zip → существующее хранилище/БД → cleanup`.

Поддержан одиночный и массовый импорт книг/комиксов. Аудиокниги пока намеренно исключены из массового GitHub-импорта.

## Идемпотентность и обновления

История хранит `package_id`, версию, commit SHA, статус, размер, количество файлов, VoxLyra ID, ошибку и снимок manifest (`manifest_json`). Пакет считается полностью уже импортированным только при совпадении версии и Git commit SHA. Та же версия с другим commit не замалчивается и показывается владельцу как обновление.

Для новой версии/commit строится файловый diff по сохранённому manifest:

- `+ file` — добавлен файл/глава;
- `- file` — файл удалён;
- `~ file` — SHA-256 файла изменён.

Diff выводится в скрытом owner-only GitHub Import перед ручным обновлением. Исторические записи, созданные до появления manifest snapshot, сравниваются консервативно: текущие файлы показываются как новые, без выдумывания старого состояния.

Существующий импортёр VoxLyra использует duplicate fingerprint / `source_file_hash` и replacement-backup/restore; GitHub не создаёт параллельный механизм замены.

## Rollback и cleanup

При фатальной ошибке существующего импортёра replacement backup восстанавливается, finalize не выполняется, ошибка записывается в закрытую историю. После любого исхода временный каталог и временный `.voxlyra.zip` удаляются. Успешный импорт, напротив, финализирует backup и сохраняет внутренний VoxLyra `book_id` в истории.

## Тестирование

Канонические тесты GitHub Import находятся в:

- `tests/test_v1160_github_import_security.py`;
- `tests/test_v1160_github_bulk_import.py`;
- `tests/test_v1160_import_rights.py`;
- `tests/test_v1160_cross_platform_publication.py`;
- `tests/test_v1161_github_import_hardening.py`;
- `tests/test_v1161_current_release_contract.py`.

Hardening v1.16.1 проверяет owner/non-owner access, manifest/SHA/path validation, bulk/retry, low disk, explicit update confirmation, manifest diff, rollback/finalize и гарантированный cleanup.

Исторические snapshot-тесты старых релизов не должны требовать возврата к старым строкам версии или старому формату readiness/API. Их полезные утверждения переносятся в поддерживаемые текущие контракты.

## Остаток production-проверки

- сетевой interrupted-download/missing-file сценарий на HTTP transport уровне;
- подтверждение на реалистичной БД сохранения постоянного `book_id` и покупок/прогресса/закладок/отзывов при replacement;
- обновление реального комикса с добавлением/заменой главы;
- LICENSE/SOURCES + модерация end-to-end;
- большая библиотека и GitHub rate-limit;
- Bothost E2E: `BookVoxLyra → импорт → библиотека/читалка → Telegram/VK публикация → Stars/Votes`.
