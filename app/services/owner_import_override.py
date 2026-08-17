from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import PurePosixPath
from typing import Any

from app.config import settings
from app.db import connect as app_connect, utc_now

logger = logging.getLogger(__name__)

_OWNER_IMPORT: ContextVar[bool] = ContextVar("voxlyra_owner_import", default=False)
_OWNER_COMIC_IMPORT: ContextVar[bool] = ContextVar("voxlyra_owner_comic_import", default=False)
_INSTALLED = False


def owner_import_active() -> bool:
    return bool(_OWNER_IMPORT.get())


class _CursorProxy:
    def __init__(self, cursor: Any, *, hide_row: bool = False) -> None:
        self._cursor = cursor
        self._hide_row = hide_row

    async def fetchone(self):
        if self._hide_row:
            return None
        return await self._cursor.fetchone()

    async def fetchall(self):
        if self._hide_row:
            return []
        return await self._cursor.fetchall()

    def __getattr__(self, name: str):
        return getattr(self._cursor, name)


class _ConnectionProxy:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def execute(self, sql: str, parameters: Any = None):
        text = " ".join(str(sql or "").split()).lower()
        hide = False
        if _OWNER_IMPORT.get() and "from library_import_batches" in text and "where archive_hash=?" in text:
            hide = True
        if _OWNER_COMIC_IMPORT.get() and "from books" in text and "normalized_title=?" in text:
            # Only the exact comic duplicate probe is hidden. Other book queries
            # remain untouched so the existing importer keeps its safety checks.
            hide = "select id from books where publication_status!='deleted' and normalized_title=?" in text
        cursor = await self._db.execute(sql, parameters) if parameters is not None else await self._db.execute(sql)
        return _CursorProxy(cursor, hide_row=hide)

    def __getattr__(self, name: str):
        return getattr(self._db, name)


@asynccontextmanager
async def _owner_connect():
    async with app_connect() as db:
        yield _ConnectionProxy(db)


def _metadata_from_zip(zip_path, folder_info: dict[str, Any]) -> dict[str, Any]:
    import zipfile

    members = list(folder_info.get("members") or [])
    candidate = next(
        (
            name
            for name in members
            if PurePosixPath(str(name).replace("\\", "/")).name.casefold() == "metadata.json"
        ),
        "",
    )
    if not candidate:
        return {}
    try:
        with zipfile.ZipFile(zip_path) as archive:
            raw = archive.read(candidate)
        data = json.loads(raw.decode("utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


async def _find_existing_comics(zip_path, folders: list[dict[str, Any]]) -> list[tuple[int, str, str, str]]:
    matches: list[tuple[int, str, str, str]] = []
    async with app_connect() as db:
        for folder in folders:
            metadata = _metadata_from_zip(zip_path, folder)
            title = str(metadata.get("title") or "").strip()
            author = str(metadata.get("author") or "").strip()
            if not title or not author:
                continue
            cur = await db.execute(
                """SELECT id FROM books
                   WHERE publication_status!='deleted'
                     AND normalized_title=?
                     AND content_type IN ('comic','manga','manhwa','webtoon','graphic_novel')
                     AND lower(COALESCE(source_author_name,''))=lower(?)
                   ORDER BY id LIMIT 1""",
                (_norm(title), author),
            )
            row = await cur.fetchone()
            if row:
                matches.append((int(row["id"]), str(folder["name"]), title, author))
    return matches


async def _merge_user_book_rows(db: Any, old_id: int, new_id: int) -> None:
    cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [str(row["name"]) for row in await cur.fetchall()]
    excluded = {"books", "chapters", "graphic_chapters", "audio_chapters"}
    for table in tables:
        if table in excluded:
            continue
        try:
            info_cur = await db.execute(f'PRAGMA table_info("{table}")')
            columns = {str(row["name"]) for row in await info_cur.fetchall()}
        except Exception:
            continue
        if "book_id" not in columns:
            continue
        if "user_id" in columns:
            try:
                rows_cur = await db.execute(f'SELECT rowid, * FROM "{table}" WHERE book_id=?', (old_id,))
                rows = await rows_cur.fetchall()
            except Exception:
                continue
            for row in rows:
                rowid = int(row["rowid"])
                user_id = row["user_id"]
                existing_cur = await db.execute(
                    f'SELECT rowid, * FROM "{table}" WHERE user_id=? AND book_id=? LIMIT 1',
                    (user_id, new_id),
                )
                existing = await existing_cur.fetchone()
                if existing is None:
                    await db.execute(f'UPDATE "{table}" SET book_id=? WHERE rowid=?', (new_id, rowid))
                    continue
                # Preserve the furthest known reading/listening position.
                for progress_col in ("position_percent", "position_seconds", "progress_percent", "progress"):
                    if progress_col in columns:
                        old_value = int(row[progress_col] or 0)
                        new_value = int(existing[progress_col] or 0)
                        if old_value > new_value:
                            await db.execute(
                                f'UPDATE "{table}" SET {progress_col}=? WHERE rowid=?',
                                (old_value, int(existing["rowid"])),
                            )
                        break
                await db.execute(f'DELETE FROM "{table}" WHERE rowid=?', (rowid,))
        else:
            if table in {"library_channel_queue"}:
                await db.execute(f'DELETE FROM "{table}" WHERE book_id=?', (old_id,))
            else:
                try:
                    await db.execute(f'UPDATE "{table}" SET book_id=? WHERE book_id=?', (new_id, old_id))
                except Exception:
                    # A secondary uniqueness conflict must not make an otherwise
                    # successful owner replacement fail.
                    logger.warning("Could not merge table %s for comic %s -> %s", table, old_id, new_id)


async def _merge_replaced_comics(batch_id: int, replacements: list[tuple[int, str, str, str]]) -> None:
    if not replacements:
        return
    async with app_connect() as db:
        for old_id, folder_name, title, author in replacements:
            storage_marker = f"/comics/{int(batch_id)}/{folder_name}"
            cur = await db.execute(
                """SELECT id FROM books
                   WHERE import_batch_id=? AND publication_status!='deleted'
                     AND source_file_name LIKE ?
                   ORDER BY id DESC LIMIT 1""",
                (int(batch_id), f"%{storage_marker}"),
            )
            new_row = await cur.fetchone()
            if not new_row:
                cur = await db.execute(
                    """SELECT id FROM books
                       WHERE import_batch_id=? AND publication_status!='deleted'
                         AND normalized_title=?
                         AND lower(COALESCE(source_author_name,''))=lower(?)
                       ORDER BY id DESC LIMIT 1""",
                    (int(batch_id), _norm(title), author),
                )
                new_row = await cur.fetchone()
            if not new_row:
                continue
            new_id = int(new_row["id"])
            if new_id == old_id:
                continue
            await _merge_user_book_rows(db, old_id, new_id)
            # Keep the old record as a hidden rollback/history record instead of
            # deleting it. Reader progress and purchase history therefore remain
            # recoverable even if a future replacement has a problem.
            await db.execute(
                "UPDATE books SET publication_status='deleted', updated_at=? WHERE id=?",
                (utc_now(), old_id),
            )
        await db.commit()


async def _publish_owner_batch(batch_id: int) -> None:
    async with app_connect() as db:
        now = utc_now()
        cur = await db.execute(
            "SELECT id FROM books WHERE import_batch_id=? AND publication_status!='deleted'",
            (int(batch_id),),
        )
        ids = [int(row["id"]) for row in await cur.fetchall()]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        await db.execute(
            f"UPDATE books SET publication_status='published', rights_checked=1, updated_at=? WHERE id IN ({placeholders})",
            [now, *ids],
        )
        await db.execute(
            f"UPDATE chapters SET status='published', updated_at=? WHERE book_id IN ({placeholders}) AND status!='deleted'",
            [now, *ids],
        )
        await db.execute(
            f"UPDATE graphic_chapters SET status='published', updated_at=? WHERE book_id IN ({placeholders}) AND status!='deleted'",
            [now, *ids],
        )
        await db.execute(
            f"UPDATE audio_chapters SET status='published', updated_at=? WHERE book_id IN ({placeholders}) AND status!='deleted'",
            [now, *ids],
        )
        await db.execute(
            "UPDATE library_import_batches SET status='published', completed_at=COALESCE(completed_at, ?) WHERE id=?",
            (now, int(batch_id)),
        )
        await db.commit()


async def _owner_import_wrapper(original, zip_path, archive_name, actor_user_id, progress_callback=None):
    owner = settings.is_system_owner(int(actor_user_id))
    if not owner:
        return await original(zip_path, archive_name, actor_user_id, progress_callback)

    token = _OWNER_IMPORT.set(True)
    try:
        return await original(zip_path, archive_name, actor_user_id, progress_callback)
    finally:
        _OWNER_IMPORT.reset(token)


async def install_owner_import_overrides() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from app.services import library_manager as lm
    from app.services import library_import_queue as queue

    original_validate = lm._validate_import_rights
    original_import = lm.import_library_zip
    original_comics = lm._import_bulk_comics

    def validate_override(metadata: dict[str, Any], folder):
        if _OWNER_IMPORT.get():
            return []
        return original_validate(metadata, folder)

    lm._validate_import_rights = validate_override

    async def owner_comics(*, zip_path, folders, batch_id, actor_user_id, result):
        if not settings.is_system_owner(int(actor_user_id)):
            return await original_comics(
                zip_path=zip_path, folders=folders, batch_id=batch_id,
                actor_user_id=actor_user_id, result=result,
            )
        replacements = await _find_existing_comics(zip_path, folders)
        token = _OWNER_COMIC_IMPORT.set(True)
        try:
            imported = await original_comics(
                zip_path=zip_path, folders=folders, batch_id=batch_id,
                actor_user_id=actor_user_id, result=result,
            )
        finally:
            _OWNER_COMIC_IMPORT.reset(token)
        await _merge_replaced_comics(batch_id, replacements)
        return imported

    lm._import_bulk_comics = owner_comics

    async def import_wrapper(zip_path, archive_name, actor_user_id, progress_callback=None):
        owner = settings.is_system_owner(int(actor_user_id))
        token = _OWNER_IMPORT.set(owner)
        try:
            result = await original_import(zip_path, archive_name, actor_user_id, progress_callback)
            if owner:
                await _publish_owner_batch(int(result.batch_id))
            return result
        finally:
            _OWNER_IMPORT.reset(token)

    # Both references are patched: the queue imported the function directly,
    # while other services call it through library_manager.
    lm.import_library_zip = import_wrapper
    queue.import_library_zip = import_wrapper
    _INSTALLED = True
    logger.info("Owner import override installed: license bypass, repeat import and owner auto-publish enabled")
