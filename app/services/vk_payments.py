from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.config import settings
from app.db import (
    activate_premium_subscription,
    connect,
    create_paid_purchase,
    credit_wallet_topup,
    get_purchase_conflict,
    get_purchase_target,
    utc_now,
)
from app.services.payments import build_pay_target


class VKPaymentError(ValueError):
    pass


def votes_for_stars(stars: int) -> int:
    # Never accept a coefficient below 1: the VK representation must not make
    # canonical content cheaper than the existing Stars economy.
    ratio = max(1.0, float(getattr(settings, "VK_VOTES_PER_STAR", 1.0) or 1.0))
    return max(1, int(math.ceil(max(1, int(stars)) * ratio)))


async def ensure_vk_payment_schema() -> None:
    async with connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS vk_payment_intents (
                item_key TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                vk_user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                canonical_payload TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                amount_stars INTEGER NOT NULL,
                amount_votes INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                order_id TEXT,
                purchase_id INTEGER,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_vk_payment_intents_user
                ON vk_payment_intents(user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_vk_payment_intents_vk_user
                ON vk_payment_intents(vk_user_id, status, expires_at);

            CREATE TABLE IF NOT EXISTS vk_payment_orders (
                order_id TEXT NOT NULL,
                test_mode INTEGER NOT NULL DEFAULT 0,
                item_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                purchase_id INTEGER,
                response_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(order_id, test_mode)
            );
            """
        )
        # Cross-platform purchase metadata. Older INSERT statements can continue
        # omitting these columns because all have safe defaults.
        cur = await db.execute("PRAGMA table_info(purchases)")
        columns = {str(row[1]) for row in await cur.fetchall()}
        migrations = {
            "payment_platform": "ALTER TABLE purchases ADD COLUMN payment_platform TEXT NOT NULL DEFAULT 'telegram'",
            "amount_platform": "ALTER TABLE purchases ADD COLUMN amount_platform INTEGER NOT NULL DEFAULT 0",
            "platform_currency": "ALTER TABLE purchases ADD COLUMN platform_currency TEXT NOT NULL DEFAULT 'Stars'",
        }
        for name, sql in migrations.items():
            if name not in columns:
                await db.execute(sql)
        await db.commit()


def verify_callback_signature(params: Mapping[str, Any]) -> bool:
    received = str(params.get("sig") or "").lower()
    if len(received) != 32 or any(ch not in "0123456789abcdef" for ch in received):
        return False
    secret = str(
        getattr(settings, "VK_PAYMENT_SECRET", "")
        or settings.VK_APP_SECRET
        or getattr(settings, "VK_SECURE_KEY", "")
        or ""
    )
    if not secret:
        return False
    pairs = []
    for key in sorted(params):
        if key == "sig":
            continue
        value = params.get(key)
        if isinstance(value, (list, tuple)):
            value = value[-1] if value else ""
        pairs.append(f"{key}={value if value is not None else ''}")
    calculated = hashlib.md5(("".join(pairs) + secret).encode("utf-8")).hexdigest()
    return hmac.compare_digest(calculated, received)


async def _premium_target(plan_code: str) -> dict[str, Any] | None:
    return await get_purchase_target(f"vox:premium:{str(plan_code or 'monthly')}")


async def create_vk_payment_intent(
    *, user_id: int, vk_user_id: int, kind: str, target_id: int | str | None = None,
    promo_code: str | None = None, amount_stars: int | None = None, book_id: int | None = None,
) -> dict[str, Any]:
    await ensure_vk_payment_schema()
    kind = str(kind or "").strip().lower()
    target: dict[str, Any] | None = None
    canonical_payload = ""
    if kind == "premium":
        plan_code = str(target_id or "monthly")
        target = await _premium_target(plan_code)
        canonical_payload = f"vox:premium:{plan_code}"
    elif kind == "wallet_topup":
        amount = int(target_id or amount_stars or 0)
        pay_target = await build_pay_target("wallet_topup", amount, int(user_id))
        if pay_target:
            target = {
                "kind": pay_target.kind, "target_id": pay_target.target_id,
                "title": pay_target.title, "description": pay_target.description,
                "amount_stars": pay_target.amount_stars,
            }
            canonical_payload = pay_target.payload
    else:
        if target_id is None or not str(target_id).isdigit():
            raise VKPaymentError("Не удалось определить покупку.")
        extra_amount = int(book_id) if kind == "graphic_volume" and book_id else amount_stars
        pay_target = await build_pay_target(
            kind, int(target_id), int(user_id), promo_code=promo_code, amount_stars=extra_amount
        )
        if pay_target:
            target = {
                "kind": pay_target.kind, "target_id": pay_target.target_id,
                "title": pay_target.title, "description": pay_target.description,
                "amount_stars": pay_target.amount_stars,
            }
            canonical_payload = pay_target.payload
    if not target:
        raise VKPaymentError("Эта покупка сейчас недоступна.")
    price_stars = int(target.get("amount_stars") or 0)
    if price_stars <= 0:
        raise VKPaymentError("Материал бесплатный или доступ уже открыт.")
    if kind not in {"premium", "wallet_topup"}:
        conflict = await get_purchase_conflict(int(user_id), canonical_payload)
        if conflict:
            raise VKPaymentError("Этот доступ уже открыт. Повторная оплата не требуется.")
    item_key = "vx_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")[:24]
    votes = votes_for_stars(price_stars)
    now_dt = datetime.now(timezone.utc)
    expires = now_dt + timedelta(minutes=20)
    async with connect() as db:
        await db.execute(
            """INSERT INTO vk_payment_intents(
                   item_key,user_id,vk_user_id,kind,canonical_payload,title,description,
                   amount_stars,amount_votes,status,created_at,expires_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?)""",
            (
                item_key, int(user_id), int(vk_user_id), kind, canonical_payload,
                str(target.get("title") or "VoxLyra")[:120], str(target.get("description") or "")[:500],
                price_stars, votes, now_dt.isoformat(), expires.isoformat(), now_dt.isoformat(),
            ),
        )
        await db.commit()
    return {
        "item_key": item_key, "kind": kind, "title": str(target.get("title") or "VoxLyra"),
        "amount_stars": price_stars, "amount_votes": votes, "expires_at": expires.isoformat(),
        "status": "active",
    }


async def get_vk_payment_intent(item_key: str, *, user_id: int | None = None) -> dict[str, Any] | None:
    await ensure_vk_payment_schema()
    async with connect() as db:
        sql = "SELECT * FROM vk_payment_intents WHERE item_key=?"
        args: tuple[Any, ...] = (str(item_key),)
        if user_id is not None:
            sql += " AND user_id=?"
            args = (str(item_key), int(user_id))
        cur = await db.execute(sql, args)
        row = await cur.fetchone()
    return {key: row[key] for key in row.keys()} if row else None


async def _mark_purchase_platform(charge_id: str, votes: int) -> int | None:
    await ensure_vk_payment_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM purchases WHERE telegram_payment_charge_id=? ORDER BY id DESC LIMIT 1",
            (str(charge_id),),
        )
        row = await cur.fetchone()
        if row:
            await db.execute(
                """UPDATE purchases SET payment_platform='vk',amount_platform=?,platform_currency='votes',
                                      funding_method=CASE WHEN COALESCE(funding_method,'telegram')='wallet' THEN funding_method ELSE 'vk_votes' END
                   WHERE id=?""",
                (int(votes), int(row["id"])),
            )
            await db.execute(
                "UPDATE reader_wallet_transactions SET source_type='vk_votes' WHERE source_id=? AND transaction_type='topup'",
                (str(int(row["id"])),),
            )
            await db.commit()
            return int(row["id"])
    return None


async def charge_vk_intent(*, item_key: str, order_id: str, vk_user_id: int, item_price: int, test_mode: bool) -> dict[str, Any]:
    await ensure_vk_payment_schema()
    intent = await get_vk_payment_intent(item_key)
    if not intent:
        raise VKPaymentError("Товар не найден или срок счёта истёк.")
    if int(intent["vk_user_id"]) != int(vk_user_id):
        raise VKPaymentError("Счёт принадлежит другому пользователю.")
    if str(intent["status"]) == "paid" and str(intent.get("order_id") or "") == str(order_id):
        return {"order_id": str(order_id), "app_order_id": str(intent.get("purchase_id") or order_id), "duplicate": True}
    if str(intent["expires_at"]) <= datetime.now(timezone.utc).isoformat():
        raise VKPaymentError("Срок действия счёта истёк.")
    expected_votes = int(intent["amount_votes"])
    if int(item_price) != expected_votes:
        raise VKPaymentError("Цена изменилась. Откройте покупку заново.")
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM vk_payment_orders WHERE order_id=? AND test_mode=?",
            (str(order_id), 1 if test_mode else 0),
        )
        existing = await cur.fetchone()
        if existing and str(existing["status"]) == "paid":
            return {"order_id": str(order_id), "app_order_id": str(existing["purchase_id"] or order_id), "duplicate": True}

    user_id = int(intent["user_id"])
    amount_stars = int(intent["amount_stars"])
    canonical = str(intent["canonical_payload"])
    kind = str(intent["kind"])
    charge_id = f"vk:{'test' if test_mode else 'live'}:{order_id}"
    purchase_id: int | None = None
    if kind == "wallet_topup":
        result = await credit_wallet_topup(
            user_id=user_id, amount_stars=amount_stars,
            telegram_payment_charge_id=charge_id, payload=canonical,
        )
        purchase_id = int(result.get("purchase_id") or 0) or None
        await _mark_purchase_platform(charge_id, expected_votes)
    elif kind == "premium":
        target = await get_purchase_target(canonical)
        if not target:
            raise VKPaymentError("Тариф Premium больше недоступен.")
        await activate_premium_subscription(
            user_id=user_id, plan_code=str(target.get("plan_code") or "monthly"),
            amount_stars=amount_stars, telegram_payment_charge_id=charge_id,
            subscription_expiration_date=None, is_recurring=False, is_first_recurring=False,
            invoice_payload=canonical,
        )
        purchase_id = await _mark_purchase_platform(charge_id, expected_votes)
    else:
        purchase_id = await create_paid_purchase(
            user_id=user_id, payload=canonical, amount_stars=amount_stars,
            telegram_payment_charge_id=charge_id,
        )
        await _mark_purchase_platform(charge_id, expected_votes)

    now = utc_now()
    async with connect() as db:
        await db.execute(
            """INSERT INTO vk_payment_orders(order_id,test_mode,item_key,user_id,status,purchase_id,response_json,created_at,updated_at)
               VALUES(?,?,?,?, 'paid', ?, '{}', ?, ?)
               ON CONFLICT(order_id,test_mode) DO UPDATE SET
                 status='paid',purchase_id=excluded.purchase_id,updated_at=excluded.updated_at""",
            (str(order_id), 1 if test_mode else 0, str(item_key), user_id, purchase_id, now, now),
        )
        await db.execute(
            "UPDATE vk_payment_intents SET status='paid',order_id=?,purchase_id=?,updated_at=? WHERE item_key=?",
            (str(order_id), purchase_id, now, str(item_key)),
        )
        await db.commit()
    return {"order_id": str(order_id), "app_order_id": str(purchase_id or order_id), "duplicate": False}


async def refund_vk_order(order_id: str, *, test_mode: bool) -> None:
    await ensure_vk_payment_schema()
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """SELECT vpo.item_key,vpo.purchase_id,vpi.kind,vpi.user_id,vpi.amount_stars,
                      p.telegram_payment_charge_id,p.purchase_kind
               FROM vk_payment_orders vpo
               JOIN vk_payment_intents vpi ON vpi.item_key=vpo.item_key
               LEFT JOIN purchases p ON p.id=vpo.purchase_id
               WHERE vpo.order_id=? AND vpo.test_mode=?""",
            (str(order_id), 1 if test_mode else 0),
        )
        row = await cur.fetchone()
        if not row:
            await db.rollback()
            return
        purchase_id = int(row["purchase_id"] or 0)
        kind = str(row["kind"] or "")
        user_id = int(row["user_id"] or 0)
        charge_id = str(row["telegram_payment_charge_id"] or f"vk:{'test' if test_mode else 'live'}:{order_id}")
        if purchase_id:
            await db.execute("UPDATE purchases SET status='refunded' WHERE id=?", (purchase_id,))
            await db.execute("UPDATE purchase_access_claims SET status='released',updated_at=? WHERE purchase_id=?", (now, purchase_id))
        if kind == "premium":
            await db.execute(
                """UPDATE premium_subscriptions SET status='refunded',auto_renew=0,
                          canceled_at=COALESCE(canceled_at,?),updated_at=?
                   WHERE telegram_payment_charge_id=?""",
                (now, now, charge_id),
            )
            if purchase_id:
                await db.execute(
                    "UPDATE premium_author_pools SET status='refunded',updated_at=? WHERE purchase_id=? AND status='pending'",
                    (now, purchase_id),
                )
        elif kind == "wallet_topup" and purchase_id:
            cur = await db.execute(
                "SELECT * FROM wallet_topups WHERE purchase_id=? AND status='paid' LIMIT 1",
                (purchase_id,),
            )
            topup = await cur.fetchone()
            if topup:
                amount = int(topup["amount_stars"] or 0)
                buyer_points = int(topup["buyer_bonus_points"] or 0)
                referrer_id = int(topup["referrer_user_id"] or 0)
                referrer_points = int(topup["referrer_bonus_points"] or 0)
                # A refund can arrive after some wallet funds were spent. Keeping
                # the exact negative correction is safer than silently granting
                # free value; future spending is blocked until the balance recovers.
                await db.execute(
                    "UPDATE reader_wallets SET balance_stars=balance_stars-?,updated_at=? WHERE user_id=?",
                    (amount, now, user_id),
                )
                await db.execute(
                    "INSERT INTO reader_wallet_transactions(user_id,amount_stars,transaction_type,source_type,source_id,metadata_json,created_at) VALUES(?,?,'refund','vk_votes',?,?,?)",
                    (user_id, -amount, str(purchase_id), json.dumps({"order_id": str(order_id)}, ensure_ascii=False), now),
                )
                if buyer_points:
                    await db.execute("UPDATE bonus_wallets SET balance=balance-?,updated_at=? WHERE user_id=?", (buyer_points, now, user_id))
                if referrer_id and referrer_points:
                    await db.execute("UPDATE bonus_wallets SET balance=balance-?,updated_at=? WHERE user_id=?", (referrer_points, now, referrer_id))
                await db.execute("UPDATE wallet_topups SET status='refunded',refunded_at=? WHERE id=?", (now, int(topup["id"])))
        await db.execute(
            "UPDATE vk_payment_orders SET status='refunded',updated_at=? WHERE order_id=? AND test_mode=?",
            (now, str(order_id), 1 if test_mode else 0),
        )
        await db.execute("UPDATE vk_payment_intents SET status='refunded',updated_at=? WHERE item_key=?", (now, row["item_key"]))
        await db.commit()


def vk_callback_error(code: int, message: str) -> dict[str, Any]:
    return {"error": {"error_code": int(code), "error_msg": str(message)[:180], "critical": True}}
