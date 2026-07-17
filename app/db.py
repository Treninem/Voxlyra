import asyncio
import json
import os
import sqlite3
import uuid
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
                saved_is_free INTEGER,
                saved_price_stars INTEGER,
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
        await _ensure_chapter_columns(db)
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
        await _ensure_v1110_recommendation_schema(db)
        await _ensure_v1110_social_schema(db)
        await _ensure_v1110_assistant_schema(db)
        await _ensure_v1110_analytics_schema(db)
        await _ensure_v1110_premium_schema(db)
        await _ensure_v1111_final_schema(db)
        await _ensure_v1114_access_schema(db)
        await _ensure_v1118_premium_revenue_schema(db)
        await _ensure_v1200_bonus_wallet_schema(db)
        await db.commit()



async def _ensure_v1111_final_schema(db: aiosqlite.Connection) -> None:
    """Финальная безопасная нормализация бесплатных книг и согласий.

    Миграция идемпотентна: её можно выполнять при каждом Redeploy. Она не
    удаляет покупки, тексты, прогресс или файлы пользователей.
    """
    now = utc_now()
    # Сохраняем старые цены глав как черновик, затем полностью открываем книги
    # с нулевой ценой. Это исправляет старые противоречивые записи, где книга
    # бесплатна, а глава всё ещё имела закрывающий флаг.
    await db.execute(
        """
        UPDATE chapters
        SET saved_is_free=CASE
                WHEN saved_is_free IS NULL THEN is_free
                ELSE saved_is_free
            END,
            saved_price_stars=CASE
                WHEN COALESCE(saved_price_stars, 0)=0 AND COALESCE(price_stars, 0)>0
                    THEN price_stars
                ELSE saved_price_stars
            END,
            is_free=1,
            price_stars=0,
            updated_at=?
        WHERE status!='deleted' AND book_id IN (
            SELECT id FROM books
            WHERE publication_status!='deleted' AND COALESCE(price_stars, 0)<=0 AND COALESCE(pricing_type,'free')!='premium'
        )
        """,
        (now,),
    )
    await db.execute(
        """
        UPDATE books
        SET price_stars=0, pricing_type='free', updated_at=?
        WHERE publication_status!='deleted' AND COALESCE(price_stars, 0)<=0 AND COALESCE(pricing_type,'free')!='premium'
          AND (pricing_type!='free' OR price_stars!=0)
        """,
        (now,),
    )
    # У полностью бесплатных книг платные пакеты текстовых глав бессмысленны.
    await db.execute(
        """
        UPDATE chapter_packages
        SET is_active=0, updated_at=?
        WHERE content_scope IN ('text','all') AND book_id IN (
            SELECT id FROM books
            WHERE publication_status!='deleted' AND COALESCE(price_stars, 0)<=0 AND COALESCE(pricing_type,'free')!='premium'
        )
        """,
        (now,),
    )
    # Обычный выпуск приложения не требует повторного согласия. Для реальной
    # существенной редакции документа владелец/разработчик указывает здесь
    # конкретную версию через настройку legal_reaccept_<code>_version.
    for code in ("terms", "privacy", "personal_data_consent", "author_license", "author_data_consent"):
        await db.execute(
            """
            INSERT INTO settings(key, value, updated_at) VALUES(?, '', ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (f"legal_reaccept_{code}_version", now),
        )


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
        "license_type": "ALTER TABLE books ADD COLUMN license_type TEXT NOT NULL DEFAULT 'platform_original'",
        "source_name": "ALTER TABLE books ADD COLUMN source_name TEXT",
        "rights_checked": "ALTER TABLE books ADD COLUMN rights_checked INTEGER NOT NULL DEFAULT 0",
        "import_batch_id": "ALTER TABLE books ADD COLUMN import_batch_id INTEGER",
        "import_file_hash": "ALTER TABLE books ADD COLUMN import_file_hash TEXT",
        "source_author_name": "ALTER TABLE books ADD COLUMN source_author_name TEXT",
        "source_year": "ALTER TABLE books ADD COLUMN source_year TEXT",
        "source_language": "ALTER TABLE books ADD COLUMN source_language TEXT NOT NULL DEFAULT 'ru'",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await _execute_schema_ddl(db, sql)


async def _ensure_chapter_columns(db: aiosqlite.Connection) -> None:
    """Мягкая миграция правил продажи текстовых глав v1.11.0."""
    cur = await db.execute("PRAGMA table_info(chapters)")
    existing = {row[1] for row in await cur.fetchall()}
    migrations = {
        "saved_is_free": "ALTER TABLE chapters ADD COLUMN saved_is_free INTEGER",
        "saved_price_stars": "ALTER TABLE chapters ADD COLUMN saved_price_stars INTEGER",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await _execute_schema_ddl(db, sql)

    # Новое строгое правило: книга с нулевой ценой полностью бесплатна.
    # Старые цены сохраняются как черновик, чтобы автор мог восстановить их явно.
    await db.execute(
        """
        UPDATE chapters
        SET saved_is_free=COALESCE(saved_is_free, is_free),
            saved_price_stars=CASE
                WHEN COALESCE(saved_price_stars, 0) <= 0 AND price_stars > 0 THEN price_stars
                ELSE saved_price_stars
            END
        WHERE book_id IN (SELECT id FROM books WHERE COALESCE(price_stars, 0) <= 0 AND COALESCE(pricing_type,'free')!='premium')
          AND (is_free=0 OR price_stars>0)
        """
    )
    await db.execute(
        "UPDATE chapters SET is_free=1, price_stars=0 "
        "WHERE book_id IN (SELECT id FROM books WHERE COALESCE(price_stars, 0) <= 0 AND COALESCE(pricing_type,'free')!='premium')"
    )
    await db.execute(
        "UPDATE books SET pricing_type='free' WHERE COALESCE(price_stars, 0) <= 0 AND COALESCE(pricing_type,'free')!='premium'"
    )


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


async def _ensure_v1110_recommendation_schema(db: aiosqlite.Connection) -> None:
    """Персональная лента v1.11.0: события показа, открытия и скрытия рекомендаций."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS recommendation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            event_count INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, book_id, event_type),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_recommendation_events_user_type
            ON recommendation_events(user_id, event_type, updated_at);
        CREATE INDEX IF NOT EXISTS idx_recommendation_events_book_type
            ON recommendation_events(book_id, event_type, updated_at);
        """
    )


async def _ensure_v1110_social_schema(db: aiosqlite.Connection) -> None:
    """Обсуждения v1.11.0: ответы, спойлеры, лайки, реакции и жалобы."""
    cur = await db.execute("PRAGMA table_info(comments)")
    existing = {row[1] for row in await cur.fetchall()}
    migrations = {
        "parent_id": "ALTER TABLE comments ADD COLUMN parent_id INTEGER",
        "is_spoiler": "ALTER TABLE comments ADD COLUMN is_spoiler INTEGER NOT NULL DEFAULT 0",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await _execute_schema_ddl(db, sql)

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS comment_likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(comment_id, user_id),
            FOREIGN KEY(comment_id) REFERENCES comments(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS chapter_reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reaction_code TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(chapter_id, user_id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_comments_chapter_parent_status
            ON comments(chapter_id, parent_id, status, id);
        CREATE INDEX IF NOT EXISTS idx_comment_likes_comment
            ON comment_likes(comment_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_chapter_reactions_chapter
            ON chapter_reactions(chapter_id, reaction_code);
        CREATE INDEX IF NOT EXISTS idx_complaints_comment_target
            ON complaints(target_type, target_id, status);
        """
    )


async def _ensure_v1110_assistant_schema(db: aiosqlite.Connection) -> None:
    """Помощник по книге v1.11.0: безопасный локальный кэш разбора опубликованных глав."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS book_assistant_cache (
            chapter_id INTEGER PRIMARY KEY,
            text_digest TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            characters_json TEXT NOT NULL DEFAULT '[]',
            terms_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_book_assistant_cache_digest
            ON book_assistant_cache(text_digest, updated_at);
        """
    )


async def _ensure_v1110_analytics_schema(db: aiosqlite.Connection) -> None:
    """Этап 7 v1.11.0: аналитика, достижения и ненавязчивые умные напоминания."""
    cur = await db.execute("PRAGMA table_info(user_preferences)")
    existing = {row[1] for row in await cur.fetchall()}
    migrations = {
        "notifications_reminders": "ALTER TABLE user_preferences ADD COLUMN notifications_reminders INTEGER NOT NULL DEFAULT 1",
        "notifications_achievements": "ALTER TABLE user_preferences ADD COLUMN notifications_achievements INTEGER NOT NULL DEFAULT 1",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await _execute_schema_ddl(db, sql)

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            achievement_code TEXT NOT NULL,
            progress_value INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            awarded_at TEXT NOT NULL,
            UNIQUE(user_id, achievement_code),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS smart_notification_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            notification_code TEXT NOT NULL,
            context_key TEXT NOT NULL DEFAULT '',
            last_sent_at TEXT NOT NULL,
            send_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(user_id, notification_code, context_key),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_user_achievements_user_awarded
            ON user_achievements(user_id, awarded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_smart_notifications_last_sent
            ON smart_notification_state(notification_code, last_sent_at);
        """
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
            SET title=?, updated_at=?
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
            SET age_limit=?, updated_at=?
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
        # Поле pricing_type оставлено только для обратной совместимости.
        # Цена всей книги и цены отдельных глав теперь независимы.
        clean["pricing_type"] = str(clean["pricing_type"])
    if "price_stars" in clean:
        clean["price_stars"] = max(0, min(100000, int(clean["price_stars"] or 0)))
        clean.pop("pricing_type", None)
    if "content_type" in clean:
        clean["content_type"] = str(clean["content_type"] or "book")
    if "reading_mode" in clean:
        clean["reading_mode"] = str(clean["reading_mode"] or "ltr")

    now = utc_now()
    fields = [f"{key}=?" for key in clean]
    values_list = list(clean.values())
    sensitive = {"content_type", "reading_mode"}
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
        changed = cur.rowcount > 0
        if changed and ("price_stars" in clean or "pricing_type" in values):
            await _sync_book_pricing_type_conn(db, int(book_id))
        await db.commit()
        return changed


async def get_book(book_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*, COALESCE(a.pen_name, b.source_author_name) AS pen_name, a.user_id AS author_user_id,
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
            SELECT b.*, COALESCE(a.pen_name, b.source_author_name) AS pen_name,
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


_RECOMMENDATION_EVENT_TYPES = {"impression", "open", "dismiss"}
_RECOMMENDATION_BOOKMARK_WEIGHTS = {
    "favorite": 8.0,
    "finished": 7.0,
    "reading": 5.0,
    "planned": 2.0,
    "dropped": -8.0,
}
_RECOMMENDATION_OPTION_WEIGHTS = {
    "genres": 1.15,
    "tropes": 0.95,
    "audience": 0.55,
    "language": 0.25,
    "content_type": 0.35,
}


async def record_recommendation_event(
    user_id: int,
    book_id: int,
    event_type: str,
    reason: str = "",
) -> bool:
    event_type = str(event_type or "").strip().lower()
    if event_type not in _RECOMMENDATION_EVENT_TYPES:
        raise ValueError("Неизвестное событие рекомендации")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT 1 FROM books WHERE id=? AND publication_status='published'",
            (int(book_id),),
        )
        if await cur.fetchone() is None:
            return False
        await db.execute(
            """
            INSERT INTO recommendation_events(
                user_id, book_id, event_type, reason, event_count, created_at, updated_at
            ) VALUES(?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(user_id, book_id, event_type) DO UPDATE SET
                reason=CASE WHEN excluded.reason!='' THEN excluded.reason ELSE recommendation_events.reason END,
                event_count=recommendation_events.event_count + 1,
                updated_at=excluded.updated_at
            """,
            (int(user_id), int(book_id), event_type, str(reason or "")[:240], now, now),
        )
        await db.commit()
    return True


async def record_recommendation_events(
    user_id: int,
    book_ids: list[int] | tuple[int, ...] | set[int],
    event_type: str,
) -> int:
    unique_ids: list[int] = []
    seen: set[int] = set()
    for raw_id in book_ids or []:
        try:
            book_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if book_id <= 0 or book_id in seen:
            continue
        seen.add(book_id)
        unique_ids.append(book_id)
    saved = 0
    for book_id in unique_ids[:50]:
        if await record_recommendation_event(user_id, book_id, event_type):
            saved += 1
    return saved


def _recommendation_catalog_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return {}


async def list_personalized_books(user_id: int, limit: int = 12) -> list[dict[str, Any]]:
    """Возвращает прозрачную персональную подборку без влияния на публичные топы.

    Сигналы пользователя используются только для его личной ленты: чтение,
    прослушивание, покупки, библиотека и оценки. Общая популярность имеет
    небольшой ограниченный вес, поэтому накрутка открытий не способна вытеснить
    действительно похожие произведения.
    """
    limit = max(1, min(30, int(limit or 12)))
    catalog = [_recommendation_catalog_dict(row) for row in await list_catalog_books(limit=300, include_drafts=False)]
    catalog = [item for item in catalog if int(item.get("id") or 0) > 0]
    if not catalog:
        return []

    source_scores: dict[int, float] = {}
    seen_books: set[int] = set()
    dismissed_books: set[int] = set()
    own_books: set[int] = set()
    source_titles: dict[int, str] = {}
    source_authors: dict[int, int] = {}
    author_preferences: dict[int, float] = {}

    def add_signal(book_id: Any, value: float, *, mark_seen: bool = True) -> None:
        try:
            normalized = int(book_id)
        except (TypeError, ValueError):
            return
        if normalized <= 0:
            return
        source_scores[normalized] = source_scores.get(normalized, 0.0) + float(value)
        if mark_seen:
            seen_books.add(normalized)

    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM author_profiles WHERE user_id=?",
            (int(user_id),),
        )
        author_profile = await cur.fetchone()
        if author_profile:
            cur = await db.execute("SELECT id FROM books WHERE author_id=?", (int(author_profile["id"]),))
            own_books = {int(row["id"]) for row in await cur.fetchall()}

        cur = await db.execute(
            "SELECT book_id, status FROM bookmarks WHERE user_id=?",
            (int(user_id),),
        )
        for row in await cur.fetchall():
            add_signal(row["book_id"], _RECOMMENDATION_BOOKMARK_WEIGHTS.get(str(row["status"]), 3.0))

        cur = await db.execute(
            """
            SELECT book_id, MAX(position_percent) AS max_percent
            FROM reading_progress WHERE user_id=? GROUP BY book_id
            """,
            (int(user_id),),
        )
        for row in await cur.fetchall():
            add_signal(row["book_id"], 2.0 + min(5.0, float(row["max_percent"] or 0) / 20.0))

        cur = await db.execute(
            """
            SELECT c.book_id, MAX(tp.position_seconds) AS position_seconds
            FROM tts_progress tp JOIN chapters c ON c.id=tp.chapter_id
            WHERE tp.user_id=? GROUP BY c.book_id
            """,
            (int(user_id),),
        )
        for row in await cur.fetchall():
            add_signal(row["book_id"], 3.5)

        cur = await db.execute(
            """
            SELECT ac.book_id, MAX(lp.position_seconds) AS position_seconds
            FROM listening_progress lp JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id
            WHERE lp.user_id=? GROUP BY ac.book_id
            """,
            (int(user_id),),
        )
        for row in await cur.fetchall():
            add_signal(row["book_id"], 4.5)

        cur = await db.execute(
            """
            SELECT gc.book_id, MAX(grp.page_number) AS page_number
            FROM graphic_reading_progress grp
            JOIN graphic_chapters gc ON gc.id=grp.graphic_chapter_id
            WHERE grp.user_id=? GROUP BY gc.book_id
            """,
            (int(user_id),),
        )
        for row in await cur.fetchall():
            add_signal(row["book_id"], 4.5)

        cur = await db.execute(
            """
            SELECT COALESCE(p.book_id, c.book_id, ac.book_id, gc.book_id, cp.book_id) AS resolved_book_id,
                   COUNT(*) AS purchases_count
            FROM purchases p
            LEFT JOIN chapters c ON c.id=p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
            LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
            LEFT JOIN chapter_packages cp ON cp.id=p.chapter_package_id
            WHERE p.user_id=? AND p.status='paid'
            GROUP BY resolved_book_id
            """,
            (int(user_id),),
        )
        for row in await cur.fetchall():
            add_signal(row["resolved_book_id"], 8.0 + min(4.0, float(row["purchases_count"] or 0)))

        cur = await db.execute(
            "SELECT book_id, rating FROM reviews WHERE user_id=? AND status='published'",
            (int(user_id),),
        )
        for row in await cur.fetchall():
            rating = max(1, min(5, int(row["rating"] or 3)))
            add_signal(row["book_id"], float((rating - 3) * 2), mark_seen=True)

        cur = await db.execute(
            "SELECT book_id FROM recommendation_events WHERE user_id=? AND event_type='dismiss'",
            (int(user_id),),
        )
        dismissed_books = {int(row["book_id"]) for row in await cur.fetchall()}

        all_source_ids = {book_id for book_id, score in source_scores.items() if score != 0}
        all_option_book_ids = all_source_ids | {int(item["id"]) for item in catalog}
        options_by_book: dict[int, list[tuple[str, str, str]]] = {}
        if all_option_book_ids:
            placeholders = ",".join("?" for _ in all_option_book_ids)
            cur = await db.execute(
                f"""
                SELECT book_id, option_group, option_code, option_label
                FROM book_option_values
                WHERE book_id IN ({placeholders})
                """,
                tuple(sorted(all_option_book_ids)),
            )
            for row in await cur.fetchall():
                options_by_book.setdefault(int(row["book_id"]), []).append(
                    (str(row["option_group"]), str(row["option_code"]), str(row["option_label"]))
                )

        if all_source_ids:
            placeholders = ",".join("?" for _ in all_source_ids)
            cur = await db.execute(
                f"SELECT id, title, author_id FROM books WHERE id IN ({placeholders})",
                tuple(sorted(all_source_ids)),
            )
            for row in await cur.fetchall():
                book_id = int(row["id"])
                source_titles[book_id] = str(row["title"] or "")
                if row["author_id"] is not None:
                    source_authors[book_id] = int(row["author_id"])

    preference_scores: dict[tuple[str, str], float] = {}
    for book_id, signal in source_scores.items():
        for group, code, _label in options_by_book.get(book_id, []):
            preference_scores[(group, code)] = preference_scores.get((group, code), 0.0) + signal
        author_id = source_authors.get(book_id)
        if author_id:
            author_preferences[author_id] = author_preferences.get(author_id, 0.0) + signal

    positive_profile = any(value > 0.5 for value in preference_scores.values()) or any(
        value > 0.5 for value in author_preferences.values()
    )
    newest_order = {int(item["id"]): index for index, item in enumerate(catalog)}
    scored: list[dict[str, Any]] = []

    for item in catalog:
        book_id = int(item.get("id") or 0)
        if book_id in seen_books or book_id in dismissed_books or book_id in own_books:
            continue

        score = 0.0
        matched: list[tuple[float, str, str, str]] = []
        candidate_options = options_by_book.get(book_id, [])
        candidate_codes = {(group, code) for group, code, _ in candidate_options}
        for group, code, label in candidate_options:
            preference = preference_scores.get((group, code), 0.0)
            if preference <= 0:
                continue
            contribution = min(12.0, preference) * _RECOMMENDATION_OPTION_WEIGHTS.get(group, 0.25)
            score += contribution
            matched.append((contribution, group, code, label))

        author_id = int(item.get("author_id") or 0)
        author_score = max(0.0, author_preferences.get(author_id, 0.0)) if author_id else 0.0
        if author_score:
            score += min(7.0, author_score * 0.7)

        rating = float(item.get("rating") or 0)
        reviews_count = int(item.get("reviews_count") or 0)
        purchase_count = int(item.get("purchase_count") or 0)
        if reviews_count >= 2 and rating > 3:
            score += min(1.8, (rating - 3.0) * 0.9)
        score += min(0.7, reviews_count * 0.035)
        score += min(0.7, purchase_count * 0.035)
        score += max(0.0, 0.8 - newest_order.get(book_id, 1000) * 0.01)
        if int(item.get("price_stars") or 0) <= 0 or int(item.get("free_chapters_count") or 0) > 0:
            score += 0.25

        source_match_id = 0
        source_match_count = 0
        for source_id, source_signal in source_scores.items():
            if source_signal <= 0:
                continue
            source_codes = {(group, code) for group, code, _ in options_by_book.get(source_id, [])}
            overlap = len(candidate_codes & source_codes)
            if overlap > source_match_count:
                source_match_count = overlap
                source_match_id = source_id

        matched.sort(reverse=True)
        if source_match_id and source_match_count >= 2 and source_titles.get(source_match_id):
            reason = f"Похоже на «{source_titles[source_match_id]}»"
            basis = "similar_book"
        elif author_score >= 5.0 and str(item.get("pen_name") or "").strip():
            reason = f"Новая история автора {str(item.get('pen_name')).strip()}"
            basis = "author"
        elif matched:
            reason = f"Ваш интерес: {matched[0][3]}"
            basis = matched[0][1]
        elif rating >= 4.2 and reviews_count >= 2:
            reason = "Высоко оценено читателями"
            basis = "quality"
        elif int(item.get("price_stars") or 0) <= 0 or int(item.get("free_chapters_count") or 0) > 0:
            reason = "Можно начать бесплатно"
            basis = "free"
        else:
            reason = "Новинка VoxLyra"
            basis = "new"

        if not positive_profile:
            # Для нового читателя подборка остаётся полезной, но честно называется стартовой.
            score = (
                min(2.0, max(0.0, rating - 3.0))
                + min(1.0, reviews_count * 0.05)
                + min(1.0, purchase_count * 0.05)
                + max(0.0, 1.5 - newest_order.get(book_id, 1000) * 0.02)
                + (0.35 if int(item.get("price_stars") or 0) <= 0 else 0.0)
            )
            if reason.startswith("Ваш интерес") or reason.startswith("Похоже") or reason.startswith("Новая история автора"):
                reason = "Подборка для первого знакомства"
                basis = "starter"

        decorated = dict(item)
        decorated["recommendation_score"] = round(score, 3)
        decorated["recommendation_reason"] = reason
        decorated["recommendation_basis"] = basis
        decorated["recommendation_personalized"] = bool(positive_profile)
        scored.append(decorated)

    scored.sort(
        key=lambda item: (
            float(item.get("recommendation_score") or 0),
            float(item.get("rating") or 0),
            int(item.get("id") or 0),
        ),
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    per_author: dict[int, int] = {}
    per_type: dict[str, int] = {}
    for item in scored:
        author_id = int(item.get("author_id") or 0)
        content_type = str(item.get("content_type") or "book")
        if author_id and per_author.get(author_id, 0) >= 2:
            continue
        if per_type.get(content_type, 0) >= max(4, limit // 2 + 1):
            continue
        selected.append(item)
        if author_id:
            per_author[author_id] = per_author.get(author_id, 0) + 1
        per_type[content_type] = per_type.get(content_type, 0) + 1
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        selected_ids = {int(item["id"]) for item in selected}
        for item in scored:
            if int(item["id"]) in selected_ids:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
    return selected


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


async def list_author_chapter_summaries(book_id: int) -> list[aiosqlite.Row]:
    """Возвращает список глав для кабинета автора без тяжёлого поля text.

    Полный текст загружается отдельным запросом только при открытии конкретной
    главы. Это не даёт книгам на сотни и тысячи глав переполнять ответ Mini App.
    """
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, book_id, number, title, is_free, price_stars,
                   saved_price_stars, status, created_at, updated_at
            FROM chapters
            WHERE book_id = ? AND status != 'deleted'
            ORDER BY number ASC
            """,
            (int(book_id),),
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
        chapter_id = int(cur.lastrowid)
        await _sync_book_pricing_type_conn(db, int(book_id))
        await db.commit()
        return chapter_id


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
        await _sync_book_pricing_type_conn(db, int(book_id))
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
            SELECT DISTINCT u.id, u.telegram_id, u.username, u.full_name,
                   (
                       SELECT c.number
                       FROM reading_progress rp
                       JOIN chapters c ON c.id=rp.chapter_id
                       WHERE rp.user_id=u.id AND rp.book_id=?
                       ORDER BY rp.updated_at DESC, c.number DESC
                       LIMIT 1
                   ) AS last_chapter_number,
                   (
                       SELECT rp.position_percent
                       FROM reading_progress rp
                       WHERE rp.user_id=u.id AND rp.book_id=?
                       ORDER BY rp.updated_at DESC
                       LIMIT 1
                   ) AS last_position_percent,
                   (SELECT bm.status FROM bookmarks bm WHERE bm.user_id=u.id AND bm.book_id=? LIMIT 1) AS bookmark_status
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
            (book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, book_id, int(limit)),
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


COMMENT_REACTION_CODES = {"fire", "heart", "cry", "laugh", "shock", "epic"}


async def add_comment(
    user_id: int,
    chapter_id: int,
    text: str,
    *,
    parent_id: int | None = None,
    is_spoiler: bool = False,
) -> int:
    """Publish a chapter comment or a one-level reply.

    Replies are always attached to the root comment, so the interface cannot turn
    into an unreadable chain of deeply nested messages.
    """
    now = utc_now()
    clean_text = str(text or "").strip()[:2000]
    if len(clean_text) < 2:
        raise ValueError("Comment is too short")
    async with connect() as db:
        cur = await db.execute("SELECT book_id FROM chapters WHERE id=?", (int(chapter_id),))
        chapter = await cur.fetchone()
        if not chapter:
            raise ValueError("Chapter not found")

        root_parent_id: int | None = None
        if parent_id:
            cur = await db.execute(
                "SELECT id, chapter_id, parent_id, status FROM comments WHERE id=?",
                (int(parent_id),),
            )
            parent = await cur.fetchone()
            if not parent or int(parent["chapter_id"]) != int(chapter_id) or parent["status"] != "published":
                raise ValueError("Reply target not found")
            root_parent_id = int(parent["parent_id"] or parent["id"])

        cur = await db.execute(
            """
            INSERT INTO comments(
                user_id, book_id, chapter_id, text, status, parent_id, is_spoiler, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, 'published', ?, ?, ?, ?)
            """,
            (
                int(user_id),
                int(chapter["book_id"]),
                int(chapter_id),
                clean_text,
                root_parent_id,
                1 if is_spoiler else 0,
                now,
                now,
            ),
        )
        await db.commit()
        return int(cur.lastrowid)


async def list_comments_for_chapter(
    chapter_id: int,
    limit: int = 50,
    viewer_user_id: int | None = None,
) -> list[aiosqlite.Row]:
    viewer_id = int(viewer_user_id or 0)
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, u.username, u.full_name,
                   (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id=c.id) AS like_count,
                   CASE WHEN ? > 0 AND EXISTS(
                       SELECT 1 FROM comment_likes mine
                       WHERE mine.comment_id=c.id AND mine.user_id=?
                   ) THEN 1 ELSE 0 END AS viewer_liked,
                   (SELECT COUNT(*) FROM complaints cp
                    WHERE cp.target_type='comment' AND cp.target_id=CAST(c.id AS TEXT)
                      AND cp.status IN ('new', 'pending')) AS report_count
            FROM comments c
            JOIN users u ON u.id = c.user_id
            LEFT JOIN comments parent ON parent.id = c.parent_id
            WHERE c.chapter_id=? AND c.status='published'
              AND (c.parent_id IS NULL OR parent.status='published')
            ORDER BY COALESCE(c.parent_id, c.id) DESC,
                     CASE WHEN c.parent_id IS NULL THEN 0 ELSE 1 END,
                     c.id ASC
            LIMIT ?
            """,
            (viewer_id, viewer_id, int(chapter_id), max(1, min(200, int(limit)))),
        )
        return await cur.fetchall()


async def get_public_comment(comment_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.*, u.username, u.full_name
            FROM comments c
            JOIN users u ON u.id=c.user_id
            WHERE c.id=? AND c.status='published'
            """,
            (int(comment_id),),
        )
        return await cur.fetchone()


async def toggle_comment_like(user_id: int, comment_id: int) -> dict[str, Any]:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT id FROM comments WHERE id=? AND status='published'", (int(comment_id),))
        if not await cur.fetchone():
            raise ValueError("Comment not found")
        cur = await db.execute(
            "SELECT id FROM comment_likes WHERE comment_id=? AND user_id=?",
            (int(comment_id), int(user_id)),
        )
        existing = await cur.fetchone()
        liked = existing is None
        if liked:
            await db.execute(
                "INSERT INTO comment_likes(comment_id, user_id, created_at) VALUES(?, ?, ?)",
                (int(comment_id), int(user_id), now),
            )
        else:
            await db.execute("DELETE FROM comment_likes WHERE id=?", (int(existing["id"]),))
        cur = await db.execute("SELECT COUNT(*) AS cnt FROM comment_likes WHERE comment_id=?", (int(comment_id),))
        count_row = await cur.fetchone()
        await db.commit()
        return {"liked": liked, "like_count": int(count_row["cnt"] or 0)}


async def list_chapter_reactions(chapter_id: int, viewer_user_id: int | None = None) -> dict[str, Any]:
    counts = {code: 0 for code in sorted(COMMENT_REACTION_CODES)}
    selected: str | None = None
    async with connect() as db:
        cur = await db.execute(
            "SELECT reaction_code, COUNT(*) AS cnt FROM chapter_reactions WHERE chapter_id=? GROUP BY reaction_code",
            (int(chapter_id),),
        )
        for row in await cur.fetchall():
            code = str(row["reaction_code"] or "")
            if code in counts:
                counts[code] = int(row["cnt"] or 0)
        if viewer_user_id:
            cur = await db.execute(
                "SELECT reaction_code FROM chapter_reactions WHERE chapter_id=? AND user_id=?",
                (int(chapter_id), int(viewer_user_id)),
            )
            row = await cur.fetchone()
            if row and str(row["reaction_code"] or "") in COMMENT_REACTION_CODES:
                selected = str(row["reaction_code"])
    return {"counts": counts, "selected": selected}


async def set_chapter_reaction(user_id: int, chapter_id: int, reaction_code: str) -> dict[str, Any]:
    code = str(reaction_code or "").strip().lower()
    if code not in COMMENT_REACTION_CODES:
        raise ValueError("Unknown reaction")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT reaction_code FROM chapter_reactions WHERE chapter_id=? AND user_id=?", (int(chapter_id), int(user_id)))
        existing = await cur.fetchone()
        if existing and str(existing["reaction_code"]) == code:
            await db.execute("DELETE FROM chapter_reactions WHERE chapter_id=? AND user_id=?", (int(chapter_id), int(user_id)))
        else:
            await db.execute(
                """
                INSERT INTO chapter_reactions(chapter_id, user_id, reaction_code, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(chapter_id, user_id) DO UPDATE SET
                    reaction_code=excluded.reaction_code, updated_at=excluded.updated_at
                """,
                (int(chapter_id), int(user_id), code, now, now),
            )
        await db.commit()
    return await list_chapter_reactions(int(chapter_id), int(user_id))


async def report_comment(user_id: int, comment_id: int, reason: str) -> dict[str, Any]:
    clean_reason = str(reason or "").strip()[:1200]
    if len(clean_reason) < 3:
        raise ValueError("Report reason is too short")
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id, user_id, chapter_id FROM comments WHERE id=? AND status='published'",
            (int(comment_id),),
        )
        comment = await cur.fetchone()
        if not comment:
            raise ValueError("Comment not found")
        if int(comment["user_id"]) == int(user_id):
            raise ValueError("Cannot report own comment")
        cur = await db.execute(
            """
            SELECT id FROM complaints
            WHERE user_id=? AND target_type='comment' AND target_id=?
              AND status IN ('new', 'pending')
            ORDER BY id DESC LIMIT 1
            """,
            (int(user_id), str(int(comment_id))),
        )
        existing = await cur.fetchone()
        if existing:
            return {"complaint_id": int(existing["id"]), "created": False}
        cur = await db.execute(
            """
            INSERT INTO complaints(user_id, target_type, target_id, reason, status, created_at, updated_at)
            VALUES(?, 'comment', ?, ?, 'new', ?, ?)
            """,
            (int(user_id), str(int(comment_id)), clean_reason, now, now),
        )
        await db.commit()
        return {"complaint_id": int(cur.lastrowid), "created": True}


async def resolve_comment_complaints(comment_id: int, handled_by_user_id: int | None = None) -> int:
    """Close all active reports after the comment has been reviewed or hidden."""
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE complaints
            SET status='closed', handled_by_user_id=?, updated_at=?
            WHERE target_type='comment' AND target_id=? AND status IN ('new', 'pending')
            """,
            (handled_by_user_id, now, str(int(comment_id))),
        )
        await db.commit()
        return int(cur.rowcount or 0)


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
                   b.title AS book_title, ch.title AS chapter_title,
                   (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id=c.id) AS like_count,
                   (SELECT COUNT(*) FROM complaints cp
                    WHERE cp.target_type='comment' AND cp.target_id=CAST(c.id AS TEXT)
                      AND cp.status IN ('new', 'pending')) AS report_count
            FROM comments c
            JOIN users u ON u.id = c.user_id
            JOIN books b ON b.id = c.book_id
            JOIN chapters ch ON ch.id = c.chapter_id
            WHERE c.status='published'
            ORDER BY report_count DESC, c.id DESC
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
                   b.title AS book_title, ch.title AS chapter_title,
                   (SELECT COUNT(*) FROM comment_likes cl WHERE cl.comment_id=c.id) AS like_count,
                   (SELECT COUNT(*) FROM complaints cp
                    WHERE cp.target_type='comment' AND cp.target_id=CAST(c.id AS TEXT)
                      AND cp.status IN ('new', 'pending')) AS report_count
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
                "premium_total": 0, "premium_held": 0, "premium_available": 0,
                "premium_requested": 0, "premium_paid": 0,
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
                COALESCE(SUM(CASE WHEN status!='refunded' THEN net_minor ELSE 0 END), 0) AS net_minor,
                COALESCE(SUM(CASE WHEN source_type='premium_pool' AND status!='refunded' THEN net_stars ELSE 0 END), 0) AS premium_total,
                COALESCE(SUM(CASE WHEN source_type='premium_pool' AND status='held' THEN net_stars ELSE 0 END), 0) AS premium_held,
                COALESCE(SUM(CASE WHEN source_type='premium_pool' AND status='available' THEN net_stars ELSE 0 END), 0) AS premium_available,
                COALESCE(SUM(CASE WHEN source_type='premium_pool' AND status='payout_requested' THEN net_stars ELSE 0 END), 0) AS premium_requested,
                COALESCE(SUM(CASE WHEN source_type='premium_pool' AND status='paid' THEN net_stars ELSE 0 END), 0) AS premium_paid
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
    """Проверяет действующее согласие без повторных запросов после обычного обновления.

    Текстовый хэш хранится как доказательство принятой редакции, но изменение
    технических реквизитов, шаблона PDF или кода приложения само по себе не
    аннулирует согласие. Повторное подтверждение требуется только если для
    конкретного документа явно установлена настройка
    ``legal_reaccept_<код>_version`` со значением текущей версии.
    """
    code = str(doc_code)
    version = str(doc_version)
    # Старый callback-код ``authors`` равнозначен действующему author_license.
    equivalent_codes = [code]
    if code == "author_license":
        equivalent_codes.append("authors")
    elif code == "authors":
        equivalent_codes.append("author_license")
    placeholders = ",".join("?" for _ in equivalent_codes)
    async with connect() as db:
        cur = await db.execute(
            f"SELECT id FROM legal_acceptances "
            f"WHERE user_id=? AND doc_code IN ({placeholders}) "
            "AND doc_version=? AND withdrawn_at IS NULL LIMIT 1",
            (int(user_id), *equivalent_codes, version),
        )
        if await cur.fetchone() is not None:
            return True

        # Существенная редакция включается явно. Пока ключ пустой, любое ранее
        # сохранённое активное согласие по этому документу остаётся действующим.
        cur = await db.execute(
            "SELECT value FROM settings WHERE key=? LIMIT 1",
            (f"legal_reaccept_{code}_version",),
        )
        row = await cur.fetchone()
        forced_version = str(row["value"] or "").strip() if row else ""
        if forced_version and forced_version == version:
            return False

        cur = await db.execute(
            f"SELECT id FROM legal_acceptances "
            f"WHERE user_id=? AND doc_code IN ({placeholders}) "
            "AND withdrawn_at IS NULL ORDER BY accepted_at DESC LIMIT 1",
            (int(user_id), *equivalent_codes),
        )
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
    "notifications_reminders": "1",
    "notifications_achievements": "1",
}


async def get_user_preferences(user_id: int) -> dict[str, str]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT theme, font_size, notifications, notifications_chapters,
                   notifications_audio, notifications_discounts,
                   notifications_reminders, notifications_achievements
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
                notifications_audio, notifications_discounts, notifications_reminders,
                notifications_achievements, updated_at
            )
            VALUES(?, 'system', 'normal', 1, 1, 1, 1, 1, 1, ?)
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
        "legal_terms_version": "2026-07-13",
        "legal_personal_data_version": "2026-07-13",
        "legal_author_license_version": "2026-07-13",
        "legal_author_data_version": "2026-07-13",
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
                   b.price_stars AS book_price_stars, b.pricing_type,
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


# --- v1.11.0: независимая цена всей книги и отдельных текстовых глав ---
async def _sync_book_pricing_type_conn(db: aiosqlite.Connection, book_id: int) -> str:
    cur = await db.execute(
        "SELECT price_stars FROM books WHERE id=? AND publication_status!='deleted'",
        (int(book_id),),
    )
    book = await cur.fetchone()
    if not book:
        return "free"
    cur = await db.execute(
        "SELECT COUNT(*) AS paid_count FROM chapters WHERE book_id=? AND status!='deleted' AND is_free=0 AND price_stars>0",
        (int(book_id),),
    )
    row = await cur.fetchone()
    has_paid_chapters = bool(row and int(row["paid_count"] or 0) > 0)
    mode = "whole_book" if int(book["price_stars"] or 0) > 0 else ("chapters" if has_paid_chapters else "free")
    await db.execute(
        "UPDATE books SET pricing_type=?, updated_at=? WHERE id=?",
        (mode, utc_now(), int(book_id)),
    )
    return mode


async def sync_book_pricing_type(book_id: int) -> str:
    async with connect() as db:
        mode = await _sync_book_pricing_type_conn(db, int(book_id))
        await db.commit()
        return mode


async def update_book_price(book_id: int, author_user_id: int, pricing_type: str, price_stars: int) -> bool:
    """Меняет только предложение "вся книга"; цены глав сохраняются."""
    price = max(0, min(100000, int(price_stars or 0)))
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET price_stars=?, updated_at=?
            WHERE id=? AND publication_status!='deleted'
              AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (price, now, int(book_id), int(author_user_id)),
        )
        changed = cur.rowcount > 0
        if changed:
            await _sync_book_pricing_type_conn(db, int(book_id))
        await db.commit()
        return changed


async def update_chapter_price(chapter_id: int, author_user_id: int, is_free: bool, price_stars: int) -> bool:
    """Меняет цену одной главы; цену всей книги не затрагивает."""
    price = 0 if bool(is_free) else max(1, min(100000, int(price_stars or 0)))
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.book_id FROM chapters c
            JOIN books b ON b.id=c.book_id
            JOIN author_profiles a ON a.id=b.author_id
            WHERE c.id=? AND c.status!='deleted' AND a.user_id=?
            """,
            (int(chapter_id), int(author_user_id)),
        )
        row = await cur.fetchone()
        if not row:
            return False
        book_id = int(row["book_id"])
        cur = await db.execute(
            "UPDATE chapters SET is_free=?, price_stars=?, updated_at=? WHERE id=? AND status!='deleted'",
            (1 if price == 0 else 0, price, now, int(chapter_id)),
        )
        changed = cur.rowcount > 0
        if changed:
            await _sync_book_pricing_type_conn(db, book_id)
        await db.commit()
        return changed


async def update_chapter_price_range(
    book_id: int, author_user_id: int, start_number: int, end_number: int, price_stars: int
) -> dict[str, int | bool]:
    """Назначает цену одной главе или диапазону по номеру включительно.

    price_stars=0 делает главы бесплатными. Цена всей книги не меняется.
    """
    start = max(1, int(start_number))
    end = max(1, int(end_number))
    if start > end:
        start, end = end, start
    if end - start > 100000:
        raise ValueError("Диапазон слишком большой")
    price = max(0, min(100000, int(price_stars or 0)))
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.id FROM books b JOIN author_profiles a ON a.id=b.author_id
            WHERE b.id=? AND b.publication_status!='deleted' AND a.user_id=?
            """,
            (int(book_id), int(author_user_id)),
        )
        if not await cur.fetchone():
            return {"ok": False, "updated": 0, "start_number": start, "end_number": end, "price_stars": price}
        cur = await db.execute(
            """
            UPDATE chapters SET is_free=?, price_stars=?, updated_at=?
            WHERE book_id=? AND status!='deleted' AND number BETWEEN ? AND ?
            """,
            (1 if price == 0 else 0, price, now, int(book_id), start, end),
        )
        updated = int(cur.rowcount or 0)
        if updated:
            await _sync_book_pricing_type_conn(db, int(book_id))
            await db.execute(
                "UPDATE books SET publication_status=CASE WHEN publication_status='published' THEN 'review' ELSE publication_status END, updated_at=? WHERE id=?",
                (now, int(book_id)),
            )
        await db.commit()
        return {"ok": bool(updated), "updated": updated, "start_number": start, "end_number": end, "price_stars": price}

# --- v1.11.0: строгие режимы продажи книги и текстовых глав ---
# free       — вся книга и все текстовые главы бесплатны;
# whole_book — продаётся только вся книга, отдельные главы не продаются;
# chapters   — вся книга продаётся целиком, а выбранные главы можно купить отдельно.
# premium    — прямой покупки нет; закрытые главы доступны по активной подписке Premium.


def _normalize_text_pricing_mode(price_stars: int, requested_mode: str | None) -> str:
    requested = str(requested_mode or "").strip().lower()
    if requested == "premium":
        return "premium"
    price = max(0, int(price_stars or 0))
    if price <= 0:
        return "free"
    return "chapters" if requested == "chapters" else "whole_book"


async def _sync_book_pricing_type_conn(db: aiosqlite.Connection, book_id: int) -> str:
    """Нормализует режим, не выводя его из случайных цен глав.

    Режим выбирает автор явно. Нулевая цена всегда означает полностью
    бесплатную книгу; при положительной цене допустимы только whole_book и chapters.
    """
    cur = await db.execute(
        "SELECT price_stars, pricing_type FROM books WHERE id=? AND publication_status!='deleted'",
        (int(book_id),),
    )
    book = await cur.fetchone()
    if not book:
        return "free"
    mode = _normalize_text_pricing_mode(int(book["price_stars"] or 0), str(book["pricing_type"] or ""))
    if mode != str(book["pricing_type"] or ""):
        await db.execute(
            "UPDATE books SET pricing_type=?, updated_at=? WHERE id=?",
            (mode, utc_now(), int(book_id)),
        )
    return mode


async def sync_book_pricing_type(book_id: int) -> str:
    async with connect() as db:
        mode = await _sync_book_pricing_type_conn(db, int(book_id))
        await db.commit()
        return mode


async def get_book_pricing_state(book_id: int) -> dict[str, Any]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT id, price_stars, pricing_type FROM books WHERE id=? AND publication_status!='deleted'",
            (int(book_id),),
        )
        book = await cur.fetchone()
        if not book:
            return {"exists": False, "mode": "free", "price_stars": 0, "saved_prices_count": 0}
        mode = _normalize_text_pricing_mode(int(book["price_stars"] or 0), str(book["pricing_type"] or ""))
        cur = await db.execute(
            "SELECT COUNT(*) AS count FROM chapters WHERE book_id=? AND status!='deleted' "
            "AND COALESCE(saved_price_stars, 0)>0",
            (int(book_id),),
        )
        row = await cur.fetchone()
        return {
            "exists": True,
            "mode": mode,
            "price_stars": int(book["price_stars"] or 0),
            "saved_prices_count": int(row["count"] or 0) if row else 0,
        }


async def update_book_price(
    book_id: int,
    author_user_id: int,
    pricing_type: str,
    price_stars: int,
    *,
    restore_saved_prices: bool = False,
) -> bool:
    """Меняет режим доступа к текстовой книге без смешивания разных оплат.

    * free — вся книга и все главы бесплатны;
    * whole_book — покупка только всей книги;
    * chapters — покупка всей книги или выбранных глав;
    * premium — прямой покупки нет, закрытые главы читает активный подписчик.
    """
    requested = str(pricing_type or "").strip().lower()
    mode = _normalize_text_pricing_mode(price_stars, requested)
    price = 0 if mode in {"free", "premium"} else max(1, min(100000, int(price_stars or 0)))
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.id, b.price_stars, b.pricing_type
            FROM books b JOIN author_profiles a ON a.id=b.author_id
            WHERE b.id=? AND b.publication_status!='deleted' AND a.user_id=?
            """,
            (int(book_id), int(author_user_id)),
        )
        old = await cur.fetchone()
        if not old:
            return False
        old_mode = _normalize_text_pricing_mode(int(old["price_stars"] or 0), str(old["pricing_type"] or ""))

        if mode in {"free", "whole_book", "premium"}:
            await db.execute(
                """
                UPDATE chapters
                SET saved_is_free=CASE
                        WHEN is_free=0 OR price_stars>0 THEN is_free
                        ELSE saved_is_free
                    END,
                    saved_price_stars=CASE
                        WHEN price_stars>0 THEN price_stars
                        ELSE saved_price_stars
                    END
                WHERE book_id=? AND status!='deleted'
                """,
                (int(book_id),),
            )

        if mode == "free":
            await db.execute(
                "UPDATE chapters SET is_free=1, price_stars=0, updated_at=? WHERE book_id=? AND status!='deleted'",
                (now, int(book_id)),
            )
            await db.execute(
                "UPDATE chapter_packages SET is_active=0, updated_at=? WHERE book_id=? AND content_scope IN ('text','all')",
                (now, int(book_id)),
            )
        elif mode == "whole_book":
            await db.execute(
                "UPDATE chapters SET price_stars=0, updated_at=? WHERE book_id=? AND status!='deleted'",
                (now, int(book_id)),
            )
            await db.execute(
                "UPDATE chapter_packages SET is_active=0, updated_at=? WHERE book_id=? AND content_scope IN ('text','all')",
                (now, int(book_id)),
            )
        elif mode == "premium":
            # При переходе с полностью бесплатной книги оставляем первые три главы
            # ознакомительными, остальные делаем доступными по подписке. В других
            # режимах сохраняем уже выбранные бесплатные ознакомительные главы.
            if old_mode == "free":
                await db.execute(
                    "UPDATE chapters SET is_free=CASE WHEN number<=3 THEN 1 ELSE 0 END, price_stars=0, updated_at=? "
                    "WHERE book_id=? AND status!='deleted'",
                    (now, int(book_id)),
                )
            else:
                await db.execute(
                    "UPDATE chapters SET price_stars=0, updated_at=? WHERE book_id=? AND status!='deleted'",
                    (now, int(book_id)),
                )
            await db.execute(
                "UPDATE chapter_packages SET is_active=0, updated_at=? WHERE book_id=? AND content_scope IN ('text','all')",
                (now, int(book_id)),
            )
        elif restore_saved_prices:
            await db.execute(
                """
                UPDATE chapters
                SET is_free=CASE
                        WHEN COALESCE(saved_price_stars, 0)>0 THEN 0
                        WHEN saved_is_free IS NOT NULL THEN saved_is_free
                        ELSE is_free
                    END,
                    price_stars=CASE
                        WHEN COALESCE(saved_price_stars, 0)>0 THEN saved_price_stars
                        ELSE 0
                    END,
                    updated_at=?
                WHERE book_id=? AND status!='deleted'
                  AND (saved_is_free IS NOT NULL OR saved_price_stars IS NOT NULL)
                """,
                (now, int(book_id)),
            )

        cur = await db.execute(
            """
            UPDATE books
            SET pricing_type=?, price_stars=?,
                publication_status=CASE WHEN publication_status='published' THEN 'review' ELSE publication_status END,
                updated_at=?
            WHERE id=?
            """,
            (mode, price, now, int(book_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def restore_saved_chapter_prices(book_id: int, author_user_id: int) -> dict[str, Any]:
    """Явно восстанавливает сохранённые цены только в режиме продажи по главам."""
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.pricing_type, b.price_stars
            FROM books b JOIN author_profiles a ON a.id=b.author_id
            WHERE b.id=? AND b.publication_status!='deleted' AND a.user_id=?
            """,
            (int(book_id), int(author_user_id)),
        )
        book = await cur.fetchone()
        if not book:
            return {"ok": False, "updated": 0, "reason": "not_found"}
        mode = _normalize_text_pricing_mode(int(book["price_stars"] or 0), str(book["pricing_type"] or ""))
        if mode != "chapters":
            return {"ok": False, "updated": 0, "reason": "chapter_sales_disabled"}
        cur = await db.execute(
            """
            UPDATE chapters
            SET is_free=CASE
                    WHEN COALESCE(saved_price_stars, 0)>0 THEN 0
                    WHEN saved_is_free IS NOT NULL THEN saved_is_free
                    ELSE is_free
                END,
                price_stars=CASE
                    WHEN COALESCE(saved_price_stars, 0)>0 THEN saved_price_stars
                    ELSE 0
                END,
                updated_at=?
            WHERE book_id=? AND status!='deleted'
              AND (saved_is_free IS NOT NULL OR saved_price_stars IS NOT NULL)
            """,
            (now, int(book_id)),
        )
        updated = int(cur.rowcount or 0)
        if updated:
            await db.execute(
                "UPDATE books SET publication_status=CASE WHEN publication_status='published' THEN 'review' ELSE publication_status END, updated_at=? WHERE id=?",
                (now, int(book_id)),
            )
        await db.commit()
        return {"ok": True, "updated": updated, "reason": ""}


async def update_chapter_access_range(
    book_id: int,
    author_user_id: int,
    start_number: int,
    end_number: int,
    access_mode: str,
    price_stars: int = 0,
) -> dict[str, Any]:
    """Меняет доступ одной главы или диапазона в рамках выбранного режима книги."""
    start = max(1, int(start_number))
    end = max(1, int(end_number))
    if start > end:
        start, end = end, start
    if end - start > 100000:
        raise ValueError("Диапазон слишком большой")
    access = str(access_mode or "").strip().lower()
    if access not in {"free", "book", "chapter", "premium"}:
        raise ValueError("Неизвестный режим доступа")
    price = max(0, min(100000, int(price_stars or 0)))
    now = utc_now()

    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.id, b.price_stars, b.pricing_type
            FROM books b JOIN author_profiles a ON a.id=b.author_id
            WHERE b.id=? AND b.publication_status!='deleted' AND a.user_id=?
            """,
            (int(book_id), int(author_user_id)),
        )
        book = await cur.fetchone()
        if not book:
            return {"ok": False, "updated": 0, "reason": "not_found", "start_number": start, "end_number": end}
        mode = _normalize_text_pricing_mode(int(book["price_stars"] or 0), str(book["pricing_type"] or ""))
        if mode == "free":
            return {"ok": False, "updated": 0, "reason": "book_is_free", "start_number": start, "end_number": end}
        if mode == "premium" and access not in {"free", "premium"}:
            return {"ok": False, "updated": 0, "reason": "premium_mode_only", "start_number": start, "end_number": end}
        if mode != "premium" and access == "premium":
            return {"ok": False, "updated": 0, "reason": "premium_mode_required", "start_number": start, "end_number": end}
        if access == "chapter" and mode != "chapters":
            return {"ok": False, "updated": 0, "reason": "chapter_sales_disabled", "start_number": start, "end_number": end}
        if access == "chapter" and price <= 0:
            return {"ok": False, "updated": 0, "reason": "price_required", "start_number": start, "end_number": end}

        is_free = 1 if access == "free" else 0
        active_price = price if access == "chapter" else 0
        cur = await db.execute(
            """
            UPDATE chapters
            SET saved_is_free=CASE
                    WHEN ?='chapter' THEN 0
                    WHEN ?='free' AND (price_stars>0 OR is_free=0) THEN COALESCE(saved_is_free, is_free)
                    ELSE saved_is_free
                END,
                saved_price_stars=CASE
                    WHEN ?='chapter' THEN ?
                    WHEN price_stars>0 THEN price_stars
                    ELSE saved_price_stars
                END,
                is_free=?, price_stars=?, updated_at=?
            WHERE book_id=? AND status!='deleted' AND number BETWEEN ? AND ?
            """,
            (access, access, access, active_price, is_free, active_price, now, int(book_id), start, end),
        )
        updated = int(cur.rowcount or 0)
        if updated:
            await db.execute(
                "UPDATE books SET publication_status=CASE WHEN publication_status='published' THEN 'review' ELSE publication_status END, updated_at=? WHERE id=?",
                (now, int(book_id)),
            )
        await db.commit()
        return {
            "ok": bool(updated),
            "updated": updated,
            "reason": "" if updated else "chapters_not_found",
            "mode": mode,
            "access_mode": access,
            "price_stars": active_price,
            "start_number": start,
            "end_number": end,
        }


async def update_chapter_price(chapter_id: int, author_user_id: int, is_free: bool, price_stars: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT c.book_id, c.number
            FROM chapters c JOIN books b ON b.id=c.book_id JOIN author_profiles a ON a.id=b.author_id
            WHERE c.id=? AND c.status!='deleted' AND a.user_id=?
            """,
            (int(chapter_id), int(author_user_id)),
        )
        row = await cur.fetchone()
    if not row:
        return False
    access = "free" if bool(is_free) or int(price_stars or 0) <= 0 else "chapter"
    result = await update_chapter_access_range(
        int(row["book_id"]), int(author_user_id), int(row["number"]), int(row["number"]), access, int(price_stars or 0)
    )
    return bool(result.get("updated"))


async def update_chapter_price_range(
    book_id: int, author_user_id: int, start_number: int, end_number: int, price_stars: int
) -> dict[str, Any]:
    """Совместимый интерфейс: 0 делает главы бесплатными, цена >0 продаёт отдельно."""
    price = max(0, min(100000, int(price_stars or 0)))
    return await update_chapter_access_range(
        int(book_id), int(author_user_id), int(start_number), int(end_number),
        "free" if price <= 0 else "chapter", price,
    )


async def add_manual_chapter(book_id: int, title: str, text: str, is_free: bool = True, price_stars: int = 0) -> int:
    """Добавляет главу, принудительно соблюдая выбранный режим продажи книги."""
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT price_stars, pricing_type FROM books WHERE id=? AND publication_status!='deleted'",
            (int(book_id),),
        )
        book = await cur.fetchone()
        if not book:
            raise ValueError("Book not found")
        mode = _normalize_text_pricing_mode(int(book["price_stars"] or 0), str(book["pricing_type"] or ""))
        requested_price = max(0, min(100000, int(price_stars or 0)))
        if mode == "free":
            chapter_free, chapter_price = 1, 0
        elif mode == "whole_book":
            chapter_free, chapter_price = (1 if bool(is_free) else 0), 0
        elif mode == "premium":
            # В Premium нулевая цена не означает бесплатную главу: доступ
            # определяется флагом is_free. Так новые главы автора могут сразу
            # публиковаться для подписчиков, не превращаясь в бесплатные.
            chapter_free, chapter_price = (1 if bool(is_free) else 0), 0
        else:
            chapter_free = 1 if bool(is_free) or requested_price <= 0 else 0
            chapter_price = 0 if chapter_free else requested_price

        cur = await db.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS next_number FROM chapters WHERE book_id=? AND status!='deleted'",
            (int(book_id),),
        )
        row = await cur.fetchone()
        number = int(row["next_number"] if row else 1)
        cur = await db.execute(
            """
            INSERT INTO chapters(book_id, number, title, text, is_free, price_stars,
                                 saved_is_free, saved_price_stars, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                int(book_id), number, str(title)[:160], str(text), chapter_free, chapter_price,
                chapter_free if chapter_price > 0 else None,
                chapter_price if chapter_price > 0 else None,
                now, now,
            ),
        )
        chapter_id = int(cur.lastrowid)
        await db.commit()
        return chapter_id


async def upsert_imported_chapters(
    book_id: int,
    chapters: list[Any],
    first_free: int = 3,
    default_price_stars: int = 0,
    *,
    return_published_ids: bool = False,
) -> int | dict[str, Any]:
    """Импортирует главы с учётом режима книги.

    Бесплатная книга игнорирует ценовые параметры и открывает всё. У платной книги
    первые first_free глав являются ознакомительными, остальные доступны по книге;
    в режиме chapters ненулевая default_price_stars может сразу включить продажу отдельно.
    """
    now = utc_now()
    saved = 0
    published_ids: list[int] = []
    async with connect() as db:
        cur = await db.execute(
            "SELECT publication_status, price_stars, pricing_type FROM books WHERE id=?",
            (int(book_id),),
        )
        book = await cur.fetchone()
        if not book:
            raise ValueError("Book not found")
        target_status = "published" if book["publication_status"] == "published" else "draft"
        mode = _normalize_text_pricing_mode(int(book["price_stars"] or 0), str(book["pricing_type"] or ""))
        preview_count = max(0, int(first_free or 0))
        default_price = max(0, min(100000, int(default_price_stars or 0)))
        for chapter in chapters:
            if isinstance(chapter, dict):
                number = int(chapter["number"]); title = str(chapter["title"])[:160]; text = str(chapter["text"])
            else:
                number = int(chapter.number); title = str(chapter.title)[:160]; text = str(chapter.text)

            if mode == "free":
                chapter_free, chapter_price = 1, 0
            else:
                chapter_free = 1 if number <= preview_count else 0
                chapter_price = default_price if mode == "chapters" and not chapter_free and default_price > 0 else 0

            cur = await db.execute("SELECT id FROM chapters WHERE book_id=? AND number=?", (int(book_id), number))
            existing = await cur.fetchone()
            await db.execute(
                """
                INSERT INTO chapters(book_id, number, title, text, is_free, price_stars,
                                     saved_is_free, saved_price_stars, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(book_id, number) DO UPDATE SET
                    title=excluded.title,
                    text=excluded.text,
                    is_free=excluded.is_free,
                    price_stars=excluded.price_stars,
                    saved_is_free=CASE
                        WHEN chapters.price_stars>0 THEN chapters.is_free
                        ELSE chapters.saved_is_free
                    END,
                    saved_price_stars=CASE
                        WHEN chapters.price_stars>0 THEN chapters.price_stars
                        ELSE chapters.saved_price_stars
                    END,
                    status=CASE WHEN chapters.status='published' THEN 'published' ELSE excluded.status END,
                    updated_at=excluded.updated_at
                """,
                (
                    int(book_id), number, title, text, chapter_free, chapter_price,
                    chapter_free if chapter_price > 0 else None,
                    chapter_price if chapter_price > 0 else None,
                    target_status, now, now,
                ),
            )
            if existing is None and target_status == "published":
                cur = await db.execute("SELECT id FROM chapters WHERE book_id=? AND number=?", (int(book_id), number))
                inserted = await cur.fetchone()
                if inserted:
                    published_ids.append(int(inserted["id"]))
            saved += 1
        await db.commit()
    if return_published_ids:
        return {"saved": saved, "published_ids": published_ids}
    return saved


async def get_book_assistant_cache(chapter_id: int, digest: str) -> dict[str, Any] | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT chapter_id, text_digest, summary, characters_json, terms_json, updated_at
            FROM book_assistant_cache
            WHERE chapter_id=? AND text_digest=?
            """,
            (int(chapter_id), str(digest)),
        )
        row = await cur.fetchone()
        if not row:
            return None
        try:
            characters = json.loads(str(row["characters_json"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            characters = []
        try:
            terms = json.loads(str(row["terms_json"] or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            terms = []
        return {
            "chapter_id": int(row["chapter_id"]),
            "digest": str(row["text_digest"]),
            "summary": str(row["summary"] or ""),
            "characters": characters if isinstance(characters, list) else [],
            "terms": terms if isinstance(terms, list) else [],
            "updated_at": str(row["updated_at"] or ""),
        }


async def save_book_assistant_cache(
    chapter_id: int,
    digest: str,
    summary: str,
    characters: list[dict[str, Any]],
    terms: list[dict[str, Any]],
) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO book_assistant_cache(
                chapter_id, text_digest, summary, characters_json, terms_json, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chapter_id) DO UPDATE SET
                text_digest=excluded.text_digest,
                summary=excluded.summary,
                characters_json=excluded.characters_json,
                terms_json=excluded.terms_json,
                updated_at=excluded.updated_at
            """,
            (
                int(chapter_id),
                str(digest),
                str(summary or "")[:4000],
                json.dumps(characters or [], ensure_ascii=False),
                json.dumps(terms or [], ensure_ascii=False),
                now,
                now,
            ),
        )
        await db.commit()


async def list_book_assistant_chapters(
    book_id: int,
    max_number: int,
    *,
    limit: int = 24,
) -> list[aiosqlite.Row]:
    """Последние опубликованные текстовые главы до установленной границы спойлеров."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, book_id, number, title, text, is_free, price_stars, status
            FROM chapters
            WHERE book_id=? AND status='published' AND number<=?
            ORDER BY number DESC, id DESC
            LIMIT ?
            """,
            (int(book_id), max(1, int(max_number)), max(1, min(120, int(limit)))),
        )
        rows = list(await cur.fetchall())
        rows.reverse()
        return rows


async def search_book_assistant_chapters(
    book_id: int,
    max_number: int,
    keywords: list[str],
    *,
    limit: int = 30,
) -> list[aiosqlite.Row]:
    """Ищет главы по словам вопроса, никогда не выходя за номер текущей главы."""
    clean: list[str] = []
    for raw in keywords:
        value = str(raw or "").strip()[:60]
        if len(value) < 2:
            continue
        for variant in (value, value.lower(), value.capitalize(), value.upper()):
            if variant not in clean:
                clean.append(variant)
        if len(clean) >= 24:
            break
    if not clean:
        return await list_book_assistant_chapters(book_id, max_number, limit=min(24, limit))
    clauses = []
    params: list[Any] = [int(book_id), max(1, int(max_number))]
    for value in clean:
        clauses.append("(instr(c.text, ?) > 0 OR instr(c.title, ?) > 0)")
        params.extend((value, value))
    params.append(max(1, min(80, int(limit))))
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT c.id, c.book_id, c.number, c.title, c.text, c.is_free, c.price_stars, c.status
            FROM chapters c
            WHERE c.book_id=? AND c.status='published' AND c.number<=?
              AND ({' OR '.join(clauses)})
            ORDER BY c.number DESC, c.id DESC
            LIMIT ?
            """,
            tuple(params),
        )
        rows = list(await cur.fetchall())
        rows.reverse()
        return rows


async def user_can_access_chapter(user_id: int, chapter_id: int) -> bool:
    chapter = await get_chapter(chapter_id)
    if not chapter:
        return False
    mode = _normalize_text_pricing_mode(
        int(chapter["book_price_stars"] or 0), str(chapter["pricing_type"] or "")
    )
    if mode == "free" or int(chapter["is_free"] or 0) == 1:
        return True
    if await has_manual_chapter_access(int(user_id), int(chapter_id)):
        return True
    if mode == "premium":
        return await user_has_premium(int(user_id))
    return await has_purchase_access(int(user_id), chapter_id=int(chapter_id))


_previous_get_purchase_target_v1110 = get_purchase_target


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    target = await _previous_get_purchase_target_v1110(payload)
    parts = str(payload or "").split(":")
    if not target or len(parts) < 3 or parts[0] != "vox":
        return target
    if parts[1] == "chapter":
        try:
            chapter = await get_chapter(int(parts[2]))
        except (TypeError, ValueError):
            return None
        if not chapter:
            return None
        mode = _normalize_text_pricing_mode(
            int(chapter["book_price_stars"] or 0), str(chapter["pricing_type"] or "")
        )
        if mode != "chapters" or int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0:
            return None
    elif parts[1] == "book":
        try:
            book = await get_book(int(parts[2]))
        except (TypeError, ValueError):
            return None
        if not book:
            return None
        mode = _normalize_text_pricing_mode(int(book["price_stars"] or 0), str(book["pricing_type"] or ""))
        if mode in {"free", "premium"}:
            return None
    return target


_ACHIEVEMENT_CATALOG: dict[str, dict[str, str]] = {
    "first_chapter": {"title": "Первая глава", "description": "Прочитать первую главу до конца.", "icon": "📖", "group": "reader"},
    "hundred_chapters": {"title": "100 глав", "description": "Прочитать сто глав до конца.", "icon": "💯", "group": "reader"},
    "night_reader": {"title": "Ночной читатель", "description": "Читать поздним вечером или ночью.", "icon": "🌙", "group": "reader"},
    "collector": {"title": "Коллекционер", "description": "Сохранить десять произведений в библиотеке.", "icon": "📚", "group": "reader"},
    "first_review": {"title": "Первый отзыв", "description": "Оставить первый опубликованный отзыв.", "icon": "⭐", "group": "reader"},
    "first_book": {"title": "Первая книга", "description": "Опубликовать первое произведение.", "icon": "✍️", "group": "author"},
    "author_hundred_chapters": {"title": "Автор 100 глав", "description": "Опубликовать сто текстовых или графических глав.", "icon": "🏛", "group": "author"},
    "thousand_readers": {"title": "1000 читателей", "description": "Собрать тысячу уникальных читателей.", "icon": "👥", "group": "author"},
    "author_month": {"title": "Автор месяца", "description": "Стать первым по уникальным читателям месяца.", "icon": "🏆", "group": "author"},
}


async def get_author_analytics(author_user_id: int, days: int = 30) -> dict[str, Any]:
    """Понятная авторская аналитика без раскрытия личных данных читателей."""
    period_days = max(7, min(365, int(days or 30)))
    since = (datetime.now(timezone.utc) - timedelta(days=period_days)).isoformat()
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(author_user_id),))
        author = await cur.fetchone()
        if not author:
            return {"days": period_days, "summary": {}, "books": [], "dropoff": [], "daily": []}
        author_id = int(author["id"])

        # Независимые подзапросы не перемножают чтения, отзывы, комментарии и реакции.
        # Это важно для точного среднего рейтинга и стабильной аналитики на активных книгах.
        cur = await db.execute(
            """
            SELECT
                (
                    SELECT COUNT(DISTINCT rp.user_id)
                    FROM reading_progress rp JOIN books rb ON rb.id=rp.book_id
                    WHERE rb.author_id=? AND rb.publication_status!='deleted' AND rp.updated_at>=?
                ) AS unique_readers,
                (
                    SELECT COUNT(*) FROM (
                        SELECT rp.user_id, rp.chapter_id
                        FROM reading_progress rp JOIN books rb ON rb.id=rp.book_id
                        WHERE rb.author_id=? AND rb.publication_status!='deleted'
                          AND rp.position_percent>=90 AND rp.updated_at>=?
                        GROUP BY rp.user_id, rp.chapter_id
                    )
                ) AS completed_chapters,
                (
                    SELECT COUNT(*) FROM (
                        SELECT bm.user_id, bm.book_id
                        FROM bookmarks bm JOIN books rb ON rb.id=bm.book_id
                        WHERE rb.author_id=? AND rb.publication_status!='deleted' AND bm.created_at>=?
                        GROUP BY bm.user_id, bm.book_id
                    )
                ) AS library_additions,
                (
                    SELECT COUNT(*)
                    FROM reviews r JOIN books rb ON rb.id=r.book_id
                    WHERE rb.author_id=? AND rb.publication_status!='deleted'
                      AND r.status='published' AND r.created_at>=?
                ) AS reviews_count,
                (
                    SELECT ROUND(COALESCE(AVG(r.rating),0),2)
                    FROM reviews r JOIN books rb ON rb.id=r.book_id
                    WHERE rb.author_id=? AND rb.publication_status!='deleted'
                      AND r.status='published' AND r.created_at>=?
                ) AS average_rating,
                (
                    SELECT COUNT(*)
                    FROM comments cm JOIN books rb ON rb.id=cm.book_id
                    WHERE rb.author_id=? AND rb.publication_status!='deleted'
                      AND cm.status='published' AND cm.created_at>=?
                ) AS comments_count,
                (
                    SELECT COUNT(*)
                    FROM chapter_reactions cr
                    JOIN chapters c ON c.id=cr.chapter_id
                    JOIN books rb ON rb.id=c.book_id
                    WHERE rb.author_id=? AND rb.publication_status!='deleted' AND cr.created_at>=?
                ) AS reactions_count
            """,
            (
                author_id, since,
                author_id, since,
                author_id, since,
                author_id, since,
                author_id, since,
                author_id, since,
                author_id, since,
            ),
        )
        engagement = await cur.fetchone()

        cur = await db.execute(
            """
            SELECT COUNT(DISTINCT p.id) AS sales_count, COALESCE(SUM(p.amount_stars),0) AS revenue_stars
            FROM purchases p
            LEFT JOIN chapters c ON c.id=p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
            LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
            LEFT JOIN books b ON b.id=COALESCE(p.book_id, c.book_id, ac.book_id, gc.book_id)
            WHERE b.author_id=? AND p.status='paid' AND p.created_at>=?
            """,
            (author_id, since),
        )
        sales = await cur.fetchone()

        cur = await db.execute(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN pce.event_type='open' THEN pce.user_id END) AS premium_readers,
                COUNT(CASE WHEN pce.event_type='open' THEN 1 END) AS premium_opens,
                COUNT(CASE WHEN pce.event_type='complete' THEN 1 END) AS premium_completions
            FROM premium_content_events pce
            JOIN books pb ON pb.id=pce.book_id
            WHERE pb.author_id=? AND pb.publication_status!='deleted' AND pce.created_at>=?
            """,
            (author_id, since),
        )
        premium_activity = await cur.fetchone()

        cur = await db.execute(
            """
            SELECT COALESCE(SUM(net_stars),0) AS income_stars
            FROM author_ledger
            WHERE author_id=? AND source_type='premium_pool'
              AND status!='refunded' AND created_at>=?
            """,
            (author_id, since),
        )
        premium_income = await cur.fetchone()

        cur = await db.execute(
            """
            SELECT b.id, b.title, b.publication_status,
                   COUNT(DISTINCT rp.user_id) AS readers,
                   COUNT(DISTINCT bm.user_id) AS saved,
                   ROUND(COALESCE(AVG(CASE WHEN r.status='published' THEN r.rating END),0),2) AS rating,
                   (
                     SELECT COUNT(*) FROM purchases p
                     LEFT JOIN chapters pc ON pc.id=p.chapter_id
                     LEFT JOIN audio_chapters pac ON pac.id=p.audio_chapter_id
                     LEFT JOIN graphic_chapters pgc ON pgc.id=p.graphic_chapter_id
                     WHERE p.status='paid' AND p.created_at>=? AND COALESCE(p.book_id,pc.book_id,pac.book_id,pgc.book_id)=b.id
                   ) AS sales,
                   (
                     SELECT COALESCE(SUM(p.amount_stars),0) FROM purchases p
                     LEFT JOIN chapters pc ON pc.id=p.chapter_id
                     LEFT JOIN audio_chapters pac ON pac.id=p.audio_chapter_id
                     LEFT JOIN graphic_chapters pgc ON pgc.id=p.graphic_chapter_id
                     WHERE p.status='paid' AND p.created_at>=? AND COALESCE(p.book_id,pc.book_id,pac.book_id,pgc.book_id)=b.id
                   ) AS revenue_stars
            FROM books b
            LEFT JOIN reading_progress rp ON rp.book_id=b.id AND rp.updated_at>=?
            LEFT JOIN bookmarks bm ON bm.book_id=b.id AND bm.created_at>=?
            LEFT JOIN reviews r ON r.book_id=b.id AND r.created_at>=?
            WHERE b.author_id=? AND b.publication_status!='deleted'
            GROUP BY b.id
            ORDER BY readers DESC, sales DESC, b.updated_at DESC
            LIMIT 20
            """,
            (since, since, since, since, since, author_id),
        )
        book_rows = await cur.fetchall()

        cur = await db.execute(
            """
            SELECT b.id AS book_id, b.title AS book_title, c.id AS chapter_id, c.number, c.title,
                   COUNT(DISTINCT CASE WHEN rp.position_percent>=5 THEN rp.user_id END) AS started,
                   COUNT(DISTINCT CASE WHEN rp.position_percent>=90 THEN rp.user_id END) AS completed
            FROM books b
            JOIN chapters c ON c.book_id=b.id AND c.status='published'
            LEFT JOIN reading_progress rp ON rp.chapter_id=c.id AND rp.updated_at>=?
            WHERE b.author_id=? AND b.publication_status='published'
            GROUP BY c.id
            HAVING started > 0
            ORDER BY started DESC, b.id, c.number
            LIMIT 40
            """,
            (since, author_id),
        )
        drop_rows = await cur.fetchall()

        cur = await db.execute(
            """
            WITH dates(day) AS (
                SELECT date('now', '-' || (? - 1) || ' day')
                UNION ALL SELECT date(day, '+1 day') FROM dates WHERE day < date('now')
            ), activity AS (
                SELECT substr(rp.updated_at,1,10) AS day, COUNT(DISTINCT rp.user_id) AS readers
                FROM reading_progress rp JOIN books b ON b.id=rp.book_id
                WHERE b.author_id=? AND rp.updated_at>=? GROUP BY substr(rp.updated_at,1,10)
            )
            SELECT dates.day, COALESCE(activity.readers,0) AS readers
            FROM dates LEFT JOIN activity ON activity.day=dates.day ORDER BY dates.day
            """,
            (min(period_days, 31), author_id, since),
        )
        daily_rows = await cur.fetchall()

        summary = {
            "unique_readers": int(engagement["unique_readers"] or 0),
            "completed_chapters": int(engagement["completed_chapters"] or 0),
            "library_additions": int(engagement["library_additions"] or 0),
            "reviews_count": int(engagement["reviews_count"] or 0),
            "average_rating": float(engagement["average_rating"] or 0),
            "comments_count": int(engagement["comments_count"] or 0),
            "reactions_count": int(engagement["reactions_count"] or 0),
            "premium_readers": int(premium_activity["premium_readers"] or 0),
            "premium_opens": int(premium_activity["premium_opens"] or 0),
            "premium_completions": int(premium_activity["premium_completions"] or 0),
            "premium_income_stars": int(premium_income["income_stars"] or 0),
            "sales_count": int(sales["sales_count"] or 0),
            "revenue_stars": int(sales["revenue_stars"] or 0),
        }
        books = [{key: row[key] for key in row.keys()} for row in book_rows]
        dropoff = []
        for row in drop_rows:
            started = int(row["started"] or 0)
            completed = int(row["completed"] or 0)
            dropoff.append({
                "book_id": int(row["book_id"]), "book_title": row["book_title"],
                "chapter_id": int(row["chapter_id"]), "number": int(row["number"]), "title": row["title"],
                "started": started, "completed": completed,
                "completion_rate": round((completed / started * 100) if started else 0, 1),
            })
        daily = [{"day": row["day"], "readers": int(row["readers"] or 0)} for row in daily_rows]
        return {"days": period_days, "summary": summary, "books": books, "dropoff": dropoff, "daily": daily}


async def sync_user_achievements(user_id: int) -> dict[str, list[dict[str, Any]]]:
    """Начисляет только подтверждённые достижениями действия; уже выданные не отзываются."""
    uid = int(user_id)
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS completed FROM reading_progress WHERE user_id=? AND position_percent>=90", (uid,)
        )
        completed = int((await cur.fetchone())["completed"] or 0)
        cur = await db.execute("SELECT COUNT(*) AS saved FROM bookmarks WHERE user_id=?", (uid,))
        saved = int((await cur.fetchone())["saved"] or 0)
        cur = await db.execute("SELECT COUNT(*) AS reviews FROM reviews WHERE user_id=? AND status='published'", (uid,))
        reviews = int((await cur.fetchone())["reviews"] or 0)
        cur = await db.execute(
            "SELECT COUNT(*) AS night FROM reading_progress WHERE user_id=? AND (CAST(substr(updated_at,12,2) AS INTEGER)>=22 OR CAST(substr(updated_at,12,2) AS INTEGER)<5)",
            (uid,),
        )
        night = int((await cur.fetchone())["night"] or 0)
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (uid,))
        author = await cur.fetchone()
        published_books = published_chapters = author_readers = month_rank = 0
        if author:
            author_id = int(author["id"])
            cur = await db.execute("SELECT COUNT(*) AS cnt FROM books WHERE author_id=? AND publication_status='published'", (author_id,))
            published_books = int((await cur.fetchone())["cnt"] or 0)
            cur = await db.execute(
                "SELECT (SELECT COUNT(*) FROM chapters c JOIN books b ON b.id=c.book_id WHERE b.author_id=? AND b.publication_status='published' AND c.status='published') + "
                "(SELECT COUNT(*) FROM graphic_chapters gc JOIN books b ON b.id=gc.book_id WHERE b.author_id=? AND b.publication_status='published' AND gc.status='published') AS cnt",
                (author_id, author_id),
            )
            published_chapters = int((await cur.fetchone())["cnt"] or 0)
            cur = await db.execute(
                "SELECT COUNT(DISTINCT rp.user_id) AS cnt FROM reading_progress rp JOIN books b ON b.id=rp.book_id WHERE b.author_id=?",
                (author_id,),
            )
            author_readers = int((await cur.fetchone())["cnt"] or 0)
            month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
            cur = await db.execute(
                """
                WITH author_month AS (
                    SELECT b.author_id, COUNT(DISTINCT rp.user_id) AS readers
                    FROM books b LEFT JOIN reading_progress rp ON rp.book_id=b.id AND rp.updated_at>=?
                    WHERE b.publication_status='published' GROUP BY b.author_id
                )
                SELECT 1 + COUNT(*) AS rank FROM author_month mine, author_month other
                WHERE mine.author_id=? AND other.readers>mine.readers
                """,
                (month_start, author_id),
            )
            row = await cur.fetchone()
            month_rank = int(row["rank"] or 0) if row else 0

        candidates = {
            "first_chapter": (completed >= 1, completed),
            "hundred_chapters": (completed >= 100, completed),
            "night_reader": (night >= 1, night),
            "collector": (saved >= 10, saved),
            "first_review": (reviews >= 1, reviews),
            "first_book": (published_books >= 1, published_books),
            "author_hundred_chapters": (published_chapters >= 100, published_chapters),
            "thousand_readers": (author_readers >= 1000, author_readers),
            "author_month": (month_rank == 1 and author_readers >= 20, author_readers),
        }
        awarded_codes: list[str] = []
        for code, (eligible, value) in candidates.items():
            if not eligible:
                continue
            cur = await db.execute(
                "INSERT OR IGNORE INTO user_achievements(user_id, achievement_code, progress_value, metadata_json, awarded_at) VALUES(?, ?, ?, '{}', ?)",
                (uid, code, int(value), now),
            )
            if cur.rowcount:
                awarded_codes.append(code)
        await db.commit()
        cur = await db.execute(
            "SELECT achievement_code, progress_value, metadata_json, awarded_at FROM user_achievements WHERE user_id=? ORDER BY awarded_at DESC, id DESC",
            (uid,),
        )
        rows = await cur.fetchall()

    def public(row: Any) -> dict[str, Any]:
        code = str(row["achievement_code"])
        info = _ACHIEVEMENT_CATALOG.get(code, {"title": code, "description": "", "icon": "✦", "group": "reader"})
        return {"code": code, **info, "progress_value": int(row["progress_value"] or 0), "awarded_at": row["awarded_at"]}

    all_items = [public(row) for row in rows]
    new_items = [item for item in all_items if item["code"] in awarded_codes]
    return {"new": new_items, "items": all_items}


async def list_smart_reader_reminder_candidates(limit: int = 100) -> list[aiosqlite.Row]:
    """Возвращает только давно не продолженные активные книги, не чаще одного раза в неделю."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    repeat_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            WITH latest AS (
                SELECT rp.user_id, rp.book_id, MAX(rp.updated_at) AS last_read_at
                FROM reading_progress rp GROUP BY rp.user_id, rp.book_id
            )
            SELECT u.id AS user_id, u.telegram_id, u.full_name, b.id AS book_id, b.title AS book_title,
                   c.number AS chapter_number, c.title AS chapter_title, latest.last_read_at
            FROM latest
            JOIN users u ON u.id=latest.user_id AND u.is_blocked=0
            JOIN books b ON b.id=latest.book_id AND b.publication_status='published'
            JOIN reading_progress rp ON rp.user_id=latest.user_id AND rp.book_id=latest.book_id AND rp.updated_at=latest.last_read_at
            JOIN chapters c ON c.id=rp.chapter_id
            LEFT JOIN bookmarks bm ON bm.user_id=u.id AND bm.book_id=b.id
            LEFT JOIN user_preferences pref ON pref.user_id=u.id
            LEFT JOIN smart_notification_state sns ON sns.user_id=u.id AND sns.notification_code='continue_reading' AND sns.context_key=CAST(b.id AS TEXT)
            WHERE latest.last_read_at<=? AND latest.last_read_at>=?
              AND COALESCE(pref.notifications,1)=1 AND COALESCE(pref.notifications_reminders,1)=1
              AND COALESCE(bm.status,'reading') NOT IN ('finished','dropped')
              AND (sns.last_sent_at IS NULL OR sns.last_sent_at<?)
            ORDER BY latest.last_read_at ASC
            LIMIT ?
            """,
            (cutoff, recent, repeat_cutoff, int(limit)),
        )
        return await cur.fetchall()


async def mark_smart_notification_sent(user_id: int, code: str, context_key: str) -> None:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO smart_notification_state(user_id, notification_code, context_key, last_sent_at, send_count)
            VALUES(?, ?, ?, ?, 1)
            ON CONFLICT(user_id, notification_code, context_key) DO UPDATE SET
                last_sent_at=excluded.last_sent_at, send_count=smart_notification_state.send_count+1
            """,
            (int(user_id), str(code)[:60], str(context_key)[:120], now),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# VoxLyra v1.11.0 — этап 8: Premium без ограничения базового чтения
# ---------------------------------------------------------------------------

PREMIUM_PLAN_MONTHLY = "monthly"
PREMIUM_SUBSCRIPTION_PERIOD_SECONDS = 2_592_000  # 30 суток — период Telegram Stars


async def _ensure_v1110_premium_schema(db: aiosqlite.Connection) -> None:
    """Мягкая миграция Premium.

    Premium добавляет удобства, оформление и доступ к произведениям, которые
    авторы добровольно включили в подписку. Бесплатные функции и разовые
    покупки других книг остаются отдельными.
    """
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS premium_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price_stars INTEGER NOT NULL,
            duration_days INTEGER NOT NULL DEFAULT 30,
            subscription_period_seconds INTEGER NOT NULL DEFAULT 2592000,
            is_recurring INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 10,
            features_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS premium_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            started_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            telegram_payment_charge_id TEXT NOT NULL UNIQUE,
            is_recurring INTEGER NOT NULL DEFAULT 0,
            auto_renew INTEGER NOT NULL DEFAULT 0,
            is_first_recurring INTEGER NOT NULL DEFAULT 0,
            canceled_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS premium_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            plan_code TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_premium_subscriptions_user_expiry
            ON premium_subscriptions(user_id, expires_at DESC);
        CREATE INDEX IF NOT EXISTS idx_premium_subscriptions_status_expiry
            ON premium_subscriptions(status, expires_at);
        CREATE INDEX IF NOT EXISTS idx_premium_events_user_created
            ON premium_events(user_id, created_at DESC);
        """
    )
    features = json.dumps(
        [
            {"code": "premium_books", "title": "Чтение книг и глав, которые авторы включили в Premium"},
            {"code": "author_support", "title": "Часть оплаты направляется в фонд прочитанных авторов"},
            {"code": "no_promoted_blocks", "title": "Без рекламных рекомендаций в читалке"},
            {"code": "priority_tts", "title": "Приоритет в очереди озвучивания"},
            {"code": "premium_badge", "title": "Знак Premium в личной библиотеке"},
            {"code": "quote_styles", "title": "Дополнительные стили карточек цитат"},
            {"code": "reading_insights", "title": "Расширенная личная статистика"},
        ],
        ensure_ascii=False,
    )
    await db.execute(
        """
        INSERT INTO premium_plans(
            code, title, description, price_stars, duration_days,
            subscription_period_seconds, is_recurring, is_active, sort_order,
            features_json, created_at, updated_at
        ) VALUES(?, ?, ?, ?, 30, ?, 1, 1, 10, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            duration_days=excluded.duration_days,
            subscription_period_seconds=excluded.subscription_period_seconds,
            is_recurring=excluded.is_recurring,
            features_json=excluded.features_json,
            updated_at=excluded.updated_at
        """,
        (
            PREMIUM_PLAN_MONTHLY,
            "VoxLyra Premium",
            "Книги авторов по подписке, поддержка прочитанных авторов, дополнительный комфорт, оформление и приоритетная локальная озвучка. Бесплатные функции и разовые покупки остаются отдельными.",
            99,
            PREMIUM_SUBSCRIPTION_PERIOD_SECONDS,
            features,
            now,
            now,
        ),
    )
    defaults = {
        "premium_enabled": "1",
        "premium_monthly_price_stars": "99",
        "premium_free_tts_voice_limit": "2",
        "premium_priority_tts": "1",
    }
    for key, value in defaults.items():
        await db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )
    # Цена управляется настройкой владельца, но хранится и в плане для платежной проверки.
    cur = await db.execute("SELECT value FROM settings WHERE key='premium_monthly_price_stars'")
    row = await cur.fetchone()
    try:
        configured_price = max(1, min(10000, int(row["value"] if row else 99)))
    except (TypeError, ValueError):
        configured_price = 99
    await db.execute(
        "UPDATE premium_plans SET price_stars=?, updated_at=? WHERE code=?",
        (configured_price, now, PREMIUM_PLAN_MONTHLY),
    )


def _premium_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


async def list_premium_plans(*, include_inactive: bool = False) -> list[dict[str, Any]]:
    async with connect() as db:
        sql = "SELECT * FROM premium_plans"
        params: tuple[Any, ...] = ()
        if not include_inactive:
            sql += " WHERE is_active=1"
        sql += " ORDER BY sort_order, id"
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        try:
            item["features"] = json.loads(str(item.get("features_json") or "[]"))
        except json.JSONDecodeError:
            item["features"] = []
        result.append(item)
    return result


async def get_premium_plan(code: str = PREMIUM_PLAN_MONTHLY) -> dict[str, Any] | None:
    plans = await list_premium_plans(include_inactive=True)
    for plan in plans:
        if str(plan.get("code")) == str(code):
            return plan
    return None


async def set_premium_plan_settings(
    *,
    price_stars: int | None = None,
    enabled: bool | None = None,
    author_pool_percent: int | None = None,
) -> dict[str, Any]:
    now = utc_now()
    async with connect() as db:
        if price_stars is not None:
            price = max(1, min(10000, int(price_stars)))
            await db.execute(
                "UPDATE premium_plans SET price_stars=?, updated_at=? WHERE code=?",
                (price, now, PREMIUM_PLAN_MONTHLY),
            )
            await db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES('premium_monthly_price_stars',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (str(price), now),
            )
        if enabled is not None:
            flag = 1 if enabled else 0
            await db.execute(
                "UPDATE premium_plans SET is_active=?, updated_at=? WHERE code=?",
                (flag, now, PREMIUM_PLAN_MONTHLY),
            )
            await db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES('premium_enabled',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (str(flag), now),
            )
        if author_pool_percent is not None:
            pool_percent = max(1, min(95, int(author_pool_percent)))
            await db.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES('premium_author_pool_percent',?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (str(pool_percent), now),
            )
        await db.commit()
    plan = await get_premium_plan(PREMIUM_PLAN_MONTHLY)
    if not plan:
        raise RuntimeError("Premium plan is missing")
    return plan


async def _expire_premium_subscriptions(db: aiosqlite.Connection, *, user_id: int | None = None) -> None:
    now = utc_now()
    if user_id is None:
        await db.execute(
            "UPDATE premium_subscriptions SET status='expired', updated_at=? "
            "WHERE status IN ('active','canceled') AND expires_at<=?",
            (now, now),
        )
    else:
        await db.execute(
            "UPDATE premium_subscriptions SET status='expired', updated_at=? "
            "WHERE user_id=? AND status IN ('active','canceled') AND expires_at<=?",
            (now, int(user_id), now),
        )


async def get_user_premium_status(user_id: int) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    async with connect() as db:
        await _expire_premium_subscriptions(db, user_id=int(user_id))
        await db.commit()
        cur = await db.execute(
            """
            SELECT ps.*, pp.title AS plan_title, pp.price_stars, pp.features_json,
                   pp.duration_days, pp.subscription_period_seconds
            FROM premium_subscriptions ps
            LEFT JOIN premium_plans pp ON pp.code=ps.plan_code
            WHERE ps.user_id=?
            ORDER BY ps.expires_at DESC, ps.id DESC
            LIMIT 1
            """,
            (int(user_id),),
        )
        row = await cur.fetchone()
    if not row:
        return {
            "active": False,
            "status": "none",
            "plan_code": "",
            "plan_title": "",
            "expires_at": "",
            "days_left": 0,
            "is_recurring": False,
            "auto_renew": False,
            "features": [],
        }
    expires_dt = _premium_dt(str(row["expires_at"] or ""))
    active = bool(expires_dt and expires_dt > now_dt and str(row["status"]) in {"active", "canceled"})
    seconds_left = max(0, int((expires_dt - now_dt).total_seconds())) if active and expires_dt else 0
    try:
        features = json.loads(str(row["features_json"] or "[]"))
    except json.JSONDecodeError:
        features = []
    return {
        "active": active,
        "status": str(row["status"] or "none"),
        "subscription_id": int(row["id"]),
        "plan_code": str(row["plan_code"] or ""),
        "plan_title": str(row["plan_title"] or "VoxLyra Premium"),
        "price_stars": int(row["price_stars"] or 0),
        "started_at": str(row["started_at"] or ""),
        "expires_at": str(row["expires_at"] or ""),
        "days_left": (seconds_left + 86399) // 86400,
        "is_recurring": bool(row["is_recurring"]),
        "auto_renew": bool(row["auto_renew"]),
        "is_first_recurring": bool(row["is_first_recurring"]),
        "telegram_payment_charge_id": str(row["telegram_payment_charge_id"] or ""),
        "source": str(row["source"] or "payment") if "source" in row.keys() else "payment",
        "grant_note": str(row["grant_note"] or "") if "grant_note" in row.keys() else "",
        "features": features,
    }


async def user_has_premium(user_id: int) -> bool:
    return bool((await get_user_premium_status(int(user_id))).get("active"))


async def activate_premium_subscription(
    *,
    user_id: int,
    plan_code: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
    subscription_expiration_date: int | None = None,
    is_recurring: bool = False,
    is_first_recurring: bool = False,
    invoice_payload: str = "",
) -> int:
    plan = await get_premium_plan(plan_code)
    if not plan or int(plan.get("is_active") or 0) != 1:
        raise ValueError("Premium временно недоступен")
    expected = int(plan.get("price_stars") or 0)
    if expected <= 0 or int(amount_stars) != expected:
        raise ValueError("Цена Premium изменилась. Откройте оплату заново")
    charge_id = str(telegram_payment_charge_id or "").strip()
    if not charge_id:
        raise ValueError("Не указан идентификатор платежа")
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    if subscription_expiration_date:
        expires_dt = datetime.fromtimestamp(int(subscription_expiration_date), tz=timezone.utc)
        if expires_dt <= now_dt:
            expires_dt = now_dt + timedelta(days=int(plan.get("duration_days") or 30))
    else:
        expires_dt = now_dt + timedelta(days=int(plan.get("duration_days") or 30))
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT id, user_id FROM premium_subscriptions WHERE telegram_payment_charge_id=?",
            (charge_id,),
        )
        existing = await cur.fetchone()
        if existing:
            if int(existing["user_id"]) != int(user_id):
                await db.rollback()
                raise ValueError("Идентификатор платежа уже использован")
            await db.commit()
            return int(existing["id"])
        # Если Telegram не передал дату (разовая резервная оплата), продлеваем от
        # текущего окончания, а не обнуляем уже оплаченный остаток.
        if not subscription_expiration_date:
            cur = await db.execute(
                "SELECT expires_at FROM premium_subscriptions WHERE user_id=? ORDER BY expires_at DESC LIMIT 1",
                (int(user_id),),
            )
            current = await cur.fetchone()
            current_expiry = _premium_dt(str(current["expires_at"])) if current else None
            if current_expiry and current_expiry > now_dt:
                expires_dt = current_expiry + timedelta(days=int(plan.get("duration_days") or 30))
        cur = await db.execute(
            """
            INSERT INTO premium_subscriptions(
                user_id, plan_code, status, started_at, expires_at,
                telegram_payment_charge_id, is_recurring, auto_renew,
                is_first_recurring, created_at, updated_at
            ) VALUES(?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id), str(plan_code), now, expires_dt.isoformat(), charge_id,
                1 if is_recurring else 0, 1 if is_recurring else 0,
                1 if is_first_recurring else 0, now, now,
            ),
        )
        subscription_id = int(cur.lastrowid)
        # Отдельная строка в общей истории покупок, чтобы пользователь и владелец
        # видели платёж, но он не попадал в доход конкретного автора.
        cur = await db.execute(
            """
            INSERT INTO purchases(
                user_id, amount_stars, status, telegram_payment_charge_id,
                created_at, payload, purchase_kind
            ) VALUES(?, ?, 'paid', ?, ?, ?, 'premium')
            """,
            (int(user_id), expected, charge_id, now, str(invoice_payload or f"vox:premium:{plan_code}")),
        )
        purchase_id = int(cur.lastrowid)
        await _create_premium_author_pool_row(
            db,
            purchase_id=purchase_id,
            subscription_id=subscription_id,
            user_id=int(user_id),
            gross_stars=expected,
            period_end=expires_dt,
            duration_days=int(plan.get("duration_days") or 30),
            now=now,
        )
        await db.execute(
            "INSERT INTO premium_events(user_id,event_type,plan_code,metadata_json,created_at) VALUES(?,?,?,?,?)",
            (
                int(user_id), "payment", str(plan_code),
                json.dumps({"purchase_id": purchase_id, "subscription_id": subscription_id, "recurring": bool(is_recurring)}, ensure_ascii=False),
                now,
            ),
        )
        await db.commit()
        return subscription_id


async def set_premium_auto_renew(user_id: int, *, enabled: bool) -> bool:
    now = utc_now()
    async with connect() as db:
        await _expire_premium_subscriptions(db, user_id=int(user_id))
        cur = await db.execute(
            "SELECT id FROM premium_subscriptions WHERE user_id=? AND status IN ('active','canceled') "
            "AND expires_at>? ORDER BY expires_at DESC LIMIT 1",
            (int(user_id), now),
        )
        row = await cur.fetchone()
        if not row:
            await db.commit()
            return False
        await db.execute(
            """
            UPDATE premium_subscriptions
            SET auto_renew=?, status=?, canceled_at=?, updated_at=?
            WHERE id=?
            """,
            (
                1 if enabled else 0,
                "active" if enabled else "canceled",
                None if enabled else now,
                now,
                int(row["id"]),
            ),
        )
        await db.execute(
            "INSERT INTO premium_events(user_id,event_type,plan_code,metadata_json,created_at) VALUES(?,?,?,?,?)",
            (int(user_id), "renew_enabled" if enabled else "renew_canceled", PREMIUM_PLAN_MONTHLY, "{}", now),
        )
        await db.commit()
        return True


async def get_premium_checkout_target(payload: str) -> dict[str, Any] | None:
    value = str(payload or "")
    if value.startswith("vox:intent:"):
        intent = await get_payment_intent(value)
        if not intent:
            return None
        value = str(intent["canonical_payload"] or "")
    parts = value.split(":")
    if len(parts) != 3 or parts[0] != "vox" or parts[1] != "premium":
        return None
    plan = await get_premium_plan(parts[2])
    if not plan or int(plan.get("is_active") or 0) != 1:
        return None
    return {
        "kind": "premium",
        "target_id": int(plan["id"]),
        "plan_code": str(plan["code"]),
        "title": str(plan["title"]),
        "description": str(plan["description"]),
        "amount_stars": int(plan["price_stars"]),
        "author_id": None,
        "book_id": None,
        "features": list(plan.get("features") or []),
    }


_previous_get_purchase_target_v1110_premium = get_purchase_target


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    premium = await get_premium_checkout_target(payload)
    if premium:
        return premium
    return await _previous_get_purchase_target_v1110_premium(payload)


async def get_premium_owner_summary() -> dict[str, Any]:
    now = utc_now()
    async with connect() as db:
        await _expire_premium_subscriptions(db)
        await db.commit()
        cur = await db.execute(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN status IN ('active','canceled') AND expires_at>? THEN user_id END) AS active_users,
                COUNT(CASE WHEN auto_renew=1 AND status='active' AND expires_at>? THEN 1 END) AS auto_renew,
                COUNT(CASE WHEN COALESCE(source,'payment')='payment' THEN 1 END) AS payments,
                COUNT(CASE WHEN COALESCE(source,'payment')='manual' THEN 1 END) AS manual_grants
            FROM premium_subscriptions
            """,
            (now, now),
        )
        counts = await cur.fetchone()
        cur = await db.execute(
            "SELECT COALESCE(SUM(amount_stars),0) AS gross FROM purchases WHERE purchase_kind='premium' AND status='paid'"
        )
        gross = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT
              COUNT(CASE WHEN status='pending' THEN 1 END) AS pending_pools,
              COUNT(CASE WHEN status='settled' THEN 1 END) AS settled_pools,
              COUNT(CASE WHEN status='no_activity' THEN 1 END) AS no_activity_pools,
              COALESCE(SUM(CASE WHEN status IN ('settled','no_activity') THEN allocated_stars ELSE 0 END),0) AS author_allocated_stars,
              COALESCE(SUM(CASE WHEN status IN ('settled','no_activity') THEN unallocated_stars ELSE 0 END),0) AS unallocated_stars,
              COALESCE(SUM(platform_stars),0) AS platform_stars
            FROM premium_author_pools
            """
        )
        pools = await cur.fetchone()
        cur = await db.execute("SELECT value FROM settings WHERE key='premium_author_pool_percent'")
        percent_row = await cur.fetchone()
    try:
        pool_percent = max(1, min(95, int(percent_row["value"] if percent_row else 70)))
    except (TypeError, ValueError):
        pool_percent = 70
    return {
        "active_users": int(counts["active_users"] or 0) if counts else 0,
        "auto_renew": int(counts["auto_renew"] or 0) if counts else 0,
        "payments": int(counts["payments"] or 0) if counts else 0,
        "manual_grants": int(counts["manual_grants"] or 0) if counts and "manual_grants" in counts.keys() else 0,
        "gross_stars": int(gross["gross"] or 0) if gross else 0,
        "author_pool_percent": pool_percent,
        "pending_pools": int(pools["pending_pools"] or 0) if pools else 0,
        "settled_pools": int(pools["settled_pools"] or 0) if pools else 0,
        "no_activity_pools": int(pools["no_activity_pools"] or 0) if pools else 0,
        "author_allocated_stars": int(pools["author_allocated_stars"] or 0) if pools else 0,
        "unallocated_stars": int(pools["unallocated_stars"] or 0) if pools else 0,
        "platform_stars": int(pools["platform_stars"] or 0) if pools else 0,
    }

async def get_personal_reading_insights(user_id: int) -> dict[str, Any]:
    """Личная агрегированная статистика Premium без передачи истории третьим лицам."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT
                COUNT(DISTINCT rp.book_id) AS books_started,
                COUNT(DISTINCT CASE WHEN rp.position_percent>=90 THEN rp.chapter_id END) AS chapters_finished,
                COALESCE(AVG(rp.position_percent), 0) AS average_progress
            FROM reading_progress rp
            WHERE rp.user_id=?
            """,
            (int(user_id),),
        )
        reading = await cur.fetchone()
        cur = await db.execute(
            "SELECT COALESCE(SUM(position_seconds),0) AS seconds FROM listening_progress WHERE user_id=?",
            (int(user_id),),
        )
        listening = await cur.fetchone()
        cur = await db.execute("SELECT COUNT(*) AS count FROM bookmarks WHERE user_id=?", (int(user_id),))
        bookmarks = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT substr(updated_at,1,10) AS day
            FROM reading_progress
            WHERE user_id=? AND updated_at!=''
            GROUP BY substr(updated_at,1,10)
            ORDER BY day DESC
            LIMIT 120
            """,
            (int(user_id),),
        )
        days = [str(row["day"]) for row in await cur.fetchall() if row["day"]]
        cur = await db.execute(
            """
            SELECT CAST(substr(updated_at,12,2) AS INTEGER) AS hour, COUNT(*) AS amount
            FROM reading_progress
            WHERE user_id=? AND length(updated_at)>=13
            GROUP BY hour ORDER BY amount DESC, hour LIMIT 1
            """,
            (int(user_id),),
        )
        hour_row = await cur.fetchone()
    streak = 0
    expected = datetime.now(timezone.utc).date()
    available = set(days)
    # Сегодня может не быть активности — серия считается живой, если была вчера.
    if expected.isoformat() not in available:
        expected -= timedelta(days=1)
    while expected.isoformat() in available:
        streak += 1
        expected -= timedelta(days=1)
    hour = int(hour_row["hour"] or 0) if hour_row else 0
    if 5 <= hour < 12:
        favorite_period = "Утро"
    elif 12 <= hour < 18:
        favorite_period = "День"
    elif 18 <= hour < 24:
        favorite_period = "Вечер"
    else:
        favorite_period = "Ночь"
    return {
        "books_started": int(reading["books_started"] or 0) if reading else 0,
        "chapters_finished": int(reading["chapters_finished"] or 0) if reading else 0,
        "average_progress": round(float(reading["average_progress"] or 0), 1) if reading else 0.0,
        "listening_minutes": int((listening["seconds"] or 0) // 60) if listening else 0,
        "saved_books": int(bookmarks["count"] or 0) if bookmarks else 0,
        "reading_streak_days": streak,
        "favorite_period": favorite_period,
    }


# ---------------------------------------------------------------------------
# VoxLyra v1.11.4 — чтение по Premium и служебная выдача доступа
# ---------------------------------------------------------------------------

async def _ensure_v1114_access_schema(db: aiosqlite.Connection) -> None:
    now = utc_now()
    cur = await db.execute("PRAGMA table_info(premium_subscriptions)")
    columns = {str(row["name"]) for row in await cur.fetchall()}
    additions = {
        "source": "ALTER TABLE premium_subscriptions ADD COLUMN source TEXT NOT NULL DEFAULT 'payment'",
        "granted_by_user_id": "ALTER TABLE premium_subscriptions ADD COLUMN granted_by_user_id INTEGER",
        "grant_note": "ALTER TABLE premium_subscriptions ADD COLUMN grant_note TEXT NOT NULL DEFAULT ''",
        "revoked_at": "ALTER TABLE premium_subscriptions ADD COLUMN revoked_at TEXT",
    }
    for name, sql in additions.items():
        if name not in columns:
            await _execute_schema_ddl(db, sql)
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS manual_chapter_grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            expires_at TEXT,
            note TEXT NOT NULL DEFAULT '',
            granted_by_user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revoked_at TEXT,
            UNIQUE(user_id, chapter_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
            FOREIGN KEY(granted_by_user_id) REFERENCES users(id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_manual_chapter_grants_active
            ON manual_chapter_grants(user_id, chapter_id, status, expires_at);
        CREATE INDEX IF NOT EXISTS idx_manual_chapter_grants_actor
            ON manual_chapter_grants(granted_by_user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS premium_content_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            period_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, chapter_id, event_type, period_key),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_premium_content_events_book_period
            ON premium_content_events(book_id, period_key, event_type);
        """
    )
    await db.execute(
        "INSERT INTO settings(key,value,updated_at) VALUES('premium_content_access_enabled','1',?) ON CONFLICT(key) DO NOTHING",
        (now,),
    )


async def list_grantable_books(query: str = "", limit: int = 50) -> list[aiosqlite.Row]:
    clean = str(query or "").strip()
    params: list[Any] = []
    where = "b.publication_status!='deleted'"
    if clean:
        like = f"%{clean}%"
        where += " AND (b.title LIKE ? OR CAST(b.id AS TEXT)=? OR ap.pen_name LIKE ?)"
        params.extend((like, clean, like))
    params.append(max(1, min(100, int(limit))))
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.id, b.title, b.publication_status, b.pricing_type, b.price_stars,
                   ap.pen_name,
                   COUNT(CASE WHEN c.status!='deleted' THEN 1 END) AS chapters_count
            FROM books b
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            LEFT JOIN chapters c ON c.book_id=b.id
            WHERE {where}
            GROUP BY b.id
            ORDER BY CASE WHEN b.publication_status='published' THEN 0 ELSE 1 END, b.title COLLATE NOCASE
            LIMIT ?
            """,
            tuple(params),
        )
        return await cur.fetchall()


async def resolve_chapters_by_numbers(book_id: int, numbers: list[int] | tuple[int, ...]) -> dict[str, Any]:
    requested = sorted({int(value) for value in numbers if int(value) > 0})
    if not requested:
        return {"book": None, "chapters": [], "missing": []}
    placeholders = ",".join("?" for _ in requested)
    async with connect() as db:
        cur = await db.execute(
            "SELECT id, title, publication_status FROM books WHERE id=? AND publication_status!='deleted'",
            (int(book_id),),
        )
        book = await cur.fetchone()
        if not book:
            return {"book": None, "chapters": [], "missing": requested}
        cur = await db.execute(
            f"SELECT id, book_id, number, title, status FROM chapters "
            f"WHERE book_id=? AND status!='deleted' AND number IN ({placeholders}) ORDER BY number",
            (int(book_id), *requested),
        )
        chapters = list(await cur.fetchall())
    found = {int(row["number"]) for row in chapters}
    return {"book": book, "chapters": chapters, "missing": [value for value in requested if value not in found]}


def _grant_expiry(days: int | None) -> str | None:
    if days is None or int(days) <= 0:
        return None
    safe_days = max(1, min(3650, int(days)))
    return (datetime.now(timezone.utc) + timedelta(days=safe_days)).isoformat()


async def grant_manual_chapter_access(
    *,
    user_id: int,
    book_id: int,
    chapter_ids: list[int] | tuple[int, ...],
    granted_by_user_id: int,
    duration_days: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    ids = sorted({int(value) for value in chapter_ids if int(value) > 0})
    if not ids:
        return {"granted": 0, "expires_at": None}
    now = utc_now()
    expires_at = _grant_expiry(duration_days)
    clean_note = str(note or "").strip()[:500]
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        granted = 0
        for chapter_id in ids:
            cur = await db.execute(
                "SELECT book_id FROM chapters WHERE id=? AND book_id=? AND status!='deleted'",
                (chapter_id, int(book_id)),
            )
            if not await cur.fetchone():
                continue
            await db.execute(
                """
                INSERT INTO manual_chapter_grants(
                    user_id, book_id, chapter_id, status, expires_at, note,
                    granted_by_user_id, created_at, updated_at, revoked_at
                ) VALUES(?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(user_id, chapter_id) DO UPDATE SET
                    book_id=excluded.book_id,
                    status='active',
                    expires_at=excluded.expires_at,
                    note=excluded.note,
                    granted_by_user_id=excluded.granted_by_user_id,
                    updated_at=excluded.updated_at,
                    revoked_at=NULL
                """,
                (int(user_id), int(book_id), chapter_id, expires_at, clean_note,
                 int(granted_by_user_id), now, now),
            )
            granted += 1
        await db.commit()
    return {"granted": granted, "expires_at": expires_at}


async def has_manual_chapter_access(user_id: int, chapter_id: int) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT 1 FROM manual_chapter_grants
            WHERE user_id=? AND chapter_id=? AND status='active'
              AND (expires_at IS NULL OR expires_at>?)
            LIMIT 1
            """,
            (int(user_id), int(chapter_id), now),
        )
        return await cur.fetchone() is not None


async def grant_premium_manually(
    *,
    user_id: int,
    duration_days: int,
    granted_by_user_id: int,
    note: str = "",
) -> dict[str, Any]:
    days = max(1, min(3650, int(duration_days)))
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await _expire_premium_subscriptions(db, user_id=int(user_id))
        cur = await db.execute(
            "SELECT MAX(expires_at) AS expires_at FROM premium_subscriptions "
            "WHERE user_id=? AND status IN ('active','canceled') AND expires_at>?",
            (int(user_id), now),
        )
        row = await cur.fetchone()
        current = _premium_dt(str(row["expires_at"] or "")) if row and row["expires_at"] else None
        starts_from = current if current and current > now_dt else now_dt
        expires_at = (starts_from + timedelta(days=days)).isoformat()
        charge_id = f"manual:{uuid.uuid4().hex}"
        cur = await db.execute(
            """
            INSERT INTO premium_subscriptions(
                user_id, plan_code, status, started_at, expires_at,
                telegram_payment_charge_id, is_recurring, auto_renew,
                is_first_recurring, created_at, updated_at, source,
                granted_by_user_id, grant_note
            ) VALUES(?, ?, 'active', ?, ?, ?, 0, 0, 0, ?, ?, 'manual', ?, ?)
            """,
            (int(user_id), PREMIUM_PLAN_MONTHLY, now, expires_at, charge_id,
             now, now, int(granted_by_user_id), str(note or "").strip()[:500]),
        )
        subscription_id = int(cur.lastrowid)
        await db.execute(
            "INSERT INTO premium_events(user_id,event_type,plan_code,metadata_json,created_at) VALUES(?,?,?,?,?)",
            (int(user_id), "manual_grant", PREMIUM_PLAN_MONTHLY,
             json.dumps({"subscription_id": subscription_id, "days": days, "granted_by": int(granted_by_user_id)}, ensure_ascii=False), now),
        )
        await db.commit()
    return {"subscription_id": subscription_id, "expires_at": expires_at, "days": days}


async def list_manual_access_grants(user_id: int, limit: int = 100) -> dict[str, list[dict[str, Any]]]:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT mg.id, mg.book_id, mg.chapter_id, mg.status, mg.expires_at, mg.note,
                   mg.created_at, mg.updated_at, mg.revoked_at,
                   b.title AS book_title, c.number AS chapter_number, c.title AS chapter_title,
                   actor.full_name AS granted_by_name, actor.username AS granted_by_username
            FROM manual_chapter_grants mg
            JOIN books b ON b.id=mg.book_id
            JOIN chapters c ON c.id=mg.chapter_id
            LEFT JOIN users actor ON actor.id=mg.granted_by_user_id
            WHERE mg.user_id=?
            ORDER BY CASE WHEN mg.status='active' AND (mg.expires_at IS NULL OR mg.expires_at>?) THEN 0 ELSE 1 END,
                     mg.updated_at DESC
            LIMIT ?
            """,
            (int(user_id), now, max(1, min(500, int(limit)))),
        )
        chapter_rows = await cur.fetchall()
        cur = await db.execute(
            """
            SELECT ps.id, ps.status, ps.started_at, ps.expires_at, ps.grant_note,
                   ps.created_at, ps.updated_at, ps.revoked_at,
                   actor.full_name AS granted_by_name, actor.username AS granted_by_username
            FROM premium_subscriptions ps
            LEFT JOIN users actor ON actor.id=ps.granted_by_user_id
            WHERE ps.user_id=? AND COALESCE(ps.source,'payment')='manual'
            ORDER BY ps.expires_at DESC, ps.id DESC
            LIMIT ?
            """,
            (int(user_id), max(1, min(100, int(limit)))),
        )
        premium_rows = await cur.fetchall()
    return {
        "chapters": [dict(row) for row in chapter_rows],
        "premium": [dict(row) for row in premium_rows],
    }


async def revoke_manual_access_grant(*, grant_type: str, grant_id: int, revoked_by_user_id: int) -> bool:
    now = utc_now()
    kind = str(grant_type or "").strip().lower()
    async with connect() as db:
        if kind == "chapter":
            cur = await db.execute(
                "UPDATE manual_chapter_grants SET status='revoked', revoked_at=?, updated_at=? "
                "WHERE id=? AND status='active'",
                (now, now, int(grant_id)),
            )
        elif kind == "premium":
            cur = await db.execute(
                "UPDATE premium_subscriptions SET status='revoked', revoked_at=?, updated_at=? "
                "WHERE id=? AND COALESCE(source,'payment')='manual' AND status IN ('active','canceled')",
                (now, now, int(grant_id)),
            )
            if cur.rowcount:
                await db.execute(
                    "INSERT INTO premium_events(user_id,event_type,plan_code,metadata_json,created_at) "
                    "SELECT user_id,'manual_revoke',plan_code,?,? FROM premium_subscriptions WHERE id=?",
                    (json.dumps({"revoked_by": int(revoked_by_user_id)}, ensure_ascii=False), now, int(grant_id)),
                )
        else:
            return False
        changed = cur.rowcount > 0
        await db.commit()
        return changed


async def record_premium_content_event(user_id: int, chapter_id: int, event_type: str) -> bool:
    if str(event_type) not in {"open", "complete"}:
        return False
    chapter = await get_chapter(int(chapter_id))
    if not chapter:
        return False
    mode = _normalize_text_pricing_mode(int(chapter["book_price_stars"] or 0), str(chapter["pricing_type"] or ""))
    if mode != "premium" or int(chapter["is_free"] or 0) == 1 or not await user_has_premium(int(user_id)):
        return False
    now_dt = datetime.now(timezone.utc)
    async with connect() as db:
        period = await _premium_event_period_key(db, int(user_id), now_dt)
        cur = await db.execute(
            "INSERT OR IGNORE INTO premium_content_events(user_id,book_id,chapter_id,event_type,period_key,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (int(user_id), int(chapter["book_id"]), int(chapter_id), str(event_type), period, now_dt.isoformat()),
        )
        await db.commit()
        return cur.rowcount > 0


_previous_has_purchase_access_v1114 = has_purchase_access


async def has_purchase_access(
    user_id: int,
    *,
    book_id: int | None = None,
    chapter_id: int | None = None,
    audio_chapter_id: int | None = None,
    graphic_chapter_id: int | None = None,
) -> bool:
    if chapter_id is not None and await has_manual_chapter_access(int(user_id), int(chapter_id)):
        return True
    return await _previous_has_purchase_access_v1114(
        int(user_id),
        book_id=book_id,
        chapter_id=chapter_id,
        audio_chapter_id=audio_chapter_id,
        graphic_chapter_id=graphic_chapter_id,
    )


# ---------------------------------------------------------------------------
# VoxLyra v1.11.8 — целочисленное распределение дохода Premium авторам
# ---------------------------------------------------------------------------

def allocate_integer_stars(total_stars: int, weights: dict[int, int]) -> dict[int, int]:
    """Распределяет целые Stars методом наибольших остатков.

    Дробные Stars в Telegram не существуют. Поэтому сначала каждому автору
    начисляется целая нижняя часть его доли, а оставшиеся Stars по одной
    передаются авторам с наибольшим дробным остатком. Итог всегда целый и
    всегда в точности равен ``total_stars`` при наличии положительных весов.
    """
    total = max(0, int(total_stars))
    clean = {int(author_id): max(0, int(weight)) for author_id, weight in weights.items()}
    clean = {author_id: weight for author_id, weight in clean.items() if weight > 0}
    weight_sum = sum(clean.values())
    if total <= 0 or weight_sum <= 0:
        return {author_id: 0 for author_id in clean}

    allocations: dict[int, int] = {}
    ranking: list[tuple[int, int, int]] = []
    assigned = 0
    for author_id in sorted(clean):
        weight = clean[author_id]
        base, remainder = divmod(total * weight, weight_sum)
        allocations[author_id] = int(base)
        assigned += int(base)
        # Больше остаток -> выше приоритет. При равенстве больше реальный вес,
        # затем меньший id для полностью детерминированного результата.
        ranking.append((int(remainder), int(weight), int(author_id)))

    remaining = total - assigned
    ranking.sort(key=lambda item: (-item[0], -item[1], item[2]))
    for _, _, author_id in ranking[:remaining]:
        allocations[author_id] += 1
    return allocations


async def _ensure_v1118_premium_revenue_schema(db: aiosqlite.Connection) -> None:
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS premium_author_pools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id INTEGER NOT NULL UNIQUE,
            subscription_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            gross_stars INTEGER NOT NULL,
            author_pool_percent INTEGER NOT NULL DEFAULT 70,
            author_pool_stars INTEGER NOT NULL,
            platform_stars INTEGER NOT NULL,
            period_start_at TEXT NOT NULL,
            period_end_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            allocated_stars INTEGER NOT NULL DEFAULT 0,
            unallocated_stars INTEGER NOT NULL DEFAULT 0,
            settled_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE CASCADE,
            FOREIGN KEY(subscription_id) REFERENCES premium_subscriptions(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS premium_author_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pool_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            weight_points INTEGER NOT NULL,
            total_weight_points INTEGER NOT NULL,
            allocated_stars INTEGER NOT NULL,
            ledger_id INTEGER,
            created_at TEXT NOT NULL,
            UNIQUE(pool_id, author_id),
            FOREIGN KEY(pool_id) REFERENCES premium_author_pools(id) ON DELETE CASCADE,
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE,
            FOREIGN KEY(ledger_id) REFERENCES author_ledger(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_premium_author_pools_due
            ON premium_author_pools(status, period_end_at);
        CREATE INDEX IF NOT EXISTS idx_premium_author_pools_user_period
            ON premium_author_pools(user_id, period_start_at, period_end_at);
        CREATE INDEX IF NOT EXISTS idx_premium_author_allocations_author
            ON premium_author_allocations(author_id, created_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_author_ledger_premium_pool_unique
            ON author_ledger(author_id, source_type, source_id)
            WHERE source_type='premium_pool';
        """
    )
    defaults = {
        "premium_author_pool_percent": "70",
        "premium_open_weight": "1",
        "premium_complete_weight": "2",
        "premium_settlement_enabled": "1",
    }
    for key, value in defaults.items():
        await db.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )

    # Старые оплаченные Premium-периоды получают фонд без повторного платежа.
    # Это не затрагивает ручные выдачи Premium: у них нет оплаченной покупки.
    cur = await db.execute(
        """
        SELECT p.id AS purchase_id, ps.id AS subscription_id, ps.user_id,
               p.amount_stars, ps.expires_at, COALESCE(pp.duration_days, 30) AS duration_days
        FROM purchases p
        JOIN premium_subscriptions ps
          ON ps.telegram_payment_charge_id=p.telegram_payment_charge_id
        LEFT JOIN premium_plans pp ON pp.code=ps.plan_code
        LEFT JOIN premium_author_pools pap ON pap.purchase_id=p.id
        WHERE p.purchase_kind='premium' AND p.status='paid'
          AND COALESCE(ps.source,'payment')='payment' AND pap.id IS NULL
        """
    )
    for row in await cur.fetchall():
        period_end = _premium_dt(str(row["expires_at"] or ""))
        if not period_end:
            continue
        await _create_premium_author_pool_row(
            db,
            purchase_id=int(row["purchase_id"]),
            subscription_id=int(row["subscription_id"]),
            user_id=int(row["user_id"]),
            gross_stars=int(row["amount_stars"] or 0),
            period_end=period_end,
            duration_days=int(row["duration_days"] or 30),
            now=now,
        )


async def _create_premium_author_pool_row(
    db: aiosqlite.Connection,
    *,
    purchase_id: int,
    subscription_id: int,
    user_id: int,
    gross_stars: int,
    period_end: datetime,
    duration_days: int,
    now: str,
) -> None:
    cur = await db.execute("SELECT value FROM settings WHERE key='premium_author_pool_percent'")
    row = await cur.fetchone()
    try:
        percent = max(1, min(95, int(row["value"] if row else 70)))
    except (TypeError, ValueError):
        percent = 70
    gross = max(0, int(gross_stars))
    author_pool = gross * percent // 100
    platform = gross - author_pool
    safe_days = max(1, min(366, int(duration_days or 30)))
    end_dt = period_end.astimezone(timezone.utc)
    start_dt = end_dt - timedelta(days=safe_days)
    await db.execute(
        """
        INSERT OR IGNORE INTO premium_author_pools(
            purchase_id, subscription_id, user_id, gross_stars,
            author_pool_percent, author_pool_stars, platform_stars,
            period_start_at, period_end_at, status, allocated_stars,
            unallocated_stars, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,'pending',0,0,?,?)
        """,
        (
            int(purchase_id), int(subscription_id), int(user_id), gross,
            percent, author_pool, platform, start_dt.isoformat(), end_dt.isoformat(),
            now, now,
        ),
    )


async def _premium_event_period_key(db: aiosqlite.Connection, user_id: int, now_dt: datetime) -> str:
    now = now_dt.isoformat()
    cur = await db.execute(
        """
        SELECT id FROM premium_author_pools
        WHERE user_id=? AND status='pending' AND period_start_at<=? AND period_end_at>?
        ORDER BY period_end_at, id LIMIT 1
        """,
        (int(user_id), now, now),
    )
    row = await cur.fetchone()
    if row:
        return f"pool:{int(row['id'])}"
    # Ручной или бонусный Premium не создаёт авторский денежный фонд.
    return f"manual:{now_dt.strftime('%Y-%m')}"


async def settle_due_premium_author_pools(*, limit: int = 100, now_at: str | None = None) -> dict[str, int]:
    """Закрывает завершившиеся оплаченные Premium-периоды и начисляет авторам Stars."""
    now = str(now_at or utc_now())
    result = {"processed": 0, "settled": 0, "no_activity": 0, "refunded": 0, "allocated_stars": 0}
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT value FROM settings WHERE key='premium_settlement_enabled'")
        enabled_row = await cur.fetchone()
        if str(enabled_row["value"] if enabled_row else "1") != "1":
            await db.commit()
            return result

        settings_values: dict[str, int] = {}
        for key, default in (("premium_open_weight", 1), ("premium_complete_weight", 2),
                             ("hold_days_default", 14), ("payments_stars_author_rate_minor", 100)):
            cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = await cur.fetchone()
            try:
                settings_values[key] = int(row["value"] if row else default)
            except (TypeError, ValueError):
                settings_values[key] = default
        open_weight = max(1, settings_values["premium_open_weight"])
        complete_weight = max(1, settings_values["premium_complete_weight"])
        hold_days = max(0, settings_values["hold_days_default"])
        settlement_rate_minor = max(1, settings_values["payments_stars_author_rate_minor"])

        cur = await db.execute(
            """
            SELECT pap.*, p.status AS purchase_status
            FROM premium_author_pools pap
            JOIN purchases p ON p.id=pap.purchase_id
            WHERE pap.status='pending' AND pap.period_end_at<=?
            ORDER BY pap.period_end_at, pap.id
            LIMIT ?
            """,
            (now, max(1, min(1000, int(limit)))),
        )
        pools = await cur.fetchall()
        for pool in pools:
            result["processed"] += 1
            pool_id = int(pool["id"])
            if str(pool["purchase_status"] or "") != "paid":
                await db.execute(
                    "UPDATE premium_author_pools SET status='refunded', unallocated_stars=author_pool_stars, "
                    "settled_at=?, updated_at=? WHERE id=? AND status='pending'",
                    (now, now, pool_id),
                )
                result["refunded"] += 1
                continue

            event_key = f"pool:{pool_id}"
            cur = await db.execute(
                """
                SELECT b.author_id,
                       SUM(CASE WHEN pce.event_type='complete' THEN ? ELSE ? END) AS weight_points
                FROM premium_content_events pce
                JOIN books b ON b.id=pce.book_id
                JOIN author_profiles ap ON ap.id=b.author_id
                WHERE pce.user_id=? AND b.author_id IS NOT NULL AND ap.user_id<>?
                  AND (
                    pce.period_key=? OR
                    (pce.period_key NOT LIKE 'pool:%' AND pce.created_at>=? AND pce.created_at<?)
                  )
                GROUP BY b.author_id
                HAVING weight_points>0
                """,
                (
                    complete_weight, open_weight,
                    int(pool["user_id"]), int(pool["user_id"]), event_key,
                    str(pool["period_start_at"]), str(pool["period_end_at"]),
                ),
            )
            weights = {int(row["author_id"]): int(row["weight_points"] or 0) for row in await cur.fetchall()}
            total_pool = max(0, int(pool["author_pool_stars"] or 0))
            allocations = allocate_integer_stars(total_pool, weights)
            total_weight = sum(weights.values())
            allocated = 0
            available_at = (datetime.now(timezone.utc) + timedelta(days=hold_days)).isoformat()

            for author_id, stars in allocations.items():
                if stars <= 0:
                    continue
                cur = await db.execute(
                    """
                    INSERT OR IGNORE INTO author_ledger(
                        author_id, purchase_id, source_type, source_id,
                        gross_stars, commission_percent, commission_stars, net_stars,
                        settlement_rate_minor, net_minor, hold_days, available_at,
                        status, created_at, updated_at
                    ) VALUES(?,?,'premium_pool',?,?,0,0,?,?,?,?,?,'held',?,?)
                    """,
                    (
                        int(author_id), int(pool["purchase_id"]), pool_id,
                        int(stars), int(stars), settlement_rate_minor,
                        int(stars) * settlement_rate_minor, hold_days, available_at, now, now,
                    ),
                )
                if cur.rowcount:
                    ledger_id = int(cur.lastrowid)
                else:
                    existing = await db.execute(
                        "SELECT id FROM author_ledger WHERE author_id=? AND source_type='premium_pool' AND source_id=?",
                        (int(author_id), pool_id),
                    )
                    ledger_row = await existing.fetchone()
                    ledger_id = int(ledger_row["id"]) if ledger_row else None
                await db.execute(
                    """
                    INSERT OR IGNORE INTO premium_author_allocations(
                        pool_id, author_id, weight_points, total_weight_points,
                        allocated_stars, ledger_id, created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (pool_id, int(author_id), int(weights[author_id]), int(total_weight), int(stars), ledger_id, now),
                )
                allocated += int(stars)

            unallocated = max(0, total_pool - allocated)
            status = "settled" if allocated > 0 else "no_activity"
            await db.execute(
                """
                UPDATE premium_author_pools
                SET status=?, allocated_stars=?, unallocated_stars=?, settled_at=?, updated_at=?
                WHERE id=? AND status='pending'
                """,
                (status, allocated, unallocated, now, now, pool_id),
            )
            result[status] += 1
            result["allocated_stars"] += allocated
        await db.commit()
    return result


async def get_author_premium_income_summary(author_user_id: int) -> dict[str, int]:
    async with connect() as db:
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(author_user_id),))
        author = await cur.fetchone()
        if not author:
            return {"total": 0, "held": 0, "available": 0, "requested": 0, "paid": 0, "readers": 0}
        author_id = int(author["id"])
        cur = await db.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN al.status!='refunded' THEN al.net_stars ELSE 0 END),0) AS total,
              COALESCE(SUM(CASE WHEN al.status='held' THEN al.net_stars ELSE 0 END),0) AS held,
              COALESCE(SUM(CASE WHEN al.status='available' THEN al.net_stars ELSE 0 END),0) AS available,
              COALESCE(SUM(CASE WHEN al.status='payout_requested' THEN al.net_stars ELSE 0 END),0) AS requested,
              COALESCE(SUM(CASE WHEN al.status='paid' THEN al.net_stars ELSE 0 END),0) AS paid,
              COUNT(DISTINCT pap.user_id) AS readers
            FROM author_ledger al
            LEFT JOIN premium_author_pools pap ON pap.id=al.source_id AND al.source_type='premium_pool'
            WHERE al.author_id=? AND al.source_type='premium_pool'
            """,
            (author_id,),
        )
        row = await cur.fetchone()
        return {key: int(row[key] or 0) for key in row.keys()} if row else {}


# ---------------------------------------------------------------------------
# VoxLyra v1.12.0 — баланс покупок, кешбэк и реферальные бонусы
# ---------------------------------------------------------------------------

async def _ensure_v1200_bonus_wallet_schema(db: aiosqlite.Connection) -> None:
    """Idempotent wallet/bonus economy migration.

    Old daily/referral points are preserved as bonus points. Daily issuance is
    disabled by the handlers, while the nullable legacy timestamp remains for
    backward compatibility with existing databases.
    """
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_wallets (
            user_id INTEGER PRIMARY KEY,
            balance_stars INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reader_wallet_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount_stars INTEGER NOT NULL,
            transaction_type TEXT NOT NULL,
            source_type TEXT,
            source_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS wallet_topups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            purchase_id INTEGER,
            amount_stars INTEGER NOT NULL,
            buyer_bonus_points INTEGER NOT NULL DEFAULT 0,
            referrer_user_id INTEGER,
            referrer_bonus_points INTEGER NOT NULL DEFAULT 0,
            telegram_payment_charge_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'paid',
            created_at TEXT NOT NULL,
            refunded_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(referrer_user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_reader_wallet_tx_user_created
          ON reader_wallet_transactions(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_wallet_topups_user_created
          ON wallet_topups(user_id, created_at DESC);
        """
    )
    purchase_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(purchases)")).fetchall()}
    purchase_migrations = {
        "original_amount_stars": "ALTER TABLE purchases ADD COLUMN original_amount_stars INTEGER NOT NULL DEFAULT 0",
        "wallet_stars_used": "ALTER TABLE purchases ADD COLUMN wallet_stars_used INTEGER NOT NULL DEFAULT 0",
        "bonus_points_used": "ALTER TABLE purchases ADD COLUMN bonus_points_used INTEGER NOT NULL DEFAULT 0",
        "funding_method": "ALTER TABLE purchases ADD COLUMN funding_method TEXT NOT NULL DEFAULT 'telegram'",
    }
    for column, sql in purchase_migrations.items():
        if column not in purchase_columns:
            await _execute_schema_ddl(db, sql)

    ledger_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(author_ledger)")).fetchall()}
    ledger_migrations = {
        "platform_stars": "ALTER TABLE author_ledger ADD COLUMN platform_stars INTEGER NOT NULL DEFAULT 0",
        "bonus_pool_stars": "ALTER TABLE author_ledger ADD COLUMN bonus_pool_stars INTEGER NOT NULL DEFAULT 0",
        "bonus_discount_stars": "ALTER TABLE author_ledger ADD COLUMN bonus_discount_stars INTEGER NOT NULL DEFAULT 0",
    }
    for column, sql in ledger_migrations.items():
        if column not in ledger_columns:
            await _execute_schema_ddl(db, sql)

    referral_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(referrals)")).fetchall()}
    referral_migrations = {
        "qualified_at": "ALTER TABLE referrals ADD COLUMN qualified_at TEXT",
        "topup_count": "ALTER TABLE referrals ADD COLUMN topup_count INTEGER NOT NULL DEFAULT 0",
        "rewarded_bonus_points": "ALTER TABLE referrals ADD COLUMN rewarded_bonus_points INTEGER NOT NULL DEFAULT 0",
    }
    for column, sql in referral_migrations.items():
        if column not in referral_columns:
            await _execute_schema_ddl(db, sql)

    defaults = {
        "revenue_author_percent": "80",
        "revenue_platform_percent": "19",
        "revenue_bonus_percent": "1",
        "bonus_points_per_star": "100",
        "referral_percent_of_bonus": "30",
        "wallet_topup_packages": "50,100,250,500,1000",
        "daily_bonus_enabled": "0",
    }
    for key, value in defaults.items():
        await db.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )
    # Эти параметры намеренно фиксированы в v1.12.0. Обновляем и старые базы,
    # чтобы служебные экраны не показывали устаревшие значения.
    await db.execute("UPDATE settings SET value='0',updated_at=? WHERE key='daily_bonus_enabled'", (now,))
    await db.execute("UPDATE settings SET value='100',updated_at=? WHERE key='bonus_points_per_star'", (now,))
    await db.execute("UPDATE settings SET value='30',updated_at=? WHERE key='referral_percent_of_bonus'", (now,))
    # Normalize old rows for transparent purchase history.
    await db.execute(
        "UPDATE purchases SET original_amount_stars=amount_stars WHERE COALESCE(original_amount_stars,0)=0 AND amount_stars>0"
    )


async def ensure_reader_wallet(user_id: int) -> aiosqlite.Row:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,0,?,?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (int(user_id), now, now),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM reader_wallets WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        if not row:
            raise RuntimeError("Reader wallet was not created")
        return row


async def get_reader_wallet_balance(user_id: int) -> int:
    row = await ensure_reader_wallet(int(user_id))
    return int(row["balance_stars"] or 0)


async def list_reader_wallet_transactions(user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM reader_wallet_transactions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (int(user_id), max(1, min(100, int(limit)))),
        )
        return await cur.fetchall()


async def get_wallet_summary(user_id: int) -> dict[str, int]:
    wallet = await get_reader_wallet_balance(int(user_id))
    bonus = await get_bonus_balance(int(user_id))
    points_per_star = int(await get_setting("bonus_points_per_star", "100") or 100)
    return {
        "wallet_stars": wallet,
        "bonus_points": bonus,
        "bonus_whole_stars": bonus // max(1, points_per_star),
        "points_per_star": max(1, points_per_star),
    }


async def _referrer_for_user(db: aiosqlite.Connection, user_id: int) -> aiosqlite.Row | None:
    cur = await db.execute(
        "SELECT * FROM referrals WHERE referred_user_id=? ORDER BY id LIMIT 1",
        (int(user_id),),
    )
    return await cur.fetchone()


async def credit_wallet_topup(
    *,
    user_id: int,
    amount_stars: int,
    telegram_payment_charge_id: str,
    payload: str,
) -> dict[str, int]:
    """Credit a top-up exactly once and issue cashback/referral points."""
    from app.services.bonus_economy import load_revenue_split_settings, topup_bonus_points

    amount = max(1, int(amount_stars))
    charge_id = str(telegram_payment_charge_id or "").strip()
    if not charge_id:
        raise ValueError("Missing Telegram charge id")
    cfg = await load_revenue_split_settings()
    raw_payload = str(payload or "")
    intent = await get_payment_intent(raw_payload) if raw_payload.startswith("vox:intent:") else None
    canonical_payload = str(intent["canonical_payload"] or "") if intent else raw_payload
    expected_payload = f"vox:wallet_topup:{amount}"
    if canonical_payload != expected_payload:
        raise ValueError("Top-up payload does not match the paid amount")
    # Сумма могла исчезнуть из меню уже после выставления счёта. Успешный
    # платёж всё равно зачисляется: повторно платить пользователь не должен.
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT * FROM wallet_topups WHERE telegram_payment_charge_id=?", (charge_id,))
        existing = await cur.fetchone()
        if existing:
            await db.commit()
            cur = await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (int(user_id),))
            wallet = await cur.fetchone()
            cur = await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (int(user_id),))
            bonus = await cur.fetchone()
            return {
                "topup_id": int(existing["id"]),
                "purchase_id": int(existing["purchase_id"] or 0),
                "wallet_stars": int(wallet["balance_stars"] or 0) if wallet else 0,
                "buyer_bonus_points": int(existing["buyer_bonus_points"] or 0),
                "referrer_user_id": int(existing["referrer_user_id"] or 0),
                "referrer_bonus_points": int(existing["referrer_bonus_points"] or 0),
                "bonus_points": int(bonus["balance"] or 0) if bonus else 0,
            }

        referral = await _referrer_for_user(db, int(user_id))
        referrer_user_id = int(referral["referrer_user_id"]) if referral else None
        points = topup_bonus_points(
            amount,
            bonus_percent=cfg.bonus_percent,
            points_per_star=cfg.points_per_star,
            referral_percent_of_bonus=cfg.referral_percent_of_bonus,
            has_referrer=referrer_user_id is not None,
        )
        cur = await db.execute(
            """
            INSERT INTO purchases(user_id,amount_stars,status,telegram_payment_charge_id,created_at,payload,purchase_kind,
                                  original_amount_stars,wallet_stars_used,bonus_points_used,funding_method)
            VALUES(?,?,'paid',?,?,?,'wallet_topup',?,0,0,'telegram')
            """,
            (int(user_id), amount, charge_id, now, canonical_payload, amount),
        )
        purchase_id = int(cur.lastrowid)
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING",
            (int(user_id), now, now),
        )
        await db.execute(
            "UPDATE reader_wallets SET balance_stars=balance_stars+?,updated_at=? WHERE user_id=?",
            (amount, now, int(user_id)),
        )
        await db.execute(
            "INSERT INTO reader_wallet_transactions(user_id,amount_stars,transaction_type,source_type,source_id,metadata_json,created_at) "
            "VALUES(?,?,'topup','telegram',?,?,?)",
            (int(user_id), amount, str(purchase_id), json.dumps({"charge_id": charge_id}, ensure_ascii=False), now),
        )
        for uid in [int(user_id)] + ([referrer_user_id] if referrer_user_id is not None else []):
            await db.execute(
                "INSERT INTO bonus_wallets(user_id,balance,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING",
                (uid, now, now),
            )
        if points["buyer_points"]:
            await db.execute("UPDATE bonus_wallets SET balance=balance+?,updated_at=? WHERE user_id=?", (points["buyer_points"], now, int(user_id)))
            await db.execute(
                "INSERT INTO bonus_transactions(user_id,amount,reason,source_type,source_id,created_at) VALUES(?,?, 'topup_cashback','wallet_topup',?,?)",
                (int(user_id), points["buyer_points"], str(purchase_id), now),
            )
        if referrer_user_id is not None and points["referrer_points"]:
            await db.execute("UPDATE bonus_wallets SET balance=balance+?,updated_at=? WHERE user_id=?", (points["referrer_points"], now, referrer_user_id))
            await db.execute(
                "INSERT INTO bonus_transactions(user_id,amount,reason,source_type,source_id,created_at) VALUES(?,?, 'referral_topup','wallet_topup',?,?)",
                (referrer_user_id, points["referrer_points"], str(purchase_id), now),
            )
            await db.execute(
                "UPDATE referrals SET bonus_given=1,qualified_at=COALESCE(qualified_at,?),topup_count=topup_count+1,"
                "rewarded_bonus_points=rewarded_bonus_points+? WHERE id=?",
                (now, points["referrer_points"], int(referral["id"])),
            )
        cur = await db.execute(
            """
            INSERT INTO wallet_topups(user_id,purchase_id,amount_stars,buyer_bonus_points,referrer_user_id,
                                      referrer_bonus_points,telegram_payment_charge_id,status,created_at)
            VALUES(?,?,?,?,?,?,?,'paid',?)
            """,
            (int(user_id), purchase_id, amount, points["buyer_points"], referrer_user_id, points["referrer_points"], charge_id, now),
        )
        topup_id = int(cur.lastrowid)
        if intent:
            await db.execute(
                "UPDATE payment_intents SET status='paid',paid_charge_id=?,updated_at=? WHERE token=?",
                (charge_id, now, str(intent["token"])),
            )
        await db.commit()
        cur = await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (int(user_id),))
        wallet = await cur.fetchone()
        cur = await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (int(user_id),))
        bonus = await cur.fetchone()
        return {
            "topup_id": topup_id,
            "purchase_id": purchase_id,
            "wallet_stars": int(wallet["balance_stars"] or 0),
            "buyer_bonus_points": int(points["buyer_points"]),
            "referrer_user_id": int(referrer_user_id or 0),
            "referrer_bonus_points": int(points["referrer_points"]),
            "bonus_points": int(bonus["balance"] or 0),
        }


async def get_chapter_wallet_checkout(user_id: int, chapter_id: int) -> dict[str, Any] | None:
    from app.services.bonus_economy import bonus_discount_limit, load_revenue_split_settings

    target = await get_purchase_target(f"vox:chapter:{int(chapter_id)}")
    if not target or str(target.get("kind")) != "chapter" or int(target.get("amount_stars") or 0) <= 0:
        return None
    if await has_purchase_access(int(user_id), chapter_id=int(chapter_id)):
        return {"already_owned": True, "chapter_id": int(chapter_id), "book_id": int(target.get("book_id") or 0)}
    cfg = await load_revenue_split_settings()
    wallet = await get_reader_wallet_balance(int(user_id))
    bonus = await get_bonus_balance(int(user_id))
    discount = bonus_discount_limit(
        int(target["amount_stars"]), bonus,
        points_per_star=cfg.points_per_star,
        author_percent=cfg.author_percent,
        platform_percent=cfg.platform_percent,
        bonus_percent=cfg.bonus_percent,
    )
    return {
        "already_owned": False,
        "chapter_id": int(chapter_id),
        "book_id": int(target.get("book_id") or 0),
        "title": str(target.get("title") or "Глава"),
        "book_title": str(target.get("book_title") or "Книга"),
        "price_stars": int(target["amount_stars"]),
        "wallet_stars": wallet,
        "bonus_points": bonus,
        "points_per_star": cfg.points_per_star,
        **discount,
        "can_buy_with_bonus": wallet >= int(discount["wallet_stars_needed"]),
        "can_buy_without_bonus": wallet >= int(target["amount_stars"]),
    }


async def purchase_chapter_from_wallet(user_id: int, chapter_id: int, *, use_bonus: bool = True) -> dict[str, int]:
    """Atomically buy one text chapter from the prepaid wallet."""
    from app.services.bonus_economy import bonus_discount_limit, load_revenue_split_settings

    cfg = await load_revenue_split_settings()
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            SELECT c.*, b.title AS book_title, b.author_id, b.pricing_type, b.price_stars AS book_price_stars,
                   b.publication_status
            FROM chapters c JOIN books b ON b.id=c.book_id WHERE c.id=?
            """,
            (int(chapter_id),),
        )
        chapter = await cur.fetchone()
        if not chapter or str(chapter["publication_status"] or "") != "published":
            await db.rollback(); raise ValueError("Глава не найдена")
        if str(chapter["pricing_type"] or "") != "chapters" or int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0:
            await db.rollback(); raise ValueError("Эта глава отдельно не продаётся")
        cur = await db.execute(
            "SELECT id FROM purchases WHERE user_id=? AND chapter_id=? AND status='paid' LIMIT 1",
            (int(user_id), int(chapter_id)),
        )
        if await cur.fetchone():
            await db.rollback(); raise ValueError("Глава уже куплена")
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING",
            (int(user_id), now, now),
        )
        await db.execute(
            "INSERT INTO bonus_wallets(user_id,balance,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING",
            (int(user_id), now, now),
        )
        cur = await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (int(user_id),))
        wallet = int((await cur.fetchone())["balance_stars"] or 0)
        cur = await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (int(user_id),))
        bonus = int((await cur.fetchone())["balance"] or 0)
        price = int(chapter["price_stars"] or 0)
        plan = bonus_discount_limit(
            price,
            bonus if use_bonus else 0,
            points_per_star=cfg.points_per_star,
            author_percent=cfg.author_percent,
            platform_percent=cfg.platform_percent,
            bonus_percent=cfg.bonus_percent,
        )
        wallet_needed = int(plan["wallet_stars_needed"])
        bonus_stars = int(plan["bonus_stars_used"])
        bonus_points = bonus_stars * cfg.points_per_star
        if wallet < wallet_needed:
            await db.rollback(); raise ValueError(f"Недостаточно Stars на балансе. Нужно ещё {wallet_needed-wallet}.")
        await db.execute("UPDATE reader_wallets SET balance_stars=balance_stars-?,updated_at=? WHERE user_id=?", (wallet_needed, now, int(user_id)))
        if bonus_points:
            bonus_update = await db.execute(
                "UPDATE bonus_wallets SET balance=balance-?,updated_at=? WHERE user_id=? AND balance>=?",
                (bonus_points, now, int(user_id), bonus_points),
            )
            if bonus_update.rowcount <= 0:
                await db.rollback(); raise ValueError("Бонусный баланс изменился. Повторите покупку.")
        charge_id = f"wallet:{uuid.uuid4().hex}"
        cur = await db.execute(
            """
            INSERT INTO purchases(user_id,book_id,chapter_id,amount_stars,status,telegram_payment_charge_id,created_at,payload,purchase_kind,
                                  original_amount_stars,wallet_stars_used,bonus_points_used,funding_method)
            VALUES(?,?,?,?,'paid',?,?,?,'content',?,?,?,'wallet')
            """,
            (int(user_id), int(chapter["book_id"]), int(chapter_id), price, charge_id, now,
             f"vox:chapter:{int(chapter_id)}", price, wallet_needed, bonus_points),
        )
        purchase_id = int(cur.lastrowid)
        await db.execute(
            "INSERT INTO reader_wallet_transactions(user_id,amount_stars,transaction_type,source_type,source_id,metadata_json,created_at) "
            "VALUES(?,?,'chapter_purchase','purchase',?,?,?)",
            (int(user_id), -wallet_needed, str(purchase_id), json.dumps({"chapter_id": int(chapter_id), "bonus_stars": bonus_stars}, ensure_ascii=False), now),
        )
        if bonus_points:
            await db.execute(
                "INSERT INTO bonus_transactions(user_id,amount,reason,source_type,source_id,created_at) VALUES(?,?,'chapter_purchase_discount','purchase',?,?)",
                (int(user_id), -bonus_points, str(purchase_id), now),
            )
        author_id = chapter["author_id"]
        if author_id is not None:
            hold_days = int(await get_setting("hold_days_default", "14") or 14)
            rate_minor = int(await get_setting("payments_stars_author_rate_minor", "100") or 100)
            available_at = (datetime.now(timezone.utc) + timedelta(days=max(0, hold_days))).isoformat()
            commission = int(plan["platform_stars"] + plan["bonus_pool_stars"])
            await db.execute(
                """
                INSERT INTO author_ledger(author_id,purchase_id,source_type,source_id,gross_stars,commission_percent,
                                          commission_stars,net_stars,settlement_rate_minor,net_minor,hold_days,
                                          available_at,status,created_at,updated_at,platform_stars,bonus_pool_stars,bonus_discount_stars)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'held',?,?,?,?,?)
                """,
                (int(author_id), purchase_id, "chapter", int(chapter_id), price, 100-cfg.author_percent,
                 commission, int(plan["author_stars"]), rate_minor, int(plan["author_stars"])*rate_minor,
                 hold_days, available_at, now, now, int(plan["platform_stars"]), int(plan["bonus_pool_stars"]), bonus_stars),
            )
        await db.commit()
        return {
            "purchase_id": purchase_id,
            "price_stars": price,
            "wallet_stars_used": wallet_needed,
            "bonus_stars_used": bonus_stars,
            "bonus_points_used": bonus_points,
            "wallet_stars": wallet-wallet_needed,
            "bonus_points": bonus-bonus_points,
            "author_stars": int(plan["author_stars"]),
            "platform_stars": max(0, int(plan["platform_stars"] + plan["bonus_pool_stars"])-bonus_stars),
        }


async def get_referral_stats(user_id: int) -> dict[str, int]:
    """Qualified referrals are counted only after a real wallet top-up."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) AS invited,
                   COALESCE(SUM(CASE WHEN topup_count>0 THEN 1 ELSE 0 END),0) AS funded,
                   COALESCE(SUM(topup_count),0) AS topups,
                   COALESCE(SUM(rewarded_bonus_points),0) AS earned_points
            FROM referrals WHERE referrer_user_id=?
            """,
            (int(user_id),),
        )
        row = await cur.fetchone()
        return {
            "invited": int(row["invited"] or 0),
            "rewarded": int(row["funded"] or 0),
            "funded": int(row["funded"] or 0),
            "topups": int(row["topups"] or 0),
            "earned_points": int(row["earned_points"] or 0),
        }


_previous_get_purchase_target_v1200_wallet = get_purchase_target


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    value = str(payload or "")
    if value.startswith("vox:intent:"):
        intent = await get_payment_intent(value)
        if not intent or str(intent["status"] or "") not in {"active", "paid"}:
            return None
        canonical = str(intent["canonical_payload"] or "")
        wallet = await get_purchase_target(canonical)
        return wallet
    parts = value.split(":")
    if len(parts) == 3 and parts[0] == "vox" and parts[1] == "wallet_topup" and parts[2].isdigit():
        amount = int(parts[2])
        if amount < 1 or amount > 10000:
            return None
        # Доступные кнопки проверяются при создании счёта. Здесь сохраняем
        # уже выставленный счёт действительным, даже если владелец позже
        # изменил набор пакетов пополнения.
        return {
            "kind": "wallet_topup",
            "target_id": amount,
            "amount_stars": amount,
            "title": "Баланс VoxLyra",
            "description": f"Пополнение баланса на {amount} Stars",
            "author_id": None,
            "book_id": None,
        }
    return await _previous_get_purchase_target_v1200_wallet(value)

_previous_create_paid_purchase_v1200_split = create_paid_purchase


async def create_paid_purchase(
    *,
    user_id: int,
    payload: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
) -> int:
    """Create a Telegram-funded purchase and normalize its 80/19/1 split.

    Premium, advertising and wallet top-ups have their own accounting paths.
    For author content, the owner-configured split is applied in whole Stars.
    """
    purchase_id = await _previous_create_paid_purchase_v1200_split(
        user_id=int(user_id),
        payload=str(payload),
        amount_stars=int(amount_stars),
        telegram_payment_charge_id=str(telegram_payment_charge_id),
    )
    target = await get_purchase_target(str(payload))
    if not target or str(target.get("kind") or "") in {"premium", "wallet_topup", "ad_budget", "channel_promo"}:
        return purchase_id
    from app.services.bonus_economy import allocate_revenue_stars, load_revenue_split_settings
    cfg = await load_revenue_split_settings()
    split = allocate_revenue_stars(
        int(amount_stars),
        author_percent=cfg.author_percent,
        platform_percent=cfg.platform_percent,
        bonus_percent=cfg.bonus_percent,
    )
    rate_minor = int(await get_setting("payments_stars_author_rate_minor", "100") or 100)
    commission = int(split["platform_stars"] + split["bonus_pool_stars"])
    async with connect() as db:
        await db.execute(
            """
            UPDATE purchases
            SET original_amount_stars=CASE WHEN COALESCE(original_amount_stars,0)=0 THEN amount_stars ELSE original_amount_stars END,
                wallet_stars_used=0, bonus_points_used=0, funding_method='telegram'
            WHERE id=?
            """,
            (int(purchase_id),),
        )
        await db.execute(
            """
            UPDATE author_ledger
            SET commission_percent=?, commission_stars=?, net_stars=?,
                settlement_rate_minor=?, net_minor=?, platform_stars=?, bonus_pool_stars=?, bonus_discount_stars=0,
                updated_at=?
            WHERE purchase_id=? AND source_type!='premium_pool'
            """,
            (
                100-cfg.author_percent,
                commission,
                int(split["author_stars"]),
                rate_minor,
                int(split["author_stars"])*rate_minor,
                int(split["platform_stars"]),
                int(split["bonus_pool_stars"]),
                utc_now(),
                int(purchase_id),
            ),
        )
        await db.commit()
    return purchase_id

async def restore_wallet_purchase_funds(purchase_id: int, user_id: int | None = None) -> dict[str, int]:
    """Restore an internally funded purchase exactly once before finalizing refund."""
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT * FROM purchases WHERE id=?",
            (int(purchase_id),),
        )
        purchase = await cur.fetchone()
        if not purchase or str(purchase["funding_method"] or "") != "wallet":
            await db.rollback()
            raise ValueError("Это не покупка с внутреннего баланса")
        if user_id is not None and int(purchase["user_id"]) != int(user_id):
            await db.rollback()
            raise ValueError("Покупка принадлежит другому пользователю")
        cur = await db.execute(
            "SELECT 1 FROM reader_wallet_transactions WHERE transaction_type='purchase_refund' AND source_type='purchase' AND source_id=? LIMIT 1",
            (str(int(purchase_id)),),
        )
        if await cur.fetchone():
            await db.commit()
            cur = await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (int(purchase["user_id"]),))
            wallet = await cur.fetchone()
            cur = await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (int(purchase["user_id"]),))
            bonus = await cur.fetchone()
            return {
                "wallet_stars_restored": 0,
                "bonus_points_restored": 0,
                "wallet_stars": int(wallet["balance_stars"] or 0) if wallet else 0,
                "bonus_points": int(bonus["balance"] or 0) if bonus else 0,
            }
        if str(purchase["status"] or "") not in {"paid", "canceling"}:
            await db.rollback()
            raise ValueError("Покупка уже обработана")
        wallet_stars = max(0, int(purchase["wallet_stars_used"] or 0))
        bonus_points = max(0, int(purchase["bonus_points_used"] or 0))
        uid = int(purchase["user_id"])
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING",
            (uid, now, now),
        )
        await db.execute(
            "UPDATE reader_wallets SET balance_stars=balance_stars+?,updated_at=? WHERE user_id=?",
            (wallet_stars, now, uid),
        )
        await db.execute(
            "INSERT INTO reader_wallet_transactions(user_id,amount_stars,transaction_type,source_type,source_id,metadata_json,created_at) VALUES(?,?,'purchase_refund','purchase',?,?,?)",
            (uid, wallet_stars, str(int(purchase_id)), json.dumps({"bonus_points": bonus_points}, ensure_ascii=False), now),
        )
        if bonus_points:
            await db.execute(
                "INSERT INTO bonus_wallets(user_id,balance,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING",
                (uid, now, now),
            )
            await db.execute(
                "UPDATE bonus_wallets SET balance=balance+?,updated_at=? WHERE user_id=?",
                (bonus_points, now, uid),
            )
            await db.execute(
                "INSERT INTO bonus_transactions(user_id,amount,reason,source_type,source_id,created_at) VALUES(?,?,'chapter_purchase_refund','purchase',?,?)",
                (uid, bonus_points, str(int(purchase_id)), now),
            )
        await db.commit()
        cur = await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (uid,))
        wallet = await cur.fetchone()
        cur = await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (uid,))
        bonus = await cur.fetchone()
        return {
            "wallet_stars_restored": wallet_stars,
            "bonus_points_restored": bonus_points,
            "wallet_stars": int(wallet["balance_stars"] or 0) if wallet else 0,
            "bonus_points": int(bonus["balance"] or 0) if bonus else 0,
        }


async def list_refund_requests(status: str = "new", limit: int = 30) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rr.*, p.amount_stars, p.telegram_payment_charge_id, p.funding_method,
                   p.wallet_stars_used, p.bonus_points_used, u.telegram_id, u.username, u.full_name,
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
            (str(status), max(1, int(limit))),
        )
        return await cur.fetchall()


async def get_refund_request(refund_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rr.*, p.amount_stars, p.telegram_payment_charge_id, p.status AS purchase_status,
                   p.funding_method, p.wallet_stars_used, p.bonus_points_used,
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
            (int(refund_id),),
        )
        return await cur.fetchone()


async def list_user_purchases(user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
    """Content purchases only; wallet top-ups are shown in wallet history."""
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
            WHERE p.user_id=? AND COALESCE(p.purchase_kind,'content')!='wallet_topup'
            ORDER BY p.id DESC LIMIT ?
            """,
            (int(user_id), max(1, int(limit))),
        )
        return await cur.fetchall()


async def get_platform_finance_summary() -> dict[str, int]:
    """Financial summary without double-counting prepaid wallet spending.

    Telegram inflow counts wallet top-ups and direct Telegram purchases once.
    Purchases later paid from the internal wallet are content turnover, but are
    not a second external receipt of Stars.
    """
    async with connect() as db:
        await _release_ready_author_ledger(db)
        await db.commit()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE
                    WHEN status='paid' AND COALESCE(funding_method,'telegram')!='wallet'
                    THEN amount_stars ELSE 0 END), 0) AS telegram_inflow_stars,
                COALESCE(SUM(CASE
                    WHEN status='refunded' AND COALESCE(funding_method,'telegram')!='wallet'
                    THEN amount_stars ELSE 0 END), 0) AS telegram_refunded_stars,
                COUNT(CASE
                    WHEN status='paid' AND COALESCE(funding_method,'telegram')!='wallet'
                    THEN 1 END) AS external_paid_count,
                COUNT(CASE
                    WHEN status='refunded' AND COALESCE(funding_method,'telegram')!='wallet'
                    THEN 1 END) AS external_refunded_count,
                COALESCE(SUM(CASE
                    WHEN status='paid' AND COALESCE(purchase_kind,'content')!='wallet_topup'
                    THEN CASE WHEN COALESCE(original_amount_stars,0)>0
                              THEN original_amount_stars ELSE amount_stars END
                    ELSE 0 END), 0) AS content_sales_stars,
                COALESCE(SUM(CASE
                    WHEN status='paid' AND COALESCE(purchase_kind,'content')='wallet_topup'
                    THEN amount_stars ELSE 0 END), 0) AS wallet_topup_stars,
                COALESCE(SUM(CASE
                    WHEN status='paid' AND COALESCE(funding_method,'telegram')='wallet'
                    THEN wallet_stars_used ELSE 0 END), 0) AS wallet_spent_stars
            FROM purchases
            """
        )
        purchases = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN status!='refunded' THEN
                    CASE
                      WHEN COALESCE(platform_stars,0)=0 AND COALESCE(bonus_pool_stars,0)=0
                      THEN commission_stars
                      ELSE platform_stars
                    END ELSE 0 END), 0) AS platform_commission,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN bonus_pool_stars ELSE 0 END), 0) AS bonus_pool_stars,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN bonus_discount_stars ELSE 0 END), 0) AS bonus_discount_stars,
                COALESCE(SUM(CASE WHEN status!='refunded' THEN commission_stars ELSE 0 END), 0) AS non_author_total,
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
        cur = await db.execute("SELECT COALESCE(SUM(balance_stars),0) AS total FROM reader_wallets")
        wallet = await cur.fetchone()
        cur = await db.execute("SELECT COALESCE(SUM(balance),0) AS total FROM bonus_wallets")
        bonuses = await cur.fetchone()
        points_per_star = 100
        return {
            "paid_gross": int(purchases["telegram_inflow_stars"] or 0),
            "telegram_inflow_stars": int(purchases["telegram_inflow_stars"] or 0),
            "refunded_gross": int(purchases["telegram_refunded_stars"] or 0),
            "paid_count": int(purchases["external_paid_count"] or 0),
            "refunded_count": int(purchases["external_refunded_count"] or 0),
            "content_sales_stars": int(purchases["content_sales_stars"] or 0),
            "wallet_topup_stars": int(purchases["wallet_topup_stars"] or 0),
            "wallet_spent_stars": int(purchases["wallet_spent_stars"] or 0),
            "wallet_liability_stars": int(wallet["total"] or 0),
            "bonus_liability_points": int(bonuses["total"] or 0),
            "bonus_liability_stars": int(bonuses["total"] or 0) // points_per_star,
            "platform_commission": int(ledger["platform_commission"] or 0),
            "bonus_pool_stars": int(ledger["bonus_pool_stars"] or 0),
            "bonus_discount_stars": int(ledger["bonus_discount_stars"] or 0),
            "non_author_total": int(ledger["non_author_total"] or 0),
            "held_authors": int(ledger["held_authors"] or 0),
            "available_authors": int(ledger["available_authors"] or 0),
            "requested_authors": int(ledger["requested_authors"] or 0),
            "paid_authors": int(ledger["paid_authors"] or 0),
            "payout_requests_open": int(payouts["cnt"] or 0),
        }


async def claim_daily_bonus(user_id: int) -> tuple[bool, int, int]:
    """Compatibility stub: daily rewards were permanently removed in v1.12.0."""
    return False, 0, await get_bonus_balance(int(user_id))
