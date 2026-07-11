import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from app.config import settings
from app.catalog_options import label_for
from app.permissions import DELEGABLE_PERMISSION_CODES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@asynccontextmanager
async def connect():
    db_path = settings.DATABASE_PATH
    folder = os.path.dirname(db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
    finally:
        await db.close()


_INIT_DB_LOCK = asyncio.Lock()
_INITIALIZED_DATABASES: set[str] = set()


async def _execute_schema_ddl(db: aiosqlite.Connection, sql: str) -> None:
    """Execute schema DDL and tolerate a concurrent identical ADD COLUMN.

    Bot polling and FastAPI lifespan start together. On an older database both
    startup paths can discover the same missing column before either connection
    commits it. SQLite then raises ``duplicate column name`` for the slower path.
    Rechecking the schema is unnecessary when SQLite explicitly reports that
    another initializer already added the column. Other migration errors remain
    fatal.
    """
    try:
        await db.execute(sql)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


async def init_db() -> None:
    """Initialize one database path once per process, safely under concurrency."""
    database_key = os.path.abspath(os.path.expanduser(str(settings.DATABASE_PATH)))
    if database_key in _INITIALIZED_DATABASES:
        return
    async with _INIT_DB_LOCK:
        if database_key in _INITIALIZED_DATABASES:
            return
        await _init_db_impl()
        _INITIALIZED_DATABASES.add(database_key)


async def _init_db_impl() -> None:
    async with connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                full_name TEXT,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS author_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                pen_name TEXT NOT NULL,
                bio TEXT,
                avatar_file_id TEXT,
                contacts TEXT,
                country TEXT,
                is_adult INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                trust_level INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS admin_staff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                added_by_user_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(added_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS admin_permissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL,
                permission_code TEXT NOT NULL,
                allowed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(admin_id, permission_code),
                FOREIGN KEY(admin_id) REFERENCES admin_staff(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                theme TEXT NOT NULL DEFAULT 'system',
                font_size TEXT NOT NULL DEFAULT 'normal',
                notifications INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS book_option_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                option_group TEXT NOT NULL,
                option_code TEXT NOT NULL,
                option_label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(book_id, option_group, option_code),
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reader_ad_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                source_book_id INTEGER,
                source_chapter_id INTEGER,
                promoted_book_id INTEGER NOT NULL,
                placement TEXT NOT NULL,
                event_type TEXT NOT NULL DEFAULT 'impression',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(source_book_id) REFERENCES books(id) ON DELETE SET NULL,
                FOREIGN KEY(source_chapter_id) REFERENCES chapters(id) ON DELETE SET NULL,
                FOREIGN KEY(promoted_book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                age_limit TEXT DEFAULT '16+',
                writing_status TEXT NOT NULL DEFAULT 'writing',
                publication_status TEXT NOT NULL DEFAULT 'draft',
                cover_file_id TEXT,
                cover_path TEXT,
                normalized_title TEXT,
                source_file_hash TEXT,
                source_file_name TEXT,
                duplicate_override INTEGER NOT NULL DEFAULT 0,
                allow_download INTEGER NOT NULL DEFAULT 0,
                has_audio INTEGER NOT NULL DEFAULT 0,
                pricing_type TEXT NOT NULL DEFAULT 'free',
                price_stars INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                text TEXT NOT NULL,
                is_free INTEGER NOT NULL DEFAULT 1,
                price_stars INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(book_id, number),
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audio_chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_id INTEGER,
                number INTEGER NOT NULL,
                title TEXT NOT NULL,
                file_id TEXT,
                file_path TEXT,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                narrator TEXT,
                source_filename TEXT,
                mime_type TEXT,
                file_size INTEGER NOT NULL DEFAULT 0,
                sample_seconds INTEGER NOT NULL DEFAULT 60,
                is_free INTEGER NOT NULL DEFAULT 0,
                price_stars INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS listening_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                audio_chapter_id INTEGER NOT NULL,
                position_seconds INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, audio_chapter_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(audio_chapter_id) REFERENCES audio_chapters(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reading_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                position_percent INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, chapter_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tts_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                voice_code TEXT NOT NULL DEFAULT 'anna',
                position_seconds INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, chapter_id, voice_code),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                book_id INTEGER,
                chapter_id INTEGER,
                audio_chapter_id INTEGER,
                amount_stars INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'paid',
                telegram_payment_charge_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE SET NULL,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE SET NULL,
                FOREIGN KEY(audio_chapter_id) REFERENCES audio_chapters(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'reading',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, book_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                text TEXT,
                status TEXT NOT NULL DEFAULT 'published',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, book_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'published',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                before_value TEXT,
                after_value TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS author_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER,
                purchase_id INTEGER,
                source_type TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                gross_stars INTEGER NOT NULL DEFAULT 0,
                commission_percent INTEGER NOT NULL DEFAULT 0,
                commission_stars INTEGER NOT NULL DEFAULT 0,
                net_stars INTEGER NOT NULL DEFAULT 0,
                hold_days INTEGER NOT NULL DEFAULT 14,
                available_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'held',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE SET NULL,
                FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS refund_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purchase_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                handled_by_user_id INTEGER,
                moderator_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY(handled_by_user_id) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS bonus_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                balance INTEGER NOT NULL DEFAULT 0,
                last_daily_bonus_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS bonus_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                source_type TEXT,
                source_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ad_campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                placement TEXT NOT NULL DEFAULT 'reader_both',
                budget_units INTEGER NOT NULL DEFAULT 0,
                spent_units INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS ad_campaign_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id INTEGER NOT NULL,
                user_id INTEGER,
                source_book_id INTEGER,
                source_chapter_id INTEGER,
                event_type TEXT NOT NULL DEFAULT 'impression',
                created_at TEXT NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES ad_campaigns(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
                FOREIGN KEY(source_book_id) REFERENCES books(id) ON DELETE SET NULL,
                FOREIGN KEY(source_chapter_id) REFERENCES chapters(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                author_id INTEGER NOT NULL,
                book_id INTEGER NOT NULL,
                code TEXT NOT NULL UNIQUE,
                discount_percent INTEGER NOT NULL DEFAULT 0,
                max_uses INTEGER NOT NULL DEFAULT 100,
                used_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE,
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
            );
            """
        )
        await _ensure_book_columns(db)
        await _ensure_audio_columns(db)
        await _ensure_defaults(db)
        await _ensure_stage9_schema(db)
        await _ensure_stage10_schema(db)
        await _ensure_stage11_schema(db)
        await _ensure_v176_schema(db)
        await _ensure_v179_schema(db)
        await _ensure_v184_schema(db)
        await _ensure_v185_schema(db)
        await _ensure_v191_schema(db)
        await _ensure_v193_schema(db)
        await _ensure_v196_schema(db)
        await _ensure_v197_schema(db)
        await _ensure_v198_schema(db)
        await _ensure_v199_schema(db)
        await _ensure_v1100_schema(db)
        await db.commit()



async def _ensure_stage11_schema(db: aiosqlite.Connection) -> None:
    """Мягкие миграции этапа 11: юридические документы, согласия и финальные правила."""
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS legal_acceptances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            doc_code TEXT NOT NULL,
            doc_version TEXT NOT NULL,
            accepted_at TEXT NOT NULL,
            UNIQUE(user_id, doc_code, doc_version),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    for key, value in {
        "legal_terms_version": "2026-07-01",
        "legal_privacy_version": "2026-07-01",
        "legal_refunds_version": "2026-07-01",
        "legal_copyright_version": "2026-07-01",
        "legal_authors_version": "2026-07-01",
        "legal_content_version": "2026-07-01",
        "support_notice": "Опишите проблему одним сообщением. Для платежей укажите книгу, главу и дату оплаты.",
        "copyright_contact_notice": "Для жалобы правообладателя укажите материал и подтверждение прав.",
    }.items():
        await db.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value, now),
        )


async def _ensure_book_columns(db: aiosqlite.Connection) -> None:
    """Мягкая миграция для баз, созданных на прошлых этапах."""
    cur = await db.execute("PRAGMA table_info(books)")
    existing = {row[1] for row in await cur.fetchall()}
    migrations = {
        "writing_status": "ALTER TABLE books ADD COLUMN writing_status TEXT NOT NULL DEFAULT 'writing'",
        "publication_status": "ALTER TABLE books ADD COLUMN publication_status TEXT NOT NULL DEFAULT 'draft'",
        "cover_file_id": "ALTER TABLE books ADD COLUMN cover_file_id TEXT",
        "cover_path": "ALTER TABLE books ADD COLUMN cover_path TEXT",
        "normalized_title": "ALTER TABLE books ADD COLUMN normalized_title TEXT",
        "source_file_hash": "ALTER TABLE books ADD COLUMN source_file_hash TEXT",
        "source_file_name": "ALTER TABLE books ADD COLUMN source_file_name TEXT",
        "duplicate_override": "ALTER TABLE books ADD COLUMN duplicate_override INTEGER NOT NULL DEFAULT 0",
        "pricing_type": "ALTER TABLE books ADD COLUMN pricing_type TEXT NOT NULL DEFAULT 'free'",
        "price_stars": "ALTER TABLE books ADD COLUMN price_stars INTEGER NOT NULL DEFAULT 0",
        "content_type": "ALTER TABLE books ADD COLUMN content_type TEXT NOT NULL DEFAULT 'book'",
        "reading_mode": "ALTER TABLE books ADD COLUMN reading_mode TEXT NOT NULL DEFAULT 'ltr'",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await _execute_schema_ddl(db, sql)


async def _ensure_audio_columns(db: aiosqlite.Connection) -> None:
    """Мягкая миграция аудиотаблиц для баз прошлых этапов."""
    cur = await db.execute("PRAGMA table_info(audio_chapters)")
    existing = {row[1] for row in await cur.fetchall()}
    migrations = {
        "source_filename": "ALTER TABLE audio_chapters ADD COLUMN source_filename TEXT",
        "mime_type": "ALTER TABLE audio_chapters ADD COLUMN mime_type TEXT",
        "file_size": "ALTER TABLE audio_chapters ADD COLUMN file_size INTEGER NOT NULL DEFAULT 0",
        "sample_seconds": "ALTER TABLE audio_chapters ADD COLUMN sample_seconds INTEGER NOT NULL DEFAULT 60",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await _execute_schema_ddl(db, sql)


async def _ensure_defaults(db: aiosqlite.Connection) -> None:
    now = utc_now()
    defaults = {
        "commission_books": "20",
        "commission_audio": "20",
        "commission_donations": "10",
        "hold_days_new_author": "30",
        "hold_days_default": "14",
        "hold_days_trusted": "7",
        "refund_window_days": "14",
        "reserve_percent": "10",
        "reader_ads_enabled": "1",
        "reader_ads_top": "1",
        "reader_ads_bottom": "1",
        "reader_ads_label": "Похоже по жанру и сюжету",
        "daily_bonus_amount": "3",
        "ad_impression_cost": "1",
        "ad_click_cost": "3",
        "promo_default_max_uses": "100",
        "channel_promotion_price_stars": "50",
        "channel_promotion_cooldown_days": "30",
    }
    for key, value in defaults.items():
        await db.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value, now),
        )


async def _ensure_stage9_schema(db: aiosqlite.Connection) -> None:
    """Мягкие миграции этапа 9: реклама Stars, промокоды, рефералы, поиск и блокировки."""
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS promo_uses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            purchase_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(promo_code_id, user_id, purchase_id),
            FOREIGN KEY(promo_code_id) REFERENCES promo_codes(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_user_id INTEGER NOT NULL,
            referred_user_id INTEGER NOT NULL UNIQUE,
            bonus_given INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(referrer_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(referred_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ad_budget_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            purchase_id INTEGER,
            amount_stars INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(campaign_id) REFERENCES ad_campaigns(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
        );
        """
    )
    cur = await db.execute("PRAGMA table_info(purchases)")
    existing = {row[1] for row in await cur.fetchall()}
    if "payload" not in existing:
        await _execute_schema_ddl(db, "ALTER TABLE purchases ADD COLUMN payload TEXT")
    if "purchase_kind" not in existing:
        await _execute_schema_ddl(db, "ALTER TABLE purchases ADD COLUMN purchase_kind TEXT NOT NULL DEFAULT 'content'")
    cur = await db.execute("PRAGMA table_info(complaints)")
    existing = {row[1] for row in await cur.fetchall()}
    if "handled_by_user_id" not in existing:
        await _execute_schema_ddl(db, "ALTER TABLE complaints ADD COLUMN handled_by_user_id INTEGER")
    for key, value in {
        "ad_budget_min_stars": "10",
        "ad_budget_units_per_star": "10",
        "referral_reader_bonus": "10",
        "referral_friend_bonus": "10",
        "promo_max_discount": "100",
    }.items():
        await db.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value, now),
        )


async def _ensure_stage10_schema(db: aiosqlite.Connection) -> None:
    """Мягкие миграции этапа 10: выплаты авторам, реквизиты, заморозки и удержания."""
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS author_payout_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL UNIQUE,
            method_type TEXT NOT NULL DEFAULT 'manual',
            details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS author_payout_freezes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL UNIQUE,
            is_active INTEGER NOT NULL DEFAULT 1,
            reason TEXT,
            created_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE,
            FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS author_payout_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL,
            author_user_id INTEGER NOT NULL,
            amount_stars INTEGER NOT NULL,
            method_type TEXT NOT NULL,
            payout_details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            requested_at TEXT NOT NULL,
            handled_by_user_id INTEGER,
            handled_at TEXT,
            paid_at TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE,
            FOREIGN KEY(author_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(handled_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS author_payout_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payout_request_id INTEGER,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(payout_request_id) REFERENCES author_payout_requests(id) ON DELETE SET NULL,
            FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        """
    )
    for key, value in {
        "payout_min_stars": "100",
        "payout_default_method": "TON",
        "payout_manual_review": "1",
        "payout_freeze_on_complaint": "1",
        "payout_owner_note": "Выплата выполняется вручную после проверки удержаний, жалоб и реквизитов.",
    }.items():
        await db.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value, now),
        )


async def upsert_user(telegram_id: int, username: str | None, full_name: str | None) -> aiosqlite.Row:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO users(telegram_id, username, full_name, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                updated_at=excluded.updated_at
            """,
            (telegram_id, username, full_name, now, now),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("User was not saved")
        return row


async def get_user_by_telegram_id(telegram_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return await cur.fetchone()


async def get_user_by_username(username: str) -> aiosqlite.Row | None:
    username = username.strip().lstrip("@").lower()
    async with connect() as db:
        cur = await db.execute("SELECT * FROM users WHERE lower(username) = ?", (username,))
        return await cur.fetchone()


async def get_author_profile(user_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM author_profiles WHERE user_id = ?", (user_id,))
        return await cur.fetchone()


async def get_author_profile_by_telegram_id(telegram_id: int) -> aiosqlite.Row | None:
    user = await get_user_by_telegram_id(telegram_id)
    if not user:
        return None
    return await get_author_profile(user["id"])


async def create_author_profile(user_id: int, pen_name: str, bio: str, country: str, is_adult: bool) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO author_profiles(user_id, pen_name, bio, country, is_adult, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (user_id, pen_name, bio, country, 1 if is_adult else 0, now, now),
        )
        await db.commit()


async def update_author_profile(user_id: int, pen_name: str, bio: str, country: str, is_adult: bool) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            UPDATE author_profiles
            SET pen_name=?, bio=?, country=?, is_adult=?, updated_at=?
            WHERE user_id=?
            """,
            (pen_name, bio, country, 1 if is_adult else 0, now, user_id),
        )
        await db.commit()


async def update_book_description(book_id: int, author_user_id: int, description: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET description=?, updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (description, now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_book_price(book_id: int, author_user_id: int, pricing_type: str, price_stars: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET pricing_type=?, price_stars=?, updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (pricing_type, int(price_stars), now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_book_title(book_id: int, author_user_id: int, title: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET title=?, publication_status=CASE WHEN publication_status='published' THEN 'review' ELSE publication_status END, updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (title[:160], now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_book_age_limit(book_id: int, author_user_id: int, age_limit: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET age_limit=?, publication_status=CASE WHEN publication_status='published' THEN 'review' ELSE publication_status END, updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (age_limit, now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_book_writing_status(book_id: int, author_user_id: int, writing_status: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET writing_status=?, updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (writing_status, now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_book_download(book_id: int, author_user_id: int, allow_download: bool) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET allow_download=?, updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (1 if allow_download else 0, now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def soft_delete_book(book_id: int, author_user_id: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET publication_status='deleted', updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def create_book(author_id: int, title: str, description: str, age_limit: str, writing_status: str,
                      allow_download: bool, pricing_type: str, price_stars: int,
                      cover_file_id: str | None = None, content_type: str = "book",
                      reading_mode: str = "ltr") -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO books(author_id, title, description, age_limit, writing_status, publication_status,
                              cover_file_id, normalized_title, allow_download, pricing_type, price_stars,
                              content_type, reading_mode, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                author_id,
                title,
                description,
                age_limit,
                writing_status,
                cover_file_id,
                " ".join(str(title).casefold().replace("ё", "е").split()),
                1 if allow_download else 0,
                pricing_type,
                int(price_stars),
                str(content_type or "book"),
                str(reading_mode or "ltr"),
                now,
                now,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def update_book_cover_path(book_id: int, cover_path: str | None) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "UPDATE books SET cover_path=?, updated_at=? WHERE id=?",
            (cover_path, now, int(book_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_books_missing_cover_files(limit: int = 500) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, cover_file_id, cover_path
            FROM books
            WHERE cover_file_id IS NOT NULL
              AND TRIM(cover_file_id) <> ''
              AND publication_status <> 'deleted'
            ORDER BY id ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        return await cur.fetchall()


async def list_books_for_author(author_user_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*
            FROM books b
            JOIN author_profiles a ON a.id = b.author_id
            WHERE a.user_id = ? AND b.publication_status != 'deleted'
            ORDER BY b.id DESC
            """,
            (author_user_id,),
        )
        return await cur.fetchall()


async def list_author_books_with_counts(author_user_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status!='deleted') AS text_chapters_count,
                   (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status!='deleted') AS graphic_chapters_count,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status!='deleted') +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status!='deleted')) AS chapters_count,
                   (SELECT COALESCE(SUM(gc.pages_count), 0) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status!='deleted') AS graphic_pages_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status!='deleted') AS audio_count,
                   (SELECT COUNT(*) FROM purchases p WHERE p.status='paid' AND (
                       p.book_id=b.id OR
                       p.chapter_id IN (SELECT c2.id FROM chapters c2 WHERE c2.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT a2.id FROM audio_chapters a2 WHERE a2.book_id=b.id) OR
                       p.graphic_chapter_id IN (SELECT g2.id FROM graphic_chapters g2 WHERE g2.book_id=b.id)
                   )) AS purchase_count
            FROM books b
            JOIN author_profiles a ON a.id=b.author_id
            WHERE a.user_id=? AND b.publication_status!='deleted'
            ORDER BY b.updated_at DESC, b.id DESC
            """,
            (int(author_user_id),),
        )
        return await cur.fetchall()


async def update_author_book_fields(book_id: int, author_user_id: int, values: dict[str, Any]) -> bool:
    allowed = {
        "title", "description", "age_limit", "writing_status",
        "allow_download", "pricing_type", "price_stars",
        "normalized_title", "duplicate_override", "content_type", "reading_mode",
    }
    clean: dict[str, Any] = {key: value for key, value in values.items() if key in allowed}
    if not clean:
        return False
    if "title" in clean:
        clean["title"] = str(clean["title"]).strip()[:160]
        if len(clean["title"]) < 2:
            return False
        clean["normalized_title"] = " ".join(clean["title"].casefold().replace("ё", "е").split())
        clean["duplicate_override"] = 0
    if "description" in clean:
        clean["description"] = str(clean["description"]).strip()[:12000]
    if "age_limit" in clean:
        clean["age_limit"] = str(clean["age_limit"])
    if "writing_status" in clean:
        clean["writing_status"] = str(clean["writing_status"])
    if "allow_download" in clean:
        clean["allow_download"] = 1 if bool(clean["allow_download"]) else 0
    if "pricing_type" in clean:
        clean["pricing_type"] = str(clean["pricing_type"])
    if "price_stars" in clean:
        clean["price_stars"] = max(0, min(100000, int(clean["price_stars"] or 0)))
    if "content_type" in clean:
        clean["content_type"] = str(clean["content_type"] or "book")
    if "reading_mode" in clean:
        clean["reading_mode"] = str(clean["reading_mode"] or "ltr")

    now = utc_now()
    fields = [f"{key}=?" for key in clean]
    values_list = list(clean.values())
    sensitive = {"title", "description", "age_limit", "pricing_type", "price_stars", "content_type", "reading_mode"}
    if sensitive.intersection(clean):
        fields.append("publication_status=CASE WHEN publication_status='published' THEN 'review' ELSE publication_status END")
    fields.append("updated_at=?")
    values_list.extend([now, int(book_id), int(author_user_id)])
    async with connect() as db:
        cur = await db.execute(
            f"""
            UPDATE books SET {', '.join(fields)}
            WHERE id=? AND publication_status!='deleted'
              AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            values_list,
        )
        await db.commit()
        return cur.rowcount > 0


async def get_book(book_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*, a.pen_name, a.user_id AS author_user_id,
                   u.telegram_id AS author_telegram_id
            FROM books b
            LEFT JOIN author_profiles a ON a.id = b.author_id
            LEFT JOIN users u ON u.id = a.user_id
            WHERE b.id = ?
            """,
            (book_id,),
        )
        return await cur.fetchone()


async def list_catalog_books(limit: int = 50, include_drafts: bool = False) -> list[aiosqlite.Row]:
    """Возвращает книги для витрины с реальными агрегатами."""
    status_filter = "b.publication_status != 'deleted'" if include_drafts else "b.publication_status = 'published'"
    chapter_status = "c.status != 'deleted'" if include_drafts else "c.status = 'published'"
    graphic_status = "gc.status != 'deleted'" if include_drafts else "gc.status = 'published'"
    audio_status = "ac.status != 'deleted'" if include_drafts else "ac.status = 'published'"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.*, a.pen_name,
                   COALESCE((SELECT AVG(r.rating) FROM reviews r WHERE r.book_id=b.id AND r.status='published'), 0) AS rating,
                   (SELECT COUNT(*) FROM reviews r WHERE r.book_id=b.id AND r.status='published') AS reviews_count,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND {chapter_status}) AS text_chapters_count,
                   (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND {graphic_status}) AS graphic_chapters_count,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND {chapter_status}) +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND {graphic_status})) AS chapters_count,
                   (SELECT COALESCE(SUM(gc.pages_count), 0) FROM graphic_chapters gc WHERE gc.book_id=b.id AND {graphic_status}) AS graphic_pages_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND {audio_status}) AS audio_count,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND {chapter_status} AND c.is_free=1) +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND {graphic_status} AND gc.is_free=1)) AS free_chapters_count,
                   (SELECT c.id FROM chapters c WHERE c.book_id=b.id AND {chapter_status} ORDER BY c.number, c.id LIMIT 1) AS first_chapter_id,
                   (SELECT gc.id FROM graphic_chapters gc WHERE gc.book_id=b.id AND {graphic_status} ORDER BY gc.number, gc.id LIMIT 1) AS first_graphic_chapter_id,
                   (SELECT ac.id FROM audio_chapters ac WHERE ac.book_id=b.id AND {audio_status} ORDER BY ac.number, ac.id LIMIT 1) AS first_audio_id,
                   (SELECT GROUP_CONCAT(v.option_label, '||') FROM book_option_values v WHERE v.book_id=b.id AND v.option_group='genres') AS genre_labels,
                   (
                     SELECT COUNT(*) FROM purchases p
                     WHERE p.status='paid' AND (
                       p.book_id=b.id OR
                       p.chapter_id IN (SELECT c3.id FROM chapters c3 WHERE c3.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT ac3.id FROM audio_chapters ac3 WHERE ac3.book_id=b.id) OR
                       p.graphic_chapter_id IN (SELECT gc3.id FROM graphic_chapters gc3 WHERE gc3.book_id=b.id)
                     )
                   ) AS purchase_count
            FROM books b
            LEFT JOIN author_profiles a ON a.id=b.author_id
            WHERE {status_filter}
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        return await cur.fetchall()


async def submit_book_for_review(book_id: int, author_user_id: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET publication_status='review', updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (now, book_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def set_book_publication_status(book_id: int, status: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "UPDATE books SET publication_status=?, updated_at=? WHERE id=?",
            (status, now, book_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_books_for_moderation() -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*, a.pen_name,
                   (
                       SELECT c.id
                       FROM chapters c
                       WHERE c.book_id=b.id AND c.status!='deleted'
                       ORDER BY c.number ASC, c.id ASC
                       LIMIT 1
                   ) AS first_chapter_id,
                   (
                       SELECT gc.id
                       FROM graphic_chapters gc
                       WHERE gc.book_id=b.id AND gc.status!='deleted'
                       ORDER BY gc.number ASC, gc.id ASC
                       LIMIT 1
                   ) AS first_graphic_chapter_id,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status!='deleted') +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status!='deleted')) AS chapters_count,
                   (SELECT COALESCE(SUM(gc.pages_count), 0) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status!='deleted') AS graphic_pages_count
            FROM books b
            LEFT JOIN author_profiles a ON a.id = b.author_id
            WHERE b.publication_status = 'review'
            ORDER BY b.id ASC
            """
        )
        return await cur.fetchall()


async def add_admin(user_id: int, added_by_user_id: int | None) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO admin_staff(user_id, added_by_user_id, is_active, created_at, updated_at)
            VALUES(?, ?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_active=1, updated_at=excluded.updated_at
            """,
            (user_id, added_by_user_id, now, now),
        )
        await db.commit()


async def remove_admin(user_id: int) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute("UPDATE admin_staff SET is_active=0, updated_at=? WHERE user_id=?", (now, user_id))
        await db.commit()


async def get_admin_record_by_user_id(user_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM admin_staff WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        return await cur.fetchone()


async def list_admins() -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT s.*, u.telegram_id, u.username, u.full_name
            FROM admin_staff s
            JOIN users u ON u.id = s.user_id
            WHERE s.is_active = 1
            ORDER BY s.id DESC
            """
        )
        return await cur.fetchall()


async def get_admin_permissions(user_id: int) -> set[str]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.permission_code
            FROM admin_permissions p
            JOIN admin_staff s ON s.id = p.admin_id
            WHERE s.user_id = ? AND s.is_active = 1 AND p.allowed = 1
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
        return {row["permission_code"] for row in rows if row["permission_code"] in DELEGABLE_PERMISSION_CODES}


async def set_permission(user_id: int, permission_code: str, allowed: bool) -> None:
    if permission_code not in DELEGABLE_PERMISSION_CODES:
        raise ValueError("Это право нельзя передать администратору")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT id FROM admin_staff WHERE user_id=? AND is_active=1", (user_id,))
        admin = await cur.fetchone()
        if admin is None:
            raise ValueError("Администратор не найден")
        await db.execute(
            """
            INSERT INTO admin_permissions(admin_id, permission_code, allowed, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(admin_id, permission_code) DO UPDATE SET
                allowed=excluded.allowed,
                updated_at=excluded.updated_at
            """,
            (admin["id"], permission_code, 1 if allowed else 0, now),
        )
        await db.commit()


async def add_audit(actor_user_id: int | None, action: str, target_type: str | None = None,
                    target_id: str | None = None, before_value: str | None = None,
                    after_value: str | None = None) -> None:
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO audit_logs(actor_user_id, action, target_type, target_id, before_value, after_value, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (actor_user_id, action, target_type, target_id, before_value, after_value, utc_now()),
        )
        await db.commit()


async def list_audit(limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT l.*, u.telegram_id, u.username, u.full_name
            FROM audit_logs l
            LEFT JOIN users u ON u.id = l.actor_user_id
            ORDER BY l.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


async def get_setting(key: str, default: str = "") -> str:
    async with connect() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )
        await db.commit()


async def get_platform_stats() -> dict[str, int]:
    async with connect() as db:
        result: dict[str, int] = {}
        for name, sql in {
            "users": "SELECT COUNT(*) FROM users",
            "authors": "SELECT COUNT(*) FROM author_profiles",
            "books": "SELECT COUNT(*) FROM books",
            "books_review": "SELECT COUNT(*) FROM books WHERE publication_status='review'",
            "books_published": "SELECT COUNT(*) FROM books WHERE publication_status='published'",
            "chapters": "SELECT COUNT(*) FROM chapters",
            "graphic_chapters": "SELECT COUNT(*) FROM graphic_chapters",
            "graphic_pages": "SELECT COUNT(*) FROM graphic_pages",
            "audio": "SELECT COUNT(*) FROM audio_chapters",
            "complaints": "SELECT COUNT(*) FROM complaints WHERE status='new'",
            "ad_campaigns": "SELECT COUNT(*) FROM ad_campaigns WHERE status='running'",
            "promo_codes": "SELECT COUNT(*) FROM promo_codes WHERE status='active'",
            "comments": "SELECT COUNT(*) FROM comments WHERE status='published'",
            "reviews": "SELECT COUNT(*) FROM reviews WHERE status='published'",
        }.items():
            cur = await db.execute(sql)
            row = await cur.fetchone()
            result[name] = int(row[0]) if row else 0
        return result


async def get_author_dashboard_stats(author_user_id: int) -> dict[str, int]:
    """Короткая сводка для кабинета автора без служебных данных."""
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(author_user_id),))
        author = await cur.fetchone()
        if not author:
            return {
                "books_total": 0, "books_draft": 0, "books_review": 0,
                "books_published": 0, "chapters": 0, "text_chapters": 0, "graphic_chapters": 0, "graphic_pages": 0, "audio": 0,
            }
        author_id = int(author["id"])
        cur = await db.execute(
            """
            SELECT
                COUNT(*) AS books_total,
                SUM(CASE WHEN publication_status='draft' THEN 1 ELSE 0 END) AS books_draft,
                SUM(CASE WHEN publication_status='review' THEN 1 ELSE 0 END) AS books_review,
                SUM(CASE WHEN publication_status='published' THEN 1 ELSE 0 END) AS books_published
            FROM books
            WHERE author_id=? AND publication_status!='deleted'
            """,
            (author_id,),
        )
        books = await cur.fetchone()
        cur = await db.execute(
            "SELECT COUNT(*) FROM chapters c JOIN books b ON b.id=c.book_id WHERE b.author_id=? AND b.publication_status!='deleted' AND c.status!='deleted'",
            (author_id,),
        )
        text_chapters = await cur.fetchone()
        cur = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(gc.pages_count), 0) FROM graphic_chapters gc JOIN books b ON b.id=gc.book_id WHERE b.author_id=? AND b.publication_status!='deleted' AND gc.status!='deleted'",
            (author_id,),
        )
        graphic = await cur.fetchone()
        cur = await db.execute(
            "SELECT COUNT(*) FROM audio_chapters a JOIN books b ON b.id=a.book_id WHERE b.author_id=? AND b.publication_status!='deleted'",
            (author_id,),
        )
        audio = await cur.fetchone()
        return {
            "books_total": int(books["books_total"] or 0),
            "books_draft": int(books["books_draft"] or 0),
            "books_review": int(books["books_review"] or 0),
            "books_published": int(books["books_published"] or 0),
            "chapters": int(text_chapters[0] or 0) + int(graphic[0] or 0),
            "text_chapters": int(text_chapters[0] or 0),
            "graphic_chapters": int(graphic[0] or 0),
            "graphic_pages": int(graphic[1] or 0),
            "audio": int(audio[0] or 0),
        }


async def get_owner_today_stats() -> dict[str, int]:
    """Сводка владельца за текущий день в UTC."""
    today = datetime.now(timezone.utc).date().isoformat()
    async with connect() as db:
        queries = {
            "new_users": ("SELECT COUNT(*) FROM users WHERE substr(created_at, 1, 10)=?", (today,)),
            "purchases": ("SELECT COUNT(*) FROM purchases WHERE status='paid' AND substr(created_at, 1, 10)=?", (today,)),
            "stars": ("SELECT COALESCE(SUM(amount_stars),0) FROM purchases WHERE status='paid' AND substr(created_at, 1, 10)=?", (today,)),
            "new_books": ("SELECT COUNT(*) FROM books WHERE substr(created_at, 1, 10)=? AND publication_status!='deleted'", (today,)),
            "reviews": ("SELECT COUNT(*) FROM reviews WHERE substr(created_at, 1, 10)=?", (today,)),
            "comments": ("SELECT COUNT(*) FROM comments WHERE substr(created_at, 1, 10)=?", (today,)),
        }
        result: dict[str, int] = {}
        for key, (sql, params) in queries.items():
            cur = await db.execute(sql, params)
            row = await cur.fetchone()
            result[key] = int(row[0] or 0) if row else 0
        cur = await db.execute("SELECT COUNT(*) FROM complaints WHERE status='new'")
        row = await cur.fetchone()
        result["complaints"] = int(row[0] or 0) if row else 0
        cur = await db.execute("SELECT COUNT(*) FROM books WHERE publication_status='review'")
        row = await cur.fetchone()
        result["books_review"] = int(row[0] or 0) if row else 0
        return result



async def set_book_options(book_id: int, option_group: str, option_codes: list[str] | set[str] | tuple[str, ...]) -> None:
    """Сохраняет выбранные кнопками параметры книги: жанры, теги, аудиторию, предупреждения, язык, тип."""
    now = utc_now()
    clean_codes = []
    seen = set()
    for code in option_codes or []:
        code = str(code).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        clean_codes.append(code)
    async with connect() as db:
        await db.execute("DELETE FROM book_option_values WHERE book_id=? AND option_group=?", (book_id, option_group))
        for code in clean_codes:
            await db.execute(
                """
                INSERT INTO book_option_values(book_id, option_group, option_code, option_label, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (book_id, option_group, code, label_for(option_group, code), now),
            )
        await db.commit()


async def get_book_options(book_id: int) -> dict[str, list[str]]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT option_group, option_label
            FROM book_option_values
            WHERE book_id=?
            ORDER BY option_group, id
            """,
            (book_id,),
        )
        rows = await cur.fetchall()
    result: dict[str, list[str]] = {}
    for row in rows:
        result.setdefault(row["option_group"], []).append(row["option_label"])
    return result


async def list_similar_books(book_id: int, limit: int = 6) -> list[aiosqlite.Row]:
    """Подбирает похожие опубликованные книги по жанрам, тропам и аудитории."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*, a.pen_name,
                   COUNT(DISTINCT v2.option_group || ':' || v2.option_code) AS match_score,
                   GROUP_CONCAT(DISTINCT v2.option_label) AS matched_options,
                   COALESCE(AVG(CASE WHEN r.status='published' THEN r.rating END), 0) AS rating,
                   COUNT(DISTINCT CASE WHEN r.status='published' THEN r.id END) AS reviews_count,
                   COUNT(DISTINCT CASE WHEN c.status='published' THEN c.id END) AS chapters_count,
                   COUNT(DISTINCT CASE WHEN ac.status='published' THEN ac.id END) AS audio_count
            FROM book_option_values v1
            JOIN book_option_values v2
              ON v2.option_group=v1.option_group
             AND v2.option_code=v1.option_code
             AND v2.book_id != v1.book_id
            JOIN books b ON b.id=v2.book_id
            LEFT JOIN author_profiles a ON a.id=b.author_id
            LEFT JOIN reviews r ON r.book_id=b.id
            LEFT JOIN chapters c ON c.book_id=b.id
            LEFT JOIN audio_chapters ac ON ac.book_id=b.id
            WHERE v1.book_id=?
              AND v1.option_group IN ('genres','tropes','audience')
              AND b.publication_status='published'
              AND b.id != ?
            GROUP BY b.id
            ORDER BY match_score DESC, rating DESC, b.id DESC
            LIMIT ?
            """,
            (book_id, book_id, int(limit)),
        )
        rows = await cur.fetchall()
        if rows:
            return rows
        cur = await db.execute(
            """
            SELECT b.*, a.pen_name, 0 AS match_score, '' AS matched_options,
                   COALESCE(AVG(CASE WHEN r.status='published' THEN r.rating END), 0) AS rating,
                   COUNT(DISTINCT CASE WHEN r.status='published' THEN r.id END) AS reviews_count,
                   COUNT(DISTINCT CASE WHEN c.status='published' THEN c.id END) AS chapters_count,
                   COUNT(DISTINCT CASE WHEN ac.status='published' THEN ac.id END) AS audio_count
            FROM books b
            LEFT JOIN author_profiles a ON a.id=b.author_id
            LEFT JOIN reviews r ON r.book_id=b.id
            LEFT JOIN chapters c ON c.book_id=b.id
            LEFT JOIN audio_chapters ac ON ac.book_id=b.id
            WHERE b.publication_status='published' AND b.id != ?
            GROUP BY b.id
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (book_id, int(limit)),
        )
        return await cur.fetchall()


async def get_book_option_codes(book_id: int, option_group: str) -> list[str]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT option_code FROM book_option_values WHERE book_id=? AND option_group=? ORDER BY id",
            (book_id, option_group),
        )
        rows = await cur.fetchall()
    return [row["option_code"] for row in rows]


async def list_contextual_book_ads(current_book_id: int, limit: int = 4) -> list[aiosqlite.Row]:
    """Подбирает нативную рекламу книг для читалки.

    Сначала показывает активные рекламные кампании с совпадением по жанрам/тегам/аудитории,
    затем добирает органическими похожими книгами. Так реклама не выглядит случайной.
    """
    async with connect() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key='reader_ads_enabled'")
        enabled = await cur.fetchone()
        if enabled and str(enabled["value"]) == "0":
            return []

        cur = await db.execute(
            """
            SELECT b.*, a.pen_name,
                   ac.id AS campaign_id,
                   ac.placement AS ad_placement,
                   (ac.budget_units - ac.spent_units) AS remaining_budget,
                   COUNT(DISTINCT v2.option_group || ':' || v2.option_code) AS match_score,
                   GROUP_CONCAT(DISTINCT v2.option_label) AS matched_options
            FROM book_option_values v1
            JOIN book_option_values v2
              ON v2.option_group = v1.option_group
             AND v2.option_code = v1.option_code
             AND v2.book_id != v1.book_id
            JOIN books b ON b.id = v2.book_id
            JOIN ad_campaigns ac ON ac.book_id = b.id
            LEFT JOIN author_profiles a ON a.id = b.author_id
            WHERE v1.book_id = ?
              AND v1.option_group IN ('genres', 'tropes', 'audience')
              AND b.publication_status = 'published'
              AND b.id != ?
              AND ac.status = 'running'
              AND ac.budget_units > ac.spent_units
            GROUP BY b.id, ac.id
            ORDER BY match_score DESC, remaining_budget DESC, ac.id DESC
            LIMIT ?
            """,
            (current_book_id, current_book_id, int(limit)),
        )
        rows = await cur.fetchall()
        if len(rows) >= limit:
            return rows

        used_ids = {int(row["id"]) for row in rows}
        placeholders = ",".join("?" for _ in used_ids)
        not_in = f"AND b.id NOT IN ({placeholders})" if used_ids else ""
        params = [current_book_id, current_book_id, max(0, int(limit) - len(rows)), *used_ids]
        # SQLite параметры в NOT IN должны идти до LIMIT, поэтому строим аккуратно.
        if used_ids:
            params = [current_book_id, current_book_id, *used_ids, max(0, int(limit) - len(rows))]
        else:
            params = [current_book_id, current_book_id, max(0, int(limit) - len(rows))]
        cur = await db.execute(
            f"""
            SELECT b.*, a.pen_name,
                   NULL AS campaign_id,
                   '' AS ad_placement,
                   0 AS remaining_budget,
                   COUNT(DISTINCT v2.option_group || ':' || v2.option_code) AS match_score,
                   GROUP_CONCAT(DISTINCT v2.option_label) AS matched_options
            FROM book_option_values v1
            JOIN book_option_values v2
              ON v2.option_group = v1.option_group
             AND v2.option_code = v1.option_code
             AND v2.book_id != v1.book_id
            JOIN books b ON b.id = v2.book_id
            LEFT JOIN author_profiles a ON a.id = b.author_id
            WHERE v1.book_id = ?
              AND v1.option_group IN ('genres', 'tropes', 'audience')
              AND b.publication_status = 'published'
              AND b.id != ?
              {not_in}
            GROUP BY b.id
            ORDER BY match_score DESC, b.id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        organic = await cur.fetchall()
        result = list(rows) + list(organic)
        if result:
            return result[:limit]
        cur = await db.execute(
            """
            SELECT b.*, a.pen_name, NULL AS campaign_id, '' AS ad_placement,
                   0 AS remaining_budget, 0 AS match_score, '' AS matched_options
            FROM books b
            LEFT JOIN author_profiles a ON a.id = b.author_id
            WHERE b.publication_status='published' AND b.id != ?
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (current_book_id, int(limit)),
        )
        return await cur.fetchall()


async def get_reader_ad_settings() -> dict[str, bool | str]:
    defaults = {
        "reader_ads_enabled": "1",
        "reader_ads_top": "1",
        "reader_ads_bottom": "1",
        "reader_ads_label": "Похоже по жанру и сюжету",
        "daily_bonus_amount": "3",
        "ad_impression_cost": "1",
        "ad_click_cost": "3",
        "promo_default_max_uses": "100",
        "channel_promotion_price_stars": "50",
        "channel_promotion_cooldown_days": "30",
    }
    async with connect() as db:
        cur = await db.execute("SELECT key, value FROM settings WHERE key IN ('reader_ads_enabled','reader_ads_top','reader_ads_bottom','reader_ads_label')")
        rows = await cur.fetchall()
    values = defaults | {row["key"]: row["value"] for row in rows}
    return {
        "enabled": values["reader_ads_enabled"] != "0",
        "top": values["reader_ads_top"] != "0",
        "bottom": values["reader_ads_bottom"] != "0",
        "label": values["reader_ads_label"],
    }

async def book_belongs_to_author(book_id: int, author_user_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1
            FROM books b
            JOIN author_profiles a ON a.id = b.author_id
            WHERE b.id = ? AND a.user_id = ?
            """,
            (book_id, author_user_id),
        )
        return await cur.fetchone() is not None


async def list_chapters_for_book(
    book_id: int,
    include_deleted: bool = False,
    published_only: bool = False,
) -> list[aiosqlite.Row]:
    if published_only:
        status_filter = "AND status = 'published'"
    else:
        status_filter = "" if include_deleted else "AND status != 'deleted'"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT *
            FROM chapters
            WHERE book_id = ? {status_filter}
            ORDER BY number ASC
            """,
            (book_id,),
        )
        return await cur.fetchall()


async def get_adjacent_chapters(chapter_id: int) -> dict[str, aiosqlite.Row | None]:
    """Возвращает соседние опубликованные главы той же книги."""
    async with connect() as db:
        cur = await db.execute(
            "SELECT book_id, number FROM chapters WHERE id=? AND status='published'",
            (chapter_id,),
        )
        current = await cur.fetchone()
        if not current:
            return {"previous": None, "next": None}
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM chapters
            WHERE book_id=? AND status='published' AND number < ?
            ORDER BY number DESC
            LIMIT 1
            """,
            (int(current["book_id"]), int(current["number"])),
        )
        previous = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM chapters
            WHERE book_id=? AND status='published' AND number > ?
            ORDER BY number ASC
            LIMIT 1
            """,
            (int(current["book_id"]), int(current["number"])),
        )
        next_row = await cur.fetchone()
        return {"previous": previous, "next": next_row}


async def get_adjacent_chapters_for_moderation(chapter_id: int) -> dict[str, aiosqlite.Row | None]:
    """Соседние неудалённые главы для владельца и сотрудников проверки книг."""
    async with connect() as db:
        cur = await db.execute(
            "SELECT book_id, number FROM chapters WHERE id=? AND status!='deleted'",
            (int(chapter_id),),
        )
        current = await cur.fetchone()
        if not current:
            return {"previous": None, "next": None}
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM chapters
            WHERE book_id=? AND status!='deleted' AND number < ?
            ORDER BY number DESC, id DESC
            LIMIT 1
            """,
            (int(current["book_id"]), int(current["number"])),
        )
        previous = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM chapters
            WHERE book_id=? AND status!='deleted' AND number > ?
            ORDER BY number ASC, id ASC
            LIMIT 1
            """,
            (int(current["book_id"]), int(current["number"])),
        )
        next_row = await cur.fetchone()
        return {"previous": previous, "next": next_row}


async def get_chapter_by_number_for_moderation(book_id: int, chapter_number: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM chapters
            WHERE book_id=? AND number=? AND status!='deleted'
            ORDER BY id ASC
            LIMIT 1
            """,
            (int(book_id), int(chapter_number)),
        )
        return await cur.fetchone()


async def get_chapter_bounds_for_moderation(book_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT COALESCE(MIN(number), 0) AS min_number,
                   COALESCE(MAX(number), 0) AS max_number,
                   COUNT(*) AS chapters_count
            FROM chapters
            WHERE book_id=? AND status!='deleted'
            """,
            (int(book_id),),
        )
        row = await cur.fetchone()
        return {
            "min_number": int(row["min_number"] or 0) if row else 0,
            "max_number": int(row["max_number"] or 0) if row else 0,
            "chapters_count": int(row["chapters_count"] or 0) if row else 0,
        }


async def count_chapters_for_book(book_id: int) -> int:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM chapters WHERE book_id=? AND status!='deleted') +
              (SELECT COUNT(*) FROM graphic_chapters WHERE book_id=? AND status!='deleted')
            """,
            (book_id, book_id),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def get_published_chapter_by_number(book_id: int, chapter_number: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM chapters
            WHERE book_id=? AND number=? AND status='published'
            LIMIT 1
            """,
            (int(book_id), int(chapter_number)),
        )
        return await cur.fetchone()


async def get_published_chapter_bounds(book_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT COALESCE(MIN(number), 0) AS min_number,
                   COALESCE(MAX(number), 0) AS max_number,
                   COUNT(*) AS chapters_count
            FROM chapters
            WHERE book_id=? AND status='published'
            """,
            (int(book_id),),
        )
        row = await cur.fetchone()
        return {
            "min_number": int(row["min_number"] or 0) if row else 0,
            "max_number": int(row["max_number"] or 0) if row else 0,
            "chapters_count": int(row["chapters_count"] or 0) if row else 0,
        }


async def get_chapter(chapter_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, b.title AS book_title, b.publication_status, b.price_stars AS book_price_stars,
                   b.pricing_type, b.allow_download, a.pen_name
            FROM chapters c
            JOIN books b ON b.id = c.book_id
            LEFT JOIN author_profiles a ON a.id = b.author_id
            WHERE c.id = ? AND c.status != 'deleted'
            """,
            (chapter_id,),
        )
        return await cur.fetchone()


async def add_manual_chapter(book_id: int, title: str, text: str, is_free: bool = True, price_stars: int = 0) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS next_number FROM chapters WHERE book_id=? AND status != 'deleted'",
            (book_id,),
        )
        row = await cur.fetchone()
        number = int(row["next_number"] if row else 1)
        cur = await db.execute(
            """
            INSERT INTO chapters(book_id, number, title, text, is_free, price_stars, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (book_id, number, title, text, 1 if is_free else 0, int(price_stars), now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def upsert_imported_chapters(
    book_id: int,
    chapters: list[Any],
    first_free: int = 3,
    default_price_stars: int = 0,
    *,
    return_published_ids: bool = False,
) -> int | dict[str, Any]:
    """Сохраняет импорт и при необходимости возвращает новые опубликованные главы для уведомления."""
    now = utc_now()
    saved = 0
    published_ids: list[int] = []
    async with connect() as db:
        cur = await db.execute("SELECT publication_status FROM books WHERE id=?", (book_id,))
        book = await cur.fetchone()
        target_status = "published" if book and book["publication_status"] == "published" else "draft"
        for chapter in chapters:
            if isinstance(chapter, dict):
                number = int(chapter["number"])
                title = str(chapter["title"])[:160]
                text = str(chapter["text"])
            else:
                number = int(chapter.number)
                title = str(chapter.title)[:160]
                text = str(chapter.text)
            is_free = 1 if number <= int(first_free) else 0
            price = 0 if is_free else int(default_price_stars)
            cur = await db.execute(
                "SELECT id FROM chapters WHERE book_id=? AND number=?",
                (book_id, number),
            )
            existing = await cur.fetchone()
            await db.execute(
                """
                INSERT INTO chapters(book_id, number, title, text, is_free, price_stars, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(book_id, number) DO UPDATE SET
                    title=excluded.title,
                    text=excluded.text,
                    is_free=excluded.is_free,
                    price_stars=excluded.price_stars,
                    status=CASE WHEN chapters.status='published' THEN 'published' ELSE excluded.status END,
                    updated_at=excluded.updated_at
                """,
                (book_id, number, title, text, is_free, price, target_status, now, now),
            )
            if existing is None and target_status == "published":
                cur = await db.execute("SELECT id FROM chapters WHERE book_id=? AND number=?", (book_id, number))
                inserted = await cur.fetchone()
                if inserted:
                    published_ids.append(int(inserted["id"]))
            saved += 1
        await db.commit()
    if return_published_ids:
        return {"saved": saved, "published_ids": published_ids}
    return saved


async def set_chapter_status(chapter_id: int, status: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("UPDATE chapters SET status=?, updated_at=? WHERE id=?", (status, now, chapter_id))
        await db.commit()
        return cur.rowcount > 0


async def update_chapter_title(chapter_id: int, author_user_id: int, title: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE chapters
            SET title=?, updated_at=?
            WHERE id=? AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles a ON a.id=b.author_id WHERE a.user_id=?
            )
            """,
            (title[:160], now, chapter_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_chapter_text(chapter_id: int, author_user_id: int, text: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE chapters
            SET text=?, updated_at=?
            WHERE id=? AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles a ON a.id=b.author_id WHERE a.user_id=?
            )
            """,
            (text, now, chapter_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def update_chapter_price(chapter_id: int, author_user_id: int, is_free: bool, price_stars: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE chapters
            SET is_free=?, price_stars=?, updated_at=?
            WHERE id=? AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles a ON a.id=b.author_id WHERE a.user_id=?
            )
            """,
            (1 if is_free else 0, int(price_stars), now, chapter_id, author_user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def soft_delete_chapter_for_author(chapter_id: int, author_user_id: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE chapters
            SET status='deleted', updated_at=?
            WHERE id=? AND status!='deleted' AND book_id IN (
                SELECT b.id FROM books b
                JOIN author_profiles a ON a.id=b.author_id
                WHERE a.user_id=? AND b.publication_status!='deleted'
            )
            """,
            (now, int(chapter_id), int(author_user_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_book_with_counts(book_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*, a.pen_name,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') AS text_chapters_count,
                   (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published') AS graphic_chapters_count,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published')) AS chapters_count,
                   (SELECT COALESCE(SUM(gc.pages_count), 0) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published') AS graphic_pages_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status='published') AS audio_count,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published' AND c.is_free=1) +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published' AND gc.is_free=1)) AS free_chapters_count,
                   COALESCE((SELECT SUM(LENGTH(c.text)) FROM chapters c WHERE c.book_id=b.id AND c.status='published'), 0) AS text_chars,
                   COALESCE((SELECT AVG(r.rating) FROM reviews r WHERE r.book_id=b.id AND r.status='published'), 0) AS rating,
                   (SELECT COUNT(*) FROM reviews r WHERE r.book_id=b.id AND r.status='published') AS reviews_count,
                   (
                     SELECT COUNT(*) FROM purchases p
                     WHERE p.status='paid' AND (
                       p.book_id=b.id OR
                       p.chapter_id IN (SELECT c3.id FROM chapters c3 WHERE c3.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT ac3.id FROM audio_chapters ac3 WHERE ac3.book_id=b.id) OR
                       p.graphic_chapter_id IN (SELECT gc3.id FROM graphic_chapters gc3 WHERE gc3.book_id=b.id)
                     )
                   ) AS purchase_count
            FROM books b
            LEFT JOIN author_profiles a ON a.id=b.author_id
            WHERE b.id=?
            """,
            (book_id,),
        )
        return await cur.fetchone()


async def add_audio_chapter(
    book_id: int,
    title: str,
    file_id: str | None,
    file_path: str | None,
    duration_seconds: int,
    narrator: str | None,
    source_filename: str | None,
    mime_type: str | None,
    file_size: int,
    is_free: bool = False,
    price_stars: int = 0,
    sample_seconds: int = 60,
) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS next_number FROM audio_chapters WHERE book_id=? AND status != 'deleted'",
            (book_id,),
        )
        row = await cur.fetchone()
        number = int(row["next_number"] if row else 1)
        cur = await db.execute(
            """
            INSERT INTO audio_chapters(book_id, number, title, file_id, file_path, duration_seconds, narrator,
                                       source_filename, mime_type, file_size, sample_seconds, is_free,
                                       price_stars, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                book_id, number, title, file_id, file_path, int(duration_seconds or 0), narrator,
                source_filename, mime_type, int(file_size or 0), int(sample_seconds or 60),
                1 if is_free else 0, int(price_stars or 0), now, now,
            ),
        )
        await db.execute("UPDATE books SET has_audio=1, updated_at=? WHERE id=?", (now, book_id))
        await db.commit()
        return int(cur.lastrowid)


async def list_audio_chapters_for_book(
    book_id: int,
    include_deleted: bool = False,
    published_only: bool = False,
) -> list[aiosqlite.Row]:
    if published_only:
        status_filter = "AND status = 'published'"
    else:
        status_filter = "" if include_deleted else "AND status != 'deleted'"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT *
            FROM audio_chapters
            WHERE book_id = ? {status_filter}
            ORDER BY number ASC
            """,
            (book_id,),
        )
        return await cur.fetchall()


async def get_adjacent_audio_chapters(audio_id: int) -> dict[str, aiosqlite.Row | None]:
    """Возвращает соседние опубликованные аудиоглавы той же книги."""
    async with connect() as db:
        cur = await db.execute(
            "SELECT book_id, number FROM audio_chapters WHERE id=? AND status='published'",
            (audio_id,),
        )
        current = await cur.fetchone()
        if not current:
            return {"previous": None, "next": None}
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM audio_chapters
            WHERE book_id=? AND status='published' AND number < ?
            ORDER BY number DESC
            LIMIT 1
            """,
            (int(current["book_id"]), int(current["number"])),
        )
        previous = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT id, book_id, number, title
            FROM audio_chapters
            WHERE book_id=? AND status='published' AND number > ?
            ORDER BY number ASC
            LIMIT 1
            """,
            (int(current["book_id"]), int(current["number"])),
        )
        next_row = await cur.fetchone()
        return {"previous": previous, "next": next_row}


async def count_audio_chapters_for_book(book_id: int) -> int:
    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM audio_chapters WHERE book_id=? AND status != 'deleted'", (book_id,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def get_audio_chapter(audio_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT ac.*, b.title AS book_title, b.publication_status, b.price_stars AS book_price_stars,
                   b.pricing_type, a.pen_name
            FROM audio_chapters ac
            JOIN books b ON b.id = ac.book_id
            LEFT JOIN author_profiles a ON a.id = b.author_id
            WHERE ac.id = ? AND ac.status != 'deleted'
            """,
            (audio_id,),
        )
        return await cur.fetchone()


async def set_audio_chapter_status(audio_id: int, status: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("UPDATE audio_chapters SET status=?, updated_at=? WHERE id=?", (status, now, audio_id))
        await db.commit()
        return cur.rowcount > 0


async def save_listening_progress(user_id: int, audio_chapter_id: int, position_seconds: int) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO listening_progress(user_id, audio_chapter_id, position_seconds, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, audio_chapter_id) DO UPDATE SET
                position_seconds=excluded.position_seconds,
                updated_at=excluded.updated_at
            """,
            (user_id, audio_chapter_id, max(0, int(position_seconds)), now),
        )
        await db.commit()


async def get_listening_progress(user_id: int, audio_chapter_id: int) -> int:
    async with connect() as db:
        cur = await db.execute(
            "SELECT position_seconds FROM listening_progress WHERE user_id=? AND audio_chapter_id=?",
            (user_id, audio_chapter_id),
        )
        row = await cur.fetchone()
        return int(row["position_seconds"]) if row else 0


async def has_purchase_access(user_id: int, *, book_id: int | None = None, chapter_id: int | None = None,
                              audio_chapter_id: int | None = None) -> bool:
    """Проверяет, купил ли пользователь книгу/главу/аудиоглаву. Бесплатность проверяется отдельно."""
    async with connect() as db:
        if chapter_id is not None:
            cur = await db.execute(
                """
                SELECT 1
                FROM purchases p
                WHERE p.user_id=? AND p.status='paid' AND (
                    p.chapter_id=? OR p.book_id=(SELECT book_id FROM chapters WHERE id=?)
                )
                LIMIT 1
                """,
                (user_id, chapter_id, chapter_id),
            )
            return await cur.fetchone() is not None
        if audio_chapter_id is not None:
            cur = await db.execute(
                """
                SELECT 1
                FROM purchases p
                WHERE p.user_id=? AND p.status='paid' AND (
                    p.audio_chapter_id=? OR p.book_id=(SELECT book_id FROM audio_chapters WHERE id=?)
                )
                LIMIT 1
                """,
                (user_id, audio_chapter_id, audio_chapter_id),
            )
            return await cur.fetchone() is not None
        if book_id is not None:
            cur = await db.execute(
                "SELECT 1 FROM purchases WHERE user_id=? AND book_id=? AND status='paid' LIMIT 1",
                (user_id, book_id),
            )
            return await cur.fetchone() is not None
        return False


async def get_book_author_id(book_id: int) -> int | None:
    async with connect() as db:
        cur = await db.execute("SELECT author_id FROM books WHERE id=?", (book_id,))
        row = await cur.fetchone()
        return int(row["author_id"]) if row and row["author_id"] is not None else None


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    """Возвращает цель покупки по payload формата vox:chapter:123 / vox:audio:123 / vox:book:123."""
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "vox":
        return None
    kind = parts[1]
    try:
        target_id = int(parts[2])
    except ValueError:
        return None
    if kind == "chapter":
        chapter = await get_chapter(target_id)
        if not chapter:
            return None
        return {
            "kind": "chapter",
            "target_id": target_id,
            "book_id": int(chapter["book_id"]),
            "title": chapter["title"],
            "book_title": chapter["book_title"],
            "amount_stars": int(chapter["price_stars"] or 0),
            "author_id": await get_book_author_id(int(chapter["book_id"])),
        }
    if kind == "audio":
        audio = await get_audio_chapter(target_id)
        if not audio:
            return None
        return {
            "kind": "audio",
            "target_id": target_id,
            "book_id": int(audio["book_id"]),
            "title": audio["title"],
            "book_title": audio["book_title"],
            "amount_stars": int(audio["price_stars"] or 0),
            "author_id": await get_book_author_id(int(audio["book_id"])),
        }
    if kind == "book":
        book = await get_book(target_id)
        if not book:
            return None
        return {
            "kind": "book",
            "target_id": target_id,
            "book_id": target_id,
            "title": book["title"],
            "book_title": book["title"],
            "amount_stars": int(book["price_stars"] or 0),
            "author_id": int(book["author_id"]) if book["author_id"] is not None else None,
        }
    return None


async def _ensure_v179_schema(db: aiosqlite.Connection) -> None:
    """Миграция категорий уведомлений и защита от повторной рассылки."""
    cur = await db.execute("PRAGMA table_info(user_preferences)")
    existing = {row[1] for row in await cur.fetchall()}
    for column in ("notifications_chapters", "notifications_audio", "notifications_discounts"):
        if column not in existing:
            await _execute_schema_ddl(db, f"ALTER TABLE user_preferences ADD COLUMN {column} INTEGER NOT NULL DEFAULT 1")
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(event_key, user_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_notification_deliveries_event ON notification_deliveries(event_key);
        """
    )
    # Ранние версии публиковали книгу, но могли оставить её главы черновиками.
    await db.execute(
        "UPDATE chapters SET status='published' WHERE status='draft' AND book_id IN "
        "(SELECT id FROM books WHERE publication_status='published')"
    )
    await db.execute(
        "UPDATE audio_chapters SET status='published' WHERE status='draft' AND book_id IN "
        "(SELECT id FROM books WHERE publication_status='published')"
    )


async def publish_book_content(book_id: int) -> dict[str, int]:
    """Публикует подготовленные главы вместе с первой публикацией книги без массовой рассылки."""
    now = utc_now()
    async with connect() as db:
        chapters = await db.execute(
            "UPDATE chapters SET status='published', updated_at=? WHERE book_id=? AND status='draft'",
            (now, int(book_id)),
        )
        graphics = await db.execute(
            "UPDATE graphic_chapters SET status='published', updated_at=? WHERE book_id=? AND status='draft'",
            (now, int(book_id)),
        )
        audio = await db.execute(
            "UPDATE audio_chapters SET status='published', updated_at=? WHERE book_id=? AND status='draft'",
            (now, int(book_id)),
        )
        await db.commit()
        result = {"chapters": int(chapters.rowcount), "audio": int(audio.rowcount)}
        if int(graphics.rowcount) > 0:
            result["graphics"] = int(graphics.rowcount)
        return result


async def list_book_notification_recipients(book_id: int, limit: int = 5000) -> list[aiosqlite.Row]:
    """Читатели, которые сохранили, открывали, оценивали или покупали книгу."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT DISTINCT u.id, u.telegram_id, u.username, u.full_name
            FROM users u
            WHERE u.is_blocked=0
              AND u.id != COALESCE((
                  SELECT ap.user_id FROM books b
                  LEFT JOIN author_profiles ap ON ap.id=b.author_id
                  WHERE b.id=?
              ), -1)
              AND (
                  EXISTS(SELECT 1 FROM bookmarks bm WHERE bm.user_id=u.id AND bm.book_id=?)
                  OR EXISTS(SELECT 1 FROM reading_progress rp WHERE rp.user_id=u.id AND rp.book_id=?)
                  OR EXISTS(
                      SELECT 1 FROM listening_progress lp
                      JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id
                      WHERE lp.user_id=u.id AND ac.book_id=?
                  )
                  OR EXISTS(SELECT 1 FROM reviews r WHERE r.user_id=u.id AND r.book_id=?)
                  OR EXISTS(
                      SELECT 1 FROM purchases p
                      WHERE p.user_id=u.id AND p.status='paid' AND (
                          p.book_id=?
                          OR p.chapter_id IN (SELECT id FROM chapters WHERE book_id=?)
                          OR p.audio_chapter_id IN (SELECT id FROM audio_chapters WHERE book_id=?)
                          OR p.graphic_chapter_id IN (SELECT id FROM graphic_chapters WHERE book_id=?)
                      )
                  )
              )
            ORDER BY u.id
            LIMIT ?
            """,
            (book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, int(limit)),
        )
        return await cur.fetchall()


async def claim_notification_delivery(event_key: str, user_id: int, category: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO notification_deliveries(event_key, user_id, category, status, created_at, updated_at)
            VALUES(?, ?, ?, 'pending', ?, ?)
            """,
            (str(event_key)[:180], int(user_id), str(category)[:40], now, now),
        )
        await db.commit()
        return cur.rowcount > 0


async def finish_notification_delivery(event_key: str, user_id: int, status: str) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            "UPDATE notification_deliveries SET status=?, updated_at=? WHERE event_key=? AND user_id=?",
            (str(status)[:30], now, str(event_key)[:180], int(user_id)),
        )
        await db.commit()


async def create_paid_purchase(
    *,
    user_id: int,
    payload: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
) -> int:
    target = await get_purchase_target(payload)
    if target is None:
        raise ValueError("Unknown purchase payload")
    now = utc_now()
    book_id = target["book_id"] if target["kind"] == "book" else None
    chapter_id = target["target_id"] if target["kind"] == "chapter" else None
    audio_chapter_id = target["target_id"] if target["kind"] == "audio" else None
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO purchases(user_id, book_id, chapter_id, audio_chapter_id, amount_stars, status,
                                  telegram_payment_charge_id, created_at)
            VALUES(?, ?, ?, ?, ?, 'paid', ?, ?)
            """,
            (user_id, book_id, chapter_id, audio_chapter_id, int(amount_stars), telegram_payment_charge_id, now),
        )
        purchase_id = int(cur.lastrowid)
        author_id = target.get("author_id")
        if author_id is not None:
            setting_key = "commission_audio" if target["kind"] == "audio" else "commission_books"
            cur_setting = await db.execute("SELECT value FROM settings WHERE key=?", (setting_key,))
            row_setting = await cur_setting.fetchone()
            commission_percent = int(row_setting["value"] if row_setting else 20)
            cur_hold = await db.execute("SELECT value FROM settings WHERE key='hold_days_default'")
            row_hold = await cur_hold.fetchone()
            hold_days = int(row_hold["value"] if row_hold else 14)
            commission_stars = int(round(int(amount_stars) * commission_percent / 100))
            net_stars = max(0, int(amount_stars) - commission_stars)
            available_at = (datetime.now(timezone.utc) + timedelta(days=hold_days)).isoformat()
            await db.execute(
                """
                INSERT INTO author_ledger(author_id, purchase_id, source_type, source_id, gross_stars,
                                          commission_percent, commission_stars, net_stars, hold_days,
                                          available_at, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'held', ?, ?)
                """,
                (
                    author_id,
                    purchase_id,
                    target["kind"],
                    int(target["target_id"]),
                    int(amount_stars),
                    commission_percent,
                    commission_stars,
                    net_stars,
                    hold_days,
                    available_at,
                    now,
                    now,
                ),
            )
        await db.commit()
        return purchase_id


async def list_user_purchases(user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, b.title AS book_title,
                   c.title AS chapter_title,
                   ac.title AS audio_title
            FROM purchases p
            LEFT JOIN books b ON b.id = p.book_id
            LEFT JOIN chapters c ON c.id = p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id = p.audio_chapter_id
            WHERE p.user_id=?
            ORDER BY p.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return await cur.fetchall()


async def get_purchase(purchase_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, u.telegram_id, u.username, u.full_name,
                   b.title AS book_title,
                   c.title AS chapter_title,
                   ac.title AS audio_title
            FROM purchases p
            JOIN users u ON u.id = p.user_id
            LEFT JOIN books b ON b.id = p.book_id
            LEFT JOIN chapters c ON c.id = p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id = p.audio_chapter_id
            WHERE p.id=?
            """,
            (purchase_id,),
        )
        return await cur.fetchone()


async def create_refund_request(purchase_id: int, user_id: int, reason: str) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO refund_requests(purchase_id, user_id, reason, status, created_at, updated_at)
            VALUES(?, ?, ?, 'new', ?, ?)
            """,
            (purchase_id, user_id, reason[:1000], now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_refund_requests(status: str = "new", limit: int = 30) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rr.*, p.amount_stars, p.telegram_payment_charge_id, u.telegram_id, u.username, u.full_name,
                   b.title AS book_title, c.title AS chapter_title, ac.title AS audio_title
            FROM refund_requests rr
            JOIN purchases p ON p.id = rr.purchase_id
            JOIN users u ON u.id = rr.user_id
            LEFT JOIN books b ON b.id = p.book_id
            LEFT JOIN chapters c ON c.id = p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id = p.audio_chapter_id
            WHERE rr.status=?
            ORDER BY rr.id ASC
            LIMIT ?
            """,
            (status, limit),
        )
        return await cur.fetchall()


async def get_refund_request(refund_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rr.*, p.amount_stars, p.telegram_payment_charge_id, p.status AS purchase_status,
                   u.telegram_id, u.username, u.full_name,
                   b.title AS book_title, c.title AS chapter_title, ac.title AS audio_title
            FROM refund_requests rr
            JOIN purchases p ON p.id = rr.purchase_id
            JOIN users u ON u.id = rr.user_id
            LEFT JOIN books b ON b.id = p.book_id
            LEFT JOIN chapters c ON c.id = p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id = p.audio_chapter_id
            WHERE rr.id=?
            """,
            (refund_id,),
        )
        return await cur.fetchone()


async def set_refund_status(refund_id: int, status: str, handled_by_user_id: int | None = None,
                            note: str | None = None) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            UPDATE refund_requests
            SET status=?, handled_by_user_id=?, moderator_note=?, updated_at=?
            WHERE id=?
            """,
            (status, handled_by_user_id, note, now, refund_id),
        )
        await db.commit()


async def mark_purchase_refunded(purchase_id: int) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute("UPDATE purchases SET status='refunded' WHERE id=?", (purchase_id,))
        await db.execute("UPDATE author_ledger SET status='refunded', updated_at=? WHERE purchase_id=?", (now, purchase_id))
        await db.commit()


async def get_author_finance_summary(author_user_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (author_user_id,))
        author = await cur.fetchone()
        if not author:
            return {"gross": 0, "commission": 0, "net": 0, "held": 0, "available": 0, "refunded": 0}
        author_id = int(author["id"])
        now = utc_now()
        await db.execute(
            "UPDATE author_ledger SET status='available', updated_at=? WHERE author_id=? AND status='held' AND available_at <= ?",
            (now, author_id, now),
        )
        await db.commit()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status!='refunded' THEN gross_stars ELSE 0 END), 0) AS gross,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN commission_stars ELSE 0 END), 0) AS commission,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN net_stars ELSE 0 END), 0) AS net,
                COALESCE(SUM(CASE WHEN status='held' THEN net_stars ELSE 0 END), 0) AS held,
                COALESCE(SUM(CASE WHEN status='available' THEN net_stars ELSE 0 END), 0) AS available,
                COALESCE(SUM(CASE WHEN status='refunded' THEN gross_stars ELSE 0 END), 0) AS refunded
            FROM author_ledger
            WHERE author_id=?
            """,
            (author_id,),
        )
        row = await cur.fetchone()
        return {key: int(row[key] or 0) for key in row.keys()} if row else {}


async def get_platform_finance_summary() -> dict[str, int]:
    async with connect() as db:
        now = utc_now()
        await db.execute("UPDATE author_ledger SET status='available', updated_at=? WHERE status='held' AND available_at <= ?", (now, now))
        await db.commit()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN p.status='paid' THEN p.amount_stars ELSE 0 END), 0) AS paid_gross,
                COALESCE(SUM(CASE WHEN p.status='refunded' THEN p.amount_stars ELSE 0 END), 0) AS refunded_gross,
                COUNT(CASE WHEN p.status='paid' THEN 1 END) AS paid_count,
                COUNT(CASE WHEN p.status='refunded' THEN 1 END) AS refunded_count
            FROM purchases p
            """
        )
        purchases = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status!='refunded' THEN commission_stars ELSE 0 END), 0) AS platform_commission,
                COALESCE(SUM(CASE WHEN status='held' THEN net_stars ELSE 0 END), 0) AS held_authors,
                COALESCE(SUM(CASE WHEN status='available' THEN net_stars ELSE 0 END), 0) AS available_authors
            FROM author_ledger
            """
        )
        ledger = await cur.fetchone()
        return {
            "paid_gross": int(purchases["paid_gross"] or 0),
            "refunded_gross": int(purchases["refunded_gross"] or 0),
            "paid_count": int(purchases["paid_count"] or 0),
            "refunded_count": int(purchases["refunded_count"] or 0),
            "platform_commission": int(ledger["platform_commission"] or 0),
            "held_authors": int(ledger["held_authors"] or 0),
            "available_authors": int(ledger["available_authors"] or 0),
        }


async def save_reading_progress(user_id: int, chapter_id: int, position_percent: int) -> None:
    position_percent = max(0, min(100, int(position_percent)))
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT book_id FROM chapters WHERE id=?", (chapter_id,))
        chapter = await cur.fetchone()
        if not chapter:
            return
        await db.execute(
            """
            INSERT INTO reading_progress(user_id, book_id, chapter_id, position_percent, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chapter_id) DO UPDATE SET
                position_percent=excluded.position_percent,
                updated_at=excluded.updated_at
            """,
            (user_id, int(chapter["book_id"]), chapter_id, position_percent, now),
        )
        await db.commit()


async def get_reading_progress(user_id: int, chapter_id: int) -> int:
    async with connect() as db:
        cur = await db.execute(
            "SELECT position_percent FROM reading_progress WHERE user_id=? AND chapter_id=?",
            (user_id, chapter_id),
        )
        row = await cur.fetchone()
        return int(row["position_percent"]) if row else 0


async def set_bookmark(user_id: int, book_id: int, status: str = "reading") -> None:
    status = status if status in {"reading", "favorite", "planned", "finished", "dropped"} else "reading"
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO bookmarks(user_id, book_id, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (user_id, book_id, status, now, now),
        )
        await db.commit()


async def remove_bookmark(user_id: int, book_id: int) -> None:
    async with connect() as db:
        await db.execute("DELETE FROM bookmarks WHERE user_id=? AND book_id=?", (user_id, book_id))
        await db.commit()


async def get_bookmark(user_id: int, book_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM bookmarks WHERE user_id=? AND book_id=?", (user_id, book_id))
        return await cur.fetchone()


async def list_user_bookmarks(user_id: int, limit: int = 50, published_only: bool = False) -> list[aiosqlite.Row]:
    publication_filter = "AND b.publication_status='published'" if published_only else ""
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT bm.*, b.title, b.description, b.age_limit, b.publication_status, b.cover_path, b.cover_file_id,
                   ap.pen_name,
                   COALESCE(MAX(rp.updated_at), bm.updated_at) AS last_progress_at,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') AS chapters_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status='published') AS audio_count
            FROM bookmarks bm
            JOIN books b ON b.id = bm.book_id
            LEFT JOIN author_profiles ap ON ap.id = b.author_id
            LEFT JOIN reading_progress rp ON rp.user_id = bm.user_id AND rp.book_id = bm.book_id
            WHERE bm.user_id=? {publication_filter}
            GROUP BY bm.id
            ORDER BY bm.updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return await cur.fetchall()


async def list_user_continue_reading(user_id: int, limit: int = 12) -> list[aiosqlite.Row]:
    """Последняя открытая опубликованная глава каждой книги."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rp.*, b.title, b.description, b.age_limit, b.cover_path, b.cover_file_id,
                   ap.pen_name, c.title AS chapter_title, c.number AS chapter_number,
                   (
                     SELECT COUNT(*)
                     FROM chapters c2
                     WHERE c2.book_id=b.id AND c2.status='published'
                   ) AS chapters_count
            FROM reading_progress rp
            JOIN books b ON b.id=rp.book_id AND b.publication_status='published'
            JOIN chapters c ON c.id=rp.chapter_id AND c.status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE rp.user_id=?
              AND rp.updated_at=(
                SELECT MAX(rp2.updated_at)
                FROM reading_progress rp2
                WHERE rp2.user_id=rp.user_id AND rp2.book_id=rp.book_id
              )
            ORDER BY rp.updated_at DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        return await cur.fetchall()


async def list_user_continue_listening(user_id: int, limit: int = 12) -> list[aiosqlite.Row]:
    """Последняя прослушиваемая опубликованная аудиоглава каждой книги."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT lp.*, ac.book_id, ac.title AS audio_title, ac.number AS audio_number,
                   ac.duration_seconds, b.title, b.cover_path, b.cover_file_id, ap.pen_name
            FROM listening_progress lp
            JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id AND ac.status='published'
            JOIN books b ON b.id=ac.book_id AND b.publication_status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE lp.user_id=?
              AND lp.updated_at=(
                SELECT MAX(lp2.updated_at)
                FROM listening_progress lp2
                JOIN audio_chapters ac2 ON ac2.id=lp2.audio_chapter_id
                WHERE lp2.user_id=lp.user_id AND ac2.book_id=ac.book_id
              )
            ORDER BY lp.updated_at DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        return await cur.fetchall()


async def upsert_review(user_id: int, book_id: int, rating: int, text: str) -> None:
    rating = max(1, min(5, int(rating)))
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO reviews(user_id, book_id, rating, text, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, 'published', ?, ?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                rating=excluded.rating,
                text=excluded.text,
                status='published',
                updated_at=excluded.updated_at
            """,
            (user_id, book_id, rating, text[:3000], now, now),
        )
        await db.commit()


async def get_user_review(user_id: int, book_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM reviews WHERE user_id=? AND book_id=? AND status='published'",
            (user_id, book_id),
        )
        return await cur.fetchone()


async def list_reviews_for_book(book_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT r.*, u.username, u.full_name
            FROM reviews r
            JOIN users u ON u.id = r.user_id
            WHERE r.book_id=? AND r.status='published'
            ORDER BY r.updated_at DESC
            LIMIT ?
            """,
            (book_id, limit),
        )
        return await cur.fetchall()


async def add_comment(user_id: int, chapter_id: int, text: str) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT book_id FROM chapters WHERE id=?", (chapter_id,))
        chapter = await cur.fetchone()
        if not chapter:
            raise ValueError("Chapter not found")
        cur = await db.execute(
            """
            INSERT INTO comments(user_id, book_id, chapter_id, text, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, 'published', ?, ?)
            """,
            (user_id, int(chapter["book_id"]), chapter_id, text[:2000], now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_comments_for_chapter(chapter_id: int, limit: int = 50) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, u.username, u.full_name
            FROM comments c
            JOIN users u ON u.id = c.user_id
            WHERE c.chapter_id=? AND c.status='published'
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (chapter_id, limit),
        )
        return await cur.fetchall()


async def user_can_access_chapter(user_id: int, chapter_id: int) -> bool:
    chapter = await get_chapter(chapter_id)
    if not chapter:
        return False
    if int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0:
        return True
    return await has_purchase_access(user_id, chapter_id=chapter_id)


async def user_can_access_audio(user_id: int, audio_chapter_id: int) -> bool:
    audio = await get_audio_chapter(audio_chapter_id)
    if not audio:
        return False
    if int(audio["is_free"] or 0) == 1 or int(audio["price_stars"] or 0) <= 0:
        return True
    return await has_purchase_access(user_id, audio_chapter_id=audio_chapter_id)



async def ensure_bonus_wallet(user_id: int) -> aiosqlite.Row:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO bonus_wallets(user_id, balance, created_at, updated_at)
            VALUES(?, 0, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, now, now),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM bonus_wallets WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row is None:
            raise RuntimeError("Bonus wallet was not created")
        return row


async def get_bonus_balance(user_id: int) -> int:
    wallet = await ensure_bonus_wallet(user_id)
    return int(wallet["balance"] or 0)


async def add_bonus_transaction(user_id: int, amount: int, reason: str, source_type: str | None = None,
                                source_id: str | None = None) -> int:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO bonus_wallets(user_id, balance, created_at, updated_at)
            VALUES(?, 0, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, now, now),
        )
        await db.execute(
            "UPDATE bonus_wallets SET balance=MAX(0, balance + ?), updated_at=? WHERE user_id=?",
            (int(amount), now, user_id),
        )
        cur = await db.execute(
            """
            INSERT INTO bonus_transactions(user_id, amount, reason, source_type, source_id, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (user_id, int(amount), reason[:120], source_type, source_id, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def claim_daily_bonus(user_id: int) -> tuple[bool, int, int]:
    """Возвращает: получил ли бонус, размер бонуса, текущий баланс."""
    today = datetime.now(timezone.utc).date().isoformat()
    amount = int(await get_setting("daily_bonus_amount", "3") or 3)
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO bonus_wallets(user_id, balance, created_at, updated_at)
            VALUES(?, 0, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, now, now),
        )
        cur = await db.execute("SELECT * FROM bonus_wallets WHERE user_id=?", (user_id,))
        wallet = await cur.fetchone()
        last = (wallet["last_daily_bonus_at"] or "")[:10] if wallet else ""
        if last == today:
            return False, amount, int(wallet["balance"] or 0)
        await db.execute(
            """
            UPDATE bonus_wallets
            SET balance=balance + ?, last_daily_bonus_at=?, updated_at=?
            WHERE user_id=?
            """,
            (amount, now, now, user_id),
        )
        await db.execute(
            """
            INSERT INTO bonus_transactions(user_id, amount, reason, source_type, source_id, created_at)
            VALUES(?, ?, 'daily_bonus', 'system', ?, ?)
            """,
            (user_id, amount, today, now),
        )
        await db.commit()
        cur = await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (user_id,))
        wallet = await cur.fetchone()
        return True, amount, int(wallet["balance"] or 0)


async def list_bonus_transactions(user_id: int, limit: int = 10) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM bonus_transactions
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return await cur.fetchall()


async def create_ad_campaign(author_user_id: int, book_id: int, title: str, placement: str,
                             budget_units: int) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM author_profiles WHERE user_id=?",
            (author_user_id,),
        )
        author = await cur.fetchone()
        if not author:
            raise ValueError("Author profile not found")
        cur = await db.execute(
            "SELECT id FROM books WHERE id=? AND author_id=?",
            (book_id, int(author["id"])),
        )
        if await cur.fetchone() is None:
            raise ValueError("Book does not belong to author")
        cur = await db.execute(
            """
            INSERT INTO ad_campaigns(author_id, book_id, title, placement, budget_units, spent_units, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 0, 'running', ?, ?)
            """,
            (int(author["id"]), book_id, title[:160], placement, max(0, int(budget_units)), now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_author_ad_campaigns(author_user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT ac.*, b.title AS book_title
            FROM ad_campaigns ac
            JOIN books b ON b.id = ac.book_id
            JOIN author_profiles ap ON ap.id = ac.author_id
            WHERE ap.user_id=?
            ORDER BY ac.id DESC
            LIMIT ?
            """,
            (author_user_id, limit),
        )
        return await cur.fetchall()


async def list_active_ad_campaigns(limit: int = 30) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT ac.*, b.title AS book_title, ap.pen_name
            FROM ad_campaigns ac
            JOIN books b ON b.id = ac.book_id
            JOIN author_profiles ap ON ap.id = ac.author_id
            WHERE ac.status='running'
            ORDER BY ac.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


async def set_ad_campaign_status(campaign_id: int, status: str) -> bool:
    now = utc_now()
    status = status if status in {"running", "paused", "stopped", "blocked"} else "paused"
    async with connect() as db:
        cur = await db.execute(
            "UPDATE ad_campaigns SET status=?, updated_at=? WHERE id=?",
            (status, now, campaign_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def create_promo_code(author_user_id: int, book_id: int, code: str, discount_percent: int,
                            max_uses: int) -> int:
    now = utc_now()
    clean_code = "".join(ch for ch in code.upper().strip().replace(" ", "") if ch.isalnum() or ch in "_-")[:32]
    if len(clean_code) < 3:
        raise ValueError("Promo code is too short")
    discount_percent = max(0, min(100, int(discount_percent)))
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (author_user_id,))
        author = await cur.fetchone()
        if not author:
            raise ValueError("Author profile not found")
        cur = await db.execute("SELECT id FROM books WHERE id=? AND author_id=?", (book_id, int(author["id"])))
        if await cur.fetchone() is None:
            raise ValueError("Book does not belong to author")
        cur = await db.execute(
            """
            INSERT INTO promo_codes(author_id, book_id, code, discount_percent, max_uses, used_count, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 0, 'active', ?, ?)
            """,
            (int(author["id"]), book_id, clean_code, discount_percent, max(1, int(max_uses)), now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_author_promo_codes(author_user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pc.*, b.title AS book_title
            FROM promo_codes pc
            JOIN books b ON b.id = pc.book_id
            JOIN author_profiles ap ON ap.id = pc.author_id
            WHERE ap.user_id=?
            ORDER BY pc.id DESC
            LIMIT ?
            """,
            (author_user_id, limit),
        )
        return await cur.fetchall()


async def get_promo_code(code: str) -> aiosqlite.Row | None:
    clean = "".join(ch for ch in code.upper().strip().replace(" ", "") if ch.isalnum() or ch in "_-")[:32]
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pc.*, b.title AS book_title
            FROM promo_codes pc
            JOIN books b ON b.id = pc.book_id
            WHERE pc.code=? AND pc.status='active' AND pc.used_count < pc.max_uses
            """,
            (clean,),
        )
        return await cur.fetchone()


async def get_author_promo_code(author_user_id: int, promo_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pc.*, b.title AS book_title
            FROM promo_codes pc
            JOIN books b ON b.id=pc.book_id
            JOIN author_profiles ap ON ap.id=pc.author_id
            WHERE ap.user_id=? AND pc.id=?
            """,
            (int(author_user_id), int(promo_id)),
        )
        return await cur.fetchone()


async def set_author_promo_status(author_user_id: int, promo_id: int, status: str) -> bool:
    if status not in {"active", "paused"}:
        return False
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE promo_codes
            SET status=?, updated_at=?
            WHERE id=? AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (status, now, int(promo_id), int(author_user_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_moderation_comments(limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, u.telegram_id, u.username, u.full_name,
                   b.title AS book_title, ch.title AS chapter_title
            FROM comments c
            JOIN users u ON u.id = c.user_id
            JOIN books b ON b.id = c.book_id
            JOIN chapters ch ON ch.id = c.chapter_id
            WHERE c.status='published'
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


async def set_comment_status(comment_id: int, status: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("UPDATE comments SET status=?, updated_at=? WHERE id=?", (status, now, comment_id))
        await db.commit()
        return cur.rowcount > 0


async def list_moderation_reviews(limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT r.*, u.telegram_id, u.username, u.full_name, b.title AS book_title
            FROM reviews r
            JOIN users u ON u.id = r.user_id
            JOIN books b ON b.id = r.book_id
            WHERE r.status='published'
            ORDER BY r.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


async def set_review_status(review_id: int, status: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("UPDATE reviews SET status=?, updated_at=? WHERE id=?", (status, now, review_id))
        await db.commit()
        return cur.rowcount > 0


async def record_reader_ad_event(
    *,
    user_id: int | None,
    source_book_id: int | None,
    source_chapter_id: int | None,
    promoted_book_id: int,
    placement: str,
    event_type: str = "impression",
    campaign_id: int | None = None,
) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO reader_ad_events(user_id, source_book_id, source_chapter_id, promoted_book_id, placement, event_type, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, source_book_id, source_chapter_id, promoted_book_id, placement[:32], event_type[:32], now),
        )
        if campaign_id:
            cost_key = "ad_click_cost" if event_type == "click" else "ad_impression_cost"
            cur = await db.execute("SELECT value FROM settings WHERE key=?", (cost_key,))
            row = await cur.fetchone()
            cost = int(row["value"] if row else (3 if event_type == "click" else 1))
            await db.execute(
                """
                INSERT INTO ad_campaign_events(campaign_id, user_id, source_book_id, source_chapter_id, event_type, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (campaign_id, user_id, source_book_id, source_chapter_id, event_type[:32], now),
            )
            await db.execute(
                """
                UPDATE ad_campaigns
                SET spent_units=MIN(budget_units, spent_units + ?),
                    status=CASE WHEN spent_units + ? >= budget_units THEN 'stopped' ELSE status END,
                    updated_at=?
                WHERE id=?
                """,
                (cost, cost, now, campaign_id),
            )
        await db.commit()

async def get_comment_for_moderation(comment_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, u.telegram_id, u.username, u.full_name,
                   b.title AS book_title, ch.title AS chapter_title
            FROM comments c
            JOIN users u ON u.id = c.user_id
            JOIN books b ON b.id = c.book_id
            JOIN chapters ch ON ch.id = c.chapter_id
            WHERE c.id=?
            """,
            (comment_id,),
        )
        return await cur.fetchone()


async def get_review_for_moderation(review_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT r.*, u.telegram_id, u.username, u.full_name, b.title AS book_title
            FROM reviews r
            JOIN users u ON u.id = r.user_id
            JOIN books b ON b.id = r.book_id
            WHERE r.id=?
            """,
            (review_id,),
        )
        return await cur.fetchone()


async def get_ad_campaign(campaign_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT ac.*, b.title AS book_title, ap.pen_name
            FROM ad_campaigns ac
            JOIN books b ON b.id = ac.book_id
            JOIN author_profiles ap ON ap.id = ac.author_id
            WHERE ac.id=?
            """,
            (campaign_id,),
        )
        return await cur.fetchone()

# =========================
# Stage 9 finance/ads/referrals overrides and helpers
# =========================

def clean_promo_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper().strip().replace(" ", "") if ch.isalnum() or ch in "_-")[:32]


async def get_promo_for_book(code: str, book_id: int) -> aiosqlite.Row | None:
    clean = clean_promo_code(code)
    if not clean:
        return None
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pc.*, b.title AS book_title
            FROM promo_codes pc
            JOIN books b ON b.id = pc.book_id
            WHERE pc.code=?
              AND pc.book_id=?
              AND pc.status='active'
              AND pc.used_count < pc.max_uses
              AND (pc.expires_at IS NULL OR pc.expires_at > ?)
            """,
            (clean, int(book_id), utc_now()),
        )
        return await cur.fetchone()


def _apply_discount(amount: int, discount_percent: int) -> int:
    amount = max(0, int(amount))
    discount_percent = max(0, min(100, int(discount_percent)))
    if discount_percent >= 100:
        return 0
    return max(1, int(round(amount * (100 - discount_percent) / 100)))


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    """Цель покупки: контент, покупка с промокодом или пополнение рекламы.

    Форматы:
    - vox:chapter:123
    - vox:audio:123
    - vox:book:123
    - vox:chapter:123:promo:CODE
    - vox:book:123:promo:CODE
    - vox:ad_budget:45:100
    - vox:channel_promo:12
    """
    parts = str(payload or "").split(":")
    if len(parts) < 3 or parts[0] != "vox":
        return None
    kind = parts[1]
    promo_code = None
    if len(parts) == 5 and parts[3] == "promo":
        promo_code = clean_promo_code(parts[4])
    if kind == "channel_promo":
        if len(parts) != 3:
            return None
        try:
            promotion_id = int(parts[2])
        except ValueError:
            return None
        promotion = await get_channel_promotion(promotion_id)
        if not promotion or promotion["status"] not in {"invoice", "paid", "failed"}:
            return None
        if promotion["publication_status"] != "published":
            return None
        return {
            "kind": "channel_promo",
            "target_id": promotion_id,
            "promotion_id": promotion_id,
            "book_id": int(promotion["book_id"]),
            "title": f"Канал: {promotion['book_title']}",
            "book_title": promotion["book_title"],
            "amount_stars": int(promotion["amount_stars"] or 0),
            "author_id": None,
            "promo_code": None,
            "discount_percent": 0,
        }
    if kind == "ad_budget":
        if len(parts) != 4:
            return None
        try:
            campaign_id = int(parts[2]); amount_stars = int(parts[3])
        except ValueError:
            return None
        campaign = await get_ad_campaign(campaign_id)
        if not campaign or amount_stars <= 0 or campaign["status"] == "blocked":
            return None
        return {
            "kind": "ad_budget",
            "target_id": campaign_id,
            "campaign_id": campaign_id,
            "book_id": int(campaign["book_id"]),
            "title": f"Реклама: {campaign['book_title']}",
            "book_title": campaign["book_title"],
            "amount_stars": amount_stars,
            "author_id": int(campaign["author_id"]),
            "promo_code": None,
            "discount_percent": 0,
        }
    try:
        target_id = int(parts[2])
    except ValueError:
        return None
    target: dict[str, Any] | None = None
    if kind == "chapter":
        chapter = await get_chapter(target_id)
        if chapter:
            target = {
                "kind": "chapter",
                "target_id": target_id,
                "book_id": int(chapter["book_id"]),
                "title": chapter["title"],
                "book_title": chapter["book_title"],
                "amount_stars": int(chapter["price_stars"] or 0),
                "author_id": await get_book_author_id(int(chapter["book_id"])),
            }
    elif kind == "audio":
        audio = await get_audio_chapter(target_id)
        if audio:
            target = {
                "kind": "audio",
                "target_id": target_id,
                "book_id": int(audio["book_id"]),
                "title": audio["title"],
                "book_title": audio["book_title"],
                "amount_stars": int(audio["price_stars"] or 0),
                "author_id": await get_book_author_id(int(audio["book_id"])),
            }
    elif kind == "book":
        book = await get_book(target_id)
        if book:
            target = {
                "kind": "book",
                "target_id": target_id,
                "book_id": target_id,
                "title": book["title"],
                "book_title": book["title"],
                "amount_stars": int(book["price_stars"] or 0),
                "author_id": int(book["author_id"]) if book["author_id"] is not None else None,
            }
    if not target:
        return None
    target["promo_code"] = None
    target["discount_percent"] = 0
    target["original_amount_stars"] = int(target["amount_stars"] or 0)
    if promo_code:
        promo = await get_promo_for_book(promo_code, int(target["book_id"]))
        if promo:
            target["promo_code"] = promo["code"]
            target["promo_id"] = int(promo["id"])
            target["discount_percent"] = int(promo["discount_percent"] or 0)
            target["amount_stars"] = _apply_discount(int(target["amount_stars"] or 0), int(promo["discount_percent"] or 0))
    return target


async def create_paid_purchase(
    *,
    user_id: int,
    payload: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
) -> int:
    target = await get_purchase_target(payload)
    if target is None:
        raise ValueError("Unknown purchase payload")
    now = utc_now()
    kind = target["kind"]
    book_id = target["book_id"] if kind == "book" else None
    chapter_id = target["target_id"] if kind == "chapter" else None
    audio_chapter_id = target["target_id"] if kind == "audio" else None
    purchase_kind = "ad_budget" if kind == "ad_budget" else "content"
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO purchases(user_id, book_id, chapter_id, audio_chapter_id, amount_stars, status,
                                  telegram_payment_charge_id, created_at, payload, purchase_kind)
            VALUES(?, ?, ?, ?, ?, 'paid', ?, ?, ?, ?)
            """,
            (user_id, book_id, chapter_id, audio_chapter_id, int(amount_stars), telegram_payment_charge_id, now, payload, purchase_kind),
        )
        purchase_id = int(cur.lastrowid)
        if kind == "channel_promo":
            await db.execute(
                "UPDATE book_channel_promotions SET purchase_id=?, status='paid', updated_at=? WHERE id=?",
                (purchase_id, now, int(target["promotion_id"])),
            )
            await db.commit()
            return purchase_id
        if kind == "ad_budget":
            units_per_star = int(await get_setting("ad_budget_units_per_star", "10") or 10)
            units = int(amount_stars) * units_per_star
            await db.execute(
                "UPDATE ad_campaigns SET budget_units=budget_units + ?, status=CASE WHEN status='stopped' THEN 'running' ELSE status END, updated_at=? WHERE id=?",
                (units, now, int(target["campaign_id"])),
            )
            await db.execute(
                """
                INSERT INTO ad_budget_payments(campaign_id, user_id, purchase_id, amount_stars, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (int(target["campaign_id"]), user_id, purchase_id, int(amount_stars), now),
            )
            await db.commit()
            return purchase_id
        author_id = target.get("author_id")
        if author_id is not None and int(amount_stars) > 0:
            setting_key = "commission_audio" if kind == "audio" else "commission_books"
            cur_setting = await db.execute("SELECT value FROM settings WHERE key=?", (setting_key,))
            row_setting = await cur_setting.fetchone()
            commission_percent = int(row_setting["value"] if row_setting else 20)
            cur_hold = await db.execute("SELECT value FROM settings WHERE key='hold_days_default'")
            row_hold = await cur_hold.fetchone()
            hold_days = int(row_hold["value"] if row_hold else 14)
            commission_stars = int(round(int(amount_stars) * commission_percent / 100))
            net_stars = max(0, int(amount_stars) - commission_stars)
            available_at = (datetime.now(timezone.utc) + timedelta(days=hold_days)).isoformat()
            await db.execute(
                """
                INSERT INTO author_ledger(author_id, purchase_id, source_type, source_id, gross_stars,
                                          commission_percent, commission_stars, net_stars, hold_days,
                                          available_at, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'held', ?, ?)
                """,
                (author_id, purchase_id, kind, int(target["target_id"]), int(amount_stars), commission_percent,
                 commission_stars, net_stars, hold_days, available_at, now, now),
            )
        if target.get("promo_id"):
            await db.execute("UPDATE promo_codes SET used_count=used_count + 1, updated_at=? WHERE id=?", (now, int(target["promo_id"])))
            await db.execute(
                """
                INSERT OR IGNORE INTO promo_uses(promo_code_id, user_id, purchase_id, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (int(target["promo_id"]), user_id, purchase_id, now),
            )
        await db.commit()
        return purchase_id


async def create_free_promo_purchase(user_id: int, kind: str, target_id: int, promo_code: str) -> int:
    payload = f"vox:{kind}:{int(target_id)}:promo:{clean_promo_code(promo_code)}"
    target = await get_purchase_target(payload)
    if target is None or int(target.get("amount_stars") or 0) != 0 or not target.get("promo_id"):
        raise ValueError("Промокод не даёт бесплатный доступ")
    now = utc_now()
    book_id = target["book_id"] if kind == "book" else None
    chapter_id = target["target_id"] if kind == "chapter" else None
    audio_chapter_id = target["target_id"] if kind == "audio" else None
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO purchases(user_id, book_id, chapter_id, audio_chapter_id, amount_stars, status,
                                  telegram_payment_charge_id, created_at, payload, purchase_kind)
            VALUES(?, ?, ?, ?, 0, 'paid', ?, ?, ?, 'promo_free')
            """,
            (user_id, book_id, chapter_id, audio_chapter_id, f"promo:{promo_code}:{now}", now, payload),
        )
        purchase_id = int(cur.lastrowid)
        await db.execute("UPDATE promo_codes SET used_count=used_count + 1, updated_at=? WHERE id=?", (now, int(target["promo_id"])))
        await db.execute(
            """
            INSERT OR IGNORE INTO promo_uses(promo_code_id, user_id, purchase_id, created_at)
            VALUES(?, ?, ?, ?)
            """,
            (int(target["promo_id"]), user_id, purchase_id, now),
        )
        await db.commit()
        return purchase_id


async def add_ad_budget_units(campaign_id: int, amount_stars: int) -> int:
    units_per_star = int(await get_setting("ad_budget_units_per_star", "10") or 10)
    units = max(0, int(amount_stars)) * units_per_star
    now = utc_now()
    async with connect() as db:
        await db.execute(
            "UPDATE ad_campaigns SET budget_units=budget_units + ?, updated_at=? WHERE id=?",
            (units, now, int(campaign_id)),
        )
        await db.commit()
    return units


async def get_ad_campaign_report(campaign_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM ad_campaigns WHERE id=?", (int(campaign_id),))
        campaign = await cur.fetchone()
        if not campaign:
            return {}
        cur = await db.execute(
            """
            SELECT
                COUNT(CASE WHEN event_type='impression' THEN 1 END) AS impressions,
                COUNT(CASE WHEN event_type='click' THEN 1 END) AS clicks
            FROM ad_campaign_events
            WHERE campaign_id=?
            """,
            (int(campaign_id),),
        )
        events = await cur.fetchone()
        cur = await db.execute("SELECT COALESCE(SUM(amount_stars), 0) AS stars FROM ad_budget_payments WHERE campaign_id=?", (int(campaign_id),))
        stars = await cur.fetchone()
        return {
            "budget_units": int(campaign["budget_units"] or 0),
            "spent_units": int(campaign["spent_units"] or 0),
            "left_units": max(0, int(campaign["budget_units"] or 0) - int(campaign["spent_units"] or 0)),
            "impressions": int(events["impressions"] or 0) if events else 0,
            "clicks": int(events["clicks"] or 0) if events else 0,
            "stars_paid": int(stars["stars"] or 0) if stars else 0,
        }


async def register_referral(referrer_user_id: int, referred_user_id: int) -> bool:
    if int(referrer_user_id) == int(referred_user_id):
        return False
    now = utc_now()
    async with connect() as db:
        try:
            await db.execute(
                """
                INSERT INTO referrals(referrer_user_id, referred_user_id, bonus_given, created_at)
                VALUES(?, ?, 0, ?)
                """,
                (referrer_user_id, referred_user_id, now),
            )
            await db.commit()
            return True
        except Exception:
            return False


async def reward_referral_if_needed(referred_user_id: int) -> bool:
    now = utc_now()
    friend_bonus = int(await get_setting("referral_friend_bonus", "10") or 10)
    reader_bonus = int(await get_setting("referral_reader_bonus", "10") or 10)
    async with connect() as db:
        cur = await db.execute("SELECT * FROM referrals WHERE referred_user_id=? AND bonus_given=0", (referred_user_id,))
        ref = await cur.fetchone()
        if not ref:
            return False
        for uid, amount, reason in [
            (int(ref["referrer_user_id"]), reader_bonus, "referral_invite"),
            (int(ref["referred_user_id"]), friend_bonus, "referral_join"),
        ]:
            await db.execute("INSERT INTO bonus_wallets(user_id, balance, created_at, updated_at) VALUES(?, 0, ?, ?) ON CONFLICT(user_id) DO NOTHING", (uid, now, now))
            await db.execute("UPDATE bonus_wallets SET balance=balance + ?, updated_at=? WHERE user_id=?", (amount, now, uid))
            await db.execute("INSERT INTO bonus_transactions(user_id, amount, reason, source_type, source_id, created_at) VALUES(?, ?, ?, 'referral', ?, ?)", (uid, amount, reason, str(ref["id"]), now))
        await db.execute("UPDATE referrals SET bonus_given=1 WHERE id=?", (int(ref["id"]),))
        await db.commit()
        return True


async def get_referral_stats(user_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM referrals WHERE referrer_user_id=?", (user_id,))
        total = await cur.fetchone()
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM referrals WHERE referrer_user_id=? AND bonus_given=1", (user_id,))
        rewarded = await cur.fetchone()
        return {"invited": int(total["cnt"] or 0), "rewarded": int(rewarded["cnt"] or 0)}


async def create_complaint(user_id: int | None, target_type: str, target_id: str, reason: str) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO complaints(user_id, target_type, target_id, reason, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, 'new', ?, ?)
            """,
            (user_id, target_type[:32], str(target_id)[:64], reason[:1200], now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_complaints(status: str = "new", limit: int = 30) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, u.telegram_id, u.username, u.full_name
            FROM complaints c
            LEFT JOIN users u ON u.id = c.user_id
            WHERE c.status=?
            ORDER BY c.id ASC
            LIMIT ?
            """,
            (status, limit),
        )
        return await cur.fetchall()


async def get_complaint(complaint_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, u.telegram_id, u.username, u.full_name
            FROM complaints c
            LEFT JOIN users u ON u.id = c.user_id
            WHERE c.id=?
            """,
            (int(complaint_id),),
        )
        return await cur.fetchone()


async def set_complaint_status(complaint_id: int, status: str, handled_by_user_id: int | None = None) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "UPDATE complaints SET status=?, handled_by_user_id=?, updated_at=? WHERE id=?",
            (status, handled_by_user_id, now, int(complaint_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def search_users(query: str, limit: int = 20) -> list[aiosqlite.Row]:
    q = f"%{query.strip().lstrip('@').lower()}%"
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT u.*, ap.pen_name
            FROM users u
            LEFT JOIN author_profiles ap ON ap.user_id = u.id
            WHERE lower(COALESCE(u.username,'')) LIKE ?
               OR lower(COALESCE(u.full_name,'')) LIKE ?
               OR CAST(u.telegram_id AS TEXT) LIKE ?
               OR lower(COALESCE(ap.pen_name,'')) LIKE ?
            ORDER BY u.id DESC
            LIMIT ?
            """,
            (q, q, q, q, limit),
        )
        return await cur.fetchall()


async def search_books(query: str, limit: int = 20) -> list[aiosqlite.Row]:
    q = f"%{query.strip().lower()}%"
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*, ap.pen_name
            FROM books b
            LEFT JOIN author_profiles ap ON ap.id = b.author_id
            WHERE lower(b.title) LIKE ? OR lower(COALESCE(b.description,'')) LIKE ? OR lower(COALESCE(ap.pen_name,'')) LIKE ?
            ORDER BY b.id DESC
            LIMIT ?
            """,
            (q, q, q, limit),
        )
        return await cur.fetchall()


async def set_user_blocked(user_id: int, blocked: bool) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("UPDATE users SET is_blocked=?, updated_at=? WHERE id=?", (1 if blocked else 0, now, int(user_id)))
        await db.commit()
        return cur.rowcount > 0


async def set_book_blocked(book_id: int, blocked: bool) -> bool:
    status = "blocked" if blocked else "hidden"
    return await set_book_publication_status(int(book_id), status)


# =========================
# Stage 10 payouts: author withdrawal requests, holds and freezes
# =========================

async def _author_by_user_id(author_user_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT ap.*, u.telegram_id, u.username, u.full_name
            FROM author_profiles ap
            JOIN users u ON u.id = ap.user_id
            WHERE ap.user_id=?
            """,
            (int(author_user_id),),
        )
        return await cur.fetchone()


async def _release_ready_author_ledger(db: aiosqlite.Connection, author_id: int | None = None) -> None:
    now = utc_now()
    if author_id is None:
        await db.execute(
            "UPDATE author_ledger SET status='available', updated_at=? WHERE status='held' AND available_at <= ?",
            (now, now),
        )
    else:
        await db.execute(
            "UPDATE author_ledger SET status='available', updated_at=? WHERE author_id=? AND status='held' AND available_at <= ?",
            (now, int(author_id), now),
        )


async def get_author_finance_summary(author_user_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(author_user_id),))
        author = await cur.fetchone()
        if not author:
            return {
                "gross": 0, "commission": 0, "net": 0, "held": 0, "available": 0,
                "requested": 0, "paid": 0, "refunded": 0, "frozen": 0,
            }
        author_id = int(author["id"])
        await _release_ready_author_ledger(db, author_id)
        await db.commit()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status!='refunded' THEN gross_stars ELSE 0 END), 0) AS gross,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN commission_stars ELSE 0 END), 0) AS commission,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN net_stars ELSE 0 END), 0) AS net,
                COALESCE(SUM(CASE WHEN status='held' THEN net_stars ELSE 0 END), 0) AS held,
                COALESCE(SUM(CASE WHEN status='available' THEN net_stars ELSE 0 END), 0) AS available,
                COALESCE(SUM(CASE WHEN status='payout_requested' THEN net_stars ELSE 0 END), 0) AS requested,
                COALESCE(SUM(CASE WHEN status='paid' THEN net_stars ELSE 0 END), 0) AS paid,
                COALESCE(SUM(CASE WHEN status='refunded' THEN gross_stars ELSE 0 END), 0) AS refunded,
                COALESCE(SUM(CASE WHEN status='held' THEN net_minor ELSE 0 END), 0) AS held_minor,
                COALESCE(SUM(CASE WHEN status='available' THEN net_minor ELSE 0 END), 0) AS available_minor,
                COALESCE(SUM(CASE WHEN status='payout_requested' THEN net_minor ELSE 0 END), 0) AS requested_minor,
                COALESCE(SUM(CASE WHEN status='paid' THEN net_minor ELSE 0 END), 0) AS paid_minor,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN net_minor ELSE 0 END), 0) AS net_minor
            FROM author_ledger
            WHERE author_id=?
            """,
            (author_id,),
        )
        row = await cur.fetchone()
        cur = await db.execute("SELECT is_active FROM author_payout_freezes WHERE author_id=? AND is_active=1", (author_id,))
        frozen = await cur.fetchone()
        result = {key: int(row[key] or 0) for key in row.keys()} if row else {}
        result["frozen"] = 1 if frozen else 0
        return result


async def get_platform_finance_summary() -> dict[str, int]:
    async with connect() as db:
        await _release_ready_author_ledger(db)
        await db.commit()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN p.status='paid' THEN p.amount_stars ELSE 0 END), 0) AS paid_gross,
                COALESCE(SUM(CASE WHEN p.status='refunded' THEN p.amount_stars ELSE 0 END), 0) AS refunded_gross,
                COUNT(CASE WHEN p.status='paid' THEN 1 END) AS paid_count,
                COUNT(CASE WHEN p.status='refunded' THEN 1 END) AS refunded_count
            FROM purchases p
            """
        )
        purchases = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status!='refunded' THEN commission_stars ELSE 0 END), 0) AS platform_commission,
                COALESCE(SUM(CASE WHEN status='held' THEN net_stars ELSE 0 END), 0) AS held_authors,
                COALESCE(SUM(CASE WHEN status='available' THEN net_stars ELSE 0 END), 0) AS available_authors,
                COALESCE(SUM(CASE WHEN status='payout_requested' THEN net_stars ELSE 0 END), 0) AS requested_authors,
                COALESCE(SUM(CASE WHEN status='paid' THEN net_stars ELSE 0 END), 0) AS paid_authors
            FROM author_ledger
            """
        )
        ledger = await cur.fetchone()
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM author_payout_requests WHERE status IN ('new','approved')")
        payouts = await cur.fetchone()
        return {
            "paid_gross": int(purchases["paid_gross"] or 0),
            "refunded_gross": int(purchases["refunded_gross"] or 0),
            "paid_count": int(purchases["paid_count"] or 0),
            "refunded_count": int(purchases["refunded_count"] or 0),
            "platform_commission": int(ledger["platform_commission"] or 0),
            "held_authors": int(ledger["held_authors"] or 0),
            "available_authors": int(ledger["available_authors"] or 0),
            "requested_authors": int(ledger["requested_authors"] or 0),
            "paid_authors": int(ledger["paid_authors"] or 0),
            "payout_requests_open": int(payouts["cnt"] or 0),
        }


async def set_author_payout_method(author_user_id: int, method_type: str, details: str) -> None:
    author = await _author_by_user_id(author_user_id)
    if not author:
        raise ValueError("Профиль автора не найден")
    now = utc_now()
    method_type = (method_type or "manual").strip()[:32] or "manual"
    details = (details or "").strip()[:2000]
    if len(details) < 5:
        raise ValueError("Реквизиты слишком короткие")
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO author_payout_methods(author_id, method_type, details, status, created_at, updated_at)
            VALUES(?, ?, ?, 'active', ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
                method_type=excluded.method_type,
                details=excluded.details,
                status='active',
                updated_at=excluded.updated_at
            """,
            (int(author["id"]), method_type, details, now, now),
        )
        await db.commit()


async def get_author_payout_method(author_user_id: int) -> aiosqlite.Row | None:
    author = await _author_by_user_id(author_user_id)
    if not author:
        return None
    async with connect() as db:
        cur = await db.execute("SELECT * FROM author_payout_methods WHERE author_id=? AND status='active'", (int(author["id"]),))
        return await cur.fetchone()


async def is_author_payout_frozen(author_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute("SELECT 1 FROM author_payout_freezes WHERE author_id=? AND is_active=1", (int(author_id),))
        return await cur.fetchone() is not None


async def set_author_payout_frozen(author_id: int, frozen: bool, reason: str = "", actor_user_id: int | None = None) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO author_payout_freezes(author_id, is_active, reason, created_by_user_id, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
                is_active=excluded.is_active,
                reason=excluded.reason,
                created_by_user_id=excluded.created_by_user_id,
                updated_at=excluded.updated_at
            """,
            (int(author_id), 1 if frozen else 0, reason[:1000], actor_user_id, now, now),
        )
        await db.commit()


async def list_author_payout_requests(author_user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    author = await _author_by_user_id(author_user_id)
    if not author:
        return []
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM author_payout_requests
            WHERE author_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(author["id"]), int(limit)),
        )
        return await cur.fetchall()


async def create_author_payout_request(author_user_id: int) -> int:
    author = await _author_by_user_id(author_user_id)
    if not author:
        raise ValueError("Сначала зарегистрируйтесь как автор")
    author_id = int(author["id"])
    method = await get_author_payout_method(author_user_id)
    if not method:
        raise ValueError("Сначала укажите реквизиты для выплаты")
    if await is_author_payout_frozen(author_id):
        raise ValueError("Выплаты автора заморожены до проверки")
    min_stars = int(await get_setting("payout_min_stars", "100") or 100)
    now = utc_now()
    async with connect() as db:
        await _release_ready_author_ledger(db, author_id)
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM author_payout_requests WHERE author_id=? AND status IN ('new','approved')", (author_id,))
        existing = await cur.fetchone()
        if int(existing["cnt"] or 0) > 0:
            raise ValueError("У вас уже есть активная заявка на выплату")
        cur = await db.execute(
            "SELECT COALESCE(SUM(net_stars), 0) AS amount, COALESCE(SUM(net_minor), 0) AS amount_minor "
            "FROM author_ledger WHERE author_id=? AND status='available'",
            (author_id,),
        )
        row = await cur.fetchone()
        amount = int(row["amount"] or 0)
        amount_minor = int(row["amount_minor"] or 0)
        if amount < min_stars:
            raise ValueError(f"Минимальная сумма вывода: {min_stars} Stars")
        settlement_note = f"{amount} Stars начислений = {amount_minor / 100:.2f} ₽ по зафиксированным курсам продаж"
        cur = await db.execute(
            """
            INSERT INTO author_payout_requests(author_id, author_user_id, amount_stars, amount_minor,
                                               method_type, payout_details, settlement_note,
                                               status, requested_at, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
            """,
            (author_id, int(author_user_id), amount, amount_minor, method["method_type"], method["details"],
             settlement_note, now, now, now),
        )
        payout_id = int(cur.lastrowid)
        await db.execute("UPDATE author_ledger SET status='payout_requested', updated_at=? WHERE author_id=? AND status='available'", (now, author_id))
        await db.execute(
            "INSERT INTO author_payout_logs(payout_request_id, actor_user_id, action, note, created_at) VALUES(?, ?, 'created', ?, ?)",
            (payout_id, int(author_user_id), f"Заявка на {amount} Stars · {amount_minor / 100:.2f} ₽", now),
        )
        await db.commit()
        return payout_id


async def list_payout_requests(status: str = "new", limit: int = 30) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pr.*, ap.pen_name, u.telegram_id, u.username, u.full_name,
                   COALESCE(fr.is_active, 0) AS is_frozen
            FROM author_payout_requests pr
            JOIN author_profiles ap ON ap.id = pr.author_id
            JOIN users u ON u.id = pr.author_user_id
            LEFT JOIN author_payout_freezes fr ON fr.author_id = pr.author_id AND fr.is_active=1
            WHERE pr.status=?
            ORDER BY pr.id ASC
            LIMIT ?
            """,
            (status, int(limit)),
        )
        return await cur.fetchall()


async def get_payout_request(payout_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pr.*, ap.pen_name, u.telegram_id, u.username, u.full_name,
                   COALESCE(fr.is_active, 0) AS is_frozen, fr.reason AS freeze_reason
            FROM author_payout_requests pr
            JOIN author_profiles ap ON ap.id = pr.author_id
            JOIN users u ON u.id = pr.author_user_id
            LEFT JOIN author_payout_freezes fr ON fr.author_id = pr.author_id AND fr.is_active=1
            WHERE pr.id=?
            """,
            (int(payout_id),),
        )
        return await cur.fetchone()


async def set_payout_request_status(payout_id: int, status: str, actor_user_id: int | None = None, note: str = "") -> bool:
    allowed = {"new", "approved", "paid", "rejected", "frozen"}
    if status not in allowed:
        raise ValueError("Неверный статус выплаты")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT * FROM author_payout_requests WHERE id=?", (int(payout_id),))
        req = await cur.fetchone()
        if not req:
            return False
        old = req["status"]
        if old == "paid":
            return False
        handled_at = now if status in {"approved", "rejected", "frozen"} else req["handled_at"]
        paid_at = now if status == "paid" else req["paid_at"]
        await db.execute(
            """
            UPDATE author_payout_requests
            SET status=?, handled_by_user_id=?, handled_at=?, paid_at=?, note=?, updated_at=?
            WHERE id=?
            """,
            (status, actor_user_id, handled_at, paid_at, note[:1200], now, int(payout_id)),
        )
        if status == "paid":
            await db.execute("UPDATE author_ledger SET status='paid', updated_at=? WHERE author_id=? AND status='payout_requested'", (now, int(req["author_id"])))
        elif status == "rejected":
            await db.execute("UPDATE author_ledger SET status='available', updated_at=? WHERE author_id=? AND status='payout_requested'", (now, int(req["author_id"])))
        elif status == "frozen":
            await db.execute("UPDATE author_ledger SET status='held', updated_at=? WHERE author_id=? AND status='payout_requested'", (now, int(req["author_id"])))
        await db.execute(
            "INSERT INTO author_payout_logs(payout_request_id, actor_user_id, action, note, created_at) VALUES(?, ?, ?, ?, ?)",
            (int(payout_id), actor_user_id, status, note[:1200], now),
        )
        await db.commit()
        return True


async def get_payout_settings() -> dict[str, str]:
    return {
        "payout_min_stars": await get_setting("payout_min_stars", "100"),
        "payout_default_method": await get_setting("payout_default_method", "TON"),
        "payout_manual_review": await get_setting("payout_manual_review", "1"),
        "payout_freeze_on_complaint": await get_setting("payout_freeze_on_complaint", "1"),
        "payout_owner_note": await get_setting("payout_owner_note", "Выплата выполняется вручную после проверки."),
    }


# Stage 11 / v1.9.3: legal acceptances and documents
async def accept_legal_document(
    user_id: int,
    doc_code: str,
    doc_version: str,
    *,
    doc_hash: str = "",
    source: str = "bot",
    telegram_message_id: int | None = None,
    user_agent: str = "",
    ip_hash: str = "",
) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO legal_acceptances(
                user_id, doc_code, doc_version, accepted_at, doc_hash,
                acceptance_source, telegram_message_id, user_agent, ip_hash, withdrawn_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(user_id, doc_code, doc_version) DO UPDATE SET
                accepted_at=excluded.accepted_at,
                doc_hash=excluded.doc_hash,
                acceptance_source=excluded.acceptance_source,
                telegram_message_id=excluded.telegram_message_id,
                user_agent=excluded.user_agent,
                ip_hash=excluded.ip_hash,
                withdrawn_at=NULL
            """,
            (
                int(user_id), str(doc_code), str(doc_version), now, str(doc_hash or ""),
                str(source or "bot")[:32], telegram_message_id,
                str(user_agent or "")[:500], str(ip_hash or "")[:128],
            ),
        )
        await db.execute(
            """
            INSERT INTO legal_document_events(
                user_id, doc_code, doc_version, event_type, doc_hash, source, created_at
            ) VALUES(?, ?, ?, 'accepted', ?, ?, ?)
            """,
            (int(user_id), str(doc_code), str(doc_version), str(doc_hash or ""), str(source or "bot")[:32], now),
        )
        await db.commit()


async def has_accepted_legal_document(user_id: int, doc_code: str, doc_version: str, doc_hash: str = "") -> bool:
    async with connect() as db:
        sql = (
            "SELECT id FROM legal_acceptances "
            "WHERE user_id=? AND doc_code=? AND doc_version=? AND withdrawn_at IS NULL"
        )
        params: list[Any] = [int(user_id), str(doc_code), str(doc_version)]
        if doc_hash:
            sql += " AND doc_hash=?"
            params.append(str(doc_hash))
        sql += " LIMIT 1"
        cur = await db.execute(sql, tuple(params))
        return await cur.fetchone() is not None


async def get_missing_legal_documents(user_id: int, documents: list[tuple[str, str, str]]) -> list[str]:
    missing: list[str] = []
    for code, version, digest in documents:
        if not await has_accepted_legal_document(user_id, code, version, digest):
            missing.append(code)
    return missing


async def withdraw_legal_document(user_id: int, doc_code: str, doc_version: str) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE legal_acceptances SET withdrawn_at=?
            WHERE user_id=? AND doc_code=? AND doc_version=? AND withdrawn_at IS NULL
            """,
            (now, int(user_id), str(doc_code), str(doc_version)),
        )
        if cur.rowcount:
            await db.execute(
                """
                INSERT INTO legal_document_events(
                    user_id, doc_code, doc_version, event_type, doc_hash, source, created_at
                ) VALUES(?, ?, ?, 'withdrawn', '', 'bot', ?)
                """,
                (int(user_id), str(doc_code), str(doc_version), now),
            )
        await db.commit()
        return cur.rowcount > 0


async def get_legal_acceptances(user_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM legal_acceptances
            WHERE user_id=?
            ORDER BY accepted_at DESC, id DESC
            """,
            (int(user_id),),
        )
        return await cur.fetchall()


DEFAULT_USER_PREFERENCES = {
    "theme": "system",
    "font_size": "normal",
    "notifications": "1",
    "notifications_chapters": "1",
    "notifications_audio": "1",
    "notifications_discounts": "1",
}


async def get_user_preferences(user_id: int) -> dict[str, str]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT theme, font_size, notifications, notifications_chapters,
                   notifications_audio, notifications_discounts
            FROM user_preferences WHERE user_id=?
            """,
            (user_id,),
        )
        row = await cur.fetchone()
        if not row:
            return dict(DEFAULT_USER_PREFERENCES)
        return {key: str(row[key]) for key in DEFAULT_USER_PREFERENCES}


async def set_user_preference(user_id: int, key: str, value: str) -> dict[str, str]:
    allowed = set(DEFAULT_USER_PREFERENCES)
    if key not in allowed:
        raise ValueError("Unknown preference")
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO user_preferences(
                user_id, theme, font_size, notifications, notifications_chapters,
                notifications_audio, notifications_discounts, updated_at
            )
            VALUES(?, 'system', 'normal', 1, 1, 1, 1, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, now),
        )
        if key.startswith("notifications"):
            normalized = 1 if str(value) != "0" else 0
            await db.execute(f"UPDATE user_preferences SET {key}=?, updated_at=? WHERE user_id=?", (normalized, now, user_id))
        else:
            await db.execute(f"UPDATE user_preferences SET {key}=?, updated_at=? WHERE user_id=?", (value, now, user_id))
        await db.commit()
    return await get_user_preferences(user_id)


async def reset_user_preferences(user_id: int) -> dict[str, str]:
    async with connect() as db:
        await db.execute("DELETE FROM user_preferences WHERE user_id=?", (user_id,))
        await db.commit()
    return dict(DEFAULT_USER_PREFERENCES)


async def list_authors_for_owner(limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT ap.*, u.telegram_id, u.username, u.full_name, COUNT(b.id) AS books_count
            FROM author_profiles ap
            JOIN users u ON u.id = ap.user_id
            LEFT JOIN books b ON b.author_id = ap.id
            GROUP BY ap.id
            ORDER BY ap.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


async def list_blocked_users(limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, telegram_id, username, full_name
            FROM users
            WHERE is_blocked = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


async def was_channel_post_sent(book_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1
            FROM audit_logs
            WHERE target_type='book' AND target_id=?
              AND action IN ('channel_post_sent','channel_post_sent_web','book_published_channel_posted')
            LIMIT 1
            """,
            (str(int(book_id)),),
        )
        return await cur.fetchone() is not None


async def list_recent_channel_posts(limit: int = 10) -> list[aiosqlite.Row]:
    # Канал фиксируется через audit_logs; статус выводится по самому действию,
    # чтобы старые записи без after_value тоже отображались правильно.
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT target_id AS book_id,
                   CASE
                     WHEN action IN ('channel_post_failed','channel_post_failed_web','book_published_channel_failed') THEN 'failed'
                     WHEN action='channel_post_skipped' THEN 'not_configured'
                     ELSE 'sent'
                   END AS status,
                   before_value AS details,
                   created_at
            FROM audit_logs
            WHERE action IN (
                'book_published_channel_posted','book_published_channel_failed',
                'channel_post_sent','channel_post_failed',
                'channel_post_sent_web','channel_post_failed_web',
                'channel_post_skipped'
            )
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


async def _ensure_v184_schema(db: aiosqlite.Connection) -> None:
    """Мягкая миграция публикаций, платного продвижения и проверки копий книг."""
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS book_channel_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            requested_by_user_id INTEGER,
            purchase_id INTEGER,
            source TEXT NOT NULL DEFAULT 'paid',
            amount_stars INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'invoice',
            expires_at TEXT,
            posted_at TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(requested_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_book_promotions_book_status
            ON book_channel_promotions(book_id, source, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_books_source_hash ON books(source_file_hash);
        CREATE INDEX IF NOT EXISTS idx_books_normalized_title ON books(normalized_title);
        """
    )
    for key, value in {
        "channel_promotion_price_stars": "50",
        "channel_promotion_cooldown_days": "30",
    }.items():
        await db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )
    await db.execute(
        "UPDATE books SET normalized_title=LOWER(TRIM(title)) WHERE normalized_title IS NULL OR TRIM(normalized_title)=''"
    )


async def _ensure_v185_schema(db: aiosqlite.Connection) -> None:
    """Очередь безопасной автоматической проверки и напоминаний модерации."""
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS book_moderation_queue (
            book_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            risk_level TEXT NOT NULL DEFAULT 'manual',
            reasons TEXT,
            submitted_at TEXT NOT NULL,
            last_notified_at TEXT,
            next_reminder_at TEXT,
            reminder_count INTEGER NOT NULL DEFAULT 0,
            resolved_at TEXT,
            resolved_by_user_id INTEGER,
            resolution TEXT,
            moderator_note TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(resolved_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_book_moderation_due
            ON book_moderation_queue(status, next_reminder_at);
        """
    )
    for key, value in {
        "moderation_first_reminder_hours": "6",
        "moderation_repeat_reminder_hours": "12",
    }.items():
        await db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )


async def list_books_for_duplicate_check(exclude_book_id: int | None = None) -> list[aiosqlite.Row]:
    async with connect() as db:
        if exclude_book_id is None:
            cur = await db.execute(
                """
                SELECT b.id, b.author_id, b.title, b.normalized_title, b.source_file_hash,
                       b.publication_status, a.pen_name
                FROM books b LEFT JOIN author_profiles a ON a.id=b.author_id
                WHERE b.publication_status!='deleted'
                ORDER BY b.id DESC
                """
            )
        else:
            cur = await db.execute(
                """
                SELECT b.id, b.author_id, b.title, b.normalized_title, b.source_file_hash,
                       b.publication_status, a.pen_name
                FROM books b LEFT JOIN author_profiles a ON a.id=b.author_id
                WHERE b.publication_status!='deleted' AND b.id!=?
                ORDER BY b.id DESC
                """,
                (int(exclude_book_id),),
            )
        return await cur.fetchall()


async def update_book_import_fingerprint(
    book_id: int, *, filename: str, source_file_hash: str, duplicate_override: bool = False
) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET source_file_name=?, source_file_hash=?, duplicate_override=?, updated_at=?
            WHERE id=? AND publication_status!='deleted'
            """,
            (str(filename)[:180], str(source_file_hash)[:64], 1 if duplicate_override else 0, now, int(book_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def set_book_duplicate_override(book_id: int, allowed: bool) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "UPDATE books SET duplicate_override=?, updated_at=? WHERE id=?",
            (1 if allowed else 0, utc_now(), int(book_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_channel_promotion_price() -> int:
    return max(1, int(await get_setting("channel_promotion_price_stars", "50") or 50))


async def get_channel_promotion_cooldown_days() -> int:
    return max(1, int(await get_setting("channel_promotion_cooldown_days", "30") or 30))


async def get_channel_promotion_availability(book_id: int, user_id: int | None = None) -> dict[str, Any]:
    cooldown_days = await get_channel_promotion_cooldown_days()
    now_dt = datetime.now(timezone.utc)
    cutoff = (now_dt - timedelta(days=cooldown_days)).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM book_channel_promotions
            WHERE book_id=? AND source='paid' AND status='sent' AND posted_at>=?
            ORDER BY posted_at DESC, id DESC LIMIT 1
            """,
            (int(book_id), cutoff),
        )
        sent = await cur.fetchone()
        if sent:
            posted_at = datetime.fromisoformat(str(sent["posted_at"]).replace("Z", "+00:00"))
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            available_at = posted_at + timedelta(days=cooldown_days)
            return {"allowed": False, "reason": "cooldown", "available_at": available_at.isoformat(), "promotion_id": int(sent["id"])}
        if user_id is not None:
            cur = await db.execute(
                """
                SELECT * FROM book_channel_promotions
                WHERE book_id=? AND requested_by_user_id=? AND source='paid'
                  AND status IN ('paid','failed')
                ORDER BY id DESC LIMIT 1
                """,
                (int(book_id), int(user_id)),
            )
            retry = await cur.fetchone()
            if retry:
                return {"allowed": True, "reason": "retry", "promotion_id": int(retry["id"]), "paid": True}
        return {"allowed": True, "reason": "available", "available_at": None}


async def reserve_channel_promotion(book_id: int, user_id: int, amount_stars: int) -> int:
    availability = await get_channel_promotion_availability(book_id, user_id)
    if not availability.get("allowed"):
        raise ValueError("Эту книгу уже публиковали в канале. Повтор будет доступен позже.")
    if availability.get("reason") == "retry":
        return int(availability["promotion_id"])
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    expires = (now_dt + timedelta(minutes=30)).isoformat()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "UPDATE book_channel_promotions SET status='expired', updated_at=? WHERE status='invoice' AND expires_at<?",
            (now, now),
        )
        cur = await db.execute(
            "SELECT id FROM book_channel_promotions WHERE book_id=? AND source='paid' AND status='invoice' AND expires_at>? ORDER BY id DESC LIMIT 1",
            (int(book_id), now),
        )
        active = await cur.fetchone()
        if active:
            await db.commit()
            return int(active["id"])
        cur = await db.execute(
            """
            INSERT INTO book_channel_promotions(
                book_id, requested_by_user_id, source, amount_stars, status, expires_at, created_at, updated_at
            ) VALUES(?, ?, 'paid', ?, 'invoice', ?, ?, ?)
            """,
            (int(book_id), int(user_id), int(amount_stars), expires, now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def get_channel_promotion(promotion_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, b.title AS book_title, b.publication_status, b.cover_path
            FROM book_channel_promotions p JOIN books b ON b.id=p.book_id
            WHERE p.id=?
            """,
            (int(promotion_id),),
        )
        return await cur.fetchone()


async def mark_channel_promotion_paid(promotion_id: int, purchase_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "UPDATE book_channel_promotions SET purchase_id=?, status='paid', updated_at=? WHERE id=? AND status IN ('invoice','paid','failed')",
            (int(purchase_id), utc_now(), int(promotion_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def finish_channel_promotion(promotion_id: int, *, sent: bool, error: str = "") -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE book_channel_promotions
            SET status=?, posted_at=CASE WHEN ? THEN ? ELSE posted_at END, error=?, updated_at=?
            WHERE id=?
            """,
            ("sent" if sent else "failed", 1 if sent else 0, now, str(error)[:1000], now, int(promotion_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def record_owner_channel_promotion(book_id: int, user_id: int | None, *, sent: bool, error: str = "") -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO book_channel_promotions(
                book_id, requested_by_user_id, source, amount_stars, status, posted_at, error, created_at, updated_at
            ) VALUES(?, ?, 'owner', 0, ?, ?, ?, ?, ?)
            """,
            (int(book_id), user_id, "sent" if sent else "failed", now if sent else None, str(error)[:1000], now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_recent_channel_promotions(limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, b.title AS book_title, u.username, u.full_name
            FROM book_channel_promotions p
            JOIN books b ON b.id=p.book_id
            LEFT JOIN users u ON u.id=p.requested_by_user_id
            ORDER BY p.id DESC LIMIT ?
            """,
            (max(1, int(limit)),),
        )
        return await cur.fetchall()


# v1.7.6 — безопасная финансовая цепочка и мобильный центр управления
async def _ensure_v176_schema(db: aiosqlite.Connection) -> None:
    """Мягкая миграция: связывает строки дохода с конкретной заявкой на выплату."""
    cur = await db.execute("PRAGMA table_info(author_ledger)")
    existing = {row[1] for row in await cur.fetchall()}
    if "payout_request_id" not in existing:
        await _execute_schema_ddl(db, "ALTER TABLE author_ledger ADD COLUMN payout_request_id INTEGER")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_author_ledger_payout_request ON author_ledger(payout_request_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_purchases_charge_id ON purchases(telegram_payment_charge_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_refund_purchase_status ON refund_requests(purchase_id, status)"
    )


async def create_paid_purchase(
    *,
    user_id: int,
    payload: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
) -> int:
    """Фиксирует платёж один раз и не допускает подмену суммы или повторную запись update."""
    target = await get_purchase_target(payload)
    if target is None:
        raise ValueError("Покупка не найдена")
    amount_stars = int(amount_stars)
    expected = int(target.get("amount_stars") or 0)
    if amount_stars != expected:
        raise ValueError("Сумма платежа не совпадает с ценой")
    charge_id = (telegram_payment_charge_id or "").strip()
    if not charge_id:
        raise ValueError("Не указан идентификатор платежа")

    now = utc_now()
    kind = str(target["kind"])
    book_id = int(target["book_id"]) if kind in {"book", "channel_promo", "graphic_volume"} else None
    chapter_id = int(target["target_id"]) if kind == "chapter" else None
    audio_chapter_id = int(target["target_id"]) if kind == "audio" else None
    graphic_chapter_id = int(target["target_id"]) if kind == "graphic" else None
    graphic_volume_number = int(target["volume_number"]) if kind == "graphic_volume" else None
    purchase_kind = {
        "ad_budget": "ad_budget",
        "channel_promo": "channel_promotion",
        "graphic": "graphic_chapter",
        "graphic_volume": "graphic_volume",
    }.get(kind, "content")

    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT id, user_id, amount_stars, payload FROM purchases WHERE telegram_payment_charge_id=? ORDER BY id LIMIT 1",
            (charge_id,),
        )
        existing = await cur.fetchone()
        if existing:
            if int(existing["user_id"]) != int(user_id) or int(existing["amount_stars"]) != amount_stars or str(existing["payload"] or "") != payload:
                raise ValueError("Идентификатор платежа уже использован")
            await db.commit()
            return int(existing["id"])

        cur = await db.execute(
            """
            INSERT INTO purchases(user_id, book_id, chapter_id, audio_chapter_id, graphic_chapter_id,
                                  graphic_volume_number, amount_stars, status, telegram_payment_charge_id,
                                  created_at, payload, purchase_kind)
            VALUES(?, ?, ?, ?, ?, ?, ?, 'paid', ?, ?, ?, ?)
            """,
            (int(user_id), book_id, chapter_id, audio_chapter_id, graphic_chapter_id,
             graphic_volume_number, amount_stars, charge_id, now, payload, purchase_kind),
        )
        purchase_id = int(cur.lastrowid)

        if kind == "channel_promo":
            await db.execute(
                "UPDATE book_channel_promotions SET purchase_id=?, status='paid', updated_at=? WHERE id=?",
                (purchase_id, now, int(target["promotion_id"])),
            )
            await db.commit()
            return purchase_id

        if kind == "ad_budget":
            cur_setting = await db.execute("SELECT value FROM settings WHERE key='ad_budget_units_per_star'")
            row_setting = await cur_setting.fetchone()
            units_per_star = int(row_setting["value"] if row_setting else 10)
            units = amount_stars * units_per_star
            await db.execute(
                "UPDATE ad_campaigns SET budget_units=budget_units + ?, status=CASE WHEN status='stopped' THEN 'running' ELSE status END, updated_at=? WHERE id=?",
                (units, now, int(target["campaign_id"])),
            )
            await db.execute(
                "INSERT INTO ad_budget_payments(campaign_id, user_id, purchase_id, amount_stars, created_at) VALUES(?, ?, ?, ?, ?)",
                (int(target["campaign_id"]), int(user_id), purchase_id, amount_stars, now),
            )
            await db.commit()
            return purchase_id

        author_id = target.get("author_id")
        if author_id is not None and amount_stars > 0:
            setting_key = "commission_audio" if kind == "audio" else "commission_books"
            cur_setting = await db.execute("SELECT value FROM settings WHERE key=?", (setting_key,))
            row_setting = await cur_setting.fetchone()
            commission_percent = max(0, min(100, int(row_setting["value"] if row_setting else 20)))
            cur_hold = await db.execute("SELECT value FROM settings WHERE key='hold_days_default'")
            row_hold = await cur_hold.fetchone()
            hold_days = max(0, int(row_hold["value"] if row_hold else 14))
            commission_stars = int(round(amount_stars * commission_percent / 100))
            net_stars = max(0, amount_stars - commission_stars)
            cur_rate = await db.execute("SELECT value FROM settings WHERE key='payments_stars_author_rate_minor'")
            row_rate = await cur_rate.fetchone()
            try:
                settlement_rate_minor = max(1, int(row_rate["value"] if row_rate else 100))
            except (TypeError, ValueError):
                settlement_rate_minor = 100
            net_minor = net_stars * settlement_rate_minor
            available_at = (datetime.now(timezone.utc) + timedelta(days=hold_days)).isoformat()
            await db.execute(
                """
                INSERT INTO author_ledger(author_id, purchase_id, source_type, source_id, gross_stars,
                                          commission_percent, commission_stars, net_stars,
                                          settlement_rate_minor, net_minor, hold_days,
                                          available_at, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'held', ?, ?)
                """,
                (int(author_id), purchase_id, kind, int(target["target_id"]), amount_stars, commission_percent,
                 commission_stars, net_stars, settlement_rate_minor, net_minor, hold_days, available_at, now, now),
            )
        if target.get("promo_id"):
            await db.execute("UPDATE promo_codes SET used_count=used_count + 1, updated_at=? WHERE id=?", (now, int(target["promo_id"])))
            await db.execute(
                "INSERT OR IGNORE INTO promo_uses(promo_code_id, user_id, purchase_id, created_at) VALUES(?, ?, ?, ?)",
                (int(target["promo_id"]), int(user_id), purchase_id, now),
            )
        await db.commit()
        return purchase_id


async def create_refund_request(purchase_id: int, user_id: int, reason: str) -> int:
    """Создаёт возврат только владельцу покупки, в разрешённый срок и без дублей."""
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Опишите причину подробнее")
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT * FROM purchases WHERE id=?", (int(purchase_id),))
        purchase = await cur.fetchone()
        if not purchase or int(purchase["user_id"]) != int(user_id):
            raise ValueError("Покупка не найдена")
        if purchase["status"] != "paid":
            raise ValueError("Эта покупка уже не доступна для возврата")
        if str(purchase["purchase_kind"] or "content") != "content":
            raise ValueError("Для этой операции возврат оформляется через поддержку")
        created_at = datetime.fromisoformat(str(purchase["created_at"]).replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        cur = await db.execute("SELECT value FROM settings WHERE key='refund_window_days'")
        row = await cur.fetchone()
        window_days = max(0, int(row["value"] if row else 14))
        if window_days and now_dt > created_at + timedelta(days=window_days):
            raise ValueError("Срок подачи запроса на возврат истёк")
        cur = await db.execute(
            "SELECT id FROM refund_requests WHERE purchase_id=? AND status IN ('new','pending','refunded') ORDER BY id DESC LIMIT 1",
            (int(purchase_id),),
        )
        existing = await cur.fetchone()
        if existing:
            raise ValueError("Запрос по этой покупке уже создан")
        cur = await db.execute(
            "INSERT INTO refund_requests(purchase_id, user_id, reason, status, created_at, updated_at) VALUES(?, ?, ?, 'new', ?, ?)",
            (int(purchase_id), int(user_id), reason[:1000], now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def finalize_refund(refund_id: int, handled_by_user_id: int | None, note: str = "Возврат Stars выполнен") -> bool:
    """После успешного ответа Telegram атомарно закрывает доступ и корректирует доход автора."""
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            SELECT rr.status AS refund_status, rr.purchase_id, p.status AS purchase_status
            FROM refund_requests rr JOIN purchases p ON p.id=rr.purchase_id WHERE rr.id=?
            """,
            (int(refund_id),),
        )
        row = await cur.fetchone()
        if not row:
            return False
        if row["refund_status"] == "refunded" and row["purchase_status"] == "refunded":
            await db.commit()
            return True
        if row["refund_status"] not in {"new", "pending"} or row["purchase_status"] not in {"paid", "canceling"}:
            return False
        await db.execute("UPDATE purchases SET status='refunded' WHERE id=?", (int(row["purchase_id"]),))
        await db.execute(
            "UPDATE author_ledger SET status='refunded', updated_at=? WHERE purchase_id=?",
            (now, int(row["purchase_id"])),
        )
        await db.execute(
            "UPDATE refund_requests SET status='refunded', handled_by_user_id=?, moderator_note=?, updated_at=? WHERE id=?",
            (handled_by_user_id, note[:1000], now, int(refund_id)),
        )
        await db.commit()
        return True


async def reject_refund_request(refund_id: int, handled_by_user_id: int | None, note: str = "Возврат отклонён") -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "UPDATE refund_requests SET status='rejected', handled_by_user_id=?, moderator_note=?, updated_at=? WHERE id=? AND status IN ('new','pending')",
            (handled_by_user_id, note[:1000], now, int(refund_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def create_author_payout_request(author_user_id: int) -> int:
    author = await _author_by_user_id(author_user_id)
    if not author:
        raise ValueError("Сначала зарегистрируйтесь как автор")
    author_id = int(author["id"])
    method = await get_author_payout_method(author_user_id)
    if not method:
        raise ValueError("Сначала укажите реквизиты для выплаты")
    if await is_author_payout_frozen(author_id):
        raise ValueError("Выплаты автора заморожены до проверки")
    min_stars = int(await get_setting("payout_min_stars", "100") or 100)
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await _release_ready_author_ledger(db, author_id)
        cur = await db.execute(
            "SELECT COUNT(*) AS cnt FROM author_payout_requests WHERE author_id=? AND status IN ('new','approved','frozen')",
            (author_id,),
        )
        existing = await cur.fetchone()
        if int(existing["cnt"] or 0) > 0:
            raise ValueError("У вас уже есть активная заявка на выплату")
        cur = await db.execute(
            "SELECT COALESCE(SUM(net_stars), 0) AS amount, COALESCE(SUM(net_minor), 0) AS amount_minor "
            "FROM author_ledger WHERE author_id=? AND status='available'",
            (author_id,),
        )
        row = await cur.fetchone()
        amount = int(row["amount"] or 0)
        amount_minor = int(row["amount_minor"] or 0)
        if amount < min_stars:
            raise ValueError(f"Минимальная сумма вывода: {min_stars} Stars")
        settlement_note = f"{amount} Stars начислений = {amount_minor / 100:.2f} ₽ по зафиксированным курсам продаж"
        cur = await db.execute(
            """
            INSERT INTO author_payout_requests(author_id, author_user_id, amount_stars, amount_minor,
                                               method_type, payout_details, settlement_note,
                                               status, requested_at, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
            """,
            (author_id, int(author_user_id), amount, amount_minor, method["method_type"], method["details"],
             settlement_note, now, now, now),
        )
        payout_id = int(cur.lastrowid)
        await db.execute(
            "UPDATE author_ledger SET status='payout_requested', payout_request_id=?, updated_at=? WHERE author_id=? AND status='available'",
            (payout_id, now, author_id),
        )
        await db.execute(
            "INSERT INTO author_payout_logs(payout_request_id, actor_user_id, action, note, created_at) VALUES(?, ?, 'created', ?, ?)",
            (payout_id, int(author_user_id), f"Заявка на {amount} Stars · {amount_minor / 100:.2f} ₽", now),
        )
        await db.commit()
        return payout_id


async def set_payout_request_status(payout_id: int, status: str, actor_user_id: int | None = None, note: str = "") -> bool:
    allowed = {"new", "approved", "paid", "rejected", "frozen"}
    if status not in allowed:
        raise ValueError("Неверный статус выплаты")
    transitions = {
        "new": {"approved", "rejected", "frozen"},
        "approved": {"paid", "rejected", "frozen"},
        "frozen": {"new", "rejected"},
        "rejected": set(),
        "paid": set(),
    }
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT * FROM author_payout_requests WHERE id=?", (int(payout_id),))
        req = await cur.fetchone()
        if not req:
            return False
        old = str(req["status"])
        if old == status:
            await db.commit()
            return True
        if status not in transitions.get(old, set()):
            return False
        if status == "paid":
            cur = await db.execute("SELECT 1 FROM author_payout_freezes WHERE author_id=? AND is_active=1", (int(req["author_id"]),))
            if await cur.fetchone():
                return False
        handled_at = now if status in {"approved", "rejected", "frozen"} else req["handled_at"]
        paid_at = now if status == "paid" else req["paid_at"]
        await db.execute(
            "UPDATE author_payout_requests SET status=?, handled_by_user_id=?, handled_at=?, paid_at=?, note=?, updated_at=? WHERE id=?",
            (status, actor_user_id, handled_at, paid_at, note[:1200], now, int(payout_id)),
        )
        target_ledger_status = {
            "paid": "paid",
            "rejected": "available",
            "frozen": "held",
            "new": "payout_requested",
        }.get(status)
        if target_ledger_status:
            cur = await db.execute(
                "UPDATE author_ledger SET status=?, updated_at=? WHERE payout_request_id=?",
                (target_ledger_status, now, int(payout_id)),
            )
            if cur.rowcount == 0:
                legacy_from = "payout_requested" if status in {"paid", "rejected", "frozen"} else "held"
                await db.execute(
                    "UPDATE author_ledger SET status=?, payout_request_id=?, updated_at=? WHERE author_id=? AND status=?",
                    (target_ledger_status, int(payout_id), now, int(req["author_id"]), legacy_from),
                )
        await db.execute(
            "INSERT INTO author_payout_logs(payout_request_id, actor_user_id, action, note, created_at) VALUES(?, ?, ?, ?, ?)",
            (int(payout_id), actor_user_id, status, note[:1200], now),
        )
        await db.commit()
        return True


async def get_control_queue_counts() -> dict[str, int]:
    """Сводка очередей без персональных и служебных данных."""
    async with connect() as db:
        queries = {
            "books_review": "SELECT COUNT(*) FROM books WHERE publication_status='review'",
            "complaints_new": "SELECT COUNT(*) FROM complaints WHERE status='new'",
            "refunds_new": "SELECT COUNT(*) FROM refund_requests WHERE status='new'",
            "payouts_new": "SELECT COUNT(*) FROM author_payout_requests WHERE status='new'",
            "payouts_approved": "SELECT COUNT(*) FROM author_payout_requests WHERE status='approved'",
            "comments": "SELECT COUNT(*) FROM comments WHERE status='published'",
            "reviews": "SELECT COUNT(*) FROM reviews WHERE status='published'",
            "ads_running": "SELECT COUNT(*) FROM ad_campaigns WHERE status='running'",
        }
        result: dict[str, int] = {}
        for key, sql in queries.items():
            cur = await db.execute(sql)
            row = await cur.fetchone()
            result[key] = int(row[0] or 0) if row else 0
        return result


async def enqueue_book_moderation(book_id: int, reasons: list[str], risk_level: str = "manual") -> None:
    now_dt = datetime.now(timezone.utc)
    first_hours = max(1, int(await get_setting("moderation_first_reminder_hours", "6") or 6))
    next_reminder = (now_dt + timedelta(hours=first_hours)).isoformat()
    clean_reasons = "\n".join(dict.fromkeys(str(item).strip() for item in reasons if str(item).strip()))[:4000]
    now = now_dt.isoformat()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO book_moderation_queue(
                book_id, status, risk_level, reasons, submitted_at,
                last_notified_at, next_reminder_at, reminder_count, updated_at
            ) VALUES(?, 'pending', ?, ?, ?, NULL, ?, 0, ?)
            ON CONFLICT(book_id) DO UPDATE SET
                status='pending',
                risk_level=excluded.risk_level,
                reasons=excluded.reasons,
                submitted_at=excluded.submitted_at,
                last_notified_at=NULL,
                next_reminder_at=excluded.next_reminder_at,
                reminder_count=0,
                resolved_at=NULL,
                resolved_by_user_id=NULL,
                resolution=NULL,
                moderator_note=NULL,
                updated_at=excluded.updated_at
            """,
            (int(book_id), str(risk_level)[:32], clean_reasons, now, next_reminder, now),
        )
        await db.commit()


async def get_book_moderation_entry(book_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM book_moderation_queue WHERE book_id=?",
            (int(book_id),),
        )
        return await cur.fetchone()


async def mark_book_moderation_notified(book_id: int, *, reminder: bool) -> None:
    now_dt = datetime.now(timezone.utc)
    repeat_hours = max(1, int(await get_setting("moderation_repeat_reminder_hours", "12") or 12))
    next_reminder = (now_dt + timedelta(hours=repeat_hours)).isoformat()
    async with connect() as db:
        await db.execute(
            """
            UPDATE book_moderation_queue
            SET last_notified_at=?, next_reminder_at=?,
                reminder_count=reminder_count + ?, updated_at=?
            WHERE book_id=? AND status='pending'
            """,
            (now_dt.isoformat(), next_reminder, 1 if reminder else 0, now_dt.isoformat(), int(book_id)),
        )
        await db.commit()


async def list_due_book_moderation_reminders(limit: int = 25) -> list[aiosqlite.Row]:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT q.*, b.title, b.author_id, a.pen_name,
                   a.user_id AS author_user_id, u.telegram_id AS author_telegram_id
            FROM book_moderation_queue q
            JOIN books b ON b.id=q.book_id
            LEFT JOIN author_profiles a ON a.id=b.author_id
            LEFT JOIN users u ON u.id=a.user_id
            WHERE q.status='pending'
              AND b.publication_status='review'
              AND q.next_reminder_at IS NOT NULL
              AND q.next_reminder_at<=?
            ORDER BY q.next_reminder_at ASC
            LIMIT ?
            """,
            (now, max(1, int(limit))),
        )
        return await cur.fetchall()


async def resolve_book_moderation(
    book_id: int,
    *,
    resolution: str,
    actor_user_id: int | None,
    note: str = "",
) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            UPDATE book_moderation_queue
            SET status='resolved', resolved_at=?, resolved_by_user_id=?,
                resolution=?, moderator_note=?, next_reminder_at=NULL, updated_at=?
            WHERE book_id=?
            """,
            (now, actor_user_id, str(resolution)[:64], str(note)[:4000], now, int(book_id)),
        )
        await db.commit()


async def list_book_moderation_staff() -> list[aiosqlite.Row]:
    """Администраторы и модераторы с действующим правом проверки книг."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT DISTINCT u.id AS user_id, u.telegram_id, u.username, u.full_name
            FROM admin_staff s
            JOIN users u ON u.id=s.user_id
            JOIN admin_permissions p ON p.admin_id=s.id
            WHERE s.is_active=1 AND p.allowed=1 AND p.permission_code='mod_books'
              AND u.is_blocked=0
            ORDER BY u.id
            """
        )
        return await cur.fetchall()


async def get_author_auto_moderation_stats(author_id: int | None, current_book_id: int) -> dict[str, int]:
    if author_id is None:
        return {"trust_level": 0, "published_books": 0, "open_complaints": 0}
    async with connect() as db:
        cur = await db.execute("SELECT trust_level FROM author_profiles WHERE id=?", (int(author_id),))
        author = await cur.fetchone()
        cur = await db.execute(
            "SELECT COUNT(*) FROM books WHERE author_id=? AND id!=? AND publication_status='published'",
            (int(author_id), int(current_book_id)),
        )
        published = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT COUNT(*)
            FROM complaints c
            JOIN books b ON b.id=c.target_id
            WHERE c.target_type='book' AND b.author_id=? AND c.status IN ('new','pending')
            """,
            (int(author_id),),
        )
        complaints = await cur.fetchone()
        return {
            "trust_level": int(author[0] or 0) if author else 0,
            "published_books": int(published[0] or 0) if published else 0,
            "open_complaints": int(complaints[0] or 0) if complaints else 0,
        }


async def get_user_by_id(user_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM users WHERE id=?", (int(user_id),))
        return await cur.fetchone()


async def get_tts_progress(user_id: int, chapter_id: int, voice_code: str = "anna") -> int:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT position_seconds
            FROM tts_progress
            WHERE user_id=? AND chapter_id=? AND voice_code=?
            """,
            (int(user_id), int(chapter_id), str(voice_code or "anna")),
        )
        row = await cur.fetchone()
        return max(0, int(row["position_seconds"] or 0)) if row else 0


async def save_tts_progress(
    user_id: int,
    chapter_id: int,
    position_seconds: int,
    voice_code: str = "anna",
) -> None:
    now = utc_now()
    position = max(0, int(position_seconds or 0))
    voice = str(voice_code or "anna")[:32]
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO tts_progress(user_id, chapter_id, voice_code, position_seconds, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id, chapter_id, voice_code)
            DO UPDATE SET position_seconds=excluded.position_seconds, updated_at=excluded.updated_at
            """,
            (int(user_id), int(chapter_id), voice, position, now),
        )
        await db.commit()

# v1.9.1 — базовый модуль комиксов, манги, манхвы и вебтунов
async def _ensure_v191_schema(db: aiosqlite.Connection) -> None:
    """Мягкая миграция графических произведений без разрушения старой базы."""
    cur = await db.execute("PRAGMA table_info(books)")
    book_columns = {row[1] for row in await cur.fetchall()}
    if "content_type" not in book_columns:
        await _execute_schema_ddl(db, "ALTER TABLE books ADD COLUMN content_type TEXT NOT NULL DEFAULT 'book'")
    if "reading_mode" not in book_columns:
        await _execute_schema_ddl(db, "ALTER TABLE books ADD COLUMN reading_mode TEXT NOT NULL DEFAULT 'ltr'")

    cur = await db.execute("PRAGMA table_info(purchases)")
    purchase_columns = {row[1] for row in await cur.fetchall()}
    if "graphic_chapter_id" not in purchase_columns:
        await _execute_schema_ddl(db, "ALTER TABLE purchases ADD COLUMN graphic_chapter_id INTEGER")

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS graphic_chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            number INTEGER NOT NULL,
            title TEXT NOT NULL,
            reading_mode TEXT NOT NULL DEFAULT 'inherit',
            is_free INTEGER NOT NULL DEFAULT 1,
            price_stars INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'draft',
            source_filename TEXT,
            pages_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(book_id, number),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS graphic_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graphic_chapter_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            source_filename TEXT,
            mime_type TEXT NOT NULL DEFAULT 'image/webp',
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            file_size INTEGER NOT NULL DEFAULT 0,
            checksum TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(graphic_chapter_id, page_number),
            FOREIGN KEY(graphic_chapter_id) REFERENCES graphic_chapters(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS graphic_reading_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            graphic_chapter_id INTEGER NOT NULL,
            page_number INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, graphic_chapter_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(graphic_chapter_id) REFERENCES graphic_chapters(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_graphic_chapters_book_status
            ON graphic_chapters(book_id, status, number);
        CREATE INDEX IF NOT EXISTS idx_graphic_pages_chapter_number
            ON graphic_pages(graphic_chapter_id, page_number);
        CREATE INDEX IF NOT EXISTS idx_graphic_progress_user
            ON graphic_reading_progress(user_id, updated_at);
        """
    )
    await db.execute(
        "UPDATE graphic_chapters SET status='published' WHERE status='draft' AND book_id IN "
        "(SELECT id FROM books WHERE publication_status='published')"
    )




# v1.9.6 — только Telegram Stars и раздельные расчётные курсы
async def _ensure_v196_schema(db: aiosqlite.Connection) -> None:
    now = utc_now()
    cur = await db.execute("PRAGMA table_info(author_ledger)")
    columns = {row[1] for row in await cur.fetchall()}
    if "settlement_rate_minor" not in columns:
        await _execute_schema_ddl(db, "ALTER TABLE author_ledger ADD COLUMN settlement_rate_minor INTEGER NOT NULL DEFAULT 100")
    if "net_minor" not in columns:
        await _execute_schema_ddl(db, "ALTER TABLE author_ledger ADD COLUMN net_minor INTEGER NOT NULL DEFAULT 0")
    await db.execute(
        "UPDATE author_ledger SET settlement_rate_minor=100 WHERE settlement_rate_minor IS NULL OR settlement_rate_minor<=0"
    )
    await db.execute(
        "UPDATE author_ledger SET net_minor=net_stars*settlement_rate_minor WHERE net_minor IS NULL OR net_minor=0"
    )

    cur = await db.execute("PRAGMA table_info(author_payout_requests)")
    payout_columns = {row[1] for row in await cur.fetchall()}
    if "amount_minor" not in payout_columns:
        await _execute_schema_ddl(db, "ALTER TABLE author_payout_requests ADD COLUMN amount_minor INTEGER NOT NULL DEFAULT 0")
    if "settlement_note" not in payout_columns:
        await _execute_schema_ddl(db, "ALTER TABLE author_payout_requests ADD COLUMN settlement_note TEXT")
    await db.execute(
        "UPDATE author_payout_requests SET amount_minor=amount_stars*100 WHERE amount_minor IS NULL OR amount_minor=0"
    )

    for key, value in {
        "payments_stars_enabled": "1",
        "payments_stars_buyer_rate_minor": "145",
        "payments_stars_author_rate_minor": "100",
        "payments_yookassa_external_enabled": "0",
        "payments_yookassa_telegram_provider_enabled": "0",
        "payments_yookassa_payouts_enabled": "0",
        "rub_payments_enabled": "0",
        "rub_payouts_enabled": "0",
    }.items():
        await db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=CASE WHEN excluded.key LIKE 'payments_yookassa_%' OR excluded.key IN ('rub_payments_enabled','rub_payouts_enabled') THEN '0' ELSE settings.value END, updated_at=excluded.updated_at",
            (key, value, now),
        )


# v1.9.7 — адаптивные страницы, отдельное файловое хранилище и офлайн-кэш
async def _ensure_v197_schema(db: aiosqlite.Connection) -> None:
    cur = await db.execute("PRAGMA table_info(graphic_pages)")
    columns = {row[1] for row in await cur.fetchall()}
    if "variants_json" not in columns:
        await _execute_schema_ddl(db, "ALTER TABLE graphic_pages ADD COLUMN variants_json TEXT NOT NULL DEFAULT '{}'")
    if "storage_backend" not in columns:
        await _execute_schema_ddl(db, "ALTER TABLE graphic_pages ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'local'")
    if "storage_key" not in columns:
        await _execute_schema_ddl(db, "ALTER TABLE graphic_pages ADD COLUMN storage_key TEXT NOT NULL DEFAULT ''")
    await db.execute(
        "UPDATE graphic_pages SET storage_backend='local' WHERE storage_backend IS NULL OR storage_backend=''"
    )
    await db.execute(
        "UPDATE graphic_pages SET storage_key=file_path WHERE storage_key IS NULL OR storage_key=''"
    )
    cur = await db.execute("PRAGMA table_info(graphic_chapters)")
    chapter_columns = {row[1] for row in await cur.fetchall()}
    if "volume_number" not in chapter_columns:
        await _execute_schema_ddl(db, "ALTER TABLE graphic_chapters ADD COLUMN volume_number INTEGER NOT NULL DEFAULT 1")
    if "volume_title" not in chapter_columns:
        await _execute_schema_ddl(db, "ALTER TABLE graphic_chapters ADD COLUMN volume_title TEXT NOT NULL DEFAULT ''")
    await db.execute("UPDATE graphic_chapters SET volume_number=1 WHERE volume_number IS NULL OR volume_number<1")


# v1.9.8 — продажи графических глав и томов, предпросмотр и постраничная модерация
async def _ensure_v198_schema(db: aiosqlite.Connection) -> None:
    now = utc_now()
    cur = await db.execute("PRAGMA table_info(purchases)")
    purchase_columns = {row[1] for row in await cur.fetchall()}
    if "graphic_volume_number" not in purchase_columns:
        await _execute_schema_ddl(db, "ALTER TABLE purchases ADD COLUMN graphic_volume_number INTEGER")

    cur = await db.execute("PRAGMA table_info(graphic_chapters)")
    chapter_columns = {row[1] for row in await cur.fetchall()}
    chapter_migrations = {
        "preview_pages": "ALTER TABLE graphic_chapters ADD COLUMN preview_pages INTEGER NOT NULL DEFAULT 3",
        "moderation_status": "ALTER TABLE graphic_chapters ADD COLUMN moderation_status TEXT NOT NULL DEFAULT 'approved'",
        "moderation_note": "ALTER TABLE graphic_chapters ADD COLUMN moderation_note TEXT NOT NULL DEFAULT ''",
        "moderated_at": "ALTER TABLE graphic_chapters ADD COLUMN moderated_at TEXT",
        "moderated_by_user_id": "ALTER TABLE graphic_chapters ADD COLUMN moderated_by_user_id INTEGER",
    }
    for name, sql in chapter_migrations.items():
        if name not in chapter_columns:
            await _execute_schema_ddl(db, sql)

    cur = await db.execute("PRAGMA table_info(graphic_pages)")
    page_columns = {row[1] for row in await cur.fetchall()}
    page_migrations = {
        "moderation_status": "ALTER TABLE graphic_pages ADD COLUMN moderation_status TEXT NOT NULL DEFAULT 'approved'",
        "moderation_note": "ALTER TABLE graphic_pages ADD COLUMN moderation_note TEXT NOT NULL DEFAULT ''",
        "moderated_at": "ALTER TABLE graphic_pages ADD COLUMN moderated_at TEXT",
        "moderated_by_user_id": "ALTER TABLE graphic_pages ADD COLUMN moderated_by_user_id INTEGER",
    }
    for name, sql in page_migrations.items():
        if name not in page_columns:
            await _execute_schema_ddl(db, sql)

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS graphic_volume_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            volume_number INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            is_free INTEGER NOT NULL DEFAULT 0,
            price_stars INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(book_id, volume_number),
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS graphic_page_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            graphic_page_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            resolution_note TEXT NOT NULL DEFAULT '',
            resolved_by_user_id INTEGER,
            resolved_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, graphic_page_id, status),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(graphic_page_id) REFERENCES graphic_pages(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_graphic_volume_settings_book
            ON graphic_volume_settings(book_id, volume_number, status);
        CREATE INDEX IF NOT EXISTS idx_graphic_page_reports_status
            ON graphic_page_reports(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_purchases_graphic_volume
            ON purchases(user_id, book_id, graphic_volume_number, status);
        """
    )
    await db.execute("UPDATE graphic_chapters SET preview_pages=3 WHERE preview_pages IS NULL OR preview_pages<0")
    await db.execute("UPDATE graphic_chapters SET moderation_status='approved' WHERE moderation_status IS NULL OR moderation_status='' ")
    await db.execute("UPDATE graphic_pages SET moderation_status='approved' WHERE moderation_status IS NULL OR moderation_status='' ")
    # Создаём настройки томов для уже существующих глав. По умолчанию том продаётся
    # только после явной установки цены автором, поэтому price_stars остаётся 0.
    await db.execute(
        """
        INSERT OR IGNORE INTO graphic_volume_settings(book_id, volume_number, title, is_free, price_stars, status, created_at, updated_at)
        SELECT book_id, COALESCE(volume_number, 1), MAX(COALESCE(volume_title, '')), 0, 0, 'active', ?, ?
        FROM graphic_chapters WHERE status!='deleted'
        GROUP BY book_id, COALESCE(volume_number, 1)
        """,
        (now, now),
    )


# v1.9.9 — гибкие пакеты любых глав
async def _ensure_v199_schema(db: aiosqlite.Connection) -> None:
    # Гибкие пакеты глав: покупатель получает не фиксированный диапазон, а
    # указанное количество персональных открытий любых платных глав этой книги.
    cur = await db.execute("PRAGMA table_info(purchases)")
    purchase_columns = {row[1] for row in await cur.fetchall()}
    if "chapter_package_id" not in purchase_columns:
        await _execute_schema_ddl(db, "ALTER TABLE purchases ADD COLUMN chapter_package_id INTEGER")

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS chapter_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            chapters_count INTEGER NOT NULL,
            price_stars INTEGER NOT NULL,
            content_scope TEXT NOT NULL DEFAULT 'text',
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chapter_package_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            package_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            content_scope TEXT NOT NULL DEFAULT 'text',
            total_credits INTEGER NOT NULL,
            remaining_credits INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(package_id) REFERENCES chapter_packages(id) ON DELETE RESTRICT,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chapter_package_unlocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            balance_id INTEGER NOT NULL,
            purchase_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            chapter_id INTEGER,
            graphic_chapter_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, chapter_id),
            UNIQUE(user_id, graphic_chapter_id),
            CHECK((chapter_id IS NOT NULL AND graphic_chapter_id IS NULL) OR
                  (chapter_id IS NULL AND graphic_chapter_id IS NOT NULL)),
            FOREIGN KEY(balance_id) REFERENCES chapter_package_balances(id) ON DELETE CASCADE,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(graphic_chapter_id) REFERENCES graphic_chapters(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_chapter_packages_book_active
            ON chapter_packages(book_id, is_active, sort_order, chapters_count);
        CREATE INDEX IF NOT EXISTS idx_chapter_package_balances_user_book
            ON chapter_package_balances(user_id, book_id, status, remaining_credits, created_at);
        CREATE INDEX IF NOT EXISTS idx_chapter_package_unlocks_user_book
            ON chapter_package_unlocks(user_id, book_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_purchases_chapter_package
            ON purchases(user_id, chapter_package_id, status);
        """
    )


# v1.9.3 — юридический контур, рублёвый учёт и подготовка выплат ЮKassa
async def _ensure_v193_schema(db: aiosqlite.Connection) -> None:
    now = utc_now()
    cur = await db.execute("PRAGMA table_info(legal_acceptances)")
    columns = {row[1] for row in await cur.fetchall()}
    migrations = {
        "doc_hash": "ALTER TABLE legal_acceptances ADD COLUMN doc_hash TEXT NOT NULL DEFAULT ''",
        "acceptance_source": "ALTER TABLE legal_acceptances ADD COLUMN acceptance_source TEXT NOT NULL DEFAULT 'bot'",
        "telegram_message_id": "ALTER TABLE legal_acceptances ADD COLUMN telegram_message_id INTEGER",
        "user_agent": "ALTER TABLE legal_acceptances ADD COLUMN user_agent TEXT",
        "ip_hash": "ALTER TABLE legal_acceptances ADD COLUMN ip_hash TEXT",
        "withdrawn_at": "ALTER TABLE legal_acceptances ADD COLUMN withdrawn_at TEXT",
    }
    for name, sql in migrations.items():
        if name not in columns:
            await _execute_schema_ddl(db, sql)

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS legal_document_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            doc_code TEXT NOT NULL,
            doc_version TEXT NOT NULL,
            event_type TEXT NOT NULL,
            doc_hash TEXT,
            source TEXT NOT NULL DEFAULT 'bot',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS author_financial_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL UNIQUE,
            legal_status TEXT NOT NULL DEFAULT 'unverified',
            legal_name TEXT,
            inn TEXT,
            ogrn TEXT,
            country TEXT NOT NULL DEFAULT 'RU',
            sbp_phone_encrypted TEXT,
            sbp_bank_id TEXT,
            sbp_bank_name TEXT,
            verification_status TEXT NOT NULL DEFAULT 'draft',
            verified_at TEXT,
            verified_by_user_id INTEGER,
            rejection_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS author_rub_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            source_id INTEGER,
            payment_id TEXT,
            currency TEXT NOT NULL DEFAULT 'RUB',
            gross_minor INTEGER NOT NULL,
            commission_percent INTEGER NOT NULL DEFAULT 20,
            commission_minor INTEGER NOT NULL,
            net_minor INTEGER NOT NULL,
            hold_days INTEGER NOT NULL DEFAULT 14,
            available_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'held',
            payout_request_id INTEGER,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(payment_id, source_kind, source_id),
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS author_rub_payout_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'RUB',
            destination_type TEXT NOT NULL DEFAULT 'sbp',
            phone_encrypted TEXT,
            bank_id TEXT,
            bank_name TEXT,
            provider TEXT NOT NULL DEFAULT 'yookassa',
            provider_payout_id TEXT,
            idempotence_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'new',
            failure_reason TEXT,
            requested_at TEXT NOT NULL,
            handled_at TEXT,
            paid_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_legal_events_user_created
            ON legal_document_events(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_author_rub_ledger_author_status
            ON author_rub_ledger(author_id, status, available_at);
        CREATE INDEX IF NOT EXISTS idx_author_rub_payout_status
            ON author_rub_payout_requests(status, requested_at);
        """
    )
    cur = await db.execute("PRAGMA table_info(author_financial_profiles)")
    financial_columns = {row[1] for row in await cur.fetchall()}
    if "verified_by_user_id" not in financial_columns:
        await _execute_schema_ddl(db, "ALTER TABLE author_financial_profiles ADD COLUMN verified_by_user_id INTEGER")
    if "rejection_reason" not in financial_columns:
        await _execute_schema_ddl(db, "ALTER TABLE author_financial_profiles ADD COLUMN rejection_reason TEXT")

    for key, value in {
        "legal_terms_version": "2026-07-10",
        "legal_personal_data_version": "2026-07-10",
        "legal_author_license_version": "2026-07-10",
        "legal_author_data_version": "2026-07-10",
        "commission_rub_percent": "20",
        "rub_hold_days": "14",
        "rub_payout_min_minor": "10000",
        "pricing_model": "commission_from_final_price",
        "rub_payments_enabled": "0",
        "rub_payouts_enabled": "0",
    }.items():
        await db.execute(
            """
            INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, value, now),
        )


async def list_graphic_chapters_for_book(
    book_id: int,
    published_only: bool = False,
) -> list[aiosqlite.Row]:
    status = "status='published'" if published_only else "status!='deleted'"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT gc.*,
                   (SELECT COUNT(*) FROM graphic_pages gp WHERE gp.graphic_chapter_id=gc.id) AS actual_pages_count
            FROM graphic_chapters gc
            WHERE gc.book_id=? AND {status}
            ORDER BY gc.volume_number, gc.number, gc.id
            """,
            (int(book_id),),
        )
        return await cur.fetchall()


async def get_graphic_chapter(graphic_chapter_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT gc.*, b.title AS book_title, b.publication_status, b.content_type,
                   b.reading_mode AS book_reading_mode, b.allow_download, b.author_id, b.price_stars AS book_price_stars,
                   b.pricing_type AS book_pricing_type, ap.user_id AS author_user_id,
                   u.telegram_id AS author_telegram_id
            FROM graphic_chapters gc
            JOIN books b ON b.id=gc.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            LEFT JOIN users u ON u.id=ap.user_id
            WHERE gc.id=?
            """,
            (int(graphic_chapter_id),),
        )
        return await cur.fetchone()


async def list_graphic_pages(graphic_chapter_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM graphic_pages
            WHERE graphic_chapter_id=?
            ORDER BY page_number, id
            """,
            (int(graphic_chapter_id),),
        )
        return await cur.fetchall()


async def get_graphic_page(graphic_page_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT gp.*, gc.book_id, gc.status AS chapter_status, gc.title AS chapter_title,
                   b.publication_status, ap.user_id AS author_user_id
            FROM graphic_pages gp
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gp.id=? AND gc.status!='deleted' AND b.publication_status!='deleted'
            """,
            (int(graphic_page_id),),
        )
        return await cur.fetchone()


async def list_graphic_pages_for_author(
    graphic_chapter_id: int,
    author_user_id: int,
) -> list[aiosqlite.Row] | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1
            FROM graphic_chapters gc
            JOIN books b ON b.id=gc.book_id
            JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gc.id=? AND ap.user_id=? AND gc.status!='deleted' AND b.publication_status!='deleted'
            """,
            (int(graphic_chapter_id), int(author_user_id)),
        )
        if not await cur.fetchone():
            return None
        cur = await db.execute(
            "SELECT * FROM graphic_pages WHERE graphic_chapter_id=? ORDER BY page_number, id",
            (int(graphic_chapter_id),),
        )
        return await cur.fetchall()


async def reorder_graphic_pages_for_author(
    graphic_chapter_id: int,
    author_user_id: int,
    ordered_page_ids: list[int],
) -> bool:
    clean_ids = [int(value) for value in ordered_page_ids]
    if not clean_ids or len(clean_ids) != len(set(clean_ids)):
        return False
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT gp.id
            FROM graphic_pages gp
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gp.graphic_chapter_id=? AND ap.user_id=?
              AND gc.status!='deleted' AND b.publication_status!='deleted'
            ORDER BY gp.page_number, gp.id
            """,
            (int(graphic_chapter_id), int(author_user_id)),
        )
        current_ids = [int(row["id"]) for row in await cur.fetchall()]
        if set(current_ids) != set(clean_ids) or len(current_ids) != len(clean_ids):
            return False
        for index, page_id in enumerate(clean_ids, 1):
            await db.execute(
                "UPDATE graphic_pages SET page_number=?, updated_at=? WHERE id=? AND graphic_chapter_id=?",
                (-index, now, page_id, int(graphic_chapter_id)),
            )
        for index, page_id in enumerate(clean_ids, 1):
            await db.execute(
                "UPDATE graphic_pages SET page_number=?, updated_at=? WHERE id=? AND graphic_chapter_id=?",
                (index, now, page_id, int(graphic_chapter_id)),
            )
        await db.execute(
            "UPDATE graphic_chapters SET pages_count=?, updated_at=? WHERE id=?",
            (len(clean_ids), now, int(graphic_chapter_id)),
        )
        await db.commit()
        return True


async def update_graphic_page_file_for_author(
    graphic_page_id: int,
    author_user_id: int,
    *,
    source_filename: str,
    mime_type: str,
    width: int,
    height: int,
    file_size: int,
    checksum: str,
    variants_json: str = "{}",
    storage_backend: str = "local",
    storage_key: str = "",
) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE graphic_pages
            SET source_filename=?, mime_type=?, width=?, height=?, file_size=?, checksum=?,
                variants_json=?, storage_backend=?, storage_key=?, updated_at=?
            WHERE id=? AND graphic_chapter_id IN (
                SELECT gc.id
                FROM graphic_chapters gc
                JOIN books b ON b.id=gc.book_id
                JOIN author_profiles ap ON ap.id=b.author_id
                WHERE ap.user_id=? AND gc.status!='deleted' AND b.publication_status!='deleted'
            )
            """,
            (
                str(source_filename or "")[:240], str(mime_type or "image/webp"),
                max(0, int(width or 0)), max(0, int(height or 0)),
                max(0, int(file_size or 0)), str(checksum or ""), str(variants_json or "{}"),
                str(storage_backend or "local"), str(storage_key or ""), now,
                int(graphic_page_id), int(author_user_id),
            ),
        )
        if cur.rowcount <= 0:
            await db.rollback()
            return False
        await db.execute(
            """
            UPDATE graphic_chapters SET updated_at=?
            WHERE id=(SELECT graphic_chapter_id FROM graphic_pages WHERE id=?)
            """,
            (now, int(graphic_page_id)),
        )
        await db.commit()
        return True


async def delete_graphic_page_for_author(
    graphic_page_id: int,
    author_user_id: int,
) -> dict[str, Any] | None:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT gp.*, gc.book_id
            FROM graphic_pages gp
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gp.id=? AND ap.user_id=? AND gc.status!='deleted' AND b.publication_status!='deleted'
            """,
            (int(graphic_page_id), int(author_user_id)),
        )
        page = await cur.fetchone()
        if not page:
            return None
        chapter_id = int(page["graphic_chapter_id"])
        cur = await db.execute(
            "SELECT COUNT(*) AS total FROM graphic_pages WHERE graphic_chapter_id=?",
            (chapter_id,),
        )
        count_row = await cur.fetchone()
        if int(count_row["total"] if count_row else 0) <= 1:
            return {"error": "last_page"}
        await db.execute("DELETE FROM graphic_pages WHERE id=?", (int(graphic_page_id),))
        cur = await db.execute(
            "SELECT id FROM graphic_pages WHERE graphic_chapter_id=? ORDER BY page_number, id",
            (chapter_id,),
        )
        remaining = [int(row["id"]) for row in await cur.fetchall()]
        for index, page_id in enumerate(remaining, 1):
            await db.execute(
                "UPDATE graphic_pages SET page_number=?, updated_at=? WHERE id=?",
                (-index, now, page_id),
            )
        for index, page_id in enumerate(remaining, 1):
            await db.execute(
                "UPDATE graphic_pages SET page_number=?, updated_at=? WHERE id=?",
                (index, now, page_id),
            )
        await db.execute(
            "UPDATE graphic_chapters SET pages_count=?, updated_at=? WHERE id=?",
            (len(remaining), now, chapter_id),
        )
        await db.commit()
        return {key: page[key] for key in page.keys()}


async def create_graphic_chapter_record(
    book_id: int,
    title: str,
    *,
    reading_mode: str = "inherit",
    is_free: bool = True,
    price_stars: int = 0,
    source_filename: str = "",
    volume_number: int = 1,
    volume_title: str = "",
    preview_pages: int = 3,
) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS next_number FROM graphic_chapters WHERE book_id=? AND status!='deleted'",
            (int(book_id),),
        )
        row = await cur.fetchone()
        number = int(row["next_number"] if row else 1)
        cur = await db.execute("SELECT publication_status FROM books WHERE id=?", (int(book_id),))
        book = await cur.fetchone()
        target_status = "published" if book and book["publication_status"] == "published" else "draft"
        cur = await db.execute(
            """
            INSERT INTO graphic_chapters(
                book_id, number, title, reading_mode, is_free, price_stars, status,
                source_filename, pages_count, volume_number, volume_title, preview_pages, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
            """,
            (
                int(book_id), number, str(title).strip()[:160], str(reading_mode or "inherit"),
                1 if is_free else 0, max(0, min(100000, int(price_stars or 0))),
                target_status, str(source_filename or "")[:240], max(1, int(volume_number or 1)),
                str(volume_title or "").strip()[:120], max(0, min(50, int(preview_pages or 0))), now, now,
            ),
        )
        chapter_id = int(cur.lastrowid)
        await db.execute(
            """
            INSERT OR IGNORE INTO graphic_volume_settings(
                book_id, volume_number, title, is_free, price_stars, status, created_at, updated_at
            ) VALUES(?, ?, ?, 0, 0, 'active', ?, ?)
            """,
            (int(book_id), max(1, int(volume_number or 1)), str(volume_title or "").strip()[:120], now, now),
        )
        await db.commit()
        return chapter_id


async def add_graphic_pages(graphic_chapter_id: int, pages: list[dict[str, Any]]) -> int:
    now = utc_now()
    async with connect() as db:
        for page in pages:
            await db.execute(
                """
                INSERT INTO graphic_pages(
                    graphic_chapter_id, page_number, file_path, source_filename, mime_type,
                    width, height, file_size, checksum, variants_json, storage_backend, storage_key, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(graphic_chapter_id), int(page["number"]), str(page["file_path"]),
                    str(page.get("source_filename") or "")[:240], str(page.get("mime_type") or "image/webp"),
                    int(page.get("width") or 0), int(page.get("height") or 0),
                    int(page.get("file_size") or 0), str(page.get("checksum") or ""),
                    str(page.get("variants_json") or "{}"), str(page.get("storage_backend") or "local"),
                    str(page.get("storage_key") or page.get("file_path") or ""), now, now,
                ),
            )
        await db.execute(
            "UPDATE graphic_chapters SET pages_count=?, updated_at=? WHERE id=?",
            (len(pages), now, int(graphic_chapter_id)),
        )
        await db.commit()
        return len(pages)


async def update_graphic_chapter_for_author(
    graphic_chapter_id: int,
    author_user_id: int,
    *,
    title: str | None = None,
    reading_mode: str | None = None,
    is_free: bool | None = None,
    price_stars: int | None = None,
    volume_number: int | None = None,
    volume_title: str | None = None,
) -> bool:
    values: dict[str, Any] = {}
    if title is not None:
        clean_title = str(title).strip()[:160]
        if len(clean_title) < 2:
            return False
        values["title"] = clean_title
    if reading_mode is not None:
        values["reading_mode"] = str(reading_mode)
    if is_free is not None:
        values["is_free"] = 1 if is_free else 0
    if price_stars is not None:
        values["price_stars"] = max(0, min(100000, int(price_stars or 0)))
    if volume_number is not None:
        values["volume_number"] = max(1, min(10000, int(volume_number or 1)))
    if volume_title is not None:
        values["volume_title"] = str(volume_title or "").strip()[:120]
    if not values:
        return False
    fields = [f"{key}=?" for key in values]
    fields.append("updated_at=?")
    params = list(values.values()) + [utc_now(), int(graphic_chapter_id), int(author_user_id)]
    async with connect() as db:
        cur = await db.execute(
            f"""
            UPDATE graphic_chapters SET {', '.join(fields)}
            WHERE id=? AND status!='deleted' AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles ap ON ap.id=b.author_id
                WHERE ap.user_id=? AND b.publication_status!='deleted'
            )
            """,
            params,
        )
        if cur.rowcount > 0 and volume_number is not None:
            await db.execute(
                """
                INSERT OR IGNORE INTO graphic_volume_settings(
                    book_id, volume_number, title, is_free, price_stars, status, created_at, updated_at
                )
                SELECT book_id, ?, ?, 0, 0, 'active', ?, ? FROM graphic_chapters WHERE id=?
                """,
                (max(1, min(10000, int(volume_number or 1))), str(volume_title or "").strip()[:120], utc_now(), utc_now(), int(graphic_chapter_id)),
            )
        await db.commit()
        return cur.rowcount > 0


async def delete_graphic_chapter_for_author(graphic_chapter_id: int, author_user_id: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE graphic_chapters SET status='deleted', updated_at=?
            WHERE id=? AND status!='deleted' AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles ap ON ap.id=b.author_id
                WHERE ap.user_id=? AND b.publication_status!='deleted'
            )
            """,
            (now, int(graphic_chapter_id), int(author_user_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def set_graphic_chapter_status(graphic_chapter_id: int, status: str) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "UPDATE graphic_chapters SET status=?, updated_at=? WHERE id=?",
            (str(status), utc_now(), int(graphic_chapter_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_adjacent_graphic_chapters(graphic_chapter_id: int) -> dict[str, aiosqlite.Row | None]:
    chapter = await get_graphic_chapter(graphic_chapter_id)
    if not chapter:
        return {"previous": None, "next": None}
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, book_id, number, title, volume_number, volume_title FROM graphic_chapters
            WHERE book_id=? AND status='published'
              AND (volume_number < ? OR (volume_number=? AND number < ?))
            ORDER BY volume_number DESC, number DESC, id DESC LIMIT 1
            """,
            (int(chapter["book_id"]), int(chapter["volume_number"] or 1), int(chapter["volume_number"] or 1), int(chapter["number"])),
        )
        previous = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT id, book_id, number, title, volume_number, volume_title FROM graphic_chapters
            WHERE book_id=? AND status='published'
              AND (volume_number > ? OR (volume_number=? AND number > ?))
            ORDER BY volume_number, number, id LIMIT 1
            """,
            (int(chapter["book_id"]), int(chapter["volume_number"] or 1), int(chapter["volume_number"] or 1), int(chapter["number"])),
        )
        next_row = await cur.fetchone()
        return {"previous": previous, "next": next_row}


async def save_graphic_reading_progress(user_id: int, graphic_chapter_id: int, page_number: int) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO graphic_reading_progress(user_id, graphic_chapter_id, page_number, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, graphic_chapter_id) DO UPDATE SET
                page_number=excluded.page_number,
                updated_at=excluded.updated_at
            """,
            (int(user_id), int(graphic_chapter_id), max(1, int(page_number or 1)), now),
        )
        await db.commit()


async def get_graphic_reading_progress(user_id: int, graphic_chapter_id: int) -> int:
    async with connect() as db:
        cur = await db.execute(
            "SELECT page_number FROM graphic_reading_progress WHERE user_id=? AND graphic_chapter_id=?",
            (int(user_id), int(graphic_chapter_id)),
        )
        row = await cur.fetchone()
        return max(1, int(row["page_number"] or 1)) if row else 1


async def user_can_access_graphic(user_id: int, graphic_chapter_id: int) -> bool:
    chapter = await get_graphic_chapter(graphic_chapter_id)
    if not chapter:
        return False
    if int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0:
        return True
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1 FROM purchases
            WHERE user_id=? AND status='paid' AND (
                graphic_chapter_id=? OR book_id=?
            ) LIMIT 1
            """,
            (int(user_id), int(graphic_chapter_id), int(chapter["book_id"])),
        )
        return await cur.fetchone() is not None


# v1.9.3 — helpers for author RUB balances and YooKassa payouts
async def upsert_author_financial_profile(
    author_user_id: int,
    *,
    legal_status: str,
    legal_name: str,
    inn: str,
    ogrn: str = "",
    country: str = "RU",
    sbp_phone_encrypted: str = "",
    sbp_bank_id: str = "",
    sbp_bank_name: str = "",
) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(author_user_id),))
        author = await cur.fetchone()
        if not author:
            return False
        await db.execute(
            """
            INSERT INTO author_financial_profiles(
                author_id, legal_status, legal_name, inn, ogrn, country,
                sbp_phone_encrypted, sbp_bank_id, sbp_bank_name,
                verification_status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT(author_id) DO UPDATE SET
                legal_status=excluded.legal_status,
                legal_name=excluded.legal_name,
                inn=excluded.inn,
                ogrn=excluded.ogrn,
                country=excluded.country,
                sbp_phone_encrypted=excluded.sbp_phone_encrypted,
                sbp_bank_id=excluded.sbp_bank_id,
                sbp_bank_name=excluded.sbp_bank_name,
                verification_status='pending',
                updated_at=excluded.updated_at
            """,
            (
                int(author["id"]), str(legal_status)[:32], str(legal_name)[:240], str(inn)[:20],
                str(ogrn)[:20], str(country or "RU")[:8], str(sbp_phone_encrypted),
                str(sbp_bank_id)[:80], str(sbp_bank_name)[:160], now, now,
            ),
        )
        await db.commit()
        return True


async def get_author_financial_profile(author_user_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT fp.* FROM author_financial_profiles fp
            JOIN author_profiles ap ON ap.id=fp.author_id
            WHERE ap.user_id=?
            """,
            (int(author_user_id),),
        )
        return await cur.fetchone()


async def credit_author_rub_ledger(
    author_id: int,
    *,
    source_kind: str,
    source_id: int | None,
    payment_id: str,
    gross_minor: int,
    commission_percent: int = 20,
    hold_days: int = 14,
    note: str = "",
) -> int:
    gross = max(0, int(gross_minor))
    percent = max(0, min(100, int(commission_percent)))
    commission = (gross * percent + 50) // 100
    net = max(0, gross - commission)
    hold = max(0, int(hold_days))
    now = utc_now()
    available_at = (datetime.now(timezone.utc) + timedelta(days=hold)).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO author_rub_ledger(
                author_id, source_kind, source_id, payment_id, gross_minor,
                commission_percent, commission_minor, net_minor, hold_days,
                available_at, status, note, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'held', ?, ?, ?)
            ON CONFLICT(payment_id, source_kind, source_id) DO UPDATE SET updated_at=excluded.updated_at
            RETURNING id
            """,
            (
                int(author_id), str(source_kind)[:32], source_id, str(payment_id)[:160], gross,
                percent, commission, net, hold, available_at, str(note)[:1000], now, now,
            ),
        )
        row = await cur.fetchone()
        await db.commit()
        return int(row["id"])


async def release_matured_rub_ledger() -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "UPDATE author_rub_ledger SET status='available', updated_at=? WHERE status='held' AND available_at<=?",
            (now, now),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def get_author_rub_finance_summary(author_user_id: int) -> dict[str, int]:
    await release_matured_rub_ledger()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN l.status='held' THEN l.net_minor ELSE 0 END), 0) AS held_minor,
                COALESCE(SUM(CASE WHEN l.status='available' THEN l.net_minor ELSE 0 END), 0) AS available_minor,
                COALESCE(SUM(CASE WHEN l.status IN ('payout_requested','processing') THEN l.net_minor ELSE 0 END), 0) AS pending_minor,
                COALESCE(SUM(CASE WHEN l.status='paid' THEN l.net_minor ELSE 0 END), 0) AS paid_minor,
                COALESCE(SUM(l.gross_minor), 0) AS gross_minor,
                COALESCE(SUM(l.commission_minor), 0) AS commission_minor
            FROM author_profiles ap
            LEFT JOIN author_rub_ledger l ON l.author_id=ap.id
            WHERE ap.user_id=?
            """,
            (int(author_user_id),),
        )
        row = await cur.fetchone()
        return {key: int(row[key] or 0) for key in row.keys()} if row else {}


async def create_author_rub_payout_request(
    author_user_id: int,
    *,
    amount_minor: int,
    phone_encrypted: str,
    bank_id: str,
    bank_name: str,
    idempotence_key: str,
) -> int:
    amount = int(amount_minor)
    if amount <= 0:
        raise ValueError("Сумма выплаты должна быть положительной")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(author_user_id),))
        author = await cur.fetchone()
        if not author:
            raise ValueError("Профиль автора не найден")
        cur = await db.execute(
            "SELECT COALESCE(SUM(net_minor),0) AS balance FROM author_rub_ledger WHERE author_id=? AND status='available'",
            (int(author["id"]),),
        )
        balance = await cur.fetchone()
        if int(balance["balance"] or 0) < amount:
            raise ValueError("Недостаточно доступного рублёвого дохода")
        cur = await db.execute(
            """
            INSERT INTO author_rub_payout_requests(
                author_id, amount_minor, phone_encrypted, bank_id, bank_name,
                idempotence_key, status, requested_at, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
            RETURNING id
            """,
            (
                int(author["id"]), amount, str(phone_encrypted), str(bank_id)[:80],
                str(bank_name)[:160], str(idempotence_key)[:80], now, now, now,
            ),
        )
        request = await cur.fetchone()
        payout_id = int(request["id"])
        remaining = amount
        cur = await db.execute(
            "SELECT id, net_minor FROM author_rub_ledger WHERE author_id=? AND status='available' ORDER BY available_at, id",
            (int(author["id"]),),
        )
        rows = await cur.fetchall()
        for row in rows:
            if remaining <= 0:
                break
            value = int(row["net_minor"] or 0)
            if value > remaining:
                raise ValueError("Частичное дробление начисления пока не поддерживается; выберите сумму доступных операций целиком")
            await db.execute(
                "UPDATE author_rub_ledger SET status='payout_requested', payout_request_id=?, updated_at=? WHERE id=?",
                (payout_id, now, int(row["id"])),
            )
            remaining -= value
        if remaining != 0:
            await db.rollback()
            raise ValueError("Не удалось зарезервировать точную сумму выплаты")
        await db.commit()
        return payout_id


async def get_author_rub_payout_request(payout_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pr.*, ap.user_id AS author_user_id, ap.pen_name
            FROM author_rub_payout_requests pr
            JOIN author_profiles ap ON ap.id=pr.author_id
            WHERE pr.id=?
            """,
            (int(payout_id),),
        )
        return await cur.fetchone()


async def update_author_rub_payout_status(
    payout_id: int,
    status: str,
    *,
    provider_payout_id: str = "",
    failure_reason: str = "",
) -> bool:
    allowed = {"new", "processing", "succeeded", "canceled", "failed"}
    if status not in allowed:
        raise ValueError("Неизвестный статус рублёвой выплаты")
    now = utc_now()
    handled_at = now if status != "new" else None
    paid_at = now if status == "succeeded" else None
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE author_rub_payout_requests
            SET status=?, provider_payout_id=CASE WHEN ?!='' THEN ? ELSE provider_payout_id END,
                failure_reason=?, handled_at=COALESCE(?, handled_at), paid_at=COALESCE(?, paid_at), updated_at=?
            WHERE id=?
            """,
            (status, provider_payout_id, provider_payout_id, failure_reason[:1000], handled_at, paid_at, now, int(payout_id)),
        )
        if cur.rowcount <= 0:
            await db.rollback()
            return False
        if status == "succeeded":
            await db.execute("UPDATE author_rub_ledger SET status='paid', updated_at=? WHERE payout_request_id=?", (now, int(payout_id)))
        elif status in {"canceled", "failed"}:
            await db.execute("UPDATE author_rub_ledger SET status='available', payout_request_id=NULL, updated_at=? WHERE payout_request_id=?", (now, int(payout_id)))
        elif status == "processing":
            await db.execute("UPDATE author_rub_ledger SET status='processing', updated_at=? WHERE payout_request_id=?", (now, int(payout_id)))
        await db.commit()
        return True


async def list_author_financial_profiles(status: str = "pending", limit: int = 100) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT fp.*, ap.pen_name, ap.user_id AS author_user_id,
                   u.telegram_id, u.username, u.full_name
            FROM author_financial_profiles fp
            JOIN author_profiles ap ON ap.id=fp.author_id
            JOIN users u ON u.id=ap.user_id
            WHERE fp.verification_status=?
            ORDER BY fp.updated_at, fp.id
            LIMIT ?
            """,
            (str(status), max(1, min(500, int(limit)))),
        )
        return await cur.fetchall()


async def set_author_financial_profile_status(
    profile_id: int,
    status: str,
    *,
    actor_user_id: int | None,
    reason: str = "",
) -> bool:
    allowed = {"pending", "verified", "rejected", "blocked"}
    if status not in allowed:
        raise ValueError("Неизвестный статус платёжного профиля")
    now = utc_now()
    verified_at = now if status == "verified" else None
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE author_financial_profiles
            SET verification_status=?, verified_at=?, verified_by_user_id=?,
                rejection_reason=?, updated_at=?
            WHERE id=?
            """,
            (
                status, verified_at, actor_user_id,
                str(reason or "")[:1000] if status in {"rejected", "blocked"} else "",
                now, int(profile_id),
            ),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_author_rub_payout_requests(status: str = "new", limit: int = 100) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT pr.*, ap.pen_name, ap.user_id AS author_user_id,
                   u.telegram_id, u.username, u.full_name
            FROM author_rub_payout_requests pr
            JOIN author_profiles ap ON ap.id=pr.author_id
            JOIN users u ON u.id=ap.user_id
            WHERE pr.status=?
            ORDER BY pr.requested_at, pr.id
            LIMIT ?
            """,
            (str(status), max(1, min(500, int(limit)))),
        )
        return await cur.fetchall()


async def get_rub_control_summary() -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM author_financial_profiles WHERE verification_status='pending') AS profiles_pending,
              (SELECT COUNT(*) FROM author_rub_payout_requests WHERE status='new') AS payouts_new,
              (SELECT COUNT(*) FROM author_rub_payout_requests WHERE status='processing') AS payouts_processing,
              (SELECT COALESCE(SUM(gross_minor),0) FROM author_rub_ledger) AS gross_minor,
              (SELECT COALESCE(SUM(commission_minor),0) FROM author_rub_ledger) AS commission_minor,
              (SELECT COALESCE(SUM(net_minor),0) FROM author_rub_ledger WHERE status='available') AS available_minor,
              (SELECT COALESCE(SUM(net_minor),0) FROM author_rub_ledger WHERE status='held') AS held_minor
            """
        )
        row = await cur.fetchone()
        return {key: int(row[key] or 0) for key in row.keys()} if row else {}

# =========================
# v1.9.8 — commerce/moderation helpers for graphic content
# =========================

async def get_graphic_volume(book_id: int, volume_number: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT gvs.*, b.title AS book_title, b.publication_status, b.author_id,
                   ap.pen_name, ap.user_id AS author_user_id,
                   (SELECT COUNT(*) FROM graphic_chapters gc
                    WHERE gc.book_id=gvs.book_id AND gc.volume_number=gvs.volume_number
                      AND gc.status!='deleted') AS chapters_count,
                   (SELECT COALESCE(SUM(gc.pages_count), 0) FROM graphic_chapters gc
                    WHERE gc.book_id=gvs.book_id AND gc.volume_number=gvs.volume_number
                      AND gc.status!='deleted') AS pages_count,
                   (SELECT gc.id FROM graphic_chapters gc
                    WHERE gc.book_id=gvs.book_id AND gc.volume_number=gvs.volume_number
                      AND gc.status='published' ORDER BY gc.number, gc.id LIMIT 1) AS first_chapter_id
            FROM graphic_volume_settings gvs
            JOIN books b ON b.id=gvs.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gvs.book_id=? AND gvs.volume_number=? AND gvs.status!='deleted'
            """,
            (int(book_id), max(1, int(volume_number))),
        )
        return await cur.fetchone()


async def list_graphic_volumes_for_book(book_id: int, *, published_only: bool = False) -> list[aiosqlite.Row]:
    status_clause = "AND gc.status='published'" if published_only else "AND gc.status!='deleted'"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT gvs.*, b.title AS book_title,
                   COUNT(gc.id) AS chapters_count,
                   COALESCE(SUM(gc.pages_count), 0) AS pages_count,
                   MIN(gc.id) AS first_chapter_id
            FROM graphic_volume_settings gvs
            JOIN books b ON b.id=gvs.book_id
            LEFT JOIN graphic_chapters gc
              ON gc.book_id=gvs.book_id AND gc.volume_number=gvs.volume_number {status_clause}
            WHERE gvs.book_id=? AND gvs.status='active'
            GROUP BY gvs.id
            HAVING COUNT(gc.id)>0
            ORDER BY gvs.volume_number
            """,
            (int(book_id),),
        )
        return await cur.fetchall()


async def upsert_graphic_volume_for_author(
    book_id: int,
    volume_number: int,
    author_user_id: int,
    *,
    title: str = "",
    price_stars: int = 0,
    is_free: bool | None = None,
) -> bool:
    number = max(1, min(10000, int(volume_number or 1)))
    price = max(0, min(1000000, int(price_stars or 0)))
    free = bool(price <= 0) if is_free is None else bool(is_free)
    if free:
        price = 0
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1 FROM books b JOIN author_profiles ap ON ap.id=b.author_id
            WHERE b.id=? AND ap.user_id=? AND b.publication_status!='deleted'
            """,
            (int(book_id), int(author_user_id)),
        )
        if not await cur.fetchone():
            return False
        await db.execute(
            """
            INSERT INTO graphic_volume_settings(
                book_id, volume_number, title, is_free, price_stars, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(book_id, volume_number) DO UPDATE SET
                title=excluded.title,
                is_free=excluded.is_free,
                price_stars=excluded.price_stars,
                status='active',
                updated_at=excluded.updated_at
            """,
            (int(book_id), number, str(title or "").strip()[:120], 1 if free else 0, price, now, now),
        )
        await db.execute(
            "UPDATE graphic_chapters SET volume_title=?, updated_at=? WHERE book_id=? AND volume_number=? AND status!='deleted'",
            (str(title or "").strip()[:120], now, int(book_id), number),
        )
        await db.commit()
        return True


async def set_graphic_chapter_preview_for_author(
    graphic_chapter_id: int,
    author_user_id: int,
    preview_pages: int,
) -> bool:
    value = max(0, min(20, int(preview_pages or 0)))
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE graphic_chapters SET preview_pages=?, updated_at=?
            WHERE id=? AND status!='deleted' AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles ap ON ap.id=b.author_id
                WHERE ap.user_id=? AND b.publication_status!='deleted'
            )
            """,
            (value, utc_now(), int(graphic_chapter_id), int(author_user_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_graphic_page_reports(status: str = "new", limit: int = 100) -> list[aiosqlite.Row]:
    allowed = status if status in {"new", "pending", "closed", "rejected"} else "new"
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT r.*, gp.page_number, gp.graphic_chapter_id, gc.title AS chapter_title,
                   gc.number AS chapter_number, gc.volume_number, b.id AS book_id,
                   b.title AS book_title, u.telegram_id, u.username, u.full_name
            FROM graphic_page_reports r
            JOIN graphic_pages gp ON gp.id=r.graphic_page_id
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            JOIN users u ON u.id=r.user_id
            WHERE r.status=?
            ORDER BY r.id DESC LIMIT ?
            """,
            (allowed, max(1, min(500, int(limit)))),
        )
        return await cur.fetchall()


async def create_graphic_page_report(user_id: int, graphic_page_id: int, reason: str) -> int:
    clean = str(reason or "").strip()[:1500]
    if len(clean) < 5:
        raise ValueError("Опишите проблему подробнее.")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM graphic_page_reports WHERE user_id=? AND graphic_page_id=? AND status IN ('new','pending')",
            (int(user_id), int(graphic_page_id)),
        )
        row = await cur.fetchone()
        if row:
            return int(row["id"])
        cur = await db.execute(
            """
            INSERT INTO graphic_page_reports(user_id, graphic_page_id, reason, status, created_at, updated_at)
            VALUES(?, ?, ?, 'new', ?, ?)
            """,
            (int(user_id), int(graphic_page_id), clean, now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def moderate_graphic_page(
    graphic_page_id: int,
    moderator_user_id: int,
    *,
    decision: str,
    note: str = "",
) -> bool:
    status = {"approve": "approved", "reject": "rejected", "pending": "pending"}.get(decision)
    if not status:
        return False
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE graphic_pages
            SET moderation_status=?, moderation_note=?, moderated_at=?, moderated_by_user_id=?, updated_at=?
            WHERE id=?
            """,
            (status, str(note or "").strip()[:1000], now, int(moderator_user_id), now, int(graphic_page_id)),
        )
        if cur.rowcount <= 0:
            await db.rollback()
            return False
        await db.execute(
            """
            UPDATE graphic_page_reports
            SET status='closed', resolution_note=?, resolved_by_user_id=?, resolved_at=?, updated_at=?
            WHERE graphic_page_id=? AND status IN ('new','pending')
            """,
            (str(note or "").strip()[:1000], int(moderator_user_id), now, now, int(graphic_page_id)),
        )
        await db.commit()
        return True


async def set_graphic_page_report_status(
    report_id: int,
    moderator_user_id: int,
    status: str,
    note: str = "",
) -> bool:
    clean_status = status if status in {"pending", "closed", "rejected"} else "pending"
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE graphic_page_reports
            SET status=?, resolution_note=?, resolved_by_user_id=?,
                resolved_at=CASE WHEN ? IN ('closed','rejected') THEN ? ELSE NULL END,
                updated_at=?
            WHERE id=?
            """,
            (clean_status, str(note or "").strip()[:1000], int(moderator_user_id), clean_status, now, now, int(report_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def has_purchase_access(
    user_id: int,
    *,
    book_id: int | None = None,
    chapter_id: int | None = None,
    audio_chapter_id: int | None = None,
    graphic_chapter_id: int | None = None,
) -> bool:
    """Проверяет точный вид цифрового доступа, не превращая покупку тома в покупку всей книги."""
    async with connect() as db:
        if chapter_id is not None:
            cur = await db.execute(
                """
                SELECT 1 FROM purchases p
                WHERE p.user_id=? AND p.status='paid' AND (
                    p.chapter_id=? OR (
                        p.book_id=(SELECT book_id FROM chapters WHERE id=?)
                        AND COALESCE(p.purchase_kind, 'content')='content'
                        AND p.graphic_volume_number IS NULL
                    )
                ) LIMIT 1
                """,
                (int(user_id), int(chapter_id), int(chapter_id)),
            )
            return await cur.fetchone() is not None
        if audio_chapter_id is not None:
            cur = await db.execute(
                """
                SELECT 1 FROM purchases p
                WHERE p.user_id=? AND p.status='paid' AND (
                    p.audio_chapter_id=? OR (
                        p.book_id=(SELECT book_id FROM audio_chapters WHERE id=?)
                        AND COALESCE(p.purchase_kind, 'content')='content'
                        AND p.graphic_volume_number IS NULL
                    )
                ) LIMIT 1
                """,
                (int(user_id), int(audio_chapter_id), int(audio_chapter_id)),
            )
            return await cur.fetchone() is not None
        if graphic_chapter_id is not None:
            cur = await db.execute(
                """
                SELECT 1 FROM purchases p
                WHERE p.user_id=? AND p.status='paid' AND (
                    p.graphic_chapter_id=? OR (
                        p.book_id=(SELECT book_id FROM graphic_chapters WHERE id=?)
                        AND COALESCE(p.purchase_kind, 'content')='content'
                        AND p.graphic_volume_number IS NULL
                    ) OR (
                        p.book_id=(SELECT book_id FROM graphic_chapters WHERE id=?)
                        AND p.purchase_kind='graphic_volume'
                        AND p.graphic_volume_number=(SELECT volume_number FROM graphic_chapters WHERE id=?)
                    )
                ) LIMIT 1
                """,
                (int(user_id), int(graphic_chapter_id), int(graphic_chapter_id),
                 int(graphic_chapter_id), int(graphic_chapter_id)),
            )
            return await cur.fetchone() is not None
        if book_id is not None:
            cur = await db.execute(
                """
                SELECT 1 FROM purchases
                WHERE user_id=? AND book_id=? AND status='paid'
                  AND COALESCE(purchase_kind, 'content')='content'
                  AND graphic_volume_number IS NULL
                LIMIT 1
                """,
                (int(user_id), int(book_id)),
            )
            return await cur.fetchone() is not None
        return False


async def user_can_access_graphic(user_id: int, graphic_chapter_id: int) -> bool:
    chapter = await get_graphic_chapter(graphic_chapter_id)
    if not chapter:
        return False
    if str(chapter["moderation_status"] or "approved") != "approved":
        return False
    if int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0:
        return True
    return await has_purchase_access(int(user_id), graphic_chapter_id=int(graphic_chapter_id))


_old_get_purchase_target_v197 = get_purchase_target


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    parts = str(payload or "").split(":")
    if len(parts) >= 3 and parts[0] == "vox" and parts[1] == "graphic":
        try:
            chapter_id = int(parts[2])
        except ValueError:
            return None
        chapter = await get_graphic_chapter(chapter_id)
        if not chapter or str(chapter["status"] or "") != "published" or str(chapter["publication_status"] or "") != "published":
            return None
        return {
            "kind": "graphic",
            "target_id": chapter_id,
            "book_id": int(chapter["book_id"]),
            "title": str(chapter["title"]),
            "book_title": str(chapter["book_title"]),
            "amount_stars": int(chapter["price_stars"] or 0),
            "author_id": int(chapter["author_id"]) if chapter["author_id"] is not None else None,
            "promo_code": None,
            "discount_percent": 0,
            "original_amount_stars": int(chapter["price_stars"] or 0),
        }
    if len(parts) >= 4 and parts[0] == "vox" and parts[1] == "graphic_volume":
        try:
            book_id = int(parts[2]); volume_number = int(parts[3])
        except ValueError:
            return None
        volume = await get_graphic_volume(book_id, volume_number)
        if not volume or str(volume["publication_status"] or "") != "published":
            return None
        return {
            "kind": "graphic_volume",
            "target_id": int(volume_number),
            "volume_number": int(volume_number),
            "first_chapter_id": int(volume["first_chapter_id"]) if volume["first_chapter_id"] else 0,
            "book_id": int(book_id),
            "title": f"Том {int(volume_number)}: {str(volume['title'] or volume['book_title'])}",
            "book_title": str(volume["book_title"]),
            "amount_stars": int(volume["price_stars"] or 0),
            "author_id": int(volume["author_id"]) if volume["author_id"] is not None else None,
            "promo_code": None,
            "discount_percent": 0,
            "original_amount_stars": int(volume["price_stars"] or 0),
        }
    return await _old_get_purchase_target_v197(payload)


async def list_user_purchases(user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, b.title AS book_title,
                   c.title AS chapter_title,
                   ac.title AS audio_title,
                   gc.title AS graphic_chapter_title,
                   gc.volume_number AS graphic_chapter_volume,
                   COALESCE(gvs.title, '') AS graphic_volume_title
            FROM purchases p
            LEFT JOIN books b ON b.id=p.book_id
            LEFT JOIN chapters c ON c.id=p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
            LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
            LEFT JOIN graphic_volume_settings gvs
              ON gvs.book_id=p.book_id AND gvs.volume_number=p.graphic_volume_number
            WHERE p.user_id=?
            ORDER BY p.id DESC LIMIT ?
            """,
            (int(user_id), max(1, int(limit))),
        )
        return await cur.fetchall()


async def get_purchase(purchase_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, u.telegram_id, u.username, u.full_name,
                   b.title AS book_title,
                   c.title AS chapter_title,
                   ac.title AS audio_title,
                   gc.title AS graphic_chapter_title,
                   gc.volume_number AS graphic_chapter_volume,
                   COALESCE(gvs.title, '') AS graphic_volume_title
            FROM purchases p
            JOIN users u ON u.id=p.user_id
            LEFT JOIN books b ON b.id=p.book_id
            LEFT JOIN chapters c ON c.id=p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
            LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
            LEFT JOIN graphic_volume_settings gvs
              ON gvs.book_id=p.book_id AND gvs.volume_number=p.graphic_volume_number
            WHERE p.id=?
            """,
            (int(purchase_id),),
        )
        return await cur.fetchone()

async def has_graphic_volume_purchase(user_id: int, book_id: int, volume_number: int) -> bool:
    """Проверяет покупку всего произведения или конкретного графического тома.

    Отдельно купленная глава не считается покупкой всего тома.
    """
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1 FROM purchases
            WHERE user_id=? AND book_id=? AND status='paid' AND (
                (COALESCE(purchase_kind, 'content')='content' AND graphic_volume_number IS NULL)
                OR (purchase_kind='graphic_volume' AND graphic_volume_number=?)
            ) LIMIT 1
            """,
            (int(user_id), int(book_id), max(1, int(volume_number))),
        )
        return await cur.fetchone() is not None


# v1.9.9 — гибкие пакеты глав
async def list_chapter_packages_for_book(
    book_id: int,
    *,
    include_inactive: bool = False,
) -> list[aiosqlite.Row]:
    where = "cp.book_id=?" if include_inactive else "cp.book_id=? AND cp.is_active=1"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT cp.*, b.title AS book_title, b.content_type, b.publication_status,
                   ap.id AS author_id, ap.user_id AS author_user_id,
                   CASE WHEN cp.chapters_count>0
                        THEN CAST(cp.price_stars AS REAL)/cp.chapters_count ELSE 0 END AS stars_per_chapter
            FROM chapter_packages cp
            JOIN books b ON b.id=cp.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE {where}
            ORDER BY cp.sort_order, cp.chapters_count, cp.price_stars, cp.id
            """,
            (int(book_id),),
        )
        return await cur.fetchall()


async def get_chapter_package(package_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT cp.*, b.title AS book_title, b.content_type, b.publication_status,
                   ap.id AS author_id, ap.user_id AS author_user_id,
                   CASE WHEN cp.chapters_count>0
                        THEN CAST(cp.price_stars AS REAL)/cp.chapters_count ELSE 0 END AS stars_per_chapter
            FROM chapter_packages cp
            JOIN books b ON b.id=cp.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE cp.id=?
            """,
            (int(package_id),),
        )
        return await cur.fetchone()


async def create_chapter_package_for_author(
    book_id: int,
    author_user_id: int,
    *,
    title: str,
    chapters_count: int,
    price_stars: int,
    content_scope: str,
) -> int:
    count = max(1, min(10000, int(chapters_count)))
    price = max(1, min(1000000, int(price_stars)))
    scope = content_scope if content_scope in {"text", "graphic", "all"} else "text"
    clean_title = str(title or "").strip()[:120] or f"Пакет на {count} глав"
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.id FROM books b JOIN author_profiles ap ON ap.id=b.author_id
            WHERE b.id=? AND ap.user_id=? AND b.publication_status!='deleted'
            """,
            (int(book_id), int(author_user_id)),
        )
        if not await cur.fetchone():
            raise ValueError("Произведение не найдено")
        cur = await db.execute(
            "SELECT COALESCE(MAX(sort_order), 0)+10 AS next_order FROM chapter_packages WHERE book_id=?",
            (int(book_id),),
        )
        order_row = await cur.fetchone()
        cur = await db.execute(
            """
            INSERT INTO chapter_packages(book_id, title, chapters_count, price_stars, content_scope,
                                         is_active, sort_order, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (int(book_id), clean_title, count, price, scope, int(order_row["next_order"] or 10), now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def update_chapter_package_for_author(
    package_id: int,
    author_user_id: int,
    *,
    title: str,
    chapters_count: int,
    price_stars: int,
    content_scope: str,
    is_active: bool,
) -> bool:
    count = max(1, min(10000, int(chapters_count)))
    price = max(1, min(1000000, int(price_stars)))
    scope = content_scope if content_scope in {"text", "graphic", "all"} else "text"
    clean_title = str(title or "").strip()[:120] or f"Пакет на {count} глав"
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE chapter_packages
            SET title=?, chapters_count=?, price_stars=?, content_scope=?, is_active=?, updated_at=?
            WHERE id=? AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles ap ON ap.id=b.author_id
                WHERE ap.user_id=? AND b.publication_status!='deleted'
            )
            """,
            (clean_title, count, price, scope, 1 if is_active else 0, utc_now(), int(package_id), int(author_user_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def deactivate_chapter_package_for_author(package_id: int, author_user_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE chapter_packages SET is_active=0, updated_at=?
            WHERE id=? AND book_id IN (
                SELECT b.id FROM books b JOIN author_profiles ap ON ap.id=b.author_id
                WHERE ap.user_id=? AND b.publication_status!='deleted'
            )
            """,
            (utc_now(), int(package_id), int(author_user_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_user_chapter_package_balances(
    user_id: int,
    *,
    book_id: int | None = None,
) -> list[aiosqlite.Row]:
    filters = ["cpb.user_id=?", "cpb.status='active'", "p.status='paid'"]
    params: list[Any] = [int(user_id)]
    if book_id is not None:
        filters.append("cpb.book_id=?")
        params.append(int(book_id))
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT cpb.*, cp.title AS package_title, cp.chapters_count, cp.price_stars,
                   b.title AS book_title,
                   (cpb.total_credits-cpb.remaining_credits) AS used_credits
            FROM chapter_package_balances cpb
            JOIN purchases p ON p.id=cpb.purchase_id
            JOIN chapter_packages cp ON cp.id=cpb.package_id
            JOIN books b ON b.id=cpb.book_id
            WHERE {' AND '.join(filters)}
            ORDER BY cpb.created_at, cpb.id
            """,
            tuple(params),
        )
        return await cur.fetchall()


async def get_user_chapter_credit_summary(user_id: int, book_id: int, content_scope: str) -> dict[str, int]:
    scopes = (content_scope, "all") if content_scope in {"text", "graphic"} else ("text", "graphic", "all")
    placeholders = ",".join("?" for _ in scopes)
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT COALESCE(SUM(cpb.remaining_credits), 0) AS remaining,
                   COALESCE(SUM(cpb.total_credits), 0) AS total,
                   COUNT(*) AS packages
            FROM chapter_package_balances cpb
            JOIN purchases p ON p.id=cpb.purchase_id
            WHERE cpb.user_id=? AND cpb.book_id=? AND cpb.status='active'
              AND cpb.remaining_credits>0 AND p.status='paid'
              AND cpb.content_scope IN ({placeholders})
            """,
            (int(user_id), int(book_id), *scopes),
        )
        row = await cur.fetchone()
        return {
            "remaining": int(row["remaining"] or 0),
            "total": int(row["total"] or 0),
            "packages": int(row["packages"] or 0),
        }


async def redeem_chapter_package_credit(
    user_id: int,
    *,
    chapter_id: int | None = None,
    graphic_chapter_id: int | None = None,
) -> dict[str, Any]:
    if (chapter_id is None) == (graphic_chapter_id is None):
        raise ValueError("Нужно указать одну главу")
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        if chapter_id is not None:
            cur = await db.execute(
                """
                SELECT c.id, c.book_id, c.title, c.is_free, c.price_stars, c.status,
                       b.publication_status
                FROM chapters c JOIN books b ON b.id=c.book_id WHERE c.id=?
                """,
                (int(chapter_id),),
            )
            target = await cur.fetchone()
            scope = "text"
            target_column = "chapter_id"
            target_value = int(chapter_id)
        else:
            cur = await db.execute(
                """
                SELECT gc.id, gc.book_id, gc.title, gc.is_free, gc.price_stars, gc.status,
                       gc.moderation_status, b.publication_status
                FROM graphic_chapters gc JOIN books b ON b.id=gc.book_id WHERE gc.id=?
                """,
                (int(graphic_chapter_id),),
            )
            target = await cur.fetchone()
            scope = "graphic"
            target_column = "graphic_chapter_id"
            target_value = int(graphic_chapter_id)
        if not target or str(target["status"] or "") != "published" or str(target["publication_status"] or "") != "published":
            await db.rollback()
            raise ValueError("Глава недоступна")
        if scope == "graphic" and str(target["moderation_status"] or "approved") != "approved":
            await db.rollback()
            raise ValueError("Страница главы ещё проходит проверку")
        if int(target["is_free"] or 0) == 1 or int(target["price_stars"] or 0) <= 0:
            await db.commit()
            return {"ok": True, "already_available": True, "remaining": None, "book_id": int(target["book_id"])}

        # Полная книга, отдельная глава или прежнее списание пакета уже дают доступ.
        if scope == "text":
            cur = await db.execute(
                """
                SELECT 1 FROM purchases p WHERE p.user_id=? AND p.status='paid' AND (
                    p.chapter_id=? OR (
                        p.book_id=? AND COALESCE(p.purchase_kind,'content')='content'
                        AND p.chapter_id IS NULL AND p.audio_chapter_id IS NULL
                        AND p.graphic_chapter_id IS NULL AND p.graphic_volume_number IS NULL
                    )
                ) LIMIT 1
                """,
                (int(user_id), target_value, int(target["book_id"])),
            )
        else:
            cur = await db.execute(
                """
                SELECT 1 FROM purchases p WHERE p.user_id=? AND p.status='paid' AND (
                    p.graphic_chapter_id=? OR (
                        p.book_id=? AND COALESCE(p.purchase_kind,'content')='content'
                        AND p.graphic_volume_number IS NULL
                    ) OR (
                        p.book_id=? AND p.purchase_kind='graphic_volume'
                        AND p.graphic_volume_number=(SELECT volume_number FROM graphic_chapters WHERE id=?)
                    )
                ) LIMIT 1
                """,
                (int(user_id), target_value, int(target["book_id"]), int(target["book_id"]), target_value),
            )
        if await cur.fetchone():
            await db.commit()
            return {"ok": True, "already_available": True, "remaining": None, "book_id": int(target["book_id"])}
        cur = await db.execute(
            f"SELECT 1 FROM chapter_package_unlocks WHERE user_id=? AND {target_column}=? LIMIT 1",
            (int(user_id), target_value),
        )
        if await cur.fetchone():
            await db.commit()
            return {"ok": True, "already_available": True, "remaining": None, "book_id": int(target["book_id"])}

        cur = await db.execute(
            """
            SELECT cpb.* FROM chapter_package_balances cpb
            JOIN purchases p ON p.id=cpb.purchase_id
            WHERE cpb.user_id=? AND cpb.book_id=? AND cpb.status='active'
              AND cpb.remaining_credits>0 AND p.status='paid'
              AND cpb.content_scope IN (?, 'all')
            ORDER BY cpb.created_at, cpb.id LIMIT 1
            """,
            (int(user_id), int(target["book_id"]), scope),
        )
        balance = await cur.fetchone()
        if not balance:
            await db.rollback()
            raise ValueError("В пакетах этой книги не осталось доступных глав")
        cur = await db.execute(
            """
            UPDATE chapter_package_balances
            SET remaining_credits=remaining_credits-1, updated_at=?
            WHERE id=? AND remaining_credits>0 AND status='active'
            """,
            (now, int(balance["id"])),
        )
        if cur.rowcount != 1:
            await db.rollback()
            raise ValueError("Баланс пакета изменился. Повторите открытие")
        await db.execute(
            f"""
            INSERT INTO chapter_package_unlocks(balance_id, purchase_id, user_id, book_id,
                                                {target_column}, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (int(balance["id"]), int(balance["purchase_id"]), int(user_id), int(target["book_id"]), target_value, now),
        )
        cur = await db.execute(
            "SELECT remaining_credits FROM chapter_package_balances WHERE id=?",
            (int(balance["id"]),),
        )
        row = await cur.fetchone()
        await db.commit()
        return {
            "ok": True,
            "already_available": False,
            "remaining": int(row["remaining_credits"] or 0),
            "book_id": int(target["book_id"]),
            "chapter_id": target_value if scope == "text" else None,
            "graphic_chapter_id": target_value if scope == "graphic" else None,
        }


_old_has_purchase_access_v198 = has_purchase_access


async def has_purchase_access(
    user_id: int,
    *,
    book_id: int | None = None,
    chapter_id: int | None = None,
    audio_chapter_id: int | None = None,
    graphic_chapter_id: int | None = None,
) -> bool:
    if await _old_has_purchase_access_v198(
        user_id,
        book_id=book_id,
        chapter_id=chapter_id,
        audio_chapter_id=audio_chapter_id,
        graphic_chapter_id=graphic_chapter_id,
    ):
        return True
    async with connect() as db:
        if chapter_id is not None:
            cur = await db.execute(
                """
                SELECT 1 FROM chapter_package_unlocks cpu
                JOIN chapter_package_balances cpb ON cpb.id=cpu.balance_id
                JOIN purchases p ON p.id=cpu.purchase_id
                WHERE cpu.user_id=? AND cpu.chapter_id=?
                  AND cpb.status='active' AND p.status='paid' LIMIT 1
                """,
                (int(user_id), int(chapter_id)),
            )
            return await cur.fetchone() is not None
        if graphic_chapter_id is not None:
            cur = await db.execute(
                """
                SELECT 1 FROM chapter_package_unlocks cpu
                JOIN chapter_package_balances cpb ON cpb.id=cpu.balance_id
                JOIN purchases p ON p.id=cpu.purchase_id
                WHERE cpu.user_id=? AND cpu.graphic_chapter_id=?
                  AND cpb.status='active' AND p.status='paid' LIMIT 1
                """,
                (int(user_id), int(graphic_chapter_id)),
            )
            return await cur.fetchone() is not None
    return False


_old_get_purchase_target_v198_packages = get_purchase_target


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    parts = str(payload or "").split(":")
    if len(parts) == 3 and parts[0] == "vox" and parts[1] == "chapter_package":
        try:
            package_id = int(parts[2])
        except ValueError:
            return None
        package = await get_chapter_package(package_id)
        if not package or int(package["is_active"] or 0) != 1 or str(package["publication_status"] or "") != "published":
            return None
        return {
            "kind": "chapter_package",
            "target_id": package_id,
            "package_id": package_id,
            "book_id": int(package["book_id"]),
            "title": str(package["title"]),
            "book_title": str(package["book_title"]),
            "amount_stars": int(package["price_stars"] or 0),
            "author_id": int(package["author_id"]) if package["author_id"] is not None else None,
            "chapters_count": int(package["chapters_count"] or 0),
            "content_scope": str(package["content_scope"] or "text"),
            "promo_code": None,
            "discount_percent": 0,
            "original_amount_stars": int(package["price_stars"] or 0),
        }
    return await _old_get_purchase_target_v198_packages(payload)


_old_create_paid_purchase_v198_packages = create_paid_purchase


async def create_paid_purchase(
    *,
    user_id: int,
    payload: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
) -> int:
    target = await get_purchase_target(payload)
    if not target or target.get("kind") != "chapter_package":
        return await _old_create_paid_purchase_v198_packages(
            user_id=user_id,
            payload=payload,
            amount_stars=amount_stars,
            telegram_payment_charge_id=telegram_payment_charge_id,
        )
    amount_stars = int(amount_stars)
    if amount_stars != int(target["amount_stars"] or 0):
        raise ValueError("Сумма платежа не совпадает с ценой")
    charge_id = str(telegram_payment_charge_id or "").strip()
    if not charge_id:
        raise ValueError("Не указан идентификатор платежа")
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT id, user_id, amount_stars, payload FROM purchases WHERE telegram_payment_charge_id=? ORDER BY id LIMIT 1",
            (charge_id,),
        )
        existing = await cur.fetchone()
        if existing:
            if int(existing["user_id"]) != int(user_id) or int(existing["amount_stars"]) != amount_stars or str(existing["payload"] or "") != payload:
                await db.rollback()
                raise ValueError("Идентификатор платежа уже использован")
            await db.commit()
            return int(existing["id"])
        cur = await db.execute(
            """
            INSERT INTO purchases(user_id, book_id, chapter_package_id, amount_stars, status,
                                  telegram_payment_charge_id, created_at, payload, purchase_kind)
            VALUES(?, ?, ?, ?, 'paid', ?, ?, ?, 'chapter_package')
            """,
            (int(user_id), int(target["book_id"]), int(target["package_id"]), amount_stars, charge_id, now, payload),
        )
        purchase_id = int(cur.lastrowid)
        await db.execute(
            """
            INSERT INTO chapter_package_balances(purchase_id, user_id, package_id, book_id, content_scope,
                                                 total_credits, remaining_credits, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (purchase_id, int(user_id), int(target["package_id"]), int(target["book_id"]),
             str(target["content_scope"]), int(target["chapters_count"]), int(target["chapters_count"]), now, now),
        )
        author_id = target.get("author_id")
        if author_id is not None and amount_stars > 0:
            cur_setting = await db.execute("SELECT value FROM settings WHERE key='commission_books'")
            row_setting = await cur_setting.fetchone()
            commission_percent = max(0, min(100, int(row_setting["value"] if row_setting else 20)))
            cur_hold = await db.execute("SELECT value FROM settings WHERE key='hold_days_default'")
            row_hold = await cur_hold.fetchone()
            hold_days = max(0, int(row_hold["value"] if row_hold else 14))
            commission_stars = int(round(amount_stars * commission_percent / 100))
            net_stars = max(0, amount_stars - commission_stars)
            cur_rate = await db.execute("SELECT value FROM settings WHERE key='payments_stars_author_rate_minor'")
            row_rate = await cur_rate.fetchone()
            try:
                settlement_rate_minor = max(1, int(row_rate["value"] if row_rate else 100))
            except (TypeError, ValueError):
                settlement_rate_minor = 100
            net_minor = net_stars * settlement_rate_minor
            available_at = (datetime.now(timezone.utc) + timedelta(days=hold_days)).isoformat()
            await db.execute(
                """
                INSERT INTO author_ledger(author_id, purchase_id, source_type, source_id, gross_stars,
                                          commission_percent, commission_stars, net_stars,
                                          settlement_rate_minor, net_minor, hold_days,
                                          available_at, status, created_at, updated_at)
                VALUES(?, ?, 'chapter_package', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'held', ?, ?)
                """,
                (int(author_id), purchase_id, int(target["package_id"]), amount_stars, commission_percent,
                 commission_stars, net_stars, settlement_rate_minor, net_minor, hold_days, available_at, now, now),
            )
        await db.commit()
        return purchase_id


_old_list_user_purchases_v198_packages = list_user_purchases


async def list_user_purchases(user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, b.title AS book_title,
                   c.title AS chapter_title,
                   ac.title AS audio_title,
                   gc.title AS graphic_chapter_title,
                   gc.volume_number AS graphic_chapter_volume,
                   COALESCE(gvs.title, '') AS graphic_volume_title,
                   cp.title AS chapter_package_title,
                   cp.chapters_count AS chapter_package_count,
                   cpb.total_credits AS chapter_package_total,
                   cpb.remaining_credits AS chapter_package_remaining
            FROM purchases p
            LEFT JOIN books b ON b.id=p.book_id
            LEFT JOIN chapters c ON c.id=p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
            LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
            LEFT JOIN graphic_volume_settings gvs
              ON gvs.book_id=p.book_id AND gvs.volume_number=p.graphic_volume_number
            LEFT JOIN chapter_packages cp ON cp.id=p.chapter_package_id
            LEFT JOIN chapter_package_balances cpb ON cpb.purchase_id=p.id
            WHERE p.user_id=?
            ORDER BY p.id DESC LIMIT ?
            """,
            (int(user_id), max(1, int(limit))),
        )
        return await cur.fetchall()


_old_get_purchase_v198_packages = get_purchase


async def get_purchase(purchase_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT p.*, u.telegram_id, u.username, u.full_name,
                   b.title AS book_title,
                   c.title AS chapter_title,
                   ac.title AS audio_title,
                   gc.title AS graphic_chapter_title,
                   gc.volume_number AS graphic_chapter_volume,
                   COALESCE(gvs.title, '') AS graphic_volume_title,
                   cp.title AS chapter_package_title,
                   cp.chapters_count AS chapter_package_count,
                   cpb.total_credits AS chapter_package_total,
                   cpb.remaining_credits AS chapter_package_remaining
            FROM purchases p
            JOIN users u ON u.id=p.user_id
            LEFT JOIN books b ON b.id=p.book_id
            LEFT JOIN chapters c ON c.id=p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
            LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
            LEFT JOIN graphic_volume_settings gvs
              ON gvs.book_id=p.book_id AND gvs.volume_number=p.graphic_volume_number
            LEFT JOIN chapter_packages cp ON cp.id=p.chapter_package_id
            LEFT JOIN chapter_package_balances cpb ON cpb.purchase_id=p.id
            WHERE p.id=?
            """,
            (int(purchase_id),),
        )
        return await cur.fetchone()


_old_create_refund_request_v198_packages = create_refund_request


async def create_refund_request(purchase_id: int, user_id: int, reason: str) -> int:
    purchase = await get_purchase(int(purchase_id))
    if not purchase or str(purchase["purchase_kind"] or "") != "chapter_package":
        return await _old_create_refund_request_v198_packages(purchase_id, user_id, reason)
    reason = str(reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Опишите причину подробнее")
    if int(purchase["user_id"]) != int(user_id) or str(purchase["status"]) != "paid":
        raise ValueError("Покупка не найдена")
    total = int(purchase["chapter_package_total"] or purchase["chapter_package_count"] or 0)
    remaining = int(purchase["chapter_package_remaining"] or 0)
    if remaining < total:
        raise ValueError("После использования хотя бы одной главы пакет нельзя вернуть автоматически")
    now_dt = datetime.now(timezone.utc)
    created_at = datetime.fromisoformat(str(purchase["created_at"]).replace("Z", "+00:00"))
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    async with connect() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key='refund_window_days'")
        row = await cur.fetchone()
        window_days = max(0, int(row["value"] if row else 14))
        if window_days and now_dt > created_at + timedelta(days=window_days):
            raise ValueError("Срок подачи запроса на возврат истёк")
        cur = await db.execute(
            "SELECT id FROM refund_requests WHERE purchase_id=? AND status IN ('new','pending','refunded') LIMIT 1",
            (int(purchase_id),),
        )
        if await cur.fetchone():
            raise ValueError("Запрос по этой покупке уже создан")
        now = now_dt.isoformat()
        cur = await db.execute(
            "INSERT INTO refund_requests(purchase_id, user_id, reason, status, created_at, updated_at) VALUES(?, ?, ?, 'new', ?, ?)",
            (int(purchase_id), int(user_id), reason[:1000], now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


_old_finalize_refund_v198_packages = finalize_refund


async def finalize_refund(refund_id: int, handled_by_user_id: int | None, note: str = "Возврат Stars выполнен") -> bool:
    async with connect() as db:
        cur = await db.execute(
            "SELECT rr.purchase_id, p.purchase_kind FROM refund_requests rr JOIN purchases p ON p.id=rr.purchase_id WHERE rr.id=?",
            (int(refund_id),),
        )
        row = await cur.fetchone()
    ok = await _old_finalize_refund_v198_packages(refund_id, handled_by_user_id, note)
    if ok and row and str(row["purchase_kind"] or "") == "chapter_package":
        async with connect() as db:
            await db.execute(
                "UPDATE chapter_package_balances SET status='refunded', remaining_credits=0, updated_at=? WHERE purchase_id=?",
                (utc_now(), int(row["purchase_id"])),
            )
            await db.commit()
    return ok


# v1.10.0 — завершение графического модуля и безопасная отмена покупок
async def _ensure_v1100_schema(db: aiosqlite.Connection) -> None:
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS payment_intents (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            canonical_payload TEXT NOT NULL,
            amount_stars INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            expires_at TEXT NOT NULL,
            invoice_message_id INTEGER,
            paid_charge_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_payment_intents_user_status
            ON payment_intents(user_id, status, expires_at);

        CREATE TABLE IF NOT EXISTS purchase_usage_events (
            purchase_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            usage_kind TEXT NOT NULL,
            source_id INTEGER,
            first_used_at TEXT NOT NULL,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_purchase_usage_user
            ON purchase_usage_events(user_id, first_used_at);

        CREATE TABLE IF NOT EXISTS graphic_page_texts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graphic_page_id INTEGER NOT NULL,
            language_code TEXT NOT NULL DEFAULT 'ru',
            text_kind TEXT NOT NULL DEFAULT 'ocr',
            text TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'published',
            updated_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(graphic_page_id, language_code, text_kind),
            FOREIGN KEY(graphic_page_id) REFERENCES graphic_pages(id) ON DELETE CASCADE,
            FOREIGN KEY(updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS graphic_translation_regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graphic_page_id INTEGER NOT NULL,
            language_code TEXT NOT NULL DEFAULT 'ru',
            x REAL NOT NULL,
            y REAL NOT NULL,
            width REAL NOT NULL,
            height REAL NOT NULL,
            text TEXT NOT NULL,
            style TEXT NOT NULL DEFAULT 'bubble',
            sort_order INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'published',
            updated_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(graphic_page_id) REFERENCES graphic_pages(id) ON DELETE CASCADE,
            FOREIGN KEY(updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_graphic_translation_page_lang
            ON graphic_translation_regions(graphic_page_id, language_code, status, sort_order);

        CREATE TABLE IF NOT EXISTS graphic_page_frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graphic_page_id INTEGER NOT NULL,
            x REAL NOT NULL,
            y REAL NOT NULL,
            width REAL NOT NULL,
            height REAL NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'manual',
            status TEXT NOT NULL DEFAULT 'published',
            updated_by_user_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(graphic_page_id) REFERENCES graphic_pages(id) ON DELETE CASCADE,
            FOREIGN KEY(updated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_graphic_frames_page
            ON graphic_page_frames(graphic_page_id, status, sort_order);

        CREATE TABLE IF NOT EXISTS graphic_page_bookmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            graphic_page_id INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, graphic_page_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(graphic_page_id) REFERENCES graphic_pages(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_graphic_bookmarks_user
            ON graphic_page_bookmarks(user_id, updated_at);

        CREATE TABLE IF NOT EXISTS graphic_page_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            graphic_page_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            moderated_by_user_id INTEGER,
            moderation_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(graphic_page_id) REFERENCES graphic_pages(id) ON DELETE CASCADE,
            FOREIGN KEY(moderated_by_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_graphic_page_comments_status
            ON graphic_page_comments(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_graphic_page_comments_page
            ON graphic_page_comments(graphic_page_id, status, created_at);

        CREATE TABLE IF NOT EXISTS graphic_reading_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            graphic_chapter_id INTEGER NOT NULL,
            graphic_page_id INTEGER,
            event_type TEXT NOT NULL,
            session_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(graphic_chapter_id) REFERENCES graphic_chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(graphic_page_id) REFERENCES graphic_pages(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_graphic_events_chapter_type
            ON graphic_reading_events(graphic_chapter_id, event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_graphic_events_user
            ON graphic_reading_events(user_id, created_at);
        """
    )
    for key, value in {
        "purchase_cancel_minutes": "15",
        "comic_ocr_enabled": "1",
        "comic_default_translation_language": "ru",
        "comic_page_comments_enabled": "1",
    }.items():
        await db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )


def _bounded_rect(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(0.0, min(1.0, number))


async def create_payment_intent(
    user_id: int,
    canonical_payload: str,
    amount_stars: int,
    *,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    token = os.urandom(18).hex()
    now_dt = datetime.now(timezone.utc)
    expires = now_dt + timedelta(minutes=max(5, min(120, int(ttl_minutes))))
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO payment_intents(token, user_id, canonical_payload, amount_stars, status,
                                        expires_at, created_at, updated_at)
            VALUES(?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (token, int(user_id), str(canonical_payload), int(amount_stars), expires.isoformat(), now_dt.isoformat(), now_dt.isoformat()),
        )
        await db.commit()
    return {"token": token, "payload": f"vox:intent:{token}", "expires_at": expires.isoformat()}


async def attach_payment_intent_message(token: str, message_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE payment_intents SET invoice_message_id=?, updated_at=? WHERE token=?",
            (int(message_id), utc_now(), str(token)),
        )
        await db.commit()


async def get_payment_intent(invoice_payload_or_token: str) -> aiosqlite.Row | None:
    value = str(invoice_payload_or_token or "")
    token = value.split(":", 2)[2] if value.startswith("vox:intent:") else value
    async with connect() as db:
        cur = await db.execute("SELECT * FROM payment_intents WHERE token=?", (token,))
        return await cur.fetchone()


async def cancel_payment_intent(token: str, user_id: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE payment_intents SET status='canceled', updated_at=?
            WHERE token=? AND user_id=? AND status='active'
            """,
            (now, str(token), int(user_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def validate_payment_intent(invoice_payload: str, user_id: int, amount_stars: int) -> dict[str, Any] | None:
    intent = await get_payment_intent(invoice_payload)
    if not intent or int(intent["user_id"]) != int(user_id):
        return None
    try:
        expires = datetime.fromisoformat(str(intent["expires_at"]).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if str(intent["status"]) != "active" or datetime.now(timezone.utc) >= expires:
        return None
    if int(intent["amount_stars"] or 0) != int(amount_stars):
        return None
    return {key: intent[key] for key in intent.keys()}


_old_get_purchase_target_v1100_intents = get_purchase_target


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    value = str(payload or "")
    if value.startswith("vox:intent:"):
        intent = await get_payment_intent(value)
        if not intent or str(intent["status"]) not in {"active", "paid"}:
            return None
        return await _old_get_purchase_target_v1100_intents(str(intent["canonical_payload"]))
    return await _old_get_purchase_target_v1100_intents(value)


_old_create_paid_purchase_v1100_intents = create_paid_purchase


async def create_paid_purchase(
    *,
    user_id: int,
    payload: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
) -> int:
    value = str(payload or "")
    if not value.startswith("vox:intent:"):
        return await _old_create_paid_purchase_v1100_intents(
            user_id=user_id,
            payload=value,
            amount_stars=amount_stars,
            telegram_payment_charge_id=telegram_payment_charge_id,
        )
    intent = await get_payment_intent(value)
    if not intent or int(intent["user_id"]) != int(user_id):
        raise ValueError("Платёжный счёт не найден")
    status = str(intent["status"] or "")
    charge_id = str(telegram_payment_charge_id or "").strip()
    if status == "paid" and str(intent["paid_charge_id"] or "") == charge_id:
        return await _old_create_paid_purchase_v1100_intents(
            user_id=user_id,
            payload=str(intent["canonical_payload"]),
            amount_stars=amount_stars,
            telegram_payment_charge_id=charge_id,
        )
    valid = await validate_payment_intent(value, user_id, amount_stars)
    if not valid:
        raise ValueError("Счёт отменён или срок его действия истёк")
    purchase_id = await _old_create_paid_purchase_v1100_intents(
        user_id=user_id,
        payload=str(intent["canonical_payload"]),
        amount_stars=amount_stars,
        telegram_payment_charge_id=charge_id,
    )
    async with connect() as db:
        await db.execute(
            "UPDATE payment_intents SET status='paid', paid_charge_id=?, updated_at=? WHERE token=?",
            (charge_id, utc_now(), str(intent["token"])),
        )
        await db.commit()
    return purchase_id


async def mark_purchase_access_used(
    user_id: int,
    *,
    book_id: int | None = None,
    chapter_id: int | None = None,
    audio_chapter_id: int | None = None,
    graphic_chapter_id: int | None = None,
) -> int | None:
    """Фиксирует первое фактическое получение платного содержимого.

    Событие записывается только для покупки, которая реально дала доступ. Пакеты
    глав здесь не учитываются: их использование определяется списанием кредита.
    Повторные запросы безопасны благодаря PRIMARY KEY по purchase_id.
    """
    supplied = [
        value is not None
        for value in (book_id, chapter_id, audio_chapter_id, graphic_chapter_id)
    ]
    if sum(supplied) != 1:
        raise ValueError("Нужно указать ровно один объект доступа")

    purchase_id: int | None = None
    usage_kind = "book"
    source_id = int(book_id) if book_id is not None else None
    async with connect() as db:
        if chapter_id is not None:
            usage_kind = "chapter"
            source_id = int(chapter_id)
            cur = await db.execute(
                """
                SELECT 1 FROM chapter_package_unlocks cpu
                JOIN chapter_package_balances cpb ON cpb.id=cpu.balance_id
                JOIN purchases pp ON pp.id=cpu.purchase_id
                WHERE cpu.user_id=? AND cpu.chapter_id=?
                  AND cpb.status='active' AND pp.status='paid' LIMIT 1
                """,
                (int(user_id), int(chapter_id)),
            )
            if await cur.fetchone():
                return None
            cur = await db.execute(
                """
                SELECT p.id
                FROM chapters c
                JOIN purchases p ON p.user_id=? AND p.status='paid'
                WHERE c.id=?
                  AND COALESCE(p.purchase_kind, 'content')='content'
                  AND (
                       p.chapter_id=c.id
                       OR (
                            p.book_id=c.book_id
                            AND p.chapter_id IS NULL
                            AND p.audio_chapter_id IS NULL
                            AND p.graphic_chapter_id IS NULL
                            AND p.graphic_volume_number IS NULL
                       )
                  )
                ORDER BY CASE WHEN p.chapter_id=c.id THEN 0 ELSE 1 END, p.id DESC
                LIMIT 1
                """,
                (int(user_id), int(chapter_id)),
            )
        elif audio_chapter_id is not None:
            usage_kind = "audio"
            source_id = int(audio_chapter_id)
            cur = await db.execute(
                """
                SELECT p.id
                FROM audio_chapters ac
                JOIN purchases p ON p.user_id=? AND p.status='paid'
                WHERE ac.id=?
                  AND COALESCE(p.purchase_kind, 'content')='content'
                  AND (
                       p.audio_chapter_id=ac.id
                       OR (
                            p.book_id=ac.book_id
                            AND p.chapter_id IS NULL
                            AND p.audio_chapter_id IS NULL
                            AND p.graphic_chapter_id IS NULL
                            AND p.graphic_volume_number IS NULL
                       )
                  )
                ORDER BY CASE WHEN p.audio_chapter_id=ac.id THEN 0 ELSE 1 END, p.id DESC
                LIMIT 1
                """,
                (int(user_id), int(audio_chapter_id)),
            )
        elif graphic_chapter_id is not None:
            usage_kind = "graphic"
            source_id = int(graphic_chapter_id)
            cur = await db.execute(
                """
                SELECT 1 FROM chapter_package_unlocks cpu
                JOIN chapter_package_balances cpb ON cpb.id=cpu.balance_id
                JOIN purchases pp ON pp.id=cpu.purchase_id
                WHERE cpu.user_id=? AND cpu.graphic_chapter_id=?
                  AND cpb.status='active' AND pp.status='paid' LIMIT 1
                """,
                (int(user_id), int(graphic_chapter_id)),
            )
            if await cur.fetchone():
                return None
            cur = await db.execute(
                """
                SELECT p.id
                FROM graphic_chapters gc
                JOIN purchases p ON p.user_id=? AND p.status='paid'
                WHERE gc.id=?
                  AND COALESCE(p.purchase_kind, 'content')!='chapter_package'
                  AND (
                       p.graphic_chapter_id=gc.id
                       OR (
                            p.book_id=gc.book_id
                            AND p.graphic_volume_number=gc.volume_number
                            AND COALESCE(p.purchase_kind, '')='graphic_volume'
                       )
                       OR (
                            p.book_id=gc.book_id
                            AND COALESCE(p.purchase_kind, 'content')='content'
                            AND p.chapter_id IS NULL
                            AND p.audio_chapter_id IS NULL
                            AND p.graphic_chapter_id IS NULL
                            AND p.graphic_volume_number IS NULL
                       )
                  )
                ORDER BY CASE
                    WHEN p.graphic_chapter_id=gc.id THEN 0
                    WHEN p.graphic_volume_number=gc.volume_number
                         AND COALESCE(p.purchase_kind, '')='graphic_volume' THEN 1
                    ELSE 2
                END, p.id DESC
                LIMIT 1
                """,
                (int(user_id), int(graphic_chapter_id)),
            )
        else:
            usage_kind = "book"
            source_id = int(book_id or 0)
            cur = await db.execute(
                """
                SELECT p.id
                FROM purchases p
                WHERE p.user_id=? AND p.status='paid' AND p.book_id=?
                  AND COALESCE(p.purchase_kind, 'content')='content'
                  AND p.chapter_id IS NULL
                  AND p.audio_chapter_id IS NULL
                  AND p.graphic_chapter_id IS NULL
                  AND p.graphic_volume_number IS NULL
                ORDER BY p.id DESC
                LIMIT 1
                """,
                (int(user_id), int(book_id or 0)),
            )
        row = await cur.fetchone()
        if row:
            purchase_id = int(row["id"])
            await db.execute(
                """
                INSERT OR IGNORE INTO purchase_usage_events(
                    purchase_id, user_id, usage_kind, source_id, first_used_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (purchase_id, int(user_id), usage_kind, source_id, utc_now()),
            )
            await db.commit()
    return purchase_id


async def get_immediate_purchase_cancel_eligibility(purchase_id: int, user_id: int) -> dict[str, Any]:
    purchase = await get_purchase(int(purchase_id))
    result: dict[str, Any] = {"allowed": False, "reason": "Покупка недоступна", "minutes_left": 0}
    if not purchase or int(purchase["user_id"]) != int(user_id) or str(purchase["status"]) != "paid":
        return result
    try:
        created = datetime.fromisoformat(str(purchase["created_at"]).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except Exception:
        return {**result, "reason": "Не удалось определить время покупки"}
    async with connect() as db:
        cur = await db.execute("SELECT value FROM settings WHERE key='purchase_cancel_minutes'")
        row = await cur.fetchone()
        window = max(1, min(120, int(row["value"] if row else 15)))
        deadline = created + timedelta(minutes=window)
        seconds_left = int((deadline - datetime.now(timezone.utc)).total_seconds())
        if seconds_left <= 0:
            return {**result, "reason": f"Автоматическая отмена доступна только первые {window} минут"}
        kind = str(purchase["purchase_kind"] or "content")
        if kind not in {"content", "graphic_chapter", "graphic_volume", "chapter_package"}:
            return {**result, "reason": "Для этой операции быстрая отмена недоступна; обратитесь в поддержку"}
        cur = await db.execute(
            "SELECT 1 FROM purchase_usage_events WHERE purchase_id=? LIMIT 1",
            (int(purchase_id),),
        )
        used = await cur.fetchone() is not None
        if kind == "chapter_package":
            total = int(purchase["chapter_package_total"] or purchase["chapter_package_count"] or 0)
            remaining = int(purchase["chapter_package_remaining"] or 0)
            used = remaining < total
        elif not used and purchase["chapter_id"]:
            cur = await db.execute(
                "SELECT 1 FROM reading_progress WHERE user_id=? AND chapter_id=? AND position_percent>0 LIMIT 1",
                (int(user_id), int(purchase["chapter_id"])),
            )
            used = await cur.fetchone() is not None
        elif not used and purchase["audio_chapter_id"]:
            cur = await db.execute(
                "SELECT 1 FROM listening_progress WHERE user_id=? AND audio_chapter_id=? AND position_seconds>0 LIMIT 1",
                (int(user_id), int(purchase["audio_chapter_id"])),
            )
            used = await cur.fetchone() is not None
        elif not used and "graphic_chapter_id" in purchase.keys() and purchase["graphic_chapter_id"]:
            cur = await db.execute(
                "SELECT 1 FROM graphic_reading_progress WHERE user_id=? AND graphic_chapter_id=? AND page_number>1 LIMIT 1",
                (int(user_id), int(purchase["graphic_chapter_id"])),
            )
            used = await cur.fetchone() is not None
        elif not used and "graphic_volume_number" in purchase.keys() and purchase["graphic_volume_number"]:
            cur = await db.execute(
                """
                SELECT 1 FROM graphic_reading_progress grp
                JOIN graphic_chapters gc ON gc.id=grp.graphic_chapter_id
                WHERE grp.user_id=? AND gc.book_id=? AND gc.volume_number=? AND grp.page_number>1 LIMIT 1
                """,
                (int(user_id), int(purchase["book_id"]), int(purchase["graphic_volume_number"])),
            )
            used = await cur.fetchone() is not None
        elif not used and purchase["book_id"]:
            cur = await db.execute(
                """
                SELECT 1 FROM reading_progress
                WHERE user_id=? AND book_id=? AND position_percent>0
                UNION ALL
                SELECT 1 FROM listening_progress lp
                JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id
                WHERE lp.user_id=? AND ac.book_id=? AND lp.position_seconds>0
                UNION ALL
                SELECT 1 FROM graphic_reading_progress grp
                JOIN graphic_chapters gc ON gc.id=grp.graphic_chapter_id
                WHERE grp.user_id=? AND gc.book_id=? AND grp.page_number>1
                LIMIT 1
                """,
                (int(user_id), int(purchase["book_id"]), int(user_id), int(purchase["book_id"]),
                 int(user_id), int(purchase["book_id"])),
            )
            used = await cur.fetchone() is not None
        if used:
            return {**result, "reason": "Материал уже начали использовать; оформите обычный запрос на возврат"}
    return {
        "allowed": True,
        "reason": "Покупка ещё не использована",
        "minutes_left": max(1, (seconds_left + 59) // 60),
        "deadline": deadline.isoformat(),
    }


async def create_immediate_cancel_request(purchase_id: int, user_id: int) -> int:
    eligibility = await get_immediate_purchase_cancel_eligibility(purchase_id, user_id)
    if not eligibility.get("allowed"):
        raise ValueError(str(eligibility.get("reason") or "Отмена недоступна"))
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT id FROM refund_requests WHERE purchase_id=? AND status IN ('new','pending','refunded') LIMIT 1",
            (int(purchase_id),),
        )
        existing = await cur.fetchone()
        if existing:
            await db.commit()
            return int(existing["id"])
        cur = await db.execute(
            "UPDATE purchases SET status='canceling' WHERE id=? AND user_id=? AND status='paid'",
            (int(purchase_id), int(user_id)),
        )
        if cur.rowcount <= 0:
            await db.rollback()
            raise ValueError("Покупка уже отменяется или была обработана")
        now = utc_now()
        cur = await db.execute(
            """
            INSERT INTO refund_requests(purchase_id, user_id, reason, status, created_at, updated_at)
            VALUES(?, ?, 'Отмена неиспользованной покупки пользователем', 'new', ?, ?)
            """,
            (int(purchase_id), int(user_id), now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


_old_reject_refund_request_v1100_canceling = reject_refund_request


async def reject_refund_request(refund_id: int, handled_by_user_id: int | None, note: str = "Возврат отклонён") -> bool:
    async with connect() as db:
        cur = await db.execute(
            "SELECT purchase_id FROM refund_requests WHERE id=?",
            (int(refund_id),),
        )
        row = await cur.fetchone()
    ok = await _old_reject_refund_request_v1100_canceling(refund_id, handled_by_user_id, note)
    if ok and row:
        async with connect() as db:
            await db.execute(
                "UPDATE purchases SET status='paid' WHERE id=? AND status='canceling'",
                (int(row["purchase_id"]),),
            )
            await db.commit()
    return ok


async def upsert_graphic_page_text(
    graphic_page_id: int,
    author_user_id: int,
    *,
    language_code: str,
    text_kind: str,
    text: str,
    confidence: float = 0,
    status: str = "published",
) -> bool:
    language = str(language_code or "ru").strip().lower()[:12] or "ru"
    kind = str(text_kind or "ocr").strip().lower()
    if kind not in {"ocr", "original", "translation"}:
        raise ValueError("Неизвестный вид текста")
    clean = str(text or "").strip()[:200000]
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT gp.id FROM graphic_pages gp
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gp.id=? AND ap.user_id=? AND gc.status!='deleted'
            """,
            (int(graphic_page_id), int(author_user_id)),
        )
        if not await cur.fetchone():
            return False
        now = utc_now()
        await db.execute(
            """
            INSERT INTO graphic_page_texts(graphic_page_id, language_code, text_kind, text, confidence,
                                           status, updated_by_user_id, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(graphic_page_id, language_code, text_kind) DO UPDATE SET
                text=excluded.text, confidence=excluded.confidence, status=excluded.status,
                updated_by_user_id=excluded.updated_by_user_id, updated_at=excluded.updated_at
            """,
            (int(graphic_page_id), language, kind, clean, max(0.0, min(100.0, float(confidence))),
             str(status or "published"), int(author_user_id), now, now),
        )
        await db.commit()
        return True


async def list_graphic_page_texts(graphic_page_id: int, *, published_only: bool = False) -> list[aiosqlite.Row]:
    clause = "AND status='published'" if published_only else ""
    async with connect() as db:
        cur = await db.execute(
            f"SELECT * FROM graphic_page_texts WHERE graphic_page_id=? {clause} ORDER BY language_code, text_kind",
            (int(graphic_page_id),),
        )
        return await cur.fetchall()


async def replace_graphic_translation_regions_for_author(
    graphic_page_id: int,
    author_user_id: int,
    language_code: str,
    regions: list[dict[str, Any]],
) -> bool:
    language = str(language_code or "ru").strip().lower()[:12] or "ru"
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            SELECT gp.id FROM graphic_pages gp
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gp.id=? AND ap.user_id=? AND gc.status!='deleted'
            """,
            (int(graphic_page_id), int(author_user_id)),
        )
        if not await cur.fetchone():
            await db.rollback(); return False
        await db.execute(
            "DELETE FROM graphic_translation_regions WHERE graphic_page_id=? AND language_code=?",
            (int(graphic_page_id), language),
        )
        now = utc_now()
        for index, item in enumerate(regions[:200]):
            x = _bounded_rect(item.get("x")); y = _bounded_rect(item.get("y"))
            w = max(0.01, min(1.0 - x, _bounded_rect(item.get("width"))))
            h = max(0.01, min(1.0 - y, _bounded_rect(item.get("height"))))
            text = str(item.get("text") or "").strip()[:5000]
            if not text:
                continue
            style = str(item.get("style") or "bubble")[:32]
            await db.execute(
                """
                INSERT INTO graphic_translation_regions(graphic_page_id, language_code, x, y, width, height,
                                                        text, style, sort_order, status, updated_by_user_id,
                                                        created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?)
                """,
                (int(graphic_page_id), language, x, y, w, h, text, style, index, int(author_user_id), now, now),
            )
        await db.commit(); return True


async def list_graphic_translation_regions(graphic_page_id: int, language_code: str) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM graphic_translation_regions
            WHERE graphic_page_id=? AND language_code=? AND status='published'
            ORDER BY sort_order, id
            """,
            (int(graphic_page_id), str(language_code or "ru").strip().lower()[:12] or "ru"),
        )
        return await cur.fetchall()


async def replace_graphic_frames_for_author(
    graphic_page_id: int,
    author_user_id: int,
    frames: list[dict[str, Any]],
    *,
    source: str = "manual",
) -> bool:
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            SELECT gp.id FROM graphic_pages gp
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gp.id=? AND ap.user_id=? AND gc.status!='deleted'
            """,
            (int(graphic_page_id), int(author_user_id)),
        )
        if not await cur.fetchone():
            await db.rollback(); return False
        await db.execute("DELETE FROM graphic_page_frames WHERE graphic_page_id=?", (int(graphic_page_id),))
        now = utc_now()
        for index, item in enumerate(frames[:100]):
            x = _bounded_rect(item.get("x")); y = _bounded_rect(item.get("y"))
            w = max(0.02, min(1.0 - x, _bounded_rect(item.get("width"))))
            h = max(0.02, min(1.0 - y, _bounded_rect(item.get("height"))))
            await db.execute(
                """
                INSERT INTO graphic_page_frames(graphic_page_id, x, y, width, height, sort_order,
                                                source, status, updated_by_user_id, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?)
                """,
                (int(graphic_page_id), x, y, w, h, index, str(source or "manual")[:20], int(author_user_id), now, now),
            )
        await db.commit(); return True


async def list_graphic_page_frames(graphic_page_id: int) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM graphic_page_frames WHERE graphic_page_id=? AND status='published' ORDER BY sort_order, id",
            (int(graphic_page_id),),
        )
        return await cur.fetchall()


async def get_graphic_reader_layers(page_ids: list[int], language_code: str = "ru", user_id: int | None = None) -> dict[int, dict[str, Any]]:
    ids = [int(value) for value in page_ids if int(value) > 0]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    language = str(language_code or "ru").strip().lower()[:12] or "ru"
    result = {page_id: {"texts": [], "translations": [], "frames": [], "bookmarked": False, "comments": []} for page_id in ids}
    async with connect() as db:
        cur = await db.execute(
            f"SELECT * FROM graphic_page_texts WHERE graphic_page_id IN ({placeholders}) AND status='published' ORDER BY id",
            tuple(ids),
        )
        for row in await cur.fetchall(): result[int(row["graphic_page_id"])]["texts"].append({key: row[key] for key in row.keys()})
        cur = await db.execute(
            f"SELECT * FROM graphic_translation_regions WHERE graphic_page_id IN ({placeholders}) AND language_code=? AND status='published' ORDER BY sort_order,id",
            (*ids, language),
        )
        for row in await cur.fetchall(): result[int(row["graphic_page_id"])]["translations"].append({key: row[key] for key in row.keys()})
        cur = await db.execute(
            f"SELECT * FROM graphic_page_frames WHERE graphic_page_id IN ({placeholders}) AND status='published' ORDER BY sort_order,id",
            tuple(ids),
        )
        for row in await cur.fetchall(): result[int(row["graphic_page_id"])]["frames"].append({key: row[key] for key in row.keys()})
        if user_id is not None:
            cur = await db.execute(
                f"SELECT graphic_page_id FROM graphic_page_bookmarks WHERE user_id=? AND graphic_page_id IN ({placeholders})",
                (int(user_id), *ids),
            )
            for row in await cur.fetchall(): result[int(row["graphic_page_id"])]["bookmarked"] = True
        cur = await db.execute(
            f"""
            SELECT gpc.*, u.username, u.full_name FROM graphic_page_comments gpc
            JOIN users u ON u.id=gpc.user_id
            WHERE gpc.graphic_page_id IN ({placeholders}) AND gpc.status='published'
            ORDER BY gpc.created_at DESC
            """,
            tuple(ids),
        )
        for row in await cur.fetchall(): result[int(row["graphic_page_id"])]["comments"].append({key: row[key] for key in row.keys()})
    return result


async def search_graphic_book_text(book_id: int, query: str, language_code: str = "ru", limit: int = 50) -> list[aiosqlite.Row]:
    clean = str(query or "").strip()
    if len(clean) < 2:
        return []
    pattern = f"%{clean[:200]}%"
    language = str(language_code or "ru").strip().lower()[:12] or "ru"
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT DISTINCT gp.id AS graphic_page_id, gp.page_number, gc.id AS graphic_chapter_id,
                   gc.number AS chapter_number, gc.title AS chapter_title, gc.volume_number,
                   substr(gpt.text, 1, 320) AS snippet, gpt.text_kind, gpt.language_code
            FROM graphic_page_texts gpt
            JOIN graphic_pages gp ON gp.id=gpt.graphic_page_id
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            WHERE gc.book_id=? AND gc.status='published' AND gpt.status='published'
              AND (gpt.language_code=? OR gpt.text_kind IN ('ocr','original'))
              AND lower(gpt.text) LIKE lower(?)
            ORDER BY gc.volume_number, gc.number, gp.page_number
            LIMIT ?
            """,
            (int(book_id), language, pattern, max(1, min(200, int(limit)))),
        )
        return await cur.fetchall()


async def toggle_graphic_page_bookmark(user_id: int, graphic_page_id: int, note: str = "") -> bool:
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM graphic_page_bookmarks WHERE user_id=? AND graphic_page_id=?",
            (int(user_id), int(graphic_page_id)),
        )
        existing = await cur.fetchone()
        if existing:
            await db.execute("DELETE FROM graphic_page_bookmarks WHERE id=?", (int(existing["id"]),))
            await db.commit(); return False
        now = utc_now()
        await db.execute(
            "INSERT INTO graphic_page_bookmarks(user_id, graphic_page_id, note, created_at, updated_at) VALUES(?, ?, ?, ?, ?)",
            (int(user_id), int(graphic_page_id), str(note or "")[:500], now, now),
        )
        await db.commit(); return True


async def list_user_graphic_bookmarks(user_id: int, book_id: int | None = None) -> list[aiosqlite.Row]:
    clause = "AND gc.book_id=?" if book_id is not None else ""
    params: tuple[Any, ...] = (int(user_id), int(book_id)) if book_id is not None else (int(user_id),)
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT gpb.*, gp.page_number, gc.id AS graphic_chapter_id, gc.number AS chapter_number,
                   gc.title AS chapter_title, gc.volume_number, b.id AS book_id, b.title AS book_title
            FROM graphic_page_bookmarks gpb
            JOIN graphic_pages gp ON gp.id=gpb.graphic_page_id
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            WHERE gpb.user_id=? {clause}
            ORDER BY gpb.updated_at DESC
            """,
            params,
        )
        return await cur.fetchall()


async def add_graphic_page_comment(user_id: int, graphic_page_id: int, text: str) -> int:
    clean = str(text or "").strip()
    if len(clean) < 2: raise ValueError("Комментарий слишком короткий")
    if len(clean) > 2000: raise ValueError("Комментарий слишком длинный")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO graphic_page_comments(user_id, graphic_page_id, text, status, created_at, updated_at) VALUES(?, ?, ?, 'pending', ?, ?)",
            (int(user_id), int(graphic_page_id), clean, now, now),
        )
        await db.commit(); return int(cur.lastrowid)


async def list_graphic_page_comments_for_moderation(status: str = "pending", limit: int = 100) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT gpc.*, u.telegram_id, u.username, u.full_name, gp.page_number,
                   gc.id AS graphic_chapter_id, gc.title AS chapter_title, b.id AS book_id, b.title AS book_title
            FROM graphic_page_comments gpc
            JOIN users u ON u.id=gpc.user_id
            JOIN graphic_pages gp ON gp.id=gpc.graphic_page_id
            JOIN graphic_chapters gc ON gc.id=gp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            WHERE gpc.status=? ORDER BY gpc.created_at LIMIT ?
            """,
            (str(status), max(1, min(500, int(limit)))),
        )
        return await cur.fetchall()


async def set_graphic_page_comment_status(comment_id: int, actor_user_id: int, status: str, note: str = "") -> bool:
    if status not in {"published", "hidden", "rejected"}:
        raise ValueError("Неизвестный статус комментария")
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE graphic_page_comments SET status=?, moderated_by_user_id=?, moderation_note=?, updated_at=?
            WHERE id=?
            """,
            (status, int(actor_user_id), str(note or "")[:500], utc_now(), int(comment_id)),
        )
        await db.commit(); return cur.rowcount > 0


async def record_graphic_reading_event(
    user_id: int,
    graphic_chapter_id: int,
    event_type: str,
    *,
    graphic_page_id: int | None = None,
    session_key: str = "",
) -> None:
    if event_type not in {"open", "page_view", "complete", "exit", "search", "frame_view"}:
        return
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO graphic_reading_events(user_id, graphic_chapter_id, graphic_page_id, event_type, session_key, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (int(user_id), int(graphic_chapter_id), int(graphic_page_id) if graphic_page_id else None,
             event_type, str(session_key or "")[:80], utc_now()),
        )
        await db.commit()


async def get_graphic_chapter_statistics(graphic_chapter_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT COUNT(DISTINCT CASE WHEN event_type='open' THEN user_id END) AS unique_openers,
                   COUNT(CASE WHEN event_type='open' THEN 1 END) AS opens,
                   COUNT(CASE WHEN event_type='page_view' THEN 1 END) AS page_views,
                   COUNT(DISTINCT CASE WHEN event_type='complete' THEN user_id END) AS completers,
                   COUNT(CASE WHEN event_type='exit' THEN 1 END) AS exits,
                   COUNT(CASE WHEN event_type='frame_view' THEN 1 END) AS frame_views
            FROM graphic_reading_events WHERE graphic_chapter_id=?
            """,
            (int(graphic_chapter_id),),
        )
        row = await cur.fetchone()
        return {key: int(row[key] or 0) for key in row.keys()} if row else {}
