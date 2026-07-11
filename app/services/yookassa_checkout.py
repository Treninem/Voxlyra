from __future__ import annotations

from typing import Any

import httpx

from app.services.payment_runtime import load_runtime_payment_settings

API_BASE = "https://api.yookassa.ru/v3"


class YooKassaCheckoutError(RuntimeError):
    pass


async def test_shop_connection(*, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    cfg = await load_runtime_payment_settings()
    if not cfg.shop_id.strip() or not cfg.shop_secret.strip():
        raise YooKassaCheckoutError("Укажите ShopID и секретный ключ магазина ЮKassa.")
    own = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        response = await http.get(
            f"{API_BASE}/payments",
            params={"limit": 1},
            auth=(cfg.shop_id.strip(), cfg.shop_secret.strip()),
        )
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = data.get("description") or data.get("code") or f"HTTP {response.status_code}"
            raise YooKassaCheckoutError(f"ЮKassa не подтвердила ключи магазина: {detail}")
        return {"ok": True, "test": bool(data.get("test", cfg.yookassa_test_mode))}
    finally:
        if own:
            await http.aclose()
