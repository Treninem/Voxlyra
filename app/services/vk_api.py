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


class VKAPIError(RuntimeError):
    """Structured VK API failure so delivery fallbacks do not parse strings."""

    def __init__(self, method: str, code: int, message: str) -> None:
        self.method = str(method)
        self.code = int(code or 0)
        self.message = str(message or "Unknown VK API error")
        super().__init__(f"VK API {self.method}: {self.code} {self.message}")


def vk_app_url(location: str = "") -> str:
    if int(settings.VK_APP_ID or 0) <= 0:
        return ""
    suffix = f"#{location.lstrip('#')}" if location else ""
    return f"https://vk.com/app{int(settings.VK_APP_ID)}{suffix}"


def _vk_section_url(location: str) -> str:
    """Return a stable VK Mini App link for a keyboard button."""
    return vk_app_url(location)


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
        raise VKAPIError(method, int(error.get("error_code") or 0), str(error.get("error_msg") or ""))
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


def vk_main_keyboard(vk_user_id: int | None = None) -> str:
    """VK equivalent of Telegram's mixed menu: content opens the Mini App,
    while personal, author and service sections stay inside the bot chat."""
    app_id = int(settings.VK_APP_ID or 0)
    owner_id = -abs(int(settings.VK_GROUP_ID or 0))
    if app_id <= 0 or owner_id == 0:
        return ""

    def app_button(label: str, location: str) -> dict[str, Any]:
        return {
            "action": {
                "type": "open_app",
                "app_id": app_id,
                "owner_id": owner_id,
                "hash": location,
                "label": label,
            },
        }

    def command_button(label: str, command: str) -> dict[str, Any]:
        return {
            "action": {
                "type": "text",
                "label": label,
                "payload": json.dumps({"vox": command}, ensure_ascii=False, separators=(",", ":")),
            },
            "color": "secondary",
        }

    buttons = [
        [app_button("📚 Книги", "catalog"), app_button("🖼 Комиксы", "comics")],
        [app_button("🎧 Слушать", "audio")],
        [command_button("⭐ Моё", "my"), command_button("✍ Автору", "author")],
        [command_button("⚙ Ещё", "more")],
    ]
    if vk_user_id is not None and int(vk_user_id) in settings.vk_owner_ids:
        buttons.append([command_button("👑 Управление", "owner")])

    keyboard = {
        "inline": True,
        "buttons": buttons,
    }
    return json.dumps(keyboard, ensure_ascii=False, separators=(",", ":"))


def _vk_command_keyboard(rows: list[list[tuple[str, str]]], *, inline: bool = False) -> str:
    buttons = []
    for row in rows:
        buttons.append([
            {
                "action": {
                    "type": "text", "label": label,
                    "payload": json.dumps({"vox": command}, ensure_ascii=False, separators=(",", ":")),
                },
                "color": "primary" if command == "main" else "secondary",
            }
            for label, command in row
        ])
    return json.dumps({"inline": bool(inline), "one_time": False, "buttons": buttons}, ensure_ascii=False, separators=(",", ":"))


def _vk_app_and_commands_keyboard(location: str, label: str, rows: list[list[tuple[str, str]]]) -> str:
    app_id = int(settings.VK_APP_ID or 0)
    owner_id = -abs(int(settings.VK_GROUP_ID or 0))
    buttons: list[list[dict[str, Any]]] = []
    if app_id > 0 and owner_id:
        buttons.append([{"action": {"type": "open_app", "app_id": app_id, "owner_id": owner_id, "hash": location, "label": label}}])
    command_keyboard = json.loads(_vk_command_keyboard(rows))
    buttons.extend(command_keyboard["buttons"])
    return json.dumps({"inline": False, "one_time": False, "buttons": buttons}, ensure_ascii=False, separators=(",", ":"))


def _vk_message_command(message: dict[str, Any]) -> str:
    payload = message.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
    if isinstance(payload, dict) and str(payload.get("vox") or "").strip():
        return str(payload["vox"]).strip().lower()
    text = str(message.get("text") or "").strip().casefold()
    aliases = {
        "начать": "main", "старт": "main", "/start": "main", "меню": "main", "главное меню": "main",
        "⭐ моё": "my", "мое": "my", "моё": "my", "✍ автору": "author", "автору": "author",
        "⚙ ещё": "more", "еще": "more", "ещё": "more", "💎 баланс и бонусы": "bonuses",
        "🛟 поддержка": "support", "📜 правила": "legal", "🎨 настройки": "settings",
        "🔗 связать telegram + vk": "link", "👑 управление": "owner",
    }
    return aliases.get(text, "main")


async def _vk_resolve_app_user(vk_user_id: int) -> tuple[int, Any]:
    from app.db import get_user_by_id, upsert_user
    from app.services.account_identity import resolve_external_identity

    identity_id = settings.vk_identity_id(int(vk_user_id))
    legacy = await upsert_user(identity_id, None, f"VK пользователь {int(vk_user_id)}")
    canonical_id = await resolve_external_identity("vk", int(vk_user_id), int(legacy["id"]))
    return canonical_id, (await get_user_by_id(canonical_id) or legacy)


async def _vk_bot_screen(vk_user_id: int, command: str) -> tuple[str, str]:
    """Render VK chat sections without forcing every action into Mini App."""
    from app.db import get_author_dashboard_stats, get_author_finance_summary, get_author_profile, get_bonus_balance, get_reader_wallet_balance
    from app.services.account_identity import identity_status

    app_user_id, _ = await _vk_resolve_app_user(vk_user_id)
    if command == "my":
        wallet = await get_reader_wallet_balance(app_user_id)
        bonuses = await get_bonus_balance(app_user_id)
        identities = await identity_status(app_user_id)
        linked = "Telegram и VK связаны" if identities.get("linked") else "аккаунты ещё не связаны"
        return (
            f"⭐ Моё\n\nБаланс: {wallet} Stars внутреннего учёта\nБонусы: {bonuses}\nПрофиль: {linked}.",
            _vk_app_and_commands_keyboard("library", "📚 Открыть мою библиотеку", [[("💎 Баланс и бонусы", "bonuses")], [("🔗 Связать Telegram + VK", "link")], [("🏠 Главное меню", "main")]]),
        )
    if command == "bonuses":
        wallet = await get_reader_wallet_balance(app_user_id)
        bonuses = await get_bonus_balance(app_user_id)
        return (f"💎 Баланс и бонусы\n\nБаланс доступа: {wallet}\nБонусных баллов: {bonuses}\n\nВо VK цены и оплата показываются в голосах, в Telegram — в Stars.", _vk_app_and_commands_keyboard("library", "💳 Открыть баланс", [[("⬅ Моё", "my")], [("🏠 Главное меню", "main")]]))
    if command == "author":
        profile = await get_author_profile(app_user_id)
        if profile:
            stats = await get_author_dashboard_stats(app_user_id)
            finance = await get_author_finance_summary(app_user_id)
            text = (f"✍ Кабинет автора\n\n{profile['pen_name']}\nПроизведений: {stats.get('books_total', 0)}\nОпубликовано: {stats.get('books_published', 0)}\nНа проверке: {stats.get('books_review', 0)}\nДоступно автору: {finance.get('available', 0)} Stars внутреннего расчёта.")
        else:
            identities = await identity_status(app_user_id)
            text = "✍ Стать автором\n\nПрофиль автора для этого аккаунта пока не создан. Создайте его во VK или свяжите существующий Telegram-профиль."
            if not identities.get("linked"):
                text += " При привязке можно объединить два профиля либо выбрать основной."
        return (text, _vk_app_and_commands_keyboard("author", "✍ Открыть кабинет автора", [[("🔗 Связать Telegram + VK", "link")], [("🏠 Главное меню", "main")]]))
    if command == "link":
        return ("🔗 Один аккаунт VoxLyra\n\nОткройте настройки, создайте код на одной платформе и введите его на другой. Если профили уже разные, VoxLyra предложит: объединить данные, оставить Telegram или оставить VK.", _vk_app_and_commands_keyboard("settings", "🔗 Открыть привязку аккаунтов", [[("⬅ Моё", "my")], [("🏠 Главное меню", "main")]]))
    if command == "support":
        return ("🛟 Поддержка\n\nОпишите проблему одним сообщением. Для покупки укажите произведение, главу и время оплаты. Сообщение будет сохранено в диалоге сообщества.", _vk_command_keyboard([[('⬅ Ещё', 'more')], [('🏠 Главное меню', 'main')]]))
    if command == "legal":
        return ("📜 Правила VoxLyra\n\nДокументы, согласия, управление личными данными и удаление профиля доступны одинаково для VK и Telegram.", _vk_app_and_commands_keyboard("settings", "📜 Открыть документы", [[("⬅ Ещё", "more")], [("🏠 Главное меню", "main")]]))
    if command == "settings":
        return ("🎨 Настройки\n\nТема, шрифт, уведомления, приватность и привязка аккаунтов сохраняются в едином профиле VoxLyra.", _vk_app_and_commands_keyboard("settings", "⚙ Открыть настройки", [[("⬅ Ещё", "more")], [("🏠 Главное меню", "main")]]))
    if command == "more":
        return ("⚙ Ещё\n\nВыберите раздел.", _vk_command_keyboard([[('🎨 Настройки', 'settings')], [('💎 Баланс и бонусы', 'bonuses'), ('🛟 Поддержка', 'support')], [('📜 Правила', 'legal')], [('🏠 Главное меню', 'main')]]))
    if command == "owner" and int(vk_user_id) in settings.vk_owner_ids:
        return ("👑 Управление VoxLyra\n\nПанель владельца использует тот же внутренний аккаунт и базу данных.", _vk_app_and_commands_keyboard("control", "👑 Открыть панель управления", [[("🏠 Главное меню", "main")]]))
    return ("VoxLyra — книги, аудио, комиксы и личная библиотека. Выберите раздел в меню ниже.", vk_main_keyboard(vk_user_id))


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
    except VKAPIError as exc:
        if exc.code == 912 and keyboard:
            # VK rejects keyboard actions while the community's "Chat bot
            # feature" switch is off. A plain message is still useful and
            # prevents the bot from appearing dead until an admin enables it.
            app_url = vk_app_url()
            fallback_text = str(text or "")
            if app_url and app_url not in fallback_text:
                fallback_text += f"\n\nОткрыть приложение: {app_url}"
            fallback_params: dict[str, Any] = {
                "user_id": int(vk_user_id),
                "random_id": random.randint(1, 2_147_483_647),
                "message": fallback_text[:4096],
            }
            try:
                await vk_api_call("messages.send", fallback_params, token=settings.VK_GROUP_TOKEN)
                logger.warning(
                    "VK Chat bot feature is disabled (API 912); sent user %s a keyboard-free fallback. "
                    "Enable Community management -> Messages -> Bot settings -> Chat bot feature.",
                    vk_user_id,
                )
                return True
            except Exception as fallback_exc:
                logger.warning("VK fallback delivery failed for user %s: %s", vk_user_id, fallback_exc)
                return False
        logger.warning("VK message delivery failed for user %s: %s", vk_user_id, exc)
        return False
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
    delay = 2
    processed_event_ids: set[str] = set()
    processed_event_order: list[str] = []
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
                        event_id = str(event.get("event_id") or "").strip()
                        if event_id and event_id in processed_event_ids:
                            continue
                        if event_id:
                            processed_event_ids.add(event_id)
                            processed_event_order.append(event_id)
                            if len(processed_event_order) > 2048:
                                processed_event_ids.discard(processed_event_order.pop(0))
                        message = ((event.get("object") or {}).get("message") or {})
                        from_id = int(message.get("from_id") or 0)
                        if from_id <= 0:
                            continue
                        command = _vk_message_command(message)
                        try:
                            text, keyboard = await _vk_bot_screen(from_id, command)
                        except Exception as screen_exc:
                            logger.exception("VK screen %s failed for user %s", command, from_id)
                            text = "Не удалось открыть выбранный раздел. Главное меню уже восстановлено — попробуйте ещё раз."
                            keyboard = vk_main_keyboard(from_id)
                        if not keyboard and app_url:
                            text += f"\n\nОткрыть приложение: {app_url}"
                        await send_vk_message(from_id, text, keyboard=keyboard)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("VK community bot stopped; retrying in %s seconds", delay)
            await asyncio.sleep(delay)
            delay = min(60, delay * 2)
