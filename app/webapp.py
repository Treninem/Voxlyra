from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from aiogram import Bot
from aiogram.types import LabeledPrice

from app.config import settings
from app.build_info import OWNER_BUILD_VERSION
from app.legal_texts import LEGAL_DOCS, REQUIRED_ON_START, all_docs, get_doc, operator_is_configured
from app.services.legal_documents import ensure_legal_pdf
from app.services.secure_fields import decrypt_text, encrypt_text, mask_phone
from app.services.yookassa_payouts import create_sbp_payout, list_sbp_banks, normalize_phone, payouts_configured, YooKassaPayoutError
from app.services.payment_runtime import load_runtime_payment_settings, public_runtime_payment_settings, update_runtime_payment_settings
from app.services.yookassa_checkout import test_shop_connection, YooKassaCheckoutError
from app.services.pricing import split_platform_commission, final_price_for_desired_net, two_rate_price
from app.services.rankings import attach_ranking, attach_rankings
from app.db import (
    add_comment,
    add_audit,
    add_manual_chapter,
    add_graphic_pages,
    book_belongs_to_author,
    create_book,
    create_graphic_chapter_record,
    delete_graphic_chapter_for_author,
    count_chapters_for_book,
    get_adjacent_audio_chapters,
    get_adjacent_chapters,
    get_adjacent_chapters_for_moderation,
    get_adjacent_graphic_chapters,
    get_audio_chapter,
    get_book,
    get_book_options,
    get_author_dashboard_stats,
    get_author_analytics,
    get_author_finance_summary,
    get_author_financial_profile,
    get_author_rub_finance_summary,
    get_author_profile,
    create_author_rub_payout_request,
    get_author_rub_payout_request,
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
    get_legal_acceptances,
    get_missing_legal_documents,
    get_graphic_chapter,
    get_graphic_page,
    get_graphic_reading_progress,
    get_reader_ad_settings,
    get_reading_progress,
    get_book_assistant_cache,
    save_book_assistant_cache,
    list_book_assistant_chapters,
    search_book_assistant_chapters,
    get_user_review,
    get_user_preferences,
    get_user_premium_status,
    list_premium_plans,
    set_premium_auto_renew,
    set_premium_plan_settings,
    get_premium_owner_summary,
    get_personal_reading_insights,
    sync_user_achievements,
    get_user_by_id,
    search_users,
    list_grantable_books,
    resolve_chapters_by_numbers,
    grant_manual_chapter_access,
    grant_premium_manually,
    list_manual_access_grants,
    revoke_manual_access_grant,
    record_premium_content_event,
    get_tts_progress,
    has_purchase_access,
    mark_purchase_access_used,
    init_db,
    list_audio_chapters_for_book,
    list_author_books_with_counts,
    list_graphic_chapters_for_book,
    list_graphic_volumes_for_book,
    get_graphic_volume,
    upsert_graphic_volume_for_author,
    set_graphic_chapter_preview_for_author,
    list_graphic_page_reports,
    create_graphic_page_report,
    moderate_graphic_page,
    set_graphic_page_report_status,
    list_graphic_pages,
    list_graphic_pages_for_author,
    upsert_graphic_page_text,
    list_graphic_page_texts,
    replace_graphic_translation_regions_for_author,
    list_graphic_translation_regions,
    replace_graphic_frames_for_author,
    list_graphic_page_frames,
    get_graphic_reader_layers,
    search_graphic_book_text,
    toggle_graphic_page_bookmark,
    list_user_graphic_bookmarks,
    add_graphic_page_comment,
    list_graphic_page_comments_for_moderation,
    set_graphic_page_comment_status,
    record_graphic_reading_event,
    get_graphic_chapter_statistics,
    list_books_for_moderation,
    list_complaints,
    list_refund_requests,
    get_refund_request,
    get_rub_control_summary,
    get_complaint,
    get_comment_for_moderation,
    get_public_comment,
    get_review_for_moderation,
    finalize_refund,
    reject_refund_request,
    list_payout_requests,
    list_author_financial_profiles,
    list_author_rub_payout_requests,
    get_payout_request,
    set_payout_request_status,
    set_author_payout_frozen,
    set_author_financial_profile_status,
    list_moderation_comments,
    list_moderation_reviews,
    set_comment_status,
    set_chapter_reaction,
    set_review_status,
    set_complaint_status,
    set_book_publication_status,
    resolve_book_moderation,
    resolve_comment_complaints,
    publish_book_content,
    list_catalog_books,
    list_chapters_for_book,
    list_chapter_packages_for_book,
    get_chapter_package,
    create_chapter_package_for_author,
    update_chapter_package_for_author,
    deactivate_chapter_package_for_author,
    get_user_chapter_credit_summary,
    list_user_chapter_package_balances,
    redeem_chapter_package_credit,
    list_comments_for_chapter,
    list_chapter_reactions,
    list_contextual_book_ads,
    list_reviews_for_book,
    list_similar_books,
    list_personalized_books,
    record_recommendation_event,
    record_recommendation_events,
    list_user_bookmarks,
    list_user_continue_listening,
    list_user_continue_reading,
    list_user_purchases,
    record_reader_ad_event,
    remove_bookmark,
    save_listening_progress,
    save_graphic_reading_progress,
    soft_delete_book,
    soft_delete_chapter_for_author,
    submit_book_for_review,
    update_author_book_fields,
    update_book_price,
    get_book_pricing_state,
    restore_saved_chapter_prices,
    upsert_author_financial_profile,
    update_author_rub_payout_status,
    update_chapter_price,
    update_chapter_price_range,
    update_chapter_access_range,
    update_chapter_text,
    update_chapter_title,
    update_graphic_chapter_for_author,
    reorder_graphic_pages_for_author,
    update_graphic_page_file_for_author,
    delete_graphic_page_for_author,
    upsert_imported_chapters,
    update_book_import_fingerprint,
    set_book_duplicate_override,
    save_reading_progress,
    save_tts_progress,
    set_bookmark,
    set_chapter_status,
    set_graphic_chapter_status,
    set_user_preference,
    reset_user_preferences,
    upsert_review,
    toggle_comment_like,
    report_comment,
    user_can_access_audio,
    user_can_access_chapter,
    user_can_access_graphic,
)
from app.services.tma_auth import TMAAuthError, TMAUser, authenticate_init_data
from app.permissions import PERMISSION_BY_CODE
from app.keyboards import author_book_card_menu
from app.services.diagnostics import diagnostics_summary
from app.services.book_assistant import (
    answer_question as answer_book_question,
    build_chapter_analysis,
    build_recap as build_book_recap,
    question_keywords as book_question_keywords,
    text_digest as book_text_digest,
)
from app.services.quote_cards import build_quote_card, normalize_quote, quote_belongs_to_text
from app.services.book_parser import BookParseError, build_import_report, parse_book_file
from app.services.access_grants import ChapterSelectionError, parse_chapter_selection
from app.services.duplicate_books import find_book_duplicates, sha256_file
from app.services.graphic_import import (
    GraphicImportError,
    PreparedGraphicPage,
    graphic_report,
    prepare_graphic_file,
    prepare_graphic_images,
    prepare_replacement_page,
    rotate_graphic_page_file,
)
from app.services.graphic_ocr import (
    GraphicOCRError,
    ocr_engine_available,
    recognize_graphic_text,
    suggest_graphic_frames,
)
from app.services.graphic_storage import (
    delete_page_files,
    graphic_storage_root,
    install_prepared_page,
    public_variant_info,
    safe_graphic_path,
    select_page_variant,
)
from app.services.chunked_upload import (
    ChunkedUploadError,
    CHUNK_SIZE_BYTES,
    assemble_upload,
    cleanup_upload,
    create_graphic_upload,
    create_upload,
    get_upload_status,
    load_upload,
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
from app.services.tts_providers import (
    TTSProviderError,
    TTSProviderUnavailable,
    get_vosk_voice_profile,
    set_vosk_voice_selection,
    vosk_sample_path,
)
from app.services.tts_queue import build_default_generation_queue
from app.services.tts_sessions import (
    TTSSessionManager,
    TTSSessionNotFound,
    TTSSegmentNotReady,
    validate_segment_media_token,
)
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


GRAPHIC_CONTENT_TYPES = {"comic", "manga", "manhwa", "webtoon", "graphic_novel"}
GRAPHIC_READING_MODES = {"ltr", "rtl", "vertical", "single", "spread", "inherit"}
GRAPHIC_STORAGE_ROOT = graphic_storage_root()
GRAPHIC_TEMP_ROOT = Path("storage/temp/graphic_imports")


def _reader_watermark_label(user: TMAUser) -> str:
    name = (user.full_name or (f"@{user.username}" if user.username else "Читатель")).strip()[:48]
    tail = str(user.telegram_id)[-4:]
    return f"{name} · {tail}"


async def _content_protection_payload(user: TMAUser, *, allow_download: bool, book_id: int) -> dict[str, Any]:
    cfg = await load_runtime_payment_settings()
    protected = bool(cfg.content_protection_enabled and not allow_download)
    return {
        "protected": protected,
        "allow_copy": not protected,
        "allow_download": bool(allow_download),
        "download_url": f"/api/book/{int(book_id)}/download.txt" if allow_download else "",
        "watermark": _reader_watermark_label(user) if protected and cfg.watermark_enabled else "",
        "screenshot_block_guaranteed": False,
    }


def _graphic_signing_secret() -> bytes:
    value = (
        settings.COMIC_SIGNING_SECRET.strip()
        or settings.TTS_SIGNING_SECRET.strip()
        or settings.BOT_TOKEN.strip()
        or "voxlyra-local-comic-secret"
    )
    return value.encode("utf-8")


def _graphic_media_token(*, user_id: int, chapter_id: int, page_number: int, expires_at: int) -> str:
    payload = f"{int(user_id)}:{int(chapter_id)}:{int(page_number)}:{int(expires_at)}".encode("utf-8")
    return hmac.new(_graphic_signing_secret(), payload, hashlib.sha256).hexdigest()


def _validate_graphic_media_token(
    *,
    user_id: int,
    chapter_id: int,
    page_number: int,
    expires_at: int,
    token: str,
) -> bool:
    if int(expires_at) < int(time.time()):
        return False
    expected = _graphic_media_token(
        user_id=user_id, chapter_id=chapter_id, page_number=page_number, expires_at=expires_at
    )
    return hmac.compare_digest(expected, str(token or ""))


def _safe_graphic_path(value: str) -> Path | None:
    return safe_graphic_path(value, root=GRAPHIC_STORAGE_ROOT)


async def _commit_graphic_chapter(
    *,
    book_id: int,
    title: str,
    reading_mode: str,
    price_stars: int,
    source_filename: str,
    prepared_pages: list[PreparedGraphicPage],
    volume_number: int = 1,
    volume_title: str = "",
    preview_pages: int = 3,
) -> dict[str, Any]:
    chapter_id = await create_graphic_chapter_record(
        book_id,
        title,
        reading_mode=reading_mode,
        is_free=int(price_stars or 0) <= 0,
        price_stars=int(price_stars or 0),
        source_filename=source_filename,
        volume_number=max(1, int(volume_number or 1)),
        volume_title=str(volume_title or "").strip()[:120],
        preview_pages=max(0, min(50, int(preview_pages or 0))),
    )
    final_dir = GRAPHIC_STORAGE_ROOT / str(int(book_id)) / str(int(chapter_id))
    shutil.rmtree(final_dir, ignore_errors=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    try:
        for page in prepared_pages:
            target = final_dir / f"page-{int(page.number):05d}.webp"
            installed = install_prepared_page(page, target)
            rows.append(
                {
                    "number": int(page.number),
                    "file_path": installed["file_path"],
                    "source_filename": page.source_filename,
                    "mime_type": installed["mime_type"],
                    "width": installed["width"],
                    "height": installed["height"],
                    "file_size": installed["file_size"],
                    "checksum": installed["checksum"],
                    "variants_json": installed["variants_json"],
                    "storage_backend": installed["storage_backend"],
                    "storage_key": installed["storage_key"],
                }
            )
        await add_graphic_pages(chapter_id, rows)
    except Exception:
        shutil.rmtree(final_dir, ignore_errors=True)
        await set_graphic_chapter_status(chapter_id, "deleted")
        raise
    chapter = await get_graphic_chapter(chapter_id)
    return _row_to_dict(chapter) if chapter else {"id": chapter_id, "pages_count": len(rows)}


async def _notify_graphic_chapter_if_published(*, book_id: int, chapter: dict[str, Any], actor_user_id: int) -> dict[str, Any] | None:
    chapter_id = int(chapter.get("id") or 0)
    if chapter_id <= 0:
        return None
    current = await get_graphic_chapter(chapter_id)
    book = await get_book(book_id)
    if not current or not book or str(book["publication_status"] or "") != "published" or str(current["status"] or "") != "published":
        return None
    notification = await notify_book_followers(
        book_id=book_id,
        event_key=f"graphic-chapter:{chapter_id}:published",
        category="chapters",
        text=new_chapter_message(str(book["title"] or "Произведение"), str(current["title"] or "Новая глава"), int(current["number"] or 0)),
    )
    await add_audit(actor_user_id, "graphic_chapter_followers_notified_web", "graphic_chapter", str(chapter_id), None, str(notification))
    return notification


def _graphic_page_payloads(*, user_id: int, chapter_id: int, pages: list[Any], include_ids: bool = False) -> list[dict[str, Any]]:
    expires_at = int(time.time()) + 60 * 30
    result: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page["page_number"])
        token = _graphic_media_token(
            user_id=int(user_id),
            chapter_id=int(chapter_id),
            page_number=page_number,
            expires_at=expires_at,
        )
        base_url = (
            f"/media/comic/{int(chapter_id)}/{page_number}"
            f"?user_id={int(user_id)}&expires={expires_at}&token={token}"
        )
        variant_meta = public_variant_info(page, root=GRAPHIC_STORAGE_ROOT)
        item: dict[str, Any] = {
            "number": page_number,
            "width": int(page["width"] or 0),
            "height": int(page["height"] or 0),
            "file_size": int(page["file_size"] or 0),
            "source_filename": str(page["source_filename"] or ""),
            "cache_key": str(page["checksum"] or f"{chapter_id}:{page_number}"),
            "url": base_url,
            "variants": {
                label: {**meta, "url": f"{base_url}&variant={label}"}
                for label, meta in variant_meta.items()
            },
        }
        if include_ids:
            item["id"] = int(page["id"])
        result.append(item)
    return result


def _graphic_type_label(content_type: str) -> str:
    return {
        "comic": "Комикс",
        "manga": "Манга",
        "manhwa": "Манхва",
        "webtoon": "Вебтун",
        "graphic_novel": "Графический роман",
    }.get(str(content_type or ""), "Графическое произведение")


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


async def _notify_new_achievements(user: TMAUser, payload: dict[str, Any]) -> None:
    for item in list(payload.get("new") or [])[:3]:
        await send_user_notification(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            text=(
                f"{item.get('icon', '✦')} Новое достижение\n\n"
                f"{item.get('title', 'Награда')}\n{item.get('description', '')}"
            ),
            category="achievements",
        )


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


async def _graphic_access(*, app_user_id: int, telegram_id: int, chapter: Any) -> tuple[bool, bool]:
    """Доступ к графической главе с теми же правилами проверки, что и для текста."""
    is_public = (
        str(chapter["publication_status"] or "") == "published"
        and str(chapter["status"] or "") == "published"
    )
    if is_public and await user_can_access_graphic(int(app_user_id), int(chapter["id"])):
        return True, False
    if int(telegram_id) in settings.owner_ids:
        moderation = str(chapter["publication_status"] or "") != "deleted" and str(chapter["status"] or "") != "deleted"
        return moderation, moderation
    permissions = await get_admin_permissions(int(app_user_id))
    moderation = (
        "mod_books" in permissions
        and str(chapter["status"] or "") != "deleted"
        and str(chapter["publication_status"] or "") in {"published", "review"}
    )
    return moderation, moderation


async def _book_assistant_accessible_rows(user_id: int, rows: list[Any]) -> list[dict[str, Any]]:
    """Оставляет только главы, которые читатель действительно может открыть."""
    result: list[dict[str, Any]] = []
    for row in rows:
        chapter_id = int(row["id"])
        if await user_can_access_chapter(int(user_id), chapter_id):
            result.append(_row_to_dict(row))
    return result


async def _book_assistant_analyze_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Разбирает главы локально и использует кэш, привязанный к хэшу текста."""
    result: list[dict[str, Any]] = []
    for row in rows:
        text = str(row.get("text") or "")
        digest = book_text_digest(text)
        cached = await get_book_assistant_cache(int(row["id"]), digest)
        if cached is None:
            cached = build_chapter_analysis(text)
            await save_book_assistant_cache(
                int(row["id"]),
                str(cached["digest"]),
                str(cached["summary"]),
                list(cached["characters"]),
                list(cached["terms"]),
            )
        enriched = dict(row)
        enriched.update({
            "summary": str(cached.get("summary") or ""),
            "characters": list(cached.get("characters") or []),
            "terms": list(cached.get("terms") or []),
        })
        result.append(enriched)
    return result


def _book_assistant_merge_entities(rows: list[dict[str, Any]], key: str, label_key: str, limit: int = 14) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        for item in row.get(key) or []:
            label = str(item.get(label_key) or "").strip()
            if not label:
                continue
            normalized = label.lower().replace("ё", "е")
            current = merged.setdefault(normalized, {label_key: label, "count": 0, "excerpt": "", "chapter_number": 0})
            current["count"] += int(item.get("count") or 1)
            if not current["excerpt"]:
                current["excerpt"] = str(item.get("excerpt") or "")[:260]
                current["chapter_number"] = int(row.get("number") or 0)
    values = list(merged.values())
    values.sort(key=lambda item: (-int(item.get("count") or 0), str(item.get(label_key) or "")))
    return values[:max(1, int(limit))]


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
    graphics = [row for row in books if str(row["content_type"] or "book") != "book"][:8]
    return {
        "newest": newest,
        "popular": popular,
        "audio_books": audio,
        "free_books": free,
        "graphic_books": graphics,
    }


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await init_db()
        tts_sessions = TTSSessionManager(build_default_generation_queue())
        application.state.tts_sessions = tts_sessions
        await tts_sessions.start()
        try:
            yield
        finally:
            await tts_sessions.close()

    app = FastAPI(title="Вокслира", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/comic-sw.js", include_in_schema=False)
    async def comic_service_worker() -> FileResponse:
        response = FileResponse(
            Path("static/js/comic-sw.js"),
            media_type="application/javascript; charset=utf-8",
        )
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
        return response
    templates = Jinja2Templates(directory="templates")
    templates.env.globals["asset_version"] = OWNER_BUILD_VERSION

    def common_context(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        data = {"project_name": settings.PROJECT_NAME}
        if extra:
            data.update(extra)
        return data

    async def price_context() -> dict[str, Any]:
        cfg = await load_runtime_payment_settings()
        return {
            "buyer_star_rate_minor": cfg.buyer_star_rate_minor,
            "buyer_star_rate_rubles": cfg.buyer_star_rate_minor / 100,
            "author_star_rate_minor": cfg.author_star_rate_minor,
        }

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

    async def premium_invoice_link(plan: dict[str, Any]) -> str:
        if not settings.BOT_TOKEN.strip():
            raise HTTPException(status_code=503, detail="Бот не настроен для оплаты Premium.")
        bot = Bot(token=settings.BOT_TOKEN)
        try:
            return await bot.create_invoice_link(
                title=str(plan.get("title") or "VoxLyra Premium")[:32],
                description=str(plan.get("description") or "Дополнительные возможности VoxLyra")[:255],
                payload=f"vox:premium:{plan.get('code') or 'monthly'}",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label="Premium на 30 дней", amount=int(plan.get("price_stars") or 0))],
                subscription_period=int(plan.get("subscription_period_seconds") or 2_592_000),
            )
        finally:
            await bot.session.close()

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

    @app.get("/legal", response_class=HTMLResponse)
    async def legal_index(request: Request):
        return templates.TemplateResponse(
            request,
            "legal.html",
            common_context({
                "documents": all_docs(),
                "document": None,
                "operator_configured": operator_is_configured(),
            }),
        )

    @app.get("/legal/{code}.pdf")
    async def legal_pdf(code: str):
        doc = get_doc(code)
        if not doc:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        path = ensure_legal_pdf(doc.code)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=doc.filename,
            headers={"Cache-Control": "public, max-age=3600", "X-Legal-Document-Hash": doc.digest},
        )

    @app.get("/legal/{code}", response_class=HTMLResponse)
    async def legal_document_page(request: Request, code: str):
        doc = get_doc(code)
        if not doc:
            raise HTTPException(status_code=404, detail="Документ не найден.")
        return templates.TemplateResponse(
            request,
            "legal.html",
            common_context({
                "documents": all_docs(),
                "document": doc,
                "operator_configured": operator_is_configured(),
            }),
        )

    @app.get("/api/legal/status")
    async def api_legal_status(x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        required = [(code, LEGAL_DOCS[code].version, LEGAL_DOCS[code].digest) for code in REQUIRED_ON_START]
        missing = await get_missing_legal_documents(user.app_user_id, required)
        accepted = await get_legal_acceptances(user.app_user_id)
        return {
            "ok": True,
            "required_version": LEGAL_DOCS["terms"].version,
            "missing": missing,
            "accepted": [
                {
                    "code": row["doc_code"],
                    "version": row["doc_version"],
                    "accepted_at": row["accepted_at"],
                    "active": not bool(row["withdrawn_at"]),
                }
                for row in accepted
            ],
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        books = await attach_rankings(await list_catalog_books(limit=80, include_drafts=False))
        sections = _showcase_sections(books)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            common_context({"books": books, **sections, **(await price_context())}),
        )

    @app.get("/catalog", response_class=HTMLResponse)
    async def catalog(request: Request):
        books = await attach_rankings(await list_catalog_books(limit=80, include_drafts=False))
        sections = _showcase_sections(books)
        return templates.TemplateResponse(
            request,
            "catalog.html",
            common_context({"books": books, **sections, **(await price_context())}),
        )

    @app.get("/api/recommendations/for-you")
    async def api_recommendations_for_you(
        limit: int = 12,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        items = await list_personalized_books(user.app_user_id, limit=max(1, min(30, int(limit))))
        if items:
            items = await attach_rankings(items)
        return {
            "ok": True,
            "items": items,
            "personalized": bool(items and items[0].get("recommendation_personalized")),
            "privacy_note": "Подборка строится только по действиям внутри VoxLyra и не влияет на публичные топы.",
        }

    @app.post("/api/recommendations/events")
    async def api_recommendation_events(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        event_type = str((payload or {}).get("event_type") or "").strip().lower()
        if event_type == "impression":
            raw_ids = (payload or {}).get("book_ids") or []
            if not isinstance(raw_ids, list):
                raise HTTPException(status_code=400, detail="Неверный список рекомендаций.")
            saved = await record_recommendation_events(user.app_user_id, raw_ids, "impression")
            return {"ok": True, "saved": saved}
        if event_type not in {"open", "dismiss"}:
            raise HTTPException(status_code=400, detail="Неизвестное действие с рекомендацией.")
        try:
            book_id = int((payload or {}).get("book_id") or 0)
        except (TypeError, ValueError):
            book_id = 0
        if book_id <= 0:
            raise HTTPException(status_code=400, detail="Книга не указана.")
        saved = await record_recommendation_event(
            user.app_user_id,
            book_id,
            event_type,
            str((payload or {}).get("reason") or ""),
        )
        if not saved:
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        return {"ok": True, "saved": 1}

    @app.get("/comics", response_class=HTMLResponse)
    async def comics_catalog(request: Request):
        rows = await list_catalog_books(limit=120, include_drafts=False)
        graphic_rows = [row for row in rows if str(row["content_type"] or "book") in GRAPHIC_CONTENT_TYPES]
        books = await attach_rankings(graphic_rows, category="comic")
        return templates.TemplateResponse(
            request,
            "comics.html",
            common_context({"books": books, **(await price_context())}),
        )

    @app.get("/book/{book_id}", response_class=HTMLResponse)
    async def book(request: Request, book_id: int):
        book_row = await get_book_with_counts(book_id)
        if book_row and book_row["publication_status"] != "published":
            book_row = None
        if book_row:
            book_row = await attach_ranking(book_row)
        chapters = await list_chapters_for_book(book_id, published_only=True) if book_row else []
        graphic_chapters = await list_graphic_chapters_for_book(book_id, published_only=True) if book_row else []
        graphic_volumes = await list_graphic_volumes_for_book(book_id, published_only=True) if book_row else []
        chapter_packages = await list_chapter_packages_for_book(book_id) if book_row else []
        audios = await list_audio_chapters_for_book(book_id, published_only=True) if book_row else []
        options = await get_book_options(book_id) if book_row else {}
        similar_books = await list_similar_books(book_id, limit=6) if book_row else []
        if similar_books:
            similar_books = await attach_rankings(similar_books)
        public_reviews = await list_reviews_for_book(book_id, limit=20) if book_row else []
        return templates.TemplateResponse(
            request,
            "book.html",
            {
                "book": book_row,
                "book_id": book_id,
                "chapters": chapters,
                "graphic_chapters": graphic_chapters,
                "graphic_volumes": graphic_volumes,
                "graphic_volume_map": {int(row["volume_number"]): row for row in graphic_volumes},
                "chapter_packages": chapter_packages,
                "audios": audios,
                "options": options,
                "similar_books": similar_books,
                "public_reviews": public_reviews,
                "project_name": settings.PROJECT_NAME,
                "bot_username": settings.BOT_USERNAME.strip().lstrip("@"),
                "channel_promotion_enabled": bool(settings.CHANNEL_ID.strip()),
                **(await price_context()),
            },
        )

    @app.get("/api/book/{book_id}/download.txt")
    async def api_download_book_text(
        book_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        book_row = await get_book(book_id)
        if not book_row or book_row["publication_status"] == "deleted":
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        if int(book_row["allow_download"] or 0) != 1:
            raise HTTPException(status_code=403, detail="Автор разрешил только чтение внутри приложения.")
        is_author = await book_belongs_to_author(book_id, user.app_user_id)
        chapters = await list_chapters_for_book(book_id, published_only=not is_author)
        available = []
        for chapter_row in chapters:
            if is_author or await user_can_access_chapter(user.app_user_id, int(chapter_row["id"])):
                available.append(chapter_row)
        if not available:
            raise HTTPException(status_code=403, detail="Нет доступных для скачивания глав.")
        if not is_author:
            for chapter_row in available:
                if int(chapter_row["is_free"] or 0) != 1 and int(chapter_row["price_stars"] or 0) > 0:
                    await mark_purchase_access_used(
                        user.app_user_id, chapter_id=int(chapter_row["id"])
                    )
        parts = [str(book_row["title"] or "Книга"), f"Автор: {book_row['pen_name'] or 'не указан'}", ""]
        for chapter_row in available:
            parts.extend([
                f"Глава {int(chapter_row['number'])}. {chapter_row['title']}",
                "",
                str(chapter_row["text"] or ""),
                "",
            ])
        safe_name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(book_row["title"] or "book"))[:80] or "book"
        content = "\n".join(parts).encode("utf-8")
        return Response(
            content=content,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}.txt"; filename*=UTF-8\'\'{safe_name}.txt',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
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
        book_price = int(chapter["book_price_stars"] or 0) if chapter else 0
        declared_mode = str(chapter["pricing_type"] or "").strip().lower() if chapter else ""
        pricing_mode = (
            "premium" if declared_mode == "premium"
            else ("free" if book_price <= 0 else ("chapters" if declared_mode == "chapters" else "whole_book"))
        )
        chapter_is_free = bool(
            chapter
            and (pricing_mode == "free" or int(chapter["is_free"] or 0) == 1)
        )
        can_buy_chapter = bool(
            is_public
            and chapter
            and pricing_mode == "chapters"
            and not chapter_is_free
            and int(chapter["price_stars"] or 0) > 0
        )
        purchase_url = _bot_purchase_url("chapter", chapter_id) if can_buy_chapter else ""
        book_purchase_url = (
            _bot_purchase_url("book", int(chapter["book_id"]))
            if is_public and chapter and pricing_mode not in {"free", "premium"}
            else ""
        )
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
        runtime_cfg = await load_runtime_payment_settings()
        server_text_visible = bool(
            is_public
            and chapter
            and chapter_is_free
            and (not runtime_cfg.content_protection_enabled or int(chapter["allow_download"] or 0) == 1)
        )
        return templates.TemplateResponse(
            request,
            "reader.html",
            {
                "chapter": chapter,
                "chapter_id": chapter_id,
                "purchase_url": purchase_url,
                "book_purchase_url": book_purchase_url,
                "pricing_mode": pricing_mode,
                "chapter_is_free": chapter_is_free,
                "can_buy_chapter": can_buy_chapter,
                "reader_ads": ads,
                "ad_settings": ad_settings,
                "previous_chapter": adjacent.get("previous"),
                "next_chapter": adjacent.get("next"),
                "chapter_bounds": chapter_bounds,
                "server_text_visible": server_text_visible,
                "project_name": settings.PROJECT_NAME,
                "bot_username": settings.BOT_USERNAME.strip().lstrip("@"),
                **(await price_context()),
            },
        )

    @app.get("/comic/{graphic_chapter_id}", response_class=HTMLResponse)
    async def comic_reader(request: Request, graphic_chapter_id: int):
        chapter = await get_graphic_chapter(graphic_chapter_id)
        is_public = bool(
            chapter
            and chapter["publication_status"] == "published"
            and chapter["status"] == "published"
        )
        adjacent = await get_adjacent_graphic_chapters(graphic_chapter_id) if is_public else {"previous": None, "next": None}
        return templates.TemplateResponse(
            request,
            "comic_reader.html",
            common_context(
                {
                    "graphic_chapter": chapter,
                    "graphic_chapter_id": graphic_chapter_id,
                    "previous_chapter": adjacent.get("previous"),
                    "next_chapter": adjacent.get("next"),
                    "bot_username": settings.BOT_USERNAME.strip().lstrip("@"),
                }
            ),
        )


    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return templates.TemplateResponse(request, "settings.html", common_context())

    @app.get("/library", response_class=HTMLResponse)
    async def library_page(request: Request):
        return templates.TemplateResponse(request, "library.html", common_context())

    @app.get("/premium", response_class=HTMLResponse)
    async def premium_page(request: Request):
        return templates.TemplateResponse(request, "premium.html", common_context())

    @app.get("/api/premium/status")
    async def api_premium_status(x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        plans = await list_premium_plans()
        status = await get_user_premium_status(user.app_user_id)
        insights = await get_personal_reading_insights(user.app_user_id) if status.get("active") else None
        return {"ok": True, "plans": plans, "subscription": status, "insights": insights}

    @app.post("/api/premium/checkout")
    async def api_premium_checkout(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        missing = await get_missing_legal_documents(
            user.app_user_id,
            [(code, LEGAL_DOCS[code].version, LEGAL_DOCS[code].digest) for code in REQUIRED_ON_START],
        )
        if missing:
            raise HTTPException(status_code=409, detail="Сначала откройте бота и примите актуальные документы.")
        plan_code = str((payload or {}).get("plan_code") or "monthly").strip().lower()
        plan = next((item for item in await list_premium_plans() if str(item.get("code")) == plan_code), None)
        if not plan or int(plan.get("price_stars") or 0) <= 0:
            raise HTTPException(status_code=404, detail="Тариф Premium недоступен.")
        current = await get_user_premium_status(user.app_user_id)
        if current.get("active") and current.get("auto_renew"):
            raise HTTPException(status_code=409, detail="Автопродление Premium уже активно.")
        link = await premium_invoice_link(plan)
        await add_audit(user.app_user_id, "premium_checkout_created", "premium_plan", plan_code, None, str(plan.get("price_stars")))
        return {"ok": True, "invoice_link": link, "plan": plan}

    @app.post("/api/premium/auto-renew")
    async def api_premium_auto_renew(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        status = await get_user_premium_status(user.app_user_id)
        if not status.get("active"):
            raise HTTPException(status_code=404, detail="Активная подписка Premium не найдена.")
        if not status.get("is_recurring"):
            raise HTTPException(status_code=409, detail="У этой оплаты нет автопродления.")
        raw_enabled = (payload or {}).get("enabled")
        enabled = raw_enabled if isinstance(raw_enabled, bool) else str(raw_enabled).strip().lower() in {"1", "true", "yes", "on"}
        charge_id = str(status.get("telegram_payment_charge_id") or "")
        if not charge_id:
            raise HTTPException(status_code=409, detail="Не найден идентификатор подписки Telegram.")
        bot = Bot(token=settings.BOT_TOKEN)
        try:
            await bot.edit_user_star_subscription(
                user_id=user.telegram_id,
                telegram_payment_charge_id=charge_id,
                is_canceled=not enabled,
            )
        finally:
            await bot.session.close()
        if not await set_premium_auto_renew(user.app_user_id, enabled=enabled):
            raise HTTPException(status_code=409, detail="Подписка уже завершена.")
        await add_audit(user.app_user_id, "premium_renew_enabled" if enabled else "premium_renew_canceled", "premium", str(status.get("subscription_id") or ""), None, "")
        return {"ok": True, "subscription": await get_user_premium_status(user.app_user_id)}

    @app.get("/api/control/premium")
    async def api_control_premium(x_telegram_init_data: str | None = Header(default=None)):
        _, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Настройки Premium доступны только владельцу.")
        return {"ok": True, "plans": await list_premium_plans(include_inactive=True), "summary": await get_premium_owner_summary()}

    @app.patch("/api/control/premium")
    async def api_control_premium_update(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Настройки Premium доступны только владельцу.")
        price = payload.get("price_stars") if "price_stars" in payload else None
        raw_enabled = payload.get("enabled") if "enabled" in payload else None
        enabled = None
        if raw_enabled is not None:
            enabled = raw_enabled if isinstance(raw_enabled, bool) else str(raw_enabled).strip().lower() in {"1", "true", "yes", "on"}
        try:
            plan = await set_premium_plan_settings(
                price_stars=int(price) if price is not None else None,
                enabled=enabled,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Цена Premium должна быть целым числом от 1 до 10000 Stars.") from exc
        await add_audit(user.app_user_id, "premium_settings_updated", "premium_plan", str(plan.get("code")), None, json.dumps({"price": plan.get("price_stars"), "enabled": plan.get("is_active")}, ensure_ascii=False))
        return {"ok": True, "plan": plan, "summary": await get_premium_owner_summary()}

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
        audio_rows = [book for book in rows if int(book["audio_count"] or 0) > 0 or int(book["has_audio"] or 0) == 1]
        books = await attach_rankings(audio_rows, category="audio")
        return templates.TemplateResponse(request, "audio.html", common_context({"books": books, **(await price_context())}))

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
                **(await price_context()),
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
        chapter_package_balances = await list_user_chapter_package_balances(user.app_user_id)
        author_profile = await get_author_profile(user.app_user_id)
        is_owner = user.telegram_id in settings.owner_ids
        permissions = set(PERMISSION_BY_CODE) if is_owner else await get_admin_permissions(user.app_user_id)
        preferences = await get_user_preferences(user.app_user_id)
        achievements = await sync_user_achievements(user.app_user_id)
        premium = await get_user_premium_status(user.app_user_id)
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
            "chapter_package_balances": _rows_to_dicts(chapter_package_balances),
            "preferences": preferences,
            "achievements": achievements,
            "premium": premium,
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
            "notifications_reminders", "notifications_achievements",
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
        achievements = await sync_user_achievements(user.app_user_id)
        await _notify_new_achievements(user, achievements)
        return {"ok": True, "bookmark": _row_to_dict(bookmark) if bookmark else None, "achievements": achievements}

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
        achievements = await sync_user_achievements(user.app_user_id)
        await _notify_new_achievements(user, achievements)
        return {"ok": True, "reviews": _rows_to_dicts(reviews), "achievements": achievements}

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
        book_price = int(chapter["book_price_stars"] or 0)
        declared_mode = str(chapter["pricing_type"] or "").strip().lower()
        pricing_mode = "premium" if declared_mode == "premium" else ("free" if book_price <= 0 else ("chapters" if declared_mode == "chapters" else "whole_book"))
        chapter_is_free = pricing_mode == "free" or int(chapter["is_free"] or 0) == 1
        can_buy_chapter = pricing_mode == "chapters" and not chapter_is_free and int(chapter["price_stars"] or 0) > 0
        premium_required = pricing_mode == "premium" and not chapter_is_free
        purchase_url = "" if moderation_access or not can_buy_chapter else _bot_purchase_url("chapter", chapter_id)
        book_purchase_url = "" if moderation_access or pricing_mode in {"free", "premium"} or book_price <= 0 else _bot_purchase_url("book", int(chapter["book_id"]))
        comments = await list_comments_for_chapter(chapter_id, limit=100, viewer_user_id=user.app_user_id) if allowed and is_public and not moderation_access else []
        reactions = await list_chapter_reactions(chapter_id, user.app_user_id) if allowed and is_public and not moderation_access else {"counts": {}, "selected": None}
        progress = await get_reading_progress(user.app_user_id, chapter_id) if allowed and not moderation_access else 0
        if (
            allowed
            and not moderation_access
            and int(chapter["is_free"] or 0) != 1
            and int(chapter["price_stars"] or 0) > 0
        ):
            await mark_purchase_access_used(user.app_user_id, chapter_id=chapter_id)
        reader_ads = []
        ad_settings = await get_reader_ad_settings()
        premium_status = await get_user_premium_status(user.app_user_id)
        if premium_status.get("active"):
            ad_settings = {**ad_settings, "enabled": False, "hidden_by_premium": True}
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
        protection = await _content_protection_payload(
            user,
            allow_download=bool(int(chapter["allow_download"] or 0)),
            book_id=int(chapter["book_id"]),
        )
        package_credits = await get_user_chapter_credit_summary(
            user.app_user_id, int(chapter["book_id"]), "text"
        )
        return {
            "ok": True,
            "allowed": allowed,
            "moderation_access": moderation_access,
            "access_mode": "moderation" if moderation_access else ("reader" if allowed else "locked"),
            "purchase_url": purchase_url,
            "book_purchase_url": book_purchase_url,
            "pricing_mode": pricing_mode,
            "can_buy_chapter": can_buy_chapter,
            "premium_required": premium_required,
            "premium_url": "/premium" if premium_required else "",
            "package_credits": package_credits if pricing_mode == "chapters" else {"remaining": 0},
            "package_unlock_url": f"/api/reader/{int(chapter_id)}/unlock-package",
            "progress_percent": progress,
            "protection": protection,
            "chapter": {
                "id": int(chapter["id"]),
                "book_id": int(chapter["book_id"]),
                "book_title": chapter["book_title"],
                "title": chapter["title"],
                "number": int(chapter["number"]),
                "is_free": chapter_is_free,
                "price_stars": int(chapter["price_stars"] or 0),
                "buyer_estimate_minor": (int(chapter["price_stars"] or 0) if can_buy_chapter else 0) * (await load_runtime_payment_settings()).buyer_star_rate_minor,
                "book_price_stars": book_price,
                "premium_required": premium_required,
                "text": chapter["text"] if allowed else "",
            },
            "comments": _rows_to_dicts(comments),
            "reactions": reactions,
            "reader_ads": _rows_to_dicts(reader_ads),
            "ad_settings": ad_settings,
            "chapter_bounds": chapter_bounds,
            "navigation": {
                "previous": _row_to_dict(adjacent["previous"]) if adjacent["previous"] else None,
                "next": _row_to_dict(adjacent["next"]) if adjacent["next"] else None,
            },
        }

    @app.post("/api/reader/{chapter_id}/unlock-package")
    async def api_unlock_text_chapter_from_package(
        chapter_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        try:
            result = await redeem_chapter_package_credit(user.app_user_id, chapter_id=chapter_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await add_audit(user.app_user_id, "chapter_package_credit_used", "chapter", str(chapter_id), None, str(result.get("remaining")))
        return result

    @app.get("/api/reader/tts/voices")
    async def api_reader_tts_voices(x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        premium_status = await get_user_premium_status(user.app_user_id)
        legacy_status = tts_engine_status()
        manager: TTSSessionManager = app.state.tts_sessions
        provider_statuses = await manager.queue.registry.statuses()
        providers = [
            {
                "name": item.name,
                "available": bool(item.available),
                "warmed": bool(item.warmed),
                "message": item.message,
                "details": dict(item.details),
            }
            for item in provider_statuses
        ]
        enabled = any(item["available"] for item in providers) or bool(legacy_status["enabled"])
        return {
            "ok": True,
            "enabled": enabled,
            "message": "Сегментное озвучивание готово" if enabled else legacy_status["message"],
            "engine": "segmented-v1.11.0-local-voices",
            "providers": providers,
            "voices": available_voices(),
            "styles": available_styles(),
            "rates": available_rates(),
            "playback_rate_in_player": True,
            "premium": {
                "active": bool(premium_status.get("active")),
                "priority_queue": bool(premium_status.get("active")),
            },
            "quality_control": {
                "enabled": True,
                "first_segment_wait_seconds": int(settings.TTS_FIRST_SEGMENT_WAIT_SECONDS),
                "automatic_retries": int(settings.TTS_QUALITY_RETRIES),
                "segment_cache_version": "v3-local-voices",
            },
        }

    @app.post("/api/reader/{chapter_id}/tts/session")
    async def api_reader_tts_session_create(
        chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id, telegram_id=user.telegram_id, chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not allowed:
            raise HTTPException(status_code=403, detail="Озвучивание доступно после открытия главы.")
        if not moderation_access and int(chapter["is_free"] or 0) != 1 and int(chapter["price_stars"] or 0) > 0:
            await mark_purchase_access_used(user.app_user_id, chapter_id=chapter_id)

        selected_voice = validate_voice(str(payload.get("voice") or "irina"))
        selected_style = validate_style(str(payload.get("style") or "natural"))
        selected_rate = validate_rate(payload.get("rate"))
        high_quality = bool(payload.get("high_quality", False))
        premium_status = await get_user_premium_status(user.app_user_id)
        manager: TTSSessionManager = app.state.tts_sessions
        try:
            session = await manager.create_session(
                user_id=user.app_user_id, chapter_id=chapter_id, text=str(chapter["text"] or ""),
                voice=selected_voice, style=selected_style, high_quality=high_quality,
                priority_boost=bool(premium_status.get("active")), reuse=True,
            )
            await manager.await_first(session.id)
            manifest = await manager.manifest(
                session.id, user_id=user.app_user_id, start_index=0, count=settings.TTS_SESSION_INITIAL_SEGMENTS,
            )
        except (ReaderTTSError, ValueError, TTSProviderError, TTSProviderUnavailable) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        adjacent = await (get_adjacent_chapters_for_moderation(chapter_id) if moderation_access else get_adjacent_chapters(chapter_id))
        profile_key = tts_profile_key(selected_voice, selected_rate, selected_style)
        progress = await get_tts_progress(user.app_user_id, chapter_id, profile_key)
        manifest.update({
            "ok": True, "moderation_access": moderation_access,
            "access_mode": "moderation" if moderation_access else "reader",
            "device_cache_allowed": not moderation_access,
            "playback_rate": selected_rate, "progress_seconds": int(progress or 0),
            "premium_priority": bool(premium_status.get("active")),
            "chapter": {
                "id": int(chapter["id"]), "book_id": int(chapter["book_id"]),
                "book_title": chapter["book_title"], "pen_name": chapter["pen_name"] or "Автор не указан",
                "title": chapter["title"], "number": int(chapter["number"]), "text": chapter["text"],
            },
            "navigation": {
                "previous": _row_to_dict(adjacent["previous"]) if adjacent["previous"] else None,
                "next": _row_to_dict(adjacent["next"]) if adjacent["next"] else None,
            },
        })
        if moderation_access:
            await _audit_moderation_reader_access(user_id=user.app_user_id, chapter_id=chapter_id, action="moderation_chapter_tts_segmented")
        return manifest

    @app.get("/api/reader/tts/session/{session_id}")
    async def api_reader_tts_session_manifest(
        session_id: str, start: int = 0, count: int = 10,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        manager: TTSSessionManager = app.state.tts_sessions
        try:
            manifest = await manager.manifest(session_id, user_id=user.app_user_id, start_index=start, count=count)
        except TTSSessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, **manifest}

    @app.post("/api/reader/tts/session/{session_id}/event")
    async def api_reader_tts_session_event(
        session_id: str,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        manager: TTSSessionManager = app.state.tts_sessions
        try:
            item = await manager.record_client_event(
                session_id,
                user_id=user.app_user_id,
                event=str(payload.get("event") or ""),
                segment_index=payload.get("segment_index"),
                player_version=str(payload.get("player_version") or ""),
                details=dict(payload.get("details") or {}),
            )
        except TTSSessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "event": item}

    @app.delete("/api/reader/tts/session/{session_id}")
    async def api_reader_tts_session_delete(
        session_id: str, x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        manager: TTSSessionManager = app.state.tts_sessions
        removed = await manager.remove(session_id, user_id=user.app_user_id)
        return {"ok": True, "removed": removed}

    @app.get("/api/reader/{chapter_id}/tts")
    async def api_reader_tts(
        chapter_id: int,
        voice: str = "irina",
        rate: float = 1.0,
        style: str = "natural",
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
        if (
            not moderation_access
            and int(chapter["is_free"] or 0) != 1
            and int(chapter["price_stars"] or 0) > 0
        ):
            await mark_purchase_access_used(user.app_user_id, chapter_id=chapter_id)
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
        selected_style = validate_style(str(payload.get("style") or "natural"))
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

    @app.get("/media/reader-tts/session/{session_id}/{segment_index}.mp3")
    async def media_reader_tts_segment(
        session_id: str, segment_index: int, uid: int, exp: int = 0, sig: str = "",
    ):
        manager: TTSSessionManager = app.state.tts_sessions
        try:
            session = await manager.get(session_id, user_id=int(uid))
        except TTSSessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not 0 <= int(segment_index) < session.segment_count:
            raise HTTPException(status_code=404, detail="Аудиофрагмент не найден.")
        segment = session.prepared.segments[int(segment_index)]
        if not validate_segment_media_token(
            user_id=int(uid), session_id=session_id, segment_index=int(segment_index),
            segment_digest=segment.digest, expires_at=int(exp), signature=sig,
        ):
            raise HTTPException(status_code=403, detail="Ссылка на аудиофрагмент устарела.")
        user_row = await get_user_by_id(int(uid))
        if not user_row or int(user_row["is_blocked"] or 0) == 1:
            raise HTTPException(status_code=403, detail="Доступ закрыт.")
        chapter = await get_chapter(session.chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=int(uid), telegram_id=int(user_row["telegram_id"]), chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к главе.")
        try:
            audio = await manager.await_segment(
                session.id, int(segment_index), user_id=int(uid), timeout=settings.TTS_REMOTE_TIMEOUT_SECONDS,
            )
        except TTSSegmentNotReady as exc:
            raise HTTPException(status_code=425, detail=str(exc)) from exc
        except (TTSProviderError, TTSProviderUnavailable) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return FileResponse(
            audio.path, media_type="audio/mpeg",
            headers={
                "Cache-Control": "private, max-age=3600", "Accept-Ranges": "bytes",
                "Content-Disposition": f'inline; filename="tts_{session.chapter_id}_{segment_index}.mp3"',
                "X-Voxlyra-TTS-Provider": audio.provider, "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/media/reader-tts/{chapter_id}.mp3")
    async def media_reader_tts(
        chapter_id: int,
        uid: int,
        voice: str,
        rate: float = 1.0,
        style: str = "natural",
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
        if (
            not moderation_access
            and int(chapter["is_free"] or 0) != 1
            and int(chapter["price_stars"] or 0) > 0
        ):
            await mark_purchase_access_used(int(uid), chapter_id=chapter_id)
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
        achievements = {"new": [], "items": []}
        if not moderation_access:
            await save_reading_progress(user.app_user_id, chapter_id, percent)
            await record_premium_content_event(user.app_user_id, chapter_id, "open")
            if percent >= 90:
                await record_premium_content_event(user.app_user_id, chapter_id, "complete")
                achievements = await sync_user_achievements(user.app_user_id)
                await _notify_new_achievements(user, achievements)
        return {
            "ok": True,
            "position_percent": percent,
            "moderation_access": moderation_access,
            "achievements": achievements,
        }

    @app.post("/api/reader/{chapter_id}/quote-card")
    async def api_reader_quote_card(
        chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id, telegram_id=user.telegram_id, chapter=chapter
        )
        if not allowed or moderation_access:
            raise HTTPException(status_code=403, detail="Карточка цитаты недоступна в этом режиме.")
        quote = normalize_quote(payload.get("quote"))
        if not quote_belongs_to_text(quote, str(chapter["text"] or "")):
            raise HTTPException(status_code=400, detail="Выберите фрагмент длиной от 20 символов из текущей главы.")
        style = str(payload.get("style") or "standard").strip().lower()
        if style not in {"standard", "aurora", "parchment"}:
            style = "standard"
        premium_status = await get_user_premium_status(user.app_user_id)
        if style != "standard" and not premium_status.get("active"):
            raise HTTPException(status_code=403, detail="Этот стиль карточки доступен в VoxLyra Premium.")
        image = build_quote_card(
            quote=quote,
            book_title=str(chapter["book_title"] or "Книга"),
            chapter_title=f"Глава {int(chapter['number'])}. {chapter['title']}",
            author_name=str(chapter["pen_name"] or "Автор не указан"),
            style=style,
        )
        filename = f"voxlyra_quote_{chapter_id}.png"
        return StreamingResponse(
            iter([image]),
            media_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/reader/{chapter_id}/assistant")
    async def api_book_assistant_context(
        chapter_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id, telegram_id=user.telegram_id, chapter=chapter
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Помощник доступен после открытия главы.")
        if moderation_access:
            raise HTTPException(status_code=403, detail="Помощник отключён в служебном режиме проверки.")
        rows = await list_book_assistant_chapters(
            int(chapter["book_id"]), int(chapter["number"]), limit=18
        )
        accessible = await _book_assistant_accessible_rows(user.app_user_id, rows)
        if not any(int(item["id"]) == int(chapter_id) for item in accessible):
            accessible.append(_row_to_dict(chapter))
            accessible.sort(key=lambda item: int(item.get("number") or 0))
        analyzed = await _book_assistant_analyze_rows(accessible)
        recap = build_book_recap(analyzed, current_number=int(chapter["number"]), limit=6)
        current = next((item for item in analyzed if int(item["id"]) == int(chapter_id)), None)
        return {
            "ok": True,
            "spoiler_limit": int(chapter["number"]),
            "book_title": str(chapter["book_title"] or ""),
            "chapter": {"id": int(chapter["id"]), "number": int(chapter["number"]), "title": str(chapter["title"] or "")},
            "current_summary": str((current or {}).get("summary") or ""),
            "recap": recap,
            "characters": _book_assistant_merge_entities(analyzed, "characters", "name"),
            "terms": _book_assistant_merge_entities(analyzed, "terms", "term"),
            "notice": f"Ответы строятся только по доступным вам главам до главы {int(chapter['number'])} включительно.",
        }

    @app.post("/api/reader/{chapter_id}/assistant/ask")
    async def api_book_assistant_ask(
        chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _chapter_access(
            app_user_id=user.app_user_id, telegram_id=user.telegram_id, chapter=chapter
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Помощник доступен после открытия главы.")
        if moderation_access:
            raise HTTPException(status_code=403, detail="Помощник отключён в служебном режиме проверки.")
        question = str(payload.get("question") or "").strip()
        if len(question) < 3:
            raise HTTPException(status_code=400, detail="Введите более точный вопрос.")
        if len(question) > 400:
            raise HTTPException(status_code=400, detail="Вопрос слишком длинный.")
        keywords = book_question_keywords(question, limit=8)
        found = await search_book_assistant_chapters(
            int(chapter["book_id"]), int(chapter["number"]), keywords, limit=36
        )
        recent = await list_book_assistant_chapters(
            int(chapter["book_id"]), int(chapter["number"]), limit=16
        )
        by_id: dict[int, Any] = {int(row["id"]): row for row in [*found, *recent]}
        by_id[int(chapter_id)] = chapter
        accessible = await _book_assistant_accessible_rows(
            user.app_user_id, sorted(by_id.values(), key=lambda row: int(row["number"]))
        )
        response = answer_book_question(
            question, accessible, current_number=int(chapter["number"])
        )
        return {
            "ok": True,
            "question": question,
            **response,
            "notice": f"Будущие главы после {int(chapter['number'])}-й не использовались.",
        }

    @app.get("/api/reader/{chapter_id}/comments")
    async def api_comments(chapter_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not await user_can_access_chapter(user.app_user_id, chapter_id):
            raise HTTPException(status_code=403, detail="Комментарии доступны после открытия главы.")
        comments = await list_comments_for_chapter(chapter_id, limit=100, viewer_user_id=user.app_user_id)
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
        parent_id = int(payload.get("parent_id") or 0) or None
        try:
            await add_comment(
                user.app_user_id,
                chapter_id,
                text,
                parent_id=parent_id,
                is_spoiler=bool(payload.get("is_spoiler")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        comments = await list_comments_for_chapter(chapter_id, limit=100, viewer_user_id=user.app_user_id)
        return {"ok": True, "comments": _rows_to_dicts(comments)}

    @app.post("/api/comments/{comment_id}/like")
    async def api_toggle_comment_like(comment_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        comment = await get_public_comment(comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Комментарий не найден.")
        chapter = await get_chapter(int(comment["chapter_id"]))
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not await user_can_access_chapter(user.app_user_id, int(comment["chapter_id"])):
            raise HTTPException(status_code=403, detail="Реакции доступны после открытия главы.")
        try:
            result = await toggle_comment_like(user.app_user_id, comment_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, **result}

    @app.post("/api/comments/{comment_id}/report")
    async def api_report_comment(
        comment_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        comment = await get_public_comment(comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Комментарий не найден.")
        if not await user_can_access_chapter(user.app_user_id, int(comment["chapter_id"])):
            raise HTTPException(status_code=403, detail="Жалоба недоступна.")
        try:
            result = await report_comment(user.app_user_id, comment_id, str(payload.get("reason") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            **result,
            "message": "Жалоба отправлена модераторам." if result["created"] else "Эта жалоба уже находится на проверке.",
        }

    @app.get("/api/reader/{chapter_id}/reactions")
    async def api_chapter_reactions(chapter_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not await user_can_access_chapter(user.app_user_id, chapter_id):
            raise HTTPException(status_code=403, detail="Реакции доступны после открытия главы.")
        return {"ok": True, "reactions": await list_chapter_reactions(chapter_id, user.app_user_id)}

    @app.post("/api/reader/{chapter_id}/reactions")
    async def api_set_chapter_reaction(
        chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_chapter(chapter_id)
        if not chapter or chapter["publication_status"] != "published" or chapter["status"] != "published":
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        if not await user_can_access_chapter(user.app_user_id, chapter_id):
            raise HTTPException(status_code=403, detail="Реакции доступны после открытия главы.")
        try:
            reactions = await set_chapter_reaction(user.app_user_id, chapter_id, str(payload.get("reaction") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "reactions": reactions}

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

    @app.get("/api/comic/{graphic_chapter_id}")
    async def api_comic_reader(
        graphic_chapter_id: int,
        language: str = "ru",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_graphic_chapter(graphic_chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Графическая глава не найдена.")
        allowed, moderation_access = await _graphic_access(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            chapter=chapter,
        )
        is_public = chapter["publication_status"] == "published" and chapter["status"] == "published"
        if not is_public and not moderation_access:
            raise HTTPException(status_code=404, detail="Графическая глава не найдена.")

        all_pages = await list_graphic_pages(graphic_chapter_id)
        visible_pages = all_pages if moderation_access else [
            page for page in all_pages if str(page["moderation_status"] or "approved") != "rejected"
        ]
        preview_count = max(0, min(20, int(chapter["preview_pages"] or 0)))
        preview_only = bool(not allowed and is_public and preview_count > 0)
        purchase_url = _bot_purchase_url("graphic", int(graphic_chapter_id))
        package_credits = await get_user_chapter_credit_summary(
            user.app_user_id, int(chapter["book_id"]), "graphic"
        )
        if not allowed and not preview_only:
            return {
                "ok": True,
                "allowed": False,
                "preview_only": False,
                "chapter": _row_to_dict(chapter),
                "purchase_url": purchase_url,
                "package_credits": package_credits,
                "package_unlock_url": f"/api/comic/{int(graphic_chapter_id)}/unlock-package",
                "pages": [],
            }

        pages = visible_pages[:preview_count] if preview_only else visible_pages
        page_items = _graphic_page_payloads(
            user_id=user.app_user_id,
            chapter_id=graphic_chapter_id,
            pages=pages,
            include_ids=True,
        )
        layer_map = await get_graphic_reader_layers(
            [int(item["id"]) for item in page_items if item.get("id")],
            language_code=language,
            user_id=user.app_user_id,
        )
        for item in page_items:
            item["layers"] = layer_map.get(int(item.get("id") or 0), {"texts": [], "translations": [], "frames": [], "bookmarked": False, "comments": []})
        adjacent = await get_adjacent_graphic_chapters(graphic_chapter_id)
        progress_page = 1 if (moderation_access or preview_only) else await get_graphic_reading_progress(
            user.app_user_id, graphic_chapter_id
        )
        effective_mode = str(chapter["reading_mode"] or "inherit")
        if effective_mode == "inherit":
            effective_mode = str(chapter["book_reading_mode"] or "ltr")
        if effective_mode not in GRAPHIC_READING_MODES:
            effective_mode = "ltr"
        if moderation_access:
            await add_audit(
                user.app_user_id,
                "moderation_graphic_reader_opened",
                "graphic_chapter",
                str(graphic_chapter_id),
                None,
                "Служебный доступ для проверки графических страниц",
            )
        protection = await _content_protection_payload(
            user,
            allow_download=False if preview_only else bool(int(chapter["allow_download"] or 0)),
            book_id=int(chapter["book_id"]),
        )
        if preview_only:
            protection["protected"] = True
            protection["allow_download"] = False
            protection["download_url"] = ""
        return {
            "ok": True,
            "allowed": True,
            "preview_only": preview_only,
            "preview_pages": preview_count,
            "purchase_url": purchase_url if preview_only else "",
            "package_credits": package_credits,
            "package_unlock_url": f"/api/comic/{int(graphic_chapter_id)}/unlock-package",
            "moderation_access": moderation_access,
            "access_mode": "moderation" if moderation_access else ("preview" if preview_only else "reader"),
            "chapter": _row_to_dict(chapter),
            "content_type_label": _graphic_type_label(str(chapter["content_type"] or "")),
            "reading_mode": effective_mode,
            "progress_page": progress_page,
            "protection": protection,
            "pages": page_items,
            "delivery": {
                "adaptive_variants": True,
                "device_cache_max_mb": max(64, int(settings.COMIC_DEVICE_CACHE_MAX_MB or 512)),
                "device_cache_max_items": max(100, int(settings.COMIC_DEVICE_CACHE_MAX_ITEMS or 1200)),
                "preload_fast": max(1, int(settings.COMIC_PRELOAD_PAGES_FAST or 6)),
                "preload_slow": max(0, int(settings.COMIC_PRELOAD_PAGES_SLOW or 1)),
                "storage_backend": "local-volume",
            },
            "advanced_reading": {
                "ocr_available": ocr_engine_available(),
                "search_url": f"/api/comic/book/{int(chapter['book_id'])}/search",
                "bookmarks_url": f"/api/comic/book/{int(chapter['book_id'])}/bookmarks",
                "language": str(language or "ru")[:12],
                "translation_layers": True,
                "frame_mode": any(bool(item.get("layers", {}).get("frames")) for item in page_items),
                "page_comments": True,
            },
            "navigation": {
                "previous": _row_to_dict(adjacent["previous"]) if adjacent["previous"] else None,
                "next": None if preview_only else (_row_to_dict(adjacent["next"]) if adjacent["next"] else None),
            },
        }

    @app.post("/api/comic/{graphic_chapter_id}/unlock-package")
    async def api_unlock_graphic_chapter_from_package(
        graphic_chapter_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        try:
            result = await redeem_chapter_package_credit(
                user.app_user_id, graphic_chapter_id=graphic_chapter_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await add_audit(user.app_user_id, "chapter_package_credit_used", "graphic_chapter", str(graphic_chapter_id), None, str(result.get("remaining")))
        return result

    @app.get("/api/comic/book/{book_id}/search")
    async def api_comic_text_search(
        book_id: int,
        q: str,
        language: str = "ru",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        rows = await search_graphic_book_text(book_id, q, language_code=language, limit=80)
        items = []
        for row in rows:
            if await user_can_access_graphic(user.app_user_id, int(row["graphic_chapter_id"])):
                items.append(_row_to_dict(row))
        if items:
            await record_graphic_reading_event(user.app_user_id, int(items[0]["graphic_chapter_id"]), "search")
        return {"ok": True, "query": q, "items": items}

    @app.post("/api/comic/page/{graphic_page_id}/bookmark")
    async def api_comic_page_bookmark(
        graphic_page_id: int,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        page = await get_graphic_page(graphic_page_id)
        if not page or not await user_can_access_graphic(user.app_user_id, int(page["graphic_chapter_id"])):
            raise HTTPException(status_code=403, detail="Нет доступа к этой странице.")
        active = await toggle_graphic_page_bookmark(user.app_user_id, graphic_page_id, str((payload or {}).get("note") or ""))
        return {"ok": True, "bookmarked": active}

    @app.get("/api/comic/book/{book_id}/bookmarks")
    async def api_comic_bookmarks(
        book_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        return {"ok": True, "items": _rows_to_dicts(await list_user_graphic_bookmarks(user.app_user_id, book_id))}

    @app.post("/api/comic/page/{graphic_page_id}/comments")
    async def api_comic_page_comment(
        graphic_page_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        page = await get_graphic_page(graphic_page_id)
        if not page or not await user_can_access_graphic(user.app_user_id, int(page["graphic_chapter_id"])):
            raise HTTPException(status_code=403, detail="Нет доступа к этой странице.")
        try:
            comment_id = await add_graphic_page_comment(user.app_user_id, graphic_page_id, str(payload.get("text") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "comment_id": comment_id, "message": "Комментарий отправлен на модерацию."}

    @app.post("/api/comic/{graphic_chapter_id}/event")
    async def api_comic_reading_event(
        graphic_chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_graphic_chapter(graphic_chapter_id)
        if not chapter or not await user_can_access_graphic(user.app_user_id, graphic_chapter_id):
            raise HTTPException(status_code=403, detail="Нет доступа к главе.")
        page_id = int(payload.get("graphic_page_id") or 0) or None
        await record_graphic_reading_event(
            user.app_user_id,
            graphic_chapter_id,
            str(payload.get("event_type") or "page_view"),
            graphic_page_id=page_id,
            session_key=str(payload.get("session_key") or ""),
        )
        return {"ok": True}

    @app.get("/api/comic/book/{book_id}/offline-manifest")
    async def api_comic_offline_manifest(
        book_id: int,
        volume: int = 0,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        book = await get_book(book_id)
        if not book or str(book["publication_status"] or "") != "published":
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
        if int(book["allow_download"] or 0) != 1:
            raise HTTPException(status_code=403, detail="Автор не разрешил сохранять это произведение на устройство.")
        chapters = await list_graphic_chapters_for_book(book_id, published_only=True)
        selected = [
            chapter for chapter in chapters
            if int(volume or 0) <= 0 or int(chapter["volume_number"] or 1) == int(volume)
        ]
        items: list[dict[str, Any]] = []
        protection = await _content_protection_payload(
            user, allow_download=True, book_id=int(book_id)
        )
        for chapter in selected:
            chapter_id = int(chapter["id"])
            if not await user_can_access_graphic(user.app_user_id, chapter_id):
                continue
            pages = [
                page for page in await list_graphic_pages(chapter_id)
                if str(page["moderation_status"] or "approved") != "rejected"
            ]
            effective_mode = str(chapter["reading_mode"] or "inherit")
            if effective_mode == "inherit":
                effective_mode = str(book["reading_mode"] or "ltr")
            adjacent = await get_adjacent_graphic_chapters(chapter_id)
            items.append({
                "meta": {
                    "ok": True,
                    "allowed": True,
                    "moderation_access": False,
                    "access_mode": "reader",
                    "chapter": _row_to_dict(chapter),
                    "content_type_label": _graphic_type_label(str(book["content_type"] or "")),
                    "reading_mode": effective_mode,
                    "progress_page": 1,
                    "protection": protection,
                    "pages": _graphic_page_payloads(
                        user_id=user.app_user_id, chapter_id=chapter_id, pages=pages
                    ),
                    "navigation": {
                        "previous": _row_to_dict(adjacent["previous"]) if adjacent["previous"] else None,
                        "next": _row_to_dict(adjacent["next"]) if adjacent["next"] else None,
                    },
                }
            })
        if not items:
            raise HTTPException(status_code=403, detail="Нет доступных глав для сохранения.")
        return {
            "ok": True,
            "book_id": int(book_id),
            "volume_number": max(0, int(volume or 0)),
            "chapters": items,
            "delivery": {
                "device_cache_max_mb": max(64, int(settings.COMIC_DEVICE_CACHE_MAX_MB or 512)),
                "device_cache_max_items": max(100, int(settings.COMIC_DEVICE_CACHE_MAX_ITEMS or 1200)),
            },
        }

    @app.post("/api/comic/{graphic_chapter_id}/progress")
    async def api_comic_progress(
        graphic_chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        chapter = await get_graphic_chapter(graphic_chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Графическая глава не найдена.")
        allowed, moderation_access = await _graphic_access(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            chapter=chapter,
        )
        if not allowed:
            raise HTTPException(status_code=403, detail="Нет доступа к главе.")
        page_number = max(1, min(int(chapter["pages_count"] or 1), int(payload.get("page_number") or 1)))
        if not moderation_access:
            await save_graphic_reading_progress(user.app_user_id, graphic_chapter_id, page_number)
        return {"ok": True, "page_number": page_number, "moderation_access": moderation_access}

    @app.post("/api/comic/page/{graphic_page_id}/report")
    async def api_comic_page_report(
        graphic_page_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user = await _tma_user(x_telegram_init_data)
        page = await get_graphic_page(graphic_page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        chapter = await get_graphic_chapter(int(page["graphic_chapter_id"]))
        if not chapter:
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        allowed, moderation_access = await _graphic_access(
            app_user_id=user.app_user_id,
            telegram_id=user.telegram_id,
            chapter=chapter,
        )
        preview_allowed = (
            chapter["publication_status"] == "published"
            and chapter["status"] == "published"
            and int(page["page_number"] or 0) <= max(0, int(chapter["preview_pages"] or 0))
        )
        if not allowed and not preview_allowed and not moderation_access:
            raise HTTPException(status_code=403, detail="Нет доступа к этой странице.")
        try:
            report_id = await create_graphic_page_report(
                user.app_user_id, graphic_page_id, str(payload.get("reason") or "")
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await add_audit(user.app_user_id, "graphic_page_reported", "graphic_page", str(graphic_page_id))
        return {"ok": True, "report_id": report_id, "message": "Сообщение отправлено модератору."}

    @app.get("/media/comic/{graphic_chapter_id}/{page_number}")
    async def media_comic_page(
        graphic_chapter_id: int,
        page_number: int,
        user_id: int,
        expires: int,
        token: str,
        variant: str = "auto",
        width: int = 0,
    ):
        if not _validate_graphic_media_token(
            user_id=user_id,
            chapter_id=graphic_chapter_id,
            page_number=page_number,
            expires_at=expires,
            token=token,
        ):
            raise HTTPException(status_code=403, detail="Ссылка на страницу устарела.")
        media_user = await get_user_by_id(int(user_id))
        if not media_user or int(media_user["is_blocked"] or 0) == 1:
            raise HTTPException(status_code=403, detail="Доступ закрыт.")
        chapter = await get_graphic_chapter(graphic_chapter_id)
        if not chapter or chapter["status"] == "deleted" or chapter["publication_status"] == "deleted":
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        allowed, moderation_access = await _graphic_access(
            app_user_id=int(user_id),
            telegram_id=int(media_user["telegram_id"]),
            chapter=chapter,
        )
        preview_allowed = (
            str(chapter["publication_status"] or "") == "published"
            and str(chapter["status"] or "") == "published"
            and int(page_number) <= max(0, int(chapter["preview_pages"] or 0))
        )
        if not allowed and not moderation_access and not preview_allowed:
            raise HTTPException(status_code=403, detail="Страница доступна после покупки.")
        pages = await list_graphic_pages(graphic_chapter_id)
        page = next((row for row in pages if int(row["page_number"]) == int(page_number)), None)
        if not page:
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        if str(page["moderation_status"] or "approved") == "rejected":
            can_review = bool(int(media_user["telegram_id"]) in settings.owner_ids)
            if media_user and not can_review:
                can_review = "mod_books" in await get_admin_permissions(int(user_id))
            if not can_review:
                raise HTTPException(status_code=404, detail="Страница скрыта после проверки.")
        if (
            allowed
            and not moderation_access
            and not preview_allowed
            and int(chapter["is_free"] or 0) != 1
            and int(chapter["price_stars"] or 0) > 0
        ):
            await mark_purchase_access_used(
                int(user_id), graphic_chapter_id=graphic_chapter_id
            )
        selected = select_page_variant(
            page, requested=variant, target_width=max(0, int(width or 0)), root=GRAPHIC_STORAGE_ROOT
        )
        if not selected:
            raise HTTPException(status_code=404, detail="Файл страницы не найден.")
        path = Path(selected["path"])
        return FileResponse(
            path,
            media_type=str(selected.get("mime_type") or "image/webp"),
            headers={
                "Cache-Control": (
                    "private, max-age=604800, immutable"
                    if int(chapter["allow_download"] or 0) == 1
                    else "private, no-store, max-age=0"
                ),
                "Content-Disposition": f'inline; filename="comic_{graphic_chapter_id}_{page_number}_{variant}.webp"',
                "X-Content-Type-Options": "nosniff",
            },
        )

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
                "buyer_estimate_minor": int(audio["price_stars"] or 0) * (await load_runtime_payment_settings()).buyer_star_rate_minor,
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
        if int(audio["is_free"] or 0) != 1 and int(audio["price_stars"] or 0) > 0:
            await mark_purchase_access_used(user.app_user_id, audio_chapter_id=audio_id)
        path = Path(audio["file_path"])
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="Аудиофайл не найден.")
        return FileResponse(path, media_type=audio["mime_type"] or "audio/mpeg", filename=audio["source_filename"] or path.name)

    @app.get("/api/author/dashboard")
    async def api_author_dashboard(x_telegram_init_data: str | None = Header(default=None)):
        user, profile = await author_session(x_telegram_init_data)
        stats = await get_author_dashboard_stats(user.app_user_id)
        analytics = await get_author_analytics(user.app_user_id, 30)
        achievements = await sync_user_achievements(user.app_user_id)
        finance = await get_author_finance_summary(user.app_user_id)
        rub_finance = {
            "held_minor": int(finance.get("held_minor", 0)),
            "available_minor": int(finance.get("available_minor", 0)),
            "pending_minor": int(finance.get("requested_minor", 0)),
            "paid_minor": int(finance.get("paid_minor", 0)),
            "gross_minor": int(finance.get("net_minor", 0)),
            "commission_minor": 0,
        }
        financial_profile = await get_author_financial_profile(user.app_user_id)
        books = await list_author_books_with_counts(user.app_user_id)
        public_financial_profile = None
        if financial_profile:
            public_financial_profile = {
                "legal_status": financial_profile["legal_status"],
                "legal_name": financial_profile["legal_name"],
                "inn": financial_profile["inn"],
                "ogrn": financial_profile["ogrn"],
                "country": financial_profile["country"],
                "sbp_phone_masked": mask_phone(decrypt_text(financial_profile["sbp_phone_encrypted"] or "")),
                "sbp_bank_id": financial_profile["sbp_bank_id"],
                "sbp_bank_name": financial_profile["sbp_bank_name"],
                "verification_status": financial_profile["verification_status"],
                "rejection_reason": financial_profile["rejection_reason"],
            }
        return {
            "ok": True,
            "profile": _row_to_dict(profile),
            "stats": stats,
            "analytics": analytics,
            "achievements": achievements,
            "finance": finance,
            "rub_finance": rub_finance,
            "financial_profile": public_financial_profile,
            "pricing_policy": {
                "model": "stars_only_two_rates",
                "commission_percent": 20,
                "hold_days": 14,
                "example": two_rate_price(
                    10,
                    (await load_runtime_payment_settings()).buyer_star_rate_minor,
                    (await load_runtime_payment_settings()).author_star_rate_minor,
                    20,
                ),
                "yookassa_payouts_configured": False,
                "manual_payouts_only": True,
                "payout_min_minor": 10000,
            },
            "books": _rows_to_dicts(books),
            "upload": {
                "chunk_size": CHUNK_SIZE_BYTES,
                "max_mb": int(settings.MAX_BOOK_UPLOAD_MB or 0),
                "formats": ["TXT", "DOCX", "FB2", "EPUB", "PDF", "ZIP"],
            },
            "graphic_upload": {
                "chunk_size": CHUNK_SIZE_BYTES,
                "max_mb": int(settings.MAX_COMIC_UPLOAD_MB or 0),
                "max_pages": int(settings.MAX_COMIC_PAGES or 500),
                "formats": ["PDF", "CBZ", "ZIP", "CBR", "RAR", "7Z", "EPUB fixed-layout", "JPG", "PNG", "WEBP", "AVIF", "GIF", "BMP", "TIFF"],
            },
        }

    @app.get("/api/author/analytics")
    async def api_author_analytics(days: int = 30, x_telegram_init_data: str | None = Header(default=None)):
        user, _ = await author_session(x_telegram_init_data)
        analytics = await get_author_analytics(user.app_user_id, days)
        achievements = await sync_user_achievements(user.app_user_id)
        return {"ok": True, "analytics": analytics, "achievements": achievements}

    @app.get("/api/author/sbp-banks")
    async def api_author_sbp_banks(x_telegram_init_data: str | None = Header(default=None)):
        await author_session(x_telegram_init_data)
        if not await payouts_configured():
            return {"ok": True, "configured": False, "items": []}
        try:
            items = await list_sbp_banks()
        except YooKassaPayoutError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "configured": True, "items": items}

    @app.put("/api/author/financial-profile")
    async def api_author_financial_profile(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        required = [(code, LEGAL_DOCS[code].version, LEGAL_DOCS[code].digest) for code in ("author_license", "author_data_consent")]
        missing = await get_missing_legal_documents(user.app_user_id, required)
        if missing:
            raise HTTPException(status_code=409, detail="Сначала примите актуальный договор автора и отдельное согласие на обработку данных в боте.")
        status = str(payload.get("legal_status") or "").strip()
        if status not in {"self_employed", "individual_entrepreneur", "legal_entity", "individual"}:
            raise HTTPException(status_code=400, detail="Выберите правовой и налоговый статус автора.")
        legal_name = str(payload.get("legal_name") or "").strip()
        inn = "".join(ch for ch in str(payload.get("inn") or "") if ch.isdigit())
        ogrn = "".join(ch for ch in str(payload.get("ogrn") or "") if ch.isdigit())
        if len(legal_name) < 3 or len(inn) not in {10, 12}:
            raise HTTPException(status_code=400, detail="Укажите ФИО/наименование и корректный ИНН.")
        current_profile = await get_author_financial_profile(user.app_user_id)
        raw_phone = str(payload.get("sbp_phone") or "").strip()
        if raw_phone:
            try:
                phone = normalize_phone(raw_phone)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            phone_encrypted = encrypt_text(phone)
        elif current_profile and current_profile["sbp_phone_encrypted"]:
            phone_encrypted = str(current_profile["sbp_phone_encrypted"])
            phone = decrypt_text(phone_encrypted)
        else:
            raise HTTPException(status_code=400, detail="Укажите номер телефона, привязанный к СБП.")
        bank_id = str(payload.get("sbp_bank_id") or "").strip()
        bank_name = str(payload.get("sbp_bank_name") or "").strip()
        if (not bank_id or not bank_name) and current_profile:
            bank_id = bank_id or str(current_profile["sbp_bank_id"] or "")
            bank_name = bank_name or str(current_profile["sbp_bank_name"] or "")
        if not bank_id or not bank_name:
            raise HTTPException(status_code=400, detail="Выберите банк для СБП.")
        ok = await upsert_author_financial_profile(
            user.app_user_id,
            legal_status=status,
            legal_name=legal_name,
            inn=inn,
            ogrn=ogrn,
            country=str(payload.get("country") or "RU"),
            sbp_phone_encrypted=phone_encrypted,
            sbp_bank_id=bank_id,
            sbp_bank_name=bank_name,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Профиль автора не найден.")
        await add_audit(user.app_user_id, "author_financial_profile_updated", "author", str(user.app_user_id), None, status)
        return {"ok": True, "verification_status": "pending", "sbp_phone_masked": mask_phone(phone)}

    @app.post("/api/author/rub-payouts")
    async def api_author_request_rub_payout(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        profile = await get_author_financial_profile(user.app_user_id)
        if not profile or profile["verification_status"] != "verified":
            raise HTTPException(status_code=409, detail="Сначала заполните и подтвердите платёжный профиль автора.")
        amount_minor = int(payload.get("amount_minor") or 0)
        if amount_minor < 10000:
            raise HTTPException(status_code=400, detail="Минимальная заявка на выплату — 100 рублей.")
        payout_id = await create_author_rub_payout_request(
            user.app_user_id,
            amount_minor=amount_minor,
            phone_encrypted=str(profile["sbp_phone_encrypted"] or ""),
            bank_id=str(profile["sbp_bank_id"] or ""),
            bank_name=str(profile["sbp_bank_name"] or ""),
            idempotence_key=str(uuid.uuid4()),
        )
        await add_audit(user.app_user_id, "author_rub_payout_requested", "rub_payout", str(payout_id), None, str(amount_minor))
        return {"ok": True, "payout_id": payout_id, "status": "new", "amount_minor": amount_minor}

    @app.post("/api/control/rub-payout/{payout_id}/execute")
    async def api_control_execute_rub_payout(
        payout_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        actor, _, _ = await control_session(x_telegram_init_data, "payouts")
        request_row = await get_author_rub_payout_request(payout_id)
        if not request_row:
            raise HTTPException(status_code=404, detail="Заявка не найдена.")
        if request_row["status"] not in {"new", "failed"}:
            raise HTTPException(status_code=409, detail="Заявка уже обрабатывается или завершена.")
        if not await payouts_configured():
            raise HTTPException(status_code=503, detail="Выплаты ЮKassa ещё не подключены владельцем.")
        phone = decrypt_text(str(request_row["phone_encrypted"] or ""))
        try:
            await update_author_rub_payout_status(payout_id, "processing")
            result = await create_sbp_payout(
                amount_minor=int(request_row["amount_minor"]),
                phone=phone,
                bank_id=str(request_row["bank_id"] or ""),
                description=f"Вознаграждение автора Вокслиры, заявка {payout_id}",
                metadata={"voxlyra_payout_id": payout_id, "author_id": int(request_row["author_id"])},
                idempotence_key=str(request_row["idempotence_key"]),
            )
            local_status = "succeeded" if result.status == "succeeded" else "processing"
            await update_author_rub_payout_status(
                payout_id,
                local_status,
                provider_payout_id=result.payout_id,
            )
            await add_audit(actor.app_user_id, "author_rub_payout_executed", "rub_payout", str(payout_id), None, result.status)
            return {"ok": True, "status": result.status, "provider_payout_id": result.payout_id}
        except (ValueError, YooKassaPayoutError) as exc:
            await update_author_rub_payout_status(payout_id, "failed", failure_reason=str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/author/projects")
    async def api_author_create_project(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, profile = await author_session(x_telegram_init_data)
        title = str(payload.get("title") or "").strip()
        content_type = str(payload.get("content_type") or "book")
        reading_mode = str(payload.get("reading_mode") or ("rtl" if content_type == "manga" else "vertical" if content_type in {"manhwa", "webtoon"} else "ltr"))
        if len(title) < 2:
            raise HTTPException(status_code=400, detail="Введите название произведения.")
        if content_type not in {"book", *GRAPHIC_CONTENT_TYPES}:
            raise HTTPException(status_code=400, detail="Выберите тип произведения из списка.")
        if reading_mode not in GRAPHIC_READING_MODES - {"inherit"}:
            raise HTTPException(status_code=400, detail="Выберите режим чтения из списка.")
        book_id = await create_book(
            int(profile["id"]),
            title[:160],
            str(payload.get("description") or "").strip()[:12000],
            str(payload.get("age_limit") or "16+"),
            "writing",
            False,
            "free",
            0,
            content_type=content_type,
            reading_mode=reading_mode,
        )
        await add_audit(user.app_user_id, "author_project_created_web", "book", str(book_id), None, content_type)
        book = await get_book(book_id)
        return {"ok": True, "book": _row_to_dict(book)}

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
        graphic_chapters = await list_graphic_chapters_for_book(book_id)
        graphic_volumes = await list_graphic_volumes_for_book(book_id)
        chapter_packages = await list_chapter_packages_for_book(book_id, include_inactive=True)
        audio = await list_audio_chapters_for_book(book_id)
        return {
            "ok": True,
            "book": _row_to_dict(book_row),
            "pricing": await get_book_pricing_state(book_id),
            "chapters": _rows_to_dicts(chapters),
            "graphic_chapters": _rows_to_dicts(graphic_chapters),
            "graphic_volumes": _rows_to_dicts(graphic_volumes),
            "chapter_packages": _rows_to_dicts(chapter_packages),
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

        current = await get_book(book_id)
        if not current:
            raise HTTPException(status_code=404, detail="Книга не найдена.")

        values = {key: payload[key] for key in (
            "title", "description", "age_limit", "writing_status",
            "allow_download", "content_type", "reading_mode",
        ) if key in payload}
        if "age_limit" in values and values["age_limit"] not in {"0+", "6+", "12+", "16+", "18+"}:
            raise HTTPException(status_code=400, detail="Выберите возрастное ограничение из списка.")
        if "writing_status" in values and values["writing_status"] not in {"writing", "finished", "frozen"}:
            raise HTTPException(status_code=400, detail="Выберите состояние книги из списка.")
        if "content_type" in values and values["content_type"] not in {"book", *GRAPHIC_CONTENT_TYPES}:
            raise HTTPException(status_code=400, detail="Выберите тип произведения из списка.")
        if "reading_mode" in values and values["reading_mode"] not in GRAPHIC_READING_MODES - {"inherit"}:
            raise HTTPException(status_code=400, detail="Выберите режим чтения из списка.")

        commerce_changed = False
        if "price_stars" in payload or "pricing_type" in payload:
            try:
                price = max(0, min(100000, int(payload.get("price_stars", current["price_stars"]) or 0)))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Цена всей книги должна быть числом.")
            requested_mode = str(payload.get("pricing_type") or current["pricing_type"] or "free").strip().lower()
            if requested_mode not in {"free", "whole_book", "chapters", "premium"}:
                raise HTTPException(status_code=400, detail="Выберите доступ: бесплатно, покупка книги, покупка глав или VoxLyra Premium.")
            if requested_mode in {"whole_book", "chapters"} and price <= 0:
                raise HTTPException(status_code=400, detail="Для режима разовой покупки укажите цену всей книги больше 0 Stars.")
            if requested_mode in {"free", "premium"}:
                price = 0
            current_mode = "premium" if str(current["pricing_type"] or "") == "premium" else ("free" if int(current["price_stars"] or 0) <= 0 else str(current["pricing_type"] or "whole_book"))
            if requested_mode == "free" and current_mode != "free" and not bool(payload.get("confirm_make_free")):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "confirm_make_free",
                        "message": "Подтвердите перевод книги в полностью бесплатный режим. Все главы станут бесплатными, а продажа и доступ по Premium отключатся.",
                    },
                )
            commerce_changed = await update_book_price(
                book_id,
                user.app_user_id,
                requested_mode,
                price,
                restore_saved_prices=bool(payload.get("restore_saved_prices")),
            )

        fields_changed = True
        if values:
            fields_changed = await update_author_book_fields(book_id, user.app_user_id, values)
        if not commerce_changed and values and not fields_changed:
            raise HTTPException(status_code=400, detail="Не удалось сохранить изменения.")
        if not commerce_changed and not values and not ({"price_stars", "pricing_type"} & payload.keys()):
            raise HTTPException(status_code=400, detail="Нет изменений для сохранения.")

        await add_audit(
            user.app_user_id,
            "book_updated_web",
            "book",
            str(book_id),
            None,
            ",".join(sorted(set(values.keys()) | ({"pricing"} if commerce_changed else set()))),
        )
        book_row = await get_book(book_id)
        return {
            "ok": True,
            "book": _row_to_dict(book_row),
            "pricing": await get_book_pricing_state(book_id),
        }

    @app.post("/api/author/book/{book_id}/restore-chapter-prices")
    async def api_author_restore_chapter_prices(
        book_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        result = await restore_saved_chapter_prices(book_id, user.app_user_id)
        if result.get("reason") == "not_found":
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        if result.get("reason") == "chapter_sales_disabled":
            raise HTTPException(status_code=400, detail="Сначала включите продажу отдельных глав.")
        await add_audit(user.app_user_id, "chapter_prices_restored_web", "book", str(book_id), None, str(result.get("updated", 0)))
        return result

    @app.post("/api/author/book/{book_id}/chapter-packages")
    async def api_author_create_chapter_package(
        book_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
        count = int(payload.get("chapters_count") or 0)
        price = int(payload.get("price_stars") or 0)
        scope = str(payload.get("content_scope") or "text")
        if count < 1 or count > 10000:
            raise HTTPException(status_code=400, detail="В пакете должно быть от 1 до 10 000 глав.")
        if price < 1 or price > 1000000:
            raise HTTPException(status_code=400, detail="Цена пакета должна быть от 1 до 1 000 000 Stars.")
        if scope not in {"text", "graphic", "all"}:
            raise HTTPException(status_code=400, detail="Выберите вид глав для пакета.")
        book = await get_book(book_id)
        is_graphic_book = bool(book and str(book["content_type"] or "book") in GRAPHIC_CONTENT_TYPES)
        pricing_mode = "free" if not book or int(book["price_stars"] or 0) <= 0 else ("chapters" if str(book["pricing_type"] or "") == "chapters" else "whole_book")
        if not is_graphic_book and scope in {"text", "all"} and pricing_mode != "chapters":
            raise HTTPException(status_code=400, detail="Текстовые пакеты доступны только когда включена продажа отдельных глав.")
        package_id = await create_chapter_package_for_author(
            book_id, user.app_user_id,
            title=str(payload.get("title") or ""),
            chapters_count=count,
            price_stars=price,
            content_scope=scope,
        )
        await add_audit(user.app_user_id, "chapter_package_created", "chapter_package", str(package_id), None, f"{count}:{price}:{scope}")
        packages = await list_chapter_packages_for_book(book_id, include_inactive=True)
        return {"ok": True, "package_id": package_id, "items": _rows_to_dicts(packages)}

    @app.patch("/api/author/chapter-package/{package_id}")
    async def api_author_update_chapter_package(
        package_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        count = int(payload.get("chapters_count") or 0)
        price = int(payload.get("price_stars") or 0)
        scope = str(payload.get("content_scope") or "text")
        if count < 1 or count > 10000 or price < 1 or price > 1000000 or scope not in {"text", "graphic", "all"}:
            raise HTTPException(status_code=400, detail="Проверьте количество глав, цену и тип пакета.")
        package = await get_chapter_package(package_id)
        if not package or int(package["author_user_id"] or 0) != int(user.app_user_id):
            raise HTTPException(status_code=404, detail="Пакет не найден.")
        is_graphic_book = str(package["content_type"] or "book") in GRAPHIC_CONTENT_TYPES
        pricing_mode = "free" if int(package["book_price_stars"] or 0) <= 0 else ("chapters" if str(package["pricing_type"] or "") == "chapters" else "whole_book")
        if not is_graphic_book and scope in {"text", "all"} and pricing_mode != "chapters":
            raise HTTPException(status_code=400, detail="Текстовые пакеты доступны только когда включена продажа отдельных глав.")
        ok = await update_chapter_package_for_author(
            package_id, user.app_user_id,
            title=str(payload.get("title") or ""),
            chapters_count=count,
            price_stars=price,
            content_scope=scope,
            is_active=bool(payload.get("is_active", True)),
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Пакет не найден.")
        await add_audit(user.app_user_id, "chapter_package_updated", "chapter_package", str(package_id), None, f"{count}:{price}:{scope}")
        return {"ok": True}

    @app.delete("/api/author/chapter-package/{package_id}")
    async def api_author_delete_chapter_package(
        package_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        ok = await deactivate_chapter_package_for_author(package_id, user.app_user_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Пакет не найден.")
        await add_audit(user.app_user_id, "chapter_package_deactivated", "chapter_package", str(package_id), None, "")
        return {"ok": True}

    @app.post("/api/author/book/{book_id}/submit")
    async def api_author_submit_book(book_id: int, x_telegram_init_data: str | None = Header(default=None)):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        chapters = await list_chapters_for_book(book_id)
        graphic_chapters = await list_graphic_chapters_for_book(book_id)
        if not chapters and not graphic_chapters:
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

    @app.patch("/api/author/book/{book_id}/chapter-prices")
    async def api_author_update_chapter_prices(
        book_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        try:
            start_number = int(payload.get("start_number") or payload.get("chapter_number") or 0)
            end_number = int(payload.get("end_number") or start_number)
            price = int(payload.get("price_stars") or 0)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Номера глав и цена должны быть числами.")
        if start_number < 1 or end_number < 1:
            raise HTTPException(status_code=400, detail="Укажите номер главы или диапазон от 1.")
        if price < 0 or price > 100000:
            raise HTTPException(status_code=400, detail="Цена главы должна быть от 0 до 100 000 Stars.")
        access_mode = str(payload.get("access_mode") or ("free" if price <= 0 else "chapter"))
        result = await update_chapter_access_range(
            book_id, user.app_user_id, start_number, end_number, access_mode, price
        )
        reason_messages = {
            "book_is_free": "Книга полностью бесплатна. Настройка цен глав отключена.",
            "chapter_sales_disabled": "Для этой книги выключена продажа отдельных глав.",
            "price_required": "Для отдельной продажи укажите цену больше 0.",
            "premium_mode_only": "В режиме Premium главы можно сделать только бесплатными или доступными по подписке.",
            "premium_mode_required": "Сначала переключите книгу в режим VoxLyra Premium.",
            "chapters_not_found": "В указанном диапазоне нет глав.",
            "not_found": "Книга не найдена.",
        }
        if not result.get("updated"):
            reason = str(result.get("reason") or "chapters_not_found")
            raise HTTPException(status_code=400 if reason != "not_found" else 404, detail=reason_messages.get(reason, "Не удалось изменить доступ к главам."))
        await add_audit(
            user.app_user_id,
            "chapter_access_range_updated_web",
            "book",
            str(book_id),
            None,
            f"{result['start_number']}-{result['end_number']}:{result['access_mode']}={result['price_stars']}; updated={result['updated']}",
        )
        return {"ok": True, **result}

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
        access_mode = str(payload.get("access_mode") or ("free" if price <= 0 else "chapter"))
        book = await get_book(book_id)
        declared = str(book["pricing_type"] or "") if book else "free"
        mode = "premium" if declared == "premium" else ("free" if not book or int(book["price_stars"] or 0) <= 0 else ("chapters" if declared == "chapters" else "whole_book"))
        if mode == "free":
            access_mode, price = "free", 0
        elif mode == "premium":
            if access_mode not in {"free", "premium"}:
                raise HTTPException(status_code=400, detail="В режиме Premium глава может быть бесплатной или доступной по подписке.")
            price = 0
        elif access_mode == "chapter" and mode != "chapters":
            raise HTTPException(status_code=400, detail="Для этой книги выключена продажа отдельных глав.")
        elif access_mode not in {"free", "book", "chapter"}:
            raise HTTPException(status_code=400, detail="Выберите способ доступа к главе.")
        elif access_mode == "chapter" and price <= 0:
            raise HTTPException(status_code=400, detail="Для отдельной продажи укажите цену больше 0.")
        if len(title) < 2:
            raise HTTPException(status_code=400, detail="Введите название главы.")
        if len(text) < 100:
            raise HTTPException(status_code=400, detail="Текст главы слишком короткий.")
        chapter_id = await add_manual_chapter(
            book_id, title[:160], text,
            is_free=access_mode == "free",
            price_stars=price if access_mode == "chapter" else 0,
        )
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
        if "price_stars" in payload or "access_mode" in payload:
            price = max(0, min(100000, int(payload.get("price_stars") or 0)))
            access_mode = str(payload.get("access_mode") or ("free" if price <= 0 else "chapter"))
            result = await update_chapter_access_range(
                int(chapter["book_id"]), user.app_user_id, int(chapter["number"]), int(chapter["number"]), access_mode, price
            )
            if not result.get("updated"):
                reason = str(result.get("reason") or "")
                detail = {
                    "book_is_free": "Книга полностью бесплатна. Цена главы не требуется.",
                    "chapter_sales_disabled": "Для этой книги выключена продажа отдельных глав.",
                    "price_required": "Для отдельной продажи укажите цену больше 0.",
                    "premium_mode_only": "В режиме Premium глава может быть бесплатной или доступной по подписке.",
                    "premium_mode_required": "Сначала переключите книгу в режим VoxLyra Premium.",
                }.get(reason, "Не удалось изменить доступ к главе.")
                raise HTTPException(status_code=400, detail=detail)
            changed = True
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

    @app.patch("/api/author/graphic-chapter/{graphic_chapter_id}")
    async def api_author_update_graphic_chapter(
        graphic_chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        reading_mode = payload.get("reading_mode")
        if reading_mode is not None and str(reading_mode) not in GRAPHIC_READING_MODES:
            raise HTTPException(status_code=400, detail="Выберите режим чтения из списка.")
        price = max(0, min(100000, int(payload.get("price_stars") or 0))) if "price_stars" in payload else None
        ok = await update_graphic_chapter_for_author(
            graphic_chapter_id,
            user.app_user_id,
            title=payload.get("title"),
            reading_mode=str(reading_mode) if reading_mode is not None else None,
            is_free=(price == 0) if price is not None else None,
            price_stars=price,
            volume_number=(max(1, int(payload.get("volume_number") or 1)) if "volume_number" in payload else None),
            volume_title=(str(payload.get("volume_title") or "").strip()[:120] if "volume_title" in payload else None),
        )
        preview_changed = False
        if "preview_pages" in payload:
            preview_changed = await set_graphic_chapter_preview_for_author(
                graphic_chapter_id, user.app_user_id, max(0, min(50, int(payload.get("preview_pages") or 0)))
            )
        if not ok and not preview_changed:
            raise HTTPException(status_code=404, detail="Графическая глава не найдена.")
        await add_audit(user.app_user_id, "graphic_chapter_updated_web", "graphic_chapter", str(graphic_chapter_id))
        chapter = await get_graphic_chapter(graphic_chapter_id)
        return {"ok": True, "chapter": _row_to_dict(chapter)}

    @app.patch("/api/author/book/{book_id}/graphic-volume/{volume_number}")
    async def api_author_update_graphic_volume(
        book_id: int,
        volume_number: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
        price = max(0, min(100000, int(payload.get("price_stars") or 0)))
        ok = await upsert_graphic_volume_for_author(
            book_id,
            max(1, min(10000, int(volume_number))),
            user.app_user_id,
            title=str(payload.get("title") or "").strip()[:120],
            is_free=bool(payload.get("is_free", price <= 0)),
            price_stars=price,
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Том не найден.")
        row = await get_graphic_volume(book_id, volume_number)
        await add_audit(user.app_user_id, "graphic_volume_updated_web", "book", str(book_id), None, str(volume_number))
        return {"ok": True, "volume": _row_to_dict(row)}

    @app.delete("/api/author/graphic-chapter/{graphic_chapter_id}")
    async def api_author_delete_graphic_chapter(
        graphic_chapter_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        chapter = await get_graphic_chapter(graphic_chapter_id)
        pages = await list_graphic_pages(graphic_chapter_id) if chapter else []
        if not await delete_graphic_chapter_for_author(graphic_chapter_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Графическая глава не найдена.")
        for page in pages:
            delete_page_files(page, root=GRAPHIC_STORAGE_ROOT)
        if pages:
            parent = _safe_graphic_path(str(Path(str(pages[0]["file_path"])).parent))
            if parent:
                shutil.rmtree(parent, ignore_errors=True)
        await add_audit(user.app_user_id, "graphic_chapter_deleted_web", "graphic_chapter", str(graphic_chapter_id))
        return {"ok": True}

    @app.get("/api/author/graphic-chapter/{graphic_chapter_id}/pages")
    async def api_author_graphic_pages(
        graphic_chapter_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        pages = await list_graphic_pages_for_author(graphic_chapter_id, user.app_user_id)
        if pages is None:
            raise HTTPException(status_code=404, detail="Графическая глава не найдена.")
        chapter = await get_graphic_chapter(graphic_chapter_id)
        page_items = _graphic_page_payloads(
            user_id=user.app_user_id, chapter_id=graphic_chapter_id, pages=pages, include_ids=True
        )
        for item in page_items:
            page_id = int(item.get("id") or 0)
            item["texts"] = _rows_to_dicts(await list_graphic_page_texts(page_id))
            item["translations"] = _rows_to_dicts(await list_graphic_translation_regions(page_id, "ru"))
            item["frames"] = _rows_to_dicts(await list_graphic_page_frames(page_id))
        return {
            "ok": True,
            "chapter": _row_to_dict(chapter),
            "pages": page_items,
            "ocr_available": ocr_engine_available(),
        }

    @app.post("/api/author/graphic-chapter/{graphic_chapter_id}/pages/reorder")
    async def api_author_reorder_graphic_pages(
        graphic_chapter_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        raw_ids = payload.get("page_ids")
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="Передан неверный порядок страниц.")
        try:
            page_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Передан неверный порядок страниц.") from exc
        if not await reorder_graphic_pages_for_author(graphic_chapter_id, user.app_user_id, page_ids):
            raise HTTPException(status_code=400, detail="Не удалось сохранить порядок страниц. Обновите редактор и повторите.")
        await add_audit(
            user.app_user_id,
            "graphic_pages_reordered_web",
            "graphic_chapter",
            str(graphic_chapter_id),
            None,
            f"{len(page_ids)} pages",
        )
        pages = await list_graphic_pages_for_author(graphic_chapter_id, user.app_user_id) or []
        return {
            "ok": True,
            "pages": _graphic_page_payloads(
                user_id=user.app_user_id, chapter_id=graphic_chapter_id, pages=pages, include_ids=True
            ),
        }

    @app.post("/api/author/graphic-page/{graphic_page_id}/ocr")
    async def api_author_graphic_page_ocr(
        graphic_page_id: int,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        page = await get_graphic_page(graphic_page_id)
        if not page or int(page["author_user_id"] or 0) != int(user.app_user_id):
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        source = _safe_graphic_path(str(page["file_path"] or ""))
        if not source:
            raise HTTPException(status_code=404, detail="Файл страницы не найден.")
        language = str((payload or {}).get("language") or "rus+eng")[:32]
        try:
            result = await asyncio.to_thread(recognize_graphic_text, source, language)
        except GraphicOCRError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        await upsert_graphic_page_text(
            graphic_page_id, user.app_user_id, language_code="ru", text_kind="ocr",
            text=result["text"], confidence=float(result["confidence"]), status="published",
        )
        await add_audit(user.app_user_id, "graphic_page_ocr", "graphic_page", str(graphic_page_id), None, language)
        return {"ok": True, **result}

    @app.put("/api/author/graphic-page/{graphic_page_id}/text")
    async def api_author_graphic_page_text(
        graphic_page_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        ok = await upsert_graphic_page_text(
            graphic_page_id, user.app_user_id,
            language_code=str(payload.get("language_code") or "ru"),
            text_kind=str(payload.get("text_kind") or "ocr"),
            text=str(payload.get("text") or ""),
            confidence=float(payload.get("confidence") or 100),
            status="published",
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        return {"ok": True}

    @app.put("/api/author/graphic-page/{graphic_page_id}/translations")
    async def api_author_graphic_page_translations(
        graphic_page_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        regions = payload.get("regions")
        if not isinstance(regions, list):
            raise HTTPException(status_code=400, detail="Передайте список переводных областей.")
        ok = await replace_graphic_translation_regions_for_author(
            graphic_page_id, user.app_user_id, str(payload.get("language_code") or "ru"), regions
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        return {"ok": True, "regions": _rows_to_dicts(await list_graphic_translation_regions(graphic_page_id, str(payload.get("language_code") or "ru")))}

    @app.post("/api/author/graphic-page/{graphic_page_id}/frames/auto")
    async def api_author_graphic_page_frames_auto(
        graphic_page_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        page = await get_graphic_page(graphic_page_id)
        if not page or int(page["author_user_id"] or 0) != int(user.app_user_id):
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        source = _safe_graphic_path(str(page["file_path"] or ""))
        if not source:
            raise HTTPException(status_code=404, detail="Файл страницы не найден.")
        try:
            frames = await asyncio.to_thread(suggest_graphic_frames, source)
        except GraphicOCRError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await replace_graphic_frames_for_author(graphic_page_id, user.app_user_id, frames, source="auto")
        return {"ok": True, "frames": _rows_to_dicts(await list_graphic_page_frames(graphic_page_id))}

    @app.put("/api/author/graphic-page/{graphic_page_id}/frames")
    async def api_author_graphic_page_frames(
        graphic_page_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        frames = payload.get("frames")
        if not isinstance(frames, list):
            raise HTTPException(status_code=400, detail="Передайте список кадров.")
        ok = await replace_graphic_frames_for_author(graphic_page_id, user.app_user_id, frames, source="manual")
        if not ok:
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        return {"ok": True, "frames": _rows_to_dicts(await list_graphic_page_frames(graphic_page_id))}

    @app.get("/api/author/graphic-chapter/{graphic_chapter_id}/statistics")
    async def api_author_graphic_statistics(
        graphic_chapter_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        chapter = await get_graphic_chapter(graphic_chapter_id)
        if not chapter or int(chapter["author_user_id"] or 0) != int(user.app_user_id):
            raise HTTPException(status_code=404, detail="Глава не найдена.")
        return {"ok": True, "statistics": await get_graphic_chapter_statistics(graphic_chapter_id)}

    @app.post("/api/author/graphic-page/{graphic_page_id}/rotate")
    async def api_author_rotate_graphic_page(
        graphic_page_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        page = await get_graphic_page(graphic_page_id)
        if not page or int(page["author_user_id"] or 0) != int(user.app_user_id):
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        try:
            degrees = int(payload.get("degrees") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Выберите корректный поворот.") from exc
        source = _safe_graphic_path(str(page["file_path"] or ""))
        if not source or not source.is_file():
            raise HTTPException(status_code=404, detail="Файл страницы не найден.")
        temporary = source.with_name(f".{source.stem}-rotate-{uuid.uuid4().hex}.webp")
        try:
            prepared = await asyncio.to_thread(rotate_graphic_page_file, source, temporary, degrees)
            installed = install_prepared_page(prepared, source)
            ok = await update_graphic_page_file_for_author(
                graphic_page_id,
                user.app_user_id,
                source_filename=str(page["source_filename"] or source.name),
                mime_type=str(installed["mime_type"]),
                width=int(installed["width"]),
                height=int(installed["height"]),
                file_size=int(installed["file_size"]),
                checksum=str(installed["checksum"]),
                variants_json=str(installed["variants_json"]),
                storage_backend=str(installed["storage_backend"]),
                storage_key=str(installed["storage_key"]),
            )
            if not ok:
                raise HTTPException(status_code=404, detail="Страница не найдена.")
        except GraphicImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Не удалось повернуть страницу.") from exc
        finally:
            temporary.unlink(missing_ok=True)
            for item in (getattr(locals().get("prepared", None), "variants", None) or {}).values():
                try:
                    Path(str(item.get("path") or "")).unlink(missing_ok=True)
                except Exception:
                    pass
        await add_audit(user.app_user_id, "graphic_page_rotated_web", "graphic_page", str(graphic_page_id), None, str(degrees))
        updated = await get_graphic_page(graphic_page_id)
        return {"ok": True, "page": _row_to_dict(updated)}

    @app.post("/api/author/graphic-page/{graphic_page_id}/replace")
    async def api_author_replace_graphic_page(
        graphic_page_id: int,
        file: UploadFile = File(...),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        page = await get_graphic_page(graphic_page_id)
        if not page or int(page["author_user_id"] or 0) != int(user.app_user_id):
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        target = _safe_graphic_path(str(page["file_path"] or ""))
        if not target or not target.is_file():
            raise HTTPException(status_code=404, detail="Файл страницы не найден.")
        temp_dir = GRAPHIC_TEMP_ROOT / f"replace-{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=False)
        original_name = Path(file.filename or "replacement.png").name
        uploaded = temp_dir / original_name
        max_bytes = max(1, int(settings.MAX_COMIC_PAGE_MB or 30)) * 1024 * 1024
        written = 0
        try:
            with uploaded.open("wb") as stream:
                while True:
                    block = await file.read(1024 * 1024)
                    if not block:
                        break
                    written += len(block)
                    if written > max_bytes:
                        raise GraphicImportError(
                            f"Страница больше допустимых {settings.MAX_COMIC_PAGE_MB} МБ."
                        )
                    stream.write(block)
            if written <= 0:
                raise GraphicImportError("Загружен пустой файл.")
            prepared = await asyncio.to_thread(
                prepare_replacement_page, uploaded, original_name, temp_dir / "prepared"
            )
            installed = install_prepared_page(prepared, target)
            ok = await update_graphic_page_file_for_author(
                graphic_page_id,
                user.app_user_id,
                source_filename=original_name,
                mime_type=str(installed["mime_type"]),
                width=int(installed["width"]),
                height=int(installed["height"]),
                file_size=int(installed["file_size"]),
                checksum=str(installed["checksum"]),
                variants_json=str(installed["variants_json"]),
                storage_backend=str(installed["storage_backend"]),
                storage_key=str(installed["storage_key"]),
            )
            if not ok:
                raise HTTPException(status_code=404, detail="Страница не найдена.")
        except GraphicImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Не удалось заменить страницу.") from exc
        finally:
            await file.close()
            shutil.rmtree(temp_dir, ignore_errors=True)
        await add_audit(user.app_user_id, "graphic_page_replaced_web", "graphic_page", str(graphic_page_id), None, original_name)
        updated = await get_graphic_page(graphic_page_id)
        return {"ok": True, "page": _row_to_dict(updated)}

    @app.delete("/api/author/graphic-page/{graphic_page_id}")
    async def api_author_delete_graphic_page(
        graphic_page_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        deleted = await delete_graphic_page_for_author(graphic_page_id, user.app_user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        if deleted.get("error") == "last_page":
            raise HTTPException(status_code=400, detail="В главе должна остаться хотя бы одна страница.")
        delete_page_files(deleted, root=GRAPHIC_STORAGE_ROOT)
        chapter_id = int(deleted["graphic_chapter_id"])
        await add_audit(user.app_user_id, "graphic_page_deleted_web", "graphic_page", str(graphic_page_id), None, str(chapter_id))
        pages = await list_graphic_pages_for_author(chapter_id, user.app_user_id) or []
        return {
            "ok": True,
            "pages": _graphic_page_payloads(
                user_id=user.app_user_id, chapter_id=chapter_id, pages=pages, include_ids=True
            ),
        }

    @app.post("/api/author/book/{book_id}/graphic/upload/start")
    async def api_author_graphic_upload_start(
        book_id: int,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
        book = await get_book(book_id)
        if not book or str(book["content_type"] or "book") not in GRAPHIC_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="Сначала выберите тип: комикс, манга, манхва, вебтун или графический роман.")
        resume_id = str(payload.get("resume_upload_id") or "").strip()
        try:
            meta = None
            resumed = False
            if resume_id:
                existing = load_upload(resume_id, user_id=user.app_user_id, book_id=book_id)
                if (
                    str(existing.get("filename") or "") == Path(str(payload.get("filename") or "")).name
                    and int(existing.get("total_size") or 0) == int(payload.get("size") or 0)
                    and str(existing.get("kind") or "") == "graphic"
                ):
                    meta = existing
                    resumed = True
            if meta is None:
                meta = create_graphic_upload(
                    user_id=user.app_user_id,
                    book_id=book_id,
                    filename=str(payload.get("filename") or ""),
                    total_size=int(payload.get("size") or 0),
                )
            status = get_upload_status(meta["upload_id"], user_id=user.app_user_id, book_id=book_id)
        except ChunkedUploadError as exc:
            if resume_id:
                meta = create_graphic_upload(
                    user_id=user.app_user_id,
                    book_id=book_id,
                    filename=str(payload.get("filename") or ""),
                    total_size=int(payload.get("size") or 0),
                )
                status = get_upload_status(meta["upload_id"], user_id=user.app_user_id, book_id=book_id)
                resumed = False
            else:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True, "upload_id": meta["upload_id"], "chunk_size": CHUNK_SIZE_BYTES,
            "resumed": resumed, "received": status.get("received", []),
            "progress_percent": status.get("progress_percent", 0),
        }

    @app.get("/api/author/book/{book_id}/graphic/upload/{upload_id}/status")
    async def api_author_graphic_upload_status(
        book_id: int,
        upload_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
        try:
            status = get_upload_status(upload_id, user_id=user.app_user_id, book_id=book_id)
        except ChunkedUploadError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, **status, "chunk_size": CHUNK_SIZE_BYTES}

    @app.delete("/api/author/book/{book_id}/graphic/upload/{upload_id}")
    async def api_author_graphic_upload_cancel(
        book_id: int,
        upload_id: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        try:
            load_upload(upload_id, user_id=user.app_user_id, book_id=book_id)
        except ChunkedUploadError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        cleanup_upload(upload_id)
        return {"ok": True}

    @app.post("/api/author/book/{book_id}/graphic/upload/{upload_id}/chunk")
    async def api_author_graphic_upload_chunk(
        book_id: int,
        upload_id: str,
        index: int = Form(...),
        total_chunks: int = Form(...),
        chunk: UploadFile = File(...),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
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

    @app.post("/api/author/book/{book_id}/graphic/upload/{upload_id}/finish")
    async def api_author_graphic_upload_finish(
        book_id: int,
        upload_id: str,
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            cleanup_upload(upload_id)
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
        book = await get_book(book_id)
        if not book or str(book["content_type"] or "book") not in GRAPHIC_CONTENT_TYPES:
            cleanup_upload(upload_id)
            raise HTTPException(status_code=400, detail="Для загрузки страниц выберите графический тип произведения.")
        title = str(payload.get("title") or "").strip()
        if len(title) < 2:
            cleanup_upload(upload_id)
            raise HTTPException(status_code=400, detail="Введите название главы.")
        reading_mode = str(payload.get("reading_mode") or "inherit")
        if reading_mode not in GRAPHIC_READING_MODES:
            cleanup_upload(upload_id)
            raise HTTPException(status_code=400, detail="Выберите режим чтения из списка.")
        price = max(0, min(100000, int(payload.get("price_stars") or 0)))
        volume_number = max(1, min(10000, int(payload.get("volume_number") or 1)))
        volume_title = str(payload.get("volume_title") or "").strip()[:120]
        preview_pages = max(0, min(50, int(payload.get("preview_pages") or 0)))
        total_chunks = int(payload.get("total_chunks") or 0)
        try:
            path, meta = assemble_upload(
                upload_id,
                user_id=user.app_user_id,
                book_id=book_id,
                total_chunks=total_chunks,
            )
            work_dir = path.parent / "graphic-prepared"
            split_long_pages = bool(payload.get("split_long_pages")) or reading_mode == "vertical" or str(book["content_type"] or "") in {"webtoon", "manhwa"}
            prepared = await asyncio.to_thread(
                prepare_graphic_file,
                path,
                str(meta.get("filename") or path.name),
                work_dir,
                split_long_pages=split_long_pages,
            )
            report = graphic_report(prepared)
            chapter = await _commit_graphic_chapter(
                book_id=book_id,
                title=title,
                reading_mode=reading_mode,
                price_stars=price,
                source_filename=str(meta.get("filename") or path.name),
                prepared_pages=prepared,
                volume_number=volume_number,
                volume_title=volume_title,
                preview_pages=preview_pages,
            )
            await add_audit(
                user.app_user_id,
                "graphic_chapter_imported_web",
                "graphic_chapter",
                str(chapter["id"]),
                None,
                f"{report['pages_count']} pages",
            )
            workflow = None
            if settings.BOT_TOKEN:
                delivery_bot = Bot(token=settings.BOT_TOKEN)
                try:
                    result = await finish_book_content_workflow(
                        bot=delivery_bot,
                        book_id=book_id,
                        actor_user_id=user.app_user_id,
                        actor_telegram_id=user.telegram_id,
                        source="miniapp_graphic_import",
                    )
                    workflow = {
                        "status": result.workflow_status,
                        "channel_status": result.channel_status,
                        "channel_message": result.channel_message,
                        "review_reasons": result.duplicate_text,
                    }
                except Exception:
                    workflow = {"status": "saved", "channel_status": "", "channel_message": ""}
                finally:
                    await delivery_bot.session.close()
            notification = await _notify_graphic_chapter_if_published(
                book_id=book_id, chapter=chapter, actor_user_id=user.app_user_id
            )
            return {"ok": True, "chapter": chapter, "report": report, "workflow": workflow, "notification": notification}
        except (ChunkedUploadError, GraphicImportError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Не удалось обработать страницы. Проверьте файл и повторите попытку.") from exc
        finally:
            cleanup_upload(upload_id)

    @app.post("/api/author/book/{book_id}/graphic/images")
    async def api_author_graphic_images(
        book_id: int,
        title: str = Form(...),
        reading_mode: str = Form("inherit"),
        price_stars: int = Form(0),
        volume_number: int = Form(1),
        volume_title: str = Form(""),
        preview_pages: int = Form(3),
        split_long_pages: bool = Form(False),
        files: list[UploadFile] = File(...),
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _ = await author_session(x_telegram_init_data)
        if not await book_belongs_to_author(book_id, user.app_user_id):
            raise HTTPException(status_code=404, detail="Произведение не найдено.")
        book = await get_book(book_id)
        if not book or str(book["content_type"] or "book") not in GRAPHIC_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="Для загрузки страниц выберите графический тип произведения.")
        clean_title = str(title or "").strip()
        if len(clean_title) < 2:
            raise HTTPException(status_code=400, detail="Введите название главы.")
        if reading_mode not in GRAPHIC_READING_MODES:
            raise HTTPException(status_code=400, detail="Выберите режим чтения из списка.")
        if not files:
            raise HTTPException(status_code=400, detail="Выберите изображения страниц.")
        max_pages = max(1, int(settings.MAX_COMIC_PAGES or 500))
        if len(files) > max_pages:
            raise HTTPException(status_code=400, detail=f"В одной главе можно загрузить не больше {max_pages} страниц.")

        temp_dir = GRAPHIC_TEMP_ROOT / uuid.uuid4().hex
        source_dir = temp_dir / "uploads"
        source_dir.mkdir(parents=True, exist_ok=False)
        saved: list[tuple[Path, str]] = []
        total_bytes = 0
        max_total = int(settings.MAX_COMIC_UPLOAD_MB or 0) * 1024 * 1024
        max_one = max(1, int(settings.MAX_COMIC_PAGE_MB or 30)) * 1024 * 1024
        try:
            for index, upload in enumerate(files, 1):
                filename = Path(upload.filename or f"page-{index}.jpg").name
                target = source_dir / f"{index:05d}-{filename}"
                written = 0
                with target.open("wb") as destination:
                    while True:
                        data = await upload.read(1024 * 1024)
                        if not data:
                            break
                        written += len(data)
                        total_bytes += len(data)
                        if written > max_one:
                            raise GraphicImportError(f"Страница «{filename}» больше допустимых {settings.MAX_COMIC_PAGE_MB} МБ.")
                        if max_total > 0 and total_bytes > max_total:
                            raise GraphicImportError(f"Файлы превышают допустимый общий размер {settings.MAX_COMIC_UPLOAD_MB} МБ.")
                        destination.write(data)
                await upload.close()
                saved.append((target, filename))
            use_slicing = bool(split_long_pages) or reading_mode == "vertical" or str(book["content_type"] or "") in {"webtoon", "manhwa"}
            prepared = await asyncio.to_thread(
                prepare_graphic_images, saved, temp_dir / "prepared", split_long_pages=use_slicing
            )
            report = graphic_report(prepared)
            chapter = await _commit_graphic_chapter(
                book_id=book_id,
                title=clean_title,
                reading_mode=reading_mode,
                price_stars=max(0, min(100000, int(price_stars or 0))),
                source_filename=f"{len(saved)} изображений",
                prepared_pages=prepared,
                volume_number=max(1, min(10000, int(volume_number or 1))),
                volume_title=str(volume_title or "").strip()[:120],
                preview_pages=max(0, min(50, int(preview_pages or 0))),
            )
            await add_audit(
                user.app_user_id,
                "graphic_images_imported_web",
                "graphic_chapter",
                str(chapter["id"]),
                None,
                f"{report['pages_count']} pages",
            )
            workflow = None
            if settings.BOT_TOKEN:
                delivery_bot = Bot(token=settings.BOT_TOKEN)
                try:
                    result = await finish_book_content_workflow(
                        bot=delivery_bot,
                        book_id=book_id,
                        actor_user_id=user.app_user_id,
                        actor_telegram_id=user.telegram_id,
                        source="miniapp_graphic_images",
                    )
                    workflow = {
                        "status": result.workflow_status,
                        "channel_status": result.channel_status,
                        "channel_message": result.channel_message,
                        "review_reasons": result.duplicate_text,
                    }
                except Exception:
                    workflow = {"status": "saved", "channel_status": "", "channel_message": ""}
                finally:
                    await delivery_bot.session.close()
            notification = await _notify_graphic_chapter_if_published(
                book_id=book_id, chapter=chapter, actor_user_id=user.app_user_id
            )
            return {"ok": True, "chapter": chapter, "report": report, "workflow": workflow, "notification": notification}
        except GraphicImportError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            for upload in files:
                try:
                    await upload.close()
                except Exception:
                    pass
            shutil.rmtree(temp_dir, ignore_errors=True)

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
                updated_book = await get_book(book_id)
                await delivery_bot.send_message(
                    user.telegram_id,
                    message_text,
                    reply_markup=author_book_card_menu(
                        book_id, str(updated_book["publication_status"] if updated_book else "draft")
                    ),
                )
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
        if is_owner or "mod_books" in permissions:
            result["queues"]["graphic_page_reports"] = len(await list_graphic_page_reports("new", limit=500))
        if is_owner or "mod_comments" in permissions:
            result["queues"]["graphic_page_comments"] = len(await list_graphic_page_comments_for_moderation("pending", limit=500))
        if is_owner or "stats" in permissions:
            result["platform"] = await get_platform_stats()
            result["today"] = await get_owner_today_stats()
        if is_owner:
            result["premium"] = await get_premium_owner_summary()
        if is_owner or permissions.intersection({"view_finance", "refunds", "payouts"}):
            result["finance"] = await get_platform_finance_summary()
            rub = await get_rub_control_summary()
            result["rub_finance"] = rub
            if is_owner or "payouts" in permissions:
                result["queues"]["rub_profiles_pending"] = rub.get("profiles_pending", 0)
                result["queues"]["rub_payouts_new"] = rub.get("payouts_new", 0)
                result["queues"]["rub_payouts_processing"] = rub.get("payouts_processing", 0)
        return result

    @app.get("/api/control/access/users")
    async def api_control_access_users(
        q: str = "",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "grant_access")
        clean = str(q or "").strip()
        if len(clean) < 2:
            return {"ok": True, "items": []}
        rows = await search_users(clean, limit=20)
        items = []
        for row in rows:
            premium = await get_user_premium_status(int(row["id"]))
            items.append({
                "id": int(row["id"]),
                "telegram_id": int(row["telegram_id"]),
                "username": str(row["username"] or ""),
                "full_name": str(row["full_name"] or ""),
                "pen_name": str(row["pen_name"] or "") if "pen_name" in row.keys() else "",
                "is_blocked": bool(row["is_blocked"]),
                "premium": {"active": bool(premium.get("active")), "expires_at": str(premium.get("expires_at") or "")},
            })
        return {"ok": True, "items": items}

    @app.get("/api/control/access/books")
    async def api_control_access_books(
        q: str = "",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "grant_access")
        return {"ok": True, "items": _rows_to_dicts(await list_grantable_books(q, limit=60))}

    @app.post("/api/control/access/chapters/preview")
    async def api_control_access_chapters_preview(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "grant_access")
        try:
            user_id = int(payload.get("user_id") or 0)
            book_id = int(payload.get("book_id") or 0)
            selection = parse_chapter_selection(str(payload.get("chapter_spec") or ""))
        except (TypeError, ValueError, ChapterSelectionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = await get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        resolved = await resolve_chapters_by_numbers(book_id, selection.numbers)
        if not resolved.get("book"):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        return {
            "ok": True,
            "normalized": selection.normalized,
            "requested_count": len(selection.numbers),
            "found_count": len(resolved["chapters"]),
            "missing": resolved["missing"],
            "user": {"id": int(target["id"]), "telegram_id": int(target["telegram_id"]), "username": str(target["username"] or ""), "full_name": str(target["full_name"] or "")},
            "book": _row_to_dict(resolved["book"]),
            "chapters": _rows_to_dicts(resolved["chapters"][:200]),
        }

    @app.post("/api/control/access/chapters/grant")
    async def api_control_access_chapters_grant(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        actor, _, _ = await control_session(x_telegram_init_data, "grant_access")
        try:
            user_id = int(payload.get("user_id") or 0)
            book_id = int(payload.get("book_id") or 0)
            duration_raw = payload.get("duration_days")
            duration_days = None if duration_raw in {None, "", 0, "0"} else int(duration_raw)
            selection = parse_chapter_selection(str(payload.get("chapter_spec") or ""))
        except (TypeError, ValueError, ChapterSelectionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target = await get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        if bool(target["is_blocked"]):
            raise HTTPException(status_code=409, detail="Пользователь заблокирован. Сначала снимите блокировку.")
        resolved = await resolve_chapters_by_numbers(book_id, selection.numbers)
        if not resolved.get("book"):
            raise HTTPException(status_code=404, detail="Книга не найдена.")
        if resolved["missing"]:
            missing = ", ".join(map(str, resolved["missing"][:30]))
            raise HTTPException(status_code=400, detail=f"В книге нет глав: {missing}.")
        result = await grant_manual_chapter_access(
            user_id=user_id,
            book_id=book_id,
            chapter_ids=[int(row["id"]) for row in resolved["chapters"]],
            granted_by_user_id=actor.app_user_id,
            duration_days=duration_days,
            note=str(payload.get("note") or ""),
        )
        if not result.get("granted"):
            raise HTTPException(status_code=400, detail="Не удалось открыть выбранные главы.")
        expiry_text = "без ограничения срока" if not result.get("expires_at") else f"до {str(result['expires_at']).replace('T',' ')[:16]} UTC"
        book_title = str(resolved["book"]["title"] or "Книга")
        await notify_after_action(
            actor_user_id=actor.app_user_id,
            event="manual_chapters_granted",
            target_type="user",
            target_id=user_id,
            app_user_id=user_id,
            telegram_id=int(target["telegram_id"]),
            text=(f"🎟 <b>Вам открыт доступ к главам</b>\n\n"
                  f"Книга: <b>{book_title}</b>\n"
                  f"Главы: <b>{selection.normalized}</b>\n"
                  f"Срок: <b>{expiry_text}</b>\n\n"
                  "Откройте книгу в VoxLyra — доступ уже действует."),
        )
        await add_audit(actor.app_user_id, "manual_chapter_access_granted", "user", str(user_id), selection.normalized,
                        json.dumps({"book_id": book_id, "count": result["granted"], "expires_at": result.get("expires_at")}, ensure_ascii=False))
        return {"ok": True, "normalized": selection.normalized, **result}

    @app.post("/api/control/access/premium/grant")
    async def api_control_access_premium_grant(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        actor, _, _ = await control_session(x_telegram_init_data, "grant_access")
        try:
            user_id = int(payload.get("user_id") or 0)
            duration_days = int(payload.get("duration_days") or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Укажите срок Premium в днях.") from exc
        if duration_days < 1 or duration_days > 3650:
            raise HTTPException(status_code=400, detail="Срок Premium должен быть от 1 до 3650 дней.")
        target = await get_user_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        if bool(target["is_blocked"]):
            raise HTTPException(status_code=409, detail="Пользователь заблокирован. Сначала снимите блокировку.")
        result = await grant_premium_manually(
            user_id=user_id,
            duration_days=duration_days,
            granted_by_user_id=actor.app_user_id,
            note=str(payload.get("note") or ""),
        )
        expiry_text = str(result["expires_at"]).replace("T", " ")[:16] + " UTC"
        await notify_after_action(
            actor_user_id=actor.app_user_id,
            event="manual_premium_granted",
            target_type="user",
            target_id=user_id,
            app_user_id=user_id,
            telegram_id=int(target["telegram_id"]),
            text=(f"💎 <b>VoxLyra Premium активирован</b>\n\n"
                  f"Срок: <b>{duration_days} дн.</b>\n"
                  f"Действует до: <b>{expiry_text}</b>\n\n"
                  "Подписка выдана администрацией и уже доступна в Mini App."),
        )
        await add_audit(actor.app_user_id, "manual_premium_granted", "user", str(user_id), str(duration_days), str(result.get("expires_at") or ""))
        return {"ok": True, **result, "subscription": await get_user_premium_status(user_id)}

    @app.get("/api/control/access/grants")
    async def api_control_access_grants(
        user_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "grant_access")
        target = await get_user_by_id(int(user_id))
        if not target:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")
        return {
            "ok": True,
            "user": {"id": int(target["id"]), "telegram_id": int(target["telegram_id"]), "username": str(target["username"] or ""), "full_name": str(target["full_name"] or "")},
            "grants": await list_manual_access_grants(int(user_id)),
            "premium": await get_user_premium_status(int(user_id)),
        }

    @app.post("/api/control/access/revoke/{grant_type}/{grant_id}")
    async def api_control_access_revoke(
        grant_type: str,
        grant_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        actor, _, _ = await control_session(x_telegram_init_data, "grant_access")
        if grant_type not in {"chapter", "premium"}:
            raise HTTPException(status_code=404, detail="Выдача доступа не найдена.")
        if not await revoke_manual_access_grant(grant_type=grant_type, grant_id=grant_id, revoked_by_user_id=actor.app_user_id):
            raise HTTPException(status_code=404, detail="Активная выдача доступа не найдена.")
        await add_audit(actor.app_user_id, "manual_access_revoked", grant_type, str(grant_id))
        return {"ok": True}

    @app.get("/api/control/tts-diagnostics")
    async def api_control_tts_diagnostics(x_telegram_init_data: str | None = Header(default=None)):
        _, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Диагностика озвучивания доступна только владельцу.")
        manager: TTSSessionManager = app.state.tts_sessions
        statuses = await manager.queue.registry.statuses()
        snapshot = manager.queue.snapshot()
        client = await manager.client_diagnostics()
        return {
            "ok": True,
            "providers": [
                {
                    "name": item.name,
                    "available": bool(item.available),
                    "warmed": bool(item.warmed),
                    "message": item.message,
                    "details": dict(item.details),
                }
                for item in statuses
            ],
            "queue": {
                "queued": snapshot.queued,
                "running": snapshot.running,
                "completed": snapshot.completed,
                "failed": snapshot.failed,
                "deduplicated": snapshot.deduplicated,
                "workers": snapshot.workers,
            },
            "vosk_profile": get_vosk_voice_profile(),
            "provider_order": {
                "standard": [item.strip() for item in str(settings.TTS_PROVIDER_ORDER or "").split(",") if item.strip()],
                "high_quality": [item.strip() for item in str(settings.TTS_PROVIDER_ORDER_HQ or "").split(",") if item.strip()],
            },
            "player_contract_version": "v1.11.1-final-continuity-1",
            **client,
        }

    @app.post("/api/control/tts-vosk/benchmark")
    async def api_control_tts_vosk_benchmark(x_telegram_init_data: str | None = Header(default=None)):
        user, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Проверка голосов доступна только владельцу.")
        manager: TTSSessionManager = app.state.tts_sessions
        try:
            profile = await manager.queue.registry.benchmark_vosk(force=True)
        except TTSProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        await add_audit(user.app_user_id, "tts_vosk_benchmark", "setting", "tts", None, str(profile.get("source") or "automatic"))
        return {"ok": True, "profile": profile}

    @app.patch("/api/control/tts-vosk/selection")
    async def api_control_tts_vosk_selection(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Выбор голосов доступен только владельцу.")
        try:
            profile = set_vosk_voice_selection(str(payload.get("gender") or ""), int(payload.get("speaker_id")))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await add_audit(
            user.app_user_id, "tts_vosk_voice_selected", "setting", str(payload.get("gender") or ""),
            None, str(payload.get("speaker_id")),
        )
        return {"ok": True, "profile": profile}

    @app.get("/api/control/tts-vosk/sample/{speaker_id}")
    async def api_control_tts_vosk_sample(
        speaker_id: int,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        _, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Образцы голосов доступны только владельцу.")
        sample = vosk_sample_path(speaker_id)
        if sample is None:
            raise HTTPException(status_code=404, detail="Образец ещё не создан. Запустите проверку голосов.")
        response = FileResponse(sample, media_type="audio/mpeg", filename=f"voxlyra-vosk-speaker-{speaker_id}.mp3")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/control/payment-settings")
    async def api_control_payment_settings(x_telegram_init_data: str | None = Header(default=None)):
        user, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Настройки платежей доступны только владельцу.")
        return {"ok": True, "settings": await public_runtime_payment_settings()}

    @app.patch("/api/control/payment-settings")
    async def api_control_update_payment_settings(
        payload: dict[str, Any],
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Настройки платежей доступны только владельцу.")
        allowed = {
            "stars_enabled", "content_protection_enabled", "watermark_enabled",
            "buyer_star_rate_minor", "author_star_rate_minor", "purchase_cancel_minutes",
        }
        clean = {key: value for key, value in payload.items() if key in allowed}
        try:
            result = await update_runtime_payment_settings(clean)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await add_audit(user.app_user_id, "payment_settings_updated_web", "setting", "payments", None, ",".join(sorted(clean)))
        return {"ok": True, "settings": result}

    @app.post("/api/control/payment-settings/test/{kind}")
    async def api_control_test_payment_settings(
        kind: str,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, is_owner, _ = await control_session(x_telegram_init_data)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Проверка платежей доступна только владельцу.")
        try:
            if kind == "shop":
                await test_shop_connection()
                message = "ShopID и секретный ключ магазина подтверждены ЮKassa."
            elif kind == "payouts":
                banks = await list_sbp_banks()
                message = f"Ключи выплат подтверждены. Получено банков СБП: {len(banks)}."
            else:
                raise HTTPException(status_code=404, detail="Неизвестный вид проверки.")
        except (YooKassaCheckoutError, YooKassaPayoutError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        await add_audit(user.app_user_id, "payment_settings_tested", "setting", kind, None, "ok")
        return {"ok": True, "message": message}

    @app.get("/api/control/graphic-page-reports")
    async def api_control_graphic_page_reports(
        status: str = "new",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "mod_books")
        return {"ok": True, "status": status, "items": _rows_to_dicts(await list_graphic_page_reports(status, limit=200))}

    @app.post("/api/control/graphic-page/{graphic_page_id}/{action}")
    async def api_control_graphic_page_action(
        graphic_page_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "mod_books")
        if action not in {"approve", "reject", "pending"}:
            raise HTTPException(status_code=404, detail="Действие не найдено.")
        note = str((payload or {}).get("note") or "").strip()
        if action == "reject" and len(note) < 5:
            raise HTTPException(status_code=400, detail="Укажите причину отклонения страницы.")
        page_before = await get_graphic_page(graphic_page_id)
        ok = await moderate_graphic_page(graphic_page_id, user.app_user_id, decision=action, note=note)
        if not ok:
            raise HTTPException(status_code=404, detail="Страница не найдена.")
        await add_audit(user.app_user_id, f"graphic_page_{action}", "graphic_page", str(graphic_page_id), None, note)
        if action == "reject" and page_before and page_before["author_user_id"]:
            author_user = await get_user_by_id(int(page_before["author_user_id"]))
            await notify_after_action(
                actor_user_id=user.app_user_id,
                event="graphic_page_rejected",
                target_type="graphic_page",
                target_id=graphic_page_id,
                app_user_id=int(page_before["author_user_id"]),
                telegram_id=int(author_user["telegram_id"]) if author_user else None,
                text=(
                    f"<b>Страница графической главы скрыта после проверки</b>\n\n"
                    f"Глава: <b>{page_before['chapter_title']}</b>\n"
                    f"Страница: <b>{page_before['page_number']}</b>\n"
                    f"Причина: {note}\n\n"
                    "Замените или исправьте страницу в кабинете автора, затем сохраните изменения."
                ),
            )
        return {"ok": True}

    @app.post("/api/control/graphic-page-report/{report_id}/{action}")
    async def api_control_graphic_page_report_action(
        report_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "mod_books")
        status = {"pending": "pending", "close": "closed", "reject": "rejected"}.get(action)
        if not status:
            raise HTTPException(status_code=404, detail="Действие не найдено.")
        ok = await set_graphic_page_report_status(
            report_id, user.app_user_id, status, str((payload or {}).get("note") or "").strip()
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Жалоба не найдена.")
        return {"ok": True}

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
            "graphic_comments": _rows_to_dicts(await list_graphic_page_comments_for_moderation("pending", 100)),
        }

    @app.post("/api/control/graphic-comment/{comment_id}/{action}")
    async def api_control_graphic_comment(
        comment_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        user, _, _ = await control_session(x_telegram_init_data, "mod_comments")
        status = {"publish": "published", "hide": "hidden", "reject": "rejected"}.get(action)
        if not status:
            raise HTTPException(status_code=404, detail="Действие не найдено.")
        if not await set_graphic_page_comment_status(comment_id, user.app_user_id, status, str((payload or {}).get("note") or "")):
            raise HTTPException(status_code=404, detail="Комментарий не найден.")
        await add_audit(user.app_user_id, f"graphic_comment_{status}", "graphic_page_comment", str(comment_id))
        return {"ok": True}

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
            await resolve_comment_complaints(item_id, user.app_user_id)
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

    @app.get("/api/control/rub-profiles")
    async def api_control_rub_profiles(
        status: str = "pending",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "payouts")
        if status not in {"pending", "verified", "rejected", "blocked"}:
            status = "pending"
        rows = await list_author_financial_profiles(status, 100)
        items = []
        for row in rows:
            items.append({
                "id": int(row["id"]),
                "author_id": int(row["author_id"]),
                "pen_name": row["pen_name"],
                "telegram_id": row["telegram_id"],
                "username": row["username"],
                "full_name": row["full_name"],
                "legal_status": row["legal_status"],
                "legal_name": row["legal_name"],
                "inn": row["inn"],
                "ogrn": row["ogrn"],
                "country": row["country"],
                "sbp_phone_masked": mask_phone(decrypt_text(row["sbp_phone_encrypted"] or "")),
                "sbp_bank_id": row["sbp_bank_id"],
                "sbp_bank_name": row["sbp_bank_name"],
                "verification_status": row["verification_status"],
                "rejection_reason": row["rejection_reason"],
                "updated_at": row["updated_at"],
            })
        return {"ok": True, "items": items}

    @app.post("/api/control/rub-profile/{profile_id}/{action}")
    async def api_control_rub_profile_action(
        profile_id: int,
        action: str,
        payload: dict[str, Any] | None = None,
        x_telegram_init_data: str | None = Header(default=None),
    ):
        actor, _, _ = await control_session(x_telegram_init_data, "payouts")
        if action not in {"approve", "reject", "block"}:
            raise HTTPException(status_code=404, detail="Действие не найдено.")
        status = {"approve": "verified", "reject": "rejected", "block": "blocked"}[action]
        reason = str((payload or {}).get("reason") or "").strip()
        if status in {"rejected", "blocked"} and len(reason) < 8:
            raise HTTPException(status_code=400, detail="Укажите понятную причину не короче 8 символов.")
        if not await set_author_financial_profile_status(
            profile_id, status, actor_user_id=actor.app_user_id, reason=reason
        ):
            raise HTTPException(status_code=404, detail="Платёжный профиль не найден.")
        await add_audit(actor.app_user_id, f"author_financial_profile_{status}", "financial_profile", str(profile_id), None, reason)
        return {"ok": True, "status": status}

    @app.get("/api/control/rub-payouts")
    async def api_control_rub_payouts(
        status: str = "new",
        x_telegram_init_data: str | None = Header(default=None),
    ):
        await control_session(x_telegram_init_data, "payouts")
        if status not in {"new", "processing", "succeeded", "failed", "canceled"}:
            status = "new"
        rows = await list_author_rub_payout_requests(status, 100)
        items = [{
            "id": int(row["id"]),
            "author_id": int(row["author_id"]),
            "pen_name": row["pen_name"],
            "telegram_id": row["telegram_id"],
            "username": row["username"],
            "full_name": row["full_name"],
            "amount_minor": int(row["amount_minor"]),
            "currency": row["currency"],
            "bank_name": row["bank_name"],
            "provider_payout_id": row["provider_payout_id"],
            "status": row["status"],
            "failure_reason": row["failure_reason"],
            "requested_at": row["requested_at"],
            "paid_at": row["paid_at"],
        } for row in rows]
        return {"ok": True, "items": items, "provider_ready": await payouts_configured()}

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
