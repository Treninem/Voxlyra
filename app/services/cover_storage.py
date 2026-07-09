from __future__ import annotations

import asyncio
import logging
from pathlib import Path, PurePosixPath

from aiogram import Bot

from app.config import settings
from app.db import list_books_missing_cover_files, update_book_cover_path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COVER_ROOT = PROJECT_ROOT / "storage" / "covers"
_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_cover_locks: dict[int, asyncio.Lock] = {}


def _cover_suffix(telegram_file_path: str | None) -> str:
    suffix = PurePosixPath(telegram_file_path or "").suffix.lower()
    return suffix if suffix in _ALLOWED_SUFFIXES else ".jpg"


def _safe_cover_candidate(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    raw = Path(str(path_value)).expanduser()
    candidates = [raw]
    if not raw.is_absolute():
        candidates.extend((PROJECT_ROOT / raw, COVER_ROOT / raw.name))
    else:
        candidates.append(COVER_ROOT / raw.name)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def find_cover_file(book_id: int, cover_path: str | None = None) -> Path | None:
    """Находит обложку даже после переноса проекта или смены рабочего каталога."""
    direct = _safe_cover_candidate(cover_path)
    if direct:
        return direct
    COVER_ROOT.mkdir(parents=True, exist_ok=True)
    for suffix in sorted(_ALLOWED_SUFFIXES):
        candidate = (COVER_ROOT / f"{int(book_id)}{suffix}").resolve()
        if candidate.is_file():
            return candidate
    return None


async def download_book_cover(bot: Bot, book_id: int, file_id: str) -> str:
    """Скачивает Telegram-обложку в постоянную папку и сохраняет переносимый путь."""
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

    # В БД хранится относительный путь: он не ломается после Redeploy или смены /app.
    try:
        stored_path = destination.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        # В тестах и нестандартных установках папка хранения может быть вне корня проекта.
        stored_path = destination.resolve().as_posix()
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
    """Возвращает локальную обложку; при необходимости восстанавливает её из Telegram."""
    existing = find_cover_file(book_id, cover_path)
    if existing:
        relative = existing.relative_to(PROJECT_ROOT).as_posix() if existing.is_relative_to(PROJECT_ROOT) else existing.as_posix()
        if str(cover_path or "") != relative:
            await update_book_cover_path(book_id, relative)
        return existing
    if not cover_file_id or (bot is None and not settings.BOT_TOKEN):
        return None

    lock = _cover_locks.setdefault(int(book_id), asyncio.Lock())
    async with lock:
        existing = find_cover_file(book_id, cover_path)
        if existing:
            return existing
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
    """Восстанавливает обложки, отсутствующие на диске или имеющие устаревший путь."""
    restored = 0
    failed = 0
    for row in await list_books_missing_cover_files(limit=limit):
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
