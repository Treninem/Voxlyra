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
DATABASE = ROOT / os.environ.get("DATABASE_PATH", "data/voxlyra.sqlite3")
CHUNK_ROOT = ROOT / "storage" / "temp" / "chunked_book_uploads"
IMPORT_WORK_ROOT = ROOT / "storage" / "library" / "import_work"
QUEUE_UPLOAD_ROOT = ROOT / "storage" / "library" / "import_queue" / "uploads"


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
    for folder in list(CHUNK_ROOT.iterdir()):
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
        return {str(Path(str(row[0])).resolve()) for row in rows if row and row[0]}
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
    stale_chunks, preserved_chunks = cleanup_library_upload_sessions()
    work = remove_children(IMPORT_WORK_ROOT)
    orphans = remove_orphan_queue_archives()
    system_temp = remove_old_system_temp()
    print(
        "Import startup cleanup: "
        f"stale_upload_sessions={stale_chunks} preserved_upload_sessions={preserved_chunks} "
        f"work_dirs={work} orphan_archives={orphans} system_temp={system_temp}",
        flush=True,
    )


if __name__ == "__main__":
    main()
