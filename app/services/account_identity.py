from __future__ import annotations

import hashlib
import json
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

            CREATE TABLE IF NOT EXISTS account_merge_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_user_id INTEGER NOT NULL,
                secondary_user_id INTEGER NOT NULL,
                strategy TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                current_platform TEXT NOT NULL,
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(canonical_user_id) REFERENCES users(id) ON DELETE RESTRICT,
                FOREIGN KEY(secondary_user_id) REFERENCES users(id) ON DELETE RESTRICT
            );
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


async def _account_summary(db: Any, user_id: int) -> dict[str, int]:
    result: dict[str, int] = {}
    for label, table in (
        ("purchases", "purchases"), ("books", "bookmarks"),
        ("reading", "reading_progress"), ("audio", "listening_progress"),
        ("comics", "graphic_reading_progress"), ("comments", "comments"),
    ):
        try:
            row = await (await db.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=?", (int(user_id),))).fetchone()
            result[label] = int(row["n"] or 0)
        except Exception:
            result[label] = 0
    return result


async def _merge_reader_data(db: Any, source_id: int, target_id: int) -> None:
    """Move cross-platform reader data without duplicating paid access."""
    # Progress conflicts keep the furthest known position/page.
    progress = {
        "reading_progress": ("chapter_id", "position_percent"),
        "listening_progress": ("audio_chapter_id", "position_seconds"),
        "graphic_reading_progress": ("graphic_chapter_id", "page_number"),
    }
    for table, (key, value) in progress.items():
        try:
            await db.execute(
                f"UPDATE {table} SET {value}=MAX({value}, COALESCE((SELECT s.{value} FROM {table} s WHERE s.user_id=? AND s.{key}={table}.{key}),0)) WHERE user_id=?",
                (source_id, target_id),
            )
        except Exception:
            pass

    try:
        await db.execute(
            "UPDATE user_achievements SET progress_value=MAX(progress_value, COALESCE((SELECT s.progress_value FROM user_achievements s WHERE s.user_id=? AND s.achievement_code=user_achievements.achievement_code),0)) WHERE user_id=?",
            (source_id, target_id),
        )
    except Exception:
        pass

    # Wallet balances are additive; the transaction ledger is moved below.
    try:
        source_wallet = await (await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (source_id,))).fetchone()
        if source_wallet:
            await db.execute(
                "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET balance_stars=reader_wallets.balance_stars+excluded.balance_stars,updated_at=excluded.updated_at",
                (target_id, int(source_wallet["balance_stars"] or 0), utc_now(), utc_now()),
            )
            await db.execute("DELETE FROM reader_wallets WHERE user_id=?", (source_id,))
    except Exception:
        pass

    # Every user-owned table is discovered from SQLite. UPDATE OR IGNORE moves
    # non-conflicting rows; an existing canonical row wins a duplicate key.
    tables = await (await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'" )).fetchall()
    protected = {"users", "external_identities", "account_link_codes", "reader_wallets", "author_profiles"}
    for table_row in tables:
        table = str(table_row["name"])
        if table in protected or not table.replace("_", "").isalnum():
            continue
        columns = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
        if "user_id" not in {str(row["name"]) for row in columns}:
            continue
        await db.execute(f"UPDATE OR IGNORE {table} SET user_id=? WHERE user_id=?", (target_id, source_id))
        # Remaining duplicates represent the same logical item already present
        # on the chosen primary account. Paid purchase rows normally have no
        # user-scoped UNIQUE key and are moved, never deleted.
        if table not in {"purchases", "reader_wallet_transactions", "wallet_topups"}:
            await db.execute(f"DELETE FROM {table} WHERE user_id=?", (source_id,))

    # Preserve the author cabinet. If both sides have one, move source books to
    # the target author profile and retain only the chosen public profile.
    source_author = await (await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (source_id,))).fetchone()
    target_author = await (await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (target_id,))).fetchone()
    if source_author and target_author:
        source_author_id, target_author_id = int(source_author["id"]), int(target_author["id"])
        author_tables = await (await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'" )).fetchall()
        for table_row in author_tables:
            table = str(table_row["name"])
            if table == "author_profiles" or not table.replace("_", "").isalnum():
                continue
            columns = await (await db.execute(f"PRAGMA table_info({table})")).fetchall()
            if "author_id" in {str(row["name"]) for row in columns}:
                await db.execute(f"UPDATE OR IGNORE {table} SET author_id=? WHERE author_id=?", (target_author_id, source_author_id))
        # Never delete the secondary author profile here: a future schema may
        # contain a protected row that could not be reassigned. Keeping the
        # dormant row makes the merge recoverable and prevents cascade loss.
    elif source_author:
        await db.execute("UPDATE author_profiles SET user_id=? WHERE id=?", (target_id, int(source_author["id"])))


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


async def consume_link_code(*, current_user_id: int, current_platform: str, external_id: int | str, code: str, strategy: str = "") -> dict[str, Any]:
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

        accounts_differ = source_user_id != int(current_user_id)
        strategy = str(strategy or "").strip().lower()
        if accounts_differ and strategy not in {"merge", "keep_telegram", "keep_vk"}:
            return {
                "ok": True, "requires_decision": True,
                "source_platform": source_platform, "current_platform": platform,
                "source": await _account_summary(db, source_user_id),
                "current": await _account_summary(db, int(current_user_id)),
            }

        # Serialize the final decision. The same single-use code cannot be
        # consumed concurrently by two devices with different strategies.
        await db.execute("BEGIN IMMEDIATE")
        locked = await (await db.execute(
            "SELECT used_at,expires_at FROM account_link_codes WHERE code_hash=?", (_code_hash(normalized),)
        )).fetchone()
        if not locked or locked["used_at"] is not None or str(locked["expires_at"]) <= now:
            raise AccountLinkError("Код уже использован другим устройством или истёк.")

        # Telegram existed before the VK adapter and may already contain years of
        # purchases/progress. Whichever direction the code is entered, a verified
        # Telegram account is therefore the canonical row. This prevents a user
        # who generated the code in fresh VK from accidentally hiding the older
        # Telegram library behind a synthetic VK user.
        telegram_user_id = int(current_user_id) if platform == "telegram" else source_user_id
        vk_user_id = int(current_user_id) if platform == "vk" else source_user_id
        canonical_user_id = telegram_user_id if strategy != "keep_vk" else vk_user_id
        secondary_user_id = vk_user_id if canonical_user_id == telegram_user_id else telegram_user_id
        snapshot = {
            "source": await _account_summary(db, source_user_id),
            "current": await _account_summary(db, int(current_user_id)),
        }
        if strategy == "merge" and accounts_differ:
            await _merge_reader_data(db, secondary_user_id, canonical_user_id)

        if platform == "telegram" and source_platform == "vk":
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
        # Whichever account was selected, both verified identities must point to it.
        await db.execute(
            "UPDATE external_identities SET user_id=?,updated_at=? WHERE user_id IN (?,?) AND platform IN ('telegram','vk')",
            (canonical_user_id, now, source_user_id, int(current_user_id)),
        )
        await db.execute(
            "UPDATE account_link_codes SET used_at=? WHERE code_hash=? AND used_at IS NULL",
            (now, _code_hash(normalized)),
        )
        await db.execute(
            "INSERT INTO account_merge_events(canonical_user_id,secondary_user_id,strategy,source_platform,current_platform,snapshot_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (canonical_user_id, secondary_user_id, strategy or "link", source_platform, platform, json.dumps(snapshot, ensure_ascii=False), now),
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
