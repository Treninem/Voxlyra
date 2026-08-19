from __future__ import annotations

import json
import logging
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import settings

logger = logging.getLogger(__name__)


def _platform_name(platform: str) -> str:
    return "VK" if str(platform) == "vk" else "Telegram"


def _telegram_settings_url() -> str:
    username = str(settings.BOT_USERNAME or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}?startapp=settings"
    web_url = str(settings.WEBAPP_URL or "").strip().rstrip("/")
    return f"{web_url}/settings" if web_url else ""


def _vk_confirmation_keyboard() -> str:
    app_id = int(settings.VK_APP_ID or 0)
    owner_id = -abs(int(settings.VK_GROUP_ID or 0))
    if app_id <= 0 or owner_id == 0:
        return ""
    return json.dumps(
        {
            "inline": True,
            "buttons": [[
                {
                    "action": {
                        "type": "open_app",
                        "app_id": app_id,
                        "owner_id": owner_id,
                        "hash": "settings",
                        "label": "✅ Проверить и подтвердить",
                    },
                }
            ]],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _send_telegram(user_id: int | str, text: str, *, with_confirmation_button: bool) -> tuple[bool, str]:
    if not str(settings.BOT_TOKEN or "").strip():
        return False, "Telegram-бот не настроен"
    bot = Bot(token=settings.BOT_TOKEN)
    try:
        markup = None
        url = _telegram_settings_url()
        if with_confirmation_button and url:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="✅ Проверить и подтвердить", url=url)]]
            )
        await bot.send_message(int(user_id), str(text)[:4000], reply_markup=markup)
        return True, ""
    except Exception as exc:
        logger.warning("Could not send account-link Telegram notification to %s: %s", user_id, exc)
        return False, f"{type(exc).__name__}: {exc}"[:500]
    finally:
        await bot.session.close()


async def _send_vk(user_id: int | str, text: str, *, with_confirmation_button: bool) -> tuple[bool, str]:
    try:
        from app.services.vk_api import send_vk_message

        keyboard = _vk_confirmation_keyboard() if with_confirmation_button else None
        sent = await send_vk_message(int(user_id), str(text)[:4000], keyboard=keyboard or None)
        return (True, "") if sent else (False, "VK не разрешил доставить сообщение")
    except Exception as exc:
        logger.warning("Could not send account-link VK notification to %s: %s", user_id, exc)
        return False, f"{type(exc).__name__}: {exc}"[:500]


async def send_link_confirmation_request(request: dict[str, Any]) -> tuple[bool, str]:
    target_platform = str(request.get("target_platform") or "")
    target_external_id = str(request.get("target_external_id") or "")
    source_platform = str(request.get("source_platform") or "")
    source_label = str(request.get("source_label") or "аккаунта")
    text = (
        "🔗 Запрос на объединение аккаунтов VoxLyra\n\n"
        f"Аккаунт {source_label} из {_platform_name(source_platform)} хочет объединить библиотеку с этим профилем.\n\n"
        "Ничего не объединится автоматически по одному username или ID. "
        "Откройте подтверждение и согласитесь только если это ваш второй аккаунт.\n\n"
        "Запрос действует 10 минут."
    )
    if target_platform == "telegram":
        return await _send_telegram(target_external_id, text, with_confirmation_button=True)
    if target_platform == "vk":
        return await _send_vk(target_external_id, text, with_confirmation_button=True)
    return False, "Неизвестная платформа"


async def notify_link_decision(request: dict[str, Any], *, confirmed: bool) -> None:
    source_platform = str(request.get("source_platform") or "")
    source_external_id = str(request.get("source_external_id") or "")
    if not source_external_id:
        return
    if confirmed:
        text = (
            "✅ Telegram и VK объединены в один профиль VoxLyra.\n\n"
            "Библиотека, покупки, баланс, прогресс чтения и авторские данные теперь доступны через обе платформы."
        )
    else:
        text = "❌ Запрос на объединение Telegram и VK был отклонён. Никакие данные не изменены."
    if source_platform == "telegram":
        await _send_telegram(source_external_id, text, with_confirmation_button=False)
    elif source_platform == "vk":
        await _send_vk(source_external_id, text, with_confirmation_button=False)
