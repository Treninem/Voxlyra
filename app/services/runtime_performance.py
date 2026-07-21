from __future__ import annotations

import asyncio
import logging
import os
import resource
import shutil
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import connect
from app.services.chunked_upload import UPLOAD_ROOT, active_upload_count, cleanup_stale_uploads
from app.services.library_manager import DEFAULT_STORAGE_ROOT, cleanup_stale_import_work
from app.services.reader_tts import cleanup_tts_cache
from app.services.runtime_state import bot_runtime_snapshot

logger = logging.getLogger(__name__)


def _safe_disk_usage(path: Path) -> dict[str, int]:
    target = path
    while not target.exists() and target.parent != target:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
        return {"total": int(usage.total), "used": int(usage.used), "free": int(usage.free)}
    except OSError:
        return {"total": 0, "used": 0, "free": 0}


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size) if path.is_file() else 0
    except OSError:
        return 0


def _process_rss_bytes() -> int:
    try:
        # Linux reports KiB, macOS bytes. Bothost is Linux.
        raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return raw * 1024 if os.name == "posix" else raw
    except Exception:
        return 0


async def runtime_performance_report(*, deep_check: bool = True) -> dict[str, Any]:
    """Return owner-safe aggregate runtime metrics without secrets or user data."""
    db_path = Path(settings.DATABASE_PATH)
    disk = _safe_disk_usage(db_path.parent if db_path.parent != Path("") else Path("."))
    storage_disk = _safe_disk_usage(DEFAULT_STORAGE_ROOT)
    minimum_free = max(32, int(settings.LIBRARY_IMPORT_MIN_FREE_DISK_MB or 256)) * 1024 * 1024

    database_ok = False
    journal_mode = "unknown"
    busy_timeout = 0
    cache_pages = 0
    page_count = 0
    freelist_count = 0
    page_size = 0
    quick_check = "error"
    queue_counts: dict[str, int] = {}
    try:
        async with connect() as db:
            cur = await db.execute("SELECT 1")
            database_ok = int((await cur.fetchone())[0]) == 1
            cur = await db.execute("PRAGMA journal_mode")
            journal_mode = str((await cur.fetchone())[0] or "unknown")
            cur = await db.execute("PRAGMA busy_timeout")
            busy_timeout = int((await cur.fetchone())[0] or 0)
            cur = await db.execute("PRAGMA cache_size")
            cache_pages = int((await cur.fetchone())[0] or 0)
            cur = await db.execute("PRAGMA page_count")
            page_count = int((await cur.fetchone())[0] or 0)
            cur = await db.execute("PRAGMA freelist_count")
            freelist_count = int((await cur.fetchone())[0] or 0)
            cur = await db.execute("PRAGMA page_size")
            page_size = int((await cur.fetchone())[0] or 0)
            if deep_check:
                cur = await db.execute("PRAGMA quick_check(1)")
                quick_check = str((await cur.fetchone())[0] or "error")
            else:
                quick_check = "skipped"
            try:
                cur = await db.execute(
                    """SELECT status, COUNT(*) AS count FROM library_import_jobs
                       GROUP BY status"""
                )
                queue_counts = {str(row["status"]): int(row["count"] or 0) for row in await cur.fetchall()}
            except Exception:
                queue_counts = {}
    except Exception as exc:
        logger.warning("Runtime performance probe failed: %s", exc)

    free_bytes = min(
        value for value in (disk.get("free", 0), storage_disk.get("free", 0)) if value > 0
    ) if any(value > 0 for value in (disk.get("free", 0), storage_disk.get("free", 0))) else 0
    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")
    return {
        "ok": bool(database_ok and quick_check in {"ok", "skipped"} and (free_bytes == 0 or free_bytes >= minimum_free)),
        "database": {
            "ok": database_ok,
            "quick_check": quick_check,
            "journal_mode": journal_mode,
            "busy_timeout_ms": busy_timeout,
            "cache_pages": cache_pages,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "page_size": page_size,
            "database_bytes": _file_size(db_path),
            "wal_bytes": _file_size(wal_path),
            "shm_bytes": _file_size(shm_path),
        },
        "disk": {
            "database": disk,
            "storage": storage_disk,
            "minimum_free_bytes": minimum_free,
        },
        "process": {"peak_rss_bytes": _process_rss_bytes()},
        "uploads": {
            "active": await asyncio.to_thread(active_upload_count),
            "maximum": max(1, int(settings.LIBRARY_IMPORT_MAX_ACTIVE_UPLOADS or 4)),
            "root": str(UPLOAD_ROOT),
        },
        "import_queue": queue_counts,
        "bot": bot_runtime_snapshot(),
    }


async def runtime_readiness() -> dict[str, Any]:
    # Bothost may call readiness frequently, so avoid PRAGMA quick_check here.
    report = await runtime_performance_report(deep_check=False)
    minimum = int(report.get("disk", {}).get("minimum_free_bytes") or 0)
    free_values = [
        int(report.get("disk", {}).get("database", {}).get("free") or 0),
        int(report.get("disk", {}).get("storage", {}).get("free") or 0),
    ]
    known_free = [value for value in free_values if value > 0]
    disk_ok = not minimum or not known_free or min(known_free) >= minimum
    return {
        "ok": bool(report.get("ok") and disk_ok),
        "database_ok": bool(report.get("database", {}).get("ok")),
        "disk_ok": bool(disk_ok),
    }


async def runtime_maintenance_once() -> dict[str, int]:
    """Bounded cleanup/checkpoint pass suitable for periodic execution."""
    stale_uploads = await asyncio.to_thread(cleanup_stale_uploads)
    stale_import_work = await asyncio.to_thread(cleanup_stale_import_work)
    await asyncio.to_thread(cleanup_tts_cache)
    try:
        async with connect() as db:
            await db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            await db.execute("PRAGMA optimize")
            await db.commit()
    except Exception as exc:
        logger.warning("SQLite maintenance pass failed: %s", exc)
    return {
        "stale_uploads_removed": int(stale_uploads),
        "stale_import_work_removed": int(stale_import_work),
    }


async def runtime_maintenance_loop(interval_seconds: int = 30 * 60) -> None:
    """Keep temp storage and WAL bounded without blocking bot startup."""
    await asyncio.sleep(45)
    while True:
        try:
            result = await runtime_maintenance_once()
            report = await runtime_performance_report(deep_check=False)
            if result["stale_uploads_removed"] or result["stale_import_work_removed"]:
                logger.info("Runtime maintenance cleanup: %s", result)
            if not report.get("ok"):
                logger.warning("Runtime readiness degraded: database=%s disk=%s", report.get("database"), report.get("disk"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Runtime maintenance loop failed")
        await asyncio.sleep(max(300, int(interval_seconds)))
