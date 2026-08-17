from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.config import settings
from app.db import (
    get_user_monthly_reading_report,
    get_user_reading_dashboard,
    list_monthly_reader_report_candidates,
    list_smart_reader_reminder_candidates,
    list_weekly_reader_report_candidates,
    mark_smart_notification_sent,
)
from app.services.notifications import send_user_notification

logger = logging.getLogger(__name__)

_RETURN_MESSAGE_PACK: dict[str, object] | None = None
_RETURN_MESSAGE_PACK_PATH = Path(__file__).resolve().parents[1] / "data" / "reader_return_messages_v1.json"


def _load_return_message_pack() -> dict[str, object]:
    global _RETURN_MESSAGE_PACK
    if _RETURN_MESSAGE_PACK is None:
        try:
            _RETURN_MESSAGE_PACK = json.loads(_RETURN_MESSAGE_PACK_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _RETURN_MESSAGE_PACK = {
                "address_forms": {"female": ["милая"], "male": ["милый"], "neutral": ["друг"]},
                "messages": ["{address}, твоя книга ждёт тебя. Возвращайся, когда захочется 📖"],
            }
    return _RETURN_MESSAGE_PACK


def _gender_key(value: object) -> str:
    raw = str(value or "").strip().casefold()
    if raw in {"female", "woman", "girl", "f", "ж", "женский", "женщина", "девушка"}:
        return "female"
    if raw in {"male", "man", "boy", "m", "м", "мужской", "мужчина", "парень"}:
        return "male"
    return "neutral"


def _choose_return_address(gender: object = None) -> str:
    pack = _load_return_message_pack()
    forms = pack.get("address_forms") or {}
    candidates = forms.get(_gender_key(gender)) or forms.get("neutral") or ["друг"]
    return str(random.choice(list(candidates)))


def continue_reading_message(book_title: object, chapter_number: object, chapter_title: object = "", gender: object = None) -> str:
    """Выбирает случайное мягкое напоминание; пол учитывается только из явно сохранённого профиля."""
    pack = _load_return_message_pack()
    messages = [str(item) for item in (pack.get("messages") or []) if str(item).strip()]
    template = random.choice(messages) if messages else "{address}, твоя книга ждёт тебя 📖"
    text = template.replace("{address}", _choose_return_address(gender))
    text = text.replace("{book_title}", " ".join(str(book_title or "Книга").split())[:160])
    text = text.replace("{chapter_number}", str(max(1, int(chapter_number or 1))))
    text = text.replace("{chapter_title}", " ".join(str(chapter_title or "").split())[:120])
    return text


def continue_reading_markup(book_id: int) -> InlineKeyboardMarkup | None:
    base = settings.WEBAPP_URL.strip().rstrip("/")
    if not base:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Продолжить чтение", web_app=WebAppInfo(url=f"{base}/book/{int(book_id)}"))]]
    )


async def send_smart_reader_reminders(bot: Bot, limit: int = 100) -> dict[str, int]:
    totals = {"sent": 0, "disabled": 0, "unavailable": 0, "failed": 0}
    for row in await list_smart_reader_reminder_candidates(limit=limit):
        status = await send_user_notification(
            app_user_id=int(row["user_id"]),
            telegram_id=int(row["telegram_id"]),
            text=continue_reading_message(row["book_title"], row["chapter_number"], row["chapter_title"], row.get("gender")),
            bot=bot,
            category="reminders",
            reply_markup=continue_reading_markup(int(row["book_id"])),
        )
        totals[status] = totals.get(status, 0) + 1
        if status == "sent":
            user_id = int(row["user_id"])
            await mark_smart_notification_sent(user_id, "continue_reading", str(row["book_id"]))
            await mark_smart_notification_sent(user_id, "continue_reading_daily", str(row.get("daily_context_key") or ""))
    return totals


def weekly_report_message(dashboard: dict[str, object]) -> str:
    week = dashboard.get("week_totals") or {}
    completed = int(dashboard.get("completed_goals") or 0)
    enabled = int(dashboard.get("enabled_goals") or 0)
    goals_line = f"Цели: {completed} из {enabled} выполнено" if enabled else "Цели на неделю пока не заданы"
    return (
        "📚 Ваша неделя в VoxLyra\n\n"
        f"Активных дней: {int(week.get('active_days') or 0)}\n"
        f"Текстовых глав: {int(week.get('text_chapters') or 0)}\n"
        f"Аудио: {int(week.get('audio_minutes') or 0)} мин.\n"
        f"Комиксы: {int(week.get('graphic_pages') or 0)} стр.\n"
        f"Текущая серия: {int(dashboard.get('current_streak') or 0)} дн.\n"
        f"{goals_line}\n\n"
        "Отчёт личный и приходит только по выбранному расписанию."
    )


def weekly_report_markup() -> InlineKeyboardMarkup | None:
    base = settings.WEBAPP_URL.strip().rstrip("/")
    if not base:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Открыть статистику", web_app=WebAppInfo(url=f"{base}/library?tab=activity"))]])


async def send_weekly_reader_reports(bot: Bot, limit: int = 100) -> dict[str, int]:
    totals = {"sent": 0, "disabled": 0, "unavailable": 0, "failed": 0}
    for row in await list_weekly_reader_report_candidates(limit=limit):
        dashboard = await get_user_reading_dashboard(int(row["user_id"]))
        status = await send_user_notification(app_user_id=int(row["user_id"]), telegram_id=int(row["telegram_id"]), text=weekly_report_message(dashboard), bot=bot, category="reminders", reply_markup=weekly_report_markup())
        totals[status] = totals.get(status, 0) + 1
        if status == "sent":
            await mark_smart_notification_sent(int(row["user_id"]), "weekly_reading_report", str(row.get("context_key") or ""))
    return totals


_MONTH_NAMES = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _month_label(month_key: object) -> str:
    raw = str(month_key or "")
    try:
        year_raw, month_raw = raw.split("-", 1)
        year, month = int(year_raw), int(month_raw)
        if 1 <= month <= 12:
            return f"{_MONTH_NAMES[month]} {year}"
    except (TypeError, ValueError):
        pass
    return raw or "месяца"


def _comparison_line(item: dict[str, object]) -> str:
    label = str(item.get("label") or "Показатель")
    delta = int(item.get("delta") or 0)
    unit = str(item.get("unit") or "").strip()
    if delta == 0:
        value = "без изменений"
    else:
        value = f"{delta:+d}{f' {unit}' if unit else ''}"
    return f"{label}: {value}"


def monthly_report_message(report: dict[str, object]) -> str:
    totals = report.get("totals") or {}
    comparisons = report.get("comparisons") or []
    recommendation = report.get("recommendation") or {}
    best_day = report.get("best_day") or {}
    compare_lines = "\n".join(_comparison_line(dict(item)) for item in comparisons)
    best_line = ""
    if best_day:
        best_line = f"\nСамый насыщенный день: {str(best_day.get('date') or '')} · {int(best_day.get('text_chapters') or 0)} гл., {int(best_day.get('audio_minutes') or 0)} мин. аудио, {int(best_day.get('graphic_pages') or 0)} стр."
    return (
        f"📚 Итоги {_month_label(report.get('month'))} в VoxLyra\n\n"
        f"Активных дней: {int(totals.get('active_days') or 0)}\n"
        f"Текстовых глав: {int(totals.get('text_chapters') or 0)}\n"
        f"Аудио: {int(totals.get('audio_minutes') or 0)} мин.\n"
        f"Комиксы: {int(totals.get('graphic_pages') or 0)} стр.\n"
        f"Сеансов: {int(totals.get('sessions') or 0)}{best_line}\n\n"
        f"По сравнению с {_month_label(report.get('previous_month'))}:\n{compare_lines}\n\n"
        f"{str(recommendation.get('title') or 'Личный ритм')}: "
        f"{str(recommendation.get('text') or 'Выбирайте удобный темп без давления.')}"
    )


async def send_monthly_reader_reports(bot: Bot, limit: int = 100) -> dict[str, int]:
    totals = {"sent": 0, "disabled": 0, "unavailable": 0, "failed": 0, "skipped": 0}
    for row in await list_monthly_reader_report_candidates(limit=limit):
        report = await get_user_monthly_reading_report(int(row["user_id"]), str(row.get("report_month") or ""))
        month_totals = report.get("totals") or {}
        if int(month_totals.get("sessions") or 0) <= 0:
            totals["skipped"] += 1
            continue
        status = await send_user_notification(app_user_id=int(row["user_id"]), telegram_id=int(row["telegram_id"]), text=monthly_report_message(report), bot=bot, category="reminders", reply_markup=weekly_report_markup())
        totals[status] = totals.get(status, 0) + 1
        if status == "sent":
            await mark_smart_notification_sent(int(row["user_id"]), "monthly_reading_report", str(row.get("context_key") or ""))
    return totals


async def smart_reader_reminder_loop(bot: Bot) -> None:
    """Проверяет персональные расписания раз в 15 минут и не повторяет уже отправленные слоты."""
    await asyncio.sleep(90)
    while True:
        try:
            reminders = await send_smart_reader_reminders(bot, limit=100)
            reports = await send_weekly_reader_reports(bot, limit=100)
            monthly_reports = await send_monthly_reader_reports(bot, limit=100)
            if reminders.get("sent") or reports.get("sent") or monthly_reports.get("sent"):
                logger.info("Reader notifications: reminders=%s weekly=%s monthly=%s", reminders, reports, monthly_reports)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reader notification loop failed")
        await asyncio.sleep(900)
