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
    get_chapter,
    get_graphic_chapter,
    get_audio_chapter,
    publish_book_content,
    enqueue_book_moderation,
    resolve_book_moderation,
    set_book_publication_status,
    submit_book_for_review,
    was_channel_post_sent,
    was_book_ever_published,
)
from app.services.channel import build_new_book_post
from app.services.duplicate_books import duplicate_warning_text, find_book_duplicates
from app.services.cover_storage import ensure_book_cover_file
from app.services.automatic_moderation import evaluate_book_for_auto_publication, resolve_book_moderation_findings
from app.services.moderation_revisions import (
    capture_moderation_snapshot,
    get_open_revision_request,
    mark_revision_resubmitted,
    resolve_revision_request,
)
from app.services.moderation_alerts import notify_book_needs_moderation
from app.services.notifications import new_chapter_message, notify_book_followers


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
            "restored": "Книга возвращена в каталог без повторного поста в канал.",
        }.get(self.channel_status, "")


def _book_link(book_id: int) -> str:
    """Возвращает единственную рабочую точку входа в книгу из канала.

    При подключённом Mini App Telegram сразу открывает нужную книгу. Если
    веб-приложение отключено, та же кнопка открывает бота с параметром книги,
    вместо неработающего ``startapp``. Прямая веб-ссылка используется только
    как запасной вариант, когда username бота не задан.
    """
    book_id = int(book_id)
    username = settings.BOT_USERNAME.strip().lstrip("@")
    web_url = settings.WEBAPP_URL.strip().rstrip("/")
    if username and web_url:
        # The channel contains one clean target button. Direct Mini App links
        # pass the book identifier as start_param.
        return f"https://t.me/{username}?startapp=book_{book_id}"
    if username:
        # If the Main Mini App is not configured, open the bot. The general
        # /start handler now receives this payload after the payment router skips it.
        return f"https://t.me/{username}?start=book_{book_id}"
    if web_url:
        return f"{web_url}/book/{book_id}"
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


async def _notify_restored_new_chapters(
    bot: Bot,
    *,
    book_id: int,
    content_result: dict,
    actor_user_id: int | None,
) -> None:
    """Notify only genuinely new approved content, never ordinary edits.

    All event keys are stable, so retries, repeated moderator clicks and process
    restarts cannot send the same notification twice.  The same rule is used
    for text, graphic and audio chapters.
    """
    for chapter_id in content_result.get("new_chapter_ids") or []:
        chapter = await get_chapter(int(chapter_id))
        if not chapter or str(chapter["status"] or "") != "published":
            continue
        result = await notify_book_followers(
            book_id=int(book_id),
            event_key=f"chapter:{int(chapter_id)}:published",
            category="chapters",
            text=new_chapter_message(
                str(chapter["book_title"] or "Книга"),
                str(chapter["title"] or "Новая глава"),
                int(chapter["number"] or 0),
            ),
            bot=bot,
        )
        await add_audit(
            actor_user_id, "chapter_followers_notified_after_moderation",
            "chapter", str(chapter_id), None, str(result),
        )

    book = await get_book(int(book_id))
    book_title = str(book["title"] or "Произведение") if book else "Произведение"
    for graphic_id in content_result.get("new_graphic_ids") or []:
        chapter = await get_graphic_chapter(int(graphic_id))
        if not chapter or str(chapter["status"] or "") != "published":
            continue
        result = await notify_book_followers(
            book_id=int(book_id),
            event_key=f"graphic-chapter:{int(graphic_id)}:published",
            category="chapters",
            text=new_chapter_message(
                book_title, str(chapter["title"] or "Новая графическая глава"),
                int(chapter["number"] or 0),
            ),
            bot=bot,
        )
        await add_audit(
            actor_user_id, "graphic_chapter_followers_notified_after_moderation",
            "graphic_chapter", str(graphic_id), None, str(result),
        )

    for audio_id in content_result.get("new_audio_ids") or []:
        chapter = await get_audio_chapter(int(audio_id))
        if not chapter or str(chapter["status"] or "") != "published":
            continue
        result = await notify_book_followers(
            book_id=int(book_id),
            event_key=f"audio-chapter:{int(audio_id)}:published",
            category="audio",
            text=new_chapter_message(
                book_title, str(chapter["title"] or "Новая аудиоглава"),
                int(chapter["number"] or 0),
            ),
            bot=bot,
        )
        await add_audit(
            actor_user_id, "audio_chapter_followers_notified_after_moderation",
            "audio_chapter", str(audio_id), None, str(result),
        )


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
    # Legacy installations may not have an old ``book_published`` audit row.
    # The current published status is therefore also authoritative; otherwise
    # restoring approved content could create a duplicate channel post and a
    # second “new book” notification.
    published_before = (
        str(book["publication_status"] or "") == "published"
        or await was_book_ever_published(book_id)
    )
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
    content_result = await publish_book_content(book_id)
    await add_audit(
        actor_user_id,
        "book_published",
        "book",
        str(book_id),
        None,
        f"published;new_chapters={len(content_result.get('new_chapter_ids') or [])};edited_chapters={len(content_result.get('edited_chapter_ids') or [])}",
    )
    if published_before and not force_channel:
        # Restoration or approval of new/edited content for an existing book.
        # The book keeps its old channel announcement and followers never
        # receive a fake "new book" event.  Only genuinely new chapters are
        # announced, with an idempotency key that survives retries.
        await _notify_restored_new_chapters(
            bot,
            book_id=int(book_id),
            content_result=content_result,
            actor_user_id=actor_user_id,
        )
        return PublicationResult(True, "restored", workflow_status="published")
    # Импортированные книги ставит в очередь Library Manager. Обычные авторские
    # книги идут в отдельную справедливую очередь, чтобы сотни публикаций не спамили канал.
    fresh = await get_book(book_id)
    if fresh and fresh["author_id"] is not None:
        from app.services.notifications import new_book_message, notify_author_followers

        follower_result = await notify_author_followers(
            author_id=int(fresh["author_id"]),
            book_id=int(book_id),
            event_key=f"book:{int(book_id)}:published",
            text=new_book_message(fresh["title"], fresh["pen_name"]),
            bot=bot,
        )
        await add_audit(
            actor_user_id,
            "author_followers_notified",
            "book",
            str(book_id),
            None,
            str(follower_result),
        )
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
    """Завершает любой путь загрузки одной безопасной логикой публикации.

    После возврата на доработку используется сохранённый снимок книги: повторная
    автомодерация проверяет только реально изменённые метаданные и главы, но не
    забывает замечания к неизменённым частям.
    """
    book = await get_book(book_id)
    if not book:
        return PublicationResult(False, "failed", "Книга не найдена", workflow_status="failed")

    revision_request = await get_open_revision_request(book_id)
    revision_mode = revision_request is not None or book["publication_status"] == "published"

    if revision_mode:
        check = await evaluate_book_for_auto_publication(
            book_id, actor_telegram_id=actor_telegram_id, revision_mode=True
        )
        if check.auto_publish or not check.reasons:
            # One publication path for first publication, restoration and
            # approval of pending chapters.  The helper knows whether the book
            # was published before and suppresses repeated book announcements.
            result = await publish_book_and_channel(
                bot,
                book_id,
                actor_user_id=actor_user_id,
            )
            if result.published:
                await resolve_book_moderation(
                    book_id, resolution="revision_auto_approved", actor_user_id=actor_user_id,
                    note="Изменённые части прошли автоматическую проверку",
                )
                await resolve_book_moderation_findings(book_id)
                await resolve_revision_request(book_id, "auto_approved")
                await capture_moderation_snapshot(
                    book_id, snapshot_kind="approved", actor_user_id=actor_user_id, source=source
                )
            await add_audit(
                actor_user_id, "book_revision_auto_approved", "book", str(book_id), source,
                f"published;changed={check.changed_summary}",
            )
            return result

        previously_published = await was_book_ever_published(book_id)
        if previously_published or book["publication_status"] == "published":
            # A content edit must never remove an established book from the
            # catalogue.  Pending chapters stay draft/review until approval;
            # the published book and all unaffected chapters remain available.
            if book["publication_status"] != "published":
                await set_book_publication_status(book_id, "published")
        elif book["publication_status"] != "review":
            submitted = await submit_book_for_review(book_id, actor_user_id)
            if not submitted:
                await set_book_publication_status(book_id, "review")
        await mark_revision_resubmitted(book_id)
        await enqueue_book_moderation(book_id, check.reasons, risk_level="revision")
        await notify_book_needs_moderation(bot, book_id=book_id, reasons=check.reasons, reminder=False)
        await add_audit(
            actor_user_id, "book_revision_manual_review", "book", str(book_id), source,
            (" | ".join(check.reasons) + f" | changed={check.changed_summary}")[:1000],
        )
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
            await resolve_book_moderation_findings(book_id)
            await capture_moderation_snapshot(
                book_id, snapshot_kind="approved", actor_user_id=actor_user_id, source=source
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

