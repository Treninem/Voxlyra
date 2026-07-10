from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from aiogram import Bot

from app.config import settings
from app.db import (
    add_comment,
    add_audit,
    add_manual_chapter,
    book_belongs_to_author,
    count_chapters_for_book,
    get_adjacent_audio_chapters,
    get_adjacent_chapters,
    get_adjacent_chapters_for_moderation,
    get_audio_chapter,
    get_book,
    get_book_options,
    get_author_dashboard_stats,
    get_author_finance_summary,
    get_author_profile,
    get_admin_permissions,
    get_platform_stats,
    get_owner_today_stats,
    get_platform_finance_summary,
    get_control_queue_counts,
    get_book_with_counts,
    get_bookmark,
    get_chapter,
    get_published_chapter_by_number,
    get_published_chapter_bounds,
    get_chapter_by_number_for_moderation,
    get_chapter_bounds_for_moderation,
    get_listening_progress,
    get_reader_ad_settings,
    get_reading_progress,
    get_user_review,
    get_user_preferences,
    get_user_by_id,
    get_tts_progress,
    has_purchase_access,
    init_db,
    list_audio_chapters_for_book,
    list_author_books_with_counts,
    list_books_for_moderation,
    list_complaints,
    list_refund_requests,
    get_refund_request,
    get_complaint,
    get_comment_for_moderation,
    get_review_for_moderation,
    finalize_refund,
    reject_refund_request,
    list_payout_requests,
    get_payout_request,
    set_payout_request_status,
    set_author_payout_frozen,
    list_moderation_comments,
    list_moderation_reviews,
    set_comment_status,
    set_review_status,
    set_complaint_status,
    set_book_publication_status,
    resolve_book_moderation,
    publish_book_content,
    list_catalog_books,
    list_chapters_for_book,
    list_comments_for_chapter,
    list_contextual_book_ads,
    list_reviews_for_book,
    list_similar_books,
    list_user_bookmarks,
    list_user_continue_listening,
    list_user_continue_reading,
    list_user_purchases,
    record_reader_ad_event,
    remove_bookmark,
    save_listening_progress,
    soft_delete_book,
    soft_delete_chapter_for_author,
    submit_book_for_review,
    update_author_book_fields,
    update_chapter_price,
    update_chapter_text,
    update_chapter_title,
    upsert_imported_chapters,
    update_book_import_fingerprint,
    set_book_duplicate_override,
    save_reading_progress,
    save_tts_progress,
    set_bookmark,
    set_chapter_status,
    set_user_preference,
    reset_user_preferences,
    upsert_review,
    user_can_access_audio,
    user_can_access_chapter,
)
from app.services.tma_auth import TMAAuthError, TMAUser, authenticate_init_data
from app.permissions import PERMISSION_BY_CODE
from app.services.diagnostics import diagnostics_summary
from app.services.book_parser import BookParseError, build_import_report, parse_book_file
from app.services.duplicate_books import find_book_duplicates, sha256_file
from app.services.chunked_upload import (
    ChunkedUploadError,
    CHUNK_SIZE_BYTES,
    assemble_upload,
    cleanup_upload,
    create_upload,
    save_chunk,
)
from app.services.notifications import (
    book_moderation_message,
    book_revision_markup,
    complaint_message,
    content_hidden_message,
    payout_message,
    refund_message,
    new_chapter_message,
    notify_book_followers,
    send_user_notification,
)
from app.services.web_import_store import (
    delete_web_import_preview,
    load_web_import_preview,
    load_web_import_metadata,
    save_web_import_preview,
)
from app.services.publication import finish_book_content_workflow, publish_book_and_channel
from app.services.cover_storage import ensure_book_cover_file
from app.services.moderation_alerts import notify_moderation_resolved
from app.services.reader_tts import (
    TTS_CACHE_VERSION,
    ReaderTTSError,
    available_rates,
    available_styles,
    available_voices,
    build_media_url,
    generate_chapter_tts,
    tts_engine_status,
    tts_profile_key,
    validate_media_token,
    validate_rate,
    validate_style,
    validate_voice,
)




def _cover_file_response(path: Path, *, private: bool = False) -> FileResponse:
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    cache_control = "private, no-store, max-age=0" if private else "public, no-cache, max-age=0, must-revalidate"
    # Не передаём filename в FileResponse: иначе Starlette ставит attachment,
    # и Telegram WebView может не показать файл внутри <img>.
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": cache_control,
            "Content-Disposition": f'inline; filename="{path.name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )

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


async def _has_book_moderation_access(*, app_user_id: int, telegram_id: int, chapter: Any) -> bool:
    """Служебный доступ к тексту главы только владельцу и сотрудникам с mod_books.

    Владелец может проверять любую неудалённую книгу. Делегированный сотрудник —
    опубликованную книгу либо книгу, реально находящуюся в очереди review.
    """
    if int(telegram_id) in settings.owner_ids:
        return str(chapter["publication_status"] or "") != "deleted" and str(chapter["status"] or "") != "deleted"
    permissions = await get_admin_permissions(int(app_user_id))
    if "mod_books" not in permissions:
        return False
    return (
        str(chapter["status"] or "") != "deleted"
        and str(chapter["publication_status"] or "") in {"published", "review"}
    )


async def _chapter_access(*, app_user_id: int, telegram_id: int, chapter: Any) -> tuple[bool, bool]:
    """Возвращает (доступ, служебный_режим_проверки)."""
    is_public = (
        str(chapter["publication_status"] or "") == "published"
        and str(chapter["status"] or "") == "published"
    )
    if is_public and await user_can_access_chapter(int(app_user_id), int(chapter["id"])):
        return True, False
    moderation = await _has_book_moderation_access(
        app_user_id=int(app_user_id),
        telegram_id=int(telegram_id),
        chapter=chapter,
    )
    return moderation, moderation


async def _audit_moderation_reader_access(*, user_id: int, chapter_id: int, action: str) -> None:
    await add_audit(
        int(user_id),
        action,
        "chapter",
        str(int(chapter_id)),
        None,
        "Служебный доступ для проверки содержимого",
    )


def _showcase_sections(books: list[Any]) -> dict[str, list[Any]]:
    newest = list(books[:8])
    popular_candidates = [
        row for row in books
        if int(row["purchase_count"] or 0) > 0
        or int(row["reviews_count"] or 0) > 0
        or float(row["rating"] or 0) > 0
    ]
    popular = sorted(
        popular_candidates,
        key=lambda row: (
            int(row["purchase_count"] or 0),
            int(row["reviews_count"] or 0),
            float(row["rating"] or 0),
            int(row["id"]),
        ),
        reverse=True,
    )[:8]
    audio = [row for row in books if int(row["audio_count"] or 0) > 0 or int(row["has_audio"] or 0) == 1][:8]
    free = [
        row for row in books
        if int(row["price_stars"] or 0) <= 0 or int(row["free_chapters_count"] or 0) > 0
    ][:8]
    return {"newest": newest, "popular": popular, "audio_books": audio, "free_books": free}


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await init_db()
        yield

    app = FastAPI(title="Вокслира", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory="static"), name="static")
    templates = Jinja2Templates(directory="templates")

    def common_context(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data = {"project_name": settings.PROJECT_NAME}
        if extra:
            data.update(extra)
        return data

    async def author_session(init_data: str | None) -> tuple[TMAUser, Any]:
        user = await _tma_user(init_data)
        profile = await get_author_profile(user.app_user_id)
        if not profile or profile["status"] not in {"active", "approved"}:
            raise HTTPException(
                status_code=403,
                detail="Сначала создайте профиль автора в боте. После этого кабинет откроется здесь.",
            )
        return user, profile

    async def notify_after_action(
        *,
        actor_user_id: int,
        event: str,
        target_type: str,
        target_id: int,
        app_user_id: int | None,
        telegram_id: int | None,
        text: str,
        reply_markup=None,
    ) -> str:
        result = await send_user_notification(
            app_user_id=app_user_id,
            telegram_id=telegram_id,
            text=text,
            reply_markup=reply_markup,
        )
        await add_audit(
            actor_user_id,
            f"notification_{result}",
            target_type,
            str(target_id),
            event,
            result,
        )
        return result

    async def control_session(init_data: str | None, *required: str) -> tuple[TMAUser, bool, set[str]]:
        user = await _tma_user(init_data)
        is_owner = user.telegram_id in settings.owner_ids
        permissions = set(PERMISSION_BY_CODE) if is_owner else await get_admin_permissions(user.app_user_id)
        if not permissions:
            raise HTTPException(status_code=403, detail="У вас нет доступа к панели управления.")
        if required and not is_owner and not any(code in permissions for code in required):
            raise HTTPException(status_code=403, detail="Для этого действия не выдано право доступа.")
        return user, is_owner, permissions

    @app.get("/health")
    async def health():
        """Короткая проверка для Bothost и владельца. Не раскрывает токен и секреты."""
        summary = diagnostics_summary()
        return {
            "ok": True,
            "project": settings.PROJECT_NAME,
        }

    @app.get("/readiness")
    async def readiness():
        """Подробная, но безопасная проверка окружения после деплоя."""
        summary = diagnostics_summary()
        return {"ok": bool(summary["ok"])}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        books = await list_catalog_books(limit=80, include_drafts=False)
        sections = _showcase_sections(books)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            common_context({"books": books, **sections}),
        )

    @app.get("/catalog", response_class=HTMLResponse)
    async def catalog(request: Request):
        books = await list_catalog_books(limit=80, include_drafts=False)
        sections = _showcase_sections(books)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            common_context({"books": books, **sections}),
        )

    @app.get("/book/{book_id}", response_class=HTMLResponse)
    async def book(request: Request, book_id: int):
        book_row = await get_book_with_counts(book_id)
        if book_row and book_row["publication_status"] != "published":
            book_row = None
        chapters = await list_chapters_for_book(book_id, published_only=True) if book_row else []
        audios = await list_audio_chapters_for_book(book_id, published_only=True) if book_row else []
        options = await get_book_options(book_id) if book_row else {}
        similar_books = await list_similar_books(book_id, limit=6) if book_row else []
        public_reviews = await list_reviews_for_book(book_id, limit=20) if book_row else []
        return templates.TemplateResponse(
            request,
            "book.html",
            {
                "book": book_row,
                "book_id": book_id,
                "chapters": chapters,
                "audios": audios,
                "options": options,
                "similar_books": similar_books,
                "public_reviews": public_reviews,
                "project_name": settings.PROJECT_NAME,
                "bot_username": settings.BOT_USERNAME.strip().lstrip("@"),
                "channel_promotion_enabled": bool(settings.CHANNEL_ID.strip()),
            },
        )

    @app.get("/reader/{chapter_id}", response_class=HTMLResponse)
    async def reader(request: Request, chapter_id: int):
        # HTML-оболочка не содержит закрытый текст. Право на платную или проверяемую
        # главу определяется только в /api/reader после проверки Telegram initData.
        chapter = await get_chapter(chapter_id)
        is_public = bool(
            chapter
            and chapter["publication_status"] == "published"
            and chapter["status"] == "published"
        )
        purchase_url = _bot_purchase_url("chapter", chapter_id) if is_public else ""
        ads = []
        adjacent = await get_adjacent_chapters(chapter_id) if is_public else {"previous": None, "next": None}
        chapter_bounds = (
            await get_published_chapter_bounds(int(chapter["book_id"]))
            if is_public and chapter
            else {"min_number": 0, "max_number": 0, "chapters_count": 0}
        )
        ad_settings = await get_reader_ad_settings()
        if is_public and chapter and ad_settings.get("enabled"):
            ads = await list_contextual_book_ads(int(chapter["book_id"]), limit=4)
        server_text_visible = bool(
            is_public
            and chapter
            and (int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0)
        )
        return templates.TemplateResponse(
            request,
            "reader.html",
            {
                "chapter": chapter,
                "chapter_id": chapter_id,
                "purchase_url": purchase_url,
                "reader_ads": ads,
                "ad_settings": ad_settings,
                "previous_chapter": adjacent.get("previous"),
                "next_chapter": adjacent.get("next"),
                "chapter_bounds": chapter_bounds,
                "server_text_visible": server_text_visible,
                "project_name": settings.PROJECT_NAME,
                "bot_username": settings.BOT_USERNAME.strip().lstrip("@"),
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", common_context())

    @app.get("/library", response_class=HTMLResponse)
    async def library_page(request: Request):
        return templates.TemplateResponse(request, "library.html", common_context())

    @app.get("/author", response_class=HTMLResponse)
    async def author_page(request: Request):
        return templates.TemplateResponse(
            request,
            "author.html",
            common_context({"max_book_upload_mb": int(settings.MAX_BOOK_UPLOAD_MB or 0)}),
        )

    @app.get("/control", response_class=HTMLResponse)
    async def control_page(request: Request):
        return templates.TemplateResponse(request, "control.html", common_context())

    @app.get("/audio", response_class=HTMLResponse)
    async def audio_index(request: Request):
        rows = await list_catalog_books(limit=60, include_drafts=False)
        books = [book for book in rows if int(book["audio_count"] or 0) > 0 or int(book["has_audio"] or 0) == 1]
        return templates.TemplateResponse(request, "audio.html", common_context({"books": books}))

    @app.get("/audio/{audio_id}", response_class=HTMLResponse)
    async def audio_player(request: Request, audio_id: int):
        audio = await get_audio_chapter(audio_id)
        if audio and (audio["publication_status"] != "published" or audio["status"] != "published"):
            audio = None
        adjacent = await get_adjacent_audio_chapters(audio_id) if audio else {"previous": None, "next": None}
        return templates.TemplateResponse(
            request,
            "audio_player.html",
            {
                "audio": audio,
                "audio_id": audio_id,
                "previous_audio": adjacent.get("previous"),
                "next_audio": adjacent.get("next"),
                "purchase_url": _bot_purchase_url("audio", audio_id),
                "project_name": settings.PROJECT_NAME,
            },
        )

    @app.get("/media/cover/{book_id}")
    async def book_cover(book_id: int):
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] != "published":
            raise HTTPException(status_code=404, detail="Обложка не найдена.")
        path = await ensure_book_cover_file(
            book_id=book_id,
            cover_file_id=str(book_row["cover_file_id"] or ""),
            cover_path=str(book_row["cover_path"] or ""),
        )
        if not path:
            raise HTTPException(
                status_code=404,
                detail="Обложка не найдена.",
                headers={"Cache-Control": "no-store, max-age=0"},
            )
        return _cover_file_response(path, private=False)

    @app.get("/api/me")
    async def api_me(x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        bookmarks = await list_user_bookmarks(user.app_user_id, limit=40, published_only=True)
        continue_reading = await list_user_continue_reading(user.app_user_id, limit=12)
        continue_listening = await list_user_continue_listening(user.app_user_id, limit=12)
        purchases = await list_user_purchases(user.app_user_id, limit=30)
        author_profile = await get_author_profile(user.app_user_id)
        is_owner = user.telegram_id in settings.owner_ids
        permissions = set(PERMISSION_BY_CODE) if is_owner else await get_admin_permissions(user.app_user_id)
        preferences = await get_user_preferences(user.app_user_id)
        return {
            "ok": True,
            "user": {
                "id": user.app_user_id,
                "telegram_id": user.telegram_id,
                "username": user.username,
                "full_name": user.full_name,
            },
            "bookmarks": _rows_to_dicts(bookmarks),
            "continue_reading": _rows_to_dicts(continue_reading),
            "continue_listening": _rows_to_dicts(continue_listening),
            "purchases": _rows_to_dicts(purchases),
            "preferences": preferences,
            "author": {
                "enabled": bool(author_profile and author_profile["status"] in {"active", "approved"}),
            },
            "control": {
                "enabled": bool(permissions),
                "owner": is_owner,
                "permissions": sorted(permissions),
            },
        }

    @app.get("/api/preferences")
    async def api_preferences(x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        return {"ok": True, "preferences": await get_user_preferences(user.app_user_id)}

    @app.patch("/api/preferences")
    async def api_preferences_update(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        key = str(payload.get("key") or "")
        value = str(payload.get("value") or "")
        allowed = {
            "theme", "font_size", "notifications",
            "notifications_chapters", "notifications_audio", "notifications_discounts",
        }
        if key not in allowed:
            raise HTTPException(status_code=400, detail="Настройка не найдена.")
        preferences = await set_user_preference(user.app_user_id, key, value)
        return {"ok": True, "preferences": preferences}

    @app.delete("/api/preferences")
    async def api_preferences_reset(x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        return {"ok": True, "preferences": await reset_user_preferences(user.app_user_id)}

    @app.get("/api/book/{book_id}/state")
    async def api_book_state(book_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] != "published":
            raise HTTPException(status_code=404, detail="Книга не найдена.")
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
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] != "published":
            raise HTTPException(status_code=404, detail="Книга не найдена.")
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
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] != "published":
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        rating = int(payload.get("rating") or 5)
        text = str(payload.get("text") or "").strip()
        if len(text) > 3000:
            raise HTTPException(status_code=400, detail="Отзыв слишком длинный.")
        await upsert_review(user.app_user_id, book_id, rating, text)
        reviews = await list_reviews_for_book(book_id, limit=20)
        return {"ok": True, "reviews": _rows_to_dicts(reviews)}

    @app.get("/api/book/{book_id}/chapter-number/{chapter_number}")
    async def api_chapter_by_number(
        book_id: int,
        chapter_number: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        if chapter_number < 1 or chapter_number > 1_000_000:
            raise HTTPException(status_code=400, detail="Введите корректный номер главы.")
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] == "deleted":
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        if book_row["publication_status"] == "published":
            chapter_row = await get_published_chapter_by_number(book_id, chapter_number)
            bounds = await get_published_chapter_bounds(book_id)
        else:
            user = await _tma_user(x_telegram_init_data)
            probe = await get_chapter_by_number_for_moderation(book_id, chapter_number)
            if not probe:
                bounds = await get_chapter_bounds_for_moderation(book_id)
                if bounds["chapters_count"]:
                    detail = f"Главы {chapter_number} нет. Доступны номера от {bounds['min_number']} до {bounds['max_number']}."
                else:
                    detail = "У книги пока нет глав."
                raise HTTPException(status_code=404, detail=detail)
            chapter = await get_chapter(int(probe["id"]))
            if not chapter or not await _has_book_moderation_access(
                app_user_id=user.app_user_id,
                telegram_id=user.telegram_id,
                chapter=chapter,
            ):
                raise HTTPException(status_code=404, detail="Книга не найдена.")
            chapter_row = probe
            bounds = await get_chapter_bounds_for_moderation(book_id)
        if not chapter_row:
            if bounds["chapters_count"]:
                detail = f"Главы {chapter_number} нет. Доступны номера от {bounds['min_number']} до {bounds['max_number']}."
            else:
                detail = "У книги пока нет опубликованных глав."
            raise HTTPException(status_code=404, detail=detail)
        return {
            "ok": True,
            "chapter": _row_to_dict(chapter_row),
            "reader_url": f"/reader/{int(chapter_row['id'])}",
            "bounds": bounds,
        }

    @app.get("/api/reader/{chapter_id}")
    async def api_reader(chapter_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        purchase_url = "" if moderation_access else _bot_purchase_url("chapter", chapter_id)
        comments = await list_comments_for_chapter(chapter_id, limit=50) if allowed and is_public and not moderation_access else []
        progress = await get_reading_progress(user.app_user_id, chapter_id) if allowed and not moderation_access else 0
        reader_ads = []
        ad_settings = await get_reader_ad_settings()
        if is_public and not moderation_access and ad_settings.get("enabled"):
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
        adjacent = (
            await get_adjacent_chapters_for_moderation(chapter_id)
            if moderation_access
            else await get_adjacent_chapters(chapter_id)
        )
        chapter_bounds = (
            await get_chapter_bounds_for_moderation(int(chapter["book_id"]))
            if moderation_access
            else await get_published_chapter_bounds(int(chapter["book_id"]))
        )
        if moderation_access:
            await _audit_moderation_reader_access(
                user_id=user.app_user_id,
                chapter_id=chapter_id,
                action="moderation_chapter_read",
            )
        return {
            "ok": True,
            "allowed": allowed,
            "moderation_access": moderation_access,
            "access_mode": "moderation" if moderation_access else ("reader" if allowed else "locked"),
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
            "chapter_bounds": chapter_bounds,
            "navigation": {
                "previous": _row_to_dict(adjacent["previous"]) if adjacent["previous"] else None,
                "next": _row_to_dict(adjacent["next"]) if adjacent["next"] else None,
            },
        }

    @app.get("/api/reader/tts/voices")
    async def api_reader_tts_voices(x_telegram_init_data: str | None = Header(default=None)):
        await _tma_user(x_telegram_init_data)
        status = tts_engine_status()
        return {
            "ok": True,
            "enabled": bool(status["enabled"]),
            "message": status["message"],
            "engine": status.get("engine", "piper"),
            "voices": available_voices(),
            "styles": available_styles(),
            "rates": available_rates(),
        }

    @app.get("/api/reader/{chapter_id}/tts")
    async def api_reader_tts(
        chapter_id: int,
        voice: str = "irina",
        rate: float = 1.0,
        style: str = "expressive",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not allowed:
            raise HTTPException(status_code=403, detail="Озвучивание доступно после открытия главы.")
        selected_voice = validate_voice(voice)
        selected_rate = validate_rate(rate)
        selected_style = validate_style(style)
        profile_key = tts_profile_key(selected_voice, selected_rate, selected_style)
        try:
            asset = await generate_chapter_tts(
                chapter_id,
                str(chapter["text"] or ""),
                selected_voice,
                selected_rate,
                selected_style,
            )
        except ReaderTTSError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        adjacent = (
            await get_adjacent_chapters_for_moderation(chapter_id)
            if moderation_access
            else await get_adjacent_chapters(chapter_id)
        )
        progress = await get_tts_progress(user.app_user_id, chapter_id, profile_key)
        if moderation_access:
            await _audit_moderation_reader_access(
                user_id=user.app_user_id,
                chapter_id=chapter_id,
                action="moderation_chapter_tts",
            )
        return {
            "ok": True,
            "enabled": True,
            "moderation_access": moderation_access,
            "access_mode": "moderation" if moderation_access else "reader",
            "device_cache_allowed": not moderation_access,
            "audio_url": build_media_url(
                user_id=user.app_user_id,
                chapter_id=chapter_id,
                voice=selected_voice,
                rate=selected_rate,
                style=selected_style,
            ),
            "duration_seconds": int(asset.duration_seconds or 0),
            "progress_seconds": int(progress or 0),
            "cache_key": asset.text_hash,
            "cache_version": TTS_CACHE_VERSION,
            "voice": selected_voice,
            "rate": selected_rate,
            "style": selected_style,
            "voices": available_voices(),
            "styles": available_styles(),
            "rates": available_rates(),
            "chapter": {
                "id": int(chapter["id"]),
                "book_id": int(chapter["book_id"]),
                "book_title": chapter["book_title"],
                "pen_name": chapter["pen_name"] or "Автор не указан",
                "title": chapter["title"],
                "number": int(chapter["number"]),
                "text": chapter["text"],
            },
            "navigation": {
                "previous": _row_to_dict(adjacent["previous"]) if adjacent["previous"] else None,
                "next": _row_to_dict(adjacent["next"]) if adjacent["next"] else None,
            },
        }

    @app.post("/api/reader/{chapter_id}/tts/progress")
    async def api_reader_tts_progress(
        chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к главе.")
        position = max(0, int(payload.get("position_seconds") or 0))
        selected_voice = validate_voice(str(payload.get("voice") or "irina"))
        selected_rate = validate_rate(payload.get("rate"))
        selected_style = validate_style(str(payload.get("style") or "expressive"))
        profile_key = tts_profile_key(selected_voice, selected_rate, selected_style)
        await save_tts_progress(user.app_user_id, chapter_id, position, profile_key)
        return {
            "ok": True,
            "position_seconds": position,
            "voice": selected_voice,
            "rate": selected_rate,
            "style": selected_style,
            "moderation_access": moderation_access,
        }

    @app.get("/media/reader-tts/{chapter_id}.mp3")
    async def media_reader_tts(
        chapter_id: int,
        uid: int,
        voice: str,
        rate: float = 1.0,
        style: str = "expressive",
        exp: int = 0,
        sig: str = "",
    ):
        selected_voice = validate_voice(voice)
        selected_rate = validate_rate(rate)
        selected_style = validate_style(style)
        if not validate_media_token(
            user_id=uid,
            chapter_id=chapter_id,
            voice=selected_voice,
            rate=selected_rate,
            style=selected_style,
            expires_at=exp,
            signature=sig,
        ):
            raise HTTPException(status_code=403, detail="Ссылка на озвучивание устарела.")
        user_row = await get_user_by_id(uid)
        if not user_row or int(user_row["is_blocked"] or 0) == 1:
            raise HTTPException(status_code=403, detail="Доступ закрыт.")
        chapter = await get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=int(uid),
            telegram_id=int(user_row["telegram_id"]),
            chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к главе.")
        try:
            asset = await generate_chapter_tts(
                chapter_id,
                str(chapter["text"] or ""),
                selected_voice,
                selected_rate,
                selected_style,
            )
        except ReaderTTSError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return FileResponse(
            asset.path,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "private, max-age=86400, immutable",
                "Accept-Ranges": "bytes",
                "Content-Disposition": (
                    f'inline; filename="chapter_{chapter_id}_{selected_voice}_{selected_style}_{selected_rate:.2f}.mp3"'
                ),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/reader/{chapter_id}/progress")
    async def api_reader_progress(
        chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к главе.")
        percent = max(0, min(100, int(payload.get("position_percent") or 0)))
        # Служебное чтение не должно попадать в личную историю и рекомендации модератора.
        if not moderation_access:
            await save_reading_progress(user.app_user_id, chapter_id, percent)
        return {
            "ok": True,
            "position_percent": percent,
            "moderation_access": moderation_access,
        }

    @app.get("/api/reader/{chapter_id}/comments")
    async def api_comments(chapter_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not await user_can_access_chapter(user.app_user_id, chapter_id):
            raise HTTPException(status_code=403, detail="Комментарии доступны после открытия главы.")
        comments = await list_comments_for_chapter(chapter_id, limit=50)
        return {"ok": True, "comments": _rows_to_dicts(comments)}

    @app.post("/api/reader/{chapter_id}/comments")
    async def api_add_comment(chapter_id: int, payload: dict[str, Any], x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
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
        if not audio or audio["publication_status"] != "published" or audio["status"] != "published":
            raise HTTPException(status_code=404, detail="Аудиоглава не найдена.")
        allowed = await user_can_access_audio(user.app_user_id, audio_id)
        progress = await get_listening_progress(user.app_user_id, audio_id) if allowed else 0
        adjacent = await get_adjacent_audio_chapters(audio_id)
        return {
            "ok": True,
            "allowed": allowed,
            "purchase_url": _bot_purchase_url("audio", audio_id),
            "navigation": {
                "previous": _row_to_dict(adjacent["previous"]) if adjacent["previous"] else None,
                "next": _row_to_dict(adjacent["next"]) if adjacent["next"] else None,
            },
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
        audio = await get_audio_chapter(audio_id)
        if not audio or audio["publication_status"] != "published" or audio["status"] != "published":
            raise HTTPException(status_code=404, detail="Аудиоглава не найдена.")
        if not await user_can_access_audio(user.app_user_id, audio_id):
            raise HTTPException(status_code=403, detail="Нет доступа к аудиоглаве.")
        position = int(payload.get("position_seconds") or 0)
        await save_listening_progress(user.app_user_id, audio_id, position)
        return {"ok": True, "position_seconds": max(0, position)}

    @app.get("/api/audio/{audio_id}/file")
    async def api_audio_file(audio_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        audio = await get_audio_chapter(audio_id)
        if not audio or audio["publication_status"] != "published" or audio["status"] != "published" or not audio["file_path"]:
            raise HTTPException(status_code=404, detail="Аудиоглава не найдена.")
        if not await user_can_access_audio(user.app_user_id, audio_id):
            raise HTTPException(status_code=403, detail="Аудиоглава доступна после покупки.")
        path = Path(audio["file_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Аудиофайл не найден.")
        return FileResponse(path, media_type=audio["mime_type"] or "audio/mpeg", filename=audio["source_filename"] or path.name)

    @app.get("/api/author/dashboard")
    async def api_author_dashboard(x_telegram_init_data: str | None = Header(default=None)):
        user, profile = await author_session(x_telegram_init_data)
        stats = await get_author_dashboard_stats(user.app_user_id)
        finance = await get_author_finance_summary(user.app_user_id)
        books = await list_author_books_with_counts(user.app_user_id)
        return {
            "ok": True,
            "profile": _row_to_dict(profile),
            "stats": stats,
            "finance": finance,
            "books": _rows_to_dicts(books),
            "upload": {
                "chunk_size": CHUNK_SIZE_BYTES,
                "max_mb": int(settings.MAX_BOOK_UPLOAD_MB or 0),
                "formats": ["TXT", "DOCX", "FB2", "EPUB", "PDF", "ZIP"],
            },
        }

    @app.get("/api/author/book/{book_id}/cover")
    async def api_author_book_cover(book_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Обложка не найдена.")
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] == "deleted":
            raise HTTPException(status_code=404, detail="Обложка не найдена.")
        path = await ensure_book_cover_file(
            book_id=book_id,
            cover_file_id=str(book_row["cover_file_id"] or ""),
            cover_path=str(book_row["cover_path"] or ""),
        )
        if not path:
            raise HTTPException(
                status_code=404,
                detail="Обложка не найдена.",
                headers={"Cache-Control": "private, no-store, max-age=0"},
            )
        return _cover_file_response(path, private=True)

    @app.get("/api/author/book/{book_id}")
    async def api_author_book(book_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] == "deleted":
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        chapters = await list_chapters_for_book(book_id)
        audio = await list_audio_chapters_for_book(book_id)
        return {
            "ok": True,
            "book": _row_to_dict(book_row),
            "chapters": _rows_to_dicts(chapters),
            "audio": _rows_to_dicts(audio),
        }

    @app.patch("/api/author/book/{book_id}")
    async def api_author_update_book(
        book_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        values = {key: payload[key] for key in (
            "title", "description", "age_limit", "writing_status",
            "allow_download", "pricing_type", "price_stars",
        ) if key in payload}
        if "age_limit" in values and values["age_limit"] not in {"0+", "6+", "12+", "16+", "18+"}:
            raise HTTPException(status_code=400, detail="Выберите возрастное ограничение из списка.")
        if "writing_status" in values and values["writing_status"] not in {"writing", "finished", "frozen"}:
            raise HTTPException(status_code=400, detail="Выберите состояние книги из списка.")
        if "pricing_type" in values and values["pricing_type"] not in {"free", "chapters", "whole_book"}:
            raise HTTPException(status_code=400, detail="Выберите способ продажи из списка.")
        ok = await update_author_book_fields(book_id, user.app_user_id, values)
        if not ok:
            raise HTTPException(status_code=400, detail="Не удалось сохранить изменения.")
        await add_audit(user.app_user_id, "book_updated_web", "book", str(book_id), None, ",".join(values.keys()))
        book_row = await get_book(book_id)
        return {"ok": True, "book": _row_to_dict(book_row)}

    @app.post("/api/author/book/{book_id}/submit")
    async def api_author_submit_book(book_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        chapters = await list_chapters_for_book(book_id)
        if not chapters:
            raise HTTPException(status_code=400, detail="Перед отправкой добавьте хотя бы одну главу.")
        if not settings.BOT_TOKEN:
            raise HTTPException(status_code=503, detail="Бот временно недоступен для проверки книги.")
        delivery_bot = Bot(token=settings.BOT_TOKEN)
        try:
            workflow = await finish_book_content_workflow(
                bot=delivery_bot,
                book_id=book_id,
                actor_user_id=user.app_user_id,
                actor_telegram_id=user.telegram_id,
                source="miniapp_submit",
            )
        finally:
            await delivery_bot.session.close()
        await add_audit(user.app_user_id, "book_submitted_web", "book", str(book_id), None, workflow.workflow_status)
        return {
            "ok": True,
            "status": workflow.workflow_status or "review",
            "channel_status": workflow.channel_status,
            "message": workflow.channel_message,
            "review_reasons": workflow.duplicate_text,
        }

    @app.delete("/api/author/book/{book_id}")
    async def api_author_delete_book(book_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user, _ = await author_session(x_telegram_init_data)
        if not await soft_delete_book(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        await add_audit(user.app_user_id, "book_deleted_web", "book", str(book_id))
        return {"ok": True}

    @app.post("/api/author/book/{book_id}/chapters")
    async def api_author_add_chapter(
        book_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        title = str(payload.get("title") or "").strip()
        text = str(payload.get("text") or "").strip()
        price = max(0, min(100000, int(payload.get("price_stars") or 0)))
        if len(title) < 2:
            raise HTTPException(status_code=400, detail="Введите название главы.")
        if len(text) < 100:
            raise HTTPException(status_code=400, detail="Текст главы слишком короткий.")
        chapter_id = await add_manual_chapter(book_id, title[:160], text, is_free=price == 0, price_stars=price)
        book = await get_book(book_id)
        if book and book["publication_status"] == "published":
            await set_chapter_status(chapter_id, "published")
        await add_audit(user.app_user_id, "chapter_created_web", "chapter", str(chapter_id), None, str(book_id))
        chapter = await get_chapter(chapter_id)
        notification = None
        if chapter and chapter["publication_status"] == "published" and chapter["status"] == "published":
            notification = await notify_book_followers(
                book_id=book_id,
                event_key=f"chapter:{chapter_id}:published",
                category="chapters",
                text=new_chapter_message(chapter["book_title"], chapter["title"], chapter["number"]),
            )
            await add_audit(user.app_user_id, "chapter_followers_notified_web", "chapter", str(chapter_id), None, str(notification))
        workflow = None
        if settings.BOT_TOKEN:
            delivery_bot = Bot(token=settings.BOT_TOKEN)
            try:
                workflow_result = await finish_book_content_workflow(
                    bot=delivery_bot,
                    book_id=book_id,
                    actor_user_id=user.app_user_id,
                    actor_telegram_id=user.telegram_id,
                    source="miniapp_manual_chapter",
                )
                workflow = {
                    "status": workflow_result.workflow_status,
                    "channel_status": workflow_result.channel_status,
                    "channel_message": workflow_result.channel_message,
                    "duplicate_text": workflow_result.duplicate_text,
                }
            finally:
                await delivery_bot.session.close()
        return {"ok": True, "chapter": _row_to_dict(chapter), "notification": notification, "workflow": workflow}

    @app.patch("/api/author/chapter/{chapter_id}")
    async def api_author_update_chapter(
        chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or not await book_belongs_to_author(int(chapter["book_id"]), user.app_user_id):
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        changed = False
        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if len(title) < 2:
                raise HTTPException(status_code=400, detail="Введите название главы.")
            changed = await update_chapter_title(chapter_id, user.app_user_id, title) or changed
        if "text" in payload:
            text = str(payload.get("text") or "").strip()
            if len(text) < 100:
                raise HTTPException(status_code=400, detail="Текст главы слишком короткий.")
            changed = await update_chapter_text(chapter_id, user.app_user_id, text) or changed
        if "price_stars" in payload:
            price = max(0, min(100000, int(payload.get("price_stars") or 0)))
            changed = await update_chapter_price(chapter_id, user.app_user_id, price == 0, price) or changed
        if not changed:
            raise HTTPException(status_code=400, detail="Нет изменений для сохранения.")
        await add_audit(user.app_user_id, "chapter_updated_web", "chapter", str(chapter_id))
        updated = await get_chapter(chapter_id)
        return {"ok": True, "chapter": _row_to_dict(updated)}

    @app.delete("/api/author/chapter/{chapter_id}")
    async def api_author_delete_chapter(chapter_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user, _ = await author_session(x_telegram_init_data)
        if not await soft_delete_chapter_for_author(chapter_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        await add_audit(user.app_user_id, "chapter_deleted_web", "chapter", str(chapter_id))
        return {"ok": True}

    @app.post("/api/author/book/{book_id}/upload/start")
    async def api_author_upload_start(
        book_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        try:
            meta = create_upload(
                user_id=user.app_user_id,
                book_id=book_id,
                filename=str(payload.get("filename") or ""),
                total_size=int(payload.get("size") or 0),
            )
        except ChunkedUploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "upload_id": meta["upload_id"], "chunk_size": CHUNK_SIZE_BYTES}

    @app.post("/api/author/book/{book_id}/upload/{upload_id}/chunk")
    async def api_author_upload_chunk(
        book_id: int,
        upload_id: str,
        index: int = Form(...),
        total_chunks: int = Form(...),
        chunk: UploadFile = File(...),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        try:
            result = await save_chunk(
                upload_id,
                user_id=user.app_user_id,
                book_id=book_id,
                index=int(index),
                total_chunks=int(total_chunks),
                chunk=chunk,
            )
        except ChunkedUploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/author/book/{book_id}/upload/{upload_id}/finish")
    async def api_author_upload_finish(
        book_id: int,
        upload_id: str,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            cleanup_upload(upload_id)
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        total_chunks = int(payload.get("total_chunks") or 0)
        try:
            path, meta = assemble_upload(
                upload_id,
                user_id=user.app_user_id,
                book_id=book_id,
                total_chunks=total_chunks,
            )
            chapters = await asyncio.to_thread(
                parse_book_file,
                path,
                str(meta.get("filename") or path.name),
                path.parent / "extract",
            )
            if not chapters:
                raise BookParseError("Главы не найдены. Проверьте структуру файла.")
            source_hash = sha256_file(path)
            book = await get_book(book_id)
            duplicate_matches = await find_book_duplicates(
                title=book["title"] if book else str(meta.get("filename") or path.name),
                author_id=int(book["author_id"]) if book and book["author_id"] is not None else None,
                exclude_book_id=book_id,
                source_file_hash=source_hash,
            )
            report = build_import_report(chapters)
            preview_token = save_web_import_preview(
                chapters,
                user_id=user.app_user_id,
                book_id=book_id,
                original_name=str(meta.get("filename") or path.name),
                source_file_hash=source_hash,
                duplicate_matches=[item.to_dict() for item in duplicate_matches],
            )
            await add_audit(
                user.app_user_id,
                "book_file_parsed_web",
                "book",
                str(book_id),
                None,
                str(meta.get("filename") or path.name),
            )
            return {
                "ok": True,
                "preview_token": preview_token,
                "filename": str(meta.get("filename") or path.name),
                "report": report,
                "duplicates": [item.to_dict() for item in duplicate_matches],
            }
        except (ChunkedUploadError, BookParseError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Не удалось обработать файл. Проверьте его и попробуйте снова.") from exc
        finally:
            cleanup_upload(upload_id)

    @app.post("/api/author/book/{book_id}/import-confirm")
    async def api_author_import_confirm(
        book_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        token = str(payload.get("preview_token") or "")
        chapters, original_name = load_web_import_preview(
            token,
            user_id=user.app_user_id,
            book_id=book_id,
        )
        metadata = load_web_import_metadata(
            token, user_id=user.app_user_id, book_id=book_id
        )
        if not chapters:
            raise HTTPException(status_code=400, detail="Предпросмотр устарел. Загрузите файл заново.")
        duplicate_matches = list(metadata.get("duplicate_matches") or [])
        allow_duplicate = bool(payload.get("allow_duplicate"))
        if duplicate_matches and not allow_duplicate:
            raise HTTPException(
                status_code=409,
                detail="Похоже, такая книга уже есть. Подтвердите, что это новая редакция или другая книга.",
            )
        first_free = max(0, min(100000, int(payload.get("first_free") or 0)))
        default_price = max(0, min(100000, int(payload.get("default_price_stars") or 0)))
        import_result = await upsert_imported_chapters(
            book_id,
            chapters,
            first_free=first_free,
            default_price_stars=default_price,
            return_published_ids=True,
        )
        saved = int(import_result["saved"])
        published_ids = [int(item) for item in import_result["published_ids"]]
        await update_book_import_fingerprint(
            book_id,
            filename=str(metadata.get("original_name") or original_name or "book"),
            source_file_hash=str(metadata.get("source_file_hash") or ""),
            duplicate_override=allow_duplicate or not bool(duplicate_matches),
        )
        if allow_duplicate:
            await set_book_duplicate_override(book_id, True)
        delete_web_import_preview(token)
        await add_audit(user.app_user_id, "book_import_confirmed_web", "book", str(book_id), None, f"{original_name}:{saved}")
        notification = None
        book = await get_book(book_id)
        if book and book["publication_status"] == "published" and published_ids:
            notification = await notify_book_followers(
                book_id=book_id,
                event_key=f"chapter-batch:{book_id}:{max(published_ids)}:{len(published_ids)}",
                category="chapters",
                text=new_chapter_message(book["title"], count=len(published_ids)),
            )
            await add_audit(user.app_user_id, "chapter_followers_notified_web", "book", str(book_id), None, str(notification))
        workflow = None
        if settings.BOT_TOKEN:
            delivery_bot = Bot(token=settings.BOT_TOKEN)
            try:
                workflow_result = await finish_book_content_workflow(
                    bot=delivery_bot,
                    book_id=book_id,
                    actor_user_id=user.app_user_id,
                    actor_telegram_id=user.telegram_id,
                    source="miniapp_file_import",
                )
                workflow = {
                    "status": workflow_result.workflow_status,
                    "channel_status": workflow_result.channel_status,
                    "channel_message": workflow_result.channel_message,
                    "duplicate_text": workflow_result.duplicate_text,
                }
                message_text = (
                    f"Книга «{book['title']}» опубликована и добавлена в каталог. {workflow_result.channel_message}"
                    if workflow_result.workflow_status == "published"
                    else (
                        f"Книга «{book['title']}» отправлена на проверку."
                        if workflow_result.workflow_status == "review"
                        else "Главы сохранены, но публикация остановлена из-за возможной копии книги."
                    )
                )
                await delivery_bot.send_message(user.telegram_id, message_text)
            except Exception:
                workflow = workflow or {"status": "saved", "channel_status": "", "channel_message": ""}
            finally:
                await delivery_bot.session.close()
        return {
            "ok": True,
            "saved": saved,
            "notification": notification,
            "workflow": workflow,
            "book_id": book_id,
        }

    @app.get("/api/control/dashboard")
    async def api_control_dashboard(x_telegram_init_data: str | None = Header(default=None)):
        user, is_owner, permissions = await control_session(x_telegram_init_data)
        queues = await get_control_queue_counts()
        result: dict[str, Any] = {
            "ok": True,
            "role": "owner" if is_owner else "moderator",
            "name": user.full_name or user.username or "Пользователь",
            "permissions": sorted(permissions),
            "queues": {},
        }
        queue_map = {
            "books_review": "mod_books",
            "complaints_new": "complaints",
            "refunds_new": "refunds",
            "payouts_new": "payouts",
            "payouts_approved": "payouts",
            "comments": "mod_comments",
            "reviews": "mod_comments",
            "ads_running": "ads",
        }
        result["queues"] = {
            key: value for key, value in queues.items()
            if is_owner or queue_map.get(key) in permissions
        }
        if is_owner or "stats" in permissions:
            result["platform"] = await get_platform_stats()
            result["today"] = await get_owner_today_stats()
        if is_owner or permissions.intersection({"view_finance", "refunds", "payouts"}):
            result["finance"] = await get_platform_finance_summary()
        return result

    @app.get("/api/control/books")
    async def api_control_books(x_telegram_init_data: str | None = Header(default=None)):
        await control_session(x_telegram_init_data, "mod_books")
        return {"ok": True, "items": _rows_to_dicts(await list_books_for_moderation())}

    @app.post("/api/control/book/{book_id}/{action}")
    async def api_control_book_action(
        book_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "mod_books")
        if action not in {"publish", "reject"}:
            raise HTTPException(status_code=404, detail="Действие не найдено.")
        book = await get_book(book_id)
        if not book or book["publication_status"] != "review":
            raise HTTPException(status_code=409, detail="Книга уже обработана или не найдена.")
        chapters_count = await count_chapters_for_book(book_id)
        if action == "publish" and chapters_count < 1:
            raise HTTPException(status_code=409, detail="Нельзя публиковать книгу без глав.")

        channel_status = ""
        notification = "unavailable"
        if action == "publish":
            if not settings.BOT_TOKEN:
                raise HTTPException(status_code=503, detail="Бот временно недоступен для публикации.")
            bot = Bot(token=settings.BOT_TOKEN)
            try:
                result = await publish_book_and_channel(
                    bot,
                    book_id,
                    actor_user_id=user.app_user_id,
                    bypass_duplicate_guard=True,
                )
                if not result.published:
                    raise HTTPException(status_code=409, detail=result.channel_error or "Книгу не удалось опубликовать.")
                channel_status = result.channel_status
                await resolve_book_moderation(
                    book_id,
                    resolution="published",
                    actor_user_id=user.app_user_id,
                    note="Опубликовано после ручной проверки в Mini App",
                )
                notification = await notify_after_action(
                    actor_user_id=user.app_user_id,
                    event="book_published",
                    target_type="book",
                    target_id=book_id,
                    app_user_id=int(book["author_user_id"]) if book["author_user_id"] is not None else None,
                    telegram_id=int(book["author_telegram_id"]) if book["author_telegram_id"] is not None else None,
                    text=book_moderation_message(book["title"], "published"),
                )
                await notify_moderation_resolved(
                    bot,
                    book_id=book_id,
                    resolution="published",
                    actor_name=user.full_name or user.username or str(user.telegram_id),
                )
            finally:
                await bot.session.close()
            status = "published"
        else:
            reason = str((payload or {}).get("reason") or "").strip()
            if len(reason) < 8:
                raise HTTPException(status_code=400, detail="Укажите понятную причину возврата на доработку.")
            await set_book_publication_status(book_id, "draft")
            await resolve_book_moderation(
                book_id,
                resolution="revision",
                actor_user_id=user.app_user_id,
                note=reason,
            )
            await add_audit(user.app_user_id, "book_revision_web", "book", str(book_id), None, reason[:1000])
            notification = await notify_after_action(
                actor_user_id=user.app_user_id,
                event="book_revision",
                target_type="book",
                target_id=book_id,
                app_user_id=int(book["author_user_id"]) if book["author_user_id"] is not None else None,
                telegram_id=int(book["author_telegram_id"]) if book["author_telegram_id"] is not None else None,
                text=book_moderation_message(book["title"], "rejected", reason=reason, book_id=book_id),
                reply_markup=book_revision_markup(book_id),
            )
            if settings.BOT_TOKEN:
                bot = Bot(token=settings.BOT_TOKEN)
                try:
                    await notify_moderation_resolved(
                        bot,
                        book_id=book_id,
                        resolution="revision",
                        actor_name=user.full_name or user.username or str(user.telegram_id),
                    )
                finally:
                    await bot.session.close()
            status = "draft"

        return {
            "ok": True,
            "status": status,
            "channel_sent": channel_status == "sent",
            "channel_status": channel_status,
            "notification": notification,
        }


    @app.get("/api/control/comments")
    async def api_control_comments(x_telegram_init_data: str | None = Header(default=None)):
        await control_session(x_telegram_init_data, "mod_comments")
        return {
            "ok": True,
            "comments": _rows_to_dicts(await list_moderation_comments(50)),
            "reviews": _rows_to_dicts(await list_moderation_reviews(50)),
        }

    @app.post("/api/control/{kind}/{item_id}/hide")
    async def api_control_hide_content(
        kind: str,
        item_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "mod_comments")
        if kind == "comment":
            item = await get_comment_for_moderation(item_id)
            if not item or item["status"] != "published":
                raise HTTPException(status_code=409, detail="Комментарий уже обработан или не найден.")
            await set_comment_status(item_id, "hidden")
            message = content_hidden_message("comment", item["book_title"], item["chapter_title"])
        elif kind == "review":
            item = await get_review_for_moderation(item_id)
            if not item or item["status"] != "published":
                raise HTTPException(status_code=409, detail="Отзыв уже обработан или не найден.")
            await set_review_status(item_id, "hidden")
            message = content_hidden_message("review", item["book_title"])
        else:
            raise HTTPException(status_code=404, detail="Материал не найден.")
        await add_audit(user.app_user_id, f"{kind}_hidden_web", kind, str(item_id))
        notification = await notify_after_action(
            actor_user_id=user.app_user_id,
            event=f"{kind}_hidden",
            target_type=kind,
            target_id=item_id,
            app_user_id=int(item["user_id"]),
            telegram_id=int(item["telegram_id"]),
            text=message,
        )
        return {"ok": True, "notification": notification}

    @app.get("/api/control/complaints")
    async def api_control_complaints(
        status: str = "new",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "complaints")
        if status not in {"new", "pending", "closed"}:
            status = "new"
        return {"ok": True, "items": _rows_to_dicts(await list_complaints(status, 60))}

    @app.post("/api/control/complaint/{complaint_id}/{status}")
    async def api_control_complaint_action(
        complaint_id: int,
        status: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "complaints")
        if status not in {"pending", "closed"}:
            raise HTTPException(status_code=404, detail="Действие не найдено.")
        complaint = await get_complaint(complaint_id)
        if not complaint:
            raise HTTPException(status_code=404, detail="Жалоба не найдена.")
        current = str(complaint["status"])
        allowed = (current == "new" and status in {"pending", "closed"}) or (current == "pending" and status == "closed")
        if not allowed:
            raise HTTPException(status_code=409, detail="Жалоба уже обработана.")
        await set_complaint_status(complaint_id, status, user.app_user_id)
        await add_audit(user.app_user_id, f"complaint_{status}_web", "complaint", str(complaint_id))
        notification = await notify_after_action(
            actor_user_id=user.app_user_id,
            event=f"complaint_{status}",
            target_type="complaint",
            target_id=complaint_id,
            app_user_id=int(complaint["user_id"]) if complaint["user_id"] is not None else None,
            telegram_id=int(complaint["telegram_id"]) if complaint["telegram_id"] is not None else None,
            text=complaint_message(status),
        )
        return {"ok": True, "status": status, "notification": notification}

    @app.get("/api/control/refunds")
    async def api_control_refunds(
        status: str = "new",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "refunds")
        if status not in {"new", "refunded", "rejected"}:
            status = "new"
        return {"ok": True, "items": _rows_to_dicts(await list_refund_requests(status, 60))}

    @app.post("/api/control/refund/{refund_id}/reject")
    async def api_control_refund_reject(
        refund_id: int,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "refunds")
        refund = await get_refund_request(refund_id)
        if not refund or refund["status"] not in {"new", "pending"}:
            raise HTTPException(status_code=409, detail="Запрос уже обработан.")
        note = str((payload or {}).get("note") or "Запрос не прошёл проверку")[:500]
        if not await reject_refund_request(refund_id, user.app_user_id, note):
            raise HTTPException(status_code=409, detail="Запрос уже обработан.")
        await add_audit(user.app_user_id, "refund_rejected_web", "refund", str(refund_id))
        notification = await notify_after_action(
            actor_user_id=user.app_user_id,
            event="refund_rejected",
            target_type="refund",
            target_id=refund_id,
            app_user_id=int(refund["user_id"]),
            telegram_id=int(refund["telegram_id"]),
            text=refund_message("rejected", refund["amount_stars"], note),
        )
        return {"ok": True, "status": "rejected", "notification": notification}

    @app.post("/api/control/refund/{refund_id}/approve")
    async def api_control_refund_approve(
        refund_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "refunds")
        refund = await get_refund_request(refund_id)
        if not refund or refund["status"] not in {"new", "pending"} or refund["purchase_status"] != "paid":
            raise HTTPException(status_code=409, detail="Запрос уже обработан или недоступен.")
        if not settings.BOT_TOKEN:
            raise HTTPException(status_code=503, detail="Возврат временно недоступен.")
        bot = Bot(token=settings.BOT_TOKEN)
        try:
            await bot.refund_star_payment(
                user_id=int(refund["telegram_id"]),
                telegram_payment_charge_id=str(refund["telegram_payment_charge_id"]),
            )
        except Exception:
            await add_audit(user.app_user_id, "refund_failed_web", "refund", str(refund_id))
            raise HTTPException(status_code=502, detail="Telegram не подтвердил возврат. Попробуйте позже.")
        finally:
            await bot.session.close()
        if not await finalize_refund(refund_id, user.app_user_id):
            raise HTTPException(status_code=409, detail="Возврат уже был обработан.")
        await add_audit(user.app_user_id, "refund_approved_web", "refund", str(refund_id))
        notification = await notify_after_action(
            actor_user_id=user.app_user_id,
            event="refund_refunded",
            target_type="refund",
            target_id=refund_id,
            app_user_id=int(refund["user_id"]),
            telegram_id=int(refund["telegram_id"]),
            text=refund_message("refunded", refund["amount_stars"]),
        )
        return {"ok": True, "status": "refunded", "notification": notification}

    @app.get("/api/control/payouts")
    async def api_control_payouts(
        status: str = "new",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "payouts")
        if status not in {"new", "approved", "frozen", "paid", "rejected"}:
            status = "new"
        return {"ok": True, "items": _rows_to_dicts(await list_payout_requests(status, 60))}

    @app.post("/api/control/payout/{payout_id}/{action}")
    async def api_control_payout_action(
        payout_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "payouts")
        payout = await get_payout_request(payout_id)
        if not payout:
            raise HTTPException(status_code=404, detail="Заявка не найдена.")
        note = str((payload or {}).get("note") or "")[:1200]
        if action == "freeze":
            target_status = "frozen"
            action_note = note or "Выплата приостановлена до дополнительной проверки"
        elif action == "unfreeze":
            target_status = "new"
            action_note = note or "Проверка выплаты возобновлена"
        elif action in {"approve", "paid", "reject"}:
            target_status = {"approve": "approved", "paid": "paid", "reject": "rejected"}[action]
            action_note = note or {
                "approved": "Выплата одобрена",
                "paid": "Выплата выполнена",
                "rejected": "Выплата не прошла проверку",
            }[target_status]
        else:
            raise HTTPException(status_code=404, detail="Действие не найдено.")
        if not await set_payout_request_status(payout_id, target_status, user.app_user_id, action_note):
            raise HTTPException(status_code=409, detail="Для текущего статуса это действие недоступно.")
        if action == "freeze":
            await set_author_payout_frozen(int(payout["author_id"]), True, action_note, user.app_user_id)
        elif action == "unfreeze":
            await set_author_payout_frozen(int(payout["author_id"]), False, "", user.app_user_id)
        await add_audit(user.app_user_id, f"payout_{target_status}_web", "payout", str(payout_id))
        notification = await notify_after_action(
            actor_user_id=user.app_user_id,
            event=f"payout_{target_status}",
            target_type="payout",
            target_id=payout_id,
            app_user_id=int(payout["author_user_id"]),
            telegram_id=int(payout["telegram_id"]),
            text=payout_message(target_status, payout["amount_stars"], action_note),
        )
        return {"ok": True, "status": target_status, "notification": notification}

    @app.get("/media/audio/{audio_id}")
    async def audio_media(audio_id: int):
        audio = await get_audio_chapter(audio_id)
        if not audio or audio["publication_status"] != "published" or audio["status"] != "published" or not audio["file_path"]:
            raise HTTPException(status_code=404, detail="Аудиоглава не найдена.")
        if not audio["is_free"]:
            raise HTTPException(status_code=403, detail="Аудио открывается после проверки доступа в Telegram.")
        path = Path(audio["file_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Аудиофайл не найден.")
        return FileResponse(path, media_type=audio["mime_type"] or "audio/mpeg", filename=audio["source_filename"] or path.name)

    return app
