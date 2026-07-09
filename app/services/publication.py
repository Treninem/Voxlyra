from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.db import (
    add_audit,
    count_chapters_for_book,
    get_book,
    get_book_options,
    publish_book_content,
    set_book_publication_status,
    submit_book_for_review,
    was_channel_post_sent,
)
from app.services.channel import build_new_book_post
from app.services.duplicate_books import duplicate_warning_text, find_book_duplicates


@dataclass(slots=True)
class PublicationResult:
    published: bool
    channel_status: str
    channel_error: str = ""
    workflow_status: str = ""
    duplicate_text: str = ""

    @property
    def channel_message(self) -> str:
        return {
            "sent": "Пост с книгой опубликован в канале.",
            "already_sent": "Пост с этой книгой уже был опубликован в канале.",
            "not_configured": "Канал не подключён: укажите CHANNEL_ID.",
            "failed": "Книга опубликована в каталоге, но пост в канал отправить не удалось.",
            "duplicate": "Публикация остановлена: найдена возможная копия книги.",
        }.get(self.channel_status, "")


def _book_link(book_id: int) -> str:
    web_url = settings.WEBAPP_URL.strip().rstrip("/")
    if web_url:
        return f"{web_url}/book/{int(book_id)}"
    username = settings.BOT_USERNAME.strip().lstrip("@")
    if username:
        return f"https://t.me/{username}?start=book_{int(book_id)}"
    return ""


def _channel_markup(book_id: int) -> InlineKeyboardMarkup | None:
    url = _book_link(book_id)
    if not url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📖 Открыть книгу", url=url)]]
    )


async def post_book_to_channel(
    bot: Bot,
    book_id: int,
    *,
    actor_user_id: int | None,
    force: bool = False,
) -> PublicationResult:
    """Публикует карточку книги в подключённый канал и явно фиксирует результат."""
    book = await get_book(book_id)
    if not book or book["publication_status"] != "published":
        return PublicationResult(False, "failed", "Книга ещё не опубликована")

    if not settings.CHANNEL_ID.strip():
        await add_audit(
            actor_user_id,
            "channel_post_skipped",
            "book",
            str(book_id),
            "CHANNEL_ID empty",
            "not_configured",
        )
        return PublicationResult(True, "not_configured", workflow_status="published")

    if not force and await was_channel_post_sent(book_id):
        return PublicationResult(True, "already_sent", workflow_status="published")

    options = await get_book_options(book_id)
    genres = options.get("genres") or []
    chapters_count = await count_chapters_for_book(book_id)
    book_url = _book_link(book_id)
    post = build_new_book_post(
        title=str(book["title"]),
        author=str(book["pen_name"] or "Автор не указан"),
        genres=genres,
        age_limit=str(book["age_limit"] or ""),
        chapters_count=int(chapters_count),
        has_audio=bool(book["has_audio"]),
        description=str(book["description"] or ""),
        pricing_type=str(book["pricing_type"] or "free"),
        price_stars=int(book["price_stars"] or 0),
        book_url=book_url,
        repeated=bool(force),
    )
    markup = _channel_markup(book_id)

    try:
        cover_path = Path(str(book["cover_path"] or ""))
        if cover_path.is_file():
            await bot.send_photo(
                settings.CHANNEL_ID.strip(),
                FSInputFile(cover_path),
                caption=post,
                reply_markup=markup,
            )
        else:
            await bot.send_message(
                settings.CHANNEL_ID.strip(),
                post,
                reply_markup=markup,
            )
        await add_audit(
            actor_user_id,
            "channel_post_sent",
            "book",
            str(book_id),
            None,
            "sent",
        )
        return PublicationResult(True, "sent", workflow_status="published")
    except Exception as exc:  # Telegram returns several exception classes here.
        error = f"{type(exc).__name__}: {exc}"[:1000]
        await add_audit(
            actor_user_id,
            "channel_post_failed",
            "book",
            str(book_id),
            error,
            "failed",
        )
        return PublicationResult(True, "failed", error, workflow_status="published")


async def _duplicate_guard(book_id: int) -> str:
    book = await get_book(book_id)
    if not book or bool(book["duplicate_override"]):
        return ""
    matches = await find_book_duplicates(
        title=book["title"],
        author_id=int(book["author_id"]) if book["author_id"] is not None else None,
        exclude_book_id=book_id,
        source_file_hash=str(book["source_file_hash"] or ""),
    )
    if not matches:
        return ""
    return duplicate_warning_text(matches)


async def publish_book_and_channel(
    bot: Bot,
    book_id: int,
    *,
    actor_user_id: int | None,
    force_channel: bool = False,
    bypass_duplicate_guard: bool = False,
) -> PublicationResult:
    """Единая публикация: проверка копий, статус книги, содержимое, затем пост в канал."""
    book = await get_book(book_id)
    if not book:
        return PublicationResult(False, "failed", "Книга не найдена")
    if await count_chapters_for_book(book_id) < 1:
        return PublicationResult(False, "failed", "Нельзя публиковать книгу без глав")

    if not bypass_duplicate_guard:
        duplicate_text = await _duplicate_guard(book_id)
        if duplicate_text:
            await add_audit(
                actor_user_id,
                "book_publication_duplicate_blocked",
                "book",
                str(book_id),
                None,
                duplicate_text[:1000],
            )
            return PublicationResult(
                False,
                "duplicate",
                "Найдена возможная копия книги",
                workflow_status="duplicate",
                duplicate_text=duplicate_text,
            )

    await set_book_publication_status(book_id, "published")
    await publish_book_content(book_id)
    await add_audit(actor_user_id, "book_published", "book", str(book_id), None, "published")
    return await post_book_to_channel(
        bot,
        book_id,
        actor_user_id=actor_user_id,
        force=force_channel,
    )


async def finish_book_content_workflow(
    *,
    bot: Bot,
    book_id: int,
    actor_user_id: int,
    actor_telegram_id: int,
    source: str,
) -> PublicationResult:
    """Одинаково завершает загрузку из бота, Mini App и ручного редактора."""
    book = await get_book(book_id)
    if not book:
        return PublicationResult(False, "failed", "Книга не найдена", workflow_status="failed")

    if book["publication_status"] == "published":
        await publish_book_content(book_id)
        return PublicationResult(True, "already_sent", workflow_status="published")

    if int(actor_telegram_id) in settings.owner_ids:
        result = await publish_book_and_channel(
            bot,
            book_id,
            actor_user_id=actor_user_id,
        )
        await add_audit(
            actor_user_id,
            "owner_content_workflow_finished",
            "book",
            str(book_id),
            source,
            result.workflow_status or result.channel_status,
        )
        return result

    duplicate_text = await _duplicate_guard(book_id)
    if duplicate_text:
        return PublicationResult(
            False,
            "duplicate",
            "Найдена возможная копия книги",
            workflow_status="duplicate",
            duplicate_text=duplicate_text,
        )

    if book["publication_status"] != "review":
        submitted = await submit_book_for_review(book_id, actor_user_id)
        if submitted:
            await add_audit(
                actor_user_id,
                "book_submitted_after_content",
                "book",
                str(book_id),
                source,
                "review",
            )
            return PublicationResult(False, "", workflow_status="review")
    return PublicationResult(False, "", workflow_status="review")
