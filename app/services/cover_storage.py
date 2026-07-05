import logging
from pathlib import Path, PurePosixPath

from aiogram import Bot

from app.db import list_books_missing_cover_files, update_book_cover_path

logger = logging.getLogger(__name__)

COVER_ROOT = Path("storage/covers")
_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _cover_suffix(telegram_file_path: str | None) -> str:
    suffix = PurePosixPath(telegram_file_path or "").suffix.lower()
    return suffix if suffix in _ALLOWED_SUFFIXES else ".jpg"


async def download_book_cover(bot: Bot, book_id: int, file_id: str) -> str:
    """Download a Telegram photo to persistent storage and return its DB path."""
    telegram_file = await bot.get_file(file_id)
    if not telegram_file.file_path:
        raise RuntimeError("Telegram did not return a file path for the cover.")

    COVER_ROOT.mkdir(parents=True, exist_ok=True)
    suffix = _cover_suffix(telegram_file.file_path)
    destination = COVER_ROOT / f"{int(book_id)}{suffix}"
    temporary = COVER_ROOT / f".{int(book_id)}{suffix}.part"

    temporary.unlink(missing_ok=True)
    try:
        await bot.download_file(telegram_file.file_path, destination=temporary)
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("The downloaded cover file is empty.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    stored_path = destination.as_posix()
    if not await update_book_cover_path(book_id, stored_path):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"Book {book_id} was not found while saving its cover.")
    return stored_path


async def restore_missing_book_covers(bot: Bot, limit: int = 500) -> tuple[int, int]:
    """Restore covers created by old versions that saved only Telegram file_id."""
    restored = 0
    failed = 0
    for row in await list_books_missing_cover_files(limit=limit):
        try:
            await download_book_cover(bot, int(row["id"]), str(row["cover_file_id"]))
            restored += 1
        except Exception:
            failed += 1
            logger.exception("Could not restore cover for book_id=%s", row["id"])
    return restored, failed
