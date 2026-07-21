from __future__ import annotations

import asyncio
import errno
import hashlib
import gzip
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from PIL import Image, UnidentifiedImageError

from app.config import settings
from app.db import connect, utc_now
from app.services.book_parser import BookParseError, parse_book_file

ALLOWED_LICENSES = {"public_domain", "creative_commons", "author_permission", "platform_original"}
BOOK_EXTENSIONS = {".epub", ".fb2", ".txt", ".docx", ".pdf"}
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_STORAGE_ROOT = Path("storage/library")
IMPORT_WORK_ROOT = DEFAULT_STORAGE_ROOT / "import_work"
MIN_IMPORT_FREE_BYTES = 32 * 1024 * 1024
STALE_IMPORT_WORK_SECONDS = 2 * 60 * 60

ProgressCallback = Callable[[dict[str, int]], Awaitable[None]]
MAX_FILES_PER_BOOK_FOLDER = 12
MAX_COMPRESSION_RATIO = 250
RIGHTS_HOLDER_TYPES = {"public_domain", "person", "publisher", "platform", "other"}
REVENUE_MODES = {"none", "platform", "author_account"}


async def _run_blocking(func, /, *args, **kwargs):
    """Run blocking work without freezing Telegram updates.

    If shutdown arrives while a ZIP/file operation is active, wait for that
    operation to finish before unwinding temporary directories. This avoids a
    race between a worker thread and cleanup of the same files.
    """
    task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


@dataclass
class ImportErrorItem:
    folder: str
    title: str = "Без названия"
    reasons: list[str] = field(default_factory=list)


@dataclass
class ImportResult:
    batch_id: int
    added: int = 0
    replaced: int = 0
    renumbered: int = 0
    duplicates: int = 0
    errors: list[ImportErrorItem] = field(default_factory=list)
    book_ids: list[int] = field(default_factory=list)
    duplicate_ids: list[int] = field(default_factory=list)
    id_changes: list[dict[str, int | str]] = field(default_factory=list)


def _safe_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts and not normalized.startswith("/")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



class LibraryImportStorageError(RuntimeError):
    pass


def _format_mb(value: int) -> str:
    return f"{max(0, int(value)) / 1024 / 1024:.1f} МБ"


def _is_no_space_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        isinstance(exc, OSError)
        and exc.errno == errno.ENOSPC
    ) or any(
        marker in message
        for marker in ("no space left", "database or disk is full", "disk full")
    )


def _ensure_library_storage_root() -> None:
    DEFAULT_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    IMPORT_WORK_ROOT.mkdir(parents=True, exist_ok=True)


def cleanup_stale_import_work(*, max_age_seconds: int = STALE_IMPORT_WORK_SECONDS) -> int:
    # После аварийного перезапуска TemporaryDirectory мог не успеть очиститься.
    _ensure_library_storage_root()
    now = time.time()
    candidates = [folder for folder in IMPORT_WORK_ROOT.iterdir() if folder.is_dir()]
    # Старые версии распаковывали весь ZIP в системный /tmp.
    system_temp = Path(tempfile.gettempdir())
    candidates.extend(
        folder
        for pattern in ("voxlyra_library_*", "voxlyra_book_*")
        for folder in system_temp.glob(pattern)
        if folder.is_dir()
    )
    removed = 0
    for folder in candidates:
        try:
            if now - folder.stat().st_mtime < max(300, int(max_age_seconds)):
                continue
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed


def _ensure_library_free_space(required_bytes: int, *, operation: str) -> None:
    _ensure_library_storage_root()
    try:
        free = int(shutil.disk_usage(DEFAULT_STORAGE_ROOT).free)
    except OSError:
        return
    required = max(0, int(required_bytes))
    if free < required:
        raise LibraryImportStorageError(
            f"На сервере недостаточно свободного места для {operation}. "
            f"Свободно {_format_mb(free)}, требуется не менее {_format_mb(required)}. "
            "Незавершённый импорт будет удалён. Освободите место или увеличьте диск и повторите загрузку."
        )


def _inspect_library_archive(zip_path: Path, max_unpacked: int) -> list[dict[str, Any]]:
    folders: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(zip_path) as archive:
        all_members = archive.infolist()
        if any(not _safe_member(item.filename) for item in all_members):
            raise ValueError("Архив содержит небезопасные пути")
        if any((item.external_attr >> 16) & 0o170000 == 0o120000 for item in all_members):
            raise ValueError("Архив содержит символические ссылки")

        unpacked_total = 0
        for item in all_members:
            if item.is_dir():
                continue
            compressed = max(1, int(item.compress_size or 0))
            size = max(0, int(item.file_size or 0))
            if size > 10 * 1024 * 1024 and size / compressed > MAX_COMPRESSION_RATIO:
                raise ValueError("Обнаружено подозрительно сильное сжатие ZIP")
            unpacked_total += size
            normalized = item.filename.replace("\\", "/")
            parts = PurePosixPath(normalized).parts
            books_index = next(
                (index for index, part in enumerate(parts) if part.casefold() == "books"),
                None,
            )
            if books_index is None or len(parts) <= books_index + 2:
                continue
            folder_name = str(parts[books_index + 1]).strip()
            if not folder_name or folder_name in {".", ".."}:
                continue
            relative_parts = parts[books_index + 2:]
            if not relative_parts:
                continue
            entry = folders.setdefault(
                folder_name,
                {"name": folder_name, "members": [], "unpacked_size": 0},
            )
            entry["members"].append(item.filename)
            entry["unpacked_size"] += size

        if unpacked_total > int(max_unpacked):
            raise ValueError("Архив после распаковки превышает допустимый размер")

    return sorted(folders.values(), key=lambda item: str(item["name"]).casefold())


def _extract_library_folder(
    zip_path: Path,
    member_names: list[str],
    destination: Path,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member_name in member_names:
                info = archive.getinfo(member_name)
                normalized = info.filename.replace("\\", "/")
                parts = PurePosixPath(normalized).parts
                books_index = next(
                    (index for index, part in enumerate(parts) if part.casefold() == "books"),
                    None,
                )
                if books_index is None or len(parts) <= books_index + 2:
                    continue
                relative_parts = parts[books_index + 2:]
                target = destination.joinpath(*relative_parts)
                target_parent = target.parent.resolve()
                if target_parent != destination_root and destination_root not in target_parent.parents:
                    raise ValueError("Архив содержит небезопасные пути")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
    except OSError as exc:
        if _is_no_space_error(exc):
            raise LibraryImportStorageError(
                "На сервере закончилось свободное место во время обработки книги. "
                "Незавершённый импорт будет удалён. Освободите место или увеличьте диск."
            ) from exc
        raise


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "windows-1251"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip()



def _revision_fingerprint(
    book_path: Path,
    cover_path: Path,
    metadata: dict[str, Any],
    description: str,
) -> str:
    digest = hashlib.sha256()
    for label, path in (("book", book_path), ("cover", cover_path)):
        digest.update(label.encode("ascii"))
        digest.update(_sha256(path).encode("ascii"))
    canonical_metadata = json.dumps(
        metadata,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    digest.update(b"metadata")
    digest.update(canonical_metadata)
    digest.update(b"description")
    digest.update(str(description or "").strip().encode("utf-8"))
    return digest.hexdigest()


def _stored_revision_fingerprint(book_path: Path, cover_path: Path) -> str | None:
    if not book_path.is_file() or not cover_path.is_file():
        return None
    folder = book_path.parent
    metadata_path = folder / "metadata.json"
    description_path = folder / "description.txt"
    if not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(_read_text(metadata_path))
        if not isinstance(metadata, dict):
            return None
        description = _read_text(description_path) if description_path.is_file() else ""
        return _revision_fingerprint(book_path, cover_path, metadata, description)
    except Exception:
        return None


def _store_duplicate_candidate(
    candidate_dir: Path,
    book_path: Path,
    cover_path: Path,
    metadata: dict[str, Any],
    description: str,
) -> None:
    if candidate_dir.exists():
        shutil.rmtree(candidate_dir, ignore_errors=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(book_path, candidate_dir / f"book{book_path.suffix.lower()}")
    shutil.copy2(cover_path, candidate_dir / f"cover{cover_path.suffix.lower()}")
    (candidate_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (candidate_dir / "description.txt").write_text(
        str(description or "").strip(),
        encoding="utf-8",
    )




def _clean_meta_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return " ".join(text.replace("\u00a0", " ").split()).strip()


def _normalize_age_rating(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    match = re.search(r"(0|6|12|14|16|18)\+?", text)
    if match:
        return f"{match.group(1)}+"
    aliases = {"adult": "18+", "mature": "18+", "teen": "12+", "children": "6+", "child": "6+"}
    return aliases.get(text, "")


def _embedded_book_metadata(path: Path) -> dict[str, Any]:
    """Извлекает переносимые метаданные из EPUB/FB2 без доверия к ним как к правам."""
    result: dict[str, Any] = {}
    try:
        suffix = path.suffix.lower()
        if suffix == ".epub":
            with zipfile.ZipFile(path) as archive:
                container = ET.fromstring(archive.read("META-INF/container.xml"))
                rootfile = next((node for node in container.iter() if node.tag.endswith("rootfile")), None)
                if rootfile is None:
                    return result
                opf_name = rootfile.attrib.get("full-path", "")
                if not opf_name:
                    return result
                root = ET.fromstring(archive.read(opf_name))
                values: dict[str, list[str]] = {}
                for node in root.iter():
                    key = node.tag.rsplit("}", 1)[-1].lower()
                    text = _clean_meta_text(node.text)
                    if text:
                        values.setdefault(key, []).append(text)
                        if key == "meta":
                            property_name = str(node.attrib.get("property") or node.attrib.get("name") or "").strip().lower()
                            if property_name:
                                values.setdefault(property_name, []).append(text)
                result["title"] = (values.get("title") or [""])[0]
                result["author"] = (values.get("creator") or [""])[0]
                result["description"] = (values.get("description") or [""])[0]
                result["language"] = (values.get("language") or ["ru"])[0]
                result["year"] = (values.get("date") or [""])[0][:4]
                result["genre"] = list(dict.fromkeys(values.get("subject") or []))
                for key in ("rating", "audience", "age_rating", "age-rating"):
                    if values.get(key):
                        result["age_rating"] = _normalize_age_rating(values[key][0])
                        break
        elif suffix == ".fb2":
            root = ET.parse(path).getroot()
            def texts(name: str) -> list[str]:
                return [_clean_meta_text(n.text) for n in root.iter() if n.tag.rsplit("}", 1)[-1] == name and _clean_meta_text(n.text)]
            result["title"] = (texts("book-title") or [""])[0]
            first = (texts("first-name") or [""])[0]
            middle = (texts("middle-name") or [""])[0]
            last = (texts("last-name") or [""])[0]
            result["author"] = " ".join(x for x in (first, middle, last) if x).strip()
            result["description"] = (texts("annotation") or [""])[0]
            result["language"] = (texts("lang") or ["ru"])[0]
            result["year"] = (texts("date") or [""])[0][:4]
            result["genre"] = list(dict.fromkeys(texts("genre")))
        elif suffix == ".txt":
            stem = path.stem.replace("_", " ").strip()
            if " - " in stem:
                author, title = stem.split(" - ", 1)
                result.update(author=author.strip(), title=title.strip())
            else:
                result["title"] = stem
    except Exception:
        return {}
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _merge_import_metadata(metadata: dict[str, Any], embedded: dict[str, Any], description_path: Path) -> dict[str, Any]:
    merged = dict(embedded)
    merged.update({key: value for key, value in metadata.items() if value not in (None, "", [])})
    if not _clean_meta_text(merged.get("description")) and description_path.exists():
        merged["description"] = _read_text(description_path)
    genres = merged.get("genre") or merged.get("genres") or []
    if isinstance(genres, str):
        genres = [part.strip() for part in re.split(r"[,;|]", genres) if part.strip()]
    merged["genre"] = list(dict.fromkeys(_clean_meta_text(item) for item in genres if _clean_meta_text(item)))
    tags = merged.get("tags") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in re.split(r"[,;|]", tags) if part.strip()]
    merged["tags"] = list(dict.fromkeys(_clean_meta_text(item) for item in tags if _clean_meta_text(item)))
    age = _normalize_age_rating(merged.get("age_rating") or merged.get("age_limit") or merged.get("rating"))
    merged["age_rating"] = age or ""
    merged["title"] = _clean_meta_text(merged.get("title"))
    merged["author"] = _clean_meta_text(merged.get("author"))
    merged["description"] = _clean_meta_text(merged.get("description"))
    merged["language"] = _clean_meta_text(merged.get("language")) or "ru"
    return merged

def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", value).strip("_")
    return value[:80] or "book"


async def ensure_library_schema() -> None:
    async with connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS library_import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                archive_name TEXT NOT NULL,
                archive_hash TEXT,
                imported_by_user_id INTEGER,
                status TEXT NOT NULL DEFAULT 'processing',
                total_found INTEGER NOT NULL DEFAULT 0,
                imported_count INTEGER NOT NULL DEFAULT 0,
                replaced_count INTEGER NOT NULL DEFAULT 0,
                renumbered_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT NOT NULL DEFAULT '[]',
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(imported_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_library_batches_created
                ON library_import_batches(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_library_batches_status
                ON library_import_batches(status);

            CREATE TABLE IF NOT EXISTS library_creators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_library_creators_name
                ON library_creators(normalized_name);

            CREATE TABLE IF NOT EXISTS library_rights_holders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                holder_type TEXT NOT NULL DEFAULT 'other',
                source_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(normalized_name, holder_type)
            );
            CREATE INDEX IF NOT EXISTS idx_library_rights_holders_name
                ON library_rights_holders(normalized_name, holder_type);

            CREATE TABLE IF NOT EXISTS book_rights (
                book_id INTEGER PRIMARY KEY,
                creator_id INTEGER NOT NULL,
                rights_holder_id INTEGER,
                license_type TEXT NOT NULL,
                revenue_mode TEXT NOT NULL DEFAULT 'none',
                revenue_author_id INTEGER,
                imported_by_user_id INTEGER,
                source_name TEXT,
                rights_checked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(creator_id) REFERENCES library_creators(id) ON DELETE RESTRICT,
                FOREIGN KEY(rights_holder_id) REFERENCES library_rights_holders(id) ON DELETE SET NULL,
                FOREIGN KEY(revenue_author_id) REFERENCES author_profiles(id) ON DELETE SET NULL,
                FOREIGN KEY(imported_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_book_rights_creator ON book_rights(creator_id);
            CREATE INDEX IF NOT EXISTS idx_book_rights_holder ON book_rights(rights_holder_id);

            CREATE TABLE IF NOT EXISTS library_import_duplicates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                existing_book_id INTEGER NOT NULL,
                folder_name TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                candidate_dir TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                resolution TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(batch_id) REFERENCES library_import_batches(id) ON DELETE CASCADE,
                FOREIGN KEY(existing_book_id) REFERENCES books(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_library_duplicates_batch_status
                ON library_import_duplicates(batch_id, status);

            CREATE TABLE IF NOT EXISTS library_import_replacement_backups (
                batch_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                backup_path TEXT NOT NULL,
                old_storage_json TEXT NOT NULL DEFAULT '[]',
                new_storage_path TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(batch_id, book_id),
                FOREIGN KEY(batch_id) REFERENCES library_import_batches(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_library_replacement_backups_batch
                ON library_import_replacement_backups(batch_id);

            CREATE TABLE IF NOT EXISTS library_import_settings (
                id INTEGER PRIMARY KEY CHECK(id=1),
                max_books INTEGER NOT NULL DEFAULT 0,
                max_archive_mb INTEGER NOT NULL DEFAULT 200,
                max_unpacked_mb INTEGER NOT NULL DEFAULT 4096,
                duplicate_policy TEXT NOT NULL DEFAULT 'ask',
                updated_at TEXT NOT NULL
            );
            INSERT OR IGNORE INTO library_import_settings(
                id, max_books, max_archive_mb, max_unpacked_mb, duplicate_policy, updated_at
            ) VALUES(1, 0, 200, 4096, 'ask', '');

            CREATE TABLE IF NOT EXISTS library_channel_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL UNIQUE,
                actor_user_id INTEGER,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                FOREIGN KEY(batch_id) REFERENCES library_import_batches(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_library_channel_queue_due
                ON library_channel_queue(status, next_attempt_at);
            """
        )
        cur = await db.execute("PRAGMA table_info(library_import_settings)")
        setting_columns = {row[1] for row in await cur.fetchall()}
        for name, ddl in {
            "channel_auto_post": "ALTER TABLE library_import_settings ADD COLUMN channel_auto_post INTEGER NOT NULL DEFAULT 1",
            "channel_interval_minutes": "ALTER TABLE library_import_settings ADD COLUMN channel_interval_minutes INTEGER NOT NULL DEFAULT 60",
            "channel_posts_per_run": "ALTER TABLE library_import_settings ADD COLUMN channel_posts_per_run INTEGER NOT NULL DEFAULT 5",
            "legacy_book_limit_removed": "ALTER TABLE library_import_settings ADD COLUMN legacy_book_limit_removed INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in setting_columns:
                await db.execute(ddl)
        cur = await db.execute("PRAGMA table_info(library_import_batches)")
        batch_columns = {row[1] for row in await cur.fetchall()}
        for name, ddl in {
            "replaced_count": "ALTER TABLE library_import_batches ADD COLUMN replaced_count INTEGER NOT NULL DEFAULT 0",
            "renumbered_count": "ALTER TABLE library_import_batches ADD COLUMN renumbered_count INTEGER NOT NULL DEFAULT 0",
        }.items():
            if name not in batch_columns:
                await db.execute(ddl)
        # v1.13.12.1: старое значение 500 было заводским ограничением, а не
        # осознанной настройкой владельца. Один раз переводим его в режим без
        # ограничения; любые другие уже выбранные значения сохраняем.
        await db.execute(
            """UPDATE library_import_settings
               SET max_books=CASE WHEN max_books=500 THEN 0 ELSE max_books END,
                   legacy_book_limit_removed=1
               WHERE id=1 AND COALESCE(legacy_book_limit_removed, 0)=0"""
        )
        cur = await db.execute("PRAGMA table_info(books)")
        existing = {row[1] for row in await cur.fetchall()}
        migrations = {
            "license_type": "ALTER TABLE books ADD COLUMN license_type TEXT NOT NULL DEFAULT 'platform_original'",
            "source_name": "ALTER TABLE books ADD COLUMN source_name TEXT",
            "rights_checked": "ALTER TABLE books ADD COLUMN rights_checked INTEGER NOT NULL DEFAULT 0",
            "import_batch_id": "ALTER TABLE books ADD COLUMN import_batch_id INTEGER",
            "import_file_hash": "ALTER TABLE books ADD COLUMN import_file_hash TEXT",
            "source_author_name": "ALTER TABLE books ADD COLUMN source_author_name TEXT",
            "source_year": "ALTER TABLE books ADD COLUMN source_year TEXT",
            "source_language": "ALTER TABLE books ADD COLUMN source_language TEXT NOT NULL DEFAULT 'ru'",
            "creator_id": "ALTER TABLE books ADD COLUMN creator_id INTEGER",
            "rights_holder_id": "ALTER TABLE books ADD COLUMN rights_holder_id INTEGER",
            "revenue_mode": "ALTER TABLE books ADD COLUMN revenue_mode TEXT NOT NULL DEFAULT 'none'",
            "import_was_replacement": "ALTER TABLE books ADD COLUMN import_was_replacement INTEGER NOT NULL DEFAULT 0",
        }
        for column, sql in migrations.items():
            if column not in existing:
                try:
                    await db.execute(sql)
                except Exception as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        await db.execute("CREATE INDEX IF NOT EXISTS idx_books_import_batch ON books(import_batch_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_books_import_hash ON books(import_file_hash)")
        await db.commit()


def _normalize_person_name(value: str) -> str:
    value = value.casefold().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())[:180]


def _normalize_work_title(value: str) -> str:
    value = str(value or "").casefold().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return " ".join(value.split())[:240]


def _requested_book_id(metadata: dict[str, Any], folder_name: str) -> int | None:
    """Возвращает желаемый ID из метаданных или числового имени папки.

    Для старых пакетов поддерживаются оба явных поля VoxLyra, а также
    общеупотребительные id/book_id. Числовая папка используется только как
    пожелание: при конфликте книга безопасно получает первый свободный ID.
    """
    value: Any = None
    for key in ("voxlyra_book_id", "existing_book_id", "book_id", "id"):
        if metadata.get(key) not in (None, ""):
            value = metadata.get(key)
            break
    if value in (None, "") and re.fullmatch(r"\d+", str(folder_name or "").strip()):
        value = str(folder_name).strip()
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Некорректный ID книги в metadata.json или имени папки") from exc
    if parsed <= 0:
        raise ValueError("ID книги должен быть положительным числом")
    return parsed




def _same_logical_work(
    *,
    existing_title: str,
    existing_author: str,
    incoming_title: str,
    incoming_author: str,
    existing_source: str = "",
    incoming_source: str = "",
) -> bool:
    """Консервативно отличает новую редакцию от чужой книги с тем же ID."""
    old_title = _normalize_work_title(existing_title)
    new_title = _normalize_work_title(incoming_title)
    old_author = _normalize_person_name(existing_author)
    new_author = _normalize_person_name(incoming_author)
    old_source = str(existing_source or "").strip().casefold()
    new_source = str(incoming_source or "").strip().casefold()

    if old_source and new_source and old_source == new_source:
        return True
    if old_title and new_title and old_title == new_title and old_author == new_author:
        return True
    if not old_author or old_author != new_author or not old_title or not new_title:
        return False
    if old_title in new_title or new_title in old_title:
        return min(len(old_title), len(new_title)) >= 5
    old_tokens = set(old_title.split())
    new_tokens = set(new_title.split())
    if not old_tokens or not new_tokens:
        return False
    shared_ratio = len(old_tokens & new_tokens) / min(len(old_tokens), len(new_tokens))
    return shared_ratio >= 0.67


async def _first_free_book_id(db, *, start_at: int = 1) -> int:
    candidate = max(1, int(start_at))
    cur = await db.execute(
        "SELECT id FROM books WHERE id>=? ORDER BY id",
        (candidate,),
    )
    for row in await cur.fetchall():
        current = int(row["id"])
        if current > candidate:
            break
        if current == candidate:
            candidate += 1
    return candidate


async def _ensure_creator_and_rights(
    db, *, author_name: str, metadata: dict[str, Any], license_type: str,
    source_name: str, actor_user_id: int, now: str
) -> tuple[int, int | None, str, int | None]:
    creator_norm = _normalize_person_name(author_name)
    await db.execute(
        """INSERT INTO library_creators(display_name, normalized_name, created_at, updated_at)
           VALUES(?, ?, ?, ?)
           ON CONFLICT(normalized_name) DO UPDATE SET
             display_name=excluded.display_name, updated_at=excluded.updated_at""",
        (author_name[:180], creator_norm, now, now),
    )
    cur = await db.execute("SELECT id FROM library_creators WHERE normalized_name=?", (creator_norm,))
    creator_id = int((await cur.fetchone())["id"])

    holder_type = str(metadata.get("rights_holder_type") or "").strip().lower()
    holder_name = str(metadata.get("rights_holder") or metadata.get("rights_holder_name") or "").strip()
    if not holder_type:
        holder_type = "public_domain" if license_type == "public_domain" else ("platform" if license_type == "platform_original" else "person")
    if holder_type not in RIGHTS_HOLDER_TYPES:
        holder_type = "other"
    if not holder_name:
        if holder_type == "public_domain":
            holder_name = "Общественное достояние"
        elif holder_type == "platform":
            holder_name = "VoxLyra"
        else:
            holder_name = author_name
    holder_norm = _normalize_person_name(holder_name)
    await db.execute(
        """INSERT INTO library_rights_holders(display_name, normalized_name, holder_type, source_name, created_at, updated_at)
           VALUES(?, ?, ?, ?, ?, ?)
           ON CONFLICT(normalized_name, holder_type) DO UPDATE SET
             display_name=excluded.display_name, source_name=excluded.source_name, updated_at=excluded.updated_at""",
        (holder_name[:180], holder_norm, holder_type, source_name[:300], now, now),
    )
    cur = await db.execute(
        "SELECT id FROM library_rights_holders WHERE normalized_name=? AND holder_type=?",
        (holder_norm, holder_type),
    )
    rights_holder_id = int((await cur.fetchone())["id"])

    revenue_mode = str(metadata.get("revenue_mode") or "").strip().lower()
    if not revenue_mode:
        revenue_mode = "platform" if holder_type == "platform" else "none"
    if revenue_mode not in REVENUE_MODES:
        revenue_mode = "none"
    revenue_author_id = None
    payout_user_id = metadata.get("revenue_user_id")
    if revenue_mode == "author_account" and payout_user_id not in (None, ""):
        try:
            cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(payout_user_id),))
            row = await cur.fetchone()
            revenue_author_id = int(row["id"]) if row else None
        except (TypeError, ValueError):
            revenue_author_id = None
        if revenue_author_id is None:
            revenue_mode = "none"
    return creator_id, rights_holder_id, revenue_mode, revenue_author_id


async def _create_batch(archive_name: str, archive_hash: str, actor_user_id: int) -> int:
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO library_import_batches(
                archive_name, archive_hash, imported_by_user_id, status, created_at
            ) VALUES(?, ?, ?, 'processing', ?)
            """,
            (archive_name, archive_hash, actor_user_id, utc_now()),
        )
        await db.commit()
        return int(cur.lastrowid)


async def _finish_batch(batch_id: int, result: ImportResult, total_found: int) -> None:
    errors = [
        {"folder": item.folder, "title": item.title, "reasons": item.reasons}
        for item in result.errors
    ]
    async with connect() as db:
        await db.execute(
            """
            UPDATE library_import_batches
            SET status='completed', total_found=?, imported_count=?, replaced_count=?,
                renumbered_count=?, duplicate_count=?, error_count=?, errors_json=?, completed_at=?
            WHERE id=?
            """,
            (
                total_found,
                result.added,
                result.replaced,
                result.renumbered,
                result.duplicates,
                len(result.errors),
                json.dumps(errors, ensure_ascii=False),
                utc_now(),
                batch_id,
            ),
        )
        await db.commit()


async def import_library_zip(
    zip_path: str | Path,
    archive_name: str,
    actor_user_id: int,
    progress_callback: ProgressCallback | None = None,
) -> ImportResult:
    zip_path = Path(zip_path)
    archive_hash = await _run_blocking(_sha256, zip_path)
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute(
            """SELECT id FROM library_import_batches
               WHERE archive_hash=? AND status IN ('completed','published')
               ORDER BY id DESC LIMIT 1""",
            (archive_hash,),
        )
        previous = await cur.fetchone()
    if previous:
        raise ValueError(f"Этот архив уже импортировался ранее: пакет #{int(previous['id'])}")

    batch_id = await _create_batch(archive_name, archive_hash, actor_user_id)
    result = ImportResult(batch_id=batch_id)
    total_found = 0

    async def report(processed: int, *, phase: int = 0) -> None:
        if progress_callback is None:
            return
        await progress_callback({
            "batch_id": batch_id,
            "processed": processed,
            "total": total_found,
            "added": result.added,
            "replaced": result.replaced,
            "renumbered": result.renumbered,
            "duplicates": result.duplicates,
            "errors": len(result.errors),
            "phase": phase,
        })

    await report(0, phase=0)

    import_settings = await get_import_settings()
    max_unpacked = int(import_settings["max_unpacked_mb"]) * 1024 * 1024
    max_books = int(import_settings["max_books"])
    duplicate_policy = str(import_settings["duplicate_policy"] or "ask")

    await _run_blocking(cleanup_stale_import_work)
    try:
        folders = await _run_blocking(_inspect_library_archive, zip_path, max_unpacked)
    except (zipfile.BadZipFile, ValueError) as exc:
        result.errors.append(ImportErrorItem(folder="ZIP", reasons=[str(exc)]))
        await _finish_batch(batch_id, result, total_found)
        return result
    except OSError as exc:
        if _is_no_space_error(exc):
            raise LibraryImportStorageError(
                "На сервере закончилось свободное место при проверке ZIP. "
                "Незавершённый импорт будет удалён."
            ) from exc
        raise

    if not folders:
        result.errors.append(ImportErrorItem(folder="Books", reasons=["Не найдена папка Books или папки книг"]))
        await _finish_batch(batch_id, result, total_found)
        return result

    total_found = len(folders)
    await report(0, phase=1)
    if max_books > 0 and len(folders) > max_books:
        result.errors.append(
            ImportErrorItem(
                folder="Books",
                reasons=[f"В архиве {len(folders)} книг; максимум {max_books}"],
            )
        )
        folders = folders[:max_books]

    for processed_index, folder_info in enumerate(folders, 1):
        folder_name = str(folder_info["name"])
        folder_unpacked = max(0, int(folder_info.get("unpacked_size") or 0))
        _ensure_library_free_space(
            folder_unpacked + MIN_IMPORT_FREE_BYTES,
            operation=f"обработки книги {processed_index} из {len(folders)}",
        )
        with tempfile.TemporaryDirectory(
            prefix=f"book_{processed_index:05d}_",
            dir=IMPORT_WORK_ROOT,
        ) as temp_name:
            temp_root = Path(temp_name)
            folder = temp_root / folder_name
            try:
                await _run_blocking(
                    _extract_library_folder,
                    zip_path,
                    list(folder_info.get("members") or []),
                    folder,
                )
                item = ImportErrorItem(folder=folder.name)
                metadata_path = folder / "metadata.json"
                description_path = folder / "description.txt"
                files = [p for p in folder.iterdir() if p.is_file()]
                if len(files) > MAX_FILES_PER_BOOK_FOLDER:
                    item.reasons.append(f"Слишком много файлов в папке: {len(files)}; максимум {MAX_FILES_PER_BOOK_FOLDER}")
                book_files = [p for p in files if p.suffix.lower() in BOOK_EXTENSIONS]
                cover_files = [p for p in files if p.suffix.lower() in COVER_EXTENSIONS and p.stem.lower().startswith("cover")]

                metadata: dict[str, Any] = {}
                metadata_error = ""
                if metadata_path.exists():
                    try:
                        metadata = json.loads(await _run_blocking(_read_text, metadata_path))
                        if not isinstance(metadata, dict):
                            raise ValueError("корневое значение должно быть объектом")
                    except Exception as exc:
                        metadata_error = f"Ошибка metadata.json: {exc}"
                if not book_files:
                    item.reasons.append("Нет файла книги EPUB/FB2/TXT/DOCX/PDF")
                embedded = await _run_blocking(_embedded_book_metadata, book_files[0]) if book_files else {}
                metadata = _merge_import_metadata(metadata, embedded, description_path)
                item.title = str(metadata.get("title") or "Без названия").strip()
                if metadata_error:
                    item.reasons.append(metadata_error)
                if not cover_files:
                    item.reasons.append("Нет обложки cover.jpg/png/webp")
                if not str(metadata.get("description") or "").strip():
                    item.reasons.append("Не найдено описание: добавьте description.txt или поле description")

                title = str(metadata.get("title") or "").strip()
                author = str(metadata.get("author") or "").strip()
                genres = metadata.get("genre") or []
                if isinstance(genres, str):
                    genres = [genres]
                if not title:
                    item.reasons.append("Не указано название")
                if not author:
                    item.reasons.append("Не указан автор")
                if not genres:
                    item.reasons.append("Не указан жанр")
                if not str(metadata.get("age_rating") or "").strip():
                    item.reasons.append("Не указано возрастное ограничение (0+/6+/12+/14+/16+/18+)")
                license_type = str(metadata.get("license") or "").strip()
                if license_type not in ALLOWED_LICENSES:
                    item.reasons.append("Недопустимый тип лицензии")
                if metadata.get("rights_checked") is not True:
                    item.reasons.append("Права не подтверждены: rights_checked должно быть true")
                if item.reasons:
                    result.errors.append(item)
                    await report(processed_index, phase=2)
                    continue

                book_path = sorted(book_files, key=lambda p: (p.suffix.lower() != ".epub", p.name.lower()))[0]
                cover_path = sorted(cover_files, key=lambda p: p.name.lower())[0]
                file_hash = await _run_blocking(_sha256, book_path)
                normalized_title = " ".join(title.casefold().replace("ё", "е").split())
                normalized_author = " ".join(author.casefold().replace("ё", "е").split())
                description = str(metadata.get("description") or "").strip() or _read_text(description_path)
                incoming_revision = await _run_blocking(
                    _revision_fingerprint,
                    book_path,
                    cover_path,
                    metadata,
                    description,
                )

                authoritative_update_id = any(
                    metadata.get(key) not in (None, "")
                    for key in ("voxlyra_book_id", "existing_book_id")
                ) or metadata.get("replace_existing") is True
                try:
                    requested_id = _requested_book_id(metadata, folder.name)
                except ValueError as exc:
                    item.reasons.append(str(exc))
                    result.errors.append(item)
                    await report(processed_index, phase=2)
                    continue

                logical_duplicate = None
                hash_duplicate = None
                assigned_id = requested_id
                async with connect() as db:
                    occupied = None
                    if requested_id is not None:
                        cur = await db.execute(
                            """SELECT id, title, source_author_name, source_name, source_file_name, cover_path
                               FROM books WHERE id=? LIMIT 1""",
                            (requested_id,),
                        )
                        occupied = await cur.fetchone()

                    # Экспортный VoxLyra-ID однозначно указывает обновляемую книгу.
                    # Обычный id/book_id и номер папки являются желаемым номером:
                    # при совпадении произведения версия заменяется, при настоящем
                    # конфликте новая книга получает первый свободный ID.
                    if occupied is not None and authoritative_update_id:
                        logical_duplicate = occupied
                    elif occupied is not None:
                        same_work = _same_logical_work(
                            existing_title=str(occupied["title"] or ""),
                            existing_author=str(occupied["source_author_name"] or ""),
                            incoming_title=title,
                            incoming_author=author,
                            existing_source=str(occupied["source_name"] or ""),
                            incoming_source=str(metadata.get("source") or ""),
                        )
                        if same_work:
                            logical_duplicate = occupied
                        else:
                            assigned_id = await _first_free_book_id(db)

                    if logical_duplicate is None:
                        cur = await db.execute(
                            """SELECT id, title, source_author_name, source_name, source_file_name, cover_path
                               FROM books
                               WHERE publication_status!='deleted' AND normalized_title=?
                               ORDER BY id LIMIT 100""",
                            (normalized_title,),
                        )
                        for row in await cur.fetchall():
                            if _normalize_person_name(str(row["source_author_name"] or "")) == _normalize_person_name(author):
                                logical_duplicate = row
                                break

                    if logical_duplicate is None:
                        cur = await db.execute(
                            """SELECT id, title, source_author_name, source_name, source_file_name, cover_path
                               FROM books
                               WHERE publication_status!='deleted'
                                 AND (import_file_hash=? OR source_file_hash=?)
                               ORDER BY id LIMIT 1""",
                            (file_hash, file_hash),
                        )
                        hash_duplicate = await cur.fetchone()

                if logical_duplicate is not None:
                    result.duplicates += 1
                    existing_id = int(logical_duplicate["id"])
                    existing_revision = await _run_blocking(
                        _stored_revision_fingerprint,
                        Path(str(logical_duplicate["source_file_name"] or "")),
                        Path(str(logical_duplicate["cover_path"] or "")),
                    )
                    if existing_revision and existing_revision == incoming_revision:
                        await report(processed_index, phase=2)
                        continue
                    await _replace_book_from_candidate(
                        existing_id,
                        folder,
                        metadata,
                        file_hash,
                        batch_id=batch_id,
                        actor_user_id=actor_user_id,
                    )
                    result.replaced += 1
                    result.book_ids.append(existing_id)
                    await report(processed_index, phase=2)
                    continue

                if hash_duplicate is not None:
                    result.duplicates += 1
                    existing_id = int(hash_duplicate["id"])
                    existing_revision = await _run_blocking(
                        _stored_revision_fingerprint,
                        Path(str(hash_duplicate["source_file_name"] or "")),
                        Path(str(hash_duplicate["cover_path"] or "")),
                    )
                    if existing_revision and existing_revision == incoming_revision:
                        await report(processed_index, phase=2)
                        continue
                    if duplicate_policy == "skip":
                        result.errors.append(ImportErrorItem(
                            folder=folder.name,
                            title=title,
                            reasons=[f"Найден дубль файла книги ID {existing_id}; пропущено по настройке"],
                        ))
                        await report(processed_index, phase=2)
                        continue
                    candidate_dir = DEFAULT_STORAGE_ROOT / "duplicates" / str(batch_id) / folder.name
                    candidate_dir.parent.mkdir(parents=True, exist_ok=True)
                    await _run_blocking(
                        _store_duplicate_candidate,
                        candidate_dir,
                        book_path,
                        cover_path,
                        metadata,
                        description,
                    )
                    async with connect() as db:
                        cur = await db.execute(
                            """INSERT INTO library_import_duplicates(
                                   batch_id, existing_book_id, folder_name, title, author, file_hash,
                                   candidate_dir, metadata_json, status, created_at
                               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                            (
                                batch_id,
                                existing_id,
                                folder.name,
                                title,
                                author,
                                file_hash,
                                str(candidate_dir),
                                json.dumps(metadata, ensure_ascii=False),
                                utc_now(),
                            ),
                        )
                        duplicate_id = int(cur.lastrowid)
                        await db.commit()
                    result.duplicate_ids.append(duplicate_id)
                    if duplicate_policy == "replace":
                        await resolve_duplicate(duplicate_id, "replace")
                    await report(processed_index, phase=2)
                    continue

                if requested_id is not None and assigned_id != requested_id:
                    result.renumbered += 1
                    result.id_changes.append({
                        "folder": folder.name,
                        "requested_id": requested_id,
                        "assigned_id": int(assigned_id),
                    })

                try:
                    chapters = await _run_blocking(
                        parse_book_file,
                        book_path,
                        original_filename=book_path.name,
                        temp_dir=temp_root / f"parse_{folder.name}",
                    )
                    chapters = [ch for ch in chapters if (ch.text or "").strip()]
                    if not chapters:
                        raise BookParseError("В книге не найден текст")
                except Exception as exc:
                    item.reasons.append(f"Ошибка чтения книги: {exc}")
                    result.errors.append(item)
                    await report(processed_index, phase=2)
                    continue

                age = str(metadata.get("age_rating") or "12+").strip()
                pricing = str(metadata.get("free_or_paid") or "free").strip().lower()
                price_stars = max(0, int(metadata.get("price_stars") or 0))
                pricing_type = "whole_book" if pricing in {"paid", "whole_book"} and price_stars > 0 else "free"
                now = utc_now()
                storage_dir = DEFAULT_STORAGE_ROOT / "books" / str(batch_id) / folder.name
                stored_book = storage_dir / f"book{book_path.suffix.lower()}"
                stored_cover = storage_dir / f"cover{cover_path.suffix.lower()}"

                def store_book_files() -> None:
                    storage_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(book_path, stored_book)
                    shutil.copy2(cover_path, stored_cover)
                    (storage_dir / "metadata.json").write_text(
                        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    (storage_dir / "description.txt").write_text(description, encoding="utf-8")

                await _run_blocking(store_book_files)

                async with connect() as db:
                    source_name = str(metadata.get("source") or "").strip()
                    creator_id, rights_holder_id, revenue_mode, revenue_author_id = await _ensure_creator_and_rights(
                        db, author_name=author, metadata=metadata, license_type=license_type,
                        source_name=source_name, actor_user_id=actor_user_id, now=now,
                    )
                    id_column = "id, " if assigned_id is not None else ""
                    id_placeholder = "?, " if assigned_id is not None else ""
                    cur = await db.execute(
                        f"""
                        INSERT INTO books(
                            {id_column}author_id, title, description, age_limit, writing_status, publication_status,
                            cover_path, normalized_title, source_file_hash, source_file_name,
                            allow_download, pricing_type, price_stars, content_type, reading_mode,
                            license_type, source_name, rights_checked, import_batch_id, import_file_hash,
                            source_author_name, source_year, source_language, creator_id, rights_holder_id,
                            revenue_mode, created_at, updated_at
                        ) VALUES({id_placeholder}NULL, ?, ?, ?, 'finished', 'draft', ?, ?, ?, ?, 1, ?, ?, 'book', 'ltr',
                                 ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        ((int(assigned_id),) if assigned_id is not None else ()) + (
                            title, description, age, str(stored_cover), normalized_title, file_hash, str(stored_book),
                            pricing_type, price_stars, license_type, source_name,
                            batch_id, file_hash, author, str(metadata.get("year") or "").strip(),
                            str(metadata.get("language") or "ru").strip(), creator_id, rights_holder_id,
                            revenue_mode, now, now,
                        ),
                    )
                    book_id = int(cur.lastrowid)
                    await db.execute(
                        """INSERT INTO book_rights(
                               book_id, creator_id, rights_holder_id, license_type, revenue_mode,
                               revenue_author_id, imported_by_user_id, source_name, rights_checked,
                               created_at, updated_at
                           ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                        (book_id, creator_id, rights_holder_id, license_type, revenue_mode,
                         revenue_author_id, actor_user_id, source_name, now, now),
                    )
                    for ch in chapters:
                        is_free = 1 if pricing_type == "free" else 0
                        await db.execute(
                            """
                            INSERT INTO chapters(book_id, number, title, text, is_free, price_stars, status, created_at, updated_at)
                            VALUES(?, ?, ?, ?, ?, 0, 'draft', ?, ?)
                            """,
                            (book_id, int(ch.number), str(ch.title)[:160], ch.text, is_free, now, now),
                        )
                    for genre in genres:
                        label = str(genre).strip()
                        if not label:
                            continue
                        code = _slug(label.casefold())
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO book_option_values(book_id, option_group, option_code, option_label, created_at)
                            VALUES(?, 'genres', ?, ?, ?)
                            """,
                            (book_id, code, label, now),
                        )
                    tags = metadata.get("tags") or []
                    if isinstance(tags, str):
                        tags = [tags]
                    for tag in tags:
                        label = str(tag).strip()
                        if label:
                            await db.execute(
                                """INSERT OR IGNORE INTO book_option_values(book_id, option_group, option_code, option_label, created_at)
                                   VALUES(?, 'plot_tags', ?, ?, ?)""",
                                (book_id, _slug(label.casefold()), label, now),
                            )
                    await db.commit()
                result.added += 1
                result.book_ids.append(book_id)
                await report(processed_index, phase=2)
            except Exception as exc:
                if _is_no_space_error(exc):
                    raise LibraryImportStorageError(
                        "На сервере закончилось свободное место во время импорта. "
                        "Незавершённый пакет будет удалён автоматически. "
                        "Освободите место или увеличьте диск и повторите загрузку."
                    ) from exc
                raise

    await _finish_batch(batch_id, result, total_found)
    await report(total_found if max_books <= 0 else min(total_found, max_books), phase=3)
    return result


async def _inspect_book_quality(row: Any, chapters: list[Any]) -> tuple[list[str], list[str], list[dict[str, Any]], int]:
    """Глубокая проверка файла, глав, языка и обложки без изменения книги."""
    blockers: list[str] = []
    warnings: list[str] = []
    evidence: list[dict[str, Any]] = []
    score = 100

    source_path = Path(str(row["source_file_name"] or ""))
    if source_path.is_file():
        try:
            parsed = parse_book_file(source_path)
            if not parsed:
                blockers.append("исходный файл не содержит читаемых глав")
                score -= 35
        except Exception as exc:
            blockers.append(f"исходный файл повреждён или не читается: {str(exc)[:120]}")
            score -= 40

    cover_path = Path(str(row["cover_path"] or ""))
    if cover_path.is_file():
        try:
            with Image.open(cover_path) as image:
                image.verify()
            with Image.open(cover_path) as image:
                width, height = image.size
            if width < 300 or height < 450:
                warnings.append(f"низкое разрешение обложки: {width}×{height}")
                score -= 6
            ratio = width / max(1, height)
            if ratio < 0.48 or ratio > 0.85:
                warnings.append("нестандартные пропорции обложки")
                score -= 4
        except (UnidentifiedImageError, OSError, ValueError):
            blockers.append("обложка повреждена или имеет неподдерживаемый формат")
            score -= 25

    if len(str(row["description"] or "").strip()) < 80:
        warnings.append("слишком короткое описание")
        score -= 5

    seen_hashes: dict[str, int] = {}
    empty_count = 0
    short_count = 0
    replacement_count = 0
    control_count = 0
    total_letters = 0
    cyrillic_letters = 0
    latin_letters = 0
    duplicate_pairs: list[tuple[int, int]] = []

    for chapter in chapters:
        number = int(chapter["number"] or 0)
        text = str(chapter["text"] or "").strip()
        if not text:
            empty_count += 1
            continue
        if len(text) < 80:
            short_count += 1
        replacement_count += text.count("�")
        control_chars = [ch for ch in text if ord(ch) < 32 and ch not in "\n\r\t"]
        control_count += len(control_chars)
        if "�" in text and len(evidence) < 12:
            index = text.index("�")
            excerpt = " ".join(text[max(0, index - 70): index + 71].split())
            evidence.append({
                "kind": "encoding",
                "chapter": number,
                "label": "Повреждённая кодировка",
                "excerpt": excerpt[:220],
            })
        if control_chars and len(evidence) < 12:
            bad = control_chars[0]
            index = text.index(bad)
            excerpt = " ".join(text[max(0, index - 70): index + 71].replace(bad, f"[U+{ord(bad):04X}]").split())
            evidence.append({
                "kind": "control_character",
                "chapter": number,
                "label": f"Управляющий символ U+{ord(bad):04X}",
                "excerpt": excerpt[:220],
            })
        normalized = re.sub(r"\s+", " ", text).strip().casefold()
        digest = hashlib.sha256(normalized.encode("utf-8", errors="ignore")).hexdigest()
        if len(normalized) >= 300 and digest in seen_hashes:
            duplicate_pairs.append((seen_hashes[digest], number))
        else:
            seen_hashes[digest] = number
        for ch in text:
            if ch.isalpha():
                total_letters += 1
                code = ord(ch.lower())
                if 0x0430 <= code <= 0x044f or ch.lower() == "ё":
                    cyrillic_letters += 1
                elif ("a" <= ch.lower() <= "z"):
                    latin_letters += 1

    if empty_count:
        blockers.append(f"пустых глав: {empty_count}")
        score -= min(30, empty_count * 5)
    if short_count:
        warnings.append(f"подозрительно коротких глав: {short_count}")
        score -= min(12, short_count * 2)
    if duplicate_pairs:
        preview = ", ".join(f"{a}={b}" for a, b in duplicate_pairs[:4])
        blockers.append(f"точные дубли глав: {preview}")
        for original, duplicate in duplicate_pairs[:8]:
            if len(evidence) >= 12:
                break
            evidence.append({
                "kind": "duplicate_chapter",
                "chapter": duplicate,
                "label": f"Глава {duplicate} полностью совпадает с главой {original}",
                "excerpt": "",
            })
        score -= min(30, len(duplicate_pairs) * 6)
    if replacement_count:
        warnings.append(f"символов повреждённой кодировки: {replacement_count}")
        score -= min(12, replacement_count)
    if control_count:
        blockers.append(f"недопустимых управляющих символов: {control_count}")
        score -= 15

    declared_language = str(row["source_language"] or "ru").strip().lower()
    if total_letters >= 500:
        cyr_share = cyrillic_letters / total_letters
        lat_share = latin_letters / total_letters
        if declared_language.startswith("ru") and cyr_share < 0.55:
            warnings.append(f"язык текста не похож на русский: кириллица {round(cyr_share * 100)}%")
            score -= 10
        elif declared_language.startswith("en") and lat_share < 0.55:
            warnings.append(f"язык текста не похож на английский: латиница {round(lat_share * 100)}%")
            score -= 10

    return blockers, warnings, evidence, max(0, score)


async def audit_batch_publication(batch_id: int) -> dict[str, Any]:
    """Глубоко проверяет черновики пакета перед публикацией."""
    await ensure_library_schema()
    ready_ids: list[int] = []
    blocked: list[dict[str, Any]] = []
    checked_items: list[dict[str, Any]] = []
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.id, b.title, b.description, b.source_author_name, b.source_file_name,
                   b.cover_path, b.license_type, b.rights_checked, b.pricing_type, b.price_stars,
                   b.creator_id, b.rights_holder_id, b.revenue_mode, b.source_language,
                   (SELECT COUNT(*) FROM book_option_values v
                    WHERE v.book_id=b.id AND v.option_group='genres') AS genres_count,
                   (SELECT COUNT(*) FROM chapters c
                    WHERE c.book_id=b.id AND c.status!='deleted') AS chapters_count
            FROM books b
            WHERE b.import_batch_id=? AND b.publication_status='draft'
            ORDER BY b.id
            """,
            (int(batch_id),),
        )
        rows = await cur.fetchall()
        chapter_map: dict[int, list[Any]] = {}
        for row in rows:
            cur = await db.execute(
                "SELECT number, title, text FROM chapters WHERE book_id=? AND status!='deleted' ORDER BY number",
                (int(row["id"]),),
            )
            chapter_map[int(row["id"])] = await cur.fetchall()

    for row in rows:
        reasons: list[str] = []
        warnings: list[str] = []
        if not str(row["title"] or "").strip(): reasons.append("не указано название")
        if not str(row["source_author_name"] or "").strip(): reasons.append("не указан автор")
        if not str(row["description"] or "").strip(): reasons.append("нет описания")
        if int(row["genres_count"] or 0) <= 0: reasons.append("не выбран жанр")
        if int(row["chapters_count"] or 0) <= 0: reasons.append("не найден текст книги")
        if not Path(str(row["source_file_name"] or "")).is_file(): reasons.append("исходный файл отсутствует")
        if not Path(str(row["cover_path"] or "")).is_file(): reasons.append("обложка отсутствует")
        if str(row["license_type"] or "").strip() not in ALLOWED_LICENSES: reasons.append("неподдерживаемый тип лицензии")
        if not bool(row["rights_checked"]): reasons.append("права не подтверждены")
        if row["creator_id"] is None: reasons.append("не создана карточка реального автора")
        if row["rights_holder_id"] is None: reasons.append("не указан правообладатель")
        if str(row["pricing_type"] or "free") != "free" and int(row["price_stars"] or 0) > 0 and str(row["revenue_mode"] or "none") == "none":
            reasons.append("для платной книги не указан получатель дохода")

        deep_blockers, deep_warnings, evidence, score = await _inspect_book_quality(row, chapter_map.get(int(row["id"]), []))
        reasons.extend(deep_blockers)
        warnings.extend(deep_warnings)
        item = {
            "book_id": int(row["id"]),
            "title": str(row["title"] or "Без названия"),
            "reasons": reasons,
            "warnings": warnings,
            "evidence": evidence,
            "quality_score": score,
        }
        checked_items.append(item)
        if reasons:
            blocked.append(item)
        else:
            ready_ids.append(int(row["id"]))

    average_score = round(sum(x["quality_score"] for x in checked_items) / len(checked_items)) if checked_items else 0
    warning_count = sum(len(x["warnings"]) for x in checked_items)
    return {
        "total": len(rows), "ready": len(ready_ids), "blocked": len(blocked),
        "ready_ids": ready_ids, "blocked_items": blocked, "checked_items": checked_items,
        "average_score": average_score, "warning_count": warning_count,
    }


async def publish_batch(batch_id: int) -> dict[str, Any]:
    """Публикует книги в каталоге и ставит карточки в регулируемую очередь канала."""
    audit = await audit_batch_publication(batch_id)
    ready_ids = list(audit["ready_ids"])
    queued = 0
    if ready_ids:
        placeholders = ",".join("?" for _ in ready_ids)
        now = utc_now()
        cfg = await get_import_settings()
        async with connect() as db:
            await db.execute(
                f"UPDATE books SET publication_status='published', updated_at=? WHERE id IN ({placeholders})",
                [now, *ready_ids],
            )
            await db.execute(
                f"UPDATE chapters SET status='published', updated_at=? WHERE book_id IN ({placeholders}) AND status='draft'",
                [now, *ready_ids],
            )
            if bool(cfg.get("channel_auto_post", 1)):
                cur = await db.execute(
                    "SELECT imported_by_user_id FROM library_import_batches WHERE id=?",
                    (int(batch_id),),
                )
                row = await cur.fetchone()
                actor_id = int(row[0]) if row and row[0] is not None else None
                for book_id in ready_ids:
                    await db.execute(
                        """INSERT OR IGNORE INTO library_channel_queue(
                               batch_id, book_id, actor_user_id, status, attempts, next_attempt_at, created_at
                           ) VALUES(?, ?, ?, 'queued', 0, ?, ?)""",
                        (int(batch_id), int(book_id), actor_id, now, now),
                    )
                cur = await db.execute(
                    "SELECT COUNT(*) FROM library_channel_queue WHERE batch_id=? AND status='queued'",
                    (int(batch_id),),
                )
                queued = int((await cur.fetchone())[0] or 0)
            cur = await db.execute(
                """SELECT
                       SUM(CASE WHEN publication_status='draft' THEN 1 ELSE 0 END) AS drafts,
                       SUM(CASE WHEN publication_status='published' THEN 1 ELSE 0 END) AS published
                   FROM books WHERE import_batch_id=?""",
                (int(batch_id),),
            )
            batch_counts = await cur.fetchone()
            if int(batch_counts["drafts"] or 0) == 0 and int(batch_counts["published"] or 0) > 0:
                await db.execute(
                    "UPDATE library_import_batches SET status='published' WHERE id=?",
                    (int(batch_id),),
                )
            await db.commit()
    return {
        "published": len(ready_ids),
        "queued": queued,
        "skipped": int(audit["blocked"]),
        "blocked_items": audit["blocked_items"],
    }


async def get_channel_schedule_status() -> dict[str, Any]:
    await ensure_library_schema()
    cfg = await get_import_settings()
    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM library_channel_queue WHERE status='queued'")
        queued = int((await cur.fetchone())[0] or 0)
        cur = await db.execute("SELECT COUNT(*) FROM library_channel_queue WHERE status='failed'")
        failed = int((await cur.fetchone())[0] or 0)
        cur = await db.execute("SELECT COUNT(*) FROM library_channel_queue WHERE status='sent'")
        sent = int((await cur.fetchone())[0] or 0)
    return {**cfg, "queued": queued, "failed": failed, "sent": sent}


async def retry_failed_channel_posts() -> int:
    await ensure_library_schema()
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "UPDATE library_channel_queue SET status='queued', attempts=0, next_attempt_at=?, last_error=NULL WHERE status='failed'",
            (now,),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def process_library_channel_queue(bot) -> int:
    """Отправляет только разрешённое число карточек и переносит следующий запуск по таймеру."""
    from app.services.publication import post_book_to_channel

    await ensure_library_schema()
    cfg = await get_import_settings()
    if not bool(cfg.get("channel_auto_post", 1)):
        return 0
    limit = max(1, min(50, int(cfg.get("channel_posts_per_run", 5))))
    interval = max(1, min(10080, int(cfg.get("channel_interval_minutes", 60))))
    now = datetime.now(timezone.utc)
    now_text = now.replace(microsecond=0).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """SELECT id, book_id, actor_user_id FROM library_channel_queue
               WHERE status='queued' AND next_attempt_at<=? ORDER BY id LIMIT ?""",
            (now_text, limit),
        )
        rows = await cur.fetchall()
    if not rows:
        return 0

    sent = 0
    next_run = (now + timedelta(minutes=interval)).replace(microsecond=0).isoformat()
    for row in rows:
        queue_id, book_id = int(row["id"]), int(row["book_id"])
        actor_id = int(row["actor_user_id"]) if row["actor_user_id"] is not None else None
        try:
            result = await post_book_to_channel(bot, book_id, actor_user_id=actor_id, force=False)
            if result.channel_status in {"sent", "already_sent"}:
                async with connect() as db:
                    await db.execute(
                        "UPDATE library_channel_queue SET status='sent', sent_at=?, last_error=NULL WHERE id=?",
                        (utc_now(), queue_id),
                    )
                    await db.commit()
                sent += 1
            elif result.channel_status == "not_configured":
                async with connect() as db:
                    await db.execute(
                        "UPDATE library_channel_queue SET next_attempt_at=?, last_error=? WHERE id=?",
                        (next_run, "CHANNEL_ID не настроен", queue_id),
                    )
                    await db.commit()
            else:
                raise RuntimeError(result.channel_error or result.channel_status)
        except Exception as exc:
            async with connect() as db:
                cur = await db.execute("SELECT attempts FROM library_channel_queue WHERE id=?", (queue_id,))
                attempts = int((await cur.fetchone())[0] or 0) + 1
                status = "failed" if attempts >= 5 else "queued"
                await db.execute(
                    "UPDATE library_channel_queue SET status=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                    (status, attempts, next_run, str(exc)[:1000], queue_id),
                )
                await db.commit()

    # Все оставшиеся ожидающие карточки получают единый следующий слот, чтобы не было 500 постов сразу.
    async with connect() as db:
        await db.execute(
            "UPDATE library_channel_queue SET next_attempt_at=? WHERE status='queued' AND next_attempt_at<=?",
            (next_run, now_text),
        )
        await db.commit()
    return sent


async def library_channel_scheduler_loop(bot) -> None:
    while True:
        try:
            await process_library_channel_queue(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30)


async def list_batches(limit: int = 20):
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute("SELECT * FROM library_import_batches ORDER BY id DESC LIMIT ?", (int(limit),))
        return await cur.fetchall()


async def count_library_books(status: str | None = None) -> int:
    await ensure_library_schema()
    async with connect() as db:
        if status:
            cur = await db.execute("SELECT COUNT(*) FROM books WHERE import_batch_id IS NOT NULL AND publication_status=?", (status,))
        else:
            cur = await db.execute("SELECT COUNT(*) FROM books WHERE import_batch_id IS NOT NULL AND publication_status!='deleted'")
        row = await cur.fetchone()
        return int(row[0] or 0)


async def export_library_zip(output_path: str | Path) -> int:
    await ensure_library_schema()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="voxlyra_export_") as temp_name:
        root = Path(temp_name) / "Books"
        root.mkdir(parents=True, exist_ok=True)
        async with connect() as db:
            cur = await db.execute(
                """
                SELECT b.*, COALESCE(c.display_name, a.pen_name, b.source_author_name, 'Неизвестный автор') AS export_author,
                       rh.display_name AS export_rights_holder, rh.holder_type AS export_rights_holder_type,
                       br.revenue_mode AS export_revenue_mode,
                       (SELECT GROUP_CONCAT(option_label, '||') FROM book_option_values v WHERE v.book_id=b.id AND option_group='genres') AS genres,
                       (SELECT GROUP_CONCAT(option_label, '||') FROM book_option_values v WHERE v.book_id=b.id AND option_group='plot_tags') AS tags
                FROM books b
                LEFT JOIN author_profiles a ON a.id=b.author_id
                LEFT JOIN library_creators c ON c.id=b.creator_id
                LEFT JOIN book_rights br ON br.book_id=b.id
                LEFT JOIN library_rights_holders rh ON rh.id=br.rights_holder_id
                WHERE b.publication_status!='deleted' ORDER BY b.id
                """
            )
            books = await cur.fetchall()
        exported = 0
        for index, book in enumerate(books, 1):
            source = Path(str(book["source_file_name"] or ""))
            if not source.is_file():
                continue
            folder = root / f"{index:04d}_{_slug(str(book['title']))}"
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, folder / f"book{source.suffix.lower()}")
            cover = Path(str(book["cover_path"] or ""))
            if cover.is_file():
                shutil.copy2(cover, folder / f"cover{cover.suffix.lower()}")
            description = str(book["description"] or "")
            (folder / "description.txt").write_text(description, encoding="utf-8")
            metadata = {
                "voxlyra_book_id": int(book["id"]), "title": book["title"], "author": book["export_author"],
                "genre": str(book["genres"] or "").split("||") if book["genres"] else [],
                "tags": str(book["tags"] or "").split("||") if book["tags"] else [],
                "description": description, "language": book["source_language"] or "ru",
                "year": book["source_year"] or "", "license": book["license_type"] or "platform_original",
                "source": book["source_name"] or "", "rights_checked": bool(book["rights_checked"]),
                "rights_holder": book["export_rights_holder"] or "",
                "rights_holder_type": book["export_rights_holder_type"] or "other",
                "revenue_mode": book["export_revenue_mode"] or "none",
                "age_rating": book["age_limit"] or "12+",
                "free_or_paid": "free" if book["pricing_type"] == "free" else "paid",
                "price_stars": int(book["price_stars"] or 0),
            }
            (folder / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            exported += 1
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted((Path(temp_name)).rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(Path(temp_name)).as_posix())
    return exported


async def get_import_settings() -> dict[str, Any]:
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute("SELECT * FROM library_import_settings WHERE id=1")
        row = await cur.fetchone()
        return dict(row) if row else {
            "max_books": 0, "max_archive_mb": 200,
            "max_unpacked_mb": 4096, "duplicate_policy": "ask",
            "channel_auto_post": 1, "channel_interval_minutes": 60,
            "channel_posts_per_run": 5,
        }


async def update_import_settings(*, max_books: int | None = None, max_archive_mb: int | None = None,
                                 max_unpacked_mb: int | None = None, duplicate_policy: str | None = None,
                                 channel_auto_post: int | bool | None = None,
                                 channel_interval_minutes: int | None = None,
                                 channel_posts_per_run: int | None = None) -> dict[str, Any]:
    current = await get_import_settings()
    values = {
        "max_books": (lambda value: 0 if value <= 0 else min(100000, value))(int(max_books if max_books is not None else current["max_books"])),
        "max_archive_mb": max(10, min(2000, int(max_archive_mb if max_archive_mb is not None else current["max_archive_mb"]))),
        "max_unpacked_mb": max(100, min(20000, int(max_unpacked_mb if max_unpacked_mb is not None else current["max_unpacked_mb"]))),
        "duplicate_policy": str(duplicate_policy if duplicate_policy is not None else current["duplicate_policy"]),
        "channel_auto_post": int(bool(channel_auto_post if channel_auto_post is not None else current.get("channel_auto_post", 1))),
        "channel_interval_minutes": max(1, min(10080, int(channel_interval_minutes if channel_interval_minutes is not None else current.get("channel_interval_minutes", 60)))),
        "channel_posts_per_run": max(1, min(50, int(channel_posts_per_run if channel_posts_per_run is not None else current.get("channel_posts_per_run", 5)))),
    }
    if values["duplicate_policy"] not in {"ask", "skip", "replace"}:
        raise ValueError("Недопустимая политика дублей")
    async with connect() as db:
        await db.execute(
            """UPDATE library_import_settings SET max_books=?, max_archive_mb=?, max_unpacked_mb=?,
               duplicate_policy=?, channel_auto_post=?, channel_interval_minutes=?,
               channel_posts_per_run=?, updated_at=? WHERE id=1""",
            (values["max_books"], values["max_archive_mb"], values["max_unpacked_mb"],
             values["duplicate_policy"], values["channel_auto_post"],
             values["channel_interval_minutes"], values["channel_posts_per_run"], utc_now()),
        )
        if channel_interval_minutes is not None:
            next_run = (
                datetime.now(timezone.utc) + timedelta(minutes=values["channel_interval_minutes"])
            ).replace(microsecond=0).isoformat()
            await db.execute(
                "UPDATE library_channel_queue SET next_attempt_at=? WHERE status='queued'",
                (next_run,),
            )
        await db.commit()
    return values


async def list_imported_books(status: str | None = None, *, limit: int = 20, offset: int = 0):
    await ensure_library_schema()
    where = "b.import_batch_id IS NOT NULL AND b.publication_status!='deleted'"
    params: list[Any] = []
    if status:
        where += " AND b.publication_status=?"
        params.append(status)
    params.extend([int(limit), int(offset)])
    async with connect() as db:
        cur = await db.execute(
            f"""SELECT b.id, b.title, b.source_author_name, b.publication_status, b.import_batch_id,
                       COUNT(c.id) AS chapters_count
                FROM books b LEFT JOIN chapters c ON c.book_id=b.id AND c.status!='deleted'
                WHERE {where}
                GROUP BY b.id ORDER BY b.id DESC LIMIT ? OFFSET ?""", params,
        )
        return await cur.fetchall()


async def get_batch(batch_id: int):
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute("SELECT * FROM library_import_batches WHERE id=?", (int(batch_id),))
        return await cur.fetchone()


async def list_batch_duplicates(batch_id: int, *, pending_only: bool = True):
    await ensure_library_schema()
    async with connect() as db:
        sql = "SELECT * FROM library_import_duplicates WHERE batch_id=?"
        params: list[Any] = [int(batch_id)]
        if pending_only:
            sql += " AND status='pending'"
        sql += " ORDER BY id"
        cur = await db.execute(sql, params)
        return await cur.fetchall()



def _jsonable_row(row: Any) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def _write_replacement_backup(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temp_path, "wt", encoding="utf-8", compresslevel=6) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), default=str)
    temp_path.replace(path)


def _read_replacement_backup(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Повреждён резерв заменяемой книги")
    return payload


async def _snapshot_replacement_before_update(
    db,
    *,
    batch_id: int,
    book_id: int,
    new_storage_path: Path,
) -> bool:
    """Сохраняет исходное состояние заменяемой книги до первого изменения в пакете."""
    cur = await db.execute(
        "SELECT backup_path FROM library_import_replacement_backups WHERE batch_id=? AND book_id=?",
        (int(batch_id), int(book_id)),
    )
    if await cur.fetchone() is not None:
        return True

    cur = await db.execute("SELECT * FROM books WHERE id=?", (int(book_id),))
    book = await cur.fetchone()
    if book is None:
        return False
    cur = await db.execute("SELECT * FROM chapters WHERE book_id=? ORDER BY id", (int(book_id),))
    chapters = [_jsonable_row(row) for row in await cur.fetchall()]
    cur = await db.execute("SELECT * FROM book_rights WHERE book_id=?", (int(book_id),))
    rights = await cur.fetchone()
    cur = await db.execute("SELECT * FROM book_option_values WHERE book_id=? ORDER BY id", (int(book_id),))
    options = [_jsonable_row(row) for row in await cur.fetchall()]
    chapter_ids = [int(row["id"]) for row in chapters]
    audio: list[dict[str, Any]] = []
    if chapter_ids:
        placeholders = ",".join("?" for _ in chapter_ids)
        cur = await db.execute(
            f"SELECT id, status, updated_at FROM audio_chapters WHERE chapter_id IN ({placeholders}) ORDER BY id",
            chapter_ids,
        )
        audio = [_jsonable_row(row) for row in await cur.fetchall()]

    old_storage: list[str] = []
    for value in (book["source_file_name"], book["cover_path"]):
        if value:
            candidate = Path(str(value)).parent
            if str(candidate) not in old_storage:
                old_storage.append(str(candidate))

    backup_path = DEFAULT_STORAGE_ROOT / "replacement_backups" / str(int(batch_id)) / f"{int(book_id)}.json.gz"
    payload = {
        "book": _jsonable_row(book),
        "chapters": chapters,
        "rights": _jsonable_row(rights) if rights is not None else None,
        "options": options,
        "audio": audio,
    }
    await _run_blocking(_write_replacement_backup, backup_path, payload)
    await db.execute(
        """INSERT INTO library_import_replacement_backups(
               batch_id, book_id, backup_path, old_storage_json, new_storage_path, created_at
           ) VALUES(?, ?, ?, ?, ?, ?)""",
        (
            int(batch_id),
            int(book_id),
            str(backup_path),
            json.dumps(old_storage, ensure_ascii=False),
            str(new_storage_path),
            utc_now(),
        ),
    )
    return True


async def _restore_table_row(db, table: str, row: dict[str, Any], *, primary_key: str = "id") -> None:
    columns = [key for key in row.keys() if key != primary_key]
    if not columns:
        return
    assignments = ", ".join(f"{key}=?" for key in columns)
    await db.execute(
        f"UPDATE {table} SET {assignments} WHERE {primary_key}=?",
        tuple(row[key] for key in columns) + (row[primary_key],),
    )


async def restore_import_replacement_backups(batch_id: int) -> int:
    """Возвращает заменённые книги в состояние до незавершённого импорта."""
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM library_import_replacement_backups WHERE batch_id=? ORDER BY book_id",
            (int(batch_id),),
        )
        rows = await cur.fetchall()

    restored = 0
    cleanup_paths: set[Path] = set()
    for backup in rows:
        backup_path = Path(str(backup["backup_path"] or ""))
        if not backup_path.is_file():
            continue
        payload = await _run_blocking(_read_replacement_backup, backup_path)
        book = payload.get("book") or {}
        chapters = payload.get("chapters") or []
        rights = payload.get("rights")
        options = payload.get("options") or []
        audio = payload.get("audio") or []
        book_id = int(backup["book_id"])
        chapter_ids = [int(row["id"]) for row in chapters if isinstance(row, dict) and row.get("id") is not None]

        async with connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            if chapter_ids:
                placeholders = ",".join("?" for _ in chapter_ids)
                await db.execute(
                    f"DELETE FROM chapters WHERE book_id=? AND id NOT IN ({placeholders})",
                    (book_id, *chapter_ids),
                )
            else:
                await db.execute("DELETE FROM chapters WHERE book_id=?", (book_id,))
            for chapter in chapters:
                if isinstance(chapter, dict):
                    await _restore_table_row(db, "chapters", chapter)
            if isinstance(book, dict) and book:
                await _restore_table_row(db, "books", book)
            await db.execute("DELETE FROM book_option_values WHERE book_id=?", (book_id,))
            for option in options:
                if not isinstance(option, dict):
                    continue
                columns = [key for key in option.keys() if key != "id"]
                placeholders = ",".join("?" for _ in columns)
                await db.execute(
                    f"INSERT INTO book_option_values({','.join(columns)}) VALUES({placeholders})",
                    tuple(option[key] for key in columns),
                )
            if rights is None:
                await db.execute("DELETE FROM book_rights WHERE book_id=?", (book_id,))
            elif isinstance(rights, dict):
                columns = list(rights.keys())
                placeholders = ",".join("?" for _ in columns)
                await db.execute(
                    f"INSERT OR REPLACE INTO book_rights({','.join(columns)}) VALUES({placeholders})",
                    tuple(rights[key] for key in columns),
                )
            for item in audio:
                if isinstance(item, dict) and item.get("id") is not None:
                    await db.execute(
                        "UPDATE audio_chapters SET status=?, updated_at=? WHERE id=?",
                        (item.get("status"), item.get("updated_at"), int(item["id"])),
                    )
            await db.execute(
                "DELETE FROM library_import_replacement_backups WHERE batch_id=? AND book_id=?",
                (int(batch_id), book_id),
            )
            await db.commit()
        new_storage = Path(str(backup["new_storage_path"] or ""))
        if new_storage.exists():
            cleanup_paths.add(new_storage)
        cleanup_paths.add(backup_path)
        restored += 1

    for path in sorted(cleanup_paths, key=lambda item: len(str(item)), reverse=True):
        try:
            if path.is_dir() and DEFAULT_STORAGE_ROOT.resolve() in path.resolve().parents:
                await asyncio.to_thread(shutil.rmtree, path, True)
            elif path.is_file() and DEFAULT_STORAGE_ROOT.resolve() in path.resolve().parents:
                await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            continue
    backup_root = DEFAULT_STORAGE_ROOT / "replacement_backups" / str(int(batch_id))
    try:
        if backup_root.is_dir():
            await asyncio.to_thread(shutil.rmtree, backup_root, True)
    except OSError:
        pass
    return restored


async def finalize_import_replacement_backups(batch_id: int) -> int:
    """Удаляет старые файлы после окончательного успешного завершения задания."""
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM library_import_replacement_backups WHERE batch_id=?",
            (int(batch_id),),
        )
        rows = await cur.fetchall()
        await db.execute(
            "DELETE FROM library_import_replacement_backups WHERE batch_id=?",
            (int(batch_id),),
        )
        await db.commit()
    cleanup_paths: set[Path] = set()
    for row in rows:
        try:
            old_storage = json.loads(str(row["old_storage_json"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            old_storage = []
        for value in old_storage if isinstance(old_storage, list) else []:
            path = Path(str(value))
            new_path = Path(str(row["new_storage_path"] or ""))
            try:
                if path.resolve() != new_path.resolve():
                    cleanup_paths.add(path)
            except OSError:
                continue
        backup_path = Path(str(row["backup_path"] or ""))
        cleanup_paths.add(backup_path)
    for path in sorted(cleanup_paths, key=lambda item: len(str(item)), reverse=True):
        try:
            if path.is_dir() and DEFAULT_STORAGE_ROOT.resolve() in path.resolve().parents:
                await asyncio.to_thread(shutil.rmtree, path, True)
            elif path.is_file() and DEFAULT_STORAGE_ROOT.resolve() in path.resolve().parents:
                await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            continue
    backup_root = DEFAULT_STORAGE_ROOT / "replacement_backups" / str(int(batch_id))
    try:
        if backup_root.is_dir():
            await asyncio.to_thread(shutil.rmtree, backup_root, True)
    except OSError:
        pass
    return len(rows)


async def _replace_book_from_candidate(
    existing_book_id: int,
    candidate_dir: Path,
    metadata: dict[str, Any],
    file_hash: str,
    *,
    batch_id: int | None = None,
    actor_user_id: int = 0,
) -> None:
    """Заменяет содержимое существующей книги, сохраняя ID книги и глав.

    Совпадающие номера глав обновляются на месте, поэтому прогресс чтения,
    покупки и связанные записи не теряют ссылки. Удалённые из новой версии
    главы переводятся в статус deleted, а новые получают новые ID.
    """
    files = [p for p in candidate_dir.iterdir() if p.is_file()]
    book_files = [p for p in files if p.suffix.lower() in BOOK_EXTENSIONS]
    cover_files = [
        p for p in files
        if p.suffix.lower() in COVER_EXTENSIONS and p.stem.lower().startswith("cover")
    ]
    if not book_files or not cover_files:
        raise ValueError("В кандидате отсутствует книга или обложка")
    book_path = sorted(book_files, key=lambda p: (p.suffix.lower() != ".epub", p.name.lower()))[0]
    cover_path = sorted(cover_files, key=lambda p: p.name.lower())[0]
    chapters = parse_book_file(
        book_path,
        original_filename=book_path.name,
        temp_dir=candidate_dir / "_parse",
    )
    chapters = [ch for ch in chapters if (ch.text or "").strip()]
    if not chapters:
        raise ValueError("В книге не найден текст")

    title = str(metadata.get("title") or "").strip()
    author = str(metadata.get("author") or "").strip()
    if not title or not author:
        raise ValueError("В изменённой книге не указано название или автор")
    genres = metadata.get("genre") or []
    if isinstance(genres, str):
        genres = [genres]
    tags = metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    description_path = candidate_dir / "description.txt"
    description = str(metadata.get("description") or "").strip()
    if not description and description_path.exists():
        description = _read_text(description_path)

    now = utc_now()
    storage_batch = str(int(batch_id)) if batch_id is not None else "manual_replacements"
    final_dir = DEFAULT_STORAGE_ROOT / "books" / storage_batch / f"replacement_{int(existing_book_id)}"
    stored_book = final_dir / f"book{book_path.suffix.lower()}"
    stored_cover = final_dir / f"cover{cover_path.suffix.lower()}"

    def store_replacement_files() -> None:
        if final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        final_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(book_path, stored_book)
        shutil.copy2(cover_path, stored_cover)
        (final_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (final_dir / "description.txt").write_text(description, encoding="utf-8")

    await _run_blocking(store_replacement_files)

    pricing = str(metadata.get("free_or_paid") or "free").strip().lower()
    price_stars = max(0, int(metadata.get("price_stars") or 0))
    pricing_type = "whole_book" if pricing in {"paid", "whole_book"} and price_stars > 0 else "free"
    normalized_title = _normalize_work_title(title)
    license_type = str(metadata.get("license") or "platform_original")
    source_name = str(metadata.get("source") or "").strip()
    rights_checked = 1 if metadata.get("rights_checked") is True else 0
    previous_storage_dirs: set[Path] = set()
    protect_replacement = False

    async with connect() as db:
        if batch_id is not None:
            cur = await db.execute(
                "SELECT status FROM library_import_batches WHERE id=?",
                (int(batch_id),),
            )
            batch_row = await cur.fetchone()
            protect_replacement = bool(batch_row and str(batch_row["status"]) == "processing")
            if protect_replacement:
                await _snapshot_replacement_before_update(
                    db,
                    batch_id=int(batch_id),
                    book_id=int(existing_book_id),
                    new_storage_path=final_dir,
                )
        cur = await db.execute(
            "SELECT source_file_name, cover_path FROM books WHERE id=?",
            (int(existing_book_id),),
        )
        previous_book = await cur.fetchone()
        if previous_book:
            for value in (previous_book["source_file_name"], previous_book["cover_path"]):
                if value:
                    previous_storage_dirs.add(Path(str(value)).parent)
        creator_id, rights_holder_id, revenue_mode, revenue_author_id = await _ensure_creator_and_rights(
            db,
            author_name=author,
            metadata=metadata,
            license_type=license_type,
            source_name=source_name,
            actor_user_id=actor_user_id,
            now=now,
        )
        await db.execute(
            """UPDATE books SET
                   title=?, description=?, age_limit=?, writing_status='finished',
                   publication_status='draft', cover_path=?, normalized_title=?,
                   source_file_hash=?, source_file_name=?, duplicate_override=0,
                   allow_download=1, pricing_type=?, price_stars=?, license_type=?,
                   source_name=?, rights_checked=?, import_batch_id=?, import_file_hash=?,
                   source_author_name=?, source_year=?, source_language=?, creator_id=?,
                   rights_holder_id=?, revenue_mode=?, import_was_replacement=1, updated_at=?
               WHERE id=?""",
            (
                title,
                description,
                str(metadata.get("age_rating") or "12+"),
                str(stored_cover),
                normalized_title,
                file_hash,
                str(stored_book),
                pricing_type,
                price_stars,
                license_type,
                source_name,
                rights_checked,
                batch_id,
                file_hash,
                author,
                str(metadata.get("year") or "").strip(),
                str(metadata.get("language") or "ru").strip(),
                creator_id,
                rights_holder_id,
                revenue_mode,
                now,
                int(existing_book_id),
            ),
        )
        await db.execute(
            """INSERT INTO book_rights(
                   book_id, creator_id, rights_holder_id, license_type, revenue_mode,
                   revenue_author_id, imported_by_user_id, source_name, rights_checked,
                   created_at, updated_at
               ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(book_id) DO UPDATE SET
                   creator_id=excluded.creator_id,
                   rights_holder_id=excluded.rights_holder_id,
                   license_type=excluded.license_type,
                   revenue_mode=excluded.revenue_mode,
                   revenue_author_id=excluded.revenue_author_id,
                   imported_by_user_id=excluded.imported_by_user_id,
                   source_name=excluded.source_name,
                   rights_checked=excluded.rights_checked,
                   updated_at=excluded.updated_at""",
            (
                int(existing_book_id),
                creator_id,
                rights_holder_id,
                license_type,
                revenue_mode,
                revenue_author_id,
                actor_user_id or None,
                source_name,
                rights_checked,
                now,
                now,
            ),
        )

        cur = await db.execute(
            "SELECT id, number FROM chapters WHERE book_id=? ORDER BY number",
            (int(existing_book_id),),
        )
        existing_chapters = {int(row["number"]): int(row["id"]) for row in await cur.fetchall()}
        incoming_numbers: set[int] = set()
        for chapter in chapters:
            number = int(chapter.number)
            incoming_numbers.add(number)
            is_free = 1 if pricing_type == "free" else 0
            chapter_id = existing_chapters.get(number)
            if chapter_id is not None:
                await db.execute(
                    """UPDATE chapters SET title=?, text=?, is_free=?, price_stars=0,
                           status='draft', updated_at=? WHERE id=?""",
                    (str(chapter.title)[:160], chapter.text, is_free, now, chapter_id),
                )
                await db.execute(
                    "UPDATE audio_chapters SET status='draft', updated_at=? WHERE chapter_id=?",
                    (now, chapter_id),
                )
            else:
                await db.execute(
                    """INSERT INTO chapters(
                           book_id, number, title, text, is_free, price_stars, status, created_at, updated_at
                       ) VALUES(?, ?, ?, ?, ?, 0, 'draft', ?, ?)""",
                    (
                        int(existing_book_id),
                        number,
                        str(chapter.title)[:160],
                        chapter.text,
                        is_free,
                        now,
                        now,
                    ),
                )

        removed_ids = [
            chapter_id for number, chapter_id in existing_chapters.items()
            if number not in incoming_numbers
        ]
        if removed_ids:
            placeholders = ",".join("?" for _ in removed_ids)
            await db.execute(
                f"UPDATE chapters SET status='deleted', updated_at=? WHERE id IN ({placeholders})",
                (now, *removed_ids),
            )
            await db.execute(
                f"UPDATE audio_chapters SET status='deleted', updated_at=? WHERE chapter_id IN ({placeholders})",
                (now, *removed_ids),
            )

        await db.execute(
            "DELETE FROM book_option_values WHERE book_id=? AND option_group IN ('genres','plot_tags')",
            (int(existing_book_id),),
        )
        for group, values in (("genres", genres), ("plot_tags", tags)):
            for value in values:
                label = str(value).strip()
                if label:
                    await db.execute(
                        """INSERT OR IGNORE INTO book_option_values(
                               book_id, option_group, option_code, option_label, created_at
                           ) VALUES(?, ?, ?, ?, ?)""",
                        (int(existing_book_id), group, _slug(label.casefold()), label, now),
                    )
        await db.commit()
    if not protect_replacement:
        for old_dir in previous_storage_dirs:
            try:
                if old_dir.resolve() == final_dir.resolve():
                    continue
                if DEFAULT_STORAGE_ROOT.resolve() in old_dir.resolve().parents:
                    shutil.rmtree(old_dir, ignore_errors=True)
            except OSError:
                continue


async def resolve_duplicate(duplicate_id: int, action: str) -> dict[str, Any]:
    if action not in {"skip", "replace"}:
        raise ValueError("Неизвестное действие")
    await ensure_library_schema()
    async with connect() as db:
        cur = await db.execute("SELECT * FROM library_import_duplicates WHERE id=?", (int(duplicate_id),))
        row = await cur.fetchone()
    if not row:
        raise ValueError("Дубль не найден")
    if row["status"] != "pending":
        return {"status": row["status"], "book_id": int(row["existing_book_id"])}
    candidate_dir = Path(str(row["candidate_dir"]))
    if action == "replace":
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        async with connect() as db:
            cur = await db.execute(
                "SELECT imported_by_user_id FROM library_import_batches WHERE id=?",
                (int(row["batch_id"]),),
            )
            batch = await cur.fetchone()
        actor_user_id = int(batch["imported_by_user_id"] or 0) if batch else 0
        await _replace_book_from_candidate(
            int(row["existing_book_id"]),
            candidate_dir,
            metadata,
            str(row["file_hash"]),
            batch_id=int(row["batch_id"]),
            actor_user_id=actor_user_id,
        )
    async with connect() as db:
        await db.execute(
            "UPDATE library_import_duplicates SET status='resolved', resolution=?, resolved_at=? WHERE id=?",
            (action, utc_now(), int(duplicate_id)),
        )
        await db.commit()
    if candidate_dir.exists():
        shutil.rmtree(candidate_dir, ignore_errors=True)
    return {"status": "resolved", "action": action, "book_id": int(row["existing_book_id"])}


async def build_batch_report(batch_id: int, output_path: str | Path) -> dict[str, int]:
    await ensure_library_schema()
    output_path = Path(output_path)
    batch = await get_batch(batch_id)
    if not batch:
        raise ValueError("Пакет не найден")
    async with connect() as db:
        cur = await db.execute(
            """SELECT b.id, b.title, b.source_author_name, b.publication_status,
                      (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status!='deleted') AS chapters_count
               FROM books b WHERE b.import_batch_id=? ORDER BY b.id""",
            (int(batch_id),),
        )
        books = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            "SELECT id, title, author, existing_book_id, status, resolution FROM library_import_duplicates WHERE batch_id=? ORDER BY id",
            (int(batch_id),),
        )
        duplicates = [dict(row) for row in await cur.fetchall()]
    errors = json.loads(str(batch["errors_json"] or "[]"))
    report = {
        "batch": {key: batch[key] for key in batch.keys()},
        "books": books,
        "duplicates": duplicates,
        "errors": errors,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"books": len(books), "duplicates": len(duplicates), "errors": len(errors)}


async def rollback_batch_drafts(batch_id: int) -> dict[str, int]:
    await ensure_library_schema()
    removed_books = 0
    removed_chapters = 0
    storage_paths: list[Path] = []
    async with connect() as db:
        cur = await db.execute(
            """SELECT id, source_file_name, cover_path FROM books
               WHERE import_batch_id=? AND publication_status='draft'
                 AND COALESCE(import_was_replacement, 0)=0""",
            (int(batch_id),),
        )
        rows = await cur.fetchall()
        for row in rows:
            book_id = int(row["id"])
            cur2 = await db.execute("SELECT COUNT(*) FROM chapters WHERE book_id=?", (book_id,))
            count_row = await cur2.fetchone()
            removed_chapters += int(count_row[0] or 0)
            for value in (row["source_file_name"], row["cover_path"]):
                if value:
                    path = Path(str(value))
                    if path.exists():
                        storage_paths.append(path.parent)
            await db.execute("DELETE FROM books WHERE id=?", (book_id,))
            removed_books += 1
        await db.execute(
            "UPDATE library_import_batches SET status=CASE WHEN imported_count>0 THEN 'rolled_back' ELSE status END WHERE id=?",
            (int(batch_id),),
        )
        await db.commit()
    for path in sorted(set(storage_paths), key=lambda p: len(str(p)), reverse=True):
        if path.exists() and DEFAULT_STORAGE_ROOT in path.parents:
            shutil.rmtree(path, ignore_errors=True)
    return {"books": removed_books, "chapters": removed_chapters}
