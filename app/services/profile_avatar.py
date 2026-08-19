from __future__ import annotations

import asyncio
import io
import logging
import time
from pathlib import Path, PurePosixPath

from aiogram import Bot
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AVATAR_ROOT = Path(str(settings.PROFILE_AVATAR_STORAGE_ROOT or "data/profile_avatars"))
if not AVATAR_ROOT.is_absolute():
    AVATAR_ROOT = PROJECT_ROOT / AVATAR_ROOT
CUSTOM_AVATAR_ROOT = AVATAR_ROOT / "custom"
_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_REFRESH_SECONDS = 6 * 60 * 60
_CUSTOM_AVATAR_MAX_BYTES = 8 * 1024 * 1024
_CUSTOM_AVATAR_SIZE = 512
_CUSTOM_AVATAR_MAX_PIXELS = 36_000_000
_avatar_locks: dict[int, asyncio.Lock] = {}
_custom_avatar_locks: dict[int, asyncio.Lock] = {}


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


def custom_profile_avatar(user_id: int) -> Path | None:
    """Return the user-selected VoxLyra avatar shared by linked Telegram/VK identities."""
    user_id = int(user_id)
    if user_id <= 0:
        return None
    path = CUSTOM_AVATAR_ROOT / f"{user_id}.webp"
    return path if path.is_file() and path.stat().st_size > 0 else None


def _prepare_custom_avatar(content: bytes) -> bytes:
    if not content:
        raise ValueError("Файл аватара пуст.")
    if len(content) > _CUSTOM_AVATAR_MAX_BYTES:
        raise ValueError("Аватар должен быть не больше 8 МБ.")
    try:
        with Image.open(io.BytesIO(content)) as source:
            source.load()
            width, height = source.size
            if width < 64 or height < 64:
                raise ValueError("Аватар должен быть не меньше 64×64 пикселей.")
            if width * height > _CUSTOM_AVATAR_MAX_PIXELS:
                raise ValueError("Изображение слишком большое.")
            image = ImageOps.exif_transpose(source)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            image = ImageOps.fit(
                image,
                (_CUSTOM_AVATAR_SIZE, _CUSTOM_AVATAR_SIZE),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=90, method=6)
            payload = output.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Поддерживаются фотографии JPG, PNG и WEBP.") from exc
    if not payload:
        raise ValueError("Не удалось подготовить аватар.")
    return payload


async def save_custom_profile_avatar(user_id: int, content: bytes) -> Path:
    """Validate, normalize and atomically save one canonical user avatar."""
    user_id = int(user_id)
    if user_id <= 0:
        raise ValueError("Не удалось определить профиль пользователя.")
    lock = _custom_avatar_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        payload = await asyncio.to_thread(_prepare_custom_avatar, bytes(content))
        CUSTOM_AVATAR_ROOT.mkdir(parents=True, exist_ok=True)
        destination = CUSTOM_AVATAR_ROOT / f"{user_id}.webp"
        temporary = CUSTOM_AVATAR_ROOT / f".{user_id}.webp.part"
        try:
            temporary.write_bytes(payload)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


async def delete_custom_profile_avatar(user_id: int) -> bool:
    """Remove a custom avatar so the profile falls back to the current platform photo."""
    user_id = int(user_id)
    if user_id <= 0:
        return False
    lock = _custom_avatar_locks.setdefault(user_id, asyncio.Lock())
    async with lock:
        path = CUSTOM_AVATAR_ROOT / f"{user_id}.webp"
        existed = path.is_file()
        path.unlink(missing_ok=True)
        return existed


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
