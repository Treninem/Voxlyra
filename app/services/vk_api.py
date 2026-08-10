from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
_profile_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def vk_app_url(location: str = "") -> str:
    if int(settings.VK_APP_ID or 0) <= 0:
        return ""
    suffix = f"#{location.lstrip('#')}" if location else ""
    return f"https://vk.com/app{int(settings.VK_APP_ID)}{suffix}"


async def vk_api_call(method: str, params: dict[str, Any] | None = None, *, token: str = "") -> Any:
    access_token = str(token or settings.VK_GROUP_TOKEN or settings.VK_SERVICE_TOKEN or "").strip()
    if not access_token:
        raise RuntimeError("VK access token is not configured")
    payload = dict(params or {})
    payload["access_token"] = access_token
    payload["v"] = str(settings.VK_API_VERSION or "5.199")
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(f"https://api.vk.com/method/{method}", data=payload)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        error = data["error"]
        raise RuntimeError(f"VK API {method}: {error.get('error_code')} {error.get('error_msg')}")
    return data.get("response")


async def get_vk_user_profile(vk_user_id: int) -> dict[str, Any] | None:
    user_id = int(vk_user_id)
    now = time.monotonic()
    cached = _profile_cache.get(user_id)
    if cached and cached[0] > now:
        return dict(cached[1])
    token = str(settings.VK_SERVICE_TOKEN or settings.VK_GROUP_TOKEN or "").strip()
    if not token:
        return None
    try:
        rows = await vk_api_call(
            "users.get",
            {"user_ids": str(user_id), "fields": "photo_200,screen_name"},
            token=token,
        )
    except Exception as exc:
        logger.warning("Could not resolve VK user profile %s: %s", user_id, exc)
        return None
    if not isinstance(rows, list) or not rows:
        return None
    row = dict(rows[0])
    _profile_cache[user_id] = (now + 900.0, row)
    return row


def vk_main_keyboard() -> str:
    """Permanent VK community keyboard with native Mini App launch buttons."""
    app_id = int(settings.VK_APP_ID or 0)
    owner_id = -abs(int(settings.VK_GROUP_ID or 0))
    if app_id <= 0 or owner_id == 0:
        return ""

    def app_button(label: str, location: str, color: str = "primary") -> dict[str, Any]:
        return {
            "action": {
                "type": "open_app", "app_id": app_id, "owner_id": owner_id,
                "hash": location, "label": label,
            },
            "color": color,
        }

    keyboard = {
        "one_time": False, "inline": False,
        "buttons": [
            [app_button("📚 Читать", "catalog"), app_button("🎧 Слушать", "audio")],
            [app_button("🖼 Комиксы", "comics"), app_button("✨ Новинки", "new")],
            [app_button("📖 Моя библиотека", "library", "positive")],
            [app_button("👤 Моё", "settings"), app_button("✍ Автору", "author")],
        ],
    }
    return json.dumps(keyboard, ensure_ascii=False, separators=(",", ":"))


async def send_vk_message(vk_user_id: int, text: str, *, keyboard: str | None = None) -> bool:
    if not settings.VK_GROUP_TOKEN:
        return False
    try:
        params: dict[str, Any] = {
            "user_id": int(vk_user_id),
            "random_id": random.randint(1, 2_147_483_647),
            "message": str(text or "")[:4096],
        }
        if keyboard:
            params["keyboard"] = keyboard
        await vk_api_call("messages.send", params, token=settings.VK_GROUP_TOKEN)
        return True
    except Exception as exc:
        logger.warning("VK message delivery failed for user %s: %s", vk_user_id, exc)
        return False


async def run_vk_community_bot() -> None:
    """Long Poll entry point for the VK community bot.

    It intentionally stays small: the product UI and business logic live in the
    shared Mini App. The community bot gives users the same entry point as the
    Telegram bot without maintaining a second application codebase.
    """
    if not settings.VK_ENABLED or not settings.VK_GROUP_TOKEN or int(settings.VK_GROUP_ID or 0) <= 0:
        return
    group_id = int(settings.VK_GROUP_ID)
    app_url = vk_app_url()
    keyboard = vk_main_keyboard()
    delay = 2
    while True:
        try:
            lp = await vk_api_call("groups.getLongPollServer", {"group_id": group_id}, token=settings.VK_GROUP_TOKEN)
            server, key, ts = str(lp["server"]), str(lp["key"]), str(lp["ts"])
            delay = 2
            async with httpx.AsyncClient(timeout=35.0) as client:
                while True:
                    response = await client.get(server, params={"act": "a_check", "key": key, "ts": ts, "wait": 25})
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("failed"):
                        break
                    ts = str(payload.get("ts") or ts)
                    for event in payload.get("updates") or []:
                        if event.get("type") != "message_new":
                            continue
                        message = ((event.get("object") or {}).get("message") or {})
                        from_id = int(message.get("from_id") or 0)
                        if from_id <= 0:
                            continue
                        text = "VoxLyra — книги, аудио, комиксы и личная библиотека. Выберите раздел кнопкой ниже."
                        if app_url:
                            text += f"\n\nОткрыть приложение: {app_url}"
                        await send_vk_message(from_id, text, keyboard=keyboard)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("VK community bot stopped; retrying in %s seconds", delay)
            await asyncio.sleep(delay)
            delay = min(60, delay * 2)
