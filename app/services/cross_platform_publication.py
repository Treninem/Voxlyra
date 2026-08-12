from __future__ import annotations

import mimetypes
from pathlib import Path

from app.config import settings
from app.db import add_audit, connect, count_chapters_for_book, get_book, get_book_options
from app.services.cover_storage import ensure_book_cover_file
from app.services.vk_api import vk_api_call, vk_app_url


def vk_book_url(book_id: int) -> str:
    """Open the same catalogue book inside VK Mini App."""
    book_id = int(book_id)
    if book_id <= 0:
        return ""
    return vk_app_url(f"book_{book_id}")


def vk_votes_from_stars(stars: int) -> int:
    """Convert the canonical catalogue price for VK presentation only."""
    stars = max(0, int(stars or 0))
    if not stars:
        return 0
    rate = max(0.01, float(settings.VK_VOTES_PER_STAR or 1.0))
    return max(1, int(round(stars * rate)))


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


async def _was_vk_wall_post_sent(book_id: int) -> bool:
    """Use the same audit table as Telegram publication for idempotency."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1
            FROM audit_logs
            WHERE action='vk_wall_post_sent' AND target_type='book' AND target_id=?
            LIMIT 1
            """,
            (str(int(book_id)),),
        )
        return await cur.fetchone() is not None


async def _vk_upload_wall_cover(path: Path) -> str:
    group_id = int(settings.VK_GROUP_ID or 0)
    upload = await vk_api_call(
        "photos.getWallUploadServer",
        {"group_id": group_id},
        token=settings.VK_GROUP_TOKEN,
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
        token=settings.VK_GROUP_TOKEN,
    )
    if not saved:
        return ""
    photo = saved[0]
    return f"photo{int(photo['owner_id'])}_{int(photo['id'])}"


async def post_book_to_vk_wall(
    book_id: int,
    *,
    actor_user_id: int | None,
    force: bool = False,
) -> str:
    """Publish a catalogue book on the VK community wall with VK-native link/currency.

    The caller is the shared first-publication workflow, so the upload origin does
    not matter: Telegram, VK, owner import, Library Manager and GitHub converge on
    this function. Failures are audited but never roll back an approved book.
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
        if cover and cover.is_file():
            attachment = await _vk_upload_wall_cover(cover)
            if attachment:
                params["attachments"] = attachment
    except Exception as exc:
        await add_audit(
            actor_user_id,
            "vk_wall_cover_failed",
            "book",
            str(book_id),
            str(exc)[:1000],
            "cover_optional",
        )
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
