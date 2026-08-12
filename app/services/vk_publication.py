from __future__ import annotations

import html
import logging
from dataclasses import dataclass

from app.config import settings
from app.db import add_audit, connect, count_chapters_for_book, get_book, get_book_options
from app.services.vk_api import vk_api_call, vk_app_url

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VKWallPublicationResult:
    status: str
    post_id: int | None = None
    error: str = ""

    @property
    def sent(self) -> bool:
        return self.status == "sent"


def vk_book_url(book_id: int) -> str:
    """Return a VK Mini App deep link that opens the shared book route."""
    book_id = int(book_id)
    if book_id <= 0:
        return ""
    return vk_app_url(f"book_{book_id}")


async def _was_vk_wall_post_sent(book_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1
            FROM audit_logs
            WHERE target_type='book' AND target_id=? AND action='vk_wall_post_sent'
            LIMIT 1
            """,
            (str(int(book_id)),),
        )
        return await cur.fetchone() is not None


def _wall_text(*, title: str, author: str, description: str, genres: list[str], chapters: int, url: str) -> str:
    safe_title = " ".join(str(title or "Книга").split())[:180]
    safe_author = " ".join(str(author or "Автор не указан").split())[:140]
    safe_description = " ".join(str(description or "").split())[:900]
    safe_genres = [" ".join(str(item or "").split())[:60] for item in genres if str(item or "").strip()][:8]
    lines = [f"📚 {safe_title}", f"✍ {safe_author}"]
    if safe_genres:
        lines.append("🏷 " + " · ".join(safe_genres))
    if int(chapters or 0) > 0:
        lines.append(f"📖 Глав: {int(chapters)}")
    if safe_description:
        lines.extend(["", safe_description])
    if url:
        lines.extend(["", f"Открыть в VK Mini App: {url}"])
    return "\n".join(lines)[:15000]


async def post_book_to_vk_wall(
    book_id: int,
    *,
    actor_user_id: int | None = None,
    force: bool = False,
) -> VKWallPublicationResult:
    """Publish one newly released book to the configured VK community wall.

    This is intentionally independent of the upload source: Telegram, VK,
    Library Manager and GitHub imports all converge on the same publication
    workflow. The audit row makes retries idempotent.
    """
    book_id = int(book_id)
    if book_id <= 0:
        return VKWallPublicationResult("failed", error="Некорректный ID книги")
    if not bool(settings.VK_ENABLED):
        return VKWallPublicationResult("disabled")
    group_id = int(settings.VK_GROUP_ID or 0)
    token = str(settings.VK_GROUP_TOKEN or "").strip()
    app_id = int(settings.VK_APP_ID or 0)
    if group_id <= 0 or not token or app_id <= 0:
        await add_audit(
            actor_user_id,
            "vk_wall_post_skipped",
            "book",
            str(book_id),
            "VK_GROUP_ID/VK_GROUP_TOKEN/VK_APP_ID not configured",
            "not_configured",
        )
        return VKWallPublicationResult("not_configured")

    book = await get_book(book_id)
    if not book or str(book["publication_status"] or "") != "published":
        return VKWallPublicationResult("failed", error="Книга ещё не опубликована")
    if not force and await _was_vk_wall_post_sent(book_id):
        return VKWallPublicationResult("already_sent")

    options = await get_book_options(book_id)
    raw_genres = list(options.get("genres") or [])
    genres: list[str] = []
    for item in raw_genres:
        if isinstance(item, dict):
            genres.append(str(item.get("label") or item.get("name") or item.get("code") or ""))
        else:
            genres.append(str(item))
    url = vk_book_url(book_id)
    message = _wall_text(
        title=str(book["title"] or "Книга"),
        author=str(book["pen_name"] or "Автор не указан"),
        description=str(book["description"] or ""),
        genres=genres,
        chapters=await count_chapters_for_book(book_id),
        url=url,
    )
    params = {
        "owner_id": -abs(group_id),
        "from_group": 1,
        "message": message,
    }
    try:
        response = await vk_api_call("wall.post", params, token=token)
        post_id = int((response or {}).get("post_id") or 0) if isinstance(response, dict) else int(response or 0)
        await add_audit(
            actor_user_id,
            "vk_wall_post_sent",
            "book",
            str(book_id),
            None,
            f"post_id={post_id};app_id={app_id}",
        )
        return VKWallPublicationResult("sent", post_id=post_id or None)
    except Exception as exc:
        safe_error = str(exc)[:1000]
        await add_audit(
            actor_user_id,
            "vk_wall_post_failed",
            "book",
            str(book_id),
            safe_error,
            "failed",
        )
        logger.warning("VK wall publication failed for book %s: %s", book_id, exc)
        return VKWallPublicationResult("failed", error=safe_error)
