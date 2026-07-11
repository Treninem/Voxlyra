from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db import get_setting, set_setting


_BOOL_KEYS = {
    "stars_enabled": "payments_stars_enabled",
    "content_protection_enabled": "content_protection_enabled",
    "watermark_enabled": "content_watermark_enabled",
}


@dataclass(frozen=True)
class RuntimePaymentSettings:
    """Активные настройки оплаты цифрового контента.

    В сборке v1.9.6 приём оплаты выполняется только Telegram Stars. Рублёвые
    значения используются исключительно как прозрачные расчётные ориентиры:
    один для покупателя и отдельный фиксируемый курс вознаграждения автора.
    """

    stars_enabled: bool
    content_protection_enabled: bool
    watermark_enabled: bool
    buyer_star_rate_minor: int
    author_star_rate_minor: int
    purchase_cancel_minutes: int

    # Поля совместимости для старых модулей. В v1.9.6 они всегда выключены,
    # чтобы сохранённые в прежней базе ключи не могли случайно активировать ЮKassa.
    yookassa_external_enabled: bool = False
    yookassa_telegram_provider_enabled: bool = False
    yookassa_payouts_enabled: bool = False
    yookassa_test_mode: bool = True
    active_provider_token: str = ""
    shop_id: str = ""
    shop_secret: str = ""
    payout_gateway_id: str = ""
    payout_secret: str = ""
    payouts_ready: bool = False
    telegram_provider_ready: bool = False
    external_checkout_ready: bool = False


def _flag(value: str | bool | int | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}


def _rate(value: Any, default: int) -> int:
    try:
        return max(1, min(100000, int(value)))
    except (TypeError, ValueError):
        return int(default)


async def load_runtime_payment_settings() -> RuntimePaymentSettings:
    flags: dict[str, bool] = {}
    defaults = {
        "stars_enabled": True,
        "content_protection_enabled": True,
        "watermark_enabled": True,
    }
    for name, key in _BOOL_KEYS.items():
        raw = await get_setting(key, "1" if defaults[name] else "0")
        flags[name] = _flag(raw, defaults[name])

    buyer_rate = _rate(await get_setting("payments_stars_buyer_rate_minor", "145"), 145)
    author_rate = _rate(await get_setting("payments_stars_author_rate_minor", "100"), 100)
    # Разница должна быть положительной. Она покрывает комиссии Telegram,
    # конвертацию, возвраты, налоги и расходы платформы; это не скрытая доплата.
    if buyer_rate <= author_rate:
        buyer_rate = min(100000, author_rate + 1)

    cancel_minutes = _rate(await get_setting("purchase_cancel_minutes", "15"), 15)
    cancel_minutes = max(1, min(120, cancel_minutes))

    return RuntimePaymentSettings(
        **flags,
        buyer_star_rate_minor=buyer_rate,
        author_star_rate_minor=author_rate,
        purchase_cancel_minutes=cancel_minutes,
    )


async def public_runtime_payment_settings() -> dict[str, Any]:
    cfg = await load_runtime_payment_settings()
    return {
        "stars_enabled": cfg.stars_enabled,
        "content_protection_enabled": cfg.content_protection_enabled,
        "watermark_enabled": cfg.watermark_enabled,
        "buyer_star_rate_minor": cfg.buyer_star_rate_minor,
        "buyer_star_rate_rubles": round(cfg.buyer_star_rate_minor / 100, 2),
        "author_star_rate_minor": cfg.author_star_rate_minor,
        "author_star_rate_rubles": round(cfg.author_star_rate_minor / 100, 2),
        "purchase_cancel_minutes": cfg.purchase_cancel_minutes,
        "rate_spread_minor": cfg.buyer_star_rate_minor - cfg.author_star_rate_minor,
        "telegram_digital_policy": "stars_only",
        "price_note": (
            "Покупатель оплачивает точное количество Telegram Stars. Рублёвая сумма рядом — ориентир: "
            "фактическая стоимость приобретения Stars зависит от Telegram, страны, платформы и сборов."
        ),
        "author_note": (
            "Курс автора фиксируется для каждой продажи. После удержания комиссии платформы доход автора "
            "рассчитывается в рублях по курсу, действовавшему в момент покупки."
        ),
        # Совместимость со старыми интерфейсами: интеграция ЮKassa полностью выключена.
        "yookassa_external_enabled": False,
        "yookassa_telegram_provider_enabled": False,
        "yookassa_test_mode": True,
        "yookassa_payouts_enabled": False,
        "telegram_provider_ready": False,
        "external_checkout_ready": False,
        "payouts_ready": False,
    }


async def update_runtime_payment_settings(payload: dict[str, Any]) -> dict[str, Any]:
    for name, key in _BOOL_KEYS.items():
        if name in payload:
            await set_setting(key, "1" if _flag(payload.get(name)) else "0")

    buyer_rate = None
    author_rate = None
    if "buyer_star_rate_minor" in payload:
        buyer_rate = _rate(payload.get("buyer_star_rate_minor"), 145)
    if "author_star_rate_minor" in payload:
        author_rate = _rate(payload.get("author_star_rate_minor"), 100)
    if "purchase_cancel_minutes" in payload:
        cancel_minutes = max(1, min(120, _rate(payload.get("purchase_cancel_minutes"), 15)))
        await set_setting("purchase_cancel_minutes", str(cancel_minutes))

    current = await load_runtime_payment_settings()
    buyer_rate = current.buyer_star_rate_minor if buyer_rate is None else buyer_rate
    author_rate = current.author_star_rate_minor if author_rate is None else author_rate
    if buyer_rate <= author_rate:
        raise ValueError("Курс для покупателя должен быть выше курса расчёта с автором.")

    if "buyer_star_rate_minor" in payload:
        await set_setting("payments_stars_buyer_rate_minor", str(buyer_rate))
    if "author_star_rate_minor" in payload:
        await set_setting("payments_stars_author_rate_minor", str(author_rate))

    # Жёсткий откат: старые переключатели ЮKassa остаются выключенными даже в старой базе.
    for key in (
        "payments_yookassa_external_enabled",
        "payments_yookassa_telegram_provider_enabled",
        "payments_yookassa_payouts_enabled",
        "rub_payments_enabled",
        "rub_payouts_enabled",
    ):
        await set_setting(key, "0")

    return await public_runtime_payment_settings()
