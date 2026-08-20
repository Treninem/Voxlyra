from __future__ import annotations

import logging

from aiogram import Bot
from fastapi import APIRouter, Body, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import settings
from app.db import get_book, record_owner_channel_promotion
from app.services.cross_platform_publication import post_book_to_vk_wall
from app.services.profile_avatar import (
    custom_profile_avatar,
    delete_custom_profile_avatar,
    save_custom_profile_avatar,
)
from app.services.publication import post_book_to_channel
from app.services.tma_auth import TMAAuthError, authenticate_init_data

router = APIRouter()
logger = logging.getLogger(__name__)
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024
_NO_CACHE = {
    "Cache-Control": "private, no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Content-Type-Options": "nosniff",
}


async def _current_user(init_data: str | None):
    try:
        return await authenticate_init_data(init_data or "")
    except TMAAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers=_NO_CACHE) from exc


async def _current_owner(init_data: str | None):
    user = await _current_user(init_data)
    if not settings.is_owner_identity(user.telegram_id):
        raise HTTPException(status_code=403, detail="Это действие доступно только владельцу.", headers=_NO_CACHE)
    return user


def _repost_target(payload: dict[str, object] | None) -> str:
    raw = str((payload or {}).get("target") or "both").strip().lower()
    aliases = {
        "both": "both",
        "all": "both",
        "telegram+vk": "both",
        "tg+vk": "both",
        "telegram": "telegram",
        "tg": "telegram",
        "vk": "vk",
    }
    target = aliases.get(raw)
    if target is None:
        raise HTTPException(status_code=400, detail="Неизвестная платформа переопубликации.", headers=_NO_CACHE)
    return target


@router.get("/api/me/custom-avatar", include_in_schema=False)
async def get_custom_profile_avatar(
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    path = custom_profile_avatar(user.app_user_id)
    if not path:
        raise HTTPException(status_code=404, detail="Свой аватар не установлен.", headers=_NO_CACHE)
    return FileResponse(path, media_type="image/webp", headers=_NO_CACHE)


@router.post("/api/me/custom-avatar", include_in_schema=False)
async def upload_custom_profile_avatar(
    avatar: UploadFile = File(...),
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    content_type = str(avatar.content_type or "").lower()
    if content_type and content_type not in {"image/jpeg", "image/jpg", "image/png", "image/webp"}:
        await avatar.close()
        raise HTTPException(status_code=400, detail="Выберите фотографию JPG, PNG или WEBP.", headers=_NO_CACHE)
    try:
        payload = await avatar.read(_MAX_UPLOAD_BYTES + 1)
    finally:
        await avatar.close()
    if len(payload) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Аватар должен быть не больше 8 МБ.", headers=_NO_CACHE)
    try:
        await save_custom_profile_avatar(user.app_user_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc), headers=_NO_CACHE) from exc
    return {"ok": True, "custom_avatar": True, "platform": user.platform}


@router.delete("/api/me/custom-avatar", include_in_schema=False)
async def reset_custom_profile_avatar(
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    removed = await delete_custom_profile_avatar(user.app_user_id)
    return {"ok": True, "removed": bool(removed), "fallback": user.platform}


@router.post("/api/control/repost-platforms/{book_id}", include_in_schema=False)
async def repost_book_to_all_platform_channels(
    book_id: int,
    x_telegram_init_data: str | None = Header(default=None),
    payload: dict[str, object] | None = Body(default=None),
):
    """Owner action: repeat a published book in both platforms or one selected platform."""
    user = await _current_owner(x_telegram_init_data)
    target = _repost_target(payload)
    publish_vk = target in {"both", "vk"}
    publish_telegram = target in {"both", "telegram"}

    book = await get_book(int(book_id))
    if not book:
        raise HTTPException(status_code=404, detail="Книга не найдена.", headers=_NO_CACHE)
    if str(book["publication_status"] or "") != "published":
        raise HTTPException(
            status_code=409,
            detail="Повторно публиковать можно только уже опубликованную книгу.",
            headers=_NO_CACHE,
        )

    # Requested platforms are delivered independently. With the default target
    # both paths run even if one fails; a single-platform repost never touches
    # the platform that the owner did not select.
    vk_status = "skipped"
    vk_error = ""
    if publish_vk:
        vk_status = "failed"
        try:
            vk_status = await post_book_to_vk_wall(
                int(book_id),
                actor_user_id=user.app_user_id,
                force=True,
            )
        except Exception as exc:
            vk_error = f"{type(exc).__name__}: {exc}"[:1000]
            logger.exception("Unexpected VK repost failure book_id=%s", int(book_id))

    telegram_status = "skipped"
    telegram_error = ""
    if publish_telegram:
        telegram_status = "not_configured"
        if settings.BOT_TOKEN:
            bot: Bot | None = None
            try:
                bot = Bot(token=settings.BOT_TOKEN)
                tg_result = await post_book_to_channel(
                    bot,
                    int(book_id),
                    actor_user_id=user.app_user_id,
                    force=True,
                )
                telegram_status = str(tg_result.channel_status or "failed")
                telegram_error = str(tg_result.channel_error or "")
            except Exception as exc:
                telegram_status = "failed"
                telegram_error = f"{type(exc).__name__}: {exc}"[:1000]
                logger.exception("Unexpected Telegram repost failure book_id=%s", int(book_id))
            finally:
                if bot is not None:
                    try:
                        await bot.session.close()
                    except Exception:
                        logger.exception("Could not close Telegram bot session after repost book_id=%s", int(book_id))

        try:
            await record_owner_channel_promotion(
                int(book_id),
                user.app_user_id,
                sent=telegram_status == "sent",
                error=telegram_error,
            )
        except Exception:
            # Delivery outcome must still be returned even if a secondary audit write fails.
            logger.exception("Could not record owner channel promotion book_id=%s", int(book_id))

    requested_statuses = []
    if publish_telegram:
        requested_statuses.append(telegram_status)
    if publish_vk:
        requested_statuses.append(str(vk_status))

    return {
        "ok": bool(requested_statuses) and all(status == "sent" for status in requested_statuses),
        "book_id": int(book_id),
        "requested_from": user.platform,
        "target": target,
        "telegram": {"status": telegram_status, "error": telegram_error},
        "vk": {"status": str(vk_status), "error": vk_error},
    }
