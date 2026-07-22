#!/usr/bin/env python3
"""Remove only stale/broken import artifacts before VoxLyra starts.

Fresh library chunk sessions are intentionally preserved so an upload can
continue after Bothost Redeploy. Queue archives referenced by active or retained
jobs are also kept.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()


def _configured_path(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default) or default)
    return value if value.is_absolute() else ROOT / value


DATABASE = _configured_path("DATABASE_PATH", "data/voxlyra.sqlite3")
CHUNK_ROOT = _configured_path("CHUNK_UPLOAD_ROOT", "data/chunked_uploads")
QUEUE_ROOT = _configured_path("LIBRARY_IMPORT_QUEUE_ROOT", "data/library_import_queue")
QUEUE_UPLOAD_ROOT = QUEUE_ROOT / "uploads"
LEGACY_CHUNK_ROOT = ROOT / "storage" / "temp" / "chunked_book_uploads"
LEGACY_QUEUE_UPLOAD_ROOT = ROOT / "storage" / "library" / "import_queue" / "uploads"
IMPORT_WORK_ROOT = ROOT / "storage" / "library" / "import_work"


def migrate_legacy_storage() -> tuple[int, int]:
    """Move still-present old import data into the DB-adjacent persistent roots."""
    CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
    QUEUE_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    moved_sessions = 0
    moved_archives = 0

    try:
        same_chunks = LEGACY_CHUNK_ROOT.resolve() == CHUNK_ROOT.resolve()
    except OSError:
        same_chunks = False
    if not same_chunks and LEGACY_CHUNK_ROOT.is_dir():
        for source in list(LEGACY_CHUNK_ROOT.iterdir()):
            if not source.is_dir():
                continue
            target = CHUNK_ROOT / source.name
            if target.exists():
                continue
            try:
                os.replace(source, target)
                moved_sessions += 1
            except OSError:
                try:
                    shutil.copytree(source, target)
                    shutil.rmtree(source, ignore_errors=True)
                    moved_sessions += 1
                except OSError:
                    shutil.rmtree(target, ignore_errors=True)

    try:
        same_queue = LEGACY_QUEUE_UPLOAD_ROOT.resolve() == QUEUE_UPLOAD_ROOT.resolve()
    except OSError:
        same_queue = False
    moved_by_name: dict[str, Path] = {}
    if not same_queue and LEGACY_QUEUE_UPLOAD_ROOT.is_dir():
        for source in list(LEGACY_QUEUE_UPLOAD_ROOT.glob("*.zip")):
            target = QUEUE_UPLOAD_ROOT / source.name
            if target.exists():
                moved_by_name[source.name] = target
                continue
            try:
                os.replace(source, target)
                moved_archives += 1
                moved_by_name[source.name] = target
            except OSError:
                try:
                    shutil.copy2(source, target)
                    source.unlink(missing_ok=True)
                    moved_archives += 1
                    moved_by_name[source.name] = target
                except OSError:
                    target.unlink(missing_ok=True)

    if DATABASE.is_file() and moved_by_name:
        try:
            with sqlite3.connect(DATABASE, timeout=3) as db:
                rows = db.execute(
                    "SELECT id, archive_path FROM library_import_jobs "
                    "WHERE status IN ('queued','processing','cancelling','failed','cancelled')"
                ).fetchall()
                for job_id, raw_path in rows:
                    name = Path(str(raw_path or "")).name
                    target = moved_by_name.get(name)
                    if target is None or not target.is_file():
                        continue
                    try:
                        portable = str(target.resolve().relative_to(ROOT.resolve()))
                    except (OSError, ValueError):
                        portable = str(target)
                    db.execute(
                        "UPDATE library_import_jobs SET archive_path=? WHERE id=?",
                        (portable, int(job_id)),
                    )
                db.commit()
        except sqlite3.Error:
            pass
    return moved_sessions, moved_archives


def _retention_seconds() -> int:
    try:
        hours = int(os.environ.get("LIBRARY_IMPORT_FAILED_ARCHIVE_HOURS", "24") or "24")
    except ValueError:
        hours = 24
    return max(1, min(168, hours)) * 60 * 60


def _timestamp_from_meta(meta: dict, folder: Path) -> float:
    raw = str(meta.get("updated_at") or meta.get("created_at") or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            pass
    try:
        return folder.stat().st_mtime
    except OSError:
        return 0.0


def remove_children(root: Path) -> int:
    """Remove disposable import work directories; queued source ZIPs stay intact."""
    removed = 0
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        return removed
    for child in list(root.iterdir()):
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    root.mkdir(parents=True, exist_ok=True)
    return removed


def cleanup_library_upload_sessions() -> tuple[int, int]:
    """Keep resumable library sessions and remove only stale/broken folders."""
    removed = 0
    preserved = 0
    now = time.time()
    retention = _retention_seconds()
    CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
    for item in list(CHUNK_ROOT.iterdir()):
        if item.is_file():
            try:
                if now - item.stat().st_mtime >= retention:
                    item.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
            continue
        folder = item
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError("meta is not an object")
        except Exception:
            # A malformed session cannot be resumed. Give very fresh filesystem
            # writes a short grace period, then remove them safely.
            try:
                age = now - folder.stat().st_mtime
            except OSError:
                age = retention + 1
            if age < 5 * 60:
                preserved += 1
                continue
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
            continue

        if str(meta.get("kind") or "") != "library_import":
            # Author/book/comic uploads use the same root and are never removed by
            # this library-specific startup cleanup.
            preserved += 1
            continue

        age = now - _timestamp_from_meta(meta, folder)
        if age < retention:
            preserved += 1
            continue
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    return removed, preserved


def active_queue_archives() -> set[str] | None:
    if not DATABASE.is_file():
        return set()
    try:
        with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=2) as db:
            # Keep active jobs and failed/cancelled archives whose retention period
            # has not expired yet. Older schemas may not have archive_expires_at.
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(library_import_jobs)")}
            if "archive_expires_at" in columns:
                rows = db.execute(
                    "SELECT archive_path FROM library_import_jobs "
                    "WHERE status IN ('queued','processing','cancelling') "
                    "OR (status IN ('failed','cancelled') AND archive_expires_at>datetime('now'))"
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT archive_path FROM library_import_jobs "
                    "WHERE status IN ('queued','processing','cancelling')"
                ).fetchall()
        result: set[str] = set()
        for row in rows:
            if not row or not row[0]:
                continue
            raw = Path(str(row[0]))
            candidates = (raw, QUEUE_UPLOAD_ROOT / raw.name, LEGACY_QUEUE_UPLOAD_ROOT / raw.name)
            for candidate in candidates:
                if candidate.is_file():
                    result.add(str(candidate.resolve()))
                    break
        return result
    except (sqlite3.Error, OSError):
        return None


def remove_orphan_queue_archives() -> int:
    QUEUE_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    keep = active_queue_archives()
    if keep is None:
        return 0
    removed = 0
    now = time.time()
    retention = _retention_seconds()
    for partial in QUEUE_UPLOAD_ROOT.glob("*.partial"):
        try:
            if now - partial.stat().st_mtime >= min(retention, 10 * 60):
                partial.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
    for path in QUEUE_UPLOAD_ROOT.glob("*.zip"):
        try:
            if str(path.resolve()) in keep:
                continue
            # Do not delete a just-created archive if the database transaction has
            # not become visible yet during a rolling deployment.
            if now - path.stat().st_mtime < min(retention, 10 * 60):
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


def remove_old_system_temp() -> int:
    removed = 0
    now = time.time()
    retention = _retention_seconds()
    temp_root = Path(tempfile.gettempdir())
    for pattern in ("voxlyra_library_*", "voxlyra_book_*"):
        for folder in temp_root.glob(pattern):
            if not folder.is_dir():
                continue
            try:
                if now - folder.stat().st_mtime < retention:
                    continue
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
            except OSError:
                pass
    return removed


def main() -> None:
    migrated_sessions, migrated_archives = migrate_legacy_storage()
    stale_chunks, preserved_chunks = cleanup_library_upload_sessions()
    work = remove_children(IMPORT_WORK_ROOT)
    orphans = remove_orphan_queue_archives()
    system_temp = remove_old_system_temp()
    print(
        "Import startup cleanup: "
        f"migrated_upload_sessions={migrated_sessions} migrated_queue_archives={migrated_archives} "
        f"stale_upload_sessions={stale_chunks} preserved_upload_sessions={preserved_chunks} "
        f"work_dirs={work} orphan_archives={orphans} system_temp={system_temp}",
        flush=True,
    )


if __name__ == "__main__":
    main()
