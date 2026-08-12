from __future__ import annotations

import html
import json
from pathlib import Path

from aiogram import Bot

from app.config import settings
from app.db import add_audit, count_chapters_for_book, get_book, get_book_options
from app.services.cover_storage import ensure_book_cover_file
from app.services.vk_api import vk_api_call, vk_app_url


def vk_book_url(book_id: int) -> str:
    """Open the same catalogue book inside VK Mini App."""
    return vk_app_url(f"book_{int(book_id)}")


def vk_votes_from_stars(stars: int) -> int:
    """Platform presentation conversion; VK users never see Telegram Stars."""
    stars = max(0, int(stars or 0))
    if not stars:
        return 0
    rate = max(0.01, float(settings.VK_VOTES_PER_STAR or 1.0))
    return max(1, int(round(stars * rate)))


def build_vk_book_post(*, title: str, author: str, genres: list[str], age_limit: str,
                       chapters_count: int, has_audio: bool, description: str,
                       pricing_type: str, price_stars: int, book_url: str) -> str:
    clean = lambda value: " ".join(str(value or "").split())
    short = clean(description)
    if len(short) > 420:
        short = short[:419].rstrip(" ,.;:-") + "…"
    votes = vk_votes_from_stars(price_stars)
    if votes:
        price = f"Вся книга: {votes} голосов VK"
    elif str(pricing_type or "free") == "chapters":
        price = "Есть платные главы — оплата голосами VK"
    else:
        price = "Бесплатно"
    genre_text = ", ".join(clean(x) for x in (genres or [])[:3] if clean(x)) or "Истории"
    lines = [
        "✨ Новая книга на Вокслире", "", f"📖 {clean(title) or 'Новая книга'}",
        f"✍️ {clean(author) or 'Автор не указан'}", "", f"🏷 {genre_text}",
        f"🔞 {clean(age_limit) or '16+'} · 📚 {int(chapters_count)} глав", f"💎 {price}",
    ]
    if has_audio:
        lines.append("🎧 Есть аудиоверсия")
    if short:
        lines += ["", short]
    if book_url:
        lines += ["", f"📖 Открыть в VK Mini App: {book_url}"]
    return "\n".join(lines)[:15000]


async def _vk_upload_wall_cover(path: Path) -> str:
    group_id = int(settings.VK_GROUP_ID or 0)
    upload = await vk_api_call("photos.getWallUploadServer", {"group_id": group_id}, token=settings.VK_GROUP_TOKEN)
    import httpx
    async with httpx.AsyncClient(timeout=30.0) as client:
        with path.open("rb") as fh:
            response = await client.post(str(upload["upload_url"]), files={"photo": (path.name, fh, "image/jpeg")})
        response.raise_for_status()
        payload = response.json()
    saved = await vk_api_call("photos.saveWallPhoto", {
        "group_id": group_id, "photo": payload["photo"], "server": payload["server"], "hash": payload["hash"]
    }, token=settings.VK_GROUP_TOKEN)
    if not saved:
        return ""
    photo = saved[0]
    return f"photo{int(photo['owner_id'])}_{int(photo['id'])}"


async def post_book_to_vk_wall(book_id: int, *, actor_user_id: int | None, force: bool = False) -> str:
    """Publish a catalogue book on the VK community wall with a VK-native link/currency."""
    if not settings.VK_ENABLED or not settings.VK_GROUP_TOKEN or int(settings.VK_GROUP_ID or 0) <= 0:
        return "not_configured"
    book = await get_book(int(book_id))
    if not book or str(book["publication_status"] or "") != "published":
        return "failed"
    # Separate idempotency from Telegram channel publication.
    from app.db import connect
    async with connect() as db:
        cur = await db.execute(
            "SELECT 1 FROM audit_log WHERE action='vk_wall_post_sent' AND entity_type='book' AND entity_id=? LIMIT 1",
            (str(int(book_id)),),
        )
        already = await cur.fetchone()
    if already and not force:
        return "already_sent"

    options = await get_book_options(int(book_id))
    url = vk_book_url(int(book_id))
    message = build_vk_book_post(
        title=str(book["title"] or ""), author=str(book["pen_name"] or "Автор не указан"),
        genres=list(options.get("genres") or []), age_limit=str(book["age_limit"] or ""),
        chapters_count=await count_chapters_for_book(int(book_id)), has_audio=bool(book["has_audio"]),
        description=str(book["description"] or ""), pricing_type=str(book["pricing_type"] or "free"),
        price_stars=int(book["price_stars"] or 0), book_url=url,
    )
    params = {"owner_id": -abs(int(settings.VK_GROUP_ID)), "from_group": 1, "message": message}
    try:
        cover = await ensure_book_cover_file(
            book_id=int(book_id), cover_file_id=str(book["cover_file_id"] or ""),
            cover_path=str(book["cover_path"] or ""), bot=None,
        )
        if cover and cover.is_file():
            attachment = await _vk_upload_wall_cover(cover)
            if attachment:
                params["attachments"] = attachment
    except Exception as exc:
        await add_audit(actor_user_id, "vk_wall_cover_failed", "book", str(book_id), str(exc)[:1000], "cover_optional")
    try:
        response = await vk_api_call("wall.post", params, token=settings.VK_GROUP_TOKEN)
        post_id = int((response or {}).get("post_id") or 0)
        await add_audit(actor_user_id, "vk_wall_post_sent", "book", str(book_id), None, f"post_id={post_id}")
        return "sent"
    except Exception as exc:
        await add_audit(actor_user_id, "vk_wall_post_failed", "book", str(book_id), str(exc)[:1000], "failed")
        return "failed"


async def post_book_everywhere(bot: Bot, book_id: int, *, actor_user_id: int | None, force: bool = False):
    """One publication event fans out to every configured platform, regardless of upload source."""
    from app.services.publication import post_book_to_channel
    telegram = await post_book_to_channel(bot, int(book_id), actor_user_id=actor_user_id, force=force)
    vk = await post_book_to_vk_wall(int(book_id), actor_user_id=actor_user_id, force=force)
    await add_audit(actor_user_id, "cross_platform_publication", "book", str(book_id), None,
                    json.dumps({"telegram": telegram.channel_status, "vk": vk}, ensure_ascii=False))
    return telegram
