from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode

from app.config import settings
from app.db import get_user_by_id, upsert_user
from app.services.vk_api import get_vk_user_profile
from app.services.account_identity import resolve_external_identity


class TMAAuthError(Exception):
    pass


@dataclass(frozen=True)
class TMAUser:
    app_user_id: int
    telegram_id: int
    username: str | None
    full_name: str | None
    photo_url: str | None = None
    platform: str = "telegram"
    external_id: int = 0
    vk_id: int | None = None


def _validate_init_data_raw(init_data: str, bot_token: str, max_age_seconds: int | None = None) -> dict[str, str]:
    if not init_data:
        raise TMAAuthError("Откройте этот раздел через Telegram или VK, чтобы сохранить доступ и прогресс.")
    if not bot_token:
        raise TMAAuthError("Сейчас не удалось проверить сессию. Откройте раздел заново через Telegram или VK.")

    if len(init_data.encode("utf-8")) > 16 * 1024:
        raise TMAAuthError("Данные сессии Telegram имеют неверный размер. Откройте раздел заново.")
    parsed_pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=False)
    keys = [key for key, _ in parsed_pairs]
    if len(keys) != len(set(keys)):
        raise TMAAuthError("Сессия Telegram содержит повторяющиеся поля. Откройте раздел заново.")
    pairs = dict(parsed_pairs)
    received_hash = pairs.pop("hash", None)
    if not received_hash or not re.fullmatch(r"[0-9a-fA-F]{64}", received_hash):
        raise TMAAuthError("Не удалось проверить сессию Telegram. Откройте раздел заново.")

    auth_date_raw = pairs.get("auth_date")
    if not auth_date_raw or not auth_date_raw.isdigit():
        raise TMAAuthError("Не удалось проверить время сессии Telegram. Откройте раздел заново.")
    auth_date = int(auth_date_raw)
    now = int(time.time())
    future_skew = max(0, int(settings.TMA_INIT_DATA_FUTURE_SKEW_SECONDS))
    if auth_date > now + future_skew:
        raise TMAAuthError("Время сессии Telegram не прошло проверку. Откройте раздел заново.")
    effective_max_age = int(settings.TMA_INIT_DATA_MAX_AGE_SECONDS if max_age_seconds is None else max_age_seconds)
    if effective_max_age > 0 and now - auth_date > effective_max_age:
        raise TMAAuthError("Сессия Mini App устарела. Откройте раздел заново через Telegram или VK.")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise TMAAuthError("Сессия не прошла проверку. Откройте раздел заново через Telegram или VK.")
    return pairs


async def _authenticate_telegram_init_data(init_data: str) -> TMAUser:
    """Проверяет Telegram WebApp initData и создаёт/обновляет пользователя в базе."""
    pairs = _validate_init_data_raw(init_data, settings.BOT_TOKEN)
    user_raw = pairs.get("user")
    if not user_raw:
        raise TMAAuthError("Не удалось определить пользователя Telegram. Откройте раздел заново.")
    try:
        tg_user: dict[str, Any] = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TMAAuthError("Не удалось прочитать данные Telegram. Откройте раздел заново.") from exc

    try:
        telegram_id = int(tg_user["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TMAAuthError("Не удалось определить пользователя Telegram. Откройте раздел заново.") from exc
    if telegram_id <= 0:
        raise TMAAuthError("Идентификатор пользователя Telegram не прошёл проверку.")
    username = tg_user.get("username")
    full_name = " ".join(filter(None, [tg_user.get("first_name"), tg_user.get("last_name")])) or username
    photo_url = str(tg_user.get("photo_url") or "").strip() or None
    legacy_user = await upsert_user(telegram_id=telegram_id, username=username, full_name=full_name)
    canonical_user_id = await resolve_external_identity("telegram", telegram_id, int(legacy_user["id"]))
    app_user = await get_user_by_id(canonical_user_id) or legacy_user
    if str(app_user["account_status"] or "active") == "deleted":
        raise TMAAuthError("Профиль удалён. Для восстановления обратитесь в поддержку.")
    if bool(app_user["is_blocked"]) and not settings.is_owner_identity(telegram_id):
        raise TMAAuthError("Доступ к платформе ограничен. Обратитесь в поддержку.")
    return TMAUser(
        app_user_id=int(app_user["id"]),
        telegram_id=int(app_user["telegram_id"] or telegram_id),
        username=username,
        full_name=full_name,
        photo_url=photo_url,
        platform="telegram",
        external_id=telegram_id,
        vk_id=None,
    )



def _validate_vk_launch_params(raw_query: str) -> dict[str, str]:
    if not settings.VK_ENABLED:
        raise TMAAuthError("Вход через VK сейчас выключен в настройках VoxLyra.")
    signing_secret = str(settings.VK_APP_SECRET or settings.VK_SECURE_KEY or "").strip()
    if int(settings.VK_APP_ID or 0) <= 0 or not signing_secret:
        raise TMAAuthError("Не задан защищённый ключ VK. Добавьте VK_APP_SECRET в переменные Bothost и выполните Redeploy.")
    if len(raw_query.encode("utf-8")) > 24 * 1024:
        raise TMAAuthError("Данные запуска VK имеют неверный размер.")
    pairs_list = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=False)
    keys = [key for key, _ in pairs_list]
    if len(keys) != len(set(keys)):
        raise TMAAuthError("Данные запуска VK содержат повторяющиеся поля.")
    pairs = dict(pairs_list)
    received_sign = str(pairs.get("sign") or "")
    if not received_sign or len(received_sign) > 128:
        raise TMAAuthError("Не удалось проверить подпись запуска VK.")
    signed = [(key, value) for key, value in pairs.items() if key.startswith("vk_")]
    signed.sort(key=lambda row: row[0])
    signing_string = urlencode(signed)
    digest = hmac.new(
        signing_secret.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    calculated = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    if not hmac.compare_digest(calculated, received_sign):
        raise TMAAuthError("Сессия VK не прошла проверку подписи.")
    app_id = str(pairs.get("vk_app_id") or "")
    if not app_id.isdigit() or int(app_id) != int(settings.VK_APP_ID):
        raise TMAAuthError("VK запустил приложение с другим идентификатором.")
    user_id = str(pairs.get("vk_user_id") or "")
    if not user_id.isdigit() or int(user_id) <= 0:
        raise TMAAuthError("Не удалось определить пользователя VK.")
    ts_raw = str(pairs.get("vk_ts") or "")
    if ts_raw.isdigit():
        ts = int(ts_raw)
        now = int(time.time())
        max_age = max(0, int(settings.VK_LAUNCH_MAX_AGE_SECONDS or 0))
        if ts > now + 120:
            raise TMAAuthError("Время запуска VK не прошло проверку.")
        if max_age and now - ts > max_age:
            raise TMAAuthError("Сессия VK устарела. Откройте Mini App заново.")
    return pairs


async def _authenticate_vk_launch_data(raw_query: str) -> TMAUser:
    pairs = _validate_vk_launch_params(raw_query)
    vk_id = int(pairs["vk_user_id"])
    profile = await get_vk_user_profile(vk_id)
    username = None
    full_name = f"VK пользователь {vk_id}"
    photo_url = None
    if profile:
        screen_name = str(profile.get("screen_name") or "").strip()
        username = screen_name or None
        full_name = " ".join(filter(None, [profile.get("first_name"), profile.get("last_name")])).strip() or full_name
        photo_url = str(profile.get("photo_200") or "").strip() or None
    identity_id = settings.vk_identity_id(vk_id)
    legacy_user = await upsert_user(telegram_id=identity_id, username=username, full_name=full_name)
    canonical_user_id = await resolve_external_identity("vk", vk_id, int(legacy_user["id"]))
    app_user = await get_user_by_id(canonical_user_id) or legacy_user
    owner_identity = identity_id if vk_id in settings.vk_owner_ids else int(app_user["telegram_id"] or identity_id)
    if str(app_user["account_status"] or "active") == "deleted":
        raise TMAAuthError("Профиль удалён. Для восстановления обратитесь в поддержку.")
    if bool(app_user["is_blocked"]) and not settings.is_owner_identity(owner_identity):
        raise TMAAuthError("Доступ к платформе ограничен. Обратитесь в поддержку.")
    return TMAUser(
        app_user_id=int(app_user["id"]),
        telegram_id=owner_identity,
        username=username,
        full_name=full_name,
        photo_url=photo_url,
        platform="vk",
        external_id=vk_id,
        vk_id=vk_id,
    )


async def authenticate_init_data(init_data: str) -> TMAUser:
    """Authenticate either Telegram Mini App or VK Mini App launch data."""
    raw = str(init_data or "")
    if raw.startswith("vk:"):
        return await _authenticate_vk_launch_data(raw[3:])
    return await _authenticate_telegram_init_data(raw)
