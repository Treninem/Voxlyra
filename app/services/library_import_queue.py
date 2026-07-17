from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.config import settings
from app.db import connect, utc_now
from app.keyboards import library_batch_menu, navigation_menu
from app.services.library_manager import (
    DEFAULT_STORAGE_ROOT,
    ImportResult,
    ensure_library_schema,
    import_library_zip,
)

logger = logging.getLogger(__name__)

IMPORT_QUEUE_ROOT = DEFAULT_STORAGE_ROOT / "import_queue"
IMPORT_UPLOAD_ROOT = IMPORT_QUEUE_ROOT / "uploads"

_QUEUE_SCHEMA_LOCK = asyncio.Lock()
_QUEUE_SCHEMA_READY: set[str] = set()


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
    duplicates: int = 0
    error_count: int = 0
    phase: int = 0


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
        duplicates=int(row["duplicate_count"] or 0),
        error_count=int(row["error_count"] or 0),
        phase=int(row["phase"] or 0),
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
                    duplicate_count INTEGER NOT NULL DEFAULT 0,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    phase INTEGER NOT NULL DEFAULT 0,
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
                """
            )
            await db.commit()
        await asyncio.to_thread(IMPORT_UPLOAD_ROOT.mkdir, parents=True, exist_ok=True)
        _QUEUE_SCHEMA_READY.add(database_key)


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
) -> tuple[int, int]:
    """Ставит уже загруженный ZIP в постоянную очередь и возвращает номер и позицию."""
    await ensure_import_queue_schema()
    archive_path = Path(archive_path)
    if not archive_path.is_file():
        raise ValueError("Загруженный архив не найден")

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

        cur = await db.execute(
            """SELECT id FROM library_import_jobs
               WHERE archive_hash=? AND status IN ('queued','processing')
               ORDER BY id DESC LIMIT 1""",
            (archive_hash,),
        )
        existing = await cur.fetchone()
        if existing:
            raise ValueError(f"Этот архив уже находится в очереди: задание #{int(existing['id'])}")

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
        await db.commit()
    return job_id, position


async def _claim_next_job() -> ImportQueueJob | None:
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
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
               SET status='processing', started_at=COALESCE(started_at, ?), heartbeat_at=?, last_error=NULL
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


async def _update_job_progress(job_id: int, data: dict[str, int]) -> None:
    async with connect() as db:
        await db.execute(
            """UPDATE library_import_jobs
               SET batch_id=?, processed=?, total=?, added=?, duplicate_count=?,
                   error_count=?, phase=?, heartbeat_at=?
               WHERE id=?""",
            (
                int(data.get("batch_id", 0)) or None,
                int(data.get("processed", 0)),
                int(data.get("total", 0)),
                int(data.get("added", 0)),
                int(data.get("duplicates", 0)),
                int(data.get("errors", 0)),
                int(data.get("phase", 0)),
                utc_now(),
                int(job_id),
            ),
        )
        await db.commit()


async def _set_job_completed(job_id: int, result: ImportResult) -> None:
    async with connect() as db:
        await db.execute(
            """UPDATE library_import_jobs
               SET status='completed', batch_id=?, added=?, duplicate_count=?,
                   error_count=?, phase=3, heartbeat_at=?, completed_at=?
               WHERE id=?""",
            (
                int(result.batch_id),
                int(result.added),
                int(result.duplicates),
                len(result.errors),
                utc_now(),
                utc_now(),
                int(job_id),
            ),
        )
        await db.commit()


async def _set_job_failed(job_id: int, error: str) -> None:
    async with connect() as db:
        await db.execute(
            """UPDATE library_import_jobs
               SET status='failed', last_error=?, heartbeat_at=?, completed_at=?
               WHERE id=?""",
            (str(error)[:2000], utc_now(), utc_now(), int(job_id)),
        )
        await db.commit()


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


def _progress_text(job_id: int, data: dict[str, int]) -> str:
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
    return (
        f"<b>⏳ {phase_text}</b>\n\n"
        f"Задание: <b>#{job_id}</b>\n"
        f"Обработано: <b>{processed} из {total_text}</b>"
        + (f" ({percent}%)" if total else "")
        + "\n"
        f"Добавлено: <b>{int(data.get('added', 0))}</b>\n"
        f"Дублей: <b>{int(data.get('duplicates', 0))}</b>\n"
        f"Ошибок: <b>{int(data.get('errors', 0))}</b>\n\n"
        "Бот продолжает работать — можно пользоваться другими разделами."
    )


def _completed_text(result: ImportResult) -> str:
    lines = [
        "<b>✅ Импорт завершён</b>",
        "",
        f"📚 Добавлено: <b>{result.added}</b>",
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
    """Удаляет только незавершённый пакет перед безопасным повторным запуском задания."""
    storage_paths: set[Path] = set()
    candidate_paths: set[Path] = set()
    async with connect() as db:
        cur = await db.execute(
            "SELECT status FROM library_import_batches WHERE id=?", (int(batch_id),)
        )
        batch = await cur.fetchone()
        if batch is None or str(batch["status"]) not in {"processing", "failed"}:
            return
        cur = await db.execute(
            "SELECT source_file_name, cover_path FROM books WHERE import_batch_id=?",
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
        await db.execute("DELETE FROM books WHERE import_batch_id=?", (int(batch_id),))
        await db.execute("DELETE FROM library_import_duplicates WHERE batch_id=?", (int(batch_id),))
        await db.execute("DELETE FROM library_import_batches WHERE id=?", (int(batch_id),))
        await db.commit()

    for path in sorted(storage_paths | candidate_paths, key=lambda item: len(str(item)), reverse=True):
        if path.exists() and _is_inside(path, DEFAULT_STORAGE_ROOT):
            await asyncio.to_thread(shutil.rmtree, path, True)


async def recover_interrupted_import_jobs() -> int:
    """Возвращает оборванные при перезапуске задания в очередь без дублей."""
    await ensure_import_queue_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id, batch_id, archive_path FROM library_import_jobs WHERE status='processing' ORDER BY id"
        )
        rows = await cur.fetchall()
    recovered = 0
    for row in rows:
        batch_id = int(row["batch_id"]) if row["batch_id"] is not None else None
        if batch_id:
            await discard_incomplete_import_batch(batch_id)
        archive_path = Path(str(row["archive_path"]))
        status = "queued" if archive_path.is_file() else "failed"
        error = None if status == "queued" else "Архив задания потерян после перезапуска"
        async with connect() as db:
            await db.execute(
                """UPDATE library_import_jobs
                   SET status=?, batch_id=NULL, processed=0, total=0, added=0,
                       duplicate_count=0, error_count=0, phase=0, last_error=?,
                       started_at=NULL, heartbeat_at=?
                   WHERE id=?""",
                (status, error, utc_now(), int(row["id"])),
            )
            await db.commit()
        if status == "queued":
            recovered += 1
    return recovered


async def process_next_import_job(bot) -> bool:
    job = await _claim_next_job()
    if job is None:
        return False

    last_notification = {"processed": -1, "phase": -1}

    async def progress_callback(data: dict[str, int]) -> None:
        await _update_job_progress(job.id, data)
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
        await _safe_edit(bot, job, _progress_text(job.id, data))

    await _safe_edit(
        bot,
        job,
        "<b>⏳ Импорт запущен</b>\n\n"
        f"Задание: <b>#{job.id}</b>\n"
        "Архив проверяется в фоновой очереди. Бот продолжает работать — можно пользоваться другими разделами.",
    )

    try:
        result = await import_library_zip(
            Path(job.archive_path),
            job.archive_name,
            job.actor_user_id,
            progress_callback=progress_callback,
        )
    except asyncio.CancelledError:
        # Задание останется processing и будет безопасно восстановлено при следующем запуске.
        raise
    except ValueError as exc:
        await _set_job_failed(job.id, str(exc))
        await _safe_edit(
            bot,
            job,
            f"<b>❌ Импорт не начат</b>\n\n{html.escape(str(exc))}",
            navigation_menu(cancel_callback="library:menu"),
        )
        await _cleanup_archive(job)
        return True
    except Exception as exc:
        logger.exception("Library import job #%s failed", job.id)
        async with connect() as db:
            cur = await db.execute("SELECT batch_id FROM library_import_jobs WHERE id=?", (job.id,))
            row = await cur.fetchone()
        if row and row["batch_id"] is not None:
            await discard_incomplete_import_batch(int(row["batch_id"]))
        await _set_job_failed(job.id, str(exc))
        await _safe_edit(
            bot,
            job,
            "<b>❌ Импорт остановлен</b>\n\n"
            f"Причина: {html.escape(str(exc)[:1000])}\n\n"
            "Архив не был опубликован. Можно исправить причину и загрузить его повторно.",
            navigation_menu(cancel_callback="library:menu"),
        )
        await _cleanup_archive(job)
        return True

    await _set_job_completed(job.id, result)
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
    while True:
        try:
            processed = await process_next_import_job(bot)
            if not processed:
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Library import queue loop error")
            await asyncio.sleep(3)
