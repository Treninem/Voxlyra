from __future__ import annotations

import sqlite3
from typing import Any

from app.db import connect, utc_now

AUTO_MODERATION_SETTING_KEY = "auto_moderation_enabled"
PROTECTED_CATEGORIES = {
    "external_link",
    "external_payment",
    "promotion",
    "damaged_text",
    "profanity_underage",
}
MIN_LEARNING_DECISIONS = 5
TRUST_APPROVAL_RATIO = 0.85


async def ensure_moderation_learning_schema() -> None:
    async with connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS moderation_learning_categories (
                category TEXT PRIMARY KEY,
                approved_count INTEGER NOT NULL DEFAULT 0,
                rejected_count INTEGER NOT NULL DEFAULT 0,
                last_decision TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS moderation_learning_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                actor_user_id INTEGER,
                decision TEXT NOT NULL,
                category TEXT NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_moderation_learning_log_book
                ON moderation_learning_log(book_id, created_at DESC);
            INSERT INTO settings(key, value, updated_at)
            VALUES('auto_moderation_enabled', '1', '')
            ON CONFLICT(key) DO NOTHING;
            """
        )
        await db.commit()


async def is_auto_moderation_enabled() -> bool:
    await ensure_moderation_learning_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT value FROM settings WHERE key=? LIMIT 1",
            (AUTO_MODERATION_SETTING_KEY,),
        )
        row = await cur.fetchone()
    return str(row["value"] if row else "1").strip().lower() not in {"0", "false", "off", "no"}


async def set_auto_moderation_enabled(enabled: bool) -> bool:
    await ensure_moderation_learning_schema()
    value = "1" if bool(enabled) else "0"
    async with connect() as db:
        await db.execute(
            """INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (AUTO_MODERATION_SETTING_KEY, value, utc_now()),
        )
        await db.commit()
    return bool(enabled)


async def record_moderation_decision(
    book_id: int,
    decision: str,
    *,
    actor_user_id: int | None = None,
    note: str = "",
) -> int:
    """Запоминает ручное решение по категориям, найденным в книге.

    Защищённые категории учитываются в статистике, но никогда не могут быть
    автоматически разрешены обучением.
    """
    normalized = str(decision or "").strip().lower()
    if normalized not in {"approve", "reject"}:
        raise ValueError("Неизвестное решение автомодерации")
    await ensure_moderation_learning_schema()
    async with connect() as db:
        try:
            cur = await db.execute(
                """SELECT DISTINCT category FROM book_moderation_findings
                   WHERE book_id=? AND status='open'""",
                (int(book_id),),
            )
            categories = {str(row["category"] or "").strip() for row in await cur.fetchall()}
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            categories = set()
        categories.discard("")
        if not categories:
            categories.add("clean_review")
        now = utc_now()
        for category in sorted(categories):
            approved = 1 if normalized == "approve" else 0
            rejected = 1 if normalized == "reject" else 0
            await db.execute(
                """INSERT INTO moderation_learning_categories(
                       category, approved_count, rejected_count, last_decision, updated_at
                   ) VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(category) DO UPDATE SET
                       approved_count=approved_count+excluded.approved_count,
                       rejected_count=rejected_count+excluded.rejected_count,
                       last_decision=excluded.last_decision,
                       updated_at=excluded.updated_at""",
                (category, approved, rejected, normalized, now),
            )
            await db.execute(
                """INSERT INTO moderation_learning_log(
                       book_id, actor_user_id, decision, category, note, created_at
                   ) VALUES(?, ?, ?, ?, ?, ?)""",
                (int(book_id), actor_user_id, normalized, category, str(note or "")[:1000], now),
            )
        await db.commit()
    return len(categories)


async def get_trusted_moderation_categories() -> set[str]:
    await ensure_moderation_learning_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT category, approved_count, rejected_count FROM moderation_learning_categories"
        )
        rows = await cur.fetchall()
    trusted: set[str] = set()
    for row in rows:
        category = str(row["category"] or "")
        if category in PROTECTED_CATEGORIES:
            continue
        approved = int(row["approved_count"] or 0)
        rejected = int(row["rejected_count"] or 0)
        total = approved + rejected
        if total >= MIN_LEARNING_DECISIONS and approved / max(1, total) >= TRUST_APPROVAL_RATIO:
            trusted.add(category)
    return trusted


async def get_moderation_learning_summary() -> dict[str, Any]:
    await ensure_moderation_learning_schema()
    enabled = await is_auto_moderation_enabled()
    trusted = await get_trusted_moderation_categories()
    async with connect() as db:
        cur = await db.execute(
            """SELECT COALESCE(SUM(approved_count),0) AS approved,
                      COALESCE(SUM(rejected_count),0) AS rejected,
                      COUNT(*) AS categories
               FROM moderation_learning_categories"""
        )
        row = await cur.fetchone()
    return {
        "enabled": enabled,
        "approved": int(row["approved"] or 0),
        "rejected": int(row["rejected"] or 0),
        "categories": int(row["categories"] or 0),
        "trusted_categories": sorted(trusted),
        "minimum_decisions": MIN_LEARNING_DECISIONS,
        "approval_ratio": TRUST_APPROVAL_RATIO,
    }
