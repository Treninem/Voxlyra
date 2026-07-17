from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.db import (
    add_audit,
    count_chapters_for_book,
    get_book,
    get_book_options,
    publish_book_content,
    enqueue_book_moderation,
    resolve_book_moderation,
    set_book_publication_status,
    submit_book_for_review,
    was_channel_post_sent,
)
from app.services.channel import build_new_book_post
from app.services.duplicate_books import duplicate_warning_text, find_book_duplicates
from app.services.cover_storage import ensure_book_cover_file
from app.services.automatic_moderation import evaluate_book_for_auto_publication
from app.services.moderation_alerts import notify_book_needs_moderation


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
            "queued": "Карточка книги поставлена в очередь канала и выйдет по расписанию.",
        }.get(self.channel_status, "")


def _book_link(book_id: int) -> str:
    """Ссылка из канала сразу запускает Main Mini App на нужной книге."""
    username = settings.BOT_USERNAME.strip().lstrip("@")
    if username:
        return f"https://t.me/{username}?startapp=book_{int(book_id)}"
    web_url = settings.WEBAPP_URL.strip().rstrip("/")
    if web_url:
        return f"{web_url}/book/{int(book_id)}"
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

    channel_id = settings.CHANNEL_ID.strip()
    cover_errors: list[str] = []
    sent_with_cover = False

    # Сначала используем Telegram file_id. Это самый надёжный путь после Redeploy:
    # изображение не зависит от локальной папки storage и не требует повторной загрузки.
    cover_file_id = str(book["cover_file_id"] or "").strip()
    if cover_file_id:
        try:
            await bot.send_photo(
                channel_id,
                cover_file_id,
                caption=post,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
            sent_with_cover = True
        except Exception as exc:
            cover_errors.append(f"file_id {type(exc).__name__}: {exc}")

    # Если Telegram file_id устарел или недоступен, восстанавливаем локальный файл
    # и повторяем отправку уже как загружаемое изображение.
    if not sent_with_cover:
        try:
            cover_path = await ensure_book_cover_file(
                book_id=book_id,
                cover_file_id=cover_file_id,
                cover_path=str(book["cover_path"] or ""),
                bot=bot,
            )
            if cover_path and cover_path.is_file():
                await bot.send_photo(
                    channel_id,
                    FSInputFile(cover_path),
                    caption=post,
                    reply_markup=markup,
                    parse_mode=ParseMode.HTML,
                )
                sent_with_cover = True
        except Exception as exc:
            cover_errors.append(f"local {type(exc).__name__}: {exc}")

    try:
        if not sent_with_cover:
            # Книга всё равно не теряется из канала. Причина отсутствия обложки
            # сохраняется только в закрытом журнале владельца.
            await bot.send_message(
                channel_id,
                post,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
            )
        detail = "sent_with_cover" if sent_with_cover else "sent_without_cover"
        error_text = " | ".join(cover_errors)[:1000]
        await add_audit(
            actor_user_id,
            "channel_post_sent",
            "book",
            str(book_id),
            error_text or None,
            detail,
        )
        return PublicationResult(
            True,
            "sent",
            error_text if not sent_with_cover else "",
            workflow_status="published",
        )
    except Exception as exc:  # Telegram returns several exception classes here.
        errors = cover_errors + [f"post {type(exc).__name__}: {exc}"]
        error = " | ".join(errors)[:1000]
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
    # Импортированные книги ставит в очередь Library Manager. Обычные авторские
    # книги идут в отдельную справедливую очередь, чтобы сотни публикаций не спамили канал.
    fresh = await get_book(book_id)
    if fresh and fresh["author_id"] is not None and fresh["import_batch_id"] is None and not force_channel:
        from app.services.author_channel_queue import enqueue_author_channel_post
        await enqueue_author_channel_post(book_id, author_id=int(fresh["author_id"]), actor_user_id=actor_user_id)
        return PublicationResult(True, "queued", workflow_status="published")
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
    """Завершает любой путь загрузки одной безопасной логикой публикации."""
    book = await get_book(book_id)
    if not book:
        return PublicationResult(False, "failed", "Книга не найдена", workflow_status="failed")

    if book["publication_status"] == "published":
        # Изменения текста опубликованной книги проходят автоматическую проверку.
        # Безопасные правки публикуются без участия модератора; сомнительные остаются
        # черновиком и создают одно объединённое задание проверки.
        check = await evaluate_book_for_auto_publication(
            book_id, actor_telegram_id=actor_telegram_id, revision_mode=True
        )
        if check.auto_publish or not check.reasons:
            await publish_book_content(book_id)
            await resolve_book_moderation(
                book_id, resolution="revision_auto_approved", actor_user_id=actor_user_id,
                note="Изменение текста прошло автоматическую проверку",
            )
            await add_audit(actor_user_id, "book_revision_auto_approved", "book", str(book_id), source, "published")
            return PublicationResult(True, "already_sent", workflow_status="published")
        await enqueue_book_moderation(book_id, check.reasons, risk_level="revision")
        await notify_book_needs_moderation(bot, book_id=book_id, reasons=check.reasons, reminder=False)
        await add_audit(actor_user_id, "book_revision_manual_review", "book", str(book_id), source, " | ".join(check.reasons)[:1000])
        return PublicationResult(False, "", workflow_status="review", duplicate_text="\n".join(check.reasons))

    check = await evaluate_book_for_auto_publication(
        book_id,
        actor_telegram_id=actor_telegram_id,
    )
    if check.auto_publish:
        result = await publish_book_and_channel(
            bot,
            book_id,
            actor_user_id=actor_user_id,
        )
        if result.published:
            await resolve_book_moderation(
                book_id,
                resolution="published",
                actor_user_id=actor_user_id,
                note="Автоматическая проверка пройдена",
            )
        await add_audit(
            actor_user_id,
            "book_auto_moderation_passed",
            "book",
            str(book_id),
            source,
            result.workflow_status or result.channel_status,
        )
        return result

    if book["publication_status"] != "review":
        submitted = await submit_book_for_review(book_id, actor_user_id)
        if not submitted:
            await set_book_publication_status(book_id, "review")
    await enqueue_book_moderation(book_id, check.reasons, risk_level=check.risk_level)
    await notify_book_needs_moderation(
        bot,
        book_id=book_id,
        reasons=check.reasons,
        reminder=False,
    )
    await add_audit(
        actor_user_id,
        "book_auto_moderation_manual_review",
        "book",
        str(book_id),
        source,
        " | ".join(check.reasons)[:1000],
    )
    return PublicationResult(
        False,
        "",
        workflow_status="review",
        duplicate_text="\n".join(check.reasons),
    )

