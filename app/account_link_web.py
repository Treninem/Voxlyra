from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

from app.db import add_audit
from app.services.account_identity import AccountLinkError
from app.services.account_link_notifications import notify_link_decision, send_link_confirmation_request
from app.services.smart_account_link import (
    cancel_smart_link_request,
    confirm_smart_link_request,
    create_smart_link_request,
    get_incoming_link_request,
    get_source_link_request,
    reject_smart_link_request,
    set_link_request_delivery,
)
from app.services.tma_auth import TMAAuthError, authenticate_init_data

router = APIRouter()
_NO_CACHE = {
    "Cache-Control": "private, no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


async def _current_user(init_data: str | None):
    try:
        return await authenticate_init_data(init_data or "")
    except TMAAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers=_NO_CACHE) from exc


def _link_error(exc: AccountLinkError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers=_NO_CACHE)


@router.post("/api/account-link/request", include_in_schema=False)
async def create_account_link_request(
    request: Request,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    payload = await request.json()
    target = str((payload or {}).get("target") or "").strip()
    try:
        result = await create_smart_link_request(
            source_user_id=user.app_user_id,
            source_platform=user.platform,
            source_external_id=user.external_id,
            source_username=user.username,
            source_full_name=user.full_name,
            target_reference=target,
        )
    except AccountLinkError as exc:
        raise _link_error(exc) from exc
    if result.get("already_linked"):
        return result

    delivered, error = await send_link_confirmation_request(result)
    await set_link_request_delivery(str(result["token"]), delivered=delivered, error=error)
    result["delivery_status"] = "sent" if delivered else "failed"
    result["delivery_error"] = error
    await add_audit(
        user.app_user_id,
        "cross_platform_link_request_created",
        "account",
        str(user.app_user_id),
        user.platform,
        f"target_platform={result.get('target_platform')};delivery={result['delivery_status']}",
    )
    return result


@router.get("/api/account-link/request/{token}", include_in_schema=False)
async def account_link_request_status(
    token: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    result = await get_source_link_request(user.app_user_id, token)
    if not result:
        raise HTTPException(status_code=404, detail="Запрос не найден.", headers=_NO_CACHE)
    return result


@router.get("/api/account-link/incoming", include_in_schema=False)
async def incoming_account_link_request(
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    result = await get_incoming_link_request(
        target_platform=user.platform,
        target_external_id=user.external_id,
    )
    return {"ok": True, "request": result}


@router.post("/api/account-link/request/{token}/confirm", include_in_schema=False)
async def confirm_account_link_request(
    token: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    try:
        result = await confirm_smart_link_request(
            token=token,
            target_user_id=user.app_user_id,
            target_platform=user.platform,
            target_external_id=user.external_id,
        )
    except AccountLinkError as exc:
        raise _link_error(exc) from exc
    await add_audit(
        int(result["canonical_user_id"]),
        "cross_platform_accounts_smart_merged",
        "account",
        str(result["canonical_user_id"]),
        result.get("source_platform", ""),
        result.get("target_platform", ""),
    )
    await notify_link_decision(result, confirmed=True)
    return result


@router.post("/api/account-link/request/{token}/reject", include_in_schema=False)
async def reject_account_link_request(
    token: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    try:
        result = await reject_smart_link_request(
            token=token,
            target_platform=user.platform,
            target_external_id=user.external_id,
        )
    except AccountLinkError as exc:
        raise _link_error(exc) from exc
    await notify_link_decision(result, confirmed=False)
    return {"ok": True, "status": "rejected"}


@router.post("/api/account-link/request/{token}/cancel", include_in_schema=False)
async def cancel_account_link_request(
    token: str,
    x_telegram_init_data: str | None = Header(default=None),
):
    user = await _current_user(x_telegram_init_data)
    try:
        return await cancel_smart_link_request(token=token, source_user_id=user.app_user_id)
    except AccountLinkError as exc:
        raise _link_error(exc) from exc
