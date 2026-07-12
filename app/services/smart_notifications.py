from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.config import settings
from app.db import list_smart_reader_reminder_candidates, mark_smart_notification_sent
from app.services.notifications import send_user_notification

logger = logging.getLogger(__name__)


def continue_reading_message(book_title: object, chapter_number: object, chapter_title: object = "") -> str:
    title = " ".join(str(book_title or "Книга").split())[:160]
    chapter = max(1, int(chapter_number or 1))
    chapter_name = " ".join(str(chapter_title or "").split())[:120]
    suffix = f" · «{chapter_name}»" if chapter_name else ""
    return (
        "📖 Продолжить чтение?\n\n"
        f"Вы остановились на книге «{title}», глава {chapter}{suffix}. "
        "Место сохранено — можно продолжить с него."
    )


def continue_reading_markup(book_id: int) -> InlineKeyboardMarkup | None:
    base = settings.WEBAPP_URL.strip().rstrip("/")
    if not base:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Продолжить", web_app=WebAppInfo(url=f"{base}/book/{int(book_id)}"))]]
    )


async def send_smart_reader_reminders(bot: Bot, limit: int = 100) -> dict[str, int]:
    totals = {"sent": 0, "disabled": 0, "unavailable": 0, "failed": 0}
    for row in await list_smart_reader_reminder_candidates(limit=limit):
        status = await send_user_notification(
            app_user_id=int(row["user_id"]),
            telegram_id=int(row["telegram_id"]),
            text=continue_reading_message(row["book_title"], row["chapter_number"], row["chapter_title"]),
            bot=bot,
            category="reminders",
            reply_markup=continue_reading_markup(int(row["book_id"])),
        )
        totals[status] = totals.get(status, 0) + 1
        if status == "sent":
            await mark_smart_notification_sent(int(row["user_id"]), "continue_reading", str(row["book_id"]))
    return totals


async def smart_reader_reminder_loop(bot: Bot) -> None:
    """Проверяет не чаще раза в час; один читатель получает напоминание по книге не чаще раза в неделю."""
    await asyncio.sleep(90)
    while True:
        try:
            totals = await send_smart_reader_reminders(bot, limit=100)
            if totals.get("sent"):
                logger.info("Smart reading reminders: %s", totals)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Smart reading reminder loop failed")
        await asyncio.sleep(3600)
