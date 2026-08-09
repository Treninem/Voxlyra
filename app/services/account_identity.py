from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import connect, get_user_by_id, utc_now


class AccountLinkError(ValueError):
    pass


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _code_hash(code: str) -> str:
    normalized = str(code or "").strip().upper().replace("-", "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def ensure_identity_schema() -> None:
    async with connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS external_identities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                external_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(platform, external_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_external_identities_user
                ON external_identities(user_id, platform);

            CREATE TABLE IF NOT EXISTS account_link_codes (
                code_hash TEXT PRIMARY KEY,
                source_user_id INTEGER NOT NULL,
                source_platform TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_account_link_codes_expiry
                ON account_link_codes(expires_at, used_at);
            """
        )
        await db.commit()


async def resolve_external_identity(platform: str, external_id: int | str, fallback_user_id: int) -> int:
    """Return the canonical VoxLyra user for a verified platform identity.

    Existing installations are migrated lazily: first verified login binds the
    legacy row as the canonical account. After a cross-platform link the same
    external identity resolves directly to the already existing canonical user.
    """
    await ensure_identity_schema()
    platform = str(platform or "").strip().lower()
    external = str(external_id or "").strip()
    if platform not in {"telegram", "vk"} or not external:
        raise AccountLinkError("Некорректная внешняя учётная запись.")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT user_id FROM external_identities WHERE platform=? AND external_id=?",
            (platform, external),
        )
        row = await cur.fetchone()
        if row:
            return int(row["user_id"])
        await db.execute(
            """INSERT INTO external_identities(platform,external_id,user_id,created_at,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(platform,external_id) DO NOTHING""",
            (platform, external, int(fallback_user_id), now, now),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT user_id FROM external_identities WHERE platform=? AND external_id=?",
            (platform, external),
        )
        row = await cur.fetchone()
        return int(row["user_id"] if row else fallback_user_id)


async def identity_status(user_id: int) -> dict[str, Any]:
    await ensure_identity_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT platform,external_id,created_at,updated_at FROM external_identities WHERE user_id=? ORDER BY platform",
            (int(user_id),),
        )
        rows = await cur.fetchall()
    identities = [
        {
            "platform": str(row["platform"]),
            "external_id": str(row["external_id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }
        for row in rows
    ]
    platforms = {item["platform"] for item in identities}
    return {
        "linked": "telegram" in platforms and "vk" in platforms,
        "telegram": "telegram" in platforms,
        "vk": "vk" in platforms,
        "identities": identities,
    }


async def create_link_code(user_id: int, platform: str, *, ttl_minutes: int = 10) -> dict[str, Any]:
    await ensure_identity_schema()
    # Eight characters are short enough to type and have enough entropy for a
    # ten-minute, single-use code. Only the SHA-256 hash is stored.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
    now_dt = _now_dt()
    expires_dt = now_dt + timedelta(minutes=max(3, min(30, int(ttl_minutes))))
    async with connect() as db:
        await db.execute(
            "DELETE FROM account_link_codes WHERE source_user_id=? OR expires_at<=? OR used_at IS NOT NULL",
            (int(user_id), now_dt.isoformat()),
        )
        await db.execute(
            """INSERT INTO account_link_codes(code_hash,source_user_id,source_platform,expires_at,used_at,created_at)
               VALUES(?,?,?,?,NULL,?)""",
            (_code_hash(code), int(user_id), str(platform or "unknown"), expires_dt.isoformat(), now_dt.isoformat()),
        )
        await db.commit()
    return {"code": code, "expires_at": expires_dt.isoformat(), "ttl_minutes": int(ttl_minutes)}


async def consume_link_code(*, current_user_id: int, current_platform: str, external_id: int | str, code: str) -> dict[str, Any]:
    await ensure_identity_schema()
    normalized = str(code or "").strip().upper().replace("-", "")
    if len(normalized) != 8:
        raise AccountLinkError("Код привязки должен состоять из 8 символов.")
    now = _now_dt().isoformat()
    async with connect() as db:
        cur = await db.execute(
            """SELECT * FROM account_link_codes
               WHERE code_hash=? AND used_at IS NULL AND expires_at>? LIMIT 1""",
            (_code_hash(normalized), now),
        )
        row = await cur.fetchone()
        if not row:
            raise AccountLinkError("Код не найден, уже использован или истёк.")
        source_user_id = int(row["source_user_id"])
        source_platform = str(row["source_platform"] or "")
        platform = str(current_platform or "").strip().lower()
        external = str(external_id or "").strip()
        if platform not in {"telegram", "vk"} or not external:
            raise AccountLinkError("Не удалось определить текущую платформу.")
        if source_platform == platform:
            raise AccountLinkError("Код нужно использовать на другой платформе.")

        # Telegram existed before the VK adapter and may already contain years of
        # purchases/progress. Whichever direction the code is entered, a verified
        # Telegram account is therefore the canonical row. This prevents a user
        # who generated the code in fresh VK from accidentally hiding the older
        # Telegram library behind a synthetic VK user.
        canonical_user_id = source_user_id
        if platform == "telegram" and source_platform == "vk":
            canonical_user_id = int(current_user_id)
            cur = await db.execute(
                "SELECT external_id FROM external_identities WHERE user_id=? AND platform='vk' ORDER BY id LIMIT 1",
                (source_user_id,),
            )
            source_vk = await cur.fetchone()
            if not source_vk:
                raise AccountLinkError("VK-аккаунт для этого кода не найден. Создайте новый код.")
            await db.execute(
                "UPDATE external_identities SET user_id=?,updated_at=? WHERE platform='vk' AND external_id=?",
                (canonical_user_id, now, str(source_vk["external_id"])),
            )
        else:
            # Normal flow: code created in Telegram and consumed in VK. Repoint
            # the verified VK identity to the existing Telegram user.
            await db.execute(
                """INSERT INTO external_identities(platform,external_id,user_id,created_at,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(platform,external_id) DO UPDATE SET
                        user_id=excluded.user_id, updated_at=excluded.updated_at""",
                (platform, external, canonical_user_id, now, now),
            )
        await db.execute(
            "UPDATE account_link_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
            (now, _code_hash(normalized)),
        )
        await db.commit()
    user = await get_user_by_id(canonical_user_id)
    return {
        "ok": True,
        "canonical_user_id": canonical_user_id,
        "telegram_id": int(user["telegram_id"]) if user else 0,
        "source_platform": source_platform,
        "linked_platform": platform,
    }
