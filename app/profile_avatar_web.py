from __future__ import annotations

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services.profile_avatar import (
    custom_profile_avatar,
    delete_custom_profile_avatar,
    save_custom_profile_avatar,
)
from app.services.tma_auth import TMAAuthError, authenticate_init_data

router = APIRouter()
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
