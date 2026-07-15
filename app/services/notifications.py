from __future__ import annotations

import logging
from typing import Literal

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.config import settings
from app.db import (
    claim_notification_delivery,
    finish_notification_delivery,
    get_user_preferences,
    list_book_notification_recipients,
)

logger = logging.getLogger(__name__)

NotificationStatus = Literal["sent", "disabled", "unavailable", "failed"]


def _clean(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def book_moderation_message(
    title: object,
    status: str,
    *,
    reason: object = "",
    book_id: int | None = None,
) -> str:
    safe_title = _clean(title, 160) or "Книга"
    if status == "published":
        return f"📚 Книга опубликована\n\n«{safe_title}» прошла проверку и появилась в Вокслире."
    safe_reason = _clean(reason, 1200)
    reason_block = f"\n\nЧто нужно исправить:\n{safe_reason}" if safe_reason else ""
    return (
        "📚 Книга возвращена на доработку\n\n"
        f"«{safe_title}» пока не прошла проверку.{reason_block}\n\n"
        "Откройте кабинет автора, внесите изменения и отправьте книгу повторно."
    )


def book_revision_markup(book_id: int) -> InlineKeyboardMarkup | None:
    web_url = settings.WEBAPP_URL.strip().rstrip("/")
    if not web_url or int(book_id or 0) <= 0:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="✍️ Открыть книгу",
                web_app=WebAppInfo(url=f"{web_url}/author?book_id={int(book_id)}"),
            )
        ]]
    )


def content_hidden_message(kind: str, book_title: object, chapter_title: object = "") -> str:
    safe_book = _clean(book_title, 160) or "Книга"
    if kind == "comment":
        safe_chapter = _clean(chapter_title, 160)
        location = f" к главе «{safe_chapter}»" if safe_chapter else f" к книге «{safe_book}»"
        return f"💬 Комментарий скрыт\n\nВаш комментарий{location} скрыт после проверки."
    return f"⭐ Отзыв скрыт\n\nВаш отзыв о книге «{safe_book}» скрыт после проверки."


def complaint_message(status: str) -> str:
    if status == "pending":
        return "🛡 Жалоба принята в работу\n\nОбращение передано на проверку."
    return "🛡 Проверка жалобы завершена\n\nРассмотрение вашего обращения завершено."


def refund_message(status: str, amount_stars: object, note: object = "") -> str:
    try:
        amount = max(0, int(amount_stars or 0))
    except (TypeError, ValueError):
        amount = 0
    if status == "refunded":
        return f"⭐ Возврат выполнен\n\n{amount} Stars возвращены на ваш баланс Telegram."
    safe_note = _clean(note, 500)
    suffix = f"\n\nПричина: {safe_note}" if safe_note else ""
    return f"⭐ Возврат отклонён\n\nЗапрос на возврат {amount} Stars не был одобрен.{suffix}"


def payout_message(status: str, amount_stars: object, note: object = "") -> str:
    try:
        amount = max(0, int(amount_stars or 0))
    except (TypeError, ValueError):
        amount = 0
    safe_note = _clean(note, 500)
    if status == "approved":
        return f"💎 Выплата одобрена\n\nЗаявка на {amount} Stars прошла проверку и готовится к выплате."
    if status == "paid":
        return f"💎 Выплата выполнена\n\nЗаявка на {amount} Stars отмечена как выплаченная."
    if status == "frozen":
        return f"💎 Выплата временно приостановлена\n\nЗаявка на {amount} Stars ожидает дополнительной проверки."
    if status == "new":
        return f"💎 Проверка выплаты возобновлена\n\nЗаявка на {amount} Stars снова передана на рассмотрение."
    suffix = f"\n\nПричина: {safe_note}" if safe_note else ""
    return f"💎 Выплата отклонена\n\nЗаявка на {amount} Stars не была одобрена.{suffix}"


def new_chapter_message(book_title: object, chapter_title: object = "", number: object = "", count: int = 1) -> str:
    safe_book = _clean(book_title, 160) or "Книга"
    if int(count or 1) > 1:
        return f"📖 Новые главы\n\nВ книге «{safe_book}» опубликовано новых глав: {int(count)}."
    safe_chapter = _clean(chapter_title, 160) or "Новая глава"
    try:
        chapter_number = int(number or 0)
    except (TypeError, ValueError):
        chapter_number = 0
    prefix = f"Глава {chapter_number}. " if chapter_number > 0 else ""
    return f"📖 Новая глава\n\nВ книге «{safe_book}» вышла {prefix}«{safe_chapter}»."


def new_audio_message(book_title: object, chapter_title: object = "", number: object = "", count: int = 1) -> str:
    safe_book = _clean(book_title, 160) or "Книга"
    if int(count or 1) > 1:
        return f"🎧 Новые аудиоглавы\n\nДля книги «{safe_book}» опубликовано аудиоглав: {int(count)}."
    safe_chapter = _clean(chapter_title, 160) or "Новая аудиоглава"
    try:
        chapter_number = int(number or 0)
    except (TypeError, ValueError):
        chapter_number = 0
    prefix = f"Аудиоглава {chapter_number}. " if chapter_number > 0 else ""
    return f"🎧 Новая аудиоглава\n\nДля книги «{safe_book}» появилась {prefix}«{safe_chapter}»."


def discount_message(book_title: object, discount_percent: object, code: object) -> str:
    safe_book = _clean(book_title, 160) or "Книга"
    safe_code = _clean(code, 40)
    try:
        discount = max(0, min(100, int(discount_percent or 0)))
    except (TypeError, ValueError):
        discount = 0
    code_line = f"\nПромокод: {safe_code}" if safe_code else ""
    return f"💎 Скидка на книгу\n\nНа «{safe_book}» действует скидка {discount}%.{code_line}"


async def send_user_notification(
    *,
    app_user_id: int | None,
    telegram_id: int | None,
    text: str,
    bot: Bot | None = None,
    category: Literal["chapters", "audio", "discounts", "reminders", "achievements"] | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: ParseMode | str | None = None,
) -> NotificationStatus:
    if not settings.BOT_TOKEN or not telegram_id:
        return "unavailable"
    if app_user_id is not None:
        preferences = await get_user_preferences(int(app_user_id))
        if preferences.get("notifications") == "0":
            return "disabled"
        if category and preferences.get(f"notifications_{category}", "1") == "0":
            return "disabled"

    owns_bot = bot is None
    delivery_bot = bot or Bot(token=settings.BOT_TOKEN)
    try:
        kwargs = {
            "chat_id": int(telegram_id),
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
        await delivery_bot.send_message(**kwargs)
        return "sent"
    except Exception as exc:  # Telegram may reject delivery when the user blocked the bot.
        logger.warning("Notification delivery failed for Telegram user %s: %s", telegram_id, exc)
        return "failed"
    finally:
        if owns_bot:
            await delivery_bot.session.close()

async def notify_book_followers(
    *,
    book_id: int,
    event_key: str,
    category: Literal["chapters", "audio", "discounts"],
    text: str,
    bot: Bot | None = None,
) -> dict[str, int]:
    """Отправляет одно событие заинтересованным читателям без повторной доставки."""
    totals = {"sent": 0, "disabled": 0, "unavailable": 0, "failed": 0, "duplicate": 0}
    recipients = await list_book_notification_recipients(int(book_id))
    if not recipients:
        return totals

    owns_bot = bot is None and bool(settings.BOT_TOKEN)
    delivery_bot = bot or (Bot(token=settings.BOT_TOKEN) if settings.BOT_TOKEN else None)
    try:
        for recipient in recipients:
            user_id = int(recipient["id"])
            claimed = await claim_notification_delivery(event_key, user_id, category)
            if not claimed:
                totals["duplicate"] += 1
                continue
            delivery_text = text
            if category == "chapters" and recipient["last_chapter_number"] is not None:
                last_number = int(recipient["last_chapter_number"] or 0)
                last_percent = int(recipient["last_position_percent"] or 0)
                if last_number > 0:
                    state = f"Вы остановились на главе {last_number}"
                    if 0 < last_percent < 90:
                        state += f" ({last_percent}%)"
                    delivery_text = f"{text}\n\n{state}. Продолжение уже доступно."
            status = await send_user_notification(
                app_user_id=user_id,
                telegram_id=int(recipient["telegram_id"]),
                text=delivery_text,
                bot=delivery_bot,
                category=category,
            )
            totals[status] += 1
            await finish_notification_delivery(event_key, user_id, status)
    finally:
        if owns_bot and delivery_bot is not None:
            await delivery_bot.session.close()
    return totals

