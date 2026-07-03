from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import (
    add_comment,
    get_audio_chapter,
    get_book_options,
    get_book_with_counts,
    get_bookmark,
    get_chapter,
    get_listening_progress,
    get_reader_ad_settings,
    get_reading_progress,
    get_user_review,
    has_purchase_access,
    init_db,
    list_audio_chapters_for_book,
    list_catalog_books,
    list_chapters_for_book,
    list_comments_for_chapter,
    list_contextual_book_ads,
    list_reviews_for_book,
    list_user_bookmarks,
    record_reader_ad_event,
    remove_bookmark,
    save_listening_progress,
    save_reading_progress,
    set_bookmark,
    upsert_review,
    user_can_access_audio,
    user_can_access_chapter,
)
from app.services.tma_auth import TMAAuthError, TMAUser, authenticate_init_data
from app.services.diagnostics import diagnostics_summary


def _bot_purchase_url(kind: str, target_id: int) -> str:
    username = settings.BOT_USERNAME.strip().lstrip("@")
    if not username:
        return ""
    return f"https://t.me/{username}?start=buy_{kind}_{target_id}"


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _rows_to_dicts(rows) -> list[dict[str, Any]]:
    return [_row_to_dict(row) for row in rows]


async def _tma_user(init_data: str | None) -> TMAUser:
    try:
        return await authenticate_init_data(init_data or "")
    except TMAAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def create_app() -> FastAPI:
    app = FastAPI(title="Voxlyra Mini App")
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")

    @app.on_event("startup")
    async def startup() -> None:
        await init_db()

    @app.get("/health")
    async def health():
        """Короткая проверка для Bothost и владельца. Не раскрывает токен и секреты."""
        summary = diagnostics_summary()
        return {
            "ok": True,
            "project": settings.PROJECT_NAME,
            "version": settings.PROJECT_VERSION,
            "checks_ok": summary["ok_count"],
            "checks_total": summary["total"],
        }

    @app.get("/readiness")
    async def readiness():
        """Подробная, но безопасная проверка окружения после деплоя."""
        summary = diagnostics_summary()
        return {
            "ok": summary["ok"],
            "checks_ok": summary["ok_count"],
            "checks_total": summary["total"],
            "items": [
                {"code": item.code, "label": item.label, "ok": item.ok, "hint": item.hint if not item.ok else ""}
                for item in summary["items"]
            ],
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        books = await list_catalog_books(limit=30, include_drafts=True)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            {"project_name": settings.PROJECT_NAME, "books": books},
        )

    @app.get("/catalog", response_class=HTMLResponse)
    async def catalog(request: Request):
        books = await list_catalog_books(limit=30, include_drafts=True)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            {"project_name": settings.PROJECT_NAME, "books": books},
        )

    @app.get("/book/{book_id}", response_class=HTMLResponse)
    async def book(request: Request, book_id: int):
        book_row = await get_book_with_counts(book_id)
        chapters = await list_chapters_for_book(book_id) if book_row else []
        audios = await list_audio_chapters_for_book(book_id) if book_row else []
        options = await get_book_options(book_id) if book_row else {}
        return templates.TemplateResponse(
            request,
            "book.html",
            {
                "book": book_row,
                "book_id": book_id,
                "chapters": chapters,
                "audios": audios,
                "options": options,
                "project_name": settings.PROJECT_NAME,
                "bot_username": settings.BOT_USERNAME.strip().lstrip("@"),
            },
        )

    @app.get("/reader/{chapter_id}", response_class=HTMLResponse)
    async def reader(request: Request, chapter_id: int):
        chapter = await get_chapter(chapter_id)
        purchase_url = _bot_purchase_url("chapter", chapter_id) if chapter else ""
        ads = []
        ad_settings = await get_reader_ad_settings()
        if chapter and ad_settings.get("enabled"):
            ads = await list_contextual_book_ads(int(chapter["book_id"]), limit=4)
        return templates.TemplateResponse(
            request,
            "reader.html",
            {
                "chapter": chapter,
                "chapter_id": chapter_id,
                "purchase_url": purchase_url,
                "reader_ads": ads,
                "ad_settings": ad_settings,
                "project_name": settings.PROJECT_NAME,
                "bot_username": settings.BOT_USERNAME.strip().lstrip("@"),
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", {"project_name": settings.PROJECT_NAME})

    @app.get("/audio", response_class=HTMLResponse)
    async def audio_index(request: Request):
        books = await list_catalog_books(limit=30, include_drafts=True)
        return templates.TemplateResponse(request, "audio.html", {"project_name": settings.PROJECT_NAME, "books": books})

    @app.get("/audio/{audio_id}", response_class=HTMLResponse)
    async def audio_player(request: Request, audio_id: int):
        audio = await get_audio_chapter(audio_id)
        return templates.TemplateResponse(
            request,
            "audio_player.html",
            {
                "audio": audio,
                "audio_id": audio_id,
                "purchase_url": _bot_purchase_url("audio", audio_id),
                "project_name": settings.PROJECT_NAME,
            },
        )

    @app.get("/api/me")
    async def api_me(x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        bookmarks = await list_user_bookmarks(user.app_user_id, limit=20)
        return {
            "ok": True,
            "user": {
                "id": user.app_user_id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "full_name": user.full_name,
            },
            "bookmarks": _rows_to_dicts(bookmarks),
        }

    @app.get("/api/book/{book_id}/state")
    async def api_book_state(book_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        bookmark = await get_bookmark(user.app_user_id, book_id)
        review = await get_user_review(user.app_user_id, book_id)
        reviews = await list_reviews_for_book(book_id, limit=20)
        return {
            "ok": True,
            "bookmark": _row_to_dict(bookmark) if bookmark else None,
            "my_review": _row_to_dict(review) if review else None,
            "reviews": _rows_to_dicts(reviews),
        }

    @app.post("/api/book/{book_id}/bookmark")
    async def api_bookmark(book_id: int, payload: dict[str, Any], x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        status = str(payload.get("status") or "reading")
        if status == "remove":
            await remove_bookmark(user.app_user_id, book_id)
            return {"ok": True, "bookmark": None}
        await set_bookmark(user.app_user_id, book_id, status=status)
        bookmark = await get_bookmark(user.app_user_id, book_id)
        return {"ok": True, "bookmark": _row_to_dict(bookmark) if bookmark else None}

    @app.post("/api/book/{book_id}/review")
    async def api_review(book_id: int, payload: dict[str, Any], x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        rating = int(payload.get("rating") or 5)
        text = str(payload.get("text") or "").strip()
        if len(text) > 3000:
            raise HTTPException(status_code=400, detail="Отзыв слишком длинный.")
        await upsert_review(user.app_user_id, book_id, rating, text)
        reviews = await list_reviews_for_book(book_id, limit=20)
        return {"ok": True, "reviews": _rows_to_dicts(reviews)}

    @app.get("/api/reader/{chapter_id}")
    async def api_reader(chapter_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed = await user_can_access_chapter(user.app_user_id, chapter_id)
        purchase_url = _bot_purchase_url("chapter", chapter_id)
        comments = await list_comments_for_chapter(chapter_id, limit=50) if allowed else []
        progress = await get_reading_progress(user.app_user_id, chapter_id) if allowed else 0
        reader_ads = []
        ad_settings = await get_reader_ad_settings()
        if ad_settings.get("enabled"):
            reader_ads = await list_contextual_book_ads(int(chapter["book_id"]), limit=4)
            for ad in reader_ads:
                await record_reader_ad_event(
                    user_id=user.app_user_id,
                    source_book_id=int(chapter["book_id"]),
                    source_chapter_id=chapter_id,
                    promoted_book_id=int(ad["id"]),
                    placement="api_reader",
                    event_type="impression",
                    campaign_id=int(ad["campaign_id"]) if ad["campaign_id"] else None,
                )
        return {
            "ok": True,
            "allowed": allowed,
            "purchase_url": purchase_url,
            "progress_percent": progress,
            "chapter": {
                "id": int(chapter["id"]),
                "book_id": int(chapter["book_id"]),
                "book_title": chapter["book_title"],
                "title": chapter["title"],
                "number": int(chapter["number"]),
                "is_free": int(chapter["is_free"] or 0) == 1,
                "price_stars": int(chapter["price_stars"] or 0),
                "text": chapter["text"] if allowed else "",
            },
            "comments": _rows_to_dicts(comments),
            "reader_ads": _rows_to_dicts(reader_ads),
            "ad_settings": ad_settings,
        }

    @app.post("/api/reader/{chapter_id}/progress")
    async def api_reader_progress(chapter_id: int, payload: dict[str, Any], x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        allowed = await user_can_access_chapter(user.app_user_id, chapter_id)
        if not allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к главе.")
        percent = int(payload.get("position_percent") or 0)
        await save_reading_progress(user.app_user_id, chapter_id, percent)
        return {"ok": True, "position_percent": max(0, min(100, percent))}

    @app.get("/api/reader/{chapter_id}/comments")
    async def api_comments(chapter_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        if not await user_can_access_chapter(user.app_user_id, chapter_id):
            raise HTTPException(status_code=403, detail="Комментарии доступны после открытия главы.")
        comments = await list_comments_for_chapter(chapter_id, limit=50)
        return {"ok": True, "comments": _rows_to_dicts(comments)}

    @app.post("/api/reader/{chapter_id}/comments")
    async def api_add_comment(chapter_id: int, payload: dict[str, Any], x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        if not await user_can_access_chapter(user.app_user_id, chapter_id):
            raise HTTPException(status_code=403, detail="Нельзя комментировать закрытую главу.")
        text = str(payload.get("text") or "").strip()
        if len(text) < 2:
            raise HTTPException(status_code=400, detail="Комментарий слишком короткий.")
        if len(text) > 2000:
            raise HTTPException(status_code=400, detail="Комментарий слишком длинный.")
        await add_comment(user.app_user_id, chapter_id, text)
        comments = await list_comments_for_chapter(chapter_id, limit=50)
        return {"ok": True, "comments": _rows_to_dicts(comments)}

    @app.post("/api/reader/ad-click")
    async def api_reader_ad_click(payload: dict[str, Any], x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        promoted_book_id = int(payload.get("promoted_book_id") or 0)
        source_book_id = payload.get("source_book_id")
        source_chapter_id = payload.get("source_chapter_id")
        campaign_id = payload.get("campaign_id")
        if promoted_book_id <= 0:
            raise HTTPException(status_code=400, detail="Нет рекламируемой книги.")
        await record_reader_ad_event(
            user_id=user.app_user_id,
            source_book_id=int(source_book_id) if source_book_id else None,
            source_chapter_id=int(source_chapter_id) if source_chapter_id else None,
            promoted_book_id=promoted_book_id,
            placement="reader_click",
            event_type="click",
            campaign_id=int(campaign_id) if campaign_id else None,
        )
        return {"ok": True}

    @app.get("/api/audio/{audio_id}/meta")
    async def api_audio_meta(audio_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        audio = await get_audio_chapter(audio_id)
        if not audio:
            raise HTTPException(status_code=404, detail="Аудиоглава не найдена.")
        allowed = await user_can_access_audio(user.app_user_id, audio_id)
        progress = await get_listening_progress(user.app_user_id, audio_id) if allowed else 0
        return {
            "ok": True,
            "allowed": allowed,
            "purchase_url": _bot_purchase_url("audio", audio_id),
            "progress_seconds": progress,
            "audio": {
                "id": int(audio["id"]),
                "book_id": int(audio["book_id"]),
                "title": audio["title"],
                "book_title": audio["book_title"],
                "narrator": audio["narrator"],
                "duration_seconds": int(audio["duration_seconds"] or 0),
                "is_free": int(audio["is_free"] or 0) == 1,
                "price_stars": int(audio["price_stars"] or 0),
            },
        }

    @app.post("/api/audio/{audio_id}/progress")
    async def api_audio_progress(audio_id: int, payload: dict[str, Any], x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        if not await user_can_access_audio(user.app_user_id, audio_id):
            raise HTTPException(status_code=403, detail="Нет доступа к аудиоглаве.")
        position = int(payload.get("position_seconds") or 0)
        await save_listening_progress(user.app_user_id, audio_id, position)
        return {"ok": True, "position_seconds": max(0, position)}

    @app.get("/api/audio/{audio_id}/file")
    async def api_audio_file(audio_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        audio = await get_audio_chapter(audio_id)
        if not audio or not audio["file_path"]:
            raise HTTPException(status_code=404, detail="Audio not found")
        if not await user_can_access_audio(user.app_user_id, audio_id):
            raise HTTPException(status_code=403, detail="Audio requires purchase")
        path = Path(audio["file_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Audio file not found")
        return FileResponse(path, media_type=audio["mime_type"] or "audio/mpeg", filename=audio["source_filename"] or path.name)

    @app.get("/media/audio/{audio_id}")
    async def audio_media(audio_id: int):
        audio = await get_audio_chapter(audio_id)
        if not audio or not audio["file_path"]:
            raise HTTPException(status_code=404, detail="Audio not found")
        if not audio["is_free"]:
            raise HTTPException(status_code=403, detail="Audio requires Mini App access check")
        path = Path(audio["file_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Audio file not found")
        return FileResponse(path, media_type=audio["mime_type"] or "audio/mpeg", filename=audio["source_filename"] or path.name)

    return app
