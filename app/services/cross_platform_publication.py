from __future__ import annotations

import mimetypes
from pathlib import Path

from app.config import settings
from app.db import add_audit, connect, count_chapters_for_book, get_book, get_book_options
from app.services.cover_storage import ensure_book_cover_file
from app.services.vk_api import vk_api_call, vk_app_url
from app.services.vk_payments import votes_for_stars


def vk_book_url(book_id: int) -> str:
    """Open the same catalogue book inside VK Mini App."""
    book_id = int(book_id)
    if book_id <= 0:
        return ""
    return vk_app_url(f"book_{book_id}")


def vk_votes_from_stars(stars: int) -> int:
    """Use the exact checkout conversion for VK-facing publication prices."""
    stars = max(0, int(stars or 0))
    return votes_for_stars(stars) if stars else 0


def build_vk_book_post(
    *,
    title: str,
    author: str,
    genres: list[str],
    age_limit: str,
    chapters_count: int,
    has_audio: bool,
    description: str,
    pricing_type: str,
    price_stars: int,
    book_url: str,
) -> str:
    clean = lambda value: " ".join(str(value or "").split())
    short = clean(description)
    if len(short) > 420:
        short = short[:419].rstrip(" ,.;:-") + "…"
    pricing = str(pricing_type or "free").strip().lower()
    votes = vk_votes_from_stars(price_stars)
    if pricing in {"chapters", "chapter", "per_chapter"}:
        price = "Есть платные главы — оплата голосами VK"
    elif votes:
        price = f"Вся книга: {votes} голосов VK"
    else:
        price = "Бесплатно"
    genre_text = ", ".join(clean(x) for x in (genres or [])[:3] if clean(x)) or "Истории"
    lines = [
        "✨ Новая книга на Вокслире",
        "",
        f"📖 {clean(title) or 'Новая книга'}",
        f"✍️ {clean(author) or 'Автор не указан'}",
        "",
        f"🏷 {genre_text}",
        f"🔞 {clean(age_limit) or '16+'} · 📚 {int(chapters_count)} глав",
        f"💎 {price}",
    ]
    if has_audio:
        lines.append("🎧 Есть аудиоверсия")
    if short:
        lines += ["", short]
    if book_url:
        lines += ["", f"📖 Открыть в VK Mini App: {book_url}"]
    return "\n".join(lines)[:15000]


async def _vk_wall_post_state(book_id: int) -> str:
    """Return the latest terminal VK wall state recorded for one book."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT action
            FROM audit_logs
            WHERE target_type='book' AND target_id=?
              AND action IN ('vk_wall_post_sent','vk_wall_post_failed')
            ORDER BY id DESC
            LIMIT 1
            """,
            (str(int(book_id)),),
        )
        row = await cur.fetchone()
    return str(row["action"] if row else "")


async def _was_vk_wall_post_sent(book_id: int) -> bool:
    """Use the same audit table as Telegram publication for idempotency."""
    return await _vk_wall_post_state(book_id) == "vk_wall_post_sent"


async def should_retry_vk_wall_post(book_id: int) -> bool:
    """Retry only a known failed first-publication attempt.

    Existing books that predate VK integration have no VK audit state and are
    intentionally left untouched, preventing a later edit from flooding the VK
    wall with the entire historical catalogue. A book whose first VK post really
    failed gets one new attempt when it next passes through publication workflow.
    """
    return await _vk_wall_post_state(book_id) == "vk_wall_post_failed"


def _vk_media_token() -> str:
    """Return the user token used only for VK photo upload.

    VK community tokens can publish wall text but photos.getWallUploadServer may
    reject group authorization with error 27. A dedicated user token is therefore
    supported through VK_MEDIA_TOKEN. Group token remains a compatibility fallback
    for deployments where VK still permits group-authenticated photo upload.
    """
    return str(settings.VK_MEDIA_TOKEN or settings.VK_GROUP_TOKEN or "").strip()


async def _vk_upload_wall_cover(path: Path) -> str:
    group_id = int(settings.VK_GROUP_ID or 0)
    media_token = _vk_media_token()
    if not media_token:
        raise RuntimeError("VK media token is not configured")
    upload = await vk_api_call(
        "photos.getWallUploadServer",
        {"group_id": group_id},
        token=media_token,
    )
    import httpx

    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    async with httpx.AsyncClient(timeout=30.0) as client:
        with path.open("rb") as fh:
            response = await client.post(
                str(upload["upload_url"]),
                files={"photo": (path.name, fh, content_type)},
            )
        response.raise_for_status()
        payload = response.json()
    saved = await vk_api_call(
        "photos.saveWallPhoto",
        {
            "group_id": group_id,
            "photo": payload["photo"],
            "server": payload["server"],
            "hash": payload["hash"],
        },
        token=media_token,
    )
    if not saved:
        return ""
    photo = saved[0]
    return f"photo{int(photo['owner_id'])}_{int(photo['id'])}"


async def _record_vk_cover_failure(
    book_id: int,
    actor_user_id: int | None,
    error: Exception | str,
) -> None:
    """Record a terminal VK publication failure when the required cover is unavailable."""
    detail = str(error)[:1000]
    await add_audit(
        actor_user_id,
        "vk_wall_cover_failed",
        "book",
        str(int(book_id)),
        detail,
        "cover_required",
    )
    await add_audit(
        actor_user_id,
        "vk_wall_post_failed",
        "book",
        str(int(book_id)),
        detail,
        "cover_required",
    )


async def post_book_to_vk_wall(
    book_id: int,
    *,
    actor_user_id: int | None,
    force: bool = False,
) -> str:
    """Publish a catalogue book on the VK community wall with VK-native link/currency.

    The caller is the shared publication workflow, so the upload origin does not
    matter: Telegram, VK, owner import, Library Manager and GitHub converge here.
    A visible cover is mandatory: a failed cover lookup/upload is audited as a
    failed VK publication and wall.post is not called, preventing text-only posts.
    """
    book_id = int(book_id)
    if (
        not settings.VK_ENABLED
        or not settings.VK_GROUP_TOKEN
        or int(settings.VK_GROUP_ID or 0) <= 0
        or int(settings.VK_APP_ID or 0) <= 0
    ):
        return "not_configured"
    book = await get_book(book_id)
    if not book or str(book["publication_status"] or "") != "published":
        return "failed"
    if not force and await _was_vk_wall_post_sent(book_id):
        return "already_sent"

    options = await get_book_options(book_id)
    url = vk_book_url(book_id)
    message = build_vk_book_post(
        title=str(book["title"] or ""),
        author=str(book["pen_name"] or "Автор не указан"),
        genres=list(options.get("genres") or []),
        age_limit=str(book["age_limit"] or ""),
        chapters_count=await count_chapters_for_book(book_id),
        has_audio=bool(book["has_audio"]),
        description=str(book["description"] or ""),
        pricing_type=str(book["pricing_type"] or "free"),
        price_stars=int(book["price_stars"] or 0),
        book_url=url,
    )
    params = {
        "owner_id": -abs(int(settings.VK_GROUP_ID)),
        "from_group": 1,
        "message": message,
    }

    try:
        cover = await ensure_book_cover_file(
            book_id=book_id,
            cover_file_id=str(book["cover_file_id"] or ""),
            cover_path=str(book["cover_path"] or ""),
            bot=None,
        )
        if not cover or not cover.is_file():
            raise RuntimeError("Book cover file is unavailable")
        attachment = await _vk_upload_wall_cover(cover)
        if not attachment:
            raise RuntimeError("VK did not return a saved wall photo")
        params["attachments"] = attachment
    except Exception as exc:
        await _record_vk_cover_failure(book_id, actor_user_id, exc)
        return "failed"

    try:
        response = await vk_api_call("wall.post", params, token=settings.VK_GROUP_TOKEN)
        post_id = int((response or {}).get("post_id") or 0) if isinstance(response, dict) else int(response or 0)
        await add_audit(
            actor_user_id,
            "vk_wall_post_sent",
            "book",
            str(book_id),
            None,
            f"post_id={post_id};app_id={int(settings.VK_APP_ID)}",
        )
        return "sent"
    except Exception as exc:
        await add_audit(
            actor_user_id,
            "vk_wall_post_failed",
            "book",
            str(book_id),
            str(exc)[:1000],
            "failed",
        )
        return "failed"
