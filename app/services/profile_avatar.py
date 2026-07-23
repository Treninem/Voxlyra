from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path, PurePosixPath

from aiogram import Bot

from app.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_ROOT = Path(str(settings.PROFILE_AVATAR_STORAGE_ROOT or "data/profile_avatars"))
if not AVATAR_ROOT.is_absolute():
    AVATAR_ROOT = PROJECT_ROOT / AVATAR_ROOT
_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_REFRESH_SECONDS = 6 * 60 * 60
_avatar_locks: dict[int, asyncio.Lock] = {}


def _avatar_suffix(file_path: str | None) -> str:
    suffix = PurePosixPath(file_path or "").suffix.lower()
    return suffix if suffix in _ALLOWED_SUFFIXES else ".jpg"


def _cached_avatar(telegram_id: int, *, fresh_only: bool = False) -> Path | None:
    AVATAR_ROOT.mkdir(parents=True, exist_ok=True)
    candidates = sorted(
        (AVATAR_ROOT / f"{int(telegram_id)}{suffix}" for suffix in _ALLOWED_SUFFIXES),
        key=lambda path: path.suffix,
    )
    for path in candidates:
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        if fresh_only and time.time() - path.stat().st_mtime > _REFRESH_SECONDS:
            continue
        return path
    return None


async def ensure_profile_avatar(telegram_id: int) -> Path | None:
    """Return a cached Telegram profile photo, refreshing it without exposing Bot API URLs."""
    telegram_id = int(telegram_id)
    fresh = _cached_avatar(telegram_id, fresh_only=True)
    if fresh:
        return fresh

    lock = _avatar_locks.setdefault(telegram_id, asyncio.Lock())
    async with lock:
        fresh = _cached_avatar(telegram_id, fresh_only=True)
        if fresh:
            return fresh
        stale = _cached_avatar(telegram_id)
        if not settings.BOT_TOKEN:
            return stale

        bot = Bot(token=settings.BOT_TOKEN)
        temporary: Path | None = None
        try:
            photos = await bot.get_user_profile_photos(user_id=telegram_id, offset=0, limit=1)
            if not photos.photos:
                return None
            largest = max(
                photos.photos[0],
                key=lambda item: int(getattr(item, "width", 0) or 0) * int(getattr(item, "height", 0) or 0),
            )
            telegram_file = await bot.get_file(largest.file_id)
            if not telegram_file.file_path:
                return stale
            suffix = _avatar_suffix(telegram_file.file_path)
            destination = AVATAR_ROOT / f"{telegram_id}{suffix}"
            temporary = AVATAR_ROOT / f".{telegram_id}{suffix}.part"
            temporary.unlink(missing_ok=True)
            await bot.download_file(telegram_file.file_path, destination=temporary)
            if not temporary.is_file() or temporary.stat().st_size <= 0:
                return stale
            temporary.replace(destination)
            for other_suffix in _ALLOWED_SUFFIXES:
                other = AVATAR_ROOT / f"{telegram_id}{other_suffix}"
                if other != destination:
                    other.unlink(missing_ok=True)
            return destination
        except Exception:
            logger.exception("Could not refresh Telegram profile photo telegram_id=%s", telegram_id)
            return stale
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            await bot.session.close()
