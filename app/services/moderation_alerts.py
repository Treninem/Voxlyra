from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings
from app.db import (
    add_audit,
    get_book,
    list_book_moderation_staff,
    list_due_book_moderation_reminders,
    mark_book_moderation_notified,
)

logger = logging.getLogger(__name__)


async def _send_alert(bot: Bot, telegram_id: int, text: str, reply_markup=None) -> None:
    kwargs = {
        "chat_id": int(telegram_id),
        "text": text,
        "parse_mode": ParseMode.HTML,
    }
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    try:
        await bot.send_message(disable_web_page_preview=True, **kwargs)
    except TypeError:
        # Упрощённые тестовые клиенты и старые обёртки могут не принимать этот параметр.
        await bot.send_message(**kwargs)


def _moderation_markup(book_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔎 Открыть проверку", callback_data=f"mod:book:{int(book_id)}")],
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"mod:book_publish:{int(book_id)}"),
                InlineKeyboardButton(text="↩️ На доработку", callback_data=f"mod:book_reject:{int(book_id)}"),
            ],
        ]
    )


async def _recipient_telegram_ids() -> list[int]:
    ids = {int(value) for value in settings.owner_ids}
    for row in await list_book_moderation_staff():
        telegram_id = int(row["telegram_id"] or 0)
        if telegram_id:
            ids.add(telegram_id)
    return sorted(ids)


async def notify_book_needs_moderation(
    bot: Bot,
    *,
    book_id: int,
    reasons: list[str],
    reminder: bool = False,
) -> dict[str, int]:
    book = await get_book(int(book_id))
    if not book:
        return {"sent": 0, "failed": 0}
    recipients = await _recipient_telegram_ids()
    reason_lines = reasons or ["Автоматическая проверка не смогла принять надёжное решение."]
    reason_text = "\n".join(f"• {html.escape(str(item))}" for item in reason_lines[:8])
    prefix = "⏰ Напоминание: книга ждёт проверки" if reminder else "🛡 Новая книга ждёт проверки"
    text = (
        f"<b>{prefix}</b>\n\n"
        f"Книга: <b>{html.escape(str(book['title'] or 'Без названия'))}</b>\n"
        f"Автор: <b>{html.escape(str(book['pen_name'] or 'не указан'))}</b>\n\n"
        f"Почему нужна ручная проверка:\n{reason_text}\n\n"
        "Книга не заблокирована и не опубликована. Она останется в очереди до решения."
    )
    totals = {"sent": 0, "failed": 0}
    for telegram_id in recipients:
        try:
            await _send_alert(bot, telegram_id, text, _moderation_markup(book_id))
            totals["sent"] += 1
        except Exception as exc:
            totals["failed"] += 1
            logger.warning("Moderation alert failed for %s: %s", telegram_id, exc)
    await mark_book_moderation_notified(int(book_id), reminder=reminder)
    await add_audit(
        None,
        "book_moderation_reminder" if reminder else "book_moderation_alert",
        "book",
        str(book_id),
        None,
        f"sent={totals['sent']};failed={totals['failed']}",
    )
    return totals


async def notify_moderation_resolved(
    bot: Bot,
    *,
    book_id: int,
    resolution: str,
    actor_name: str,
) -> None:
    book = await get_book(int(book_id))
    if not book:
        return
    labels = {
        "published": "опубликована",
        "revision": "возвращена автору на доработку",
        "rejected": "отклонена",
    }
    text = (
        "✅ <b>Проверка книги завершена</b>\n\n"
        f"«{html.escape(str(book['title'] or 'Книга'))}» {labels.get(resolution, resolution)}.\n"
        f"Решение принял: <b>{html.escape(actor_name or 'сотрудник')}</b>.\n\n"
        "Повторные напоминания прекращены."
    )
    for telegram_id in await _recipient_telegram_ids():
        try:
            await _send_alert(bot, telegram_id, text)
        except Exception:
            pass


async def moderation_reminder_loop(bot: Bot) -> None:
    """Проверяет очередь раз в десять минут; интервалы напоминаний хранятся в БД."""
    while True:
        try:
            for row in await list_due_book_moderation_reminders(limit=25):
                reasons = [line for line in str(row["reasons"] or "").splitlines() if line.strip()]
                await notify_book_needs_moderation(
                    bot,
                    book_id=int(row["book_id"]),
                    reasons=reasons,
                    reminder=True,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Moderation reminder loop failed")
        await asyncio.sleep(600)
