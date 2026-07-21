from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.config import settings
from app.db import connect, utc_now
from app.keyboards import library_batch_menu, library_import_active_menu, library_import_failed_menu, navigation_menu
from app.services.chunked_upload import cleanup_stale_uploads
from app.services.library_manager import (
    DEFAULT_STORAGE_ROOT,
    ImportResult,
    cleanup_stale_import_work,
    ensure_library_schema,
    finalize_import_replacement_backups,
    import_library_zip,
    restore_import_replacement_backups,
)

logger = logging.getLogger(__name__)


class ImportCancellationRequested(RuntimeError):
    """Внутренний сигнал кооперативной остановки между целостными операциями."""


IMPORT_QUEUE_ROOT = DEFAULT_STORAGE_ROOT / "import_queue"
IMPORT_UPLOAD_ROOT = IMPORT_QUEUE_ROOT / "uploads"

QUEUE_MODE_SETTING_KEY = "library_import_queue_mode"
QUEUE_MODE_ACTOR_SETTING_KEY = "library_import_queue_mode_actor"
QUEUE_MODES = {"running", "paused", "maintenance"}

_QUEUE_SCHEMA_LOCK = asyncio.Lock()
_QUEUE_SCHEMA_READY: set[str] = set()


def _normalize_queue_mode(value: Any) -> str:
    mode = str(value or "running").strip().lower()
    return mode if mode in QUEUE_MODES else "running"


async def _read_queue_mode(db) -> tuple[str, str, int | None]:
    cur = await db.execute(
        "SELECT value, updated_at FROM settings WHERE key=? LIMIT 1",
        (QUEUE_MODE_SETTING_KEY,),
    )
    row = await cur.fetchone()
    mode = _normalize_queue_mode(row["value"] if row else "running")
    changed_at = str(row["updated_at"] or "") if row else ""
    cur = await db.execute(
        "SELECT value FROM settings WHERE key=? LIMIT 1",
        (QUEUE_MODE_ACTOR_SETTING_KEY,),
    )
    actor_row = await cur.fetchone()
    try:
        actor_user_id = int(actor_row["value"]) if actor_row and str(actor_row["value"]).strip() else None
    except (TypeError, ValueError):
        actor_user_id = None
    return mode, changed_at, actor_user_id


def _failed_archive_retention_hours() -> int:
    return max(1, min(168, int(getattr(settings, "LIBRARY_IMPORT_FAILED_ARCHIVE_HOURS", 24) or 24)))


def _failed_archive_expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=_failed_archive_retention_hours())).isoformat()


def _iso_is_future(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _friendly_import_error(exc: BaseException) -> str:
    message = str(exc or "").strip()
    lowered = message.lower()
    if any(
        marker in lowered
        for marker in ("no space left", "database or disk is full", "disk full")
    ):
        return (
            "На сервере недостаточно свободного места. Незавершённый пакет очищен автоматически. "
            "Освободите место или увеличьте диск, затем повторите сохранённое задание импорта."
        )
    return message or "Неизвестная ошибка импорта"


async def _cleanup_orphaned_queue_archives(*, min_age_seconds: int = 10 * 60) -> int:
    """Удаляет ZIP, не принадлежащие очереди или ещё не истёкшим неудачным заданиям."""
    await asyncio.to_thread(IMPORT_UPLOAD_ROOT.mkdir, parents=True, exist_ok=True)
    async with connect() as db:
        now_iso = utc_now()
        cur = await db.execute(
            """SELECT archive_path FROM library_import_jobs
               WHERE status IN ('queued','processing','cancelling')
                  OR (status IN ('failed','cancelled') AND archive_expires_at IS NOT NULL AND archive_expires_at>?)""",
            (now_iso,),
        )
        rows = await cur.fetchall()
        await db.execute(
            """UPDATE library_import_jobs SET archive_expires_at=NULL
               WHERE status IN ('failed','cancelled') AND archive_expires_at IS NOT NULL AND archive_expires_at<=?""",
            (now_iso,),
        )
        await db.commit()
    keep = {str(Path(str(row["archive_path"])).resolve()) for row in rows}
    now = time.time()
    removed = 0
    for path in IMPORT_UPLOAD_ROOT.glob("*.zip"):
        try:
            resolved = str(path.resolve())
            if resolved in keep:
                continue
            if now - path.stat().st_mtime < max(60, int(min_age_seconds)):
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


@dataclass(slots=True)
class ImportQueueJob:
    id: int
    archive_name: str
    archive_path: str
    archive_hash: str
    actor_user_id: int
    chat_id: int
    progress_message_id: int
    status: str
    batch_id: int | None = None
    processed: int = 0
    total: int = 0
    added: int = 0
    replaced: int = 0
    renumbered: int = 0
    duplicates: int = 0
    error_count: int = 0
    phase: int = 0
    current_folder: str = ""
    current_title: str = ""
    restart_count: int = 0


def _row_to_job(row: Any) -> ImportQueueJob:
    return ImportQueueJob(
        id=int(row["id"]),
        archive_name=str(row["archive_name"]),
        archive_path=str(row["archive_path"]),
        archive_hash=str(row["archive_hash"]),
        actor_user_id=int(row["actor_user_id"]),
        chat_id=int(row["chat_id"]),
        progress_message_id=int(row["progress_message_id"]),
        status=str(row["status"]),
        batch_id=int(row["batch_id"]) if row["batch_id"] is not None else None,
        processed=int(row["processed"] or 0),
        total=int(row["total"] or 0),
        added=int(row["added"] or 0),
        replaced=int(row["replaced_count"] or 0),
        renumbered=int(row["renumbered_count"] or 0),
        duplicates=int(row["duplicate_count"] or 0),
        error_count=int(row["error_count"] or 0),
        phase=int(row["phase"] or 0),
        current_folder=str(row["current_folder"] or "") if "current_folder" in row.keys() else "",
        current_title=str(row["current_title"] or "") if "current_title" in row.keys() else "",
        restart_count=int(row["restart_count"] or 0) if "restart_count" in row.keys() else 0,
    )


async def ensure_import_queue_schema() -> None:
    database_key = os.path.abspath(os.path.expanduser(str(settings.DATABASE_PATH)))
    if database_key in _QUEUE_SCHEMA_READY:
        return
    async with _QUEUE_SCHEMA_LOCK:
        if database_key in _QUEUE_SCHEMA_READY:
            return
        await ensure_library_schema()
        async with connect() as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS library_import_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    archive_name TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    archive_hash TEXT NOT NULL,
                    actor_user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    progress_message_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    batch_id INTEGER,
                    processed INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    added INTEGER NOT NULL DEFAULT 0,
                    replaced_count INTEGER NOT NULL DEFAULT 0,
                    renumbered_count INTEGER NOT NULL DEFAULT 0,
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    phase INTEGER NOT NULL DEFAULT 0,
                    current_folder TEXT,
                    current_title TEXT,
                    restart_count INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    archive_expires_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    heartbeat_at TEXT,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_library_import_jobs_status
                    ON library_import_jobs(status, id);
                CREATE INDEX IF NOT EXISTS idx_library_import_jobs_hash
                    ON library_import_jobs(archive_hash, status);
                CREATE INDEX IF NOT EXISTS idx_library_import_jobs_claim
                    ON library_import_jobs(status, id, cancel_requested);
                CREATE INDEX IF NOT EXISTS idx_library_import_jobs_heartbeat
                    ON library_import_jobs(status, heartbeat_at);
                CREATE INDEX IF NOT EXISTS idx_library_import_jobs_actor_created
                    ON library_import_jobs(actor_user_id, created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS library_import_upload_receipts (
                    upload_id TEXT PRIMARY KEY,
                    actor_user_id INTEGER NOT NULL,
                    archive_name TEXT NOT NULL,
                    archive_hash TEXT NOT NULL,
                    job_id INTEGER NOT NULL,
                    queue_position INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES library_import_jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_library_upload_receipts_expiry
                    ON library_import_upload_receipts(expires_at);
                CREATE INDEX IF NOT EXISTS idx_library_upload_receipts_actor_expiry
                    ON library_import_upload_receipts(actor_user_id, expires_at);
                """
            )
            cur = await db.execute("PRAGMA table_info(library_import_jobs)")
            columns = {row[1] for row in await cur.fetchall()}
            for name, ddl in {
                "replaced_count": "ALTER TABLE library_import_jobs ADD COLUMN replaced_count INTEGER NOT NULL DEFAULT 0",
                "renumbered_count": "ALTER TABLE library_import_jobs ADD COLUMN renumbered_count INTEGER NOT NULL DEFAULT 0",
                "retry_count": "ALTER TABLE library_import_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
                "archive_expires_at": "ALTER TABLE library_import_jobs ADD COLUMN archive_expires_at TEXT",
                "cancel_requested": "ALTER TABLE library_import_jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0",
                "cancel_requested_at": "ALTER TABLE library_import_jobs ADD COLUMN cancel_requested_at TEXT",
                "current_folder": "ALTER TABLE library_import_jobs ADD COLUMN current_folder TEXT",
                "current_title": "ALTER TABLE library_import_jobs ADD COLUMN current_title TEXT",
                "restart_count": "ALTER TABLE library_import_jobs ADD COLUMN restart_count INTEGER NOT NULL DEFAULT 0",
            }.items():
                if name not in columns:
                    await db.execute(ddl)
            now = utc_now()
            await db.execute(
                """INSERT INTO settings(key, value, updated_at) VALUES(?, 'running', ?)
                   ON CONFLICT(key) DO NOTHING""",
                (QUEUE_MODE_SETTING_KEY, now),
            )
            await db.execute(
                """INSERT INTO settings(key, value, updated_at) VALUES(?, '', ?)
                   ON CONFLICT(key) DO NOTHING""",
                (QUEUE_MODE_ACTOR_SETTING_KEY, now),
            )
            await db.execute("DELETE FROM library_import_upload_receipts WHERE expires_at<=?", (utc_now(),))
            await db.commit()
        await asyncio.to_thread(IMPORT_UPLOAD_ROOT.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(cleanup_stale_uploads)
        await asyncio.to_thread(cleanup_stale_import_work)
        await _cleanup_orphaned_queue_archives()
        _QUEUE_SCHEMA_READY.add(database_key)


async def get_import_queue_control_state() -> dict[str, Any]:
    """Возвращает сохранённый режим очереди и её фактическое состояние."""
    await ensure_import_queue_schema()
    async with connect() as db:
        mode, changed_at, actor_user_id = await _read_queue_mode(db)
        cur = await db.execute(
            """SELECT status, COUNT(*) AS count FROM library_import_jobs
               WHERE status IN ('queued','processing','cancelling')
               GROUP BY status"""
        )
        counts = {str(row["status"]): int(row["count"] or 0) for row in await cur.fetchall()}
    active_count = int(counts.get("processing", 0)) + int(counts.get("cancelling", 0))
    queued_count = int(counts.get("queued", 0))
    return {
        "mode": mode,
        "paused": mode != "running",
        "draining": bool(mode != "running" and active_count),
        "queued": queued_count,
        "active": active_count,
        "changed_at": changed_at,
        "changed_by_user_id": actor_user_id,
    }


async def set_import_queue_mode(mode: str, *, actor_user_id: int) -> dict[str, Any]:
    """Переключает режим очереди, не прерывая уже выполняющееся задание."""
    normalized = _normalize_queue_mode(mode)
    if str(mode or "").strip().lower() not in QUEUE_MODES:
        raise ValueError("Неизвестный режим очереди импорта")
    await ensure_import_queue_schema()
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (QUEUE_MODE_SETTING_KEY, normalized, now),
        )
        await db.execute(
            """INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (QUEUE_MODE_ACTOR_SETTING_KEY, str(int(actor_user_id)), now),
        )
        await db.commit()
    return await get_import_queue_control_state()


async def calculate_archive_hash(path: str | Path) -> str:
    file_path = Path(path)

    def calculate() -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    return await asyncio.to_thread(calculate)


async def enqueue_import_job(
    *,
    archive_path: str | Path,
    archive_name: str,
    archive_hash: str,
    actor_user_id: int,
    chat_id: int,
    progress_message_id: int,
    idempotency_key: str = "",
) -> tuple[int, int]:
    """Ставит уже загруженный ZIP в постоянную очередь и возвращает номер и позицию."""
    await ensure_import_queue_schema()
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise ValueError("Загруженный архив не найден")

    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        clean_key = str(idempotency_key or "").strip()[:128]
        if clean_key:
            cur = await db.execute(
                """SELECT job_id, queue_position, actor_user_id
                   FROM library_import_upload_receipts
                   WHERE upload_id=? AND expires_at>? LIMIT 1""",
                (clean_key, utc_now()),
            )
            receipt = await cur.fetchone()
            if receipt is not None:
                if int(receipt["actor_user_id"]) != int(actor_user_id):
                    await db.rollback()
                    raise ValueError("Эта загрузка принадлежит другому пользователю")
                await db.commit()
                return int(receipt["job_id"]), max(1, int(receipt["queue_position"] or 1))
        cur = await db.execute(
            """SELECT id FROM library_import_batches
               WHERE archive_hash=? AND status IN ('completed','published')
               ORDER BY id DESC LIMIT 1""",
            (archive_hash,),
        )
        previous = await cur.fetchone()
        if previous:
            await db.rollback()
            raise ValueError(f"Этот архив уже импортировался ранее: пакет #{int(previous['id'])}")

        cur = await db.execute(
            """SELECT id FROM library_import_jobs
               WHERE archive_hash=? AND status IN ('queued','processing','cancelling')
               ORDER BY id DESC LIMIT 1""",
            (archive_hash,),
        )
        existing = await cur.fetchone()
        if existing:
            await db.rollback()
            raise ValueError(f"Этот архив уже находится в очереди: задание #{int(existing['id'])}")

        cur = await db.execute(
            """SELECT id, archive_path, archive_expires_at FROM library_import_jobs
               WHERE archive_hash=? AND status IN ('failed','cancelled')
               ORDER BY id DESC LIMIT 1""",
            (archive_hash,),
        )
        failed = await cur.fetchone()
        if failed:
            retained = Path(str(failed["archive_path"] or ""))
            if retained.is_file() and _is_inside(retained, IMPORT_QUEUE_ROOT) and _iso_is_future(failed["archive_expires_at"]):
                await db.rollback()
                raise ValueError(
                    f"Этот архив уже сохранён после ошибки в задании #{int(failed['id'])}. "
                    "Используйте кнопку «Повторить», новая загрузка не нужна."
                )

        cur = await db.execute(
            """INSERT INTO library_import_jobs(
                   archive_name, archive_path, archive_hash, actor_user_id,
                   chat_id, progress_message_id, status, created_at
               ) VALUES(?, ?, ?, ?, ?, ?, 'queued', ?)""",
            (
                archive_name,
                str(archive_path),
                archive_hash,
                int(actor_user_id),
                int(chat_id),
                int(progress_message_id),
                utc_now(),
            ),
        )
        job_id = int(cur.lastrowid)
        cur = await db.execute(
            "SELECT COUNT(*) FROM library_import_jobs WHERE status='queued' AND id<=?",
            (job_id,),
        )
        position = int((await cur.fetchone())[0] or 1)
        if clean_key:
            now = datetime.now(timezone.utc)
            expires = (now + timedelta(hours=max(24, _failed_archive_retention_hours()))).isoformat()
            await db.execute(
                """INSERT INTO library_import_upload_receipts(
                       upload_id, actor_user_id, archive_name, archive_hash, job_id,
                       queue_position, created_at, expires_at
                   ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(upload_id) DO UPDATE SET
                       actor_user_id=excluded.actor_user_id, archive_name=excluded.archive_name,
                       archive_hash=excluded.archive_hash, job_id=excluded.job_id,
                       queue_position=excluded.queue_position, created_at=excluded.created_at,
                       expires_at=excluded.expires_at""",
                (clean_key, int(actor_user_id), archive_name, archive_hash, job_id, position, now.isoformat(), expires),
            )
        await db.commit()
    return job_id, position


async def get_import_upload_receipt(upload_id: str, *, actor_user_id: int) -> dict[str, Any] | None:
    """Возвращает результат уже завершённой постановки загрузки в очередь.

    Это делает /finish идемпотентным: если Bothost принял ZIP, но ответ потерялся,
    повторный запрос получает прежний job_id, а не ошибку «загрузка не найдена».
    """
    await ensure_import_queue_schema()
    clean_key = str(upload_id or "").strip()[:128]
    if not clean_key:
        return None
    async with connect() as db:
        cur = await db.execute(
            """SELECT job_id, queue_position, actor_user_id, archive_name, archive_hash, expires_at
               FROM library_import_upload_receipts
               WHERE upload_id=? AND expires_at>? LIMIT 1""",
            (clean_key, utc_now()),
        )
        row = await cur.fetchone()
    if row is None or int(row["actor_user_id"]) != int(actor_user_id):
        return None
    return {
        "job_id": int(row["job_id"]),
        "position": max(1, int(row["queue_position"] or 1)),
        "archive_name": str(row["archive_name"] or ""),
        "archive_hash": str(row["archive_hash"] or ""),
        "expires_at": str(row["expires_at"] or ""),
    }


async def list_import_jobs(
    limit: int = 20,
    *,
    actor_user_id: int | None = None,
) -> list[dict[str, Any]]:
    """Возвращает последние задания с живым прогрессом для Mini App.

    Модератор только с правом массовой загрузки получает исключительно свои
    задания. Полный список запрашивает владелец или управляющий импортом.
    """
    await ensure_import_queue_schema()
    safe_limit = max(1, min(100, int(limit)))
    where = ""
    params: list[Any] = []
    if actor_user_id is not None:
        where = "WHERE j.actor_user_id=?"
        params.append(int(actor_user_id))
    params.append(safe_limit)
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT j.id, j.archive_name, j.actor_user_id, j.status, j.batch_id,
                   j.processed, j.total, j.added, j.replaced_count,
                   j.renumbered_count, j.duplicate_count, j.error_count,
                   j.phase, j.current_folder, j.current_title, j.restart_count,
                   j.retry_count, j.archive_expires_at, j.archive_path,
                   j.cancel_requested, j.cancel_requested_at, j.last_error, j.created_at, j.started_at,
                   j.heartbeat_at, j.completed_at,
                   CASE WHEN j.status='queued' THEN (
                       SELECT COUNT(*) FROM library_import_jobs q
                       WHERE q.status='queued' AND q.id<=j.id
                   ) ELSE NULL END AS queue_position
            FROM library_import_jobs j
            {where}
            ORDER BY j.id DESC
            LIMIT ?
            """,
            params,
        )
        rows = await cur.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        processed = max(0, int(item.get("processed") or 0))
        total = max(0, int(item.get("total") or 0))
        item["progress_percent"] = min(100, int(processed * 100 / total)) if total else 0
        item["can_cancel"] = str(item.get("status") or "") in {"queued", "processing"}
        archive_path = Path(str(item.pop("archive_path", "") or ""))
        item["archive_available"] = bool(archive_path.is_file() and _is_inside(archive_path, IMPORT_QUEUE_ROOT))
        item["can_retry"] = bool(
            str(item.get("status") or "") in {"failed", "cancelled"}
            and item["archive_available"]
            and _iso_is_future(item.get("archive_expires_at"))
        )
        result.append(item)
    return result


async def cancel_import_job(
    job_id: int,
    *,
    actor_user_id: int,
    allow_any: bool = False,
) -> dict[str, Any]:
    """Отменяет ожидающее задание или запрашивает безопасную остановку активного."""
    await ensure_import_queue_schema()
    cleanup_job: ImportQueueJob | None = None
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT * FROM library_import_jobs WHERE id=?", (int(job_id),))
        row = await cur.fetchone()
        if row is None:
            await db.commit()
            return {"ok": False, "reason": "not_found"}
        if not allow_any and int(row["actor_user_id"]) != int(actor_user_id):
            await db.commit()
            return {"ok": False, "reason": "forbidden", "status": str(row["status"])}

        status = str(row["status"])
        now = utc_now()
        if status == "queued":
            changed = await db.execute(
                """UPDATE library_import_jobs
                   SET status='cancelled', cancel_requested=0, cancel_requested_at=NULL,
                       last_error='Отменено до запуска', archive_expires_at=NULL,
                       current_folder=NULL, current_title=NULL, heartbeat_at=?, completed_at=?
                   WHERE id=? AND status='queued'""",
                (now, now, int(job_id)),
            )
            if changed.rowcount != 1:
                await db.rollback()
                return {"ok": False, "reason": "race"}
            cleanup_job = _row_to_job(row)
            await db.commit()
            result = {"ok": True, "status": "cancelled", "job_id": int(job_id), "pending": False}
        elif status == "processing":
            changed = await db.execute(
                """UPDATE library_import_jobs
                   SET status='cancelling', cancel_requested=1, cancel_requested_at=?,
                       last_error='Запрошена безопасная остановка', heartbeat_at=?
                   WHERE id=? AND status='processing'""",
                (now, now, int(job_id)),
            )
            if changed.rowcount != 1:
                await db.rollback()
                return {"ok": False, "reason": "race"}
            await db.commit()
            result = {"ok": True, "status": "cancelling", "job_id": int(job_id), "pending": True}
        elif status == "cancelling":
            await db.commit()
            result = {"ok": True, "status": "cancelling", "job_id": int(job_id), "pending": True}
        else:
            await db.commit()
            return {"ok": False, "reason": "not_cancellable", "status": status}

    if cleanup_job is not None:
        await _cleanup_archive(cleanup_job)
    return result


async def retry_import_job(
    job_id: int,
    *,
    actor_user_id: int,
    allow_any: bool = False,
) -> dict[str, Any]:
    """Возвращает неудачное задание в очередь, пока сохранён исходный ZIP."""
    await ensure_import_queue_schema()
    expired_path: Path | None = None
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT * FROM library_import_jobs WHERE id=?", (int(job_id),))
        row = await cur.fetchone()
        if row is None:
            await db.commit()
            return {"ok": False, "reason": "not_found"}
        if not allow_any and int(row["actor_user_id"]) != int(actor_user_id):
            await db.commit()
            return {"ok": False, "reason": "forbidden", "status": str(row["status"])}
        retryable_status = str(row["status"])
        if retryable_status not in {"failed", "cancelled"}:
            await db.commit()
            return {"ok": False, "reason": "not_retryable", "status": retryable_status}

        archive_path = Path(str(row["archive_path"] or ""))
        available = archive_path.is_file() and _is_inside(archive_path, IMPORT_QUEUE_ROOT)
        unexpired = _iso_is_future(row["archive_expires_at"])
        if not available or not unexpired:
            if available:
                expired_path = archive_path
            await db.execute(
                """UPDATE library_import_jobs
                   SET archive_expires_at=NULL, last_error=?
                   WHERE id=? AND status IN ('failed','cancelled')""",
                ("Срок хранения исходного ZIP истёк. Загрузите архив повторно.", int(job_id)),
            )
            await db.commit()
            result = {"ok": False, "reason": "archive_expired", "status": "failed"}
        else:
            now = utc_now()
            changed = await db.execute(
                """UPDATE library_import_jobs
                   SET status='queued', batch_id=NULL, processed=0, total=0, added=0,
                       replaced_count=0, renumbered_count=0, duplicate_count=0,
                       error_count=0, phase=0, retry_count=COALESCE(retry_count, 0)+1,
                       archive_expires_at=NULL, cancel_requested=0, cancel_requested_at=NULL,
                       current_folder=NULL, current_title=NULL,
                       last_error=NULL, started_at=NULL, heartbeat_at=?, completed_at=NULL
                   WHERE id=? AND status IN ('failed','cancelled')""",
                (now, int(job_id)),
            )
            if changed.rowcount != 1:
                await db.rollback()
                return {"ok": False, "reason": "race"}
            cur = await db.execute(
                "SELECT COUNT(*) FROM library_import_jobs WHERE status='queued' AND id<=?",
                (int(job_id),),
            )
            position = int((await cur.fetchone())[0] or 1)
            await db.commit()
            result = {"ok": True, "status": "queued", "job_id": int(job_id), "position": position}
    if expired_path is not None:
        try:
            await asyncio.to_thread(expired_path.unlink, missing_ok=True)
        except OSError:
            pass
    return result


async def _claim_next_job() -> ImportQueueJob | None:
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        mode, _, _ = await _read_queue_mode(db)
        if mode != "running":
            await db.commit()
            return None
        cur = await db.execute(
            "SELECT * FROM library_import_jobs WHERE status='queued' ORDER BY id LIMIT 1"
        )
        row = await cur.fetchone()
        if row is None:
            await db.commit()
            return None
        job_id = int(row["id"])
        changed = await db.execute(
            """UPDATE library_import_jobs
               SET status='processing', cancel_requested=0, cancel_requested_at=NULL,
                   started_at=COALESCE(started_at, ?), heartbeat_at=?, last_error=NULL
               WHERE id=? AND status='queued'""",
            (utc_now(), utc_now(), job_id),
        )
        if changed.rowcount != 1:
            await db.rollback()
            return None
        await db.commit()
        cur = await db.execute("SELECT * FROM library_import_jobs WHERE id=?", (job_id,))
        claimed = await cur.fetchone()
        return _row_to_job(claimed)


async def _update_job_progress(job_id: int, data: dict[str, Any]) -> None:
    async with connect() as db:
        await db.execute(
            """UPDATE library_import_jobs
               SET batch_id=?, processed=?, total=?, added=?, replaced_count=?,
                   renumbered_count=?, duplicate_count=?, error_count=?, phase=?,
                   current_folder=?, current_title=?, heartbeat_at=?
               WHERE id=? AND status IN ('processing','cancelling')""",
            (
                int(data.get("batch_id", 0)) or None,
                int(data.get("processed", 0)),
                int(data.get("total", 0)),
                int(data.get("added", 0)),
                int(data.get("replaced", 0)),
                int(data.get("renumbered", 0)),
                int(data.get("duplicates", 0)),
                int(data.get("errors", 0)),
                int(data.get("phase", 0)),
                str(data.get("current_folder") or "")[:240] or None,
                str(data.get("current_title") or "")[:300] or None,
                utc_now(),
                int(job_id),
            ),
        )
        await db.commit()


async def _job_cancellation_requested(job_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "SELECT status, cancel_requested FROM library_import_jobs WHERE id=?",
            (int(job_id),),
        )
        row = await cur.fetchone()
    return bool(row and (str(row["status"]) == "cancelling" or int(row["cancel_requested"] or 0) == 1))


async def _set_job_completed(job_id: int, result: ImportResult) -> bool:
    async with connect() as db:
        changed = await db.execute(
            """UPDATE library_import_jobs
               SET status='completed', batch_id=?, added=?, replaced_count=?,
                   renumbered_count=?, duplicate_count=?, error_count=?, phase=3,
                   archive_expires_at=NULL, cancel_requested=0, cancel_requested_at=NULL,
                   current_folder=NULL, current_title=NULL, heartbeat_at=?, completed_at=?
               WHERE id=? AND status='processing' AND COALESCE(cancel_requested, 0)=0""",
            (
                int(result.batch_id),
                int(result.added),
                int(result.replaced),
                int(result.renumbered),
                int(result.duplicates),
                len(result.errors),
                utc_now(),
                utc_now(),
                int(job_id),
            ),
        )
        await db.commit()
        return changed.rowcount == 1


async def _set_job_failed(job_id: int, error: str) -> bool:
    async with connect() as db:
        now = utc_now()
        changed = await db.execute(
            """UPDATE library_import_jobs
               SET status='failed', batch_id=NULL, last_error=?, archive_expires_at=?,
                   cancel_requested=0, cancel_requested_at=NULL,
                   current_folder=NULL, current_title=NULL, heartbeat_at=?, completed_at=?
               WHERE id=? AND status='processing' AND COALESCE(cancel_requested, 0)=0""",
            (str(error)[:2000], _failed_archive_expires_at(), now, now, int(job_id)),
        )
        await db.commit()
        return changed.rowcount == 1


async def _set_job_cancelled_after_processing(job_id: int) -> None:
    async with connect() as db:
        now = utc_now()
        await db.execute(
            """UPDATE library_import_jobs
               SET status='cancelled', batch_id=NULL,
                   last_error='Импорт безопасно остановлен пользователем',
                   archive_expires_at=?, cancel_requested=0, cancel_requested_at=NULL,
                   current_folder=NULL, current_title=NULL, heartbeat_at=?, completed_at=?
               WHERE id=? AND status IN ('processing','cancelling')""",
            (_failed_archive_expires_at(), now, now, int(job_id)),
        )
        await db.commit()


async def _discard_job_batch(job_id: int) -> int | None:
    async with connect() as db:
        cur = await db.execute("SELECT batch_id FROM library_import_jobs WHERE id=?", (int(job_id),))
        row = await cur.fetchone()
    batch_id = int(row["batch_id"]) if row and row["batch_id"] is not None else None
    if batch_id is not None:
        await discard_incomplete_import_batch(batch_id)
    return batch_id


async def _safe_edit(bot, job: ImportQueueJob, text: str, reply_markup=None) -> None:
    try:
        await bot.edit_message_text(
            chat_id=job.chat_id,
            message_id=job.progress_message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logger.warning("Could not update import job message #%s: %s", job.id, exc)
    except TelegramForbiddenError as exc:
        logger.warning("Import job message is unavailable #%s: %s", job.id, exc)
    except Exception as exc:
        logger.warning("Unexpected import progress error #%s: %s", job.id, exc)


def _progress_text(job_id: int, data: dict[str, Any]) -> str:
    processed = int(data.get("processed", 0))
    total = int(data.get("total", 0))
    phase = int(data.get("phase", 0))
    phase_text = {
        0: "Готовлю импорт",
        1: "Проверяю структуру",
        2: "Импортирую книги",
        3: "Завершаю пакет",
    }.get(phase, "Обрабатываю архив")
    percent = int(processed * 100 / total) if total else 0
    total_text = str(total) if total else "определяется"
    current_title = str(data.get("current_title") or "").strip()
    current_folder = str(data.get("current_folder") or "").strip()
    current_line = ""
    if current_title or current_folder:
        label = current_title or "Без названия"
        suffix = f" · {current_folder}" if current_folder else ""
        current_line = f"\nСейчас: <b>{html.escape(label[:180])}</b>{html.escape(suffix[:100])}\n"
    return (
        f"<b>⏳ {phase_text}</b>\n\n"
        f"Задание: <b>#{job_id}</b>\n"
        f"Обработано: <b>{processed} из {total_text}</b>"
        + (f" ({percent}%)" if total else "")
        + current_line
        + f"Добавлено: <b>{int(data.get('added', 0))}</b>\n"
        f"Заменено: <b>{int(data.get('replaced', 0))}</b>\n"
        f"Перенумеровано: <b>{int(data.get('renumbered', 0))}</b>\n"
        f"Дублей: <b>{int(data.get('duplicates', 0))}</b>\n"
        f"Ошибок: <b>{int(data.get('errors', 0))}</b>\n\n"
        "Бот продолжает работать — можно пользоваться другими разделами."
    )


def _completed_text(result: ImportResult) -> str:
    lines = [
        "<b>✅ Импорт завершён</b>",
        "",
        f"📚 Добавлено: <b>{result.added}</b>",
        f"🔄 Заменено: <b>{result.replaced}</b>",
        f"🔢 Перенумеровано: <b>{result.renumbered}</b>",
        f"⚠️ Найдено дублей: <b>{result.duplicates}</b>",
        f"❌ Ошибок: <b>{len(result.errors)}</b>",
        "",
        "Новые книги сохранены как черновики.",
    ]
    if result.duplicate_ids:
        lines.append("Дубли ожидают решения: пропустить или заменить существующую книгу.")
    if result.errors:
        lines.extend(["", "<b>Первые ошибки:</b>"])
        for item in result.errors[:6]:
            lines.append(
                f"• {html.escape(item.title)} ({html.escape(item.folder)}): "
                f"{html.escape('; '.join(item.reasons))}"
            )
        if len(result.errors) > 6:
            lines.append(f"…ещё {len(result.errors) - 6}")
    return "\n".join(lines)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


async def _cleanup_archive(job: ImportQueueJob) -> None:
    archive_path = Path(job.archive_path)
    try:
        if archive_path.is_file() and _is_inside(archive_path, IMPORT_QUEUE_ROOT):
            await asyncio.to_thread(archive_path.unlink)
        parent = archive_path.parent
        if parent.resolve() != IMPORT_UPLOAD_ROOT.resolve() and parent.is_dir() and _is_inside(parent, IMPORT_QUEUE_ROOT):
            await asyncio.to_thread(parent.rmdir)
    except OSError:
        pass


async def discard_incomplete_import_batch(batch_id: int) -> None:
    """Откатывает замены и удаляет только данные незавершённого пакета."""
    async with connect() as db:
        cur = await db.execute(
            "SELECT status FROM library_import_batches WHERE id=?", (int(batch_id),)
        )
        batch = await cur.fetchone()
    if batch is None or str(batch["status"]) not in {"processing", "failed", "completed"}:
        return

    # Сначала возвращаем заменённые книги вместе с прежними главами, правами и файлами.
    await restore_import_replacement_backups(int(batch_id))

    storage_paths: set[Path] = set()
    candidate_paths: set[Path] = set()
    async with connect() as db:
        cur = await db.execute(
            """SELECT source_file_name, cover_path FROM books
               WHERE import_batch_id=? AND COALESCE(import_was_replacement, 0)=0""",
            (int(batch_id),),
        )
        for row in await cur.fetchall():
            for value in (row["source_file_name"], row["cover_path"]):
                if value:
                    path = Path(str(value))
                    if path.exists():
                        storage_paths.add(path.parent)
        cur = await db.execute(
            "SELECT candidate_dir FROM library_import_duplicates WHERE batch_id=?",
            (int(batch_id),),
        )
        for row in await cur.fetchall():
            path = Path(str(row["candidate_dir"] or ""))
            if path.exists():
                candidate_paths.add(path)
        await db.execute(
            "DELETE FROM books WHERE import_batch_id=? AND COALESCE(import_was_replacement, 0)=0",
            (int(batch_id),),
        )
        await db.execute(
            """UPDATE books SET import_batch_id=NULL, import_was_replacement=0
               WHERE import_batch_id=? AND COALESCE(import_was_replacement, 0)=1""",
            (int(batch_id),),
        )
        await db.execute("DELETE FROM library_import_duplicates WHERE batch_id=?", (int(batch_id),))
        await db.execute("DELETE FROM library_import_batches WHERE id=?", (int(batch_id),))
        await db.commit()

    storage_paths.add(DEFAULT_STORAGE_ROOT / "books" / str(int(batch_id)))
    candidate_paths.add(DEFAULT_STORAGE_ROOT / "duplicates" / str(int(batch_id)))
    for path in sorted(storage_paths | candidate_paths, key=lambda item: len(str(item)), reverse=True):
        if path.exists() and _is_inside(path, DEFAULT_STORAGE_ROOT):
            await asyncio.to_thread(shutil.rmtree, path, True)


async def _job_heartbeat_loop(job_id: int) -> None:
    """Поддерживает heartbeat во время долгого разбора одной большой книги."""
    while True:
        await asyncio.sleep(30)
        async with connect() as db:
            changed = await db.execute(
                """UPDATE library_import_jobs SET heartbeat_at=?
                   WHERE id=? AND status IN ('processing','cancelling')""",
                (utc_now(), int(job_id)),
            )
            await db.commit()
        if changed.rowcount != 1:
            return


async def recover_interrupted_import_jobs(*, stale_before: str | None = None) -> int:
    """Восстанавливает оборванные задания, включая запрошенную до перезапуска остановку."""
    await ensure_import_queue_schema()
    async with connect() as db:
        if stale_before:
            cur = await db.execute(
                """SELECT id, status, batch_id, archive_path
                   FROM library_import_jobs
                   WHERE status IN ('processing','cancelling')
                     AND COALESCE(heartbeat_at, started_at, created_at)<? ORDER BY id""",
                (stale_before,),
            )
        else:
            cur = await db.execute(
                """SELECT id, status, batch_id, archive_path
                   FROM library_import_jobs
                   WHERE status IN ('processing','cancelling') ORDER BY id"""
            )
        rows = await cur.fetchall()
    recovered = 0
    for row in rows:
        batch_id = int(row["batch_id"]) if row["batch_id"] is not None else None
        if batch_id:
            await discard_incomplete_import_batch(batch_id)
        archive_path = Path(str(row["archive_path"]))
        was_cancelling = str(row["status"]) == "cancelling"
        if was_cancelling and archive_path.is_file():
            status = "cancelled"
            error = "Импорт безопасно остановлен после перезапуска"
            expires = _failed_archive_expires_at()
            completed_at = utc_now()
        elif archive_path.is_file():
            status = "queued"
            error = None
            expires = None
            completed_at = None
        else:
            status = "failed"
            error = "Архив задания потерян после перезапуска"
            expires = None
            completed_at = utc_now()
        async with connect() as db:
            await db.execute(
                """UPDATE library_import_jobs
                   SET status=?, batch_id=NULL, processed=0, total=0, added=0,
                       replaced_count=0, renumbered_count=0, duplicate_count=0,
                       error_count=0, phase=0, current_folder=NULL, current_title=NULL,
                       restart_count=COALESCE(restart_count, 0)+CASE WHEN ?='queued' THEN 1 ELSE 0 END,
                       archive_expires_at=?, last_error=?, cancel_requested=0, cancel_requested_at=NULL,
                       started_at=CASE WHEN ?='queued' THEN NULL ELSE started_at END,
                       heartbeat_at=?, completed_at=?
                   WHERE id=?""",
                (status, status, expires, error, status, utc_now(), completed_at, int(row["id"])),
            )
            await db.commit()
        if status == "queued":
            recovered += 1

    # Если процесс завершился после фиксации статуса, но до удаления временного
    # резерва, завершаем очистку при следующем запуске.
    async with connect() as db:
        cur = await db.execute(
            """SELECT DISTINCT j.batch_id
               FROM library_import_jobs j
               JOIN library_import_replacement_backups r ON r.batch_id=j.batch_id
               WHERE j.status='completed' AND j.batch_id IS NOT NULL"""
        )
        completed_batches = [int(row["batch_id"]) for row in await cur.fetchall()]
    for batch_id in completed_batches:
        try:
            await finalize_import_replacement_backups(batch_id)
        except Exception:
            logger.exception("Could not finalize recovered replacement backup for batch #%s", batch_id)
    return recovered


async def recover_stale_import_jobs(*, stale_seconds: int = 15 * 60) -> int:
    """Возвращает зависшие задания в безопасное состояние без перезапуска приложения."""
    await ensure_import_queue_schema()
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max(120, int(stale_seconds)))).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """SELECT COUNT(*) AS count FROM library_import_jobs
               WHERE status IN ('processing','cancelling')
                 AND COALESCE(heartbeat_at, started_at, created_at)<?""",
            (cutoff,),
        )
        count = int((await cur.fetchone())["count"] or 0)
    if not count:
        return 0
    logger.warning("Recovering %s stale library import job(s)", count)
    return await recover_interrupted_import_jobs(stale_before=cutoff)


async def process_next_import_job(bot) -> bool:
    job = await _claim_next_job()
    if job is None:
        return False

    asyncio.create_task(_job_heartbeat_loop(job.id))
    last_notification = {"processed": -1, "phase": -1}

    async def stop_safely() -> None:
        try:
            await _discard_job_batch(job.id)
        except Exception as exc:
            logger.exception("Could not rollback cancelled import job #%s", job.id)
            await _set_job_failed(job.id, f"Не удалось безопасно завершить откат: {exc}")
            await _safe_edit(
                bot,
                job,
                "<b>❌ Остановка не завершена</b>\n\n"
                "Не удалось полностью откатить незавершённый пакет. Повторный запуск заблокирован до проверки журнала.",
            )
            return
        await _set_job_cancelled_after_processing(job.id)
        await _safe_edit(
            bot,
            job,
            "<b>⛔ Импорт безопасно остановлен</b>\n\n"
            "Текущая целостная операция завершена. Незавершённый пакет удалён, "
            "а заменённые книги возвращены в прежнее состояние.\n\n"
            f"Исходный ZIP сохранён на <b>{_failed_archive_retention_hours()} ч.</b> "
            "и доступен для повторного запуска без новой загрузки.",
            library_import_failed_menu(job.id),
        )

    async def progress_callback(data: dict[str, Any]) -> None:
        await _update_job_progress(job.id, data)
        if await _job_cancellation_requested(job.id):
            raise ImportCancellationRequested("Запрошена безопасная остановка")
        processed = int(data.get("processed", 0))
        total = int(data.get("total", 0))
        phase = int(data.get("phase", 0))
        step = 1 if total <= 20 else max(4, total // 20)
        should_notify = (
            phase != last_notification["phase"]
            or processed in {0, total}
            or processed - last_notification["processed"] >= step
        )
        if not should_notify:
            return
        last_notification.update(processed=processed, phase=phase)
        try:
            await asyncio.wait_for(
                _safe_edit(
                    bot,
                    job,
                    _progress_text(job.id, data),
                    library_import_active_menu(job.id, processing=True),
                ),
                timeout=8,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out updating import progress message for job #%s", job.id)

    await _safe_edit(
        bot,
        job,
        "<b>⏳ Импорт запущен</b>\n\n"
        f"Задание: <b>#{job.id}</b>\n"
        "Архив проверяется в фоновой очереди. Бот продолжает работать — можно пользоваться другими разделами.",
        library_import_active_menu(job.id, processing=True),
    )

    try:
        result = await import_library_zip(
            Path(job.archive_path),
            job.archive_name,
            job.actor_user_id,
            progress_callback=progress_callback,
        )
    except asyncio.CancelledError:
        # Задание останется processing/cancelling и будет восстановлено при следующем запуске.
        raise
    except ImportCancellationRequested:
        await stop_safely()
        return True
    except ValueError as exc:
        if await _job_cancellation_requested(job.id):
            await stop_safely()
            return True
        await _discard_job_batch(job.id)
        if not await _set_job_failed(job.id, str(exc)):
            await stop_safely()
            return True
        hours = _failed_archive_retention_hours()
        await _safe_edit(
            bot,
            job,
            f"<b>❌ Импорт не начат</b>\n\n{html.escape(str(exc))}\n\n"
            f"Исходный ZIP сохранён на <b>{hours} ч.</b> Повторить импорт можно без новой загрузки.",
            library_import_failed_menu(job.id),
        )
        return True
    except Exception as exc:
        if await _job_cancellation_requested(job.id):
            await stop_safely()
            return True
        logger.exception("Library import job #%s failed", job.id)
        friendly_error = _friendly_import_error(exc)
        await _discard_job_batch(job.id)
        if not await _set_job_failed(job.id, friendly_error):
            await stop_safely()
            return True
        await _safe_edit(
            bot,
            job,
            "<b>❌ Импорт остановлен</b>\n\n"
            f"Причина: {html.escape(friendly_error[:1000])}\n\n"
            f"Архив не опубликован. Исходный ZIP сохранён на <b>{_failed_archive_retention_hours()} ч.</b> "
            "и может быть повторно поставлен в очередь без загрузки.",
            library_import_failed_menu(job.id),
        )
        return True

    if await _job_cancellation_requested(job.id):
        await stop_safely()
        return True
    completed = await _set_job_completed(job.id, result)
    if not completed:
        await stop_safely()
        return True
    try:
        await finalize_import_replacement_backups(result.batch_id)
    except Exception:
        logger.exception("Could not finalize replacement backups for batch #%s", result.batch_id)
    await _safe_edit(
        bot,
        job,
        _completed_text(result),
        library_batch_menu(result.batch_id, bool(result.duplicate_ids)),
    )
    await _cleanup_archive(job)
    return True


async def library_import_worker_loop(bot) -> None:
    await ensure_import_queue_schema()
    recovered = await recover_interrupted_import_jobs()
    if recovered:
        logger.info("Recovered %s interrupted library import job(s)", recovered)

    async def watchdog_loop() -> None:
        while True:
            try:
                await asyncio.sleep(60)
                await recover_stale_import_jobs()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Library import watchdog error")

    watchdog_task = asyncio.create_task(watchdog_loop())
    last_cleanup = 0.0
    try:
        while True:
            try:
                now = time.monotonic()
                if now - last_cleanup >= 1800:
                    await _cleanup_orphaned_queue_archives(min_age_seconds=0)
                    async with connect() as db:
                        await db.execute(
                            "DELETE FROM library_import_upload_receipts WHERE expires_at<=?",
                            (utc_now(),),
                        )
                        await db.commit()
                    last_cleanup = now
                processed = await process_next_import_job(bot)
                if not processed:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Library import queue loop error")
                await asyncio.sleep(3)
    finally:
        watchdog_task.cancel()
        await asyncio.gather(watchdog_task, return_exceptions=True)
