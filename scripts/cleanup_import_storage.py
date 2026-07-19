#!/usr/bin/env python3
"""Free only abandoned import upload/work files before VoxLyra starts."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path.cwd()
DATABASE = ROOT / os.environ.get("DATABASE_PATH", "data/voxlyra.sqlite3")
CHUNK_ROOT = ROOT / "storage" / "temp" / "chunked_book_uploads"
IMPORT_WORK_ROOT = ROOT / "storage" / "library" / "import_work"
QUEUE_UPLOAD_ROOT = ROOT / "storage" / "library" / "import_queue" / "uploads"


def remove_children(root: Path) -> int:
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


def remove_library_upload_sessions() -> int:
    """Delete abandoned library ZIP sessions without touching author uploads."""
    removed = 0
    CHUNK_ROOT.mkdir(parents=True, exist_ok=True)
    for folder in list(CHUNK_ROOT.iterdir()):
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.json"
        kind = ""
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            kind = str(meta.get("kind") or "")
        except Exception:
            # A folder without readable metadata cannot be resumed safely.
            kind = "broken"
        if kind not in {"library_import", "broken"}:
            continue
        try:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
        except OSError:
            pass
    return removed


def active_queue_archives() -> set[str] | None:
    if not DATABASE.is_file():
        return set()
    try:
        with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute(
                "SELECT archive_path FROM library_import_jobs "
                "WHERE status IN ('queued','processing')"
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
    for path in QUEUE_UPLOAD_ROOT.glob("*.zip"):
        try:
            if str(path.resolve()) in keep:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


def remove_old_system_temp() -> int:
    removed = 0
    temp_root = Path(tempfile.gettempdir())
    for pattern in ("voxlyra_library_*", "voxlyra_book_*"):
        for folder in temp_root.glob(pattern):
            if not folder.is_dir():
                continue
            try:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
            except OSError:
                pass
    return removed


def main() -> None:
    chunks = remove_library_upload_sessions()
    work = remove_children(IMPORT_WORK_ROOT)
    orphans = remove_orphan_queue_archives()
    system_temp = remove_old_system_temp()
    print(
        "Import startup cleanup: "
        f"upload_sessions={chunks} work_dirs={work} "
        f"orphan_archives={orphans} system_temp={system_temp}",
        flush=True,
    )


if __name__ == "__main__":
    main()
