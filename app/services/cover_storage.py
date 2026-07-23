from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path, PurePosixPath

from aiogram import Bot

from app.config import settings
from app.db import connect, list_books_missing_cover_files, update_book_cover_path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COVER_ROOT = Path(str(settings.BOOK_COVER_STORAGE_ROOT or "data/covers"))
if not COVER_ROOT.is_absolute():
    COVER_ROOT = PROJECT_ROOT / COVER_ROOT
LIBRARY_ROOT = Path(str(settings.LIBRARY_STORAGE_ROOT or "data/library_storage"))
if not LIBRARY_ROOT.is_absolute():
    LIBRARY_ROOT = PROJECT_ROOT / LIBRARY_ROOT
_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_cover_locks: dict[int, asyncio.Lock] = {}


def _cover_suffix(telegram_file_path: str | None) -> str:
    suffix = PurePosixPath(telegram_file_path or "").suffix.lower()
    return suffix if suffix in _ALLOWED_SUFFIXES else ".jpg"


def _portable(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return path.resolve().as_posix()


def _path_candidates(path_value: str | None) -> list[Path]:
    if not path_value:
        return []
    raw_text = str(path_value).strip().replace("\\", "/")
    raw = Path(raw_text).expanduser()
    candidates = [raw]
    if not raw.is_absolute():
        candidates.extend((PROJECT_ROOT / raw, COVER_ROOT / raw.name))
    else:
        candidates.append(COVER_ROOT / raw.name)
    normalized = "/" + raw_text.lstrip("/")
    for marker in ("/data/library_storage/", "/storage/library/"):
        if marker in normalized:
            tail = normalized.split(marker, 1)[1]
            candidates.append(LIBRARY_ROOT / tail)
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def _safe_cover_candidate(path_value: str | None) -> Path | None:
    for candidate in _path_candidates(path_value):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved.suffix.lower() in _ALLOWED_SUFFIXES and resolved.stat().st_size > 0:
            return resolved
    return None


def find_cover_file(book_id: int, cover_path: str | None = None) -> Path | None:
    """Find a cover after project moves, redeploys, or migration from legacy import storage."""
    direct = _safe_cover_candidate(cover_path)
    if direct:
        return direct
    COVER_ROOT.mkdir(parents=True, exist_ok=True)
    for suffix in sorted(_ALLOWED_SUFFIXES):
        candidate = (COVER_ROOT / f"{int(book_id)}{suffix}").resolve()
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _mirror_cover(book_id: int, source: Path) -> Path | None:
    if not source.is_file() or source.suffix.lower() not in _ALLOWED_SUFFIXES:
        return None
    COVER_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    destination = COVER_ROOT / f"{int(book_id)}{suffix}"
    if source.resolve() == destination.resolve():
        return destination
    temporary = COVER_ROOT / f".{int(book_id)}{suffix}.part"
    temporary.unlink(missing_ok=True)
    try:
        shutil.copy2(source, temporary)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            return None
        temporary.replace(destination)
        for other_suffix in _ALLOWED_SUFFIXES:
            other = COVER_ROOT / f"{int(book_id)}{other_suffix}"
            if other != destination:
                other.unlink(missing_ok=True)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


async def _recover_imported_cover(book_id: int, cover_path: str | None) -> Path | None:
    direct = _safe_cover_candidate(cover_path)
    if direct:
        return await asyncio.to_thread(_mirror_cover, book_id, direct)
    async with connect() as db:
        cur = await db.execute(
            "SELECT source_file_name, cover_path FROM books WHERE id=? LIMIT 1",
            (int(book_id),),
        )
        row = await cur.fetchone()
    if not row:
        return None
    for value in (row["cover_path"], row["source_file_name"]):
        for candidate in _path_candidates(str(value or "")):
            folder = candidate.parent if candidate.suffix else candidate
            if not folder.is_dir():
                continue
            for suffix in sorted(_ALLOWED_SUFFIXES):
                matches = sorted(folder.glob(f"cover*{suffix}"))
                for match in matches:
                    if match.is_file() and match.stat().st_size > 0:
                        return await asyncio.to_thread(_mirror_cover, book_id, match)
    return None


async def download_book_cover(bot: Bot, book_id: int, file_id: str) -> str:
    """Download a Telegram cover into persistent storage and save a portable path."""
    telegram_file = await bot.get_file(file_id)
    if not telegram_file.file_path:
        raise RuntimeError("Telegram не вернул путь к файлу обложки.")

    COVER_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = _cover_suffix(telegram_file.file_path)
    destination = COVER_ROOT / f"{int(book_id)}{suffix}"
    temporary = COVER_ROOT / f".{int(book_id)}{suffix}.part"

    temporary.unlink(missing_ok=True)
    try:
        await bot.download_file(telegram_file.file_path, destination=temporary)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("Загруженный файл обложки пуст.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    stored_path = _portable(destination)
    if not await update_book_cover_path(book_id, stored_path):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Книга {book_id} не найдена при сохранении обложки.")
    return stored_path


async def ensure_book_cover_file(
    *,
    book_id: int,
    cover_file_id: str | None,
    cover_path: str | None,
    bot: Bot | None = None,
) -> Path | None:
    """Return a stable local cover and repair imported cover paths when possible."""
    existing = find_cover_file(book_id, cover_path)
    if existing and existing.parent.resolve() == COVER_ROOT.resolve():
        relative = _portable(existing)
        if str(cover_path or "") != relative:
            await update_book_cover_path(book_id, relative)
        return existing

    lock = _cover_locks.setdefault(int(book_id), asyncio.Lock())
    async with lock:
        existing = find_cover_file(book_id, cover_path)
        if existing:
            canonical = await asyncio.to_thread(_mirror_cover, int(book_id), existing)
            if canonical:
                await update_book_cover_path(book_id, _portable(canonical))
                return canonical
        repaired = await _recover_imported_cover(int(book_id), cover_path)
        if repaired:
            await update_book_cover_path(book_id, _portable(repaired))
            return repaired
        if not cover_file_id or (bot is None and not settings.BOT_TOKEN):
            return None
        owns_bot = bot is None
        delivery_bot = bot or Bot(token=settings.BOT_TOKEN)
        try:
            stored = await download_book_cover(delivery_bot, int(book_id), str(cover_file_id))
            return find_cover_file(book_id, stored)
        except Exception:
            logger.exception("Не удалось восстановить обложку book_id=%s", book_id)
            return None
        finally:
            if owns_bot:
                await delivery_bot.session.close()


async def restore_missing_book_covers(bot: Bot, limit: int = 500) -> tuple[int, int]:
    """Restore missing covers from imported storage first, then Telegram when available."""
    restored = 0
    failed = 0
    async with connect() as db:
        cur = await db.execute(
            "SELECT id, cover_file_id, cover_path FROM books WHERE publication_status<>'deleted' ORDER BY id LIMIT ?",
            (max(1, int(limit)),),
        )
        rows = await cur.fetchall()
    for row in rows:
        path = await ensure_book_cover_file(
            book_id=int(row["id"]),
            cover_file_id=str(row["cover_file_id"] or ""),
            cover_path=str(row["cover_path"] or ""),
            bot=bot,
        )
        if path:
            restored += 1
        else:
            failed += 1
    return restored, failed
