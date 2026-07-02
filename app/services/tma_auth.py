from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from app.config import settings
from app.db import upsert_user


class TMAAuthError(Exception):
    pass


@dataclass(frozen=True)
class TMAUser:
    app_user_id: int
    telegram_id: int
    username: str | None
    full_name: str | None


def _validate_init_data_raw(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict[str, str]:
    if not init_data:
        raise TMAAuthError("Mini App не передал данные Telegram.")
    if not bot_token:
        raise TMAAuthError("BOT_TOKEN не указан, проверка Mini App невозможна.")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise TMAAuthError("В данных Mini App нет hash.")

    auth_date_raw = pairs.get("auth_date")
    if auth_date_raw and auth_date_raw.isdigit():
        auth_date = int(auth_date_raw)
        if max_age_seconds > 0 and time.time() - auth_date > max_age_seconds:
            raise TMAAuthError("Сессия Mini App устарела. Откройте раздел заново из Telegram.")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise TMAAuthError("Данные Mini App не прошли проверку Telegram.")
    return pairs


async def authenticate_init_data(init_data: str) -> TMAUser:
    """Проверяет Telegram WebApp initData и создаёт/обновляет пользователя в базе."""
    pairs = _validate_init_data_raw(init_data, settings.BOT_TOKEN)
    user_raw = pairs.get("user")
    if not user_raw:
        raise TMAAuthError("В Mini App нет пользователя Telegram.")
    try:
        tg_user: dict[str, Any] = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise TMAAuthError("Telegram user в Mini App повреждён.") from exc

    telegram_id = int(tg_user["id"])
    username = tg_user.get("username")
    full_name = " ".join(filter(None, [tg_user.get("first_name"), tg_user.get("last_name")])) or username
    app_user = await upsert_user(telegram_id=telegram_id, username=username, full_name=full_name)
    return TMAUser(
        app_user_id=int(app_user["id"]),
        telegram_id=telegram_id,
        username=username,
        full_name=full_name,
    )
