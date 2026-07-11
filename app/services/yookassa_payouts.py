from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
import uuid
from typing import Any

import httpx

from app.services.payment_runtime import load_runtime_payment_settings


API_BASE = "https://api.yookassa.ru/v3"
MIN_SBP_MINOR = 100
MAX_SBP_MINOR = 50_000_000


class YooKassaPayoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class PayoutResult:
    payout_id: str
    status: str
    raw: dict[str, Any]


async def payouts_configured() -> bool:
    cfg = await load_runtime_payment_settings()
    return cfg.payouts_ready


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) != 11 or not digits.startswith("7"):
        raise ValueError("Для выплаты через СБП нужен российский номер в формате +7XXXXXXXXXX.")
    return f"+{digits}"


def validate_amount_minor(amount_minor: int) -> int:
    value = int(amount_minor)
    if value < MIN_SBP_MINOR or value > MAX_SBP_MINOR:
        raise ValueError("Сумма выплаты через СБП должна быть от 1 до 500 000 рублей.")
    return value


def _amount_text(amount_minor: int) -> str:
    return f"{Decimal(validate_amount_minor(amount_minor)) / Decimal(100):.2f}"


async def _auth() -> tuple[str, str]:
    cfg = await load_runtime_payment_settings()
    if not cfg.payouts_ready:
        raise YooKassaPayoutError("Выплаты ЮKassa не подключены: включите выплаты и заполните gateway ID и секретный ключ.")
    return cfg.payout_gateway_id.strip(), cfg.payout_secret.strip()


async def list_sbp_banks(*, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    own = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        response = await http.get(f"{API_BASE}/sbp_banks", auth=await _auth())
        if response.status_code >= 400:
            raise YooKassaPayoutError(f"ЮKassa вернула ошибку {response.status_code} при загрузке банков.")
        payload = response.json()
        if isinstance(payload, list):
            items = payload
        else:
            items = list(payload.get("items") or [])
        banks: list[dict[str, Any]] = []
        for item in items:
            bank_id = str(item.get("bank_id") or "").strip()
            name = str(item.get("name") or "").strip()
            if bank_id and name:
                banks.append({"bank_id": bank_id, "name": name, "bic": str(item.get("bic") or "").strip()})
        banks.sort(key=lambda item: item["name"].casefold())
        return banks
    finally:
        if own:
            await http.aclose()


async def create_sbp_payout(
    *,
    amount_minor: int,
    phone: str,
    bank_id: str,
    description: str,
    metadata: dict[str, Any] | None = None,
    idempotence_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PayoutResult:
    bank = str(bank_id or "").strip()
    if not bank:
        raise ValueError("Не выбран банк для выплаты через СБП.")
    payload = {
        "amount": {"value": _amount_text(amount_minor), "currency": "RUB"},
        "payout_destination_data": {
            "type": "sbp",
            "phone": normalize_phone(phone).lstrip("+"),
            "bank_id": bank,
        },
        "description": str(description or "Выплата автору Вокслиры")[:128],
        "metadata": metadata or {},
    }
    headers = {"Idempotence-Key": idempotence_key or str(uuid.uuid4())}
    own = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await http.post(f"{API_BASE}/payouts", json=payload, headers=headers, auth=await _auth())
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = data.get("description") or data.get("code") or f"HTTP {response.status_code}"
            raise YooKassaPayoutError(f"Выплата отклонена ЮKassa: {detail}")
        return PayoutResult(str(data.get("id") or ""), str(data.get("status") or "pending"), data)
    finally:
        if own:
            await http.aclose()


async def get_payout(payout_id: str, *, client: httpx.AsyncClient | None = None) -> PayoutResult:
    payout = str(payout_id or "").strip()
    if not payout:
        raise ValueError("Не указан идентификатор выплаты.")
    own = client is None
    http = client or httpx.AsyncClient(timeout=20.0)
    try:
        response = await http.get(f"{API_BASE}/payouts/{payout}", auth=await _auth())
        data = response.json() if response.content else {}
        if response.status_code >= 400:
            raise YooKassaPayoutError(f"Не удалось получить статус выплаты: HTTP {response.status_code}")
        return PayoutResult(str(data.get("id") or payout), str(data.get("status") or "unknown"), data)
    finally:
        if own:
            await http.aclose()
