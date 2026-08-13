from __future__ import annotations

import asyncio
import html
import logging
from typing import Any

from aiogram import Bot
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.github_source_publish import GitHubSourcePublishError, publish_source_package_zip
from app.services.github_source_upload import (
    GitHubSourceUploadError,
    assemble_github_source_upload,
    claim_github_source_finish,
    cleanup_github_source_upload,
    create_github_source_upload,
    load_github_source_upload,
    release_github_source_finish,
    save_github_source_chunk,
    verify_github_source_upload_token,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _fail(detail: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail=str(detail)[:1500])


def _require_enabled() -> None:
    if not bool(settings.GITHUB_SOURCE_WRITE_ENABLED):
        raise _fail("Source ZIP → GitHub выключен", 403)
    if not str(settings.GITHUB_SOURCE_WRITE_TOKEN or "").strip():
        raise _fail("GITHUB_SOURCE_WRITE_TOKEN не настроен", 503)


def _verify(token: str):
    _require_enabled()
    try:
        return verify_github_source_upload_token(token)
    except GitHubSourceUploadError as exc:
        raise _fail(str(exc), 403) from exc


def _token_header(value: str | None) -> str:
    token = str(value or "").strip()
    if not token:
        raise _fail("Нет защищённого токена source-загрузки", 401)
    return token


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.get("/github-source-upload", response_class=HTMLResponse, include_in_schema=False)
async def github_source_upload_page(request: Request, token: str = ""):
    verified = _verify(token)
    response = templates.TemplateResponse(
        request=request,
        name="github_source_upload.html",
        context={
            "project_name": settings.PROJECT_NAME,
            "upload_token": token,
            "max_package_mb": max(1, int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB or 512)),
            "repository": settings.GITHUB_IMPORT_REPOSITORY,
            "branch": settings.GITHUB_IMPORT_BRANCH,
            "telegram_id": verified.telegram_id,
        },
    )
    return _no_store(response)


@router.post("/api/github-source-upload/start", include_in_schema=False)
async def github_source_upload_start(
    request: Request,
    x_vox_source_token: str | None = Header(default=None),
):
    token = _verify(_token_header(x_vox_source_token))
    try:
        payload: dict[str, Any] = await request.json()
        filename = str(payload.get("filename") or "source.zip")
        total_size = int(payload.get("total_size") or 0)
        meta = await asyncio.to_thread(
            create_github_source_upload,
            token=token,
            filename=filename,
            total_size=total_size,
        )
    except GitHubSourceUploadError as exc:
        raise _fail(str(exc)) from exc
    except (TypeError, ValueError):
        raise _fail("Параметры source-загрузки повреждены")
    response = JSONResponse(
        {
            "ok": True,
            "upload_id": meta["upload_id"],
            "chunk_size": int(meta["chunk_size"]),
            "received": list(meta.get("received") or []),
            "total_size": int(meta["total_size"]),
        }
    )
    return _no_store(response)


@router.get("/api/github-source-upload/{upload_id}", include_in_schema=False)
async def github_source_upload_status(
    upload_id: str,
    x_vox_source_token: str | None = Header(default=None),
):
    token = _verify(_token_header(x_vox_source_token))
    try:
        meta = await asyncio.to_thread(load_github_source_upload, upload_id, token=token)
    except GitHubSourceUploadError as exc:
        raise _fail(str(exc), 404) from exc
    response = JSONResponse(
        {
            "ok": True,
            "upload_id": meta["upload_id"],
            "chunk_size": int(meta["chunk_size"]),
            "received": list(meta.get("received") or []),
            "total_size": int(meta["total_size"]),
            "filename": meta["filename"],
        }
    )
    return _no_store(response)


@router.post("/api/github-source-upload/{upload_id}/chunk/{chunk_index}", include_in_schema=False)
async def github_source_upload_chunk(
    request: Request,
    upload_id: str,
    chunk_index: int,
    x_vox_source_token: str | None = Header(default=None),
):
    token = _verify(_token_header(x_vox_source_token))
    body = await request.body()
    if len(body) > 2 * 1024 * 1024:
        raise _fail("Часть source-загрузки слишком большая", 413)
    try:
        meta = await asyncio.to_thread(
            save_github_source_chunk,
            upload_id,
            token=token,
            index=chunk_index,
            data=body,
        )
    except GitHubSourceUploadError as exc:
        raise _fail(str(exc)) from exc
    response = JSONResponse(
        {
            "ok": True,
            "received": list(meta.get("received") or []),
            "total_size": int(meta["total_size"]),
        }
    )
    return _no_store(response)


async def _notify_owner(chat_id: int, text: str) -> None:
    if not settings.BOT_TOKEN or not chat_id:
        return
    bot = Bot(token=settings.BOT_TOKEN)
    try:
        await bot.send_message(chat_id, text)
    except Exception:
        logger.exception("Could not send source-upload completion notice")
    finally:
        await bot.session.close()


@router.post("/api/github-source-upload/{upload_id}/finish", include_in_schema=False)
async def github_source_upload_finish(
    upload_id: str,
    x_vox_source_token: str | None = Header(default=None),
):
    token = _verify(_token_header(x_vox_source_token))
    claimed = False
    try:
        claimed = await asyncio.to_thread(claim_github_source_finish, upload_id, token=token)
        if not claimed:
            raise _fail("Source-пакет уже собирается или публикуется", 409)
        archive = await asyncio.to_thread(assemble_github_source_upload, upload_id, token=token)
        result = await publish_source_package_zip(token.telegram_id, archive)
    except HTTPException:
        raise
    except (GitHubSourceUploadError, GitHubSourcePublishError) as exc:
        raise _fail(str(exc)) from exc
    except Exception as exc:
        secret = str(settings.GITHUB_SOURCE_WRITE_TOKEN or "")
        safe = str(exc).replace(secret, "[REDACTED]") if secret else str(exc)
        logger.exception("Direct source ZIP publication failed")
        raise _fail(f"Неожиданная ошибка source-публикации: {safe[:900]}", 500) from exc
    finally:
        if claimed:
            await asyncio.to_thread(release_github_source_finish, upload_id)

    await asyncio.to_thread(cleanup_github_source_upload, upload_id)
    asyncio.create_task(
        _notify_owner(
            token.chat_id,
            "✅ Source-пакет опубликован в GitHub\n"
            f"Пакет: {html.escape(str(result['package_id']))}\n"
            f"Commit: {html.escape(str(result['commit_sha'])[:12])}\n"
            "Import index: enabled=true",
        )
    )
    response = JSONResponse(
        {
            "ok": True,
            "package_id": result["package_id"],
            "commit_sha": result["commit_sha"],
            "repository": result["repository"],
            "branch": result["branch"],
            "file_count": int(result["file_count"]),
            "enabled": bool(result.get("enabled")),
        }
    )
    return _no_store(response)


# Public pull bridge for the two explicitly approved canonical owner books only.
# The destination repository is public content storage, therefore no write token
# is exposed and no arbitrary book id/title/path can be requested.
from app.canonical_source_export_web import router as canonical_source_export_router
router.include_router(canonical_source_export_router)
