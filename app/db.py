import os
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


async def init_db() -> None:
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
        "pricing_type": "ALTER TABLE books ADD COLUMN pricing_type TEXT NOT NULL DEFAULT 'free'",
        "price_stars": "ALTER TABLE books ADD COLUMN price_stars INTEGER NOT NULL DEFAULT 0",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await db.execute(sql)


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
            await db.execute(sql)


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
        await db.execute("ALTER TABLE purchases ADD COLUMN payload TEXT")
    if "purchase_kind" not in existing:
        await db.execute("ALTER TABLE purchases ADD COLUMN purchase_kind TEXT NOT NULL DEFAULT 'content'")
    cur = await db.execute("PRAGMA table_info(complaints)")
    existing = {row[1] for row in await cur.fetchall()}
    if "handled_by_user_id" not in existing:
        await db.execute("ALTER TABLE complaints ADD COLUMN handled_by_user_id INTEGER")
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
                      cover_file_id: str | None = None) -> int:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO books(author_id, title, description, age_limit, writing_status, publication_status,
                              cover_file_id, allow_download, pricing_type, price_stars, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
            """,
            (
                author_id,
                title,
                description,
                age_limit,
                writing_status,
                cover_file_id,
                1 if allow_download else 0,
                pricing_type,
                int(price_stars),
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
              AND (cover_path IS NULL OR TRIM(cover_path) = '')
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
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status!='deleted') AS chapters_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status!='deleted') AS audio_count,
                   (SELECT COUNT(*) FROM purchases p WHERE p.status='paid' AND (
                       p.book_id=b.id OR
                       p.chapter_id IN (SELECT c2.id FROM chapters c2 WHERE c2.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT a2.id FROM audio_chapters a2 WHERE a2.book_id=b.id)
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
    }
    clean: dict[str, Any] = {key: value for key, value in values.items() if key in allowed}
    if not clean:
        return False
    if "title" in clean:
        clean["title"] = str(clean["title"]).strip()[:160]
        if len(clean["title"]) < 2:
            return False
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

    now = utc_now()
    fields = [f"{key}=?" for key in clean]
    values_list = list(clean.values())
    sensitive = {"title", "description", "age_limit", "pricing_type", "price_stars"}
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
    audio_status = "ac.status != 'deleted'" if include_drafts else "ac.status = 'published'"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.*, a.pen_name,
                   COALESCE((SELECT AVG(r.rating) FROM reviews r WHERE r.book_id=b.id AND r.status='published'), 0) AS rating,
                   (SELECT COUNT(*) FROM reviews r WHERE r.book_id=b.id AND r.status='published') AS reviews_count,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND {chapter_status}) AS chapters_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND {audio_status}) AS audio_count,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND {chapter_status} AND c.is_free=1) AS free_chapters_count,
                   (SELECT c.id FROM chapters c WHERE c.book_id=b.id AND {chapter_status} ORDER BY c.number, c.id LIMIT 1) AS first_chapter_id,
                   (SELECT ac.id FROM audio_chapters ac WHERE ac.book_id=b.id AND {audio_status} ORDER BY ac.number, ac.id LIMIT 1) AS first_audio_id,
                   (SELECT GROUP_CONCAT(v.option_label, '||') FROM book_option_values v WHERE v.book_id=b.id AND v.option_group='genres') AS genre_labels,
                   (
                     SELECT COUNT(*) FROM purchases p
                     WHERE p.status='paid' AND (
                       p.book_id=b.id OR
                       p.chapter_id IN (SELECT c3.id FROM chapters c3 WHERE c3.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT ac3.id FROM audio_chapters ac3 WHERE ac3.book_id=b.id)
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
            SELECT b.*, a.pen_name
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
                "books_published": 0, "chapters": 0, "audio": 0,
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
            "SELECT COUNT(*) FROM chapters c JOIN books b ON b.id=c.book_id WHERE b.author_id=? AND b.publication_status!='deleted'",
            (author_id,),
        )
        chapters = await cur.fetchone()
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
            "chapters": int(chapters[0] or 0),
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


async def count_chapters_for_book(book_id: int) -> int:
    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM chapters WHERE book_id=? AND status != 'deleted'", (book_id,))
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def get_chapter(chapter_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, b.title AS book_title, b.publication_status, b.price_stars AS book_price_stars,
                   b.pricing_type, a.pen_name
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
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') AS chapters_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status='published') AS audio_count,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published' AND c.is_free=1) AS free_chapters_count,
                   COALESCE((SELECT SUM(LENGTH(c.text)) FROM chapters c WHERE c.book_id=b.id AND c.status='published'), 0) AS text_chars,
                   COALESCE((SELECT AVG(r.rating) FROM reviews r WHERE r.book_id=b.id AND r.status='published'), 0) AS rating,
                   (SELECT COUNT(*) FROM reviews r WHERE r.book_id=b.id AND r.status='published') AS reviews_count,
                   (
                     SELECT COUNT(*) FROM purchases p
                     WHERE p.status='paid' AND (
                       p.book_id=b.id OR
                       p.chapter_id IN (SELECT c3.id FROM chapters c3 WHERE c3.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT ac3.id FROM audio_chapters ac3 WHERE ac3.book_id=b.id)
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
            await db.execute(f"ALTER TABLE user_preferences ADD COLUMN {column} INTEGER NOT NULL DEFAULT 1")
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
        audio = await db.execute(
            "UPDATE audio_chapters SET status='published', updated_at=? WHERE book_id=? AND status='draft'",
            (now, int(book_id)),
        )
        await db.commit()
        return {"chapters": int(chapters.rowcount), "audio": int(audio.rowcount)}


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
                      )
                  )
              )
            ORDER BY u.id
            LIMIT ?
            """,
            (book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, int(limit)),
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
            SELECT bm.*, b.title, b.description, b.age_limit, b.publication_status, b.cover_path,
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
            SELECT rp.*, b.title, b.description, b.age_limit, b.cover_path,
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
                   ac.duration_seconds, b.title, b.cover_path, ap.pen_name
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
    """
    parts = str(payload or "").split(":")
    if len(parts) < 3 or parts[0] != "vox":
        return None
    kind = parts[1]
    promo_code = None
    if len(parts) == 5 and parts[3] == "promo":
        promo_code = clean_promo_code(parts[4])
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
                COALESCE(SUM(CASE WHEN status='refunded' THEN gross_stars ELSE 0 END), 0) AS refunded
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
        cur = await db.execute("SELECT COALESCE(SUM(net_stars), 0) AS amount FROM author_ledger WHERE author_id=? AND status='available'", (author_id,))
        row = await cur.fetchone()
        amount = int(row["amount"] or 0)
        if amount < min_stars:
            raise ValueError(f"Минимальная сумма вывода: {min_stars} Stars")
        cur = await db.execute(
            """
            INSERT INTO author_payout_requests(author_id, author_user_id, amount_stars, method_type, payout_details,
                                               status, requested_at, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'new', ?, ?, ?)
            """,
            (author_id, int(author_user_id), amount, method["method_type"], method["details"], now, now, now),
        )
        payout_id = int(cur.lastrowid)
        await db.execute("UPDATE author_ledger SET status='payout_requested', updated_at=? WHERE author_id=? AND status='available'", (now, author_id))
        await db.execute(
            "INSERT INTO author_payout_logs(payout_request_id, actor_user_id, action, note, created_at) VALUES(?, ?, 'created', ?, ?)",
            (payout_id, int(author_user_id), f"Заявка на {amount} Stars", now),
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


# Stage 11: legal acceptances and documents
async def accept_legal_document(user_id: int, doc_code: str, doc_version: str) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO legal_acceptances(user_id, doc_code, doc_version, accepted_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, doc_code, doc_version) DO UPDATE SET accepted_at=excluded.accepted_at
            """,
            (int(user_id), str(doc_code), str(doc_version), now),
        )
        await db.commit()


async def has_accepted_legal_document(user_id: int, doc_code: str, doc_version: str) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM legal_acceptances WHERE user_id=? AND doc_code=? AND doc_version=? LIMIT 1",
            (int(user_id), str(doc_code), str(doc_version)),
        )
        return await cur.fetchone() is not None


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


async def list_recent_channel_posts(limit: int = 10) -> list[aiosqlite.Row]:
    # В текущей схеме канал фиксируется через audit_logs, чтобы не плодить отдельные таблицы.
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT target_id AS book_id, after_value AS status, created_at
            FROM audit_logs
            WHERE action IN ('book_published_channel_posted','book_published_channel_failed','channel_post_sent')
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return await cur.fetchall()


# v1.7.6 — безопасная финансовая цепочка и мобильный центр управления
async def _ensure_v176_schema(db: aiosqlite.Connection) -> None:
    """Мягкая миграция: связывает строки дохода с конкретной заявкой на выплату."""
    cur = await db.execute("PRAGMA table_info(author_ledger)")
    existing = {row[1] for row in await cur.fetchall()}
    if "payout_request_id" not in existing:
        await db.execute("ALTER TABLE author_ledger ADD COLUMN payout_request_id INTEGER")
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
    book_id = int(target["book_id"]) if kind == "book" else None
    chapter_id = int(target["target_id"]) if kind == "chapter" else None
    audio_chapter_id = int(target["target_id"]) if kind == "audio" else None
    purchase_kind = "ad_budget" if kind == "ad_budget" else "content"

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
            INSERT INTO purchases(user_id, book_id, chapter_id, audio_chapter_id, amount_stars, status,
                                  telegram_payment_charge_id, created_at, payload, purchase_kind)
            VALUES(?, ?, ?, ?, ?, 'paid', ?, ?, ?, ?)
            """,
            (int(user_id), book_id, chapter_id, audio_chapter_id, amount_stars, charge_id, now, payload, purchase_kind),
        )
        purchase_id = int(cur.lastrowid)

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
            available_at = (datetime.now(timezone.utc) + timedelta(days=hold_days)).isoformat()
            await db.execute(
                """
                INSERT INTO author_ledger(author_id, purchase_id, source_type, source_id, gross_stars,
                                          commission_percent, commission_stars, net_stars, hold_days,
                                          available_at, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'held', ?, ?)
                """,
                (int(author_id), purchase_id, kind, int(target["target_id"]), amount_stars, commission_percent,
                 commission_stars, net_stars, hold_days, available_at, now, now),
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
        if row["refund_status"] not in {"new", "pending"} or row["purchase_status"] != "paid":
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
        cur = await db.execute("SELECT COALESCE(SUM(net_stars), 0) AS amount FROM author_ledger WHERE author_id=? AND status='available'", (author_id,))
        row = await cur.fetchone()
        amount = int(row["amount"] or 0)
        if amount < min_stars:
            raise ValueError(f"Минимальная сумма вывода: {min_stars} Stars")
        cur = await db.execute(
            """
            INSERT INTO author_payout_requests(author_id, author_user_id, amount_stars, method_type, payout_details,
                                               status, requested_at, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'new', ?, ?, ?)
            """,
            (author_id, int(author_user_id), amount, method["method_type"], method["details"], now, now, now),
        )
        payout_id = int(cur.lastrowid)
        await db.execute(
            "UPDATE author_ledger SET status='payout_requested', payout_request_id=?, updated_at=? WHERE author_id=? AND status='available'",
            (payout_id, now, author_id),
        )
        await db.execute(
            "INSERT INTO author_payout_logs(payout_request_id, actor_user_id, action, note, created_at) VALUES(?, ?, 'created', ?, ?)",
            (payout_id, int(author_user_id), f"Заявка на {amount} Stars", now),
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
