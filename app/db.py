import asyncio
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from difflib import SequenceMatcher
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from app.config import settings
from app.catalog_options import label_for
from app.permissions import DELEGABLE_PERMISSION_CODES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_book_search_text(value: object | None) -> str:
    """Canonical text used by every book search surface.

    It removes punctuation and combining marks, treats ``ё`` as ``е`` and
    collapses whitespace.  The same transformation is registered in SQLite so
    server-side and browser-side searches cannot disagree on Russian titles.
    """
    text = str(value or "").casefold().replace("ё", "е")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).replace("_", " ")
    return " ".join(text.split())


def _normalize_progress_timestamp(value: object | None) -> str:
    """Normalize an optional client timestamp for stale offline-progress protection.

    Client clocks are not trusted beyond five minutes into the future, otherwise a
    broken device clock could permanently block progress from another device.
    """
    now = datetime.now(timezone.utc)
    if value is None or not str(value).strip():
        return now.isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return now.isoformat()
    if parsed > now + timedelta(minutes=5):
        parsed = now
    return parsed.isoformat()


@asynccontextmanager
async def connect():
    db_path = settings.DATABASE_PATH
    folder = os.path.dirname(db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    db = await aiosqlite.connect(db_path)
    db.row_factory = aiosqlite.Row
    # SQLite lower()/NOCASE handle ASCII only. The project stores Russian,
    # Japanese and other author names, so searches need a Unicode-aware fold.
    await db.create_function("unicode_casefold", 1, lambda value: str(value or "").casefold())
    await db.create_function("book_search_normalize", 1, normalize_book_search_text)
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute(f"PRAGMA busy_timeout = {max(1000, int(settings.DB_BUSY_TIMEOUT_MS or 15000))}")
    configured_cache_mb = max(4, int(settings.DB_CACHE_MB or 8))
    if bool(getattr(settings, "DB_LOW_MEMORY_MODE", True)):
        configured_cache_mb = min(configured_cache_mb, 8)
    await db.execute(f"PRAGMA cache_size = {-configured_cache_mb * 1024}")
    await db.execute("PRAGMA temp_store = FILE" if bool(getattr(settings, "DB_LOW_MEMORY_MODE", True)) else "PRAGMA temp_store = MEMORY")
    if bool(getattr(settings, "DB_LOW_MEMORY_MODE", True)):
        await db.execute("PRAGMA mmap_size = 0")
    await db.execute("PRAGMA synchronous = NORMAL")
    await db.execute(
        f"PRAGMA wal_autocheckpoint = {max(100, int(settings.DB_WAL_AUTOCHECKPOINT_PAGES or 2000))}"
    )
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
        # WAL allows readers to continue while the import/moderation worker writes.
        # The settings are persistent and safe to repeat on every Redeploy.
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        await db.execute(
            f"PRAGMA wal_autocheckpoint = {max(100, int(settings.DB_WAL_AUTOCHECKPOINT_PAGES or 2000))}"
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL UNIQUE,
                username TEXT,
                full_name TEXT,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                account_status TEXT NOT NULL DEFAULT 'active',
                deleted_at TEXT,
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
        await _ensure_v11319_reader_subscription_schema(db)
        await _ensure_v11320_library_schema(db)
        await _ensure_v11321_reading_stats_schema(db)
        await _ensure_v11322_reading_notification_schema(db)
        await _ensure_v11323_monthly_reading_schema(db)
        await _ensure_v11325_reading_journal_schema(db)
        await _ensure_v11326_reread_schema(db)
        await _ensure_v11327_journal_import_schema(db)
        await _ensure_v11328_journal_import_history_schema(db)
        await _ensure_v11332_moderation_revision_schema(db)
        await _ensure_v11333_monetization_schema(db)
        await _ensure_v11336_privacy_schema(db)
        await _ensure_v11337_performance_schema(db)
        await _ensure_v114015_catalog_promotion_schema(db)
        await db.execute("PRAGMA optimize")
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

        CREATE TABLE IF NOT EXISTS achievement_showcase (
            user_id INTEGER NOT NULL,
            position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 3),
            achievement_code TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, position),
            UNIQUE(user_id, achievement_code),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_user_achievements_user_awarded
            ON user_achievements(user_id, awarded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_achievement_showcase_user_position
            ON achievement_showcase(user_id, position);
        CREATE INDEX IF NOT EXISTS idx_smart_notifications_last_sent
            ON smart_notification_state(notification_code, last_sent_at);
        """
    )


async def _ensure_v11319_reader_subscription_schema(db: aiosqlite.Connection) -> None:
    """v1.13.19: явные подписки читателей и режим уведомлений только по подпискам."""
    cur = await db.execute("PRAGMA table_info(user_preferences)")
    existing = {row[1] for row in await cur.fetchall()}
    if "notifications_followed_only" not in existing:
        await _execute_schema_ddl(
            db,
            "ALTER TABLE user_preferences ADD COLUMN notifications_followed_only INTEGER NOT NULL DEFAULT 1",
        )

    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS book_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            notify_chapters INTEGER NOT NULL DEFAULT 1,
            notify_audio INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, book_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS author_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            author_id INTEGER NOT NULL,
            notify_new_books INTEGER NOT NULL DEFAULT 1,
            notify_chapters INTEGER NOT NULL DEFAULT 1,
            notify_audio INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, author_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(author_id) REFERENCES author_profiles(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_book_subscriptions_book
            ON book_subscriptions(book_id, notify_chapters, notify_audio);
        CREATE INDEX IF NOT EXISTS idx_book_subscriptions_user
            ON book_subscriptions(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_author_subscriptions_author
            ON author_subscriptions(author_id, notify_new_books, notify_chapters, notify_audio);
        CREATE INDEX IF NOT EXISTS idx_author_subscriptions_user
            ON author_subscriptions(user_id, updated_at DESC);
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
                username=CASE WHEN users.account_status='deleted' THEN users.username ELSE excluded.username END,
                full_name=CASE WHEN users.account_status='deleted' THEN users.full_name ELSE excluded.full_name END,
                updated_at=CASE WHEN users.account_status='deleted' THEN users.updated_at ELSE excluded.updated_at END
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


async def update_book_cover_file_id(book_id: int, author_user_id: int, cover_file_id: str) -> bool:
    """Save a newly uploaded Telegram cover for a book owned by the author."""
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            UPDATE books
            SET cover_file_id=?, updated_at=?
            WHERE id=?
              AND publication_status!='deleted'
              AND author_id=(SELECT id FROM author_profiles WHERE user_id=?)
            """,
            (str(cover_file_id), now, int(book_id), int(author_user_id)),
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


async def save_listening_progress(
    user_id: int,
    audio_chapter_id: int,
    position_seconds: int,
    *,
    client_updated_at: object | None = None,
    protect_newer: bool = False,
) -> bool:
    now = _normalize_progress_timestamp(client_updated_at) if protect_newer else utc_now()
    async with connect() as db:
        position = max(0, int(position_seconds))
        cur = await db.execute(
            """
            INSERT INTO listening_progress(user_id, audio_chapter_id, position_seconds, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, audio_chapter_id) DO UPDATE SET
                position_seconds=excluded.position_seconds,
                updated_at=excluded.updated_at
            WHERE ?=0 OR listening_progress.updated_at<=excluded.updated_at
            """,
            (int(user_id), int(audio_chapter_id), position, now, 1 if protect_newer else 0),
        )
        if int(cur.rowcount or 0) <= 0:
            await db.commit()
            return False
        cur = await db.execute("SELECT book_id FROM audio_chapters WHERE id=?", (int(audio_chapter_id),))
        audio = await cur.fetchone()
        if audio:
            await _record_history_db(
                db, user_id=int(user_id), content_type="audio", target_id=int(audio_chapter_id),
                book_id=int(audio["book_id"]), position_value=position, updated_at=now,
            )
            await _record_reader_activity_db(
                db, user_id=int(user_id), content_type="audio", target_id=int(audio_chapter_id),
                position_value=position, updated_at=now,
            )
            await _touch_progress_revision_db(db, int(user_id), now)
        await db.commit()
        return True


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


async def list_book_notification_recipients(
    book_id: int,
    category: str = "chapters",
    limit: int = 5000,
) -> list[aiosqlite.Row]:
    """Получатели события: явные подписчики, либо прежние читатели при разрешённом широком режиме."""
    normalized_category = str(category or "chapters").strip().lower()
    book_flag = "notify_audio" if normalized_category == "audio" else "notify_chapters"
    author_flag = "notify_audio" if normalized_category == "audio" else "notify_chapters"
    async with connect() as db:
        cur = await db.execute(
            f"""
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
                   (SELECT bm.status FROM bookmarks bm WHERE bm.user_id=u.id AND bm.book_id=? LIMIT 1) AS bookmark_status,
                   EXISTS(
                       SELECT 1 FROM book_subscriptions bs
                       WHERE bs.user_id=u.id AND bs.book_id=? AND bs.{book_flag}=1
                   ) AS book_subscribed,
                   EXISTS(
                       SELECT 1 FROM author_subscriptions aus
                       JOIN books source_book ON source_book.author_id=aus.author_id
                       WHERE aus.user_id=u.id AND source_book.id=? AND aus.{author_flag}=1
                   ) AS author_subscribed
            FROM users u
            LEFT JOIN user_preferences pref ON pref.user_id=u.id
            WHERE u.is_blocked=0
              AND u.id != COALESCE((
                  SELECT ap.user_id FROM books b
                  LEFT JOIN author_profiles ap ON ap.id=b.author_id
                  WHERE b.id=?
              ), -1)
              AND (
                  EXISTS(
                      SELECT 1 FROM book_subscriptions bs
                      WHERE bs.user_id=u.id AND bs.book_id=? AND bs.{book_flag}=1
                  )
                  OR EXISTS(
                      SELECT 1 FROM author_subscriptions aus
                      JOIN books source_book ON source_book.author_id=aus.author_id
                      WHERE aus.user_id=u.id AND source_book.id=? AND aus.{author_flag}=1
                  )
                  OR (
                      COALESCE(pref.notifications_followed_only, 1)=0
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
                  )
              )
            ORDER BY u.id
            LIMIT ?
            """,
            (
                int(book_id), int(book_id), int(book_id), int(book_id), int(book_id), int(book_id),
                int(book_id), int(book_id), int(book_id), int(book_id), int(book_id), int(book_id),
                int(book_id), int(book_id), int(book_id), int(book_id), int(limit),
            ),
        )
        return await cur.fetchall()


async def list_author_notification_recipients(author_id: int, limit: int = 5000) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT DISTINCT u.id, u.telegram_id, u.username, u.full_name
            FROM author_subscriptions aus
            JOIN users u ON u.id=aus.user_id AND u.is_blocked=0
            JOIN author_profiles ap ON ap.id=aus.author_id
            WHERE aus.author_id=? AND aus.notify_new_books=1 AND u.id != ap.user_id
            ORDER BY u.id
            LIMIT ?
            """,
            (int(author_id), int(limit)),
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


async def save_reading_progress(
    user_id: int,
    chapter_id: int,
    position_percent: int,
    *,
    client_updated_at: object | None = None,
    protect_newer: bool = False,
) -> bool:
    position_percent = max(0, min(100, int(position_percent)))
    now = _normalize_progress_timestamp(client_updated_at) if protect_newer else utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT book_id FROM chapters WHERE id=?", (int(chapter_id),))
        chapter = await cur.fetchone()
        if not chapter:
            return False
        book_id = int(chapter["book_id"])
        cur = await db.execute(
            """
            INSERT INTO reading_progress(user_id, book_id, chapter_id, position_percent, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(user_id, chapter_id) DO UPDATE SET
                book_id=excluded.book_id,
                position_percent=excluded.position_percent,
                updated_at=excluded.updated_at
            WHERE ?=0 OR reading_progress.updated_at<=excluded.updated_at
            """,
            (int(user_id), book_id, int(chapter_id), position_percent, now, 1 if protect_newer else 0),
        )
        if int(cur.rowcount or 0) <= 0:
            await db.commit()
            return False
        await _record_history_db(
            db, user_id=int(user_id), content_type="text", target_id=int(chapter_id),
            book_id=book_id, position_value=position_percent, updated_at=now,
        )
        await _record_reader_activity_db(
            db, user_id=int(user_id), content_type="text", target_id=int(chapter_id),
            position_value=position_percent, updated_at=now,
        )
        await _touch_progress_revision_db(db, int(user_id), now)
        await db.commit()
        return True


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
        journal_status = "reading" if status == "favorite" else status
        started_on = None if status == "planned" else now[:10]
        finished_on = now[:10] if status == "finished" else None
        await db.execute(
            """
            INSERT INTO reader_book_journal(
                user_id, book_id, status, started_on, finished_on, impression,
                private_rating, last_activity_at, created_at, updated_at
            ) VALUES(?,?,?,?,?,'',0,NULL,?,?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                status=CASE WHEN ?=1 THEN reader_book_journal.status ELSE excluded.status END,
                started_on=COALESCE(reader_book_journal.started_on, excluded.started_on),
                finished_on=CASE
                    WHEN excluded.status='finished' THEN COALESCE(reader_book_journal.finished_on, excluded.finished_on)
                    ELSE reader_book_journal.finished_on
                END,
                updated_at=excluded.updated_at
            """,
            (user_id, book_id, journal_status, started_on, finished_on, now, now, 1 if status == "favorite" else 0),
        )
        if status != 'favorite' and status != 'planned':
            cur = await db.execute(
                'SELECT * FROM reader_book_cycles WHERE user_id=? AND book_id=? ORDER BY cycle_number DESC LIMIT 1',
                (int(user_id), int(book_id)),
            )
            latest_cycle = await cur.fetchone()
            cycle_status = journal_status if journal_status in {'reading','finished','dropped'} else 'reading'
            if latest_cycle:
                await db.execute(
                    """UPDATE reader_book_cycles SET status=?, finished_on=CASE WHEN ?='finished' THEN COALESCE(finished_on, ?) ELSE finished_on END, updated_at=? WHERE id=?""",
                    (cycle_status, cycle_status, finished_on, now, int(latest_cycle['id'])),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO reader_book_cycles(user_id,book_id,cycle_number,status,started_on,finished_on,note,created_at,updated_at)
                    VALUES(?,?,1,?,?,?,?,?,?)
                    """,
                    (int(user_id), int(book_id), cycle_status, started_on, finished_on if cycle_status == 'finished' else None, '', now, now),
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


async def list_user_continue_graphics(user_id: int, limit: int = 12) -> list[aiosqlite.Row]:
    """Последняя открытая опубликованная графическая глава каждой книги."""
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT grp.*, gc.book_id, gc.title AS graphic_title, gc.number AS graphic_number,
                   gc.pages_count, b.title, b.cover_path, b.cover_file_id, b.updated_at AS book_updated_at, ap.pen_name
            FROM graphic_reading_progress grp
            JOIN graphic_chapters gc ON gc.id=grp.graphic_chapter_id AND gc.status='published'
            JOIN books b ON b.id=gc.book_id AND b.publication_status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE grp.user_id=?
              AND grp.updated_at=(
                SELECT MAX(grp2.updated_at)
                FROM graphic_reading_progress grp2
                JOIN graphic_chapters gc2 ON gc2.id=grp2.graphic_chapter_id
                WHERE grp2.user_id=grp.user_id AND gc2.book_id=gc.book_id
              )
            ORDER BY grp.updated_at DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        )
        return await cur.fetchall()


async def get_book_subscription(user_id: int, book_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM book_subscriptions WHERE user_id=? AND book_id=?",
            (int(user_id), int(book_id)),
        )
        return await cur.fetchone()


async def set_book_subscription(
    user_id: int,
    book_id: int,
    *,
    enabled: bool,
    notify_chapters: bool = True,
    notify_audio: bool = True,
) -> aiosqlite.Row | None:
    now = utc_now()
    async with connect() as db:
        if not enabled:
            await db.execute(
                "DELETE FROM book_subscriptions WHERE user_id=? AND book_id=?",
                (int(user_id), int(book_id)),
            )
            await db.commit()
            return None
        await db.execute(
            """
            INSERT INTO book_subscriptions(
                user_id, book_id, notify_chapters, notify_audio, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                notify_chapters=excluded.notify_chapters,
                notify_audio=excluded.notify_audio,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id), int(book_id), 1 if notify_chapters else 0,
                1 if notify_audio else 0, now, now,
            ),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT * FROM book_subscriptions WHERE user_id=? AND book_id=?",
            (int(user_id), int(book_id)),
        )
        return await cur.fetchone()


async def get_author_subscription(user_id: int, author_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM author_subscriptions WHERE user_id=? AND author_id=?",
            (int(user_id), int(author_id)),
        )
        return await cur.fetchone()


async def set_author_subscription(
    user_id: int,
    author_id: int,
    *,
    enabled: bool,
    notify_new_books: bool = True,
    notify_chapters: bool = True,
    notify_audio: bool = True,
) -> aiosqlite.Row | None:
    now = utc_now()
    async with connect() as db:
        if not enabled:
            await db.execute(
                "DELETE FROM author_subscriptions WHERE user_id=? AND author_id=?",
                (int(user_id), int(author_id)),
            )
            await db.commit()
            return None
        await db.execute(
            """
            INSERT INTO author_subscriptions(
                user_id, author_id, notify_new_books, notify_chapters, notify_audio, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, author_id) DO UPDATE SET
                notify_new_books=excluded.notify_new_books,
                notify_chapters=excluded.notify_chapters,
                notify_audio=excluded.notify_audio,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id), int(author_id), 1 if notify_new_books else 0,
                1 if notify_chapters else 0, 1 if notify_audio else 0, now, now,
            ),
        )
        await db.commit()
        cur = await db.execute(
            "SELECT * FROM author_subscriptions WHERE user_id=? AND author_id=?",
            (int(user_id), int(author_id)),
        )
        return await cur.fetchone()


async def list_user_subscriptions(user_id: int, limit: int = 100) -> dict[str, list[aiosqlite.Row]]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT bs.*, b.title, b.description, b.age_limit, b.cover_path, b.cover_file_id,
                   b.updated_at AS book_updated_at, ap.pen_name, b.author_id,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') AS chapters_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status='published') AS audio_count,
                   (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published') AS graphic_count
            FROM book_subscriptions bs
            JOIN books b ON b.id=bs.book_id AND b.publication_status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE bs.user_id=?
            ORDER BY bs.updated_at DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        )
        books = await cur.fetchall()
        cur = await db.execute(
            """
            SELECT aus.*, ap.pen_name, ap.avatar_file_id, ap.bio,
                   (SELECT COUNT(*) FROM books b WHERE b.author_id=ap.id AND b.publication_status='published') AS books_count
            FROM author_subscriptions aus
            JOIN author_profiles ap ON ap.id=aus.author_id AND ap.status IN ('active', 'approved')
            WHERE aus.user_id=?
            ORDER BY aus.updated_at DESC
            LIMIT ?
            """,
            (int(user_id), int(limit)),
        )
        authors = await cur.fetchall()
        return {"books": books, "authors": authors}


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
    mode = _normalize_text_pricing_mode(
        int(audio["book_price_stars"] or 0), str(audio["pricing_type"] or "")
    )
    if mode == "free" or int(audio["is_free"] or 0) == 1:
        return True
    if mode == "premium":
        return await user_has_premium(int(user_id))
    return await has_purchase_access(int(user_id), audio_chapter_id=int(audio_chapter_id))



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
    clean = query.strip().lstrip("@")
    q = f"%{clean.casefold()}%"
    exact_database_id = int(clean) if clean.isdigit() else -1
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT u.*, ap.pen_name
            FROM users u
            LEFT JOIN author_profiles ap ON ap.user_id = u.id
            WHERE u.id = ?
               OR unicode_casefold(COALESCE(u.username,'')) LIKE ?
               OR unicode_casefold(COALESCE(u.full_name,'')) LIKE ?
               OR CAST(u.telegram_id AS TEXT) LIKE ?
               OR unicode_casefold(COALESCE(ap.pen_name,'')) LIKE ?
            ORDER BY CASE WHEN u.id=? THEN 0 ELSE 1 END, u.id DESC
            LIMIT ?
            """,
            (exact_database_id, q, q, q, q, exact_database_id, limit),
        )
        return await cur.fetchall()


async def search_books(query: str, limit: int = 20) -> list[aiosqlite.Row]:
    clean = query.strip().lstrip("@")
    q = f"%{clean.casefold()}%"
    exact_book_id = int(clean) if clean.isdigit() else -1
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.*, COALESCE(ap.pen_name, b.source_author_name) AS pen_name,
                   (SELECT c.id FROM chapters c WHERE c.book_id=b.id ORDER BY c.number, c.id LIMIT 1) AS first_chapter_id,
                   (SELECT gc.id FROM graphic_chapters gc WHERE gc.book_id=b.id ORDER BY gc.number, gc.id LIMIT 1) AS first_graphic_chapter_id
            FROM books b
            LEFT JOIN author_profiles ap ON ap.id = b.author_id
            WHERE b.id = ?
               OR unicode_casefold(COALESCE(b.title,'')) LIKE ?
               OR unicode_casefold(COALESCE(b.description,'')) LIKE ?
               OR unicode_casefold(COALESCE(ap.pen_name,'')) LIKE ?
               OR unicode_casefold(COALESCE(b.source_author_name,'')) LIKE ?
            ORDER BY CASE WHEN b.id=? THEN 0 ELSE 1 END, b.id DESC
            LIMIT ?
            """,
            (exact_book_id, q, q, q, q, exact_book_id, limit),
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
    "notifications_followed_only": "1",
}


async def get_user_preferences(user_id: int) -> dict[str, str]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT theme, font_size, notifications, notifications_chapters,
                   notifications_audio, notifications_discounts,
                   notifications_reminders, notifications_achievements,
                   notifications_followed_only
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
                notifications_achievements, notifications_followed_only, updated_at
            )
            VALUES(?, 'system', 'normal', 1, 1, 1, 1, 1, 1, 1, ?)
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


async def _ensure_v11332_moderation_revision_schema(db: aiosqlite.Connection) -> None:
    """Версионные снимки модерации и безопасная повторная проверка только изменённых частей."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS book_moderation_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            snapshot_kind TEXT NOT NULL,
            actor_user_id INTEGER,
            source TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_book_moderation_snapshots_book
            ON book_moderation_snapshots(book_id, id DESC);

        CREATE TABLE IF NOT EXISTS book_moderation_snapshot_items (
            snapshot_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id INTEGER,
            field_name TEXT NOT NULL DEFAULT '',
            item_number INTEGER,
            title TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            updated_at TEXT,
            PRIMARY KEY(snapshot_id, item_key),
            FOREIGN KEY(snapshot_id) REFERENCES book_moderation_snapshots(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_book_moderation_snapshot_items_source
            ON book_moderation_snapshot_items(snapshot_id, source_type, source_id);

        CREATE TABLE IF NOT EXISTS book_revision_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            baseline_snapshot_id INTEGER NOT NULL,
            actor_user_id INTEGER,
            reason TEXT NOT NULL,
            finding_ids_json TEXT NOT NULL DEFAULT '[]',
            requires_manual_confirmation INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            resubmitted_at TEXT,
            resolved_at TEXT,
            resolution TEXT,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(baseline_snapshot_id) REFERENCES book_moderation_snapshots(id) ON DELETE CASCADE,
            FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_book_revision_requests_book
            ON book_revision_requests(book_id, status, id DESC);
        """
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


async def save_graphic_reading_progress(
    user_id: int,
    graphic_chapter_id: int,
    page_number: int,
    *,
    client_updated_at: object | None = None,
    protect_newer: bool = False,
) -> bool:
    now = _normalize_progress_timestamp(client_updated_at) if protect_newer else utc_now()
    async with connect() as db:
        page = max(1, int(page_number or 1))
        cur = await db.execute(
            """
            INSERT INTO graphic_reading_progress(user_id, graphic_chapter_id, page_number, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, graphic_chapter_id) DO UPDATE SET
                page_number=excluded.page_number,
                updated_at=excluded.updated_at
            WHERE ?=0 OR graphic_reading_progress.updated_at<=excluded.updated_at
            """,
            (int(user_id), int(graphic_chapter_id), page, now, 1 if protect_newer else 0),
        )
        if int(cur.rowcount or 0) <= 0:
            await db.commit()
            return False
        cur = await db.execute("SELECT book_id FROM graphic_chapters WHERE id=?", (int(graphic_chapter_id),))
        graphic = await cur.fetchone()
        if graphic:
            await _record_history_db(
                db, user_id=int(user_id), content_type="graphic", target_id=int(graphic_chapter_id),
                book_id=int(graphic["book_id"]), position_value=page, updated_at=now,
            )
            await _record_reader_activity_db(
                db, user_id=int(user_id), content_type="graphic", target_id=int(graphic_chapter_id),
                position_value=page, updated_at=now,
            )
            await _touch_progress_revision_db(db, int(user_id), now)
        await db.commit()
        return True


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
    mode = _normalize_text_pricing_mode(
        int(chapter["book_price_stars"] or 0), str(chapter["book_pricing_type"] or "")
    )
    if mode == "free" or int(chapter["is_free"] or 0) == 1:
        return True
    if mode == "premium":
        return await user_has_premium(int(user_id))
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


_ACHIEVEMENT_CATALOG: dict[str, dict[str, Any]] = {
    "first_chapter": {
        "title": "Первая глава", "description": "Прочитать первую главу до конца.", "icon": "📖", "icon_asset": "/media/achievements/first_chapter.png", "group": "reader", "category": "reading", "rarity": "common", "goal": 1
    },
    "hundred_chapters": {
        "title": "100 глав", "description": "Прочитать сто глав до конца.", "icon": "💯", "icon_asset": "/media/achievements/hundred_chapters.png", "group": "reader", "category": "reading", "rarity": "epic", "goal": 100
    },
    "night_reader": {
        "title": "Ночной читатель", "description": "Читать поздним вечером или ночью.", "icon": "🌙", "icon_asset": "/media/achievements/night_reader.png", "group": "reader", "category": "reading", "rarity": "rare", "goal": 1
    },
    "collector": {
        "title": "Коллекционер", "description": "Сохранить десять произведений в библиотеке.", "icon": "📚", "icon_asset": "/media/achievements/collector.png", "group": "reader", "category": "reading", "rarity": "rare", "goal": 10
    },
    "first_review": {
        "title": "Первый отзыв", "description": "Оставить первый опубликованный отзыв.", "icon": "⭐", "icon_asset": "/media/achievements/first_review.png", "group": "reader", "category": "community", "rarity": "common", "goal": 1
    },
    "first_comment": {
        "title": "Первый комментарий", "description": "Опубликовать первый комментарий к главе.", "icon": "💬", "icon_asset": "/media/achievements/first_comment.png", "group": "reader", "category": "community", "rarity": "common", "goal": 1
    },
    "reading_streak_7": {
        "title": "Серия 7 дней", "description": "Читать, слушать или смотреть комиксы семь дней подряд.", "icon": "🔥", "icon_asset": "/media/achievements/reading_streak_7.png", "group": "reader", "category": "reading", "rarity": "rare", "goal": 7
    },
    "reading_streak_30": {
        "title": "Верный читатель", "description": "Сохранять активную серию тридцать дней подряд.", "icon": "✦", "icon_asset": "/media/achievements/reading_streak_30.png", "group": "reader", "category": "reading", "rarity": "legendary", "goal": 30
    },
    "audio_hour": {
        "title": "Час аудио", "description": "Прослушать суммарно шестьдесят минут аудиокниг.", "icon": "🎧", "icon_asset": "/media/achievements/audio_hour.png", "group": "reader", "category": "audio", "rarity": "rare", "goal": 60
    },
    "comic_explorer": {
        "title": "Исследователь комиксов", "description": "Открыть первую графическую главу.", "icon": "🖼", "icon_asset": "/media/achievements/comic_explorer.png", "group": "reader", "category": "comic", "rarity": "common", "goal": 1
    },
    "comic_hundred_pages": {
        "title": "100 страниц комиксов", "description": "Просмотреть сто страниц комиксов, манги или манхвы.", "icon": "💠", "icon_asset": "/media/achievements/comic_hundred_pages.png", "group": "reader", "category": "comic", "rarity": "epic", "goal": 100
    },
    "first_note": {
        "title": "Первая заметка", "description": "Сохранить первую личную заметку во время чтения.", "icon": "🖋", "icon_asset": "/media/achievements/first_note.png", "group": "reader", "category": "reading", "rarity": "common", "goal": 1
    },
    "quote_collector": {
        "title": "Хранитель цитат", "description": "Сохранить десять цитат из произведений.", "icon": "📜", "icon_asset": "/media/achievements/quote_collector.png", "group": "reader", "category": "reading", "rarity": "rare", "goal": 10
    },
    "premium_member": {
        "title": "VoxLyra Premium", "description": "Впервые оформить Premium и получить премиальный знак.", "icon": "👑", "icon_asset": "/media/achievements/premium_member.png", "group": "reader", "category": "premium", "rarity": "legendary", "goal": 1
    },
    "first_book": {
        "title": "Первая книга", "description": "Опубликовать первое произведение.", "icon": "✍️", "icon_asset": "/media/achievements/first_book.png", "group": "author", "category": "author", "rarity": "rare", "goal": 1
    },
    "author_ten_chapters": {
        "title": "Автор 10 глав", "description": "Опубликовать десять текстовых или графических глав.", "icon": "🪶", "icon_asset": "/media/achievements/author_ten_chapters.png", "group": "author", "category": "author", "rarity": "rare", "goal": 10
    },
    "author_hundred_chapters": {
        "title": "Автор 100 глав", "description": "Опубликовать сто текстовых или графических глав.", "icon": "🏛", "icon_asset": "/media/achievements/author_hundred_chapters.png", "group": "author", "category": "author", "rarity": "epic", "goal": 100
    },
    "author_ten_books": {
        "title": "Автор 10 книг", "description": "Опубликовать десять самостоятельных произведений.", "icon": "📚", "icon_asset": "/media/achievements/author_ten_books.png", "group": "author", "category": "author", "rarity": "epic", "goal": 10
    },
    "author_hundred_reactions": {
        "title": "100 реакций читателей", "description": "Получить сто реакций на опубликованные главы.", "icon": "💜", "icon_asset": "/media/achievements/author_hundred_reactions.png", "group": "author", "category": "author", "rarity": "epic", "goal": 100
    },
    "thousand_readers": {
        "title": "1000 читателей", "description": "Собрать тысячу уникальных читателей.", "icon": "👥", "icon_asset": "/media/achievements/thousand_readers.png", "group": "author", "category": "author", "rarity": "legendary", "goal": 1000
    },
    "author_month": {
        "title": "Автор месяца", "description": "Стать первым по уникальным читателям месяца.", "icon": "🏆", "icon_asset": "/media/achievements/author_month.png", "group": "author", "category": "author", "rarity": "mythic", "goal": 1
    },
    "five_hundred_chapters": {
        "title": "500 глав", "description": "Прочитать пятьсот глав до конца.", "icon": "📚", "icon_asset": "/media/achievements/five_hundred_chapters.png", "group": "reader", "category": "reading", "rarity": "legendary", "goal": 500
    },
    "thousand_chapters": {
        "title": "1000 глав", "description": "Прочитать тысячу глав до конца.", "icon": "✦", "icon_asset": "/media/achievements/thousand_chapters.png", "group": "reader", "category": "reading", "rarity": "legendary", "goal": 1000
    },
    "collector_fifty": {
        "title": "Большая библиотека", "description": "Сохранить пятьдесят произведений в личной библиотеке.", "icon": "🏛", "icon_asset": "/media/achievements/collector_fifty.png", "group": "reader", "category": "reading", "rarity": "epic", "goal": 50
    },
    "audio_ten_hours": {
        "title": "10 часов аудио", "description": "Прослушать суммарно десять часов аудиокниг.", "icon": "🎧", "icon_asset": "/media/achievements/audio_ten_hours.png", "group": "reader", "category": "audio", "rarity": "epic", "goal": 600
    },
    "comic_thousand_pages": {
        "title": "1000 страниц комиксов", "description": "Просмотреть тысячу страниц графических произведений.", "icon": "💠", "icon_asset": "/media/achievements/comic_thousand_pages.png", "group": "reader", "category": "comic", "rarity": "legendary", "goal": 1000
    },
    "author_five_hundred_chapters": {
        "title": "Автор 500 глав", "description": "Опубликовать пятьсот текстовых или графических глав.", "icon": "🪶", "icon_asset": "/media/achievements/author_five_hundred_chapters.png", "group": "author", "category": "author", "rarity": "legendary", "goal": 500
    },
    "author_fifty_books": {
        "title": "Автор 50 книг", "description": "Опубликовать пятьдесят самостоятельных произведений.", "icon": "📚", "icon_asset": "/media/achievements/author_fifty_books.png", "group": "author", "category": "author", "rarity": "legendary", "goal": 50
    },
    "author_thousand_reactions": {
        "title": "1000 реакций", "description": "Получить тысячу реакций читателей на опубликованные главы.", "icon": "💜", "icon_asset": "/media/achievements/author_thousand_reactions.png", "group": "author", "category": "author", "rarity": "legendary", "goal": 1000
    },
    "two_thousand_chapters": {
        "title": "2500 глав", "description": "Завершить две тысячи пятьсот текстовых глав.", "icon": "✦", "icon_asset": "/media/achievements/two_thousand_chapters.png", "group": "reader", "category": "reading", "rarity": "legendary", "goal": 2500
    },
    "collector_hundred": {
        "title": "Личная библиотека 100", "description": "Сохранить сто произведений в личной библиотеке.", "icon": "🏛", "icon_asset": "/media/achievements/collector_hundred.png", "group": "reader", "category": "reading", "rarity": "legendary", "goal": 100
    },
    "reviewer_fifty": {
        "title": "Критик VoxLyra", "description": "Опубликовать пятьдесят отзывов на произведения.", "icon": "⭐", "icon_asset": "/media/achievements/reviewer_fifty.png", "group": "reader", "category": "community", "rarity": "epic", "goal": 50
    },
    "commentator_hundred": {
        "title": "Голос сообщества", "description": "Опубликовать сто комментариев к главам.", "icon": "💬", "icon_asset": "/media/achievements/commentator_hundred.png", "group": "reader", "category": "community", "rarity": "epic", "goal": 100
    },
    "audio_hundred_hours": {
        "title": "100 часов аудио", "description": "Прослушать суммарно сто часов аудиокниг.", "icon": "🎧", "icon_asset": "/media/achievements/audio_hundred_hours.png", "group": "reader", "category": "audio", "rarity": "legendary", "goal": 6000
    },
    "comic_five_thousand_pages": {
        "title": "5000 страниц", "description": "Просмотреть пять тысяч страниц комиксов, манги или манхвы.", "icon": "💠", "icon_asset": "/media/achievements/comic_five_thousand_pages.png", "group": "reader", "category": "comic", "rarity": "legendary", "goal": 5000
    },
    "author_hundred_books": {
        "title": "Автор 100 книг", "description": "Опубликовать сто самостоятельных произведений.", "icon": "📚", "icon_asset": "/media/achievements/author_hundred_books.png", "group": "author", "category": "author", "rarity": "mythic", "goal": 100
    },
    "author_ten_thousand_reactions": {
        "title": "10 000 реакций", "description": "Получить десять тысяч реакций читателей на опубликованные главы.", "icon": "💜", "icon_asset": "/media/achievements/author_ten_thousand_reactions.png", "group": "author", "category": "author", "rarity": "mythic", "goal": 10000
    },
    "reader_twenty_five_chapters": {
        "title": "Первые 25 глав", "description": "Завершить двадцать пять текстовых глав.", "icon": "📖", "icon_asset": "/media/achievements/reader_twenty_five_chapters.png", "group": "reader", "category": "reading", "rarity": "common", "goal": 25
    },
    "reader_two_hundred_fifty_chapters": {
        "title": "250 глав", "description": "Завершить двести пятьдесят текстовых глав.", "icon": "📚", "icon_asset": "/media/achievements/reader_two_hundred_fifty_chapters.png", "group": "reader", "category": "reading", "rarity": "rare", "goal": 250
    },
    "reader_five_thousand_chapters": {
        "title": "Хронист 5000", "description": "Завершить пять тысяч текстовых глав — рубеж преданного читателя.", "icon": "✦", "icon_asset": "/media/achievements/reader_five_thousand_chapters.png", "group": "reader", "category": "reading", "rarity": "mythic", "goal": 5000
    },
    "reading_streak_14": {
        "title": "Две недели вместе", "description": "Сохранять подтверждённую активность четырнадцать дней подряд.", "icon": "🔥", "icon_asset": "/media/achievements/reading_streak_14.png", "group": "reader", "category": "reading", "rarity": "rare", "goal": 14
    },
    "reading_streak_100": {
        "title": "Сто дней пути", "description": "Сохранять подтверждённую активность сто дней подряд.", "icon": "🔥", "icon_asset": "/media/achievements/reading_streak_100.png", "group": "reader", "category": "reading", "rarity": "legendary", "goal": 100
    },
    "reading_streak_365": {
        "title": "Год с VoxLyra", "description": "Сохранять подтверждённую активность триста шестьдесят пять дней подряд.", "icon": "✦", "icon_asset": "/media/achievements/reading_streak_365.png", "group": "reader", "category": "reading", "rarity": "mythic", "goal": 365
    },
    "collector_twenty_five": {
        "title": "Полка на 25", "description": "Сохранить двадцать пять произведений в личной библиотеке.", "icon": "📚", "icon_asset": "/media/achievements/collector_twenty_five.png", "group": "reader", "category": "reading", "rarity": "rare", "goal": 25
    },
    "collector_two_hundred_fifty": {
        "title": "Архивариус", "description": "Сохранить двести пятьдесят произведений в личной библиотеке.", "icon": "🏛", "icon_asset": "/media/achievements/collector_two_hundred_fifty.png", "group": "reader", "category": "reading", "rarity": "legendary", "goal": 250
    },
    "reviewer_ten": {
        "title": "Вдумчивый критик", "description": "Опубликовать десять содержательных отзывов на произведения.", "icon": "⭐", "icon_asset": "/media/achievements/reviewer_ten.png", "group": "reader", "category": "community", "rarity": "rare", "goal": 10
    },
    "commentator_twenty_five": {
        "title": "Участник обсуждений", "description": "Опубликовать двадцать пять комментариев к главам.", "icon": "💬", "icon_asset": "/media/achievements/commentator_twenty_five.png", "group": "reader", "category": "community", "rarity": "rare", "goal": 25
    },
    "audio_twenty_five_hours": {
        "title": "25 часов звучания", "description": "Прослушать суммарно двадцать пять часов аудиокниг.", "icon": "🎧", "icon_asset": "/media/achievements/audio_twenty_five_hours.png", "group": "reader", "category": "audio", "rarity": "epic", "goal": 1500
    },
    "audio_five_hundred_hours": {
        "title": "Голос эпох", "description": "Прослушать суммарно пятьсот часов аудиокниг.", "icon": "✦", "icon_asset": "/media/achievements/audio_five_hundred_hours.png", "group": "reader", "category": "audio", "rarity": "mythic", "goal": 30000
    },
    "comic_twenty_five_thousand_pages": {
        "title": "Галерея миров", "description": "Просмотреть двадцать пять тысяч страниц комиксов, манги или манхвы.", "icon": "💠", "icon_asset": "/media/achievements/comic_twenty_five_thousand_pages.png", "group": "reader", "category": "comic", "rarity": "mythic", "goal": 25000
    },
    "quote_fifty": {
        "title": "Хранитель 50 цитат", "description": "Сохранить пятьдесят значимых цитат из произведений.", "icon": "📜", "icon_asset": "/media/achievements/quote_fifty.png", "group": "reader", "category": "reading", "rarity": "epic", "goal": 50
    },
    "author_fifty_chapters": {
        "title": "Автор 50 глав", "description": "Опубликовать пятьдесят текстовых или графических глав.", "icon": "🪶", "icon_asset": "/media/achievements/author_fifty_chapters.png", "group": "author", "category": "author", "rarity": "rare", "goal": 50
    },
    "author_two_hundred_fifty_chapters": {
        "title": "Автор 250 глав", "description": "Опубликовать двести пятьдесят текстовых или графических глав.", "icon": "🪶", "icon_asset": "/media/achievements/author_two_hundred_fifty_chapters.png", "group": "author", "category": "author", "rarity": "epic", "goal": 250
    },
    "author_thousand_chapters": {
        "title": "Автор 1000 глав", "description": "Опубликовать тысячу текстовых или графических глав.", "icon": "🏛", "icon_asset": "/media/achievements/author_thousand_chapters.png", "group": "author", "category": "author", "rarity": "legendary", "goal": 1000
    },
    "author_five_books": {
        "title": "Автор 5 книг", "description": "Опубликовать пять самостоятельных произведений.", "icon": "📚", "icon_asset": "/media/achievements/author_five_books.png", "group": "author", "category": "author", "rarity": "rare", "goal": 5
    },
    "author_hundred_readers": {
        "title": "Первые 100 читателей", "description": "Собрать сто уникальных читателей своих произведений.", "icon": "👥", "icon_asset": "/media/achievements/author_hundred_readers.png", "group": "author", "category": "author", "rarity": "epic", "goal": 100
    },
    "author_ten_thousand_readers": {
        "title": "Аудитория 10 000", "description": "Собрать десять тысяч уникальных читателей своих произведений.", "icon": "✦", "icon_asset": "/media/achievements/author_ten_thousand_readers.png", "group": "author", "category": "author", "rarity": "mythic", "goal": 10000
    },
    "completed_book_1": {
        "title": 'Завершённая история', "description": 'Полностью прочитать первое произведение.', "icon": '✦',
        "icon_asset": "/media/achievements/completed_book_1.png", "group": "reader",
        "category": "reading", "rarity": "common", "goal": 1
    },
    "completed_books_5": {
        "title": 'Пять завершённых миров', "description": 'Полностью прочитать пять произведений.', "icon": '✦',
        "icon_asset": "/media/achievements/completed_books_5.png", "group": "reader",
        "category": "reading", "rarity": "rare", "goal": 5
    },
    "completed_books_25": {
        "title": 'Созвездие историй', "description": 'Полностью прочитать двадцать пять произведений.', "icon": '✦',
        "icon_asset": "/media/achievements/completed_books_25.png", "group": "reader",
        "category": "reading", "rarity": "epic", "goal": 25
    },
    "completed_books_100": {
        "title": 'Страж сотни миров', "description": 'Полностью прочитать сто произведений.', "icon": '✦',
        "icon_asset": "/media/achievements/completed_books_100.png", "group": "reader",
        "category": "reading", "rarity": "legendary", "goal": 100
    },
    "completed_books_250": {
        "title": 'Владыка библиотеки', "description": 'Полностью прочитать двести пятьдесят произведений.', "icon": '✦',
        "icon_asset": "/media/achievements/completed_books_250.png", "group": "reader",
        "category": "reading", "rarity": "mythic", "goal": 250
    },
    "active_days_30": {
        "title": 'Месяц активности', "description": 'Быть активным в VoxLyra в тридцать разные дни.', "icon": '✦',
        "icon_asset": "/media/achievements/active_days_30.png", "group": "reader",
        "category": "reading", "rarity": "rare", "goal": 30
    },
    "active_days_100": {
        "title": 'Сто дней открытий', "description": 'Быть активным в VoxLyra в сто разные дни.', "icon": '✦',
        "icon_asset": "/media/achievements/active_days_100.png", "group": "reader",
        "category": "reading", "rarity": "epic", "goal": 100
    },
    "active_days_365": {
        "title": 'Год открытий', "description": 'Быть активным в VoxLyra в 365 разные дни.', "icon": '✦',
        "icon_asset": "/media/achievements/active_days_365.png", "group": "reader",
        "category": "reading", "rarity": "legendary", "goal": 365
    },
    "active_days_1000": {
        "title": 'Тысяча дней с VoxLyra', "description": 'Быть активным в VoxLyra в тысячу разные дни.', "icon": '✦',
        "icon_asset": "/media/achievements/active_days_1000.png", "group": "reader",
        "category": "reading", "rarity": "mythic", "goal": 1000
    },
    "genres_5": {
        "title": 'Пять жанров', "description": 'Завершить чтение глав в пяти разных жанрах.', "icon": '✦',
        "icon_asset": "/media/achievements/genres_5.png", "group": "reader",
        "category": "reading", "rarity": "rare", "goal": 5
    },
    "genres_10": {
        "title": 'Карта жанров', "description": 'Завершить чтение глав в десяти разных жанрах.', "icon": '✦',
        "icon_asset": "/media/achievements/genres_10.png", "group": "reader",
        "category": "reading", "rarity": "epic", "goal": 10
    },
    "genres_20": {
        "title": 'Путешественник миров', "description": 'Завершить чтение глав в двадцати разных жанрах.', "icon": '✦',
        "icon_asset": "/media/achievements/genres_20.png", "group": "reader",
        "category": "reading", "rarity": "legendary", "goal": 20
    },
    "notes_10": {
        "title": 'Десять мыслей', "description": 'Сохранить десять личных заметок во время чтения.', "icon": '✦',
        "icon_asset": "/media/achievements/notes_10.png", "group": "reader",
        "category": "reading", "rarity": "rare", "goal": 10
    },
    "notes_50": {
        "title": 'Летописец мыслей', "description": 'Сохранить пятьдесят личных заметок.', "icon": '✦',
        "icon_asset": "/media/achievements/notes_50.png", "group": "reader",
        "category": "reading", "rarity": "epic", "goal": 50
    },
    "notes_200": {
        "title": 'Архив мыслей', "description": 'Сохранить двести личных заметок.', "icon": '✦',
        "icon_asset": "/media/achievements/notes_200.png", "group": "reader",
        "category": "reading", "rarity": "legendary", "goal": 200
    },
    "quotes_100": {
        "title": 'Сотня строк', "description": 'Сохранить сто значимых цитат.', "icon": '✦',
        "icon_asset": "/media/achievements/quotes_100.png", "group": "reader",
        "category": "reading", "rarity": "legendary", "goal": 100
    },
    "quotes_250": {
        "title": 'Голос тысячелетий', "description": 'Сохранить двести пятьдесят значимых цитат.', "icon": '✦',
        "icon_asset": "/media/achievements/quotes_250.png", "group": "reader",
        "category": "reading", "rarity": "mythic", "goal": 250
    },
    "graphic_chapters_25": {
        "title": 'Двадцать пять панелей', "description": 'Полностью просмотреть двадцать пять графических глав.', "icon": '🖼',
        "icon_asset": "/media/achievements/graphic_chapters_25.png", "group": "reader",
        "category": "comic", "rarity": "rare", "goal": 25
    },
    "graphic_chapters_250": {
        "title": 'Мастер визуальных миров', "description": 'Полностью просмотреть двести пятьдесят графических глав.', "icon": '🖼',
        "icon_asset": "/media/achievements/graphic_chapters_250.png", "group": "reader",
        "category": "comic", "rarity": "epic", "goal": 250
    },
    "graphic_chapters_1000": {
        "title": 'Хранитель галерей', "description": 'Полностью просмотреть тысячу графических глав.', "icon": '🖼',
        "icon_asset": "/media/achievements/graphic_chapters_1000.png", "group": "reader",
        "category": "comic", "rarity": "legendary", "goal": 1000
    },
    "audio_chapters_10": {
        "title": 'Десять голосов', "description": 'Полностью прослушать десять аудиоглав.', "icon": '🎧',
        "icon_asset": "/media/achievements/audio_chapters_10.png", "group": "reader",
        "category": "audio", "rarity": "rare", "goal": 10
    },
    "audio_chapters_100": {
        "title": 'Сотня голосов', "description": 'Полностью прослушать сто аудиоглав.', "icon": '🎧',
        "icon_asset": "/media/achievements/audio_chapters_100.png", "group": "reader",
        "category": "audio", "rarity": "epic", "goal": 100
    },
    "audio_chapters_500": {
        "title": 'Хранитель звучащих миров', "description": 'Полностью прослушать пятьсот аудиоглав.', "icon": '🎧',
        "icon_asset": "/media/achievements/audio_chapters_500.png", "group": "reader",
        "category": "audio", "rarity": "legendary", "goal": 500
    },
    "reviews_200": {
        "title": 'Мастер критики', "description": 'Опубликовать двести содержательных отзывов.', "icon": '⭐',
        "icon_asset": "/media/achievements/reviews_200.png", "group": "reader",
        "category": "community", "rarity": "legendary", "goal": 200
    },
    "reviews_500": {
        "title": 'Верховный критик', "description": 'Опубликовать пятьсот содержательных отзывов.', "icon": '⭐',
        "icon_asset": "/media/achievements/reviews_500.png", "group": "reader",
        "category": "community", "rarity": "mythic", "goal": 500
    },
    "comments_500": {
        "title": 'Голос пятисот обсуждений', "description": 'Опубликовать пятьсот комментариев к главам.', "icon": '💬',
        "icon_asset": "/media/achievements/comments_500.png", "group": "reader",
        "category": "community", "rarity": "legendary", "goal": 500
    },
    "comments_1000": {
        "title": 'Сердце сообщества', "description": 'Опубликовать тысячу комментариев к главам.', "icon": '💬',
        "icon_asset": "/media/achievements/comments_1000.png", "group": "reader",
        "category": "community", "rarity": "mythic", "goal": 1000
    },
    "author_completed_book_1": {
        "title": 'Завершённое произведение', "description": 'Завершить и опубликовать первое полноценное произведение.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_completed_book_1.png", "group": "author",
        "category": "author", "rarity": "rare", "goal": 1
    },
    "author_completed_books_5": {
        "title": 'Пять завершённых произведений', "description": 'Завершить и опубликовать пять произведений.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_completed_books_5.png", "group": "author",
        "category": "author", "rarity": "epic", "goal": 5
    },
    "author_completed_books_20": {
        "title": 'Мастер завершённых историй', "description": 'Завершить и опубликовать двадцать произведений.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_completed_books_20.png", "group": "author",
        "category": "author", "rarity": "legendary", "goal": 20
    },
    "author_completed_books_50": {
        "title": 'Создатель эпох', "description": 'Завершить и опубликовать пятьдесят произведений.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_completed_books_50.png", "group": "author",
        "category": "author", "rarity": "mythic", "goal": 50
    },
    "author_words_100k": {
        "title": 'Сто тысяч слов', "description": 'Опубликовать сто тысяч слов авторского текста.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_words_100k.png", "group": "author",
        "category": "author", "rarity": "rare", "goal": 100000
    },
    "author_words_1m": {
        "title": 'Миллион слов', "description": 'Опубликовать один миллион слов авторского текста.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_words_1m.png", "group": "author",
        "category": "author", "rarity": "epic", "goal": 1000000
    },
    "author_words_5m": {
        "title": 'Пять миллионов слов', "description": 'Опубликовать пять миллионов слов авторского текста.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_words_5m.png", "group": "author",
        "category": "author", "rarity": "legendary", "goal": 5000000
    },
    "author_words_10m": {
        "title": 'Летописец эпох', "description": 'Опубликовать десять миллионов слов авторского текста.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_words_10m.png", "group": "author",
        "category": "author", "rarity": "mythic", "goal": 10000000
    },
    "author_rating_50": {
        "title": 'Признанное качество', "description": 'Сохранить среднюю оценку не ниже 4,7 при минимум пятидесяти отзывах.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_rating_50.png", "group": "author",
        "category": "author", "rarity": "epic", "goal": 50
    },
    "author_rating_250": {
        "title": 'Эталон VoxLyra', "description": 'Сохранить среднюю оценку не ниже 4,7 при минимум 250 отзывах.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_rating_250.png", "group": "author",
        "category": "author", "rarity": "legendary", "goal": 250
    },
    "author_library_additions_1000": {
        "title": 'Тысяча библиотек', "description": 'Произведения автора добавили в личные библиотеки тысячу раз.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_library_additions_1000.png", "group": "author",
        "category": "author", "rarity": "legendary", "goal": 1000
    },
    "author_hundred_thousand_readers": {
        "title": 'Аудитория 100 000', "description": 'Собрать сто тысяч уникальных читателей.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_hundred_thousand_readers.png", "group": "author",
        "category": "author", "rarity": "mythic", "goal": 100000
    },
    "author_hundred_thousand_reactions": {
        "title": 'Сто тысяч откликов', "description": 'Получить сто тысяч реакций читателей.', "icon": '🪶',
        "icon_asset": "/media/achievements/author_hundred_thousand_reactions.png", "group": "author",
        "category": "author", "rarity": "mythic", "goal": 100000
    },
}


_RARE_ACHIEVEMENT_CATALOG: dict[str, dict[str, Any]] = {
    "founding_member": {
        "title": "Первые хранители",
        "description": "Быть среди читателей VoxLyra до контрольной даты раннего сообщества.",
        "icon": "✦",
        "icon_asset": "/media/achievements/founding_member.png",
        "group": "reader",
        "category": "rare",
        "rarity": "mythic",
        "goal": 1,
        "special": True,
    },
    "all_rounder": {
        "title": "Голос всех миров",
        "description": "Открыть достижения в чтении, аудио, комиксах и общении.",
        "icon": "✦",
        "icon_asset": "/media/achievements/all_rounder.png",
        "group": "reader",
        "category": "rare",
        "rarity": "mythic",
        "goal": 4,
        "special": True,
    },
}

_ACHIEVEMENT_TIER_BY_RARITY: dict[str, str] = {
    "common": "bronze",
    "rare": "silver",
    "epic": "gold",
    "legendary": "platinum",
    "mythic": "legend",
}
_ACHIEVEMENT_TIER_LABELS: dict[str, str] = {
    "bronze": "Бронза",
    "silver": "Серебро",
    "gold": "Золото",
    "platinum": "Платина",
    "legend": "Легенда",
}
_ACHIEVEMENT_POINTS_BY_RARITY: dict[str, int] = {
    "common": 10,
    "rare": 25,
    "epic": 60,
    "legendary": 150,
    "mythic": 300,
}
_ACHIEVEMENT_COLLECTOR_LEVELS: tuple[tuple[int, str], ...] = (
    (0, "Новичок"),
    (100, "Искатель"),
    (250, "Коллекционер"),
    (500, "Хранитель"),
    (1000, "Легенда VoxLyra"),
)
_ACHIEVEMENT_PROGRAM_SETTING_KEY = "achievement_program_v2"
_MANUAL_ACHIEVEMENT_CATALOG_SETTING_KEY = "achievement_manual_catalog_v1"
_ACHIEVEMENT_RARITIES = {"common", "rare", "epic", "legendary", "mythic"}


def _default_achievement_program() -> dict[str, Any]:
    return {
        "points": dict(_ACHIEVEMENT_POINTS_BY_RARITY),
        "levels": [
            {"threshold": threshold, "name": name}
            for threshold, name in _ACHIEVEMENT_COLLECTOR_LEVELS
        ],
        "rare": {
            "founding_member_enabled": True,
            "founding_cutoff_date": "2026-07-23",
            "all_rounder_enabled": True,
        },
        "season": {
            "enabled": True,
            "code": "summer_2026",
            "title": "Летний марафон 2026",
            "description": "Завершить тридцать глав во время летнего сезона VoxLyra.",
            "start_date": "2026-06-01",
            "end_date": "2026-08-31",
            "goal": 30,
            "rarity": "epic",
            "custom_points": 100,
        },
    }


def _program_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _achievement_date(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    clean = str(value or "").strip()
    if not clean and allow_empty:
        return ""
    try:
        parsed = datetime.fromisoformat(clean).date()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Поле «{field_name}» должно быть датой ГГГГ-ММ-ДД.") from exc
    return parsed.isoformat()


def _achievement_campaign_slug(value: Any) -> str:
    clean = str(value or "").strip().lower()
    normalized = []
    previous_separator = False
    for char in clean:
        if char.isalnum():
            normalized.append(char)
            previous_separator = False
        elif not previous_separator:
            normalized.append("_")
            previous_separator = True
    result = "".join(normalized).strip("_")[:40]
    return result or "season"


def _normalize_achievement_program(payload: Any, *, partial: bool = False) -> dict[str, Any]:
    defaults = _default_achievement_program()
    source = payload if isinstance(payload, dict) else {}

    points_source = source.get("points") if isinstance(source.get("points"), dict) else {}
    points = {}
    for rarity, default_value in defaults["points"].items():
        raw = points_source.get(rarity, default_value)
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("Очки наград должны быть целыми числами.") from exc
        points[rarity] = max(1, min(10000, value))

    levels_source = source.get("levels") if isinstance(source.get("levels"), list) else defaults["levels"]
    levels: list[dict[str, Any]] = []
    for index, default_level in enumerate(defaults["levels"]):
        item = levels_source[index] if index < len(levels_source) and isinstance(levels_source[index], dict) else default_level
        name = str(item.get("name") or default_level["name"]).strip()[:48] or default_level["name"]
        try:
            threshold = int(item.get("threshold", default_level["threshold"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Пороги уровней должны быть целыми числами.") from exc
        if index == 0:
            threshold = 0
        threshold = max(0, min(10_000_000, threshold))
        if levels and threshold <= int(levels[-1]["threshold"]):
            raise ValueError("Каждый следующий уровень должен иметь больший порог очков.")
        levels.append({"threshold": threshold, "name": name})

    rare_source = source.get("rare") if isinstance(source.get("rare"), dict) else {}
    rare = {
        "founding_member_enabled": _program_bool(
            rare_source.get("founding_member_enabled"), defaults["rare"]["founding_member_enabled"]
        ),
        "founding_cutoff_date": _achievement_date(
            rare_source.get("founding_cutoff_date", defaults["rare"]["founding_cutoff_date"]),
            "Дата первых хранителей",
        ),
        "all_rounder_enabled": _program_bool(
            rare_source.get("all_rounder_enabled"), defaults["rare"]["all_rounder_enabled"]
        ),
    }

    season_source = source.get("season") if isinstance(source.get("season"), dict) else {}
    season = dict(defaults["season"])
    season.update({key: value for key, value in season_source.items() if key in season})
    season["enabled"] = _program_bool(season.get("enabled"), defaults["season"]["enabled"])
    season["code"] = _achievement_campaign_slug(season.get("code"))
    season["title"] = str(season.get("title") or defaults["season"]["title"]).strip()[:80]
    season["description"] = str(season.get("description") or defaults["season"]["description"]).strip()[:240]
    season["start_date"] = _achievement_date(season.get("start_date"), "Начало сезона")
    season["end_date"] = _achievement_date(season.get("end_date"), "Окончание сезона")
    if season["end_date"] < season["start_date"]:
        raise ValueError("Окончание сезона не может быть раньше начала.")
    try:
        season["goal"] = max(1, min(1_000_000, int(season.get("goal") or 1)))
        season["custom_points"] = max(0, min(10000, int(season.get("custom_points") or 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Цель и очки сезона должны быть целыми числами.") from exc
    rarity = str(season.get("rarity") or "epic").strip().lower()
    season["rarity"] = rarity if rarity in _ACHIEVEMENT_RARITIES else "epic"
    if season["enabled"] and (not season["title"] or not season["description"]):
        raise ValueError("Для активного сезона нужны название и описание.")

    return {"points": points, "levels": levels, "rare": rare, "season": season}


async def get_achievement_program_settings() -> dict[str, Any]:
    raw = await get_setting(_ACHIEVEMENT_PROGRAM_SETTING_KEY, "")
    if not raw:
        return _default_achievement_program()
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return _default_achievement_program()
    try:
        return _normalize_achievement_program(parsed)
    except ValueError:
        return _default_achievement_program()


async def set_achievement_program_settings(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_achievement_program(payload)
    await set_setting(
        _ACHIEVEMENT_PROGRAM_SETTING_KEY,
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")),
    )
    return normalized


async def get_achievement_artwork_catalog() -> list[dict[str, Any]]:
    """Возвращает все 100 запланированных изображений, даже если особая награда временно выключена."""
    program = await get_achievement_program_settings()
    catalog: dict[str, dict[str, Any]] = {code: dict(info) for code, info in _ACHIEVEMENT_CATALOG.items()}
    rare = program.get("rare") if isinstance(program.get("rare"), dict) else {}
    founding = dict(_RARE_ACHIEVEMENT_CATALOG["founding_member"])
    founding["active"] = _program_bool(rare.get("founding_member_enabled"), True)
    catalog["founding_member"] = founding
    all_rounder = dict(_RARE_ACHIEVEMENT_CATALOG["all_rounder"])
    all_rounder["active"] = _program_bool(rare.get("all_rounder_enabled"), True)
    catalog["all_rounder"] = all_rounder
    season = program.get("season") if isinstance(program.get("season"), dict) else _default_achievement_program()["season"]
    season_info = _season_achievement_info(season)
    season_info["active"] = _program_bool(season.get("enabled"), False)
    catalog[_season_achievement_code(season)] = season_info
    result: list[dict[str, Any]] = []
    for code, info in catalog.items():
        tier, tier_label = _achievement_tier(info)
        result.append({"code": code, **info, "tier": tier, "tier_label": tier_label})
    return result


async def get_achievement_owner_summary() -> dict[str, Any]:
    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) AS cnt, COUNT(DISTINCT user_id) AS users FROM user_achievements")
        totals = await cur.fetchone()
        cur = await db.execute(
            "SELECT achievement_code, COUNT(*) AS cnt FROM user_achievements "
            "GROUP BY achievement_code ORDER BY cnt DESC, achievement_code LIMIT 12"
        )
        popular = [
            {"code": str(row["achievement_code"]), "awarded": int(row["cnt"] or 0)}
            for row in await cur.fetchall()
        ]
        cur = await db.execute("SELECT COUNT(DISTINCT user_id) AS cnt FROM achievement_showcase")
        showcase_users = int((await cur.fetchone())["cnt"] or 0)
    program = await get_achievement_program_settings()
    rare = program.get("rare") if isinstance(program.get("rare"), dict) else {}
    automatic_total = len(_ACHIEVEMENT_CATALOG)
    automatic_total += 1 if _program_bool(rare.get("founding_member_enabled"), True) else 0
    automatic_total += 1 if _program_bool(rare.get("all_rounder_enabled"), True) else 0
    season = program.get("season") if isinstance(program.get("season"), dict) else {}
    automatic_total += 1 if _program_bool(season.get("enabled"), False) else 0
    return {
        "awards_total": int(totals["cnt"] or 0),
        "users_with_awards": int(totals["users"] or 0),
        "showcase_users": showcase_users,
        "automatic_total": automatic_total,
        "planned_target": 100,
        "popular": popular,
    }


def _season_achievement_code(season: dict[str, Any]) -> str:
    return f"season_{_achievement_campaign_slug(season.get('code'))}"


def _season_achievement_info(season: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(season.get("title") or "Сезон VoxLyra"),
        "description": str(season.get("description") or "Выполнить сезонную цель VoxLyra."),
        "icon": "✦",
        "icon_asset": "/media/achievements/seasonal_reader.png",
        "group": "reader",
        "category": "seasonal",
        "rarity": str(season.get("rarity") or "epic"),
        "goal": max(1, int(season.get("goal") or 1)),
        "custom_points": max(0, int(season.get("custom_points") or 0)),
        "special": True,
        "season_start": str(season.get("start_date") or ""),
        "season_end": str(season.get("end_date") or ""),
    }


def _achievement_points(info: dict[str, Any], program: dict[str, Any] | None = None) -> int:
    custom = int(info.get("custom_points") or 0)
    if custom > 0:
        return custom
    configured = (program or {}).get("points") if isinstance((program or {}).get("points"), dict) else {}
    rarity = str(info.get("rarity") or "common")
    return max(1, int(configured.get(rarity, _ACHIEVEMENT_POINTS_BY_RARITY.get(rarity, 10))))


def _achievement_collector_summary(
    total_points: int,
    unlocked_count: int,
    total_count: int,
    levels: list[dict[str, Any]] | tuple[tuple[int, str], ...] | None = None,
) -> dict[str, Any]:
    points = max(0, int(total_points))
    configured_levels: list[tuple[int, str]] = []
    for item in levels or _ACHIEVEMENT_COLLECTOR_LEVELS:
        if isinstance(item, dict):
            configured_levels.append((int(item.get("threshold") or 0), str(item.get("name") or "Уровень")))
        else:
            configured_levels.append((int(item[0]), str(item[1])))
    configured_levels.sort(key=lambda item: item[0])
    if not configured_levels or configured_levels[0][0] != 0:
        configured_levels.insert(0, (0, "Новичок"))
    level_index = 0
    for index, (threshold, _name) in enumerate(configured_levels):
        if points >= threshold:
            level_index = index
    threshold, name = configured_levels[level_index]
    if level_index + 1 < len(configured_levels):
        next_threshold, next_name = configured_levels[level_index + 1]
        span = max(1, next_threshold - threshold)
        level_progress = min(100, round((points - threshold) * 100 / span))
        points_to_next = max(0, next_threshold - points)
    else:
        next_threshold = threshold
        next_name = name
        level_progress = 100
        points_to_next = 0
    return {
        "points": points,
        "level": level_index + 1,
        "level_name": name,
        "level_progress_percent": level_progress,
        "next_level_name": next_name,
        "next_level_points": next_threshold,
        "points_to_next": points_to_next,
        "unlocked_count": max(0, int(unlocked_count)),
        "total_count": max(0, int(total_count)),
    }


def _achievement_tier(info: dict[str, Any]) -> tuple[str, str]:
    explicit_tier = str(info.get("tier") or "").strip().lower()
    tier = explicit_tier if explicit_tier in _ACHIEVEMENT_TIER_LABELS else _ACHIEVEMENT_TIER_BY_RARITY.get(
        str(info.get("rarity") or "common"), "bronze"
    )
    return tier, _ACHIEVEMENT_TIER_LABELS[tier]


async def set_user_achievement_showcase(user_id: int, achievement_codes: list[str]) -> list[str]:
    """Сохраняет до трёх уже полученных наград в витрине профиля."""
    uid = int(user_id)
    normalized: list[str] = []
    for raw_code in achievement_codes or []:
        code = str(raw_code or "").strip()
        if not code or code in normalized:
            continue
        normalized.append(code)
    if len(normalized) > 3:
        raise ValueError("На витрине можно разместить не более трёх достижений.")

    async with connect() as db:
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            cur = await db.execute(
                f"SELECT achievement_code FROM user_achievements WHERE user_id=? AND achievement_code IN ({placeholders})",
                (uid, *normalized),
            )
            owned = {str(row["achievement_code"]) for row in await cur.fetchall()}
            if any(code not in owned for code in normalized):
                raise ValueError("На витрину можно добавить только уже полученные достижения.")
        await db.execute("DELETE FROM achievement_showcase WHERE user_id=?", (uid,))
        now = utc_now()
        for position, code in enumerate(normalized, start=1):
            await db.execute(
                "INSERT INTO achievement_showcase(user_id, position, achievement_code, updated_at) VALUES(?, ?, ?, ?)",
                (uid, position, code, now),
            )
        await db.commit()
    return normalized


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


async def sync_user_achievements(user_id: int) -> dict[str, Any]:
    """Начисляет подтверждённые награды и возвращает полный каталог с прогрессом."""
    uid = int(user_id)
    now = utc_now()
    program = await get_achievement_program_settings()
    program_catalog: dict[str, dict[str, Any]] = dict(_ACHIEVEMENT_CATALOG)
    for manual_item in await get_manual_achievement_definitions():
        if manual_item.get("active", True):
            program_catalog[str(manual_item["code"])] = dict(manual_item)
    rare_settings = program.get("rare") if isinstance(program.get("rare"), dict) else {}
    if _program_bool(rare_settings.get("founding_member_enabled"), True):
        program_catalog["founding_member"] = dict(_RARE_ACHIEVEMENT_CATALOG["founding_member"])
    if _program_bool(rare_settings.get("all_rounder_enabled"), True):
        program_catalog["all_rounder"] = dict(_RARE_ACHIEVEMENT_CATALOG["all_rounder"])
    season_settings = program.get("season") if isinstance(program.get("season"), dict) else {}
    season_code = _season_achievement_code(season_settings) if _program_bool(season_settings.get("enabled"), False) else ""
    if season_code:
        program_catalog[season_code] = _season_achievement_info(season_settings)

    async with connect() as db:
        cur = await db.execute("SELECT created_at FROM users WHERE id=?", (uid,))
        user_row = await cur.fetchone()
        user_created_at = str(user_row["created_at"] or "") if user_row else ""
        cur = await db.execute(
            "SELECT COUNT(*) AS completed FROM reading_progress WHERE user_id=? AND position_percent>=90", (uid,)
        )
        completed = int((await cur.fetchone())["completed"] or 0)
        cur = await db.execute("SELECT COUNT(*) AS saved FROM bookmarks WHERE user_id=?", (uid,))
        saved = int((await cur.fetchone())["saved"] or 0)
        cur = await db.execute(
            "SELECT COUNT(*) AS reviews FROM reviews WHERE user_id=? AND status='published'", (uid,)
        )
        reviews = int((await cur.fetchone())["reviews"] or 0)
        cur = await db.execute(
            "SELECT COUNT(*) AS comments FROM comments WHERE user_id=? AND status='published'", (uid,)
        )
        comments_count = int((await cur.fetchone())["comments"] or 0)
        cur = await db.execute(
            "SELECT COUNT(*) AS night FROM reading_progress WHERE user_id=? AND "
            "(CAST(substr(updated_at,12,2) AS INTEGER)>=22 OR CAST(substr(updated_at,12,2) AS INTEGER)<5)",
            (uid,),
        )
        night = int((await cur.fetchone())["night"] or 0)
        cur = await db.execute(
            """
            SELECT
                COALESCE(SUM(audio_seconds),0) AS audio_seconds,
                COALESCE(SUM(graphic_pages),0) AS graphic_pages
            FROM reader_activity_daily WHERE user_id=?
            """,
            (uid,),
        )
        activity_totals = await cur.fetchone()
        audio_minutes = int(activity_totals["audio_seconds"] or 0) // 60
        graphic_pages = int(activity_totals["graphic_pages"] or 0)
        cur = await db.execute(
            "SELECT COUNT(DISTINCT graphic_chapter_id) AS cnt FROM graphic_reading_progress WHERE user_id=?",
            (uid,),
        )
        graphic_chapters_opened = int((await cur.fetchone())["cnt"] or 0)
        cur = await db.execute(
            """
            SELECT
                SUM(CASE WHEN annotation_type='note' THEN 1 ELSE 0 END) AS notes,
                SUM(CASE WHEN annotation_type='quote' THEN 1 ELSE 0 END) AS quotes
            FROM reader_annotations WHERE user_id=?
            """,
            (uid,),
        )
        annotations = await cur.fetchone()
        notes_count = int(annotations["notes"] or 0)
        quotes_count = int(annotations["quotes"] or 0)
        cur = await db.execute(
            "SELECT COUNT(*) AS cnt FROM premium_subscriptions WHERE user_id=? AND status!='refunded'",
            (uid,),
        )
        premium_count = int((await cur.fetchone())["cnt"] or 0)

        # v1.14.0.18 — подтверждённые метрики для каталога из 100 наград.
        cur = await db.execute(
            "SELECT COUNT(DISTINCT book_id) AS cnt FROM reader_book_cycles "
            "WHERE user_id=? AND status='finished'",
            (uid,),
        )
        completed_books = int((await cur.fetchone())["cnt"] or 0)
        cur = await db.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM reader_activity_daily
            WHERE user_id=? AND (
                text_chapters>0 OR text_progress_points>0 OR audio_seconds>0
                OR graphic_pages>0 OR sessions>0
            )
            """,
            (uid,),
        )
        active_days = int((await cur.fetchone())["cnt"] or 0)
        cur = await db.execute(
            """
            SELECT COUNT(DISTINCT bov.option_code) AS cnt
            FROM reading_progress rp
            JOIN book_option_values bov
              ON bov.book_id=rp.book_id AND bov.option_group='genres'
            WHERE rp.user_id=? AND rp.position_percent>=90
            """,
            (uid,),
        )
        completed_genres = int((await cur.fetchone())["cnt"] or 0)
        cur = await db.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM graphic_reading_progress grp
            JOIN graphic_chapters gc ON gc.id=grp.graphic_chapter_id
            WHERE grp.user_id=? AND gc.status='published' AND gc.pages_count>0
              AND grp.page_number>=gc.pages_count
            """,
            (uid,),
        )
        completed_graphic_chapters = int((await cur.fetchone())["cnt"] or 0)
        cur = await db.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM listening_progress lp
            JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id
            WHERE lp.user_id=? AND ac.status='published' AND ac.duration_seconds>0
              AND lp.position_seconds*100>=ac.duration_seconds*90
            """,
            (uid,),
        )
        completed_audio_chapters = int((await cur.fetchone())["cnt"] or 0)

        season_completed = 0
        if season_code:
            start_date = datetime.fromisoformat(str(season_settings.get("start_date"))).date()
            end_date = datetime.fromisoformat(str(season_settings.get("end_date"))).date() + timedelta(days=1)
            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM reading_progress "
                "WHERE user_id=? AND position_percent>=90 AND updated_at>=? AND updated_at<?",
                (uid, start_date.isoformat(), end_date.isoformat()),
            )
            season_completed = int((await cur.fetchone())["cnt"] or 0)

        cur = await db.execute(
            """
            SELECT activity_date FROM reader_activity_daily
            WHERE user_id=? AND (
                text_chapters>0 OR text_progress_points>0 OR audio_seconds>0 OR graphic_pages>0 OR sessions>0
            ) ORDER BY activity_date
            """,
            (uid,),
        )
        streak_dates = []
        for row in await cur.fetchall():
            try:
                streak_dates.append(datetime.fromisoformat(str(row["activity_date"])).date())
            except (TypeError, ValueError):
                continue
        longest_streak = 0
        current_streak = 0
        previous_day = None
        for activity_day in sorted(set(streak_dates)):
            if previous_day is not None and activity_day == previous_day + timedelta(days=1):
                current_streak += 1
            else:
                current_streak = 1
            longest_streak = max(longest_streak, current_streak)
            previous_day = activity_day

        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (uid,))
        author = await cur.fetchone()
        published_books = published_chapters = author_readers = month_rank = author_reactions = 0
        author_completed_books = author_word_count = author_review_count = author_library_additions = 0
        author_average_rating = 0.0
        if author:
            author_id = int(author["id"])
            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM books WHERE author_id=? AND publication_status='published'",
                (author_id,),
            )
            published_books = int((await cur.fetchone())["cnt"] or 0)
            cur = await db.execute(
                "SELECT (SELECT COUNT(*) FROM chapters c JOIN books b ON b.id=c.book_id "
                "WHERE b.author_id=? AND b.publication_status='published' AND c.status='published') + "
                "(SELECT COUNT(*) FROM graphic_chapters gc JOIN books b ON b.id=gc.book_id "
                "WHERE b.author_id=? AND b.publication_status='published' AND gc.status='published') AS cnt",
                (author_id, author_id),
            )
            published_chapters = int((await cur.fetchone())["cnt"] or 0)
            cur = await db.execute(
                "SELECT COUNT(DISTINCT rp.user_id) AS cnt FROM reading_progress rp "
                "JOIN books b ON b.id=rp.book_id WHERE b.author_id=?",
                (author_id,),
            )
            author_readers = int((await cur.fetchone())["cnt"] or 0)
            cur = await db.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM chapter_reactions cr
                JOIN chapters c ON c.id=cr.chapter_id
                JOIN books b ON b.id=c.book_id
                WHERE b.author_id=? AND b.publication_status='published'
                """,
                (author_id,),
            )
            author_reactions = int((await cur.fetchone())["cnt"] or 0)
            cur = await db.execute(
                "SELECT COUNT(*) AS cnt FROM books "
                "WHERE author_id=? AND publication_status='published' AND writing_status='finished'",
                (author_id,),
            )
            author_completed_books = int((await cur.fetchone())["cnt"] or 0)
            cur = await db.execute(
                """
                WITH author_text AS (
                    SELECT TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                               c.text, char(10), ' '), char(13), ' '), char(9), ' '),
                               '  ', ' '), '  ', ' '), '  ', ' ')) AS clean_text
                    FROM chapters c
                    JOIN books b ON b.id=c.book_id
                    WHERE b.author_id=? AND b.publication_status='published'
                      AND c.status='published'
                )
                SELECT COALESCE(SUM(
                    CASE
                        WHEN clean_text='' THEN 0
                        ELSE LENGTH(clean_text)-LENGTH(REPLACE(clean_text,' ',''))+1
                    END
                ),0) AS words
                FROM author_text
                """,
                (author_id,),
            )
            author_word_count = int((await cur.fetchone())["words"] or 0)
            cur = await db.execute(
                """
                SELECT COUNT(*) AS cnt, COALESCE(AVG(r.rating),0) AS avg_rating
                FROM reviews r
                JOIN books b ON b.id=r.book_id
                WHERE b.author_id=? AND b.publication_status='published'
                  AND r.status='published'
                """,
                (author_id,),
            )
            author_quality = await cur.fetchone()
            author_review_count = int(author_quality["cnt"] or 0)
            author_average_rating = float(author_quality["avg_rating"] or 0.0)
            cur = await db.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM bookmarks bm
                JOIN books b ON b.id=bm.book_id
                WHERE b.author_id=? AND b.publication_status='published'
                """,
                (author_id,),
            )
            author_library_additions = int((await cur.fetchone())["cnt"] or 0)
            month_start = datetime.now(timezone.utc).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            cur = await db.execute(
                """
                WITH author_month AS (
                    SELECT b.author_id, COUNT(DISTINCT rp.user_id) AS readers
                    FROM books b LEFT JOIN reading_progress rp
                      ON rp.book_id=b.id AND rp.updated_at>=?
                    WHERE b.publication_status='published' GROUP BY b.author_id
                )
                SELECT 1 + COUNT(*) AS rank FROM author_month mine, author_month other
                WHERE mine.author_id=? AND other.readers>mine.readers
                """,
                (month_start, author_id),
            )
            row = await cur.fetchone()
            month_rank = int(row["rank"] or 0) if row else 0

        author_quality_review_progress = author_review_count if author_average_rating >= 4.7 else 0

        candidates = {
            "first_chapter": (completed >= 1, completed),
            "hundred_chapters": (completed >= 100, completed),
            "night_reader": (night >= 1, night),
            "collector": (saved >= 10, saved),
            "first_review": (reviews >= 1, reviews),
            "first_comment": (comments_count >= 1, comments_count),
            "reading_streak_7": (longest_streak >= 7, longest_streak),
            "reading_streak_30": (longest_streak >= 30, longest_streak),
            "audio_hour": (audio_minutes >= 60, audio_minutes),
            "comic_explorer": (graphic_chapters_opened >= 1, graphic_chapters_opened),
            "comic_hundred_pages": (graphic_pages >= 100, graphic_pages),
            "first_note": (notes_count >= 1, notes_count),
            "quote_collector": (quotes_count >= 10, quotes_count),
            "premium_member": (premium_count >= 1, premium_count),
            "first_book": (published_books >= 1, published_books),
            "author_ten_chapters": (published_chapters >= 10, published_chapters),
            "author_hundred_chapters": (published_chapters >= 100, published_chapters),
            "author_ten_books": (published_books >= 10, published_books),
            "author_hundred_reactions": (author_reactions >= 100, author_reactions),
            "thousand_readers": (author_readers >= 1000, author_readers),
            "author_month": (month_rank == 1 and author_readers >= 20, 1 if month_rank == 1 and author_readers >= 20 else 0),
            "five_hundred_chapters": (completed >= 500, completed),
            "thousand_chapters": (completed >= 1000, completed),
            "collector_fifty": (saved >= 50, saved),
            "audio_ten_hours": (audio_minutes >= 600, audio_minutes),
            "comic_thousand_pages": (graphic_pages >= 1000, graphic_pages),
            "author_five_hundred_chapters": (published_chapters >= 500, published_chapters),
            "author_fifty_books": (published_books >= 50, published_books),
            "author_thousand_reactions": (author_reactions >= 1000, author_reactions),
            "two_thousand_chapters": (completed >= 2500, completed),
            "collector_hundred": (saved >= 100, saved),
            "reviewer_fifty": (reviews >= 50, reviews),
            "commentator_hundred": (comments_count >= 100, comments_count),
            "audio_hundred_hours": (audio_minutes >= 6000, audio_minutes),
            "comic_five_thousand_pages": (graphic_pages >= 5000, graphic_pages),
            "author_hundred_books": (published_books >= 100, published_books),
            "author_ten_thousand_reactions": (author_reactions >= 10000, author_reactions),
            "reader_twenty_five_chapters": (completed >= 25, completed),
            "reader_two_hundred_fifty_chapters": (completed >= 250, completed),
            "reader_five_thousand_chapters": (completed >= 5000, completed),
            "reading_streak_14": (longest_streak >= 14, longest_streak),
            "reading_streak_100": (longest_streak >= 100, longest_streak),
            "reading_streak_365": (longest_streak >= 365, longest_streak),
            "collector_twenty_five": (saved >= 25, saved),
            "collector_two_hundred_fifty": (saved >= 250, saved),
            "reviewer_ten": (reviews >= 10, reviews),
            "commentator_twenty_five": (comments_count >= 25, comments_count),
            "audio_twenty_five_hours": (audio_minutes >= 1500, audio_minutes),
            "audio_five_hundred_hours": (audio_minutes >= 30000, audio_minutes),
            "comic_twenty_five_thousand_pages": (graphic_pages >= 25000, graphic_pages),
            "quote_fifty": (quotes_count >= 50, quotes_count),
            "author_fifty_chapters": (published_chapters >= 50, published_chapters),
            "author_two_hundred_fifty_chapters": (published_chapters >= 250, published_chapters),
            "author_thousand_chapters": (published_chapters >= 1000, published_chapters),
            "author_five_books": (published_books >= 5, published_books),
            "author_hundred_readers": (author_readers >= 100, author_readers),
            "author_ten_thousand_readers": (author_readers >= 10000, author_readers),
            "completed_book_1": (completed_books >= 1, completed_books),
            "completed_books_5": (completed_books >= 5, completed_books),
            "completed_books_25": (completed_books >= 25, completed_books),
            "completed_books_100": (completed_books >= 100, completed_books),
            "completed_books_250": (completed_books >= 250, completed_books),
            "active_days_30": (active_days >= 30, active_days),
            "active_days_100": (active_days >= 100, active_days),
            "active_days_365": (active_days >= 365, active_days),
            "active_days_1000": (active_days >= 1000, active_days),
            "genres_5": (completed_genres >= 5, completed_genres),
            "genres_10": (completed_genres >= 10, completed_genres),
            "genres_20": (completed_genres >= 20, completed_genres),
            "notes_10": (notes_count >= 10, notes_count),
            "notes_50": (notes_count >= 50, notes_count),
            "notes_200": (notes_count >= 200, notes_count),
            "quotes_100": (quotes_count >= 100, quotes_count),
            "quotes_250": (quotes_count >= 250, quotes_count),
            "graphic_chapters_25": (completed_graphic_chapters >= 25, completed_graphic_chapters),
            "graphic_chapters_250": (completed_graphic_chapters >= 250, completed_graphic_chapters),
            "graphic_chapters_1000": (completed_graphic_chapters >= 1000, completed_graphic_chapters),
            "audio_chapters_10": (completed_audio_chapters >= 10, completed_audio_chapters),
            "audio_chapters_100": (completed_audio_chapters >= 100, completed_audio_chapters),
            "audio_chapters_500": (completed_audio_chapters >= 500, completed_audio_chapters),
            "reviews_200": (reviews >= 200, reviews),
            "reviews_500": (reviews >= 500, reviews),
            "comments_500": (comments_count >= 500, comments_count),
            "comments_1000": (comments_count >= 1000, comments_count),
            "author_completed_book_1": (author_completed_books >= 1, author_completed_books),
            "author_completed_books_5": (author_completed_books >= 5, author_completed_books),
            "author_completed_books_20": (author_completed_books >= 20, author_completed_books),
            "author_completed_books_50": (author_completed_books >= 50, author_completed_books),
            "author_words_100k": (author_word_count >= 100000, author_word_count),
            "author_words_1m": (author_word_count >= 1000000, author_word_count),
            "author_words_5m": (author_word_count >= 5000000, author_word_count),
            "author_words_10m": (author_word_count >= 10000000, author_word_count),
            "author_rating_50": (author_quality_review_progress >= 50, author_quality_review_progress),
            "author_rating_250": (author_quality_review_progress >= 250, author_quality_review_progress),
            "author_library_additions_1000": (author_library_additions >= 1000, author_library_additions),
            "author_hundred_thousand_readers": (author_readers >= 100000, author_readers),
            "author_hundred_thousand_reactions": (author_reactions >= 100000, author_reactions),
        }
        all_rounder_progress = sum((
            1 if completed >= 1 else 0,
            1 if audio_minutes >= 60 else 0,
            1 if graphic_pages >= 100 else 0,
            1 if (reviews + comments_count) >= 1 else 0,
        ))
        if "all_rounder" in program_catalog:
            candidates["all_rounder"] = (all_rounder_progress >= 4, all_rounder_progress)
        if "founding_member" in program_catalog:
            try:
                created_date = datetime.fromisoformat(user_created_at.replace("Z", "+00:00")).date()
                cutoff_date = datetime.fromisoformat(str(rare_settings.get("founding_cutoff_date"))).date()
                founding_eligible = created_date <= cutoff_date
            except (TypeError, ValueError):
                founding_eligible = False
            candidates["founding_member"] = (founding_eligible, 1 if founding_eligible else 0)
        if season_code:
            season_goal = max(1, int(program_catalog[season_code].get("goal") or 1))
            candidates[season_code] = (season_completed >= season_goal, season_completed)

        awarded_codes: list[str] = []
        for code, (eligible, value) in candidates.items():
            if not eligible:
                continue
            metadata: dict[str, Any] = {}
            if code == season_code and code in program_catalog:
                metadata = {
                    "achievement": program_catalog[code],
                    "campaign_code": str(season_settings.get("code") or ""),
                    "awarded_program_version": 2,
                }
            cur = await db.execute(
                "INSERT OR IGNORE INTO user_achievements(user_id, achievement_code, progress_value, metadata_json, awarded_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (uid, code, int(value), json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), now),
            )
            if cur.rowcount:
                awarded_codes.append(code)
        await db.commit()
        cur = await db.execute(
            "SELECT achievement_code, progress_value, metadata_json, awarded_at "
            "FROM user_achievements WHERE user_id=? ORDER BY awarded_at DESC, id DESC",
            (uid,),
        )
        rows = await cur.fetchall()
        cur = await db.execute(
            "SELECT position, achievement_code FROM achievement_showcase WHERE user_id=? ORDER BY position",
            (uid,),
        )
        showcase_rows = await cur.fetchall()

    showcase_positions = {str(row["achievement_code"]): int(row["position"]) for row in showcase_rows}

    def public(row: Any) -> dict[str, Any]:
        code = str(row["achievement_code"])
        fallback = {"title": code, "description": "", "icon": "✦", "group": "reader", "category": "reading", "rarity": "common", "goal": 1}
        info = dict(program_catalog.get(code) or _RARE_ACHIEVEMENT_CATALOG.get(code) or fallback)
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        frozen_info = metadata.get("achievement") if isinstance(metadata, dict) else None
        if isinstance(frozen_info, dict):
            info.update({key: value for key, value in frozen_info.items() if value is not None})
        goal = max(1, int(info.get("goal") or 1))
        progress = int(row["progress_value"] or 0)
        tier, tier_label = _achievement_tier(info)
        showcase_position = showcase_positions.get(code)
        return {
            "code": code, **info, "tier": tier, "tier_label": tier_label,
            "points": _achievement_points(info, program),
            "progress_value": progress, "goal": goal, "progress_percent": 100,
            "unlocked": True, "awarded_at": row["awarded_at"],
            "showcased": showcase_position is not None, "showcase_position": showcase_position,
        }

    all_items = [public(row) for row in rows]
    unlocked_by_code = {item["code"]: item for item in all_items}
    new_items = [item for item in all_items if item["code"] in awarded_codes]
    live_progress = {
        "first_chapter": completed,
        "hundred_chapters": completed,
        "night_reader": night,
        "collector": saved,
        "first_review": reviews,
        "first_comment": comments_count,
        "reading_streak_7": longest_streak,
        "reading_streak_30": longest_streak,
        "audio_hour": audio_minutes,
        "comic_explorer": graphic_chapters_opened,
        "comic_hundred_pages": graphic_pages,
        "first_note": notes_count,
        "quote_collector": quotes_count,
        "premium_member": premium_count,
        "first_book": published_books,
        "author_ten_chapters": published_chapters,
        "author_hundred_chapters": published_chapters,
        "author_ten_books": published_books,
        "author_hundred_reactions": author_reactions,
        "thousand_readers": author_readers,
        "author_month": 1 if month_rank == 1 and author_readers >= 20 else 0,
        "five_hundred_chapters": completed,
        "thousand_chapters": completed,
        "collector_fifty": saved,
        "audio_ten_hours": audio_minutes,
        "comic_thousand_pages": graphic_pages,
        "author_five_hundred_chapters": published_chapters,
        "author_fifty_books": published_books,
        "author_thousand_reactions": author_reactions,
        "two_thousand_chapters": completed,
        "collector_hundred": saved,
        "reviewer_fifty": reviews,
        "commentator_hundred": comments_count,
        "audio_hundred_hours": audio_minutes,
        "comic_five_thousand_pages": graphic_pages,
        "author_hundred_books": published_books,
        "author_ten_thousand_reactions": author_reactions,
        "all_rounder": all_rounder_progress,
        "founding_member": 1 if candidates.get("founding_member", (False, 0))[0] else 0,
        "reader_twenty_five_chapters": completed,
        "reader_two_hundred_fifty_chapters": completed,
        "reader_five_thousand_chapters": completed,
        "reading_streak_14": longest_streak,
        "reading_streak_100": longest_streak,
        "reading_streak_365": longest_streak,
        "collector_twenty_five": saved,
        "collector_two_hundred_fifty": saved,
        "reviewer_ten": reviews,
        "commentator_twenty_five": comments_count,
        "audio_twenty_five_hours": audio_minutes,
        "audio_five_hundred_hours": audio_minutes,
        "comic_twenty_five_thousand_pages": graphic_pages,
        "quote_fifty": quotes_count,
        "author_fifty_chapters": published_chapters,
        "author_two_hundred_fifty_chapters": published_chapters,
        "author_thousand_chapters": published_chapters,
        "author_five_books": published_books,
        "author_hundred_readers": author_readers,
        "author_ten_thousand_readers": author_readers,
        "completed_book_1": completed_books,
        "completed_books_5": completed_books,
        "completed_books_25": completed_books,
        "completed_books_100": completed_books,
        "completed_books_250": completed_books,
        "active_days_30": active_days,
        "active_days_100": active_days,
        "active_days_365": active_days,
        "active_days_1000": active_days,
        "genres_5": completed_genres,
        "genres_10": completed_genres,
        "genres_20": completed_genres,
        "notes_10": notes_count,
        "notes_50": notes_count,
        "notes_200": notes_count,
        "quotes_100": quotes_count,
        "quotes_250": quotes_count,
        "graphic_chapters_25": completed_graphic_chapters,
        "graphic_chapters_250": completed_graphic_chapters,
        "graphic_chapters_1000": completed_graphic_chapters,
        "audio_chapters_10": completed_audio_chapters,
        "audio_chapters_100": completed_audio_chapters,
        "audio_chapters_500": completed_audio_chapters,
        "reviews_200": reviews,
        "reviews_500": reviews,
        "comments_500": comments_count,
        "comments_1000": comments_count,
        "author_completed_book_1": author_completed_books,
        "author_completed_books_5": author_completed_books,
        "author_completed_books_20": author_completed_books,
        "author_completed_books_50": author_completed_books,
        "author_words_100k": author_word_count,
        "author_words_1m": author_word_count,
        "author_words_5m": author_word_count,
        "author_words_10m": author_word_count,
        "author_rating_50": author_quality_review_progress,
        "author_rating_250": author_quality_review_progress,
        "author_library_additions_1000": author_library_additions,
        "author_hundred_thousand_readers": author_readers,
        "author_hundred_thousand_reactions": author_reactions,
    }
    if season_code:
        live_progress[season_code] = season_completed
    catalog: list[dict[str, Any]] = []
    for code, info in program_catalog.items():
        unlocked = unlocked_by_code.get(code)
        if unlocked:
            catalog.append(dict(unlocked))
            continue
        goal = max(1, int(info.get("goal") or 1))
        progress = max(0, int(live_progress.get(code, 0)))
        tier, tier_label = _achievement_tier(info)
        catalog.append({
            "code": code, **info, "tier": tier, "tier_label": tier_label,
            "points": _achievement_points(info, program),
            "progress_value": progress, "goal": goal,
            "progress_percent": min(100, round(progress * 100 / goal)),
            "unlocked": False, "awarded_at": None,
            "showcased": False, "showcase_position": None,
        })
    catalog_codes = {str(item.get("code") or "") for item in catalog}
    catalog.extend(dict(item) for item in all_items if str(item.get("code") or "") not in catalog_codes)
    showcase = sorted(
        (item for item in all_items if item.get("showcased")),
        key=lambda item: int(item.get("showcase_position") or 99),
    )
    total_points = sum(int(item.get("points") or 0) for item in all_items)
    summary = _achievement_collector_summary(total_points, len(all_items), len(catalog), program.get("levels"))
    return {
        "new": new_items,
        "items": all_items,
        "catalog": catalog,
        "showcase": showcase,
        "summary": summary,
        "program": {
            "points": program.get("points"),
            "levels": program.get("levels"),
            "season": season_settings if season_code else {"enabled": False},
        },
    }


async def list_smart_reader_reminder_candidates(limit: int = 100, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    """Выбирает не более одного мягкого напоминания на читателя по его локальному расписанию."""
    now_value = now_utc or datetime.now(timezone.utc)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    recent = (now_value - timedelta(days=90)).isoformat()
    repeat_cutoff = (now_value - timedelta(days=7)).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            WITH latest AS (
                SELECT rp.user_id, rp.book_id, MAX(rp.updated_at) AS last_read_at
                FROM reading_progress rp GROUP BY rp.user_id, rp.book_id
            )
            SELECT u.id AS user_id, u.telegram_id, u.full_name, b.id AS book_id, b.title AS book_title,
                   c.number AS chapter_number, c.title AS chapter_title, latest.last_read_at,
                   rns.reminder_hour, rns.reminder_minute, rns.reminder_weekdays,
                   rns.inactive_days, rns.timezone_offset_minutes
            FROM latest
            JOIN users u ON u.id=latest.user_id AND u.is_blocked=0
            JOIN books b ON b.id=latest.book_id AND b.publication_status='published'
            JOIN reading_progress rp ON rp.user_id=latest.user_id AND rp.book_id=latest.book_id AND rp.updated_at=latest.last_read_at
            JOIN chapters c ON c.id=rp.chapter_id
            JOIN reader_notification_settings rns ON rns.user_id=u.id AND rns.reminder_enabled=1
            LEFT JOIN bookmarks bm ON bm.user_id=u.id AND bm.book_id=b.id
            LEFT JOIN user_preferences pref ON pref.user_id=u.id
            LEFT JOIN smart_notification_state sns ON sns.user_id=u.id AND sns.notification_code='continue_reading' AND sns.context_key=CAST(b.id AS TEXT)
            WHERE latest.last_read_at>=?
              AND COALESCE(pref.notifications,1)=1 AND COALESCE(pref.notifications_reminders,1)=1
              AND COALESCE(bm.status,'reading') NOT IN ('finished','dropped')
              AND (sns.last_sent_at IS NULL OR sns.last_sent_at<?)
            ORDER BY latest.user_id, latest.last_read_at DESC
            LIMIT ?
            """,
            (recent, repeat_cutoff, max(1, min(5000, int(limit) * 12))),
        )
        rows = [dict(row) for row in await cur.fetchall()]

    result: list[dict[str, Any]] = []
    selected_users: set[int] = set()
    for row in rows:
        user_id = int(row["user_id"])
        if user_id in selected_users:
            continue
        local_now = _local_datetime(now_value, int(row.get("timezone_offset_minutes") or 0))
        weekdays = _parse_weekdays(row.get("reminder_weekdays") or "1,2,3,4,5,6,7")
        if local_now.isoweekday() not in weekdays:
            continue
        if not _schedule_hour_due(local_now, int(row.get("reminder_hour") or 19), int(row.get("reminder_minute") or 0)):
            continue
        try:
            last_read = datetime.fromisoformat(str(row.get("last_read_at") or "").replace("Z", "+00:00"))
            if last_read.tzinfo is None:
                last_read = last_read.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now_value - last_read.astimezone(timezone.utc) < timedelta(days=max(1, int(row.get("inactive_days") or 3))):
            continue
        local_context = local_now.date().isoformat()
        if await was_smart_notification_sent(user_id, "continue_reading_daily", local_context):
            continue
        row["daily_context_key"] = local_context
        result.append(row)
        selected_users.add(user_id)
        if len(result) >= max(1, int(limit)):
            break
    return result


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
        if bool(is_first_recurring):
            cur = await db.execute(
                """SELECT id FROM premium_subscriptions
                   WHERE user_id=? AND status='active' AND auto_renew=1 AND expires_at>?
                     AND telegram_payment_charge_id!=? ORDER BY expires_at DESC LIMIT 1""",
                (int(user_id), now, charge_id),
            )
            duplicate_subscription = await cur.fetchone()
            if duplicate_subscription:
                await db.rollback()
                raise DuplicatePurchaseError(
                    "Автопродление Premium уже активно", access_key="premium:auto_renew"
                )
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


async def list_grantable_books(query: str = "", limit: int | None = None) -> list[aiosqlite.Row]:
    """Return every non-deleted book available for a manual access grant.

    v1.14.0.21 removes the old silent 60/100-book cap.  The protected owner
    screen may load the full catalogue once and filter it locally, while the
    optional query/limit arguments remain available for future API clients.
    """
    clean = str(query or "").strip()
    params: list[Any] = []
    where = "b.publication_status!='deleted'"
    if clean:
        like = f"%{clean}%"
        where += " AND (b.title LIKE ? OR CAST(b.id AS TEXT)=? OR ap.pen_name LIKE ?)"
        params.extend((like, clean, like))
    limit_sql = ""
    if limit is not None:
        safe_limit = max(1, min(10000, int(limit)))
        limit_sql = " LIMIT ?"
        params.append(safe_limit)
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
            ORDER BY CASE WHEN b.publication_status='published' THEN 0 ELSE 1 END,
                     b.title COLLATE NOCASE, b.id DESC{limit_sql}
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


# v1.13.20 — расширенные полки, история, заметки, цитаты и синхронизация прогресса.
async def _ensure_v11320_library_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_shelves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            icon TEXT NOT NULL DEFAULT '📚',
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, name),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS user_shelf_books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            shelf_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(shelf_id, book_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(shelf_id) REFERENCES user_shelves(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reading_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            position_value INTEGER NOT NULL DEFAULT 0,
            open_count INTEGER NOT NULL DEFAULT 1,
            first_opened_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, content_type, target_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reader_annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            annotation_type TEXT NOT NULL DEFAULT 'note',
            selected_text TEXT NOT NULL DEFAULT '',
            note_text TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT 'violet',
            position_percent INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS progress_sync_state (
            user_id INTEGER PRIMARY KEY,
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_user_shelves_user_position
            ON user_shelves(user_id, position, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_user_shelf_books_user
            ON user_shelf_books(user_id, shelf_id, position, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_reading_history_user_updated
            ON reading_history(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_reader_annotations_user_updated
            ON reader_annotations(user_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_reader_annotations_chapter
            ON reader_annotations(user_id, chapter_id, position_percent);
        """
    )


async def _touch_progress_revision_db(db: aiosqlite.Connection, user_id: int, updated_at: str | None = None) -> None:
    now = updated_at or utc_now()
    await db.execute(
        """
        INSERT INTO progress_sync_state(user_id, revision, updated_at)
        VALUES(?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            revision=progress_sync_state.revision + 1,
            updated_at=excluded.updated_at
        """,
        (int(user_id), now),
    )


async def _record_history_db(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    content_type: str,
    target_id: int,
    book_id: int,
    position_value: int,
    updated_at: str | None = None,
) -> None:
    kind = str(content_type or "text")
    if kind not in {"text", "audio", "graphic"}:
        return
    now = updated_at or utc_now()
    await db.execute(
        """
        INSERT INTO reading_history(
            user_id, content_type, target_id, book_id, position_value,
            open_count, first_opened_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(user_id, content_type, target_id) DO UPDATE SET
            book_id=excluded.book_id,
            position_value=excluded.position_value,
            open_count=reading_history.open_count + 1,
            updated_at=excluded.updated_at
        """,
        (int(user_id), kind, int(target_id), int(book_id), max(0, int(position_value)), now, now),
    )
    # Личный дневник создаётся при первом реальном открытии произведения.
    # Ручной статус, впечатление и оценка никогда не перезаписываются историей.
    await db.execute(
        """
        INSERT INTO reader_book_journal(
            user_id, book_id, status, started_on, finished_on, impression,
            private_rating, last_activity_at, created_at, updated_at
        ) VALUES(?, ?, 'reading', substr(?,1,10), NULL, '', 0, ?, ?, ?)
        ON CONFLICT(user_id, book_id) DO UPDATE SET
            started_on=COALESCE(reader_book_journal.started_on, excluded.started_on),
            last_activity_at=excluded.last_activity_at
        """,
        (int(user_id), int(book_id), now, now, now, now),
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO reader_book_cycles(
            user_id, book_id, cycle_number, status, started_on, finished_on,
            note, created_at, updated_at
        ) VALUES(?, ?, 1, 'reading', substr(?,1,10), NULL, '', ?, ?)
        """,
        (int(user_id), int(book_id), now, now, now),
    )


async def list_user_shelves(user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT us.*, COUNT(usb.id) AS books_count
            FROM user_shelves us
            LEFT JOIN user_shelf_books usb ON usb.shelf_id=us.id
            WHERE us.user_id=?
            GROUP BY us.id
            ORDER BY us.position, us.updated_at DESC, us.id DESC
            LIMIT ?
            """,
            (int(user_id), max(1, min(100, int(limit)))),
        )
        shelves = [dict(row) for row in await cur.fetchall()]
        for shelf in shelves:
            cur = await db.execute(
                """
                SELECT usb.id AS shelf_book_id, usb.shelf_id, usb.book_id, usb.position,
                       usb.created_at, usb.updated_at, b.title, b.description, b.age_limit,
                       b.cover_path, b.cover_file_id, b.content_type, ap.pen_name,
                       (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') AS chapters_count,
                       (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published') AS graphic_chapters_count
                FROM user_shelf_books usb
                JOIN books b ON b.id=usb.book_id AND b.publication_status='published'
                LEFT JOIN author_profiles ap ON ap.id=b.author_id
                WHERE usb.user_id=? AND usb.shelf_id=?
                ORDER BY usb.position, usb.updated_at DESC, usb.id DESC
                LIMIT 100
                """,
                (int(user_id), int(shelf["id"])),
            )
            shelf["books"] = [dict(row) for row in await cur.fetchall()]
        return shelves


async def create_user_shelf(user_id: int, name: str, icon: str = "📚") -> aiosqlite.Row:
    clean_name = " ".join(str(name or "").split())[:60]
    if len(clean_name) < 2:
        raise ValueError("Название полки должно содержать не менее 2 символов.")
    clean_icon = str(icon or "📚").strip()[:8] or "📚"
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT COALESCE(MAX(position), -1)+1 AS next_position FROM user_shelves WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        try:
            cur = await db.execute(
                "INSERT INTO user_shelves(user_id,name,icon,position,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (int(user_id), clean_name, clean_icon, int(row["next_position"] or 0), now, now),
            )
        except aiosqlite.IntegrityError as exc:
            raise ValueError("Полка с таким названием уже существует.") from exc
        await db.commit()
        cur = await db.execute("SELECT * FROM user_shelves WHERE id=?", (int(cur.lastrowid),))
        return await cur.fetchone()


async def update_user_shelf(user_id: int, shelf_id: int, *, name: str | None = None, icon: str | None = None, position: int | None = None) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM user_shelves WHERE id=? AND user_id=?", (int(shelf_id), int(user_id)))
        current = await cur.fetchone()
        if not current:
            return None
        clean_name = " ".join(str(name if name is not None else current["name"]).split())[:60]
        if len(clean_name) < 2:
            raise ValueError("Название полки должно содержать не менее 2 символов.")
        clean_icon = str(icon if icon is not None else current["icon"]).strip()[:8] or "📚"
        clean_position = max(0, int(position if position is not None else current["position"] or 0))
        try:
            await db.execute(
                "UPDATE user_shelves SET name=?, icon=?, position=?, updated_at=? WHERE id=? AND user_id=?",
                (clean_name, clean_icon, clean_position, utc_now(), int(shelf_id), int(user_id)),
            )
        except aiosqlite.IntegrityError as exc:
            raise ValueError("Полка с таким названием уже существует.") from exc
        await db.commit()
        cur = await db.execute("SELECT * FROM user_shelves WHERE id=?", (int(shelf_id),))
        return await cur.fetchone()


async def delete_user_shelf(user_id: int, shelf_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute("DELETE FROM user_shelves WHERE id=? AND user_id=?", (int(shelf_id), int(user_id)))
        await db.commit()
        return cur.rowcount > 0


async def set_user_shelf_book(user_id: int, shelf_id: int, book_id: int, *, enabled: bool = True) -> bool:
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT 1 FROM user_shelves WHERE id=? AND user_id=?", (int(shelf_id), int(user_id)))
        if not await cur.fetchone():
            raise ValueError("Полка не найдена.")
        cur = await db.execute("SELECT 1 FROM books WHERE id=? AND publication_status='published'", (int(book_id),))
        if not await cur.fetchone():
            raise ValueError("Книга не найдена.")
        if not enabled:
            cur = await db.execute(
                "DELETE FROM user_shelf_books WHERE user_id=? AND shelf_id=? AND book_id=?",
                (int(user_id), int(shelf_id), int(book_id)),
            )
            await db.commit()
            return cur.rowcount > 0
        cur = await db.execute(
            "SELECT COALESCE(MAX(position), -1)+1 AS next_position FROM user_shelf_books WHERE shelf_id=?",
            (int(shelf_id),),
        )
        row = await cur.fetchone()
        await db.execute(
            """
            INSERT INTO user_shelf_books(user_id,shelf_id,book_id,position,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(shelf_id,book_id) DO UPDATE SET updated_at=excluded.updated_at
            """,
            (int(user_id), int(shelf_id), int(book_id), int(row["next_position"] or 0), now, now),
        )
        await db.commit()
        return True


async def list_user_reading_history(user_id: int, limit: int = 100) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rh.*, b.title, b.cover_path, b.cover_file_id, b.content_type AS book_content_type,
                   ap.pen_name,
                   c.title AS chapter_title, c.number AS chapter_number,
                   ac.title AS audio_title, ac.number AS audio_number, ac.duration_seconds,
                   gc.title AS graphic_title, gc.number AS graphic_number, gc.pages_count
            FROM reading_history rh
            JOIN books b ON b.id=rh.book_id AND b.publication_status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            LEFT JOIN chapters c ON rh.content_type='text' AND c.id=rh.target_id
            LEFT JOIN audio_chapters ac ON rh.content_type='audio' AND ac.id=rh.target_id
            LEFT JOIN graphic_chapters gc ON rh.content_type='graphic' AND gc.id=rh.target_id
            WHERE rh.user_id=?
            ORDER BY rh.updated_at DESC, rh.id DESC
            LIMIT ?
            """,
            (int(user_id), max(1, min(500, int(limit)))),
        )
        return await cur.fetchall()


async def delete_user_reading_history(user_id: int, history_id: int | None = None) -> int:
    async with connect() as db:
        if history_id is None:
            cur = await db.execute("DELETE FROM reading_history WHERE user_id=?", (int(user_id),))
        else:
            cur = await db.execute("DELETE FROM reading_history WHERE id=? AND user_id=?", (int(history_id), int(user_id)))
        await db.commit()
        return max(0, int(cur.rowcount or 0))


async def create_reader_annotation(
    user_id: int,
    chapter_id: int,
    *,
    annotation_type: str = "note",
    selected_text: str = "",
    note_text: str = "",
    color: str = "violet",
    position_percent: int = 0,
) -> aiosqlite.Row:
    kind = str(annotation_type or "note")
    if kind not in {"note", "quote"}:
        raise ValueError("Неизвестный тип записи.")
    selected = str(selected_text or "").strip()[:1200]
    note = str(note_text or "").strip()[:4000]
    if kind == "quote" and len(selected) < 3:
        raise ValueError("Выберите текст цитаты.")
    if kind == "note" and len(note) < 1:
        raise ValueError("Введите текст заметки.")
    clean_color = str(color or "violet")[:24]
    now = utc_now()
    async with connect() as db:
        cur = await db.execute("SELECT book_id FROM chapters WHERE id=?", (int(chapter_id),))
        chapter = await cur.fetchone()
        if not chapter:
            raise ValueError("Глава не найдена.")
        cur = await db.execute(
            """
            INSERT INTO reader_annotations(
                user_id,book_id,chapter_id,annotation_type,selected_text,note_text,color,
                position_percent,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(user_id), int(chapter["book_id"]), int(chapter_id), kind, selected, note,
                clean_color, max(0, min(100, int(position_percent))), now, now,
            ),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM reader_annotations WHERE id=?", (int(cur.lastrowid),))
        return await cur.fetchone()


async def update_reader_annotation(user_id: int, annotation_id: int, *, note_text: str | None = None, color: str | None = None) -> aiosqlite.Row | None:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM reader_annotations WHERE id=? AND user_id=?", (int(annotation_id), int(user_id)))
        row = await cur.fetchone()
        if not row:
            return None
        note = str(note_text if note_text is not None else row["note_text"]).strip()[:4000]
        clean_color = str(color if color is not None else row["color"])[:24]
        if str(row["annotation_type"]) == "note" and not note:
            raise ValueError("Заметка не может быть пустой.")
        await db.execute(
            "UPDATE reader_annotations SET note_text=?, color=?, updated_at=? WHERE id=? AND user_id=?",
            (note, clean_color, utc_now(), int(annotation_id), int(user_id)),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM reader_annotations WHERE id=?", (int(annotation_id),))
        return await cur.fetchone()


async def delete_reader_annotation(user_id: int, annotation_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute("DELETE FROM reader_annotations WHERE id=? AND user_id=?", (int(annotation_id), int(user_id)))
        await db.commit()
        return cur.rowcount > 0


async def list_reader_annotations(user_id: int, *, chapter_id: int | None = None, limit: int = 200) -> list[aiosqlite.Row]:
    query = """
        SELECT ra.*, b.title, b.cover_path, b.cover_file_id, ap.pen_name,
               c.title AS chapter_title, c.number AS chapter_number
        FROM reader_annotations ra
        JOIN books b ON b.id=ra.book_id AND b.publication_status='published'
        JOIN chapters c ON c.id=ra.chapter_id
        LEFT JOIN author_profiles ap ON ap.id=b.author_id
        WHERE ra.user_id=?
    """
    params: list[Any] = [int(user_id)]
    if chapter_id is not None:
        query += " AND ra.chapter_id=?"
        params.append(int(chapter_id))
    query += " ORDER BY ra.updated_at DESC, ra.id DESC LIMIT ?"
    params.append(max(1, min(500, int(limit))))
    async with connect() as db:
        cur = await db.execute(query, tuple(params))
        return await cur.fetchall()


async def get_progress_sync_snapshot(user_id: int, limit: int = 300) -> dict[str, Any]:
    cap = max(1, min(1000, int(limit)))
    async with connect() as db:
        cur = await db.execute("SELECT revision, updated_at FROM progress_sync_state WHERE user_id=?", (int(user_id),))
        state = await cur.fetchone()
        cur = await db.execute(
            "SELECT chapter_id AS target_id, position_percent AS position, updated_at FROM reading_progress WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (int(user_id), cap),
        )
        text_rows = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            "SELECT audio_chapter_id AS target_id, position_seconds AS position, updated_at FROM listening_progress WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (int(user_id), cap),
        )
        audio_rows = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            "SELECT graphic_chapter_id AS target_id, page_number AS position, updated_at FROM graphic_reading_progress WHERE user_id=? ORDER BY updated_at DESC LIMIT ?",
            (int(user_id), cap),
        )
        graphic_rows = [dict(row) for row in await cur.fetchall()]
        return {
            "revision": int(state["revision"] or 0) if state else 0,
            "updated_at": str(state["updated_at"] or "") if state else "",
            "text": text_rows,
            "audio": audio_rows,
            "graphic": graphic_rows,
        }


# v1.13.21 — статистика чтения, личные цели, серии и календарь активности.
async def _ensure_v11321_reading_stats_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_activity_daily (
            user_id INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            text_chapters INTEGER NOT NULL DEFAULT 0,
            text_progress_points INTEGER NOT NULL DEFAULT 0,
            audio_seconds INTEGER NOT NULL DEFAULT 0,
            graphic_pages INTEGER NOT NULL DEFAULT 0,
            sessions INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, activity_date),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reader_activity_targets (
            user_id INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            content_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            first_position INTEGER NOT NULL DEFAULT 0,
            last_position INTEGER NOT NULL DEFAULT 0,
            accumulated_value INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, activity_date, content_type, target_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reader_goal_settings (
            user_id INTEGER PRIMARY KEY,
            active_days_week INTEGER NOT NULL DEFAULT 5,
            text_chapters_week INTEGER NOT NULL DEFAULT 7,
            audio_minutes_week INTEGER NOT NULL DEFAULT 120,
            graphic_pages_week INTEGER NOT NULL DEFAULT 100,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reader_activity_daily_user_date
            ON reader_activity_daily(user_id, activity_date DESC);
        CREATE INDEX IF NOT EXISTS idx_reader_activity_targets_user_date
            ON reader_activity_targets(user_id, activity_date DESC, content_type);
        """
    )

    # Однократная мягкая отправная точка из уже существующей истории v1.13.20.
    # Она создаёт только факт активности по последнему известному дню и не
    # приписывает пользователю минуты, страницы или проценты задним числом.
    cur = await db.execute("SELECT value FROM settings WHERE key='v11321_activity_backfill_done'")
    if not await cur.fetchone():
        now = utc_now()
        await db.execute(
            """
            INSERT OR IGNORE INTO reader_activity_targets(
                user_id, activity_date, content_type, target_id,
                first_position, last_position, accumulated_value, created_at, updated_at
            )
            SELECT user_id, substr(updated_at, 1, 10), content_type, target_id,
                   position_value, position_value, 0, updated_at, updated_at
            FROM reading_history
            WHERE length(updated_at) >= 10
              AND content_type IN ('text','audio','graphic')
            """
        )
        await db.execute(
            """
            INSERT INTO reader_activity_daily(
                user_id, activity_date, text_chapters, text_progress_points,
                audio_seconds, graphic_pages, sessions, created_at, updated_at
            )
            SELECT user_id, activity_date,
                   SUM(CASE WHEN content_type='text' THEN 1 ELSE 0 END),
                   0, 0, 0, COUNT(*), MIN(created_at), MAX(updated_at)
            FROM reader_activity_targets
            GROUP BY user_id, activity_date
            ON CONFLICT(user_id, activity_date) DO NOTHING
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO settings(key,value,updated_at) VALUES('v11321_activity_backfill_done','1',?)",
            (now,),
        )


# v1.13.22 — персональное расписание чтения и недельные отчёты.
async def _ensure_v11322_reading_notification_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_notification_settings (
            user_id INTEGER PRIMARY KEY,
            reminder_enabled INTEGER NOT NULL DEFAULT 1,
            reminder_hour INTEGER NOT NULL DEFAULT 19,
            reminder_minute INTEGER NOT NULL DEFAULT 0,
            reminder_weekdays TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
            inactive_days INTEGER NOT NULL DEFAULT 3,
            weekly_report_enabled INTEGER NOT NULL DEFAULT 1,
            weekly_report_weekday INTEGER NOT NULL DEFAULT 7,
            weekly_report_hour INTEGER NOT NULL DEFAULT 20,
            weekly_report_minute INTEGER NOT NULL DEFAULT 0,
            timezone_offset_minutes INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reader_notification_schedule
            ON reader_notification_settings(reminder_enabled, reminder_hour, weekly_report_enabled, weekly_report_hour);
        """
    )
    now = utc_now()
    await db.execute(
        """
        INSERT OR IGNORE INTO reader_notification_settings(user_id, updated_at)
        SELECT id, ? FROM users
        """,
        (now,),
    )


# v1.13.23 — месячные итоги, сравнение периодов и мягкие рекомендации по ритму.
async def _ensure_v11323_monthly_reading_schema(db: aiosqlite.Connection) -> None:
    cur = await db.execute("PRAGMA table_info(reader_notification_settings)")
    existing = {row[1] for row in await cur.fetchall()}
    migrations = {
        "monthly_report_enabled": "ALTER TABLE reader_notification_settings ADD COLUMN monthly_report_enabled INTEGER NOT NULL DEFAULT 1",
        "monthly_report_day": "ALTER TABLE reader_notification_settings ADD COLUMN monthly_report_day INTEGER NOT NULL DEFAULT 1",
        "monthly_report_hour": "ALTER TABLE reader_notification_settings ADD COLUMN monthly_report_hour INTEGER NOT NULL DEFAULT 20",
        "monthly_report_minute": "ALTER TABLE reader_notification_settings ADD COLUMN monthly_report_minute INTEGER NOT NULL DEFAULT 0",
    }
    for column, sql in migrations.items():
        if column not in existing:
            await _execute_schema_ddl(db, sql)

    await db.execute(
        """
        UPDATE reader_notification_settings
        SET monthly_report_day=CASE WHEN monthly_report_day<1 THEN 1 WHEN monthly_report_day>7 THEN 7 ELSE monthly_report_day END,
            monthly_report_hour=CASE WHEN monthly_report_hour<0 THEN 20 WHEN monthly_report_hour>23 THEN 20 ELSE monthly_report_hour END,
            monthly_report_minute=CASE WHEN monthly_report_minute<0 THEN 0 WHEN monthly_report_minute>59 THEN 0 ELSE monthly_report_minute END
        """
    )



# v1.13.25 — приватный читательский дневник и безопасный экспорт истории.
async def _ensure_v11325_reading_journal_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_book_journal (
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'reading',
            started_on TEXT,
            finished_on TEXT,
            impression TEXT NOT NULL DEFAULT '',
            private_rating INTEGER NOT NULL DEFAULT 0,
            last_activity_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, book_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reader_book_journal_user_updated
            ON reader_book_journal(user_id, updated_at DESC, book_id);
        CREATE INDEX IF NOT EXISTS idx_reader_book_journal_user_status
            ON reader_book_journal(user_id, status, finished_on DESC);
        """
    )
    now = utc_now()
    # Переносим уже существующую историю один раз на каждое произведение.
    await db.execute(
        """
        INSERT OR IGNORE INTO reader_book_journal(
            user_id, book_id, status, started_on, finished_on, impression,
            private_rating, last_activity_at, created_at, updated_at
        )
        SELECT rh.user_id, rh.book_id,
               CASE
                   WHEN bm.status='finished' THEN 'finished'
                   WHEN bm.status='dropped' THEN 'dropped'
                   WHEN bm.status='planned' THEN 'planned'
                   ELSE 'reading'
               END,
               substr(MIN(rh.first_opened_at),1,10),
               CASE WHEN bm.status='finished' THEN substr(MAX(bm.updated_at),1,10) ELSE NULL END,
               '', 0, MAX(rh.updated_at), MIN(rh.first_opened_at), MAX(rh.updated_at)
        FROM reading_history rh
        LEFT JOIN bookmarks bm ON bm.user_id=rh.user_id AND bm.book_id=rh.book_id
        GROUP BY rh.user_id, rh.book_id
        """
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO reader_book_journal(
            user_id, book_id, status, started_on, finished_on, impression,
            private_rating, last_activity_at, created_at, updated_at
        )
        SELECT bm.user_id, bm.book_id,
               CASE
                   WHEN bm.status='finished' THEN 'finished'
                   WHEN bm.status='dropped' THEN 'dropped'
                   WHEN bm.status='planned' THEN 'planned'
                   ELSE 'reading'
               END,
               CASE WHEN bm.status='planned' THEN NULL ELSE substr(bm.created_at,1,10) END,
               CASE WHEN bm.status='finished' THEN substr(bm.updated_at,1,10) ELSE NULL END,
               '', 0, NULL, bm.created_at, bm.updated_at
        FROM bookmarks bm
        """
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO reader_book_journal(
            user_id, book_id, status, started_on, finished_on, impression,
            private_rating, last_activity_at, created_at, updated_at
        )
        SELECT ra.user_id, ra.book_id, 'reading', substr(MIN(ra.created_at),1,10), NULL,
               '', 0, MAX(ra.updated_at), MIN(ra.created_at), MAX(ra.updated_at)
        FROM reader_annotations ra
        GROUP BY ra.user_id, ra.book_id
        """
    )
    await db.execute(
        """
        UPDATE reader_book_journal
        SET status=CASE WHEN status IN ('planned','reading','paused','finished','dropped') THEN status ELSE 'reading' END,
            private_rating=CASE WHEN private_rating<0 THEN 0 WHEN private_rating>5 THEN 5 ELSE private_rating END,
            updated_at=COALESCE(NULLIF(updated_at,''), ?),
            created_at=COALESCE(NULLIF(created_at,''), ?)
        """,
        (now, now),
    )



# v1.13.26 — циклы чтения, личные списки года и календарь завершений.
async def _ensure_v11326_reread_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_book_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            cycle_number INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'reading',
            started_on TEXT,
            finished_on TEXT,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, book_id, cycle_number),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reader_year_list_items (
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            list_year INTEGER NOT NULL,
            list_code TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, book_id, list_year, list_code),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reader_book_cycles_user_book
            ON reader_book_cycles(user_id, book_id, cycle_number DESC);
        CREATE INDEX IF NOT EXISTS idx_reader_book_cycles_finished
            ON reader_book_cycles(user_id, finished_on, status);
        CREATE INDEX IF NOT EXISTS idx_reader_year_lists_user_year
            ON reader_year_list_items(user_id, list_year DESC, list_code, updated_at DESC);
        """
    )
    now = utc_now()
    # Существующий дневник становится первым циклом. INSERT OR IGNORE делает
    # миграцию безопасной при каждом Redeploy и не создаёт повторов.
    await db.execute(
        """
        INSERT OR IGNORE INTO reader_book_cycles(
            user_id, book_id, cycle_number, status, started_on, finished_on,
            note, created_at, updated_at
        )
        SELECT user_id, book_id, 1,
               CASE
                   WHEN status='finished' THEN 'finished'
                   WHEN status='paused' THEN 'paused'
                   WHEN status='dropped' THEN 'dropped'
                   ELSE 'reading'
               END,
               started_on,
               CASE WHEN status='finished' THEN finished_on ELSE NULL END,
               '', created_at, updated_at
        FROM reader_book_journal
        WHERE status!='planned' OR started_on IS NOT NULL OR finished_on IS NOT NULL
        """
    )
    await db.execute(
        """
        UPDATE reader_book_cycles
        SET cycle_number=CASE WHEN cycle_number<1 THEN 1 ELSE cycle_number END,
            status=CASE WHEN status IN ('reading','paused','finished','dropped') THEN status ELSE 'reading' END,
            note=COALESCE(note,''),
            created_at=COALESCE(NULLIF(created_at,''), ?),
            updated_at=COALESCE(NULLIF(updated_at,''), ?)
        """,
        (now, now),
    )
    await db.execute(
        """
        DELETE FROM reader_year_list_items
        WHERE list_code NOT IN ('best','discovery','emotional','reread')
           OR list_year<1900 OR list_year>9999
        """
    )



# v1.13.27 — безопасный двухэтапный импорт личного дневника из JSON.
async def _ensure_v11327_journal_import_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_journal_import_previews (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            source_version TEXT NOT NULL,
            source_generated_at TEXT,
            normalized_json TEXT NOT NULL,
            preview_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            applied_at TEXT,
            result_json TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reader_journal_import_user_created
            ON reader_journal_import_previews(user_id, created_at DESC);
        """
    )
    # Предпросмотры одноразовые и короткоживущие. Старые записи не содержат
    # читательских данных после очистки и не раздувают рабочую базу.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    await db.execute(
        "DELETE FROM reader_journal_import_previews WHERE created_at<? OR (applied_at IS NOT NULL AND applied_at<?)",
        (cutoff, cutoff),
    )



# v1.13.28 — резервные точки, история восстановлений и безопасный откат.
async def _ensure_v11328_journal_import_history_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS reader_journal_import_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            preview_token TEXT NOT NULL UNIQUE,
            source_version TEXT NOT NULL,
            source_generated_at TEXT,
            backup_json TEXT NOT NULL,
            before_snapshot_json TEXT NOT NULL,
            after_snapshot_json TEXT NOT NULL,
            result_json TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            rolled_back_at TEXT,
            rollback_result_json TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reader_journal_import_runs_user_applied
            ON reader_journal_import_runs(user_id, applied_at DESC, id DESC);
        """
    )


_JOURNAL_SNAPSHOT_FIELDS = (
    'status', 'started_on', 'finished_on', 'impression', 'private_rating',
    'last_activity_at', 'created_at', 'updated_at',
)
_CYCLE_SNAPSHOT_FIELDS = (
    'cycle_number', 'status', 'started_on', 'finished_on', 'note',
    'created_at', 'updated_at',
)
_YEAR_LIST_SNAPSHOT_FIELDS = (
    'list_year', 'list_code', 'note', 'created_at', 'updated_at',
)


def _snapshot_row(row: Any, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if row is None:
        return None
    data = dict(row)
    return {field: data.get(field) for field in fields}


def _snapshot_key_parts(value: str, expected: int) -> tuple[str, ...]:
    parts = tuple(str(value or '').split(':'))
    if len(parts) != expected:
        raise ValueError('Резервная точка повреждена.')
    return parts


async def _capture_user_import_snapshot(
    db: aiosqlite.Connection,
    user_id: int,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    journal_ids = sorted({
        int(op['source']['book_id'])
        for op in operations
        if op.get('kind') in {'journal', 'cycle'}
    })
    cycle_keys = sorted({
        (int(op['source']['book_id']), int(op['source']['cycle_number']))
        for op in operations if op.get('kind') == 'cycle'
    })
    year_keys = sorted({
        (int(op['source']['book_id']), int(op['source']['year']), str(op['source']['list_code']))
        for op in operations if op.get('kind') == 'year_list'
    })
    snapshot: dict[str, Any] = {'journal': {}, 'cycles': {}, 'year_lists': {}}
    for book_id in journal_ids:
        cur = await db.execute(
            'SELECT * FROM reader_book_journal WHERE user_id=? AND book_id=?',
            (int(user_id), book_id),
        )
        snapshot['journal'][str(book_id)] = _snapshot_row(await cur.fetchone(), _JOURNAL_SNAPSHOT_FIELDS)
    for book_id, cycle_number in cycle_keys:
        cur = await db.execute(
            'SELECT * FROM reader_book_cycles WHERE user_id=? AND book_id=? AND cycle_number=?',
            (int(user_id), book_id, cycle_number),
        )
        snapshot['cycles'][f'{book_id}:{cycle_number}'] = _snapshot_row(await cur.fetchone(), _CYCLE_SNAPSHOT_FIELDS)
    for book_id, list_year, list_code in year_keys:
        cur = await db.execute(
            'SELECT * FROM reader_year_list_items WHERE user_id=? AND book_id=? AND list_year=? AND list_code=?',
            (int(user_id), book_id, list_year, list_code),
        )
        snapshot['year_lists'][f'{book_id}:{list_year}:{list_code}'] = _snapshot_row(await cur.fetchone(), _YEAR_LIST_SNAPSHOT_FIELDS)
    return snapshot


async def _build_user_journal_backup(db: aiosqlite.Connection, user_id: int, generated_at: str) -> dict[str, Any]:
    cur = await db.execute(
        """
        SELECT rbj.book_id, rbj.status, rbj.started_on, rbj.finished_on,
               rbj.impression, rbj.private_rating, rbj.last_activity_at,
               rbj.created_at, rbj.updated_at, b.title, b.content_type, ap.pen_name
        FROM reader_book_journal rbj
        JOIN books b ON b.id=rbj.book_id
        LEFT JOIN author_profiles ap ON ap.id=b.author_id
        WHERE rbj.user_id=? ORDER BY rbj.book_id
        """,
        (int(user_id),),
    )
    journal_rows = [dict(row) for row in await cur.fetchall()]
    cur = await db.execute(
        """
        SELECT rbc.book_id, rbc.cycle_number, rbc.status, rbc.started_on,
               rbc.finished_on, rbc.note, rbc.created_at, rbc.updated_at,
               b.title, ap.pen_name
        FROM reader_book_cycles rbc
        JOIN books b ON b.id=rbc.book_id
        LEFT JOIN author_profiles ap ON ap.id=b.author_id
        WHERE rbc.user_id=? ORDER BY rbc.book_id, rbc.cycle_number
        """,
        (int(user_id),),
    )
    cycle_rows = [dict(row) for row in await cur.fetchall()]
    cur = await db.execute(
        """
        SELECT ryli.book_id, ryli.list_year, ryli.list_code, ryli.note,
               ryli.created_at, ryli.updated_at, b.title, ap.pen_name
        FROM reader_year_list_items ryli
        JOIN books b ON b.id=ryli.book_id
        LEFT JOIN author_profiles ap ON ap.id=b.author_id
        WHERE ryli.user_id=? ORDER BY ryli.list_year, ryli.list_code, ryli.book_id
        """,
        (int(user_id),),
    )
    list_rows = [dict(row) for row in await cur.fetchall()]
    return {
        'export_version': '1.2',
        'generated_at': generated_at,
        'privacy': 'Автоматическая приватная резервная точка перед восстановлением дневника.',
        'backup_reason': 'before_journal_import',
        'journal': [{
            'book_id': int(item.get('book_id') or 0),
            'title': str(item.get('title') or ''),
            'author': str(item.get('pen_name') or ''),
            'content_type': str(item.get('content_type') or 'book'),
            'status': str(item.get('status') or 'reading'),
            'started_on': str(item.get('started_on') or ''),
            'finished_on': str(item.get('finished_on') or ''),
            'private_rating': int(item.get('private_rating') or 0),
            'impression': str(item.get('impression') or ''),
            'last_activity_at': str(item.get('last_activity_at') or ''),
            'created_at': str(item.get('created_at') or ''),
            'updated_at': str(item.get('updated_at') or ''),
        } for item in journal_rows],
        'reading_cycles': [{
            'book_id': int(item.get('book_id') or 0),
            'title': str(item.get('title') or ''),
            'author': str(item.get('pen_name') or ''),
            'cycle_number': int(item.get('cycle_number') or 1),
            'status': str(item.get('status') or 'reading'),
            'started_on': str(item.get('started_on') or ''),
            'finished_on': str(item.get('finished_on') or ''),
            'note': str(item.get('note') or ''),
            'created_at': str(item.get('created_at') or ''),
            'updated_at': str(item.get('updated_at') or ''),
        } for item in cycle_rows],
        'year_lists': [{
            'book_id': int(item.get('book_id') or 0),
            'title': str(item.get('title') or ''),
            'author': str(item.get('pen_name') or ''),
            'year': int(item.get('list_year') or 0),
            'list_code': str(item.get('list_code') or ''),
            'note': str(item.get('note') or ''),
            'created_at': str(item.get('created_at') or ''),
            'updated_at': str(item.get('updated_at') or ''),
        } for item in list_rows],
        'history': [],
        'annotations': [],
        'daily_activity': [],
    }


def _import_result_counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {'journal': 0, 'cycles': 0, 'year_lists': 0, 'total': 0}
    applied = raw.get('applied') if isinstance(raw.get('applied'), dict) else {}
    journal = int(applied.get('journal') or 0)
    cycles = int(applied.get('cycles') or 0)
    year_lists = int(applied.get('year_lists') or 0)
    return {'journal': journal, 'cycles': cycles, 'year_lists': year_lists, 'total': int(raw.get('total_applied') or journal + cycles + year_lists)}


def _rollback_result_counts(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {'restored': 0, 'protected': 0}
    return {
        'restored': int(raw.get('total_restored') or 0),
        'protected': int(raw.get('total_protected') or 0),
    }


async def list_user_reading_import_history(user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT id, source_version, source_generated_at, result_json, applied_at,
                   rolled_back_at, rollback_result_json
            FROM reader_journal_import_runs
            WHERE user_id=? ORDER BY applied_at DESC, id DESC LIMIT ?
            """,
            (int(user_id), max(1, min(50, int(limit)))),
        )
        rows = [dict(row) for row in await cur.fetchall()]
    history: list[dict[str, Any]] = []
    chain_clear = True
    rollback_slot_used = False
    for row in rows:
        try:
            result = json.loads(str(row.get('result_json') or '{}'))
        except json.JSONDecodeError:
            result = {}
        try:
            rollback_result = json.loads(str(row.get('rollback_result_json') or '{}'))
        except json.JSONDecodeError:
            rollback_result = {}
        rollback_counts = _rollback_result_counts(rollback_result)
        rolled_back = bool(row.get('rolled_back_at'))
        can_rollback = bool(chain_clear and not rollback_slot_used and not rolled_back)
        if can_rollback:
            rollback_slot_used = True
        if rolled_back and rollback_counts['protected'] > 0:
            chain_clear = False
        history.append({
            'id': int(row['id']),
            'source_version': str(row.get('source_version') or ''),
            'source_generated_at': str(row.get('source_generated_at') or ''),
            'applied_at': str(row.get('applied_at') or ''),
            'rolled_back_at': str(row.get('rolled_back_at') or ''),
            'status': 'rolled_back_partial' if rolled_back and rollback_counts['protected'] else ('rolled_back' if rolled_back else 'applied'),
            'counts': _import_result_counts(result),
            'rollback_counts': rollback_counts,
            'can_rollback': can_rollback,
            'backup_available': True,
        })
    return history


async def get_user_reading_import_backup(user_id: int, run_id: int) -> dict[str, Any]:
    async with connect() as db:
        cur = await db.execute(
            'SELECT backup_json FROM reader_journal_import_runs WHERE id=? AND user_id=?',
            (int(run_id), int(user_id)),
        )
        row = await cur.fetchone()
    if not row:
        raise ValueError('Резервная точка не найдена.')
    try:
        payload = json.loads(str(row['backup_json'] or '{}'))
    except json.JSONDecodeError as exc:
        raise ValueError('Резервная точка повреждена.') from exc
    if not isinstance(payload, dict):
        raise ValueError('Резервная точка повреждена.')
    return payload


async def _rollback_import_row(
    db: aiosqlite.Connection,
    *,
    table: str,
    user_id: int,
    key: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> tuple[bool, str]:
    if table == 'journal':
        (book_raw,) = _snapshot_key_parts(key, 1)
        book_id = int(book_raw)
        cur = await db.execute('SELECT * FROM reader_book_journal WHERE user_id=? AND book_id=?', (int(user_id), book_id))
        current = _snapshot_row(await cur.fetchone(), _JOURNAL_SNAPSHOT_FIELDS)
        if current != after:
            return False, f'Дневник · книга {book_id}'
        if before is None:
            await db.execute('DELETE FROM reader_book_journal WHERE user_id=? AND book_id=?', (int(user_id), book_id))
        else:
            await db.execute(
                """
                UPDATE reader_book_journal SET status=?, started_on=?, finished_on=?, impression=?,
                    private_rating=?, last_activity_at=?, created_at=?, updated_at=?
                WHERE user_id=? AND book_id=?
                """,
                tuple(before.get(field) for field in _JOURNAL_SNAPSHOT_FIELDS) + (int(user_id), book_id),
            )
        return True, ''
    if table == 'cycles':
        book_raw, cycle_raw = _snapshot_key_parts(key, 2)
        book_id, cycle_number = int(book_raw), int(cycle_raw)
        cur = await db.execute(
            'SELECT * FROM reader_book_cycles WHERE user_id=? AND book_id=? AND cycle_number=?',
            (int(user_id), book_id, cycle_number),
        )
        current = _snapshot_row(await cur.fetchone(), _CYCLE_SNAPSHOT_FIELDS)
        if current != after:
            return False, f'Цикл №{cycle_number} · книга {book_id}'
        if before is None:
            await db.execute(
                'DELETE FROM reader_book_cycles WHERE user_id=? AND book_id=? AND cycle_number=?',
                (int(user_id), book_id, cycle_number),
            )
        else:
            await db.execute(
                """
                UPDATE reader_book_cycles SET cycle_number=?, status=?, started_on=?, finished_on=?,
                    note=?, created_at=?, updated_at=?
                WHERE user_id=? AND book_id=? AND cycle_number=?
                """,
                tuple(before.get(field) for field in _CYCLE_SNAPSHOT_FIELDS) + (int(user_id), book_id, cycle_number),
            )
        return True, ''
    if table == 'year_lists':
        book_raw, year_raw, list_code = _snapshot_key_parts(key, 3)
        book_id, list_year = int(book_raw), int(year_raw)
        cur = await db.execute(
            'SELECT * FROM reader_year_list_items WHERE user_id=? AND book_id=? AND list_year=? AND list_code=?',
            (int(user_id), book_id, list_year, list_code),
        )
        current = _snapshot_row(await cur.fetchone(), _YEAR_LIST_SNAPSHOT_FIELDS)
        if current != after:
            return False, f'Список года · книга {book_id}'
        if before is None:
            await db.execute(
                'DELETE FROM reader_year_list_items WHERE user_id=? AND book_id=? AND list_year=? AND list_code=?',
                (int(user_id), book_id, list_year, list_code),
            )
        else:
            await db.execute(
                """
                UPDATE reader_year_list_items SET note=?, created_at=?, updated_at=?
                WHERE user_id=? AND book_id=? AND list_year=? AND list_code=?
                """,
                (before.get('note'), before.get('created_at'), before.get('updated_at'), int(user_id), book_id, list_year, list_code),
            )
        return True, ''
    raise ValueError('Неизвестный раздел резервной точки.')


async def rollback_user_reading_import(user_id: int, run_id: int) -> dict[str, Any]:
    now = utc_now()
    async with connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        cur = await db.execute(
            'SELECT * FROM reader_journal_import_runs WHERE id=? AND user_id=?',
            (int(run_id), int(user_id)),
        )
        run = await cur.fetchone()
        if not run:
            raise ValueError('Импорт не найден.')
        if run['rolled_back_at']:
            raise ValueError('Этот импорт уже отменён.')
        cur = await db.execute(
            """
            SELECT rolled_back_at, rollback_result_json
            FROM reader_journal_import_runs
            WHERE user_id=? AND (applied_at>? OR (applied_at=? AND id>?))
            ORDER BY applied_at DESC, id DESC
            """,
            (int(user_id), run['applied_at'], run['applied_at'], int(run_id)),
        )
        newer = [dict(row) for row in await cur.fetchall()]
        for item in newer:
            if not item.get('rolled_back_at'):
                raise ValueError('Сначала отмените более новый импорт.')
            try:
                newer_result = json.loads(str(item.get('rollback_result_json') or '{}'))
            except json.JSONDecodeError:
                newer_result = {}
            if _rollback_result_counts(newer_result)['protected'] > 0:
                raise ValueError('Более новый импорт отменён частично. Старую точку откатывать небезопасно.')
        try:
            before = json.loads(str(run['before_snapshot_json'] or '{}'))
            after = json.loads(str(run['after_snapshot_json'] or '{}'))
        except json.JSONDecodeError as exc:
            raise ValueError('Резервная точка повреждена.') from exc
        restored = {'journal': 0, 'cycles': 0, 'year_lists': 0}
        protected: list[str] = []
        protected_count = 0
        for table in ('year_lists', 'cycles', 'journal'):
            before_rows = before.get(table) if isinstance(before.get(table), dict) else {}
            after_rows = after.get(table) if isinstance(after.get(table), dict) else {}
            for key in sorted(set(before_rows) | set(after_rows)):
                ok, label = await _rollback_import_row(
                    db,
                    table=table,
                    user_id=int(user_id),
                    key=key,
                    before=before_rows.get(key),
                    after=after_rows.get(key),
                )
                if ok:
                    restored[table] += 1
                else:
                    protected_count += 1
                    if len(protected) < 30:
                        protected.append(label)
        result = {
            'restored': restored,
            'total_restored': sum(restored.values()),
            'total_protected': protected_count,
            'protected_examples': protected,
            'rolled_back_at': now,
            'safety_note': 'Ручные изменения после импорта сохранены и не были перезаписаны.',
        }
        await db.execute(
            'UPDATE reader_journal_import_runs SET rolled_back_at=?, rollback_result_json=? WHERE id=? AND user_id=?',
            (now, json.dumps(result, ensure_ascii=False, separators=(',', ':')), int(run_id), int(user_id)),
        )
        await db.commit()
    return result


def _reading_cycle_status(value: object) -> str:
    status = str(value or 'reading').strip().lower()
    if status not in {'reading', 'paused', 'finished', 'dropped'}:
        raise ValueError('Неизвестный статус цикла чтения.')
    return status


def _year_list_codes(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = str(value or '').replace(';', ',').split(',')
    allowed = {'best', 'discovery', 'emotional', 'reread'}
    result = []
    for item in raw:
        code = str(item or '').strip().lower()
        if not code:
            continue
        if code not in allowed:
            raise ValueError('Неизвестный личный список года.')
        if code not in result:
            result.append(code)
    return result


async def list_user_reading_cycles(user_id: int, book_id: int | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT rbc.*, b.title, b.content_type, ap.pen_name
        FROM reader_book_cycles rbc
        JOIN books b ON b.id=rbc.book_id
        LEFT JOIN author_profiles ap ON ap.id=b.author_id
        WHERE rbc.user_id=?
    """
    params: list[Any] = [int(user_id)]
    if book_id is not None:
        query += ' AND rbc.book_id=?'
        params.append(int(book_id))
    query += ' ORDER BY rbc.book_id, rbc.cycle_number DESC, rbc.id DESC'
    async with connect() as db:
        cur = await db.execute(query, tuple(params))
        return [dict(row) for row in await cur.fetchall()]


async def get_user_reread_summary(user_id: int) -> dict[str, Any]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) AS total_cycles,
                   SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END) AS completed_cycles,
                   SUM(CASE WHEN cycle_number>1 THEN 1 ELSE 0 END) AS reread_cycles,
                   SUM(CASE WHEN cycle_number>1 AND status='finished' THEN 1 ELSE 0 END) AS completed_rereads,
                   SUM(CASE WHEN status IN ('reading','paused') THEN 1 ELSE 0 END) AS active_cycles,
                   COUNT(DISTINCT CASE WHEN cycle_number>1 THEN book_id END) AS books_reread
            FROM reader_book_cycles WHERE user_id=?
            """,
            (int(user_id),),
        )
        row = await cur.fetchone()
    return {
        'total_cycles': int(row['total_cycles'] or 0),
        'completed_cycles': int(row['completed_cycles'] or 0),
        'reread_cycles': int(row['reread_cycles'] or 0),
        'completed_rereads': int(row['completed_rereads'] or 0),
        'active_cycles': int(row['active_cycles'] or 0),
        'books_reread': int(row['books_reread'] or 0),
    }


async def start_user_reread_cycle(
    user_id: int,
    book_id: int,
    *,
    started_on: object = None,
    note: object = '',
) -> dict[str, Any]:
    clean_started = _parse_journal_date(started_on, 'Дата начала перечитывания') or datetime.now(timezone.utc).date().isoformat()
    clean_note = str(note or '').strip()[:2000]
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM books WHERE id=? AND publication_status='published'",
            (int(book_id),),
        )
        if not await cur.fetchone():
            raise ValueError('Произведение не найдено.')
        cur = await db.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status IN ('reading','paused') THEN 1 ELSE 0 END) AS active,
                   COALESCE(MAX(cycle_number),0) AS max_cycle
            FROM reader_book_cycles WHERE user_id=? AND book_id=?
            """,
            (int(user_id), int(book_id)),
        )
        state = await cur.fetchone()
        if int(state['active'] or 0) > 0:
            raise ValueError('Сначала завершите или остановите текущий цикл чтения.')
        if int(state['completed'] or 0) <= 0:
            raise ValueError('Перечитывание можно начать после завершения первого чтения.')
        next_cycle = int(state['max_cycle'] or 0) + 1
        cur = await db.execute(
            """
            INSERT INTO reader_book_cycles(
                user_id, book_id, cycle_number, status, started_on, finished_on,
                note, created_at, updated_at
            ) VALUES(?,?,?,'reading',?,NULL,?,?,?)
            """,
            (int(user_id), int(book_id), next_cycle, clean_started, clean_note, now, now),
        )
        cycle_id = int(cur.lastrowid)
        await db.execute(
            """
            UPDATE reader_book_journal
            SET status='reading', started_on=COALESCE(started_on, ?), updated_at=?
            WHERE user_id=? AND book_id=?
            """,
            (clean_started, now, int(user_id), int(book_id)),
        )
        await db.commit()
        cur = await db.execute('SELECT * FROM reader_book_cycles WHERE id=?', (cycle_id,))
        row = await cur.fetchone()
    return dict(row) if row else {}


async def update_user_reading_cycle(
    user_id: int,
    cycle_id: int,
    *,
    status: object,
    started_on: object,
    finished_on: object,
    note: object = '',
) -> dict[str, Any]:
    clean_status = _reading_cycle_status(status)
    clean_started = _parse_journal_date(started_on, 'Дата начала цикла')
    clean_finished = _parse_journal_date(finished_on, 'Дата завершения цикла')
    clean_note = str(note or '').strip()[:2000]
    today = datetime.now(timezone.utc).date().isoformat()
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            'SELECT * FROM reader_book_cycles WHERE id=? AND user_id=?',
            (int(cycle_id), int(user_id)),
        )
        current = await cur.fetchone()
        if not current:
            raise ValueError('Цикл чтения не найден.')
        if not clean_started:
            clean_started = str(current['started_on'] or '') or today
        if clean_status == 'finished':
            clean_finished = clean_finished or str(current['finished_on'] or '') or today
        else:
            clean_finished = None
        if clean_started and clean_finished and clean_started > clean_finished:
            raise ValueError('Дата завершения не может быть раньше даты начала.')
        await db.execute(
            """
            UPDATE reader_book_cycles
            SET status=?, started_on=?, finished_on=?, note=?, updated_at=?
            WHERE id=? AND user_id=?
            """,
            (clean_status, clean_started, clean_finished, clean_note, now, int(cycle_id), int(user_id)),
        )
        book_id = int(current['book_id'])
        cur = await db.execute(
            """
            SELECT status, started_on, finished_on, cycle_number
            FROM reader_book_cycles
            WHERE user_id=? AND book_id=?
            ORDER BY cycle_number DESC, id DESC LIMIT 1
            """,
            (int(user_id), book_id),
        )
        latest = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT MIN(started_on) AS first_started, MAX(CASE WHEN status='finished' THEN finished_on END) AS last_finished
            FROM reader_book_cycles WHERE user_id=? AND book_id=?
            """,
            (int(user_id), book_id),
        )
        aggregate = await cur.fetchone()
        await db.execute(
            """
            UPDATE reader_book_journal
            SET status=?, started_on=COALESCE(?, started_on), finished_on=?, updated_at=?
            WHERE user_id=? AND book_id=?
            """,
            (
                str(latest['status'] or 'reading') if latest else clean_status,
                str(aggregate['first_started'] or '') or None,
                str(aggregate['last_finished'] or '') or None,
                now, int(user_id), book_id,
            ),
        )
        await db.commit()
        cur = await db.execute('SELECT * FROM reader_book_cycles WHERE id=?', (int(cycle_id),))
        row = await cur.fetchone()
    return dict(row) if row else {}


async def set_user_year_lists(
    user_id: int,
    book_id: int,
    *,
    list_year: int,
    list_codes: object,
) -> list[dict[str, Any]]:
    year = int(list_year)
    current_year = datetime.now(timezone.utc).year
    if year < 1900 or year > current_year:
        raise ValueError('Год списка указан неверно.')
    codes = _year_list_codes(list_codes)
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM books WHERE id=? AND publication_status='published'",
            (int(book_id),),
        )
        if not await cur.fetchone():
            raise ValueError('Произведение не найдено.')
        await db.execute(
            'DELETE FROM reader_year_list_items WHERE user_id=? AND book_id=? AND list_year=?',
            (int(user_id), int(book_id), year),
        )
        for code in codes:
            await db.execute(
                """
                INSERT INTO reader_year_list_items(
                    user_id, book_id, list_year, list_code, note, created_at, updated_at
                ) VALUES(?,?,?,?,'',?,?)
                """,
                (int(user_id), int(book_id), year, code, now, now),
            )
        await db.commit()
        cur = await db.execute(
            """
            SELECT list_year, list_code, note, created_at, updated_at
            FROM reader_year_list_items
            WHERE user_id=? AND book_id=?
            ORDER BY list_year DESC, list_code
            """,
            (int(user_id), int(book_id)),
        )
        return [dict(row) for row in await cur.fetchall()]


async def get_user_year_lists(user_id: int, list_year: int | None = None) -> dict[str, Any]:
    current_year = datetime.now(timezone.utc).year
    selected_year = int(list_year or current_year)
    if selected_year < 1900 or selected_year > current_year:
        selected_year = current_year
    labels = {
        'best': 'Лучшее за год',
        'discovery': 'Открытие года',
        'emotional': 'Самое эмоциональное',
        'reread': 'Хочу перечитать',
    }
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT ryli.book_id, ryli.list_year, ryli.list_code, ryli.note,
                   ryli.created_at, ryli.updated_at, b.title, b.content_type,
                   b.cover_path, b.cover_file_id, ap.pen_name
            FROM reader_year_list_items ryli
            JOIN books b ON b.id=ryli.book_id AND b.publication_status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE ryli.user_id=? AND ryli.list_year=?
            ORDER BY ryli.list_code, ryli.updated_at DESC, ryli.book_id DESC
            """,
            (int(user_id), selected_year),
        )
        items = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            'SELECT DISTINCT list_year FROM reader_year_list_items WHERE user_id=? ORDER BY list_year DESC',
            (int(user_id),),
        )
        years = [int(row['list_year']) for row in await cur.fetchall()]
    if current_year not in years:
        years.insert(0, current_year)
    if selected_year not in years:
        years.append(selected_year)
    groups = []
    for code, label in labels.items():
        group_items = [item for item in items if str(item.get('list_code')) == code]
        groups.append({'code': code, 'label': label, 'count': len(group_items), 'items': group_items})
    return {
        'year': selected_year,
        'available_years': sorted(set(years), reverse=True),
        'groups': groups,
        'total_items': len({int(item.get('book_id') or 0) for item in items}),
        'total_marks': len(items),
        'privacy_note': 'Личные списки года видны только вам.',
    }


async def get_user_completion_calendar(user_id: int, year: int | None = None) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    selected_year = int(year or today.year)
    if selected_year < 1900 or selected_year > today.year:
        selected_year = today.year
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rbc.id AS cycle_id, rbc.book_id, rbc.cycle_number, rbc.finished_on,
                   b.title, b.content_type, b.cover_path, b.cover_file_id, ap.pen_name
            FROM reader_book_cycles rbc
            JOIN books b ON b.id=rbc.book_id AND b.publication_status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE rbc.user_id=? AND rbc.status='finished'
              AND substr(rbc.finished_on,1,4)=?
            ORDER BY rbc.finished_on, rbc.cycle_number, rbc.id
            """,
            (int(user_id), str(selected_year)),
        )
        completions = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            """
            SELECT DISTINCT CAST(substr(finished_on,1,4) AS INTEGER) AS year
            FROM reader_book_cycles
            WHERE user_id=? AND status='finished' AND finished_on IS NOT NULL
            ORDER BY year DESC
            """,
            (int(user_id),),
        )
        years = [int(row['year']) for row in await cur.fetchall() if int(row['year'] or 0) >= 1900]
    if today.year not in years:
        years.insert(0, today.year)
    if selected_year not in years:
        years.append(selected_year)
    by_date: dict[str, list[dict[str, Any]]] = {}
    for item in completions:
        key = str(item.get('finished_on') or '')[:10]
        if key:
            by_date.setdefault(key, []).append(item)
    start = datetime(selected_year, 1, 1).date()
    end = datetime(selected_year, 12, 31).date()
    days: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        key = cursor.isoformat()
        items = by_date.get(key, [])
        days.append({
            'date': key,
            'future': cursor > today,
            'count': len(items),
            'items': [{
                'cycle_id': int(item.get('cycle_id') or 0),
                'book_id': int(item.get('book_id') or 0),
                'cycle_number': int(item.get('cycle_number') or 1),
                'title': str(item.get('title') or ''),
                'author': str(item.get('pen_name') or ''),
            } for item in items],
        })
        cursor += timedelta(days=1)
    months = []
    for month in range(1, 13):
        key = f'{selected_year:04d}-{month:02d}'
        month_items = [item for item in completions if str(item.get('finished_on') or '').startswith(key)]
        months.append({
            'month': key,
            'count': len(month_items),
            'unique_books': len({int(item.get('book_id') or 0) for item in month_items}),
            'rereads': sum(1 for item in month_items if int(item.get('cycle_number') or 1) > 1),
        })
    return {
        'year': selected_year,
        'available_years': sorted(set(years), reverse=True),
        'days': days,
        'months': months,
        'total_completions': len(completions),
        'unique_books': len({int(item.get('book_id') or 0) for item in completions}),
        'rereads': sum(1 for item in completions if int(item.get('cycle_number') or 1) > 1),
        'privacy_note': 'Календарь завершений виден только владельцу профиля.',
    }


def _parse_journal_date(value: object, field_name: str) -> str | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError as exc:
        raise ValueError(f'{field_name} должна быть указана в формате ГГГГ-ММ-ДД.') from exc
    if parsed > datetime.now(timezone.utc).date():
        raise ValueError(f'{field_name} не может быть в будущем.')
    if parsed.year < 1900:
        raise ValueError(f'{field_name} указана слишком давно.')
    return parsed.isoformat()


def _journal_status(value: object) -> str:
    status = str(value or 'reading').strip().lower()
    allowed = {'planned', 'reading', 'paused', 'finished', 'dropped'}
    if status not in allowed:
        raise ValueError('Неизвестный статус записи дневника.')
    return status


async def list_user_reading_journal(user_id: int, limit: int = 250) -> list[dict[str, Any]]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rbj.user_id, rbj.book_id, rbj.status, rbj.started_on, rbj.finished_on,
                   rbj.impression, rbj.private_rating, rbj.last_activity_at,
                   rbj.created_at, rbj.updated_at,
                   b.title, b.description, b.age_limit, b.content_type,
                   b.cover_path, b.cover_file_id, ap.pen_name,
                   (SELECT COUNT(*) FROM reading_history rh WHERE rh.user_id=rbj.user_id AND rh.book_id=rbj.book_id) AS history_items,
                   (SELECT COUNT(*) FROM reader_annotations ra WHERE ra.user_id=rbj.user_id AND ra.book_id=rbj.book_id) AS annotation_items,
                   (SELECT MAX(rh.updated_at) FROM reading_history rh WHERE rh.user_id=rbj.user_id AND rh.book_id=rbj.book_id) AS history_updated_at
            FROM reader_book_journal rbj
            JOIN books b ON b.id=rbj.book_id AND b.publication_status='published'
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE rbj.user_id=?
            ORDER BY COALESCE(rbj.finished_on, substr(rbj.last_activity_at,1,10), rbj.started_on, substr(rbj.updated_at,1,10)) DESC,
                     rbj.updated_at DESC, rbj.book_id DESC
            LIMIT ?
            """,
            (int(user_id), max(1, min(1000, int(limit)))),
        )
        entries = [dict(row) for row in await cur.fetchall()]
        if not entries:
            return []
        book_ids = [int(item['book_id']) for item in entries]
        placeholders = ','.join('?' for _ in book_ids)
        cur = await db.execute(
            f"""
            SELECT id, book_id, cycle_number, status, started_on, finished_on,
                   note, created_at, updated_at
            FROM reader_book_cycles
            WHERE user_id=? AND book_id IN ({placeholders})
            ORDER BY book_id, cycle_number DESC, id DESC
            """,
            (int(user_id), *book_ids),
        )
        cycles_by_book: dict[int, list[dict[str, Any]]] = {}
        for row in await cur.fetchall():
            item = dict(row)
            cycles_by_book.setdefault(int(item['book_id']), []).append(item)
        cur = await db.execute(
            f"""
            SELECT book_id, list_year, list_code, note, created_at, updated_at
            FROM reader_year_list_items
            WHERE user_id=? AND book_id IN ({placeholders})
            ORDER BY book_id, list_year DESC, list_code
            """,
            (int(user_id), *book_ids),
        )
        lists_by_book: dict[int, list[dict[str, Any]]] = {}
        for row in await cur.fetchall():
            item = dict(row)
            lists_by_book.setdefault(int(item['book_id']), []).append(item)
    for entry in entries:
        book_id = int(entry['book_id'])
        cycles = cycles_by_book.get(book_id, [])
        entry['cycles'] = cycles
        entry['completed_cycles'] = sum(1 for item in cycles if str(item.get('status')) == 'finished')
        entry['reread_count'] = sum(1 for item in cycles if int(item.get('cycle_number') or 1) > 1 and str(item.get('status')) == 'finished')
        entry['active_cycle'] = next((item for item in cycles if str(item.get('status')) in {'reading', 'paused'}), None)
        entry['year_lists'] = lists_by_book.get(book_id, [])
    return entries


async def get_user_reading_journal_summary(user_id: int) -> dict[str, Any]:
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status='planned' THEN 1 ELSE 0 END) AS planned,
                   SUM(CASE WHEN status IN ('reading','paused') THEN 1 ELSE 0 END) AS in_progress,
                   SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END) AS finished,
                   SUM(CASE WHEN status='dropped' THEN 1 ELSE 0 END) AS dropped,
                   SUM(CASE WHEN trim(impression)!='' THEN 1 ELSE 0 END) AS with_impressions,
                   SUM(CASE WHEN private_rating>0 THEN 1 ELSE 0 END) AS rated,
                   MIN(started_on) AS first_started_on,
                   MAX(finished_on) AS last_finished_on
            FROM reader_book_journal
            WHERE user_id=?
            """,
            (int(user_id),),
        )
        row = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT substr(COALESCE(finished_on, started_on),1,4) AS year, COUNT(*) AS total
            FROM reader_book_journal
            WHERE user_id=? AND COALESCE(finished_on, started_on) IS NOT NULL
            GROUP BY year ORDER BY year DESC
            """,
            (int(user_id),),
        )
        years = [dict(item) for item in await cur.fetchall() if item['year']]
        cur = await db.execute(
            """
            SELECT COUNT(*) AS total_cycles,
                   SUM(CASE WHEN status='finished' THEN 1 ELSE 0 END) AS completed_cycles,
                   SUM(CASE WHEN cycle_number>1 THEN 1 ELSE 0 END) AS reread_cycles,
                   SUM(CASE WHEN cycle_number>1 AND status='finished' THEN 1 ELSE 0 END) AS completed_rereads,
                   SUM(CASE WHEN status IN ('reading','paused') THEN 1 ELSE 0 END) AS active_cycles,
                   COUNT(DISTINCT CASE WHEN cycle_number>1 THEN book_id END) AS books_reread
            FROM reader_book_cycles WHERE user_id=?
            """,
            (int(user_id),),
        )
        cycles = await cur.fetchone()
        cur = await db.execute(
            'SELECT COUNT(*) AS marks, COUNT(DISTINCT book_id) AS books FROM reader_year_list_items WHERE user_id=?',
            (int(user_id),),
        )
        lists = await cur.fetchone()
    return {
        'total': int(row['total'] or 0),
        'planned': int(row['planned'] or 0),
        'in_progress': int(row['in_progress'] or 0),
        'finished': int(row['finished'] or 0),
        'dropped': int(row['dropped'] or 0),
        'with_impressions': int(row['with_impressions'] or 0),
        'rated': int(row['rated'] or 0),
        'first_started_on': str(row['first_started_on'] or ''),
        'last_finished_on': str(row['last_finished_on'] or ''),
        'years': years,
        'total_cycles': int(cycles['total_cycles'] or 0),
        'completed_cycles': int(cycles['completed_cycles'] or 0),
        'reread_cycles': int(cycles['reread_cycles'] or 0),
        'completed_rereads': int(cycles['completed_rereads'] or 0),
        'active_cycles': int(cycles['active_cycles'] or 0),
        'books_reread': int(cycles['books_reread'] or 0),
        'year_list_marks': int(lists['marks'] or 0),
        'year_list_books': int(lists['books'] or 0),
        'current_year': datetime.now(timezone.utc).year,
        'privacy_note': 'Записи дневника, циклы и личные списки видны только вам.',
    }


async def update_user_reading_journal(
    user_id: int,
    book_id: int,
    *,
    status: object,
    started_on: object,
    finished_on: object,
    impression: object,
    private_rating: int,
) -> dict[str, Any]:
    clean_status = _journal_status(status)
    clean_started = _parse_journal_date(started_on, 'Дата начала')
    clean_finished = _parse_journal_date(finished_on, 'Дата завершения')
    clean_impression = str(impression or '').strip()[:6000]
    rating = max(0, min(5, int(private_rating or 0)))
    today = datetime.now(timezone.utc).date().isoformat()
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id FROM books WHERE id=? AND publication_status='published'",
            (int(book_id),),
        )
        if not await cur.fetchone():
            raise ValueError('Произведение не найдено.')
        cur = await db.execute(
            "SELECT * FROM reader_book_journal WHERE user_id=? AND book_id=?",
            (int(user_id), int(book_id)),
        )
        current = await cur.fetchone()
        if not clean_started and clean_status != 'planned':
            if current and current['started_on']:
                clean_started = str(current['started_on'])
            else:
                cur = await db.execute(
                    "SELECT substr(MIN(first_opened_at),1,10) AS started_on FROM reading_history WHERE user_id=? AND book_id=?",
                    (int(user_id), int(book_id)),
                )
                history_start = await cur.fetchone()
                clean_started = str(history_start['started_on'] or '') if history_start else ''
                clean_started = clean_started or today
        if clean_status == 'finished' and not clean_finished:
            clean_finished = str(current['finished_on'] or '') if current and current['finished_on'] else today
        if clean_started and clean_finished and clean_started > clean_finished:
            raise ValueError('Дата завершения не может быть раньше даты начала.')
        last_activity = str(current['last_activity_at'] or '') if current else ''
        created_at = str(current['created_at'] or now) if current else now
        await db.execute(
            """
            INSERT INTO reader_book_journal(
                user_id, book_id, status, started_on, finished_on, impression,
                private_rating, last_activity_at, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, book_id) DO UPDATE SET
                status=excluded.status,
                started_on=excluded.started_on,
                finished_on=excluded.finished_on,
                impression=excluded.impression,
                private_rating=excluded.private_rating,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id), int(book_id), clean_status, clean_started, clean_finished,
                clean_impression, rating, last_activity or None, created_at, now,
            ),
        )
        cur = await db.execute(
            "SELECT * FROM reader_book_cycles WHERE user_id=? AND book_id=? ORDER BY cycle_number DESC LIMIT 1",
            (int(user_id), int(book_id)),
        )
        latest_cycle = await cur.fetchone()
        cycle_status = clean_status if clean_status in {'reading','paused','finished','dropped'} else 'reading'
        if clean_status != 'planned':
            if latest_cycle:
                cycle_started = str(latest_cycle['started_on'] or '') or clean_started
                cycle_finished = clean_finished if cycle_status == 'finished' else None
                await db.execute(
                    """
                    UPDATE reader_book_cycles
                    SET status=?, started_on=?, finished_on=?, updated_at=?
                    WHERE id=?
                    """,
                    (cycle_status, cycle_started, cycle_finished, now, int(latest_cycle['id'])),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO reader_book_cycles(
                        user_id, book_id, cycle_number, status, started_on, finished_on,
                        note, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,'',?,?)
                    """,
                    (int(user_id), int(book_id), 1, cycle_status, clean_started, clean_finished if cycle_status == 'finished' else None, created_at, now),
                )
        await db.commit()
    entries = await list_user_reading_journal(user_id, limit=1000)
    return next((item for item in entries if int(item['book_id']) == int(book_id)), {})


async def delete_user_reading_journal_entry(user_id: int, book_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            'DELETE FROM reader_book_journal WHERE user_id=? AND book_id=?',
            (int(user_id), int(book_id)),
        )
        deleted = bool(cur.rowcount)
        await db.execute(
            'DELETE FROM reader_book_cycles WHERE user_id=? AND book_id=?',
            (int(user_id), int(book_id)),
        )
        await db.execute(
            'DELETE FROM reader_year_list_items WHERE user_id=? AND book_id=?',
            (int(user_id), int(book_id)),
        )
        await db.commit()
        return deleted


async def get_user_reading_export_data(user_id: int) -> dict[str, Any]:
    summary = await get_user_reading_journal_summary(user_id)
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT rbj.book_id, rbj.status, rbj.started_on, rbj.finished_on,
                   rbj.impression, rbj.private_rating, rbj.last_activity_at,
                   rbj.created_at, rbj.updated_at,
                   b.title, b.content_type, ap.pen_name
            FROM reader_book_journal rbj
            JOIN books b ON b.id=rbj.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE rbj.user_id=?
            ORDER BY COALESCE(rbj.finished_on, substr(rbj.last_activity_at,1,10), rbj.started_on, substr(rbj.updated_at,1,10)) DESC,
                     rbj.updated_at DESC, rbj.book_id DESC
            """,
            (int(user_id),),
        )
        journal_rows = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            """
            SELECT rh.book_id, rh.content_type, rh.position_value, rh.first_opened_at, rh.updated_at,
                   b.title, ap.pen_name,
                   c.title AS chapter_title, c.number AS chapter_number,
                   ac.title AS audio_title, ac.number AS audio_number,
                   gc.title AS graphic_title, gc.number AS graphic_number
            FROM reading_history rh
            JOIN books b ON b.id=rh.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            LEFT JOIN chapters c ON rh.content_type='text' AND c.id=rh.target_id
            LEFT JOIN audio_chapters ac ON rh.content_type='audio' AND ac.id=rh.target_id
            LEFT JOIN graphic_chapters gc ON rh.content_type='graphic' AND gc.id=rh.target_id
            WHERE rh.user_id=?
            ORDER BY rh.updated_at ASC, rh.id ASC
            """,
            (int(user_id),),
        )
        history_rows = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            """
            SELECT ra.book_id, ra.annotation_type, ra.selected_text, ra.note_text,
                   ra.position_percent, ra.created_at, ra.updated_at,
                   b.title, ap.pen_name, c.title AS chapter_title, c.number AS chapter_number
            FROM reader_annotations ra
            JOIN books b ON b.id=ra.book_id
            JOIN chapters c ON c.id=ra.chapter_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE ra.user_id=?
            ORDER BY ra.created_at ASC, ra.id ASC
            """,
            (int(user_id),),
        )
        annotation_rows = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            """
            SELECT activity_date, text_chapters, audio_seconds, graphic_pages, sessions
            FROM reader_activity_daily
            WHERE user_id=? ORDER BY activity_date ASC
            """,
            (int(user_id),),
        )
        activity_rows = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            """
            SELECT rbc.book_id, rbc.cycle_number, rbc.status, rbc.started_on,
                   rbc.finished_on, rbc.note, rbc.created_at, rbc.updated_at,
                   b.title, ap.pen_name
            FROM reader_book_cycles rbc
            JOIN books b ON b.id=rbc.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE rbc.user_id=?
            ORDER BY rbc.book_id, rbc.cycle_number
            """,
            (int(user_id),),
        )
        cycle_rows = [dict(row) for row in await cur.fetchall()]
        cur = await db.execute(
            """
            SELECT ryli.book_id, ryli.list_year, ryli.list_code, ryli.note,
                   ryli.created_at, ryli.updated_at, b.title, ap.pen_name
            FROM reader_year_list_items ryli
            JOIN books b ON b.id=ryli.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE ryli.user_id=?
            ORDER BY ryli.list_year, ryli.list_code, b.title
            """,
            (int(user_id),),
        )
        year_list_rows = [dict(row) for row in await cur.fetchall()]
    safe_journal = [{
        'book_id': int(item.get('book_id') or 0),
        'title': str(item.get('title') or ''),
        'author': str(item.get('pen_name') or ''),
        'content_type': str(item.get('content_type') or 'book'),
        'status': str(item.get('status') or 'reading'),
        'started_on': str(item.get('started_on') or ''),
        'finished_on': str(item.get('finished_on') or ''),
        'private_rating': int(item.get('private_rating') or 0),
        'impression': str(item.get('impression') or ''),
        'last_activity_at': str(item.get('last_activity_at') or ''),
        'created_at': str(item.get('created_at') or ''),
        'updated_at': str(item.get('updated_at') or ''),
    } for item in journal_rows]
    safe_history = [{
        'book_id': int(item.get('book_id') or 0),
        'title': str(item.get('title') or ''),
        'author': str(item.get('pen_name') or ''),
        'content_type': str(item.get('content_type') or ''),
        'chapter_number': item.get('chapter_number') or item.get('audio_number') or item.get('graphic_number'),
        'chapter_title': str(item.get('chapter_title') or item.get('audio_title') or item.get('graphic_title') or ''),
        'position': int(item.get('position_value') or 0),
        'first_opened_at': str(item.get('first_opened_at') or ''),
        'updated_at': str(item.get('updated_at') or ''),
    } for item in history_rows]
    safe_annotations = [{
        'book_id': int(item.get('book_id') or 0),
        'title': str(item.get('title') or ''),
        'author': str(item.get('pen_name') or ''),
        'chapter_number': item.get('chapter_number'),
        'chapter_title': str(item.get('chapter_title') or ''),
        'type': str(item.get('annotation_type') or 'note'),
        'selected_text': str(item.get('selected_text') or ''),
        'note_text': str(item.get('note_text') or ''),
        'position_percent': int(item.get('position_percent') or 0),
        'created_at': str(item.get('created_at') or ''),
        'updated_at': str(item.get('updated_at') or ''),
    } for item in annotation_rows]
    safe_activity = [{
        'date': str(item.get('activity_date') or ''),
        'text_chapters': int(item.get('text_chapters') or 0),
        'audio_minutes': int(item.get('audio_seconds') or 0) // 60,
        'graphic_pages': int(item.get('graphic_pages') or 0),
        'sessions': int(item.get('sessions') or 0),
    } for item in activity_rows]
    safe_cycles = [{
        'book_id': int(item.get('book_id') or 0),
        'title': str(item.get('title') or ''),
        'author': str(item.get('pen_name') or ''),
        'cycle_number': int(item.get('cycle_number') or 1),
        'status': str(item.get('status') or 'reading'),
        'started_on': str(item.get('started_on') or ''),
        'finished_on': str(item.get('finished_on') or ''),
        'note': str(item.get('note') or ''),
        'created_at': str(item.get('created_at') or ''),
        'updated_at': str(item.get('updated_at') or ''),
    } for item in cycle_rows]
    safe_year_lists = [{
        'book_id': int(item.get('book_id') or 0),
        'title': str(item.get('title') or ''),
        'author': str(item.get('pen_name') or ''),
        'year': int(item.get('list_year') or 0),
        'list_code': str(item.get('list_code') or ''),
        'note': str(item.get('note') or ''),
        'created_at': str(item.get('created_at') or ''),
        'updated_at': str(item.get('updated_at') or ''),
    } for item in year_list_rows]
    return {
        'export_version': '1.2',
        'generated_at': utc_now(),
        'privacy': 'Экспорт содержит только личную читательскую историю владельца профиля.',
        'summary': summary,
        'journal': safe_journal,
        'reading_cycles': safe_cycles,
        'year_lists': safe_year_lists,
        'history': safe_history,
        'annotations': safe_annotations,
        'daily_activity': safe_activity,
    }




def _import_timestamp(value: object) -> datetime | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _import_timestamp_text(value: object) -> str:
    parsed = _import_timestamp(value)
    return parsed.isoformat() if parsed else ''


def _import_is_newer(source_value: object, current_value: object) -> bool:
    source = _import_timestamp(source_value)
    current = _import_timestamp(current_value)
    return bool(source and (not current or source > current))


def _import_book_key(title: object, author: object) -> tuple[str, str]:
    return (str(title or '').strip().casefold(), str(author or '').strip().casefold())


def _import_operation_selection_id(operation: dict[str, Any]) -> str:
    source = dict(operation.get('source') or {})
    kind = str(operation.get('kind') or '')
    book_id = int(source.get('book_id') or 0)
    if kind == 'journal':
        return f'journal:{book_id}'
    if kind == 'cycle':
        return f"cycle:{book_id}:{int(source.get('cycle_number') or 1)}"
    if kind == 'year_list':
        return f"year_list:{book_id}:{int(source.get('year') or 0)}:{str(source.get('list_code') or '')}"
    raise ValueError('Неизвестный раздел импорта дневника.')


def _import_selectable_item(operation: dict[str, Any]) -> dict[str, Any]:
    source = dict(operation.get('source') or {})
    kind = str(operation.get('kind') or '')
    item = {
        'selection_id': _import_operation_selection_id(operation),
        'kind': kind,
        'action': str(operation.get('action') or ''),
        'book_id': int(source.get('book_id') or 0),
        'title': str(source.get('title') or ''),
        'author': str(source.get('author') or ''),
    }
    if kind == 'journal':
        item.update({
            'status': str(source.get('status') or ''),
            'started_on': str(source.get('started_on') or ''),
            'finished_on': str(source.get('finished_on') or ''),
            'private_rating': int(source.get('private_rating') or 0),
            'has_impression': bool(str(source.get('impression') or '').strip()),
        })
    elif kind == 'cycle':
        item.update({
            'cycle_number': int(source.get('cycle_number') or 1),
            'status': str(source.get('status') or ''),
            'started_on': str(source.get('started_on') or ''),
            'finished_on': str(source.get('finished_on') or ''),
            'has_note': bool(str(source.get('note') or '').strip()),
            'requires_journal': f"journal:{int(source.get('book_id') or 0)}",
        })
    elif kind == 'year_list':
        item.update({
            'year': int(source.get('year') or 0),
            'list_code': str(source.get('list_code') or ''),
            'has_note': bool(str(source.get('note') or '').strip()),
        })
    return item


def _normalize_import_selection(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError('Выбор записей импорта должен быть списком.')
    if len(value) > 18000:
        raise ValueError('Выбрано слишком много записей импорта.')
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = str(raw or '').strip()
        if not item or len(item) > 120 or item in seen:
            continue
        if not item.startswith(('journal:', 'cycle:', 'year_list:')):
            raise ValueError('В выборе импорта обнаружена неизвестная запись.')
        seen.add(item)
        result.append(item)
    return result


def _public_import_preview(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != '_operations'}


async def _normalize_user_reading_import(
    db: aiosqlite.Connection,
    raw_data: object,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw_data, dict):
        raise ValueError('Файл должен содержать JSON-объект экспорта VoxLyra.')
    source_version = str(raw_data.get('export_version') or '').strip()
    if source_version not in {'1.1', '1.2'}:
        raise ValueError('Поддерживаются экспорты VoxLyra версий 1.1 и 1.2.')
    journal_raw = raw_data.get('journal') or []
    cycles_raw = raw_data.get('reading_cycles') or []
    lists_raw = raw_data.get('year_lists') or []
    if not isinstance(journal_raw, list) or not isinstance(cycles_raw, list) or not isinstance(lists_raw, list):
        raise ValueError('Разделы journal, reading_cycles и year_lists должны быть списками.')
    if len(journal_raw) > 2000 or len(cycles_raw) > 6000 or len(lists_raw) > 10000:
        raise ValueError('Экспорт слишком большой для безопасного восстановления.')

    cur = await db.execute(
        """
        SELECT b.id, b.title, b.content_type, ap.pen_name
        FROM books b
        LEFT JOIN author_profiles ap ON ap.id=b.author_id
        WHERE b.publication_status='published'
        """
    )
    books = [dict(row) for row in await cur.fetchall()]
    by_id = {int(item['id']): item for item in books}
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_title: dict[str, list[dict[str, Any]]] = {}
    for item in books:
        key = _import_book_key(item.get('title'), item.get('pen_name'))
        by_key.setdefault(key, []).append(item)
        by_title.setdefault(key[0], []).append(item)

    stats: dict[str, Any] = {
        'invalid_records': 0,
        'duplicate_records': 0,
        'missing_books': [],
        'missing_book_count': 0,
        'ignored_history': len(raw_data.get('history') or []) if isinstance(raw_data.get('history') or [], list) else 0,
        'ignored_annotations': len(raw_data.get('annotations') or []) if isinstance(raw_data.get('annotations') or [], list) else 0,
        'ignored_daily_activity': len(raw_data.get('daily_activity') or []) if isinstance(raw_data.get('daily_activity') or [], list) else 0,
    }

    def resolve_book(item: dict[str, Any]) -> dict[str, Any] | None:
        source_id = int(item.get('book_id') or 0)
        title = str(item.get('title') or '').strip()
        author = str(item.get('author') or '').strip()
        candidate = by_id.get(source_id)
        if candidate:
            source_key = _import_book_key(title, author)
            candidate_key = _import_book_key(candidate.get('title'), candidate.get('pen_name'))
            # Старые экспорты могли не содержать автора. ID принимается, если
            # название совпадает или название отсутствует.
            if not title or source_key[0] == candidate_key[0]:
                return candidate
        key = _import_book_key(title, author)
        matches = by_key.get(key, []) if title and author else []
        if len(matches) == 1:
            return matches[0]
        title_matches = by_title.get(key[0], []) if title else []
        if len(title_matches) == 1:
            return title_matches[0]
        stats['missing_book_count'] = int(stats.get('missing_book_count') or 0) + 1
        if len(stats['missing_books']) < 30:
            stats['missing_books'].append({'book_id': source_id, 'title': title or 'Без названия', 'author': author})
        return None

    normalized_journal: dict[int, dict[str, Any]] = {}
    for raw in journal_raw:
        if not isinstance(raw, dict):
            stats['invalid_records'] += 1
            continue
        book = resolve_book(raw)
        if not book:
            continue
        try:
            status = _journal_status(raw.get('status') or 'reading')
            started_on = _parse_journal_date(raw.get('started_on'), 'Дата начала')
            finished_on = _parse_journal_date(raw.get('finished_on'), 'Дата завершения')
            if status == 'finished' and not finished_on:
                status = 'reading'
            if started_on and finished_on and finished_on < started_on:
                raise ValueError('Дата завершения раньше даты начала.')
            rating = max(0, min(5, int(raw.get('private_rating') or 0)))
        except (TypeError, ValueError):
            stats['invalid_records'] += 1
            continue
        book_id = int(book['id'])
        item = {
            'book_id': book_id,
            'title': str(book.get('title') or ''),
            'author': str(book.get('pen_name') or ''),
            'content_type': str(book.get('content_type') or 'book'),
            'status': status,
            'started_on': started_on or '',
            'finished_on': finished_on or '',
            'private_rating': rating,
            'impression': str(raw.get('impression') or '').strip()[:6000],
            'last_activity_at': _import_timestamp_text(raw.get('last_activity_at')),
            'created_at': _import_timestamp_text(raw.get('created_at')),
            'updated_at': _import_timestamp_text(raw.get('updated_at')),
        }
        existing = normalized_journal.get(book_id)
        if existing:
            stats['duplicate_records'] += 1
            if _import_is_newer(item.get('updated_at'), existing.get('updated_at')):
                normalized_journal[book_id] = item
        else:
            normalized_journal[book_id] = item

    normalized_cycles: dict[tuple[int, int], dict[str, Any]] = {}
    for raw in cycles_raw:
        if not isinstance(raw, dict):
            stats['invalid_records'] += 1
            continue
        book = resolve_book(raw)
        if not book:
            continue
        try:
            cycle_number = int(raw.get('cycle_number') or 1)
            if cycle_number < 1 or cycle_number > 10000:
                raise ValueError
            status = _reading_cycle_status(raw.get('status') or 'reading')
            started_on = _parse_journal_date(raw.get('started_on'), 'Дата начала цикла')
            finished_on = _parse_journal_date(raw.get('finished_on'), 'Дата завершения цикла')
            if status == 'finished' and not finished_on:
                raise ValueError
            if status != 'finished':
                finished_on = None
            if started_on and finished_on and finished_on < started_on:
                raise ValueError
        except (TypeError, ValueError):
            stats['invalid_records'] += 1
            continue
        key = (int(book['id']), cycle_number)
        item = {
            'book_id': key[0],
            'title': str(book.get('title') or ''),
            'author': str(book.get('pen_name') or ''),
            'cycle_number': cycle_number,
            'status': status,
            'started_on': started_on or '',
            'finished_on': finished_on or '',
            'note': str(raw.get('note') or '').strip()[:2000],
            'created_at': _import_timestamp_text(raw.get('created_at')),
            'updated_at': _import_timestamp_text(raw.get('updated_at')),
        }
        existing = normalized_cycles.get(key)
        if existing:
            stats['duplicate_records'] += 1
            if _import_is_newer(item.get('updated_at'), existing.get('updated_at')):
                normalized_cycles[key] = item
        else:
            normalized_cycles[key] = item

    normalized_lists: dict[tuple[int, int, str], dict[str, Any]] = {}
    current_year = datetime.now(timezone.utc).year
    for raw in lists_raw:
        if not isinstance(raw, dict):
            stats['invalid_records'] += 1
            continue
        book = resolve_book(raw)
        if not book:
            continue
        try:
            year = int(raw.get('year') or raw.get('list_year') or 0)
            if year < 1900 or year > current_year:
                raise ValueError
            code = _year_list_codes([raw.get('list_code')])[0]
        except (TypeError, ValueError, IndexError):
            stats['invalid_records'] += 1
            continue
        key = (int(book['id']), year, code)
        item = {
            'book_id': key[0],
            'title': str(book.get('title') or ''),
            'author': str(book.get('pen_name') or ''),
            'year': year,
            'list_code': code,
            'note': str(raw.get('note') or '').strip()[:1000],
            'created_at': _import_timestamp_text(raw.get('created_at')),
            'updated_at': _import_timestamp_text(raw.get('updated_at')),
        }
        existing = normalized_lists.get(key)
        if existing:
            stats['duplicate_records'] += 1
            if _import_is_newer(item.get('updated_at'), existing.get('updated_at')):
                normalized_lists[key] = item
        else:
            normalized_lists[key] = item

    # Повреждённый или сокращённый экспорт может содержать циклы без общей
    # записи дневника. Создаём безопасную сводную запись, чтобы циклы не стали
    # невидимыми после восстановления.
    cycles_by_book: dict[int, list[dict[str, Any]]] = {}
    for item in normalized_cycles.values():
        cycles_by_book.setdefault(int(item['book_id']), []).append(item)
    for book_id, book_cycles in cycles_by_book.items():
        if book_id in normalized_journal:
            continue
        ordered = sorted(book_cycles, key=lambda item: int(item.get('cycle_number') or 1))
        latest = ordered[-1]
        first_started = min((str(item.get('started_on') or '') for item in ordered if item.get('started_on')), default='')
        last_finished = max((str(item.get('finished_on') or '') for item in ordered if item.get('finished_on')), default='')
        normalized_journal[book_id] = {
            'book_id': book_id,
            'title': str(latest.get('title') or ''),
            'author': str(latest.get('author') or ''),
            'content_type': 'book',
            'status': str(latest.get('status') or 'reading'),
            'started_on': first_started,
            'finished_on': last_finished if str(latest.get('status')) == 'finished' else '',
            'private_rating': 0,
            'impression': '',
            'last_activity_at': '',
            'created_at': str(ordered[0].get('created_at') or ''),
            'updated_at': str(latest.get('updated_at') or ''),
        }

    normalized = {
        'source_version': source_version,
        'source_generated_at': _import_timestamp_text(raw_data.get('generated_at')),
        'journal': list(normalized_journal.values()),
        'reading_cycles': list(normalized_cycles.values()),
        'year_lists': list(normalized_lists.values()),
    }
    return normalized, stats


async def _build_user_reading_import_plan(
    db: aiosqlite.Connection,
    user_id: int,
    normalized: dict[str, Any],
    normalization_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cur = await db.execute('SELECT * FROM reader_book_journal WHERE user_id=?', (int(user_id),))
    current_journal = {int(row['book_id']): dict(row) for row in await cur.fetchall()}
    cur = await db.execute('SELECT * FROM reader_book_cycles WHERE user_id=?', (int(user_id),))
    current_cycles = {(int(row['book_id']), int(row['cycle_number'])): dict(row) for row in await cur.fetchall()}
    active_by_book: dict[int, list[dict[str, Any]]] = {}
    for row in current_cycles.values():
        if str(row.get('status')) in {'reading', 'paused'}:
            active_by_book.setdefault(int(row['book_id']), []).append(row)
    cur = await db.execute('SELECT * FROM reader_year_list_items WHERE user_id=?', (int(user_id),))
    current_lists = {
        (int(row['book_id']), int(row['list_year']), str(row['list_code'])): dict(row)
        for row in await cur.fetchall()
    }

    operations: list[dict[str, Any]] = []
    counts = {
        'journal_add': 0, 'journal_update': 0, 'journal_fill': 0, 'journal_protected': 0, 'journal_unchanged': 0,
        'cycles_add': 0, 'cycles_update': 0, 'cycles_fill': 0, 'cycles_protected': 0, 'cycles_unchanged': 0,
        'year_lists_add': 0, 'year_lists_update': 0, 'year_lists_fill': 0, 'year_lists_protected': 0, 'year_lists_unchanged': 0,
    }
    protected: list[dict[str, Any]] = []

    for source in normalized.get('journal') or []:
        book_id = int(source['book_id'])
        current = current_journal.get(book_id)
        if not current:
            operations.append({'kind': 'journal', 'action': 'add', 'source': source})
            counts['journal_add'] += 1
            continue
        if _import_is_newer(source.get('updated_at'), current.get('updated_at')):
            operations.append({'kind': 'journal', 'action': 'update', 'source': source})
            counts['journal_update'] += 1
            continue
        fill: dict[str, Any] = {}
        # У старого экспорта нет точной даты изменения каждой записи. Поэтому
        # он может заполнить только явно пустое личное впечатление и оценку, но
        # никогда не меняет статусные даты и прогресс более новой записи.
        if not str(current.get('impression') or '').strip() and str(source.get('impression') or '').strip():
            fill['impression'] = source.get('impression')
        if int(current.get('private_rating') or 0) <= 0 and int(source.get('private_rating') or 0) > 0:
            fill['private_rating'] = int(source.get('private_rating') or 0)
        if fill:
            operations.append({'kind': 'journal', 'action': 'fill', 'source': source, 'fields': fill})
            counts['journal_fill'] += 1
        else:
            comparable = ('status', 'started_on', 'finished_on', 'impression', 'private_rating')
            differs = any(str(current.get(field) or '') != str(source.get(field) or '') for field in comparable)
            if differs:
                counts['journal_protected'] += 1
                if len(protected) < 30:
                    protected.append({'title': source.get('title') or '', 'section': 'Дневник', 'reason': 'Текущая запись новее или не имеет точной даты изменения.'})
            else:
                counts['journal_unchanged'] += 1

    planned_active: dict[int, int] = {}
    for source in sorted(normalized.get('reading_cycles') or [], key=lambda item: (int(item['book_id']), int(item['cycle_number']))):
        key = (int(source['book_id']), int(source['cycle_number']))
        current = current_cycles.get(key)
        source_active = str(source.get('status')) in {'reading', 'paused'}
        conflicting_active = [row for row in active_by_book.get(key[0], []) if int(row.get('cycle_number') or 0) != key[1]]
        if source_active and (conflicting_active or (key[0] in planned_active and planned_active[key[0]] != key[1])):
            counts['cycles_protected'] += 1
            if len(protected) < 30:
                protected.append({'title': source.get('title') or '', 'section': 'Циклы', 'reason': 'Не создан второй одновременный активный цикл.'})
            continue
        if not current:
            operations.append({'kind': 'cycle', 'action': 'add', 'source': source})
            counts['cycles_add'] += 1
            if source_active:
                planned_active[key[0]] = key[1]
            continue
        if _import_is_newer(source.get('updated_at'), current.get('updated_at')):
            operations.append({'kind': 'cycle', 'action': 'update', 'source': source})
            counts['cycles_update'] += 1
            if source_active:
                planned_active[key[0]] = key[1]
            continue
        fill: dict[str, Any] = {}
        # Без доказательства, что импортированный цикл новее, не меняем его
        # даты или статус. Разрешено только вернуть отсутствующую личную заметку.
        if not str(current.get('note') or '').strip() and str(source.get('note') or '').strip():
            fill['note'] = source.get('note')
        if fill:
            operations.append({'kind': 'cycle', 'action': 'fill', 'source': source, 'fields': fill})
            counts['cycles_fill'] += 1
        else:
            comparable = ('status', 'started_on', 'finished_on', 'note')
            differs = any(str(current.get(field) or '') != str(source.get(field) or '') for field in comparable)
            if differs:
                counts['cycles_protected'] += 1
                if len(protected) < 30:
                    protected.append({'title': source.get('title') or '', 'section': f"Цикл №{int(source.get('cycle_number') or 1)}", 'reason': 'Существующий цикл сохранён без перезаписи.'})
            else:
                counts['cycles_unchanged'] += 1

    for source in normalized.get('year_lists') or []:
        key = (int(source['book_id']), int(source['year']), str(source['list_code']))
        current = current_lists.get(key)
        if not current:
            operations.append({'kind': 'year_list', 'action': 'add', 'source': source})
            counts['year_lists_add'] += 1
            continue
        if _import_is_newer(source.get('updated_at'), current.get('updated_at')):
            operations.append({'kind': 'year_list', 'action': 'update', 'source': source})
            counts['year_lists_update'] += 1
            continue
        if not str(current.get('note') or '').strip() and str(source.get('note') or '').strip():
            operations.append({'kind': 'year_list', 'action': 'fill', 'source': source, 'fields': {'note': source.get('note')}})
            counts['year_lists_fill'] += 1
        elif str(current.get('note') or '') != str(source.get('note') or ''):
            counts['year_lists_protected'] += 1
        else:
            counts['year_lists_unchanged'] += 1

    for operation in operations:
        operation['selection_id'] = _import_operation_selection_id(operation)
    selectable_items = {'journal': [], 'cycles': [], 'year_lists': []}
    for operation in operations:
        public_item = _import_selectable_item(operation)
        if operation.get('kind') == 'journal':
            selectable_items['journal'].append(public_item)
        elif operation.get('kind') == 'cycle':
            selectable_items['cycles'].append(public_item)
        elif operation.get('kind') == 'year_list':
            selectable_items['year_lists'].append(public_item)

    stats = normalization_stats or {}
    total_changes = sum(value for key, value in counts.items() if key.endswith(('_add', '_update', '_fill')))
    total_protected = sum(value for key, value in counts.items() if key.endswith('_protected'))
    preview = {
        'source_version': str(normalized.get('source_version') or ''),
        'source_generated_at': str(normalized.get('source_generated_at') or ''),
        'source_counts': {
            'journal': len(normalized.get('journal') or []),
            'cycles': len(normalized.get('reading_cycles') or []),
            'year_lists': len(normalized.get('year_lists') or []),
        },
        'changes': counts,
        'total_changes': total_changes,
        'total_protected': total_protected,
        'invalid_records': int(stats.get('invalid_records') or 0),
        'duplicate_records': int(stats.get('duplicate_records') or 0),
        'missing_book_count': int(stats.get('missing_book_count') or 0),
        'missing_books': list(stats.get('missing_books') or [])[:30],
        'ignored_sections': {
            'history': int(stats.get('ignored_history') or 0),
            'annotations': int(stats.get('ignored_annotations') or 0),
            'daily_activity': int(stats.get('ignored_daily_activity') or 0),
        },
        'protected_examples': protected,
        'selectable_items': selectable_items,
        'default_selected': [str(operation.get('selection_id') or '') for operation in operations],
        'selection_note': 'Можно восстановить только выбранные произведения, циклы и отметки списков года.',
        'safety_note': 'Новые записи добавятся. Более новые или не датированные текущие данные не будут перезаписаны.',
        '_operations': operations,
    }
    return preview


async def prepare_user_reading_import(user_id: int, raw_data: object) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=30)
    token = uuid.uuid4().hex
    async with connect() as db:
        normalized, stats = await _normalize_user_reading_import(db, raw_data)
        plan = await _build_user_reading_import_plan(db, user_id, normalized, stats)
        public_preview = _public_import_preview(plan)
        public_preview['preview_token'] = token
        public_preview['expires_at'] = expires.isoformat()
        await db.execute(
            """
            INSERT INTO reader_journal_import_previews(
                token, user_id, source_version, source_generated_at, normalized_json,
                preview_json, created_at, expires_at, applied_at, result_json
            ) VALUES(?,?,?,?,?,?,?,?,NULL,NULL)
            """,
            (
                token, int(user_id), str(normalized.get('source_version') or ''),
                str(normalized.get('source_generated_at') or '') or None,
                json.dumps(normalized, ensure_ascii=False, separators=(',', ':')),
                json.dumps(public_preview, ensure_ascii=False, separators=(',', ':')),
                now.isoformat(), expires.isoformat(),
            ),
        )
        await db.commit()
    return public_preview


async def apply_user_reading_import(
    user_id: int,
    preview_token: object,
    selected_items: object = None,
) -> dict[str, Any]:
    token = str(preview_token or '').strip()
    if len(token) != 32 or any(char not in '0123456789abcdef' for char in token.lower()):
        raise ValueError('Предпросмотр импорта недействителен.')
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    async with connect() as db:
        cur = await db.execute(
            'SELECT * FROM reader_journal_import_previews WHERE token=? AND user_id=?',
            (token, int(user_id)),
        )
        preview_row = await cur.fetchone()
        if not preview_row:
            raise ValueError('Предпросмотр импорта не найден.')
        if preview_row['applied_at']:
            raise ValueError('Этот импорт уже был применён.')
        expires = _import_timestamp(preview_row['expires_at'])
        if not expires or expires < now_dt:
            raise ValueError('Предпросмотр устарел. Выберите файл повторно.')
        try:
            normalized = json.loads(str(preview_row['normalized_json'] or '{}'))
            stored_preview = json.loads(str(preview_row['preview_json'] or '{}'))
        except json.JSONDecodeError as exc:
            raise ValueError('Сохранённый предпросмотр повреждён.') from exc
        requested_selection = _normalize_import_selection(selected_items)
        if requested_selection is not None:
            stored_ids = {
                str(item.get('selection_id') or '')
                for section in (stored_preview.get('selectable_items') or {}).values()
                if isinstance(section, list)
                for item in section if isinstance(item, dict)
            }
            if not stored_ids:
                raise ValueError('Этот предпросмотр создан старой версией. Выберите файл повторно.')
            unknown = [item for item in requested_selection if item not in stored_ids]
            if unknown:
                raise ValueError('Выбор импорта не соответствует проверенному файлу.')
            if not requested_selection:
                raise ValueError('Выберите хотя бы одну запись для восстановления.')
        # План пересчитывается непосредственно перед записью. Это защищает
        # изменения, сделанные пользователем после открытия предпросмотра.
        await db.execute('BEGIN IMMEDIATE')
        # После получения блокировки перечитываем одноразовый токен. Два
        # одновременных нажатия не смогут применить один импорт дважды.
        cur = await db.execute(
            'SELECT applied_at, expires_at FROM reader_journal_import_previews WHERE token=? AND user_id=?',
            (token, int(user_id)),
        )
        locked_preview = await cur.fetchone()
        if not locked_preview or locked_preview['applied_at']:
            raise ValueError('Этот импорт уже был применён.')
        locked_expires = _import_timestamp(locked_preview['expires_at'])
        if not locked_expires or locked_expires < datetime.now(timezone.utc):
            raise ValueError('Предпросмотр устарел. Выберите файл повторно.')
        plan = await _build_user_reading_import_plan(db, user_id, normalized, {})
        all_operations = list(plan.get('_operations') or [])
        requested_set = set(requested_selection or [])
        operations = all_operations if requested_selection is None else [
            operation for operation in all_operations
            if str(operation.get('selection_id') or '') in requested_set
        ]
        auto_included: list[str] = []
        if requested_selection is not None:
            cycle_book_ids = {
                int(operation['source']['book_id'])
                for operation in operations if operation.get('kind') == 'cycle'
            }
            if cycle_book_ids:
                placeholders = ','.join('?' for _ in cycle_book_ids)
                cur = await db.execute(
                    f'SELECT book_id FROM reader_book_journal WHERE user_id=? AND book_id IN ({placeholders})',
                    (int(user_id), *sorted(cycle_book_ids)),
                )
                existing_journal_ids = {int(row['book_id']) for row in await cur.fetchall()}
                selected_ids = {str(operation.get('selection_id') or '') for operation in operations}
                for book_id in sorted(cycle_book_ids - existing_journal_ids):
                    dependency = next((
                        operation for operation in all_operations
                        if operation.get('kind') == 'journal' and int(operation['source']['book_id']) == book_id
                    ), None)
                    if dependency and str(dependency.get('selection_id') or '') not in selected_ids:
                        operations.append(dependency)
                        dependency_id = str(dependency.get('selection_id') or '')
                        selected_ids.add(dependency_id)
                        auto_included.append(dependency_id)
        if not operations:
            raise ValueError('Выбранные записи больше не требуют восстановления. Проверьте файл повторно.')
        backup = await _build_user_journal_backup(db, user_id, now)
        before_snapshot = await _capture_user_import_snapshot(db, user_id, operations)
        applied = {'journal': 0, 'cycles': 0, 'year_lists': 0}
        for operation in operations:
            source = operation['source']
            action = operation['action']
            kind = operation['kind']
            created_at = str(source.get('created_at') or '') or now
            updated_at = str(source.get('updated_at') or '') or now
            if kind == 'journal':
                if action == 'add':
                    await db.execute(
                        """
                        INSERT INTO reader_book_journal(
                            user_id, book_id, status, started_on, finished_on, impression,
                            private_rating, last_activity_at, created_at, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            int(user_id), int(source['book_id']), source['status'],
                            source.get('started_on') or None, source.get('finished_on') or None,
                            source.get('impression') or '', int(source.get('private_rating') or 0),
                            source.get('last_activity_at') or None, created_at, updated_at,
                        ),
                    )
                elif action == 'update':
                    await db.execute(
                        """
                        UPDATE reader_book_journal
                        SET status=?, started_on=?, finished_on=?, impression=?, private_rating=?,
                            last_activity_at=?, updated_at=?
                        WHERE user_id=? AND book_id=?
                        """,
                        (
                            source['status'], source.get('started_on') or None, source.get('finished_on') or None,
                            source.get('impression') or '', int(source.get('private_rating') or 0),
                            source.get('last_activity_at') or None, updated_at,
                            int(user_id), int(source['book_id']),
                        ),
                    )
                else:
                    fields = dict(operation.get('fields') or {})
                    assignments = ', '.join(f'{field}=?' for field in fields)
                    if assignments:
                        await db.execute(
                            f'UPDATE reader_book_journal SET {assignments}, updated_at=? WHERE user_id=? AND book_id=?',
                            (*fields.values(), now, int(user_id), int(source['book_id'])),
                        )
                applied['journal'] += 1
            elif kind == 'cycle':
                if action == 'add':
                    await db.execute(
                        """
                        INSERT INTO reader_book_cycles(
                            user_id, book_id, cycle_number, status, started_on, finished_on,
                            note, created_at, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            int(user_id), int(source['book_id']), int(source['cycle_number']), source['status'],
                            source.get('started_on') or None, source.get('finished_on') or None,
                            source.get('note') or '', created_at, updated_at,
                        ),
                    )
                elif action == 'update':
                    await db.execute(
                        """
                        UPDATE reader_book_cycles
                        SET status=?, started_on=?, finished_on=?, note=?, updated_at=?
                        WHERE user_id=? AND book_id=? AND cycle_number=?
                        """,
                        (
                            source['status'], source.get('started_on') or None, source.get('finished_on') or None,
                            source.get('note') or '', updated_at, int(user_id), int(source['book_id']),
                            int(source['cycle_number']),
                        ),
                    )
                else:
                    fields = dict(operation.get('fields') or {})
                    assignments = ', '.join(f'{field}=?' for field in fields)
                    if assignments:
                        await db.execute(
                            f'UPDATE reader_book_cycles SET {assignments}, updated_at=? WHERE user_id=? AND book_id=? AND cycle_number=?',
                            (*fields.values(), now, int(user_id), int(source['book_id']), int(source['cycle_number'])),
                        )
                applied['cycles'] += 1
            elif kind == 'year_list':
                if action == 'add':
                    await db.execute(
                        """
                        INSERT INTO reader_year_list_items(
                            user_id, book_id, list_year, list_code, note, created_at, updated_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            int(user_id), int(source['book_id']), int(source['year']), source['list_code'],
                            source.get('note') or '', created_at, updated_at,
                        ),
                    )
                elif action == 'update':
                    await db.execute(
                        """
                        UPDATE reader_year_list_items SET note=?, updated_at=?
                        WHERE user_id=? AND book_id=? AND list_year=? AND list_code=?
                        """,
                        (
                            source.get('note') or '', updated_at, int(user_id), int(source['book_id']),
                            int(source['year']), source['list_code'],
                        ),
                    )
                else:
                    await db.execute(
                        """
                        UPDATE reader_year_list_items SET note=?, updated_at=?
                        WHERE user_id=? AND book_id=? AND list_year=? AND list_code=?
                        """,
                        (
                            source.get('note') or '', now, int(user_id), int(source['book_id']),
                            int(source['year']), source['list_code'],
                        ),
                    )
                applied['year_lists'] += 1

        # Если импорт добавил циклы, но дневник уже существовал, приводим общие
        # даты к безопасной сводке, не стирая впечатления и личную оценку.
        touched_books = sorted({int(op['source']['book_id']) for op in operations if op['kind'] in {'journal', 'cycle'}})
        for book_id in touched_books:
            cur = await db.execute(
                """
                SELECT MIN(started_on) AS first_started,
                       MAX(CASE WHEN status='finished' THEN finished_on END) AS last_finished,
                       SUM(CASE WHEN status IN ('reading','paused') THEN 1 ELSE 0 END) AS active
                FROM reader_book_cycles WHERE user_id=? AND book_id=?
                """,
                (int(user_id), book_id),
            )
            aggregate = await cur.fetchone()
            if aggregate and (aggregate['first_started'] or aggregate['last_finished']):
                await db.execute(
                    """
                    UPDATE reader_book_journal
                    SET started_on=COALESCE(started_on, ?),
                        finished_on=COALESCE(finished_on, ?),
                        updated_at=CASE WHEN updated_at>? THEN updated_at ELSE ? END
                    WHERE user_id=? AND book_id=?
                    """,
                    (
                        aggregate['first_started'], aggregate['last_finished'], now, now,
                        int(user_id), book_id,
                    ),
                )
        after_snapshot = await _capture_user_import_snapshot(db, user_id, operations)
        applied_ids = [str(operation.get('selection_id') or '') for operation in operations]
        result = {
            'applied': applied,
            'total_applied': sum(applied.values()),
            'requested_selection_count': len(requested_selection or []) if requested_selection is not None else len(all_operations),
            'applied_selection_ids': applied_ids,
            'auto_included_selection_ids': auto_included,
            'skipped_after_recheck': max(0, (len(requested_selection or []) if requested_selection is not None else len(all_operations)) - len([item for item in applied_ids if item not in auto_included])),
            'selective_import': requested_selection is not None,
            'rechecked_preview': _public_import_preview(plan),
            'applied_at': now,
            'rollback_available': bool(operations),
        }
        if operations:
            cur = await db.execute(
                """
                INSERT INTO reader_journal_import_runs(
                    user_id, preview_token, source_version, source_generated_at,
                    backup_json, before_snapshot_json, after_snapshot_json,
                    result_json, applied_at, rolled_back_at, rollback_result_json
                ) VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL)
                """,
                (
                    int(user_id), token, str(preview_row['source_version'] or ''),
                    str(preview_row['source_generated_at'] or '') or None,
                    json.dumps(backup, ensure_ascii=False, separators=(',', ':')),
                    json.dumps(before_snapshot, ensure_ascii=False, separators=(',', ':')),
                    json.dumps(after_snapshot, ensure_ascii=False, separators=(',', ':')),
                    json.dumps(result, ensure_ascii=False, separators=(',', ':')), now,
                ),
            )
            result['import_run_id'] = int(cur.lastrowid)
            await db.execute(
                'UPDATE reader_journal_import_runs SET result_json=? WHERE id=?',
                (json.dumps(result, ensure_ascii=False, separators=(',', ':')), int(cur.lastrowid)),
            )
        await db.execute(
            'UPDATE reader_journal_import_previews SET applied_at=?, result_json=? WHERE token=? AND user_id=?',
            (now, json.dumps(result, ensure_ascii=False, separators=(',', ':')), token, int(user_id)),
        )
        await db.commit()
    return result

def _parse_hhmm(value: object, *, default_hour: int, default_minute: int = 0) -> tuple[int, int]:
    raw = str(value or "").strip()
    if not raw:
        return int(default_hour), int(default_minute)
    try:
        hour_raw, minute_raw = raw.split(":", 1)
        hour, minute = int(hour_raw), int(minute_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Время должно быть указано в формате ЧЧ:ММ.") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Время должно быть указано в формате ЧЧ:ММ.")
    return hour, minute


def _parse_weekdays(value: object) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = str(value or "").replace(";", ",").split(",")
    result = sorted({int(item) for item in parts if str(item).strip()})
    if any(day < 1 or day > 7 for day in result):
        raise ValueError("Дни недели должны быть от 1 до 7.")
    return result


def _notification_settings_public(row: Any) -> dict[str, Any]:
    weekdays = _parse_weekdays(row["reminder_weekdays"] or "1,2,3,4,5,6,7")
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    monthly_enabled = bool(int(row["monthly_report_enabled"] or 0)) if "monthly_report_enabled" in keys else True
    monthly_day = max(1, min(7, int(row["monthly_report_day"] or 1))) if "monthly_report_day" in keys else 1
    monthly_hour = int(row["monthly_report_hour"] or 20) if "monthly_report_hour" in keys else 20
    monthly_minute = int(row["monthly_report_minute"] or 0) if "monthly_report_minute" in keys else 0
    return {
        "reminder_enabled": bool(int(row["reminder_enabled"] or 0)),
        "reminder_time": f"{int(row['reminder_hour'] or 0):02d}:{int(row['reminder_minute'] or 0):02d}",
        "reminder_weekdays": weekdays,
        "inactive_days": max(1, int(row["inactive_days"] or 3)),
        "weekly_report_enabled": bool(int(row["weekly_report_enabled"] or 0)),
        "weekly_report_weekday": max(1, min(7, int(row["weekly_report_weekday"] or 7))),
        "weekly_report_time": f"{int(row['weekly_report_hour'] or 0):02d}:{int(row['weekly_report_minute'] or 0):02d}",
        "monthly_report_enabled": monthly_enabled,
        "monthly_report_day": monthly_day,
        "monthly_report_time": f"{monthly_hour:02d}:{monthly_minute:02d}",
        "timezone_offset_minutes": max(-720, min(840, int(row["timezone_offset_minutes"] or 0))),
        "updated_at": str(row["updated_at"] or ""),
    }


async def get_user_reading_notification_settings(user_id: int) -> dict[str, Any]:
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO reader_notification_settings(user_id, updated_at)
            VALUES(?, ?) ON CONFLICT(user_id) DO NOTHING
            """,
            (int(user_id), now),
        )
        cur = await db.execute("SELECT * FROM reader_notification_settings WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
        await db.commit()
    return _notification_settings_public(row)


async def set_user_reading_notification_settings(
    user_id: int,
    *,
    reminder_enabled: bool,
    reminder_time: object,
    reminder_weekdays: object,
    inactive_days: int,
    weekly_report_enabled: bool,
    weekly_report_weekday: int,
    weekly_report_time: object,
    monthly_report_enabled: bool,
    monthly_report_day: int,
    monthly_report_time: object,
    timezone_offset_minutes: int,
) -> dict[str, Any]:
    reminder_hour, reminder_minute = _parse_hhmm(reminder_time, default_hour=19)
    report_hour, report_minute = _parse_hhmm(weekly_report_time, default_hour=20)
    monthly_hour, monthly_minute = _parse_hhmm(monthly_report_time, default_hour=20)
    weekdays = _parse_weekdays(reminder_weekdays)
    if reminder_enabled and not weekdays:
        raise ValueError("Выберите хотя бы один день для напоминаний.")
    inactive = max(1, min(30, int(inactive_days)))
    report_weekday = max(1, min(7, int(weekly_report_weekday)))
    monthly_day = max(1, min(7, int(monthly_report_day)))
    timezone_offset = max(-720, min(840, int(timezone_offset_minutes)))
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO reader_notification_settings(
                user_id, reminder_enabled, reminder_hour, reminder_minute,
                reminder_weekdays, inactive_days, weekly_report_enabled,
                weekly_report_weekday, weekly_report_hour, weekly_report_minute,
                monthly_report_enabled, monthly_report_day, monthly_report_hour, monthly_report_minute,
                timezone_offset_minutes, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                reminder_enabled=excluded.reminder_enabled,
                reminder_hour=excluded.reminder_hour,
                reminder_minute=excluded.reminder_minute,
                reminder_weekdays=excluded.reminder_weekdays,
                inactive_days=excluded.inactive_days,
                weekly_report_enabled=excluded.weekly_report_enabled,
                weekly_report_weekday=excluded.weekly_report_weekday,
                weekly_report_hour=excluded.weekly_report_hour,
                weekly_report_minute=excluded.weekly_report_minute,
                monthly_report_enabled=excluded.monthly_report_enabled,
                monthly_report_day=excluded.monthly_report_day,
                monthly_report_hour=excluded.monthly_report_hour,
                monthly_report_minute=excluded.monthly_report_minute,
                timezone_offset_minutes=excluded.timezone_offset_minutes,
                updated_at=excluded.updated_at
            """,
            (
                int(user_id), 1 if reminder_enabled else 0, reminder_hour, reminder_minute,
                ",".join(str(day) for day in weekdays), inactive, 1 if weekly_report_enabled else 0,
                report_weekday, report_hour, report_minute, 1 if monthly_report_enabled else 0,
                monthly_day, monthly_hour, monthly_minute, timezone_offset, now,
            ),
        )
        await db.commit()
        cur = await db.execute("SELECT * FROM reader_notification_settings WHERE user_id=?", (int(user_id),))
        row = await cur.fetchone()
    return _notification_settings_public(row)


def _local_datetime(now_utc: datetime, offset_minutes: int) -> datetime:
    base = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc)
    return base.astimezone(timezone.utc) + timedelta(minutes=max(-720, min(840, int(offset_minutes))))


def _schedule_hour_due(local_now: datetime, hour: int, minute: int, *, window_minutes: int = 59) -> bool:
    scheduled = local_now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    delta_minutes = (local_now - scheduled).total_seconds() / 60
    return 0 <= delta_minutes <= max(5, int(window_minutes))


async def was_smart_notification_sent(user_id: int, code: str, context_key: str) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "SELECT 1 FROM smart_notification_state WHERE user_id=? AND notification_code=? AND context_key=?",
            (int(user_id), str(code)[:60], str(context_key)[:120]),
        )
        return bool(await cur.fetchone())


async def list_weekly_reader_report_candidates(limit: int = 100, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    now_value = now_utc or datetime.now(timezone.utc)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    recent_activity_date = (now_value - timedelta(days=14)).date().isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT u.id AS user_id, u.telegram_id, u.full_name,
                   rns.weekly_report_weekday, rns.weekly_report_hour, rns.weekly_report_minute,
                   rns.timezone_offset_minutes
            FROM users u
            JOIN reader_notification_settings rns ON rns.user_id=u.id
            LEFT JOIN user_preferences pref ON pref.user_id=u.id
            WHERE u.is_blocked=0
              AND rns.weekly_report_enabled=1
              AND COALESCE(pref.notifications,1)=1
              AND COALESCE(pref.notifications_reminders,1)=1
              AND EXISTS (
                  SELECT 1 FROM reader_activity_daily rad
                  WHERE rad.user_id=u.id AND rad.activity_date>=? AND rad.sessions>0
              )
            ORDER BY u.id
            LIMIT ?
            """,
            (recent_activity_date, max(1, min(1000, int(limit) * 5))),
        )
        rows = [dict(row) for row in await cur.fetchall()]
    result: list[dict[str, Any]] = []
    for row in rows:
        local_now = _local_datetime(now_value, int(row.get("timezone_offset_minutes") or 0))
        if local_now.isoweekday() != int(row.get("weekly_report_weekday") or 7):
            continue
        if not _schedule_hour_due(local_now, int(row.get("weekly_report_hour") or 20), int(row.get("weekly_report_minute") or 0)):
            continue
        iso_year, iso_week, _ = local_now.isocalendar()
        context_key = f"{iso_year}-W{iso_week:02d}"
        if await was_smart_notification_sent(int(row["user_id"]), "weekly_reading_report", context_key):
            continue
        row["context_key"] = context_key
        result.append(row)
        if len(result) >= max(1, int(limit)):
            break
    return result


async def list_monthly_reader_report_candidates(limit: int = 100, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    """Выбирает пользователей для одного личного итога за завершённый месяц."""
    now_value = now_utc or datetime.now(timezone.utc)
    if now_value.tzinfo is None:
        now_value = now_value.replace(tzinfo=timezone.utc)
    recent_activity_date = (now_value - timedelta(days=75)).date().isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT u.id AS user_id, u.telegram_id, u.full_name,
                   rns.monthly_report_day, rns.monthly_report_hour, rns.monthly_report_minute,
                   rns.timezone_offset_minutes, recent.active_months
            FROM users u
            JOIN reader_notification_settings rns ON rns.user_id=u.id
            JOIN (
                SELECT user_id, GROUP_CONCAT(DISTINCT substr(activity_date,1,7)) AS active_months
                FROM reader_activity_daily
                WHERE activity_date>=? AND sessions>0
                GROUP BY user_id
            ) recent ON recent.user_id=u.id
            LEFT JOIN user_preferences pref ON pref.user_id=u.id
            WHERE u.is_blocked=0
              AND rns.monthly_report_enabled=1
              AND COALESCE(pref.notifications,1)=1
              AND COALESCE(pref.notifications_reminders,1)=1
            ORDER BY u.id
            LIMIT ?
            """,
            (recent_activity_date, max(1, min(1000, int(limit) * 5))),
        )
        rows = [dict(row) for row in await cur.fetchall()]
    result: list[dict[str, Any]] = []
    for row in rows:
        local_now = _local_datetime(now_value, int(row.get("timezone_offset_minutes") or 0))
        if local_now.day != max(1, min(7, int(row.get("monthly_report_day") or 1))):
            continue
        if not _schedule_hour_due(local_now, int(row.get("monthly_report_hour") or 20), int(row.get("monthly_report_minute") or 0)):
            continue
        current_month_start = local_now.date().replace(day=1)
        report_month_end = current_month_start - timedelta(days=1)
        report_month = report_month_end.strftime("%Y-%m")
        active_months = {item for item in str(row.get("active_months") or "").split(",") if item}
        if report_month not in active_months:
            continue
        if await was_smart_notification_sent(int(row["user_id"]), "monthly_reading_report", report_month):
            continue
        row["context_key"] = report_month
        row["report_month"] = report_month
        result.append(row)
        if len(result) >= max(1, int(limit)):
            break
    return result


async def _record_reader_activity_db(
    db: aiosqlite.Connection,
    *,
    user_id: int,
    content_type: str,
    target_id: int,
    position_value: int,
    updated_at: str | None = None,
) -> None:
    kind = str(content_type or "text")
    if kind not in {"text", "audio", "graphic"}:
        return
    now = str(updated_at or utc_now())
    try:
        parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        if parsed_now.tzinfo is None:
            parsed_now = parsed_now.replace(tzinfo=timezone.utc)
    except ValueError:
        parsed_now = datetime.now(timezone.utc)
    cur = await db.execute(
        "SELECT timezone_offset_minutes FROM reader_notification_settings WHERE user_id=?",
        (int(user_id),),
    )
    timezone_row = await cur.fetchone()
    local_now = _local_datetime(parsed_now, int(timezone_row["timezone_offset_minutes"] or 0) if timezone_row else 0)
    activity_date = local_now.date().isoformat()
    position = max(0, int(position_value or 0))
    cur = await db.execute(
        """
        SELECT last_position, accumulated_value
        FROM reader_activity_targets
        WHERE user_id=? AND activity_date=? AND content_type=? AND target_id=?
        """,
        (int(user_id), activity_date, kind, int(target_id)),
    )
    previous = await cur.fetchone()
    first_session = previous is None
    delta = 0
    if previous is None:
        await db.execute(
            """
            INSERT INTO reader_activity_targets(
                user_id,activity_date,content_type,target_id,first_position,last_position,
                accumulated_value,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,0,?,?)
            """,
            (int(user_id), activity_date, kind, int(target_id), position, position, now, now),
        )
    else:
        raw_delta = max(0, position - int(previous["last_position"] or 0))
        if kind == "text":
            delta = min(100, raw_delta)
        elif kind == "audio":
            delta = min(3600, raw_delta)
        else:
            delta = min(500, raw_delta)
        await db.execute(
            """
            UPDATE reader_activity_targets
            SET last_position=?, accumulated_value=accumulated_value+?, updated_at=?
            WHERE user_id=? AND activity_date=? AND content_type=? AND target_id=?
            """,
            (position, delta, now, int(user_id), activity_date, kind, int(target_id)),
        )

    text_chapters = 1 if first_session and kind == "text" else 0
    text_progress = delta if kind == "text" else 0
    audio_seconds = delta if kind == "audio" else 0
    graphic_pages = delta if kind == "graphic" else 0
    sessions = 1 if first_session else 0
    await db.execute(
        """
        INSERT INTO reader_activity_daily(
            user_id,activity_date,text_chapters,text_progress_points,audio_seconds,
            graphic_pages,sessions,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id,activity_date) DO UPDATE SET
            text_chapters=reader_activity_daily.text_chapters+excluded.text_chapters,
            text_progress_points=reader_activity_daily.text_progress_points+excluded.text_progress_points,
            audio_seconds=reader_activity_daily.audio_seconds+excluded.audio_seconds,
            graphic_pages=reader_activity_daily.graphic_pages+excluded.graphic_pages,
            sessions=reader_activity_daily.sessions+excluded.sessions,
            updated_at=excluded.updated_at
        """,
        (
            int(user_id), activity_date, text_chapters, text_progress, audio_seconds,
            graphic_pages, sessions, now, now,
        ),
    )


async def set_user_reading_goals(
    user_id: int,
    *,
    active_days_week: int,
    text_chapters_week: int,
    audio_minutes_week: int,
    graphic_pages_week: int,
) -> dict[str, int | str]:
    active_days = max(0, min(7, int(active_days_week)))
    text_chapters = max(0, min(200, int(text_chapters_week)))
    audio_minutes = max(0, min(10080, int(audio_minutes_week)))
    graphic_pages = max(0, min(5000, int(graphic_pages_week)))
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO reader_goal_settings(
                user_id,active_days_week,text_chapters_week,audio_minutes_week,
                graphic_pages_week,updated_at
            ) VALUES(?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                active_days_week=excluded.active_days_week,
                text_chapters_week=excluded.text_chapters_week,
                audio_minutes_week=excluded.audio_minutes_week,
                graphic_pages_week=excluded.graphic_pages_week,
                updated_at=excluded.updated_at
            """,
            (int(user_id), active_days, text_chapters, audio_minutes, graphic_pages, now),
        )
        await db.commit()
    return {
        "active_days_week": active_days,
        "text_chapters_week": text_chapters,
        "audio_minutes_week": audio_minutes,
        "graphic_pages_week": graphic_pages,
        "updated_at": now,
    }


def _goal_progress(code: str, label: str, current: int, target: int, unit: str) -> dict[str, Any]:
    safe_target = max(0, int(target or 0))
    safe_current = max(0, int(current or 0))
    percent = 0 if safe_target == 0 else min(100, round(safe_current * 100 / safe_target))
    return {
        "code": code,
        "label": label,
        "current": safe_current,
        "target": safe_target,
        "unit": unit,
        "percent": percent,
        "enabled": safe_target > 0,
        "completed": safe_target > 0 and safe_current >= safe_target,
    }


def _next_month_start(value) -> Any:
    return (value.replace(day=28) + timedelta(days=4)).replace(day=1)


def _previous_month_start(value) -> Any:
    return (value.replace(day=1) - timedelta(days=1)).replace(day=1)


def _activity_totals_between(rows: list[dict[str, Any]], start_date, end_date) -> dict[str, int]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        try:
            current_date = datetime.strptime(str(row["activity_date"]), "%Y-%m-%d").date()
        except ValueError:
            continue
        if start_date <= current_date <= end_date:
            selected.append(row)
    audio_seconds = sum(int(row.get("audio_seconds") or 0) for row in selected)
    return {
        "active_days": sum(1 for row in selected if int(row.get("sessions") or 0) > 0),
        "text_chapters": sum(int(row.get("text_chapters") or 0) for row in selected),
        "text_progress_points": sum(int(row.get("text_progress_points") or 0) for row in selected),
        "audio_seconds": audio_seconds,
        "audio_minutes": audio_seconds // 60,
        "graphic_pages": sum(int(row.get("graphic_pages") or 0) for row in selected),
        "sessions": sum(int(row.get("sessions") or 0) for row in selected),
    }


def _activity_comparison(code: str, label: str, unit: str, current: int, previous: int) -> dict[str, Any]:
    current_value = max(0, int(current or 0))
    previous_value = max(0, int(previous or 0))
    delta = current_value - previous_value
    if previous_value > 0:
        percent = round(delta * 100 / previous_value)
        trend = "up" if delta > 0 else "down" if delta < 0 else "same"
    elif current_value > 0:
        percent = None
        trend = "new"
    else:
        percent = 0
        trend = "same"
    return {
        "code": code,
        "label": label,
        "unit": unit,
        "current": current_value,
        "previous": previous_value,
        "delta": delta,
        "percent": percent,
        "trend": trend,
    }


def _activity_day_score(row: dict[str, Any]) -> int:
    return (
        int(row.get("sessions") or 0) * 4
        + int(row.get("text_chapters") or 0) * 5
        + int(row.get("text_progress_points") or 0) // 10
        + int(row.get("audio_seconds") or 0) // 300
        + int(row.get("graphic_pages") or 0) // 5
    )


def _best_activity_day(rows: list[dict[str, Any]], start_date, end_date) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        try:
            current_date = datetime.strptime(str(row["activity_date"]), "%Y-%m-%d").date()
        except ValueError:
            continue
        if start_date <= current_date <= end_date and int(row.get("sessions") or 0) > 0:
            item = dict(row)
            item["score"] = _activity_day_score(item)
            candidates.append(item)
    if not candidates:
        return None
    row = max(candidates, key=lambda item: (int(item.get("score") or 0), str(item.get("activity_date") or "")))
    return {
        "date": str(row.get("activity_date") or ""),
        "sessions": int(row.get("sessions") or 0),
        "text_chapters": int(row.get("text_chapters") or 0),
        "audio_minutes": int(row.get("audio_seconds") or 0) // 60,
        "graphic_pages": int(row.get("graphic_pages") or 0),
    }


def _rhythm_recommendation(current: dict[str, int], previous: dict[str, int], elapsed_days: int) -> dict[str, str]:
    active = int(current.get("active_days") or 0)
    previous_active = int(previous.get("active_days") or 0)
    if active <= 0:
        return {
            "code": "gentle_start",
            "title": "Спокойный старт",
            "text": "В этом месяце ещё не было сеансов. Возвращайтесь тогда, когда появится желание — место чтения сохранено.",
        }
    regular_threshold = max(3, round(max(1, elapsed_days) * 0.45))
    if active >= regular_threshold:
        return {
            "code": "steady",
            "title": "Устойчивый ритм",
            "text": "Активность распределена регулярно. Сохраняйте удобный темп без необходимости увеличивать нагрузку.",
        }
    if active > previous_active:
        return {
            "code": "growing",
            "title": "Ритм становится чаще",
            "text": "Активных дней стало больше, чем за такой же отрезок прошлого месяца. Продолжайте только в комфортном темпе.",
        }
    if previous_active >= 3 and active < previous_active:
        return {
            "code": "more_space",
            "title": "Сейчас больше пространства",
            "text": "Темп стал спокойнее, и это нормально. Даже один короткий сеанс может мягко вернуть историю в привычный ритм.",
        }
    focus_values = {
        "text": int(current.get("text_chapters") or 0) * 8,
        "audio": int(current.get("audio_minutes") or 0),
        "graphic": int(current.get("graphic_pages") or 0) * 2,
    }
    focus = max(focus_values, key=focus_values.get)
    focus_text = {
        "text": "Текст остаётся главным форматом этого месяца.",
        "audio": "Чаще всего в ритм входит прослушивание.",
        "graphic": "Графические истории заметнее всего поддерживают активность.",
    }[focus]
    return {
        "code": f"flexible_{focus}",
        "title": "Гибкий ритм",
        "text": f"{focus_text} Выбирайте формат по настроению — все виды активности учитываются вместе.",
    }


def _monthly_summary_from_rows(rows: list[dict[str, Any]], today) -> dict[str, Any]:
    current_start = today.replace(day=1)
    previous_start = _previous_month_start(current_start)
    previous_end = current_start - timedelta(days=1)
    previous_same_end = min(previous_end, previous_start + timedelta(days=max(0, today.day - 1)))
    current = _activity_totals_between(rows, current_start, today)
    previous_same = _activity_totals_between(rows, previous_start, previous_same_end)
    previous_full = _activity_totals_between(rows, previous_start, previous_end)
    comparisons = [
        _activity_comparison("active_days", "Активные дни", "дн.", current["active_days"], previous_same["active_days"]),
        _activity_comparison("text_chapters", "Текстовые главы", "глав", current["text_chapters"], previous_same["text_chapters"]),
        _activity_comparison("audio_minutes", "Аудио", "мин.", current["audio_minutes"], previous_same["audio_minutes"]),
        _activity_comparison("graphic_pages", "Комиксы", "стр.", current["graphic_pages"], previous_same["graphic_pages"]),
    ]

    trend: list[dict[str, Any]] = []
    month_cursor = current_start
    month_starts = [current_start]
    for _ in range(5):
        month_cursor = _previous_month_start(month_cursor)
        month_starts.append(month_cursor)
    month_starts.reverse()
    max_score = 0
    for month_start in month_starts:
        month_end = min(today, _next_month_start(month_start) - timedelta(days=1))
        totals = _activity_totals_between(rows, month_start, month_end)
        score = (
            totals["active_days"] * 10
            + totals["text_chapters"] * 3
            + totals["audio_minutes"] // 10
            + totals["graphic_pages"] // 8
        )
        max_score = max(max_score, score)
        trend.append({"month": month_start.strftime("%Y-%m"), "totals": totals, "score": score})
    for item in trend:
        item["intensity"] = 0 if max_score <= 0 else max(4, round(int(item["score"]) * 100 / max_score))

    active_days = max(1, int(current.get("active_days") or 0))
    averages = {
        "text_chapters": round(int(current.get("text_chapters") or 0) / active_days, 1) if current.get("active_days") else 0,
        "audio_minutes": round(int(current.get("audio_minutes") or 0) / active_days, 1) if current.get("active_days") else 0,
        "graphic_pages": round(int(current.get("graphic_pages") or 0) / active_days, 1) if current.get("active_days") else 0,
    }
    return {
        "current_month": current_start.strftime("%Y-%m"),
        "previous_month": previous_start.strftime("%Y-%m"),
        "period_start": current_start.isoformat(),
        "period_end": today.isoformat(),
        "comparison_period_start": previous_start.isoformat(),
        "comparison_period_end": previous_same_end.isoformat(),
        "elapsed_days": today.day,
        "current": current,
        "previous_same_period": previous_same,
        "previous_full_month": previous_full,
        "comparisons": comparisons,
        "six_month_trend": trend,
        "best_day": _best_activity_day(rows, current_start, today),
        "averages_per_active_day": averages,
        "recommendation": _rhythm_recommendation(current, previous_same, today.day),
    }


def _completed_month_summary_from_rows(rows: list[dict[str, Any]], month_start) -> dict[str, Any]:
    month_end = _next_month_start(month_start) - timedelta(days=1)
    previous_start = _previous_month_start(month_start)
    previous_end = month_start - timedelta(days=1)
    current = _activity_totals_between(rows, month_start, month_end)
    previous = _activity_totals_between(rows, previous_start, previous_end)
    comparisons = [
        _activity_comparison("active_days", "Активные дни", "дн.", current["active_days"], previous["active_days"]),
        _activity_comparison("text_chapters", "Текстовые главы", "глав", current["text_chapters"], previous["text_chapters"]),
        _activity_comparison("audio_minutes", "Аудио", "мин.", current["audio_minutes"], previous["audio_minutes"]),
        _activity_comparison("graphic_pages", "Комиксы", "стр.", current["graphic_pages"], previous["graphic_pages"]),
    ]
    return {
        "month": month_start.strftime("%Y-%m"),
        "previous_month": previous_start.strftime("%Y-%m"),
        "totals": current,
        "previous_totals": previous,
        "comparisons": comparisons,
        "best_day": _best_activity_day(rows, month_start, month_end),
        "recommendation": _rhythm_recommendation(current, previous, month_end.day),
    }


async def get_user_monthly_reading_report(user_id: int, report_month: str | None = None) -> dict[str, Any]:
    settings_payload = await get_user_reading_notification_settings(user_id)
    local_now = _local_datetime(datetime.now(timezone.utc), int(settings_payload.get("timezone_offset_minutes") or 0))
    if report_month:
        try:
            month_start = datetime.strptime(str(report_month), "%Y-%m").date().replace(day=1)
        except ValueError as exc:
            raise ValueError("Месяц должен быть указан в формате ГГГГ-ММ.") from exc
    else:
        month_start = _previous_month_start(local_now.date().replace(day=1))
    previous_start = _previous_month_start(month_start)
    month_end = _next_month_start(month_start) - timedelta(days=1)
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM reader_activity_daily
            WHERE user_id=? AND activity_date>=? AND activity_date<=?
            ORDER BY activity_date ASC
            """,
            (int(user_id), previous_start.isoformat(), month_end.isoformat()),
        )
        rows = [dict(row) for row in await cur.fetchall()]
    return _completed_month_summary_from_rows(rows, month_start)


# v1.13.24 — личные рекорды, памятные вехи и годовая карта активности.
def _activity_rows_with_dates(rows: list[dict[str, Any]]) -> list[tuple[Any, dict[str, Any]]]:
    parsed: list[tuple[Any, dict[str, Any]]] = []
    for row in rows:
        try:
            parsed.append((datetime.strptime(str(row.get("activity_date") or ""), "%Y-%m-%d").date(), row))
        except ValueError:
            continue
    parsed.sort(key=lambda item: item[0])
    return parsed


def _record_payload(code: str, icon: str, title: str, value: int, unit: str, record_date: str = "", note: str = "") -> dict[str, Any]:
    return {
        "code": code,
        "icon": icon,
        "title": title,
        "value": max(0, int(value or 0)),
        "unit": unit,
        "date": record_date,
        "note": note,
    }


def _personal_reading_records(rows: list[dict[str, Any]], best_streak: int) -> list[dict[str, Any]]:
    parsed = [(day, row) for day, row in _activity_rows_with_dates(rows) if int(row.get("sessions") or 0) > 0]
    if not parsed:
        return []

    def best_row(field: str, *, divisor: int = 1) -> tuple[Any, dict[str, Any], int] | None:
        candidates = [(day, row, int(row.get(field) or 0) // divisor) for day, row in parsed]
        day, row, value = max(candidates, key=lambda item: (item[2], item[0]))
        return (day, row, value) if value > 0 else None

    records: list[dict[str, Any]] = []
    if best_streak > 0:
        records.append(_record_payload("best_streak", "🔥", "Лучшая серия", best_streak, "дн.", note="Самая длинная непрерывная серия активности."))

    best_day, best_day_row = max(parsed, key=lambda item: (_activity_day_score(item[1]), item[0]))
    records.append(_record_payload(
        "best_day", "✦", "Самый насыщенный день", _activity_day_score(best_day_row), "баллов",
        best_day.isoformat(),
        f"{int(best_day_row.get('text_chapters') or 0)} гл. · {int(best_day_row.get('audio_seconds') or 0) // 60} мин. · {int(best_day_row.get('graphic_pages') or 0)} стр.",
    ))

    definitions = (
        ("text_day", "📖", "Глав за один день", "text_chapters", 1, "глав"),
        ("audio_day", "🎧", "Аудио за один день", "audio_seconds", 60, "мин."),
        ("graphic_day", "🖼", "Страниц за один день", "graphic_pages", 1, "стр."),
        ("sessions_day", "📚", "Сеансов за один день", "sessions", 1, "сеансов"),
    )
    for code, icon, title, field, divisor, unit in definitions:
        item = best_row(field, divisor=divisor)
        if item:
            day, _row, value = item
            records.append(_record_payload(code, icon, title, value, unit, day.isoformat()))

    month_rows: dict[str, list[dict[str, Any]]] = {}
    for day, row in parsed:
        month_rows.setdefault(day.strftime("%Y-%m"), []).append(row)
    month_candidates: list[tuple[int, str, dict[str, int]]] = []
    for month_key, items in month_rows.items():
        first = datetime.strptime(f"{month_key}-01", "%Y-%m-%d").date()
        last = _next_month_start(first) - timedelta(days=1)
        totals = _activity_totals_between(items, first, last)
        score = totals["active_days"] * 10 + totals["text_chapters"] * 3 + totals["audio_minutes"] // 10 + totals["graphic_pages"] // 8
        month_candidates.append((score, month_key, totals))
    if month_candidates:
        _score, month_key, totals = max(month_candidates, key=lambda item: (item[0], item[1]))
        records.append(_record_payload(
            "best_month", "🗓", "Самый активный месяц", totals["active_days"], "активных дней",
            f"{month_key}-01",
            f"{totals['text_chapters']} гл. · {totals['audio_minutes']} мин. · {totals['graphic_pages']} стр.",
        ))
    return records


def _cumulative_crossing_date(parsed: list[tuple[Any, dict[str, Any]]], field: str, threshold: int) -> str:
    total = 0
    for day, row in parsed:
        total += max(0, int(row.get(field) or 0))
        if total >= threshold:
            return day.isoformat()
    return ""


def _active_day_crossing_date(parsed: list[tuple[Any, dict[str, Any]]], threshold: int) -> str:
    count = 0
    for day, row in parsed:
        if int(row.get("sessions") or 0) <= 0:
            continue
        count += 1
        if count >= threshold:
            return day.isoformat()
    return ""


def _streak_crossing_date(parsed: list[tuple[Any, dict[str, Any]]], threshold: int) -> str:
    running = 0
    previous_day = None
    for day, row in parsed:
        if int(row.get("sessions") or 0) <= 0:
            continue
        running = running + 1 if previous_day and day == previous_day + timedelta(days=1) else 1
        previous_day = day
        if running >= threshold:
            return day.isoformat()
    return ""


def _milestone_payload(
    code: str, icon: str, title: str, description: str, current: int, target: int,
    unit: str, achieved_at: str,
) -> dict[str, Any]:
    current_value = max(0, int(current or 0))
    target_value = max(1, int(target or 1))
    return {
        "code": code,
        "icon": icon,
        "title": title,
        "description": description,
        "current": current_value,
        "target": target_value,
        "unit": unit,
        "progress": min(100, round(current_value * 100 / target_value)),
        "achieved": bool(achieved_at),
        "achieved_at": achieved_at,
    }


def _personal_reading_milestones(rows: list[dict[str, Any]], best_streak: int) -> dict[str, Any]:
    parsed = _activity_rows_with_dates(rows)
    active_days = sum(1 for _day, row in parsed if int(row.get("sessions") or 0) > 0)
    text_chapters = sum(max(0, int(row.get("text_chapters") or 0)) for _day, row in parsed)
    audio_minutes = sum(max(0, int(row.get("audio_seconds") or 0)) for _day, row in parsed) // 60
    graphic_pages = sum(max(0, int(row.get("graphic_pages") or 0)) for _day, row in parsed)

    items: list[dict[str, Any]] = []
    definitions = (
        ("first_day", "🌱", "Первая сохранённая активность", "Начало личной истории чтения в VoxLyra.", "active", 1, "день"),
        ("active_7", "📅", "7 активных дней", "Семь дней с чтением, аудио или комиксами — без требования идти подряд.", "active", 7, "дн."),
        ("active_30", "🗓", "30 активных дней", "Тридцать личных дней, к которым можно вернуться в годовой карте.", "active", 30, "дн."),
        ("active_100", "✨", "100 активных дней", "Большая личная веха без сравнения с другими читателями.", "active", 100, "дн."),
        ("text_10", "📖", "10 текстовых глав", "Первые десять текстовых глав в общей статистике.", "text", 10, "глав"),
        ("text_50", "📚", "50 текстовых глав", "Пятьдесят глав, прочитанных в собственном темпе.", "text", 50, "глав"),
        ("text_100", "🏛", "100 текстовых глав", "Сотня текстовых глав в личной истории.", "text", 100, "глав"),
        ("audio_300", "🎧", "5 часов аудио", "Пять часов прослушивания книг и историй.", "audio", 300, "мин."),
        ("audio_1200", "🎙", "20 часов аудио", "Двадцать часов личной аудиотеки.", "audio", 1200, "мин."),
        ("graphic_100", "🖼", "100 страниц комиксов", "Первые сто просмотренных страниц графических историй.", "graphic", 100, "стр."),
        ("graphic_500", "🎨", "500 страниц комиксов", "Пятьсот страниц манги, манхвы и комиксов.", "graphic", 500, "стр."),
        ("streak_7", "🔥", "Серия 7 дней", "Семь активных дней подряд, если такой ритм однажды сложился сам.", "streak", 7, "дн."),
        ("streak_30", "💫", "Серия 30 дней", "Тридцать дней подряд — памятный рекорд, а не обязательная цель.", "streak", 30, "дн."),
    )
    for code, icon, title, description, category, target, unit in definitions:
        if category == "active":
            current = active_days
            achieved_at = _active_day_crossing_date(parsed, target)
        elif category == "text":
            current = text_chapters
            achieved_at = _cumulative_crossing_date(parsed, "text_chapters", target)
        elif category == "audio":
            current = audio_minutes
            achieved_at = _cumulative_crossing_date(parsed, "audio_seconds", target * 60)
        elif category == "graphic":
            current = graphic_pages
            achieved_at = _cumulative_crossing_date(parsed, "graphic_pages", target)
        else:
            current = best_streak
            achieved_at = _streak_crossing_date(parsed, target)
        items.append(_milestone_payload(code, icon, title, description, current, target, unit, achieved_at))

    achieved = sorted((item for item in items if item["achieved"]), key=lambda item: (str(item["achieved_at"]), item["target"]), reverse=True)
    upcoming = sorted((item for item in items if not item["achieved"]), key=lambda item: (int(item["progress"]), -int(item["target"])), reverse=True)
    return {
        "achieved_count": len(achieved),
        "total_count": len(items),
        "latest": achieved[:8],
        "upcoming": upcoming[:4],
        "items": items,
    }


def _yearly_activity_summary(rows: list[dict[str, Any]], selected_year: int, today) -> dict[str, Any]:
    year = max(1970, min(int(selected_year), int(today.year)))
    year_start = today.replace(year=year, month=1, day=1)
    year_end = today.replace(year=year, month=12, day=31)
    parsed = _activity_rows_with_dates(rows)
    by_date = {day.isoformat(): row for day, row in parsed if day.year == year}
    scores = [_activity_day_score(row) for day, row in parsed if day.year == year and day <= today and int(row.get("sessions") or 0) > 0]
    max_score = max(scores, default=0)
    days: list[dict[str, Any]] = []
    cursor = year_start
    while cursor <= year_end:
        row = by_date.get(cursor.isoformat(), {})
        future = cursor > today
        sessions = 0 if future else int(row.get("sessions") or 0)
        score = 0 if future else _activity_day_score(row)
        intensity = 0 if score <= 0 or max_score <= 0 else max(1, min(4, (score * 4 + max_score - 1) // max_score))
        days.append({
            "date": cursor.isoformat(),
            "future": future,
            "active": sessions > 0,
            "intensity": intensity,
            "sessions": sessions,
            "text_chapters": 0 if future else int(row.get("text_chapters") or 0),
            "audio_minutes": 0 if future else int(row.get("audio_seconds") or 0) // 60,
            "graphic_pages": 0 if future else int(row.get("graphic_pages") or 0),
        })
        cursor += timedelta(days=1)

    months: list[dict[str, Any]] = []
    strongest: dict[str, Any] | None = None
    for month in range(1, 13):
        month_start = year_start.replace(month=month, day=1)
        month_end = _next_month_start(month_start) - timedelta(days=1)
        effective_end = min(month_end, today) if year == today.year else month_end
        totals = _activity_totals_between(rows, month_start, effective_end)
        score = totals["active_days"] * 10 + totals["text_chapters"] * 3 + totals["audio_minutes"] // 10 + totals["graphic_pages"] // 8
        item = {"month": month_start.strftime("%Y-%m"), "totals": totals, "score": score, "future": month_start > today}
        months.append(item)
        if score > 0 and not item["future"] and (strongest is None or (score, item["month"]) > (int(strongest["score"]), str(strongest["month"]))):
            strongest = item

    totals = _activity_totals_between(rows, year_start, min(year_end, today))
    years = sorted({day.year for day, _row in parsed if day.year <= today.year} | {today.year, year}, reverse=True)
    return {
        "year": year,
        "available_years": years,
        "totals": totals,
        "days": days,
        "months": months,
        "strongest_month": strongest,
        "privacy_note": "Эта карта и рекорды видны только владельцу профиля.",
    }


async def get_user_reading_dashboard(user_id: int, activity_year: int | None = None) -> dict[str, Any]:
    notification_settings = await get_user_reading_notification_settings(user_id)
    local_now = _local_datetime(datetime.now(timezone.utc), int(notification_settings.get("timezone_offset_minutes") or 0))
    today = local_now.date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    calendar_start = today - timedelta(days=34)
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO reader_goal_settings(user_id,updated_at)
            VALUES(?,?) ON CONFLICT(user_id) DO NOTHING
            """,
            (int(user_id), now),
        )
        cur = await db.execute("SELECT * FROM reader_goal_settings WHERE user_id=?", (int(user_id),))
        goals_row = await cur.fetchone()
        cur = await db.execute(
            """
            SELECT * FROM reader_activity_daily
            WHERE user_id=? ORDER BY activity_date ASC
            """,
            (int(user_id),),
        )
        rows = [dict(row) for row in await cur.fetchall()]
        await db.commit()

    by_date: dict[str, dict[str, Any]] = {str(row["activity_date"]): row for row in rows}

    active_dates = []
    for row in rows:
        if int(row.get("sessions") or 0) <= 0:
            continue
        try:
            active_dates.append(datetime.strptime(str(row["activity_date"]), "%Y-%m-%d").date())
        except ValueError:
            continue
    active_set = set(active_dates)
    streak_cursor = today if today in active_set else today - timedelta(days=1)
    current_streak = 0
    while streak_cursor in active_set:
        current_streak += 1
        streak_cursor -= timedelta(days=1)
    best_streak = 0
    running = 0
    previous_date = None
    for current_date in sorted(active_set):
        running = running + 1 if previous_date and current_date == previous_date + timedelta(days=1) else 1
        best_streak = max(best_streak, running)
        previous_date = current_date

    today_totals = _activity_totals_between(rows, today, today)
    week_totals = _activity_totals_between(rows, week_start, today)
    month_totals = _activity_totals_between(rows, month_start, today)
    all_totals = _activity_totals_between(rows, datetime(1970, 1, 1).date(), today)
    monthly_summary = _monthly_summary_from_rows(rows, today)
    selected_year = int(activity_year or today.year)
    yearly_activity = _yearly_activity_summary(rows, selected_year, today)
    personal_records = _personal_reading_records(rows, best_streak)
    milestones = _personal_reading_milestones(rows, best_streak)

    calendar = []
    for offset in range(35):
        current_date = calendar_start + timedelta(days=offset)
        row = by_date.get(current_date.isoformat(), {})
        sessions = int(row.get("sessions") or 0)
        activity_score = (
            sessions
            + int(row.get("text_progress_points") or 0) // 20
            + int(row.get("audio_seconds") or 0) // 600
            + int(row.get("graphic_pages") or 0) // 10
        )
        intensity = 0 if sessions <= 0 else min(4, 1 + activity_score // 3)
        calendar.append({
            "date": current_date.isoformat(),
            "active": sessions > 0,
            "intensity": intensity,
            "sessions": sessions,
            "text_chapters": int(row.get("text_chapters") or 0),
            "audio_minutes": int(row.get("audio_seconds") or 0) // 60,
            "graphic_pages": int(row.get("graphic_pages") or 0),
        })

    goals = {
        "active_days_week": int(goals_row["active_days_week"] or 0),
        "text_chapters_week": int(goals_row["text_chapters_week"] or 0),
        "audio_minutes_week": int(goals_row["audio_minutes_week"] or 0),
        "graphic_pages_week": int(goals_row["graphic_pages_week"] or 0),
        "updated_at": str(goals_row["updated_at"] or ""),
    }
    goal_items = [
        _goal_progress("active_days_week", "Активные дни", week_totals["active_days"], goals["active_days_week"], "дн."),
        _goal_progress("text_chapters_week", "Текстовые главы", week_totals["text_chapters"], goals["text_chapters_week"], "глав"),
        _goal_progress("audio_minutes_week", "Прослушивание", week_totals["audio_minutes"], goals["audio_minutes_week"], "мин."),
        _goal_progress("graphic_pages_week", "Страницы комиксов", week_totals["graphic_pages"], goals["graphic_pages_week"], "стр."),
    ]
    enabled_goals = [item for item in goal_items if item["enabled"]]
    return {
        "timezone": f"UTC{int(notification_settings.get('timezone_offset_minutes') or 0) / 60:+g}",
        "today": today.isoformat(),
        "week_start": week_start.isoformat(),
        "current_streak": current_streak,
        "best_streak": best_streak,
        "today_totals": today_totals,
        "week_totals": week_totals,
        "month_totals": month_totals,
        "monthly_summary": monthly_summary,
        "yearly_activity": yearly_activity,
        "personal_records": personal_records,
        "milestones": milestones,
        "all_totals": all_totals,
        "calendar": calendar,
        "goals": goals,
        "goal_items": goal_items,
        "completed_goals": sum(1 for item in enabled_goals if item["completed"]),
        "enabled_goals": len(enabled_goals),
    }


# ---------------------------------------------------------------------------
# VoxLyra v1.13.33 — атомарная защита Stars и точные права доступа
# ---------------------------------------------------------------------------

async def _ensure_v11333_monetization_schema(db: aiosqlite.Connection) -> None:
    """Create an idempotent entitlement registry and backfill legacy purchases."""
    from app.services.monetization_guard import access_descriptor_for_purchase_row

    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS purchase_access_claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            access_key TEXT NOT NULL,
            purchase_id INTEGER,
            charge_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, access_key),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_purchase_access_claims_charge
            ON purchase_access_claims(charge_id) WHERE charge_id!='';
        CREATE INDEX IF NOT EXISTS idx_purchase_access_claims_status_expiry
            ON purchase_access_claims(status, expires_at);
        CREATE INDEX IF NOT EXISTS idx_purchase_access_claims_purchase
            ON purchase_access_claims(purchase_id);

        CREATE TABLE IF NOT EXISTS monetization_incidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_type TEXT NOT NULL,
            access_key TEXT NOT NULL DEFAULT '',
            charge_id TEXT NOT NULL DEFAULT '',
            purchase_id INTEGER,
            details_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_monetization_incidents_status
            ON monetization_incidents(status, created_at DESC);
        """
    )
    defaults = {
        "monetization_duplicate_guard_enabled": "1",
        "monetization_auto_refund_duplicate": "1",
    }
    for key, value in defaults.items():
        await db.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )

    cur = await db.execute(
        """
        SELECT p.* FROM purchases p
        WHERE p.status IN ('paid','canceling')
        ORDER BY p.id ASC
        """
    )
    for row in await cur.fetchall():
        descriptor = access_descriptor_for_purchase_row(row)
        if descriptor is None:
            continue
        await db.execute(
            """
            INSERT OR IGNORE INTO purchase_access_claims(
                user_id,access_key,purchase_id,charge_id,status,expires_at,created_at,updated_at
            ) VALUES(?,?,?,?, 'active',NULL,?,?)
            """,
            (
                int(row["user_id"]), descriptor.key, int(row["id"]),
                str(row["telegram_payment_charge_id"] or ""),
                str(row["created_at"] or now), now,
            ),
        )


class DuplicatePurchaseError(ValueError):
    def __init__(self, message: str = "Доступ уже открыт", *, access_key: str = "", purchase_id: int | None = None):
        super().__init__(message)
        self.access_key = str(access_key or "")
        self.purchase_id = int(purchase_id) if purchase_id else None


class PurchaseProcessingError(ValueError):
    pass


async def _paid_purchase_for_key(db: aiosqlite.Connection, user_id: int, access_key: str) -> aiosqlite.Row | None:
    kind, _, raw = str(access_key).partition(":")
    if kind == "book" and raw.isdigit():
        cur = await db.execute(
            """SELECT * FROM purchases WHERE user_id=? AND book_id=?
               AND chapter_id IS NULL AND audio_chapter_id IS NULL
               AND graphic_chapter_id IS NULL AND graphic_volume_number IS NULL
               AND COALESCE(purchase_kind,'content')='content'
               AND status IN ('paid','canceling') ORDER BY id DESC LIMIT 1""",
            (int(user_id), int(raw)),
        )
    elif kind == "chapter" and raw.isdigit():
        cur = await db.execute(
            "SELECT * FROM purchases WHERE user_id=? AND chapter_id=? AND status IN ('paid','canceling') ORDER BY id DESC LIMIT 1",
            (int(user_id), int(raw)),
        )
    elif kind == "audio" and raw.isdigit():
        cur = await db.execute(
            "SELECT * FROM purchases WHERE user_id=? AND audio_chapter_id=? AND status IN ('paid','canceling') ORDER BY id DESC LIMIT 1",
            (int(user_id), int(raw)),
        )
    elif kind == "graphic" and raw.isdigit():
        cur = await db.execute(
            "SELECT * FROM purchases WHERE user_id=? AND graphic_chapter_id=? AND status IN ('paid','canceling') ORDER BY id DESC LIMIT 1",
            (int(user_id), int(raw)),
        )
    elif kind == "graphic_volume":
        parts = raw.split(":", 1)
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None
        cur = await db.execute(
            """SELECT * FROM purchases WHERE user_id=? AND book_id=? AND graphic_volume_number=?
               AND COALESCE(purchase_kind,'')='graphic_volume'
               AND status IN ('paid','canceling') ORDER BY id DESC LIMIT 1""",
            (int(user_id), int(parts[0]), int(parts[1])),
        )
    else:
        return None
    return await cur.fetchone()


async def _claim_or_purchase_conflict(
    db: aiosqlite.Connection,
    user_id: int,
    conflict_keys: tuple[str, ...],
    *,
    ignore_charge_id: str = "",
) -> dict[str, Any] | None:
    now = utc_now()
    await db.execute(
        "DELETE FROM purchase_access_claims WHERE status='processing' AND expires_at IS NOT NULL AND expires_at<=?",
        (now,),
    )
    for key in conflict_keys:
        cur = await db.execute(
            """SELECT * FROM purchase_access_claims
               WHERE user_id=? AND access_key=? AND status IN ('processing','active') LIMIT 1""",
            (int(user_id), str(key)),
        )
        claim = await cur.fetchone()
        if claim and str(claim["charge_id"] or "") != str(ignore_charge_id or ""):
            return {
                "access_key": str(key), "purchase_id": int(claim["purchase_id"]) if claim["purchase_id"] else None,
                "status": str(claim["status"]), "charge_id": str(claim["charge_id"] or ""),
            }
        purchase = await _paid_purchase_for_key(db, int(user_id), str(key))
        if purchase and str(purchase["telegram_payment_charge_id"] or "") != str(ignore_charge_id or ""):
            return {
                "access_key": str(key), "purchase_id": int(purchase["id"]),
                "status": str(purchase["status"]), "charge_id": str(purchase["telegram_payment_charge_id"] or ""),
            }
    return None


async def _access_descriptor_for_live_target(target: dict[str, Any] | None):
    from app.services.monetization_guard import access_descriptor_for_target
    if not target:
        return None
    live = dict(target)
    if str(live.get("kind") or "") == "graphic" and not int(live.get("volume_number") or 0):
        target_id = int(live.get("target_id") or 0)
        if target_id:
            async with connect() as db:
                cur = await db.execute("SELECT book_id,volume_number FROM graphic_chapters WHERE id=?", (target_id,))
                row = await cur.fetchone()
            if row:
                live["book_id"] = int(row["book_id"] or live.get("book_id") or 0)
                live["volume_number"] = int(row["volume_number"] or 0)
    return access_descriptor_for_target(live)


async def get_purchase_conflict(user_id: int, payload: str) -> dict[str, Any] | None:
    """Return a preflight conflict for unique access, without mutating payment state."""
    target = await get_purchase_target(str(payload or ""))
    if not target:
        return None
    kind = str(target.get("kind") or "")
    if kind == "premium":
        # Recurring Telegram renewals use the same public payload. Blocking here
        # would reject a legitimate renewal; creation of a second manual invoice
        # is prevented by the Premium checkout endpoint instead.
        return None
    if kind == "chapter_package":
        book_id = int(target.get("book_id") or 0)
        if not book_id:
            return None
        async with connect() as db:
            return await _claim_or_purchase_conflict(db, int(user_id), (f"book:{book_id}",))
    descriptor = await _access_descriptor_for_live_target(target)
    if descriptor is None:
        return None
    async with connect() as db:
        return await _claim_or_purchase_conflict(db, int(user_id), descriptor.conflict_keys)


async def has_purchase_access(
    user_id: int,
    *,
    book_id: int | None = None,
    chapter_id: int | None = None,
    audio_chapter_id: int | None = None,
    graphic_chapter_id: int | None = None,
) -> bool:
    """Check exact entitlement. A child purchase never grants the whole book."""
    uid = int(user_id)
    if chapter_id is not None and await has_manual_chapter_access(uid, int(chapter_id)):
        return True
    async with connect() as db:
        if book_id is not None and all(value is None for value in (chapter_id, audio_chapter_id, graphic_chapter_id)):
            return await _paid_purchase_for_key(db, uid, f"book:{int(book_id)}") is not None
        if chapter_id is not None:
            cur = await db.execute("SELECT book_id FROM chapters WHERE id=?", (int(chapter_id),))
            row = await cur.fetchone()
            if not row:
                return False
            if await _paid_purchase_for_key(db, uid, f"book:{int(row['book_id'])}") or await _paid_purchase_for_key(db, uid, f"chapter:{int(chapter_id)}"):
                return True
            cur = await db.execute(
                """SELECT 1 FROM chapter_package_unlocks cpu
                   JOIN chapter_package_balances cpb ON cpb.id=cpu.balance_id
                   JOIN purchases p ON p.id=cpu.purchase_id
                   WHERE cpu.user_id=? AND cpu.chapter_id=? AND cpb.status='active'
                     AND p.status IN ('paid','canceling') LIMIT 1""",
                (uid, int(chapter_id)),
            )
            return await cur.fetchone() is not None
        if audio_chapter_id is not None:
            cur = await db.execute("SELECT book_id FROM audio_chapters WHERE id=?", (int(audio_chapter_id),))
            row = await cur.fetchone()
            return bool(row and (
                await _paid_purchase_for_key(db, uid, f"book:{int(row['book_id'])}")
                or await _paid_purchase_for_key(db, uid, f"audio:{int(audio_chapter_id)}")
            ))
        if graphic_chapter_id is not None:
            cur = await db.execute("SELECT book_id,volume_number FROM graphic_chapters WHERE id=?", (int(graphic_chapter_id),))
            row = await cur.fetchone()
            if not row:
                return False
            keys = [f"book:{int(row['book_id'])}", f"graphic:{int(graphic_chapter_id)}"]
            if int(row["volume_number"] or 0) > 0:
                keys.append(f"graphic_volume:{int(row['book_id'])}:{int(row['volume_number'])}")
            for key in keys:
                if await _paid_purchase_for_key(db, uid, key):
                    return True
            cur = await db.execute(
                """SELECT 1 FROM chapter_package_unlocks cpu
                   JOIN chapter_package_balances cpb ON cpb.id=cpu.balance_id
                   JOIN purchases p ON p.id=cpu.purchase_id
                   WHERE cpu.user_id=? AND cpu.graphic_chapter_id=? AND cpb.status='active'
                     AND p.status IN ('paid','canceling') LIMIT 1""",
                (uid, int(graphic_chapter_id)),
            )
            return await cur.fetchone() is not None
    return False


_create_paid_purchase_before_v11333 = create_paid_purchase


async def create_paid_purchase(
    *,
    user_id: int,
    payload: str,
    amount_stars: int,
    telegram_payment_charge_id: str,
) -> int:
    """Reserve the entitlement before accounting, preventing parallel duplicates."""
    uid = int(user_id)
    charge_id = str(telegram_payment_charge_id or "").strip()
    if not charge_id:
        raise ValueError("Не указан идентификатор платежа")
    target = await get_purchase_target(str(payload))
    if not target:
        raise ValueError("Покупка не найдена")
    canonical_payload = str(payload)
    if str(payload).startswith("vox:intent:"):
        intent = await get_payment_intent(str(payload))
        if intent:
            canonical_payload = str(intent["canonical_payload"] or payload)

    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM purchases WHERE telegram_payment_charge_id=? ORDER BY id LIMIT 1", (charge_id,)
        )
        existing = await cur.fetchone()
        if existing:
            if int(existing["user_id"]) != uid or int(existing["amount_stars"] or 0) != int(amount_stars):
                raise ValueError("Идентификатор платежа уже использован")
            return int(existing["id"])

    kind = str(target.get("kind") or "")
    descriptor = await _access_descriptor_for_live_target(target)
    if kind == "chapter_package":
        book_id = int(target.get("book_id") or 0)
        if book_id:
            async with connect() as db:
                conflict = await _claim_or_purchase_conflict(db, uid, (f"book:{book_id}",), ignore_charge_id=charge_id)
                if conflict:
                    raise DuplicatePurchaseError("Вся книга уже куплена — пакет глав не требуется", access_key=conflict["access_key"], purchase_id=conflict.get("purchase_id"))
    if descriptor is None:
        return await _create_paid_purchase_before_v11333(
            user_id=uid, payload=str(payload), amount_stars=int(amount_stars), telegram_payment_charge_id=charge_id
        )

    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        conflict = await _claim_or_purchase_conflict(db, uid, descriptor.conflict_keys, ignore_charge_id=charge_id)
        if conflict:
            await db.execute(
                """INSERT INTO monetization_incidents(user_id,event_type,access_key,charge_id,purchase_id,details_json,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,'open',?,?)""",
                (uid, "duplicate_payment_blocked", conflict["access_key"], charge_id, conflict.get("purchase_id"),
                 json.dumps({"payload": canonical_payload, "amount_stars": int(amount_stars)}, ensure_ascii=False), now, now),
            )
            await db.commit()
            raise DuplicatePurchaseError("Этот доступ уже был открыт", access_key=conflict["access_key"], purchase_id=conflict.get("purchase_id"))
        try:
            reused = await db.execute(
                """UPDATE purchase_access_claims SET purchase_id=NULL,charge_id=?,status='processing',expires_at=?,updated_at=?
                   WHERE user_id=? AND access_key=? AND status='released'""",
                (charge_id, expires, now, uid, descriptor.key),
            )
            if reused.rowcount <= 0:
                await db.execute(
                    """INSERT INTO purchase_access_claims(user_id,access_key,purchase_id,charge_id,status,expires_at,created_at,updated_at)
                       VALUES(?,?,NULL,?,'processing',?,?,?)""",
                    (uid, descriptor.key, charge_id, expires, now, now),
                )
        except sqlite3.IntegrityError:
            await db.rollback()
            async with connect() as check_db:
                cur = await check_db.execute("SELECT id FROM purchases WHERE telegram_payment_charge_id=?", (charge_id,))
                same = await cur.fetchone()
                if same:
                    return int(same["id"])
            raise PurchaseProcessingError("Платёж уже обрабатывается")
        await db.commit()

    try:
        purchase_id = await _create_paid_purchase_before_v11333(
            user_id=uid, payload=str(payload), amount_stars=int(amount_stars), telegram_payment_charge_id=charge_id
        )
    except Exception:
        async with connect() as db:
            await db.execute(
                "DELETE FROM purchase_access_claims WHERE user_id=? AND access_key=? AND charge_id=? AND status='processing'",
                (uid, descriptor.key, charge_id),
            )
            await db.commit()
        raise
    async with connect() as db:
        await db.execute(
            """UPDATE purchase_access_claims SET purchase_id=?,status='active',expires_at=NULL,updated_at=?
               WHERE user_id=? AND access_key=? AND charge_id=?""",
            (int(purchase_id), utc_now(), uid, descriptor.key, charge_id),
        )
        await db.commit()
    return int(purchase_id)


async def purchase_chapter_from_wallet(user_id: int, chapter_id: int, *, use_bonus: bool = True) -> dict[str, int]:
    """Atomically buy a chapter and its exact entitlement from the prepaid wallet."""
    from app.services.bonus_economy import bonus_discount_limit, load_revenue_split_settings

    uid, cid = int(user_id), int(chapter_id)
    cfg = await load_revenue_split_settings()
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """SELECT c.*, b.title AS book_title, b.author_id, b.pricing_type,
                      b.price_stars AS book_price_stars,b.publication_status
               FROM chapters c JOIN books b ON b.id=c.book_id WHERE c.id=?""", (cid,)
        )
        chapter = await cur.fetchone()
        if not chapter or str(chapter["publication_status"] or "") != "published":
            await db.rollback(); raise ValueError("Глава не найдена")
        if str(chapter["pricing_type"] or "") != "chapters" or int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0:
            await db.rollback(); raise ValueError("Эта глава отдельно не продаётся")
        descriptor_key = f"chapter:{cid}"
        conflict = await _claim_or_purchase_conflict(db, uid, (descriptor_key, f"book:{int(chapter['book_id'])}"))
        if conflict:
            await db.rollback(); raise ValueError("Глава уже доступна")
        cur = await db.execute(
            """SELECT 1 FROM chapter_package_unlocks cpu JOIN chapter_package_balances cpb ON cpb.id=cpu.balance_id
               JOIN purchases p ON p.id=cpu.purchase_id WHERE cpu.user_id=? AND cpu.chapter_id=?
               AND cpb.status='active' AND p.status IN ('paid','canceling') LIMIT 1""", (uid, cid)
        )
        if await cur.fetchone():
            await db.rollback(); raise ValueError("Глава уже доступна")
        charge_id = f"wallet:{uuid.uuid4().hex}"
        claim_expiry = (datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat()
        reused = await db.execute(
            """UPDATE purchase_access_claims SET purchase_id=NULL,charge_id=?,status='processing',expires_at=?,updated_at=?
               WHERE user_id=? AND access_key=? AND status='released'""",
            (charge_id, claim_expiry, now, uid, descriptor_key),
        )
        if reused.rowcount <= 0:
            await db.execute(
                """INSERT INTO purchase_access_claims(user_id,access_key,purchase_id,charge_id,status,expires_at,created_at,updated_at)
                   VALUES(?,?,NULL,?,'processing',?,?,?)""",
                (uid, descriptor_key, charge_id, claim_expiry, now, now),
            )
        await db.execute("INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING", (uid, now, now))
        await db.execute("INSERT INTO bonus_wallets(user_id,balance,created_at,updated_at) VALUES(?,0,?,?) ON CONFLICT(user_id) DO NOTHING", (uid, now, now))
        cur = await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (uid,)); wallet = int((await cur.fetchone())["balance_stars"] or 0)
        cur = await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (uid,)); bonus = int((await cur.fetchone())["balance"] or 0)
        price = int(chapter["price_stars"] or 0)
        plan = bonus_discount_limit(price, bonus if use_bonus else 0, points_per_star=cfg.points_per_star,
                                    author_percent=cfg.author_percent, platform_percent=cfg.platform_percent, bonus_percent=cfg.bonus_percent)
        wallet_needed = int(plan["wallet_stars_needed"]); bonus_stars = int(plan["bonus_stars_used"]); bonus_points = bonus_stars * cfg.points_per_star
        if wallet < wallet_needed:
            await db.rollback(); raise ValueError(f"Недостаточно Stars на балансе. Нужно ещё {wallet_needed-wallet}.")
        cur = await db.execute("UPDATE reader_wallets SET balance_stars=balance_stars-?,updated_at=? WHERE user_id=? AND balance_stars>=?", (wallet_needed, now, uid, wallet_needed))
        if cur.rowcount <= 0:
            await db.rollback(); raise ValueError("Баланс изменился. Повторите покупку.")
        if bonus_points:
            cur = await db.execute("UPDATE bonus_wallets SET balance=balance-?,updated_at=? WHERE user_id=? AND balance>=?", (bonus_points, now, uid, bonus_points))
            if cur.rowcount <= 0:
                await db.rollback(); raise ValueError("Бонусный баланс изменился. Повторите покупку.")
        cur = await db.execute(
            """INSERT INTO purchases(user_id,book_id,chapter_id,amount_stars,status,telegram_payment_charge_id,created_at,payload,purchase_kind,
                                      original_amount_stars,wallet_stars_used,bonus_points_used,funding_method)
               VALUES(?,?,?,?,'paid',?,?,?,'content',?,?,?,'wallet')""",
            (uid, int(chapter["book_id"]), cid, price, charge_id, now, f"vox:chapter:{cid}", price, wallet_needed, bonus_points),
        )
        purchase_id = int(cur.lastrowid)
        await db.execute("UPDATE purchase_access_claims SET purchase_id=?,status='active',expires_at=NULL,updated_at=? WHERE charge_id=?", (purchase_id, now, charge_id))
        await db.execute("INSERT INTO reader_wallet_transactions(user_id,amount_stars,transaction_type,source_type,source_id,metadata_json,created_at) VALUES(?,?,'chapter_purchase','purchase',?,?,?)",
                         (uid, -wallet_needed, str(purchase_id), json.dumps({"chapter_id": cid, "bonus_stars": bonus_stars}, ensure_ascii=False), now))
        if bonus_points:
            await db.execute("INSERT INTO bonus_transactions(user_id,amount,reason,source_type,source_id,created_at) VALUES(?,?,'chapter_purchase_discount','purchase',?,?)", (uid, -bonus_points, str(purchase_id), now))
        if chapter["author_id"] is not None:
            hold_days = int(await get_setting("hold_days_default", "14") or 14); rate_minor = int(await get_setting("payments_stars_author_rate_minor", "100") or 100)
            available_at = (datetime.now(timezone.utc)+timedelta(days=max(0, hold_days))).isoformat()
            commission = int(plan["platform_stars"] + plan["bonus_pool_stars"])
            await db.execute(
                """INSERT INTO author_ledger(author_id,purchase_id,source_type,source_id,gross_stars,commission_percent,commission_stars,net_stars,
                                              settlement_rate_minor,net_minor,hold_days,available_at,status,created_at,updated_at,platform_stars,bonus_pool_stars,bonus_discount_stars)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'held',?,?,?,?,?)""",
                (int(chapter["author_id"]), purchase_id, "chapter", cid, price, 100-cfg.author_percent, commission, int(plan["author_stars"]),
                 rate_minor, int(plan["author_stars"])*rate_minor, hold_days, available_at, now, now,
                 int(plan["platform_stars"]), int(plan["bonus_pool_stars"]), bonus_stars),
            )
        await db.commit()
        return {"purchase_id": purchase_id, "price_stars": price, "wallet_stars_used": wallet_needed,
                "bonus_stars_used": bonus_stars, "bonus_points_used": bonus_points, "wallet_stars": wallet-wallet_needed,
                "bonus_points": bonus-bonus_points, "author_stars": int(plan["author_stars"]),
                "platform_stars": max(0, int(plan["platform_stars"]+plan["bonus_pool_stars"])-bonus_stars)}


_get_user_premium_status_before_v11333 = get_user_premium_status


async def get_user_premium_status(user_id: int) -> dict[str, Any]:
    """Prefer a currently active subscription over a newer refunded/revoked row."""
    now_dt = datetime.now(timezone.utc)
    async with connect() as db:
        await _expire_premium_subscriptions(db, user_id=int(user_id))
        await db.commit()
        cur = await db.execute(
            """SELECT ps.*,pp.title AS plan_title,pp.price_stars,pp.features_json,pp.duration_days,pp.subscription_period_seconds
               FROM premium_subscriptions ps LEFT JOIN premium_plans pp ON pp.code=ps.plan_code
               WHERE ps.user_id=?
               ORDER BY CASE WHEN ps.status IN ('active','canceled') AND ps.expires_at>? THEN 0 ELSE 1 END,
                        ps.expires_at DESC,ps.id DESC LIMIT 1""",
            (int(user_id), now_dt.isoformat()),
        )
        row = await cur.fetchone()
    if not row:
        return await _get_user_premium_status_before_v11333(int(user_id))
    expires_dt = _premium_dt(str(row["expires_at"] or ""))
    active = bool(expires_dt and expires_dt > now_dt and str(row["status"] or "") in {"active", "canceled"})
    try: features = json.loads(str(row["features_json"] or "[]"))
    except json.JSONDecodeError: features = []
    seconds_left = max(0, int((expires_dt-now_dt).total_seconds())) if active and expires_dt else 0
    return {"active": active, "status": str(row["status"] or "none"), "subscription_id": int(row["id"]),
            "plan_code": str(row["plan_code"] or ""), "plan_title": str(row["plan_title"] or "VoxLyra Premium"),
            "price_stars": int(row["price_stars"] or 0), "started_at": str(row["started_at"] or ""),
            "expires_at": str(row["expires_at"] or ""), "days_left": (seconds_left+86399)//86400,
            "is_recurring": bool(row["is_recurring"]), "auto_renew": bool(row["auto_renew"]),
            "is_first_recurring": bool(row["is_first_recurring"]), "telegram_payment_charge_id": str(row["telegram_payment_charge_id"] or ""),
            "source": str(row["source"] or "payment") if "source" in row.keys() else "payment",
            "grant_note": str(row["grant_note"] or "") if "grant_note" in row.keys() else "", "features": features}


_create_refund_request_before_v11333 = create_refund_request


async def create_refund_request(purchase_id: int, user_id: int, reason: str) -> int:
    purchase = await get_purchase(int(purchase_id))
    if not purchase or str(purchase["purchase_kind"] or "content") != "premium":
        return await _create_refund_request_before_v11333(int(purchase_id), int(user_id), reason)
    reason = str(reason or "").strip()
    if len(reason) < 10:
        raise ValueError("Опишите причину подробнее")
    now_dt = datetime.now(timezone.utc); now = now_dt.isoformat()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("SELECT * FROM purchases WHERE id=? AND user_id=?", (int(purchase_id), int(user_id)))
        row = await cur.fetchone()
        if not row or str(row["status"] or "") != "paid":
            await db.rollback(); raise ValueError("Эта покупка уже не доступна для возврата")
        created = _premium_dt(str(row["created_at"] or "")) or now_dt
        cur = await db.execute("SELECT value FROM settings WHERE key='refund_window_days'"); cfg = await cur.fetchone()
        window_days = max(0, int(cfg["value"] if cfg else 14))
        if window_days and now_dt > created + timedelta(days=window_days):
            await db.rollback(); raise ValueError("Срок подачи запроса на возврат истёк")
        cur = await db.execute("SELECT id FROM refund_requests WHERE purchase_id=? AND status IN ('new','pending','refunded') LIMIT 1", (int(purchase_id),))
        if await cur.fetchone():
            await db.rollback(); raise ValueError("Запрос по этой покупке уже создан")
        cur = await db.execute("INSERT INTO refund_requests(purchase_id,user_id,reason,status,created_at,updated_at) VALUES(?,?,?,'new',?,?)",
                               (int(purchase_id), int(user_id), reason[:1000], now, now))
        await db.commit(); return int(cur.lastrowid)


_finalize_refund_before_v11333 = finalize_refund


async def finalize_refund(refund_id: int, handled_by_user_id: int | None, note: str = "Возврат Stars выполнен") -> bool:
    async with connect() as db:
        cur = await db.execute("SELECT rr.purchase_id,p.purchase_kind,p.telegram_payment_charge_id,p.user_id FROM refund_requests rr JOIN purchases p ON p.id=rr.purchase_id WHERE rr.id=?", (int(refund_id),))
        info = await cur.fetchone()
    ok = await _finalize_refund_before_v11333(int(refund_id), handled_by_user_id, note)
    if not ok or not info:
        return ok
    now = utc_now(); purchase_id = int(info["purchase_id"])
    async with connect() as db:
        await db.execute("UPDATE purchase_access_claims SET status='released',updated_at=? WHERE purchase_id=?", (now, purchase_id))
        if str(info["purchase_kind"] or "") == "premium":
            await db.execute(
                """UPDATE premium_subscriptions SET status='refunded',auto_renew=0,canceled_at=COALESCE(canceled_at,?),updated_at=?
                   WHERE telegram_payment_charge_id=?""", (now, now, str(info["telegram_payment_charge_id"] or ""))
            )
            await db.execute("UPDATE premium_author_pools SET status='refunded',updated_at=? WHERE purchase_id=? AND status='pending'", (now, purchase_id))
            await db.execute("INSERT INTO premium_events(user_id,event_type,plan_code,metadata_json,created_at) VALUES(?,'refund','',?,?)",
                             (int(info["user_id"]), json.dumps({"purchase_id": purchase_id}, ensure_ascii=False), now))
        await db.commit()
    return True


async def list_refund_requests(status: str = "new", limit: int = 30) -> list[aiosqlite.Row]:
    async with connect() as db:
        cur = await db.execute(
            """SELECT rr.*,p.amount_stars,p.telegram_payment_charge_id,p.funding_method,p.wallet_stars_used,p.bonus_points_used,
                      p.purchase_kind,p.graphic_volume_number,p.graphic_chapter_id,p.chapter_package_id,
                      u.telegram_id,u.username,u.full_name,b.title AS book_title,c.title AS chapter_title,ac.title AS audio_title,
                      gc.title AS graphic_chapter_title,gvs.title AS graphic_volume_title,cp.title AS chapter_package_title
               FROM refund_requests rr JOIN purchases p ON p.id=rr.purchase_id JOIN users u ON u.id=rr.user_id
               LEFT JOIN books b ON b.id=p.book_id LEFT JOIN chapters c ON c.id=p.chapter_id
               LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
               LEFT JOIN graphic_volume_settings gvs ON gvs.book_id=p.book_id AND gvs.volume_number=p.graphic_volume_number
               LEFT JOIN chapter_packages cp ON cp.id=p.chapter_package_id
               WHERE rr.status=? ORDER BY rr.id ASC LIMIT ?""", (str(status), max(1,int(limit))))
        return await cur.fetchall()


async def get_refund_request(refund_id: int) -> aiosqlite.Row | None:
    rows = await list_refund_requests("new", 100000)
    for row in rows:
        if int(row["id"]) == int(refund_id):
            return row
    async with connect() as db:
        cur = await db.execute(
            """SELECT rr.*,p.*,p.status AS purchase_status,u.telegram_id,u.username,u.full_name,
                      b.title AS book_title,c.title AS chapter_title,ac.title AS audio_title,
                      gc.title AS graphic_chapter_title,gvs.title AS graphic_volume_title,cp.title AS chapter_package_title
               FROM refund_requests rr JOIN purchases p ON p.id=rr.purchase_id JOIN users u ON u.id=rr.user_id
               LEFT JOIN books b ON b.id=p.book_id LEFT JOIN chapters c ON c.id=p.chapter_id LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
               LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
               LEFT JOIN graphic_volume_settings gvs ON gvs.book_id=p.book_id AND gvs.volume_number=p.graphic_volume_number
               LEFT JOIN chapter_packages cp ON cp.id=p.chapter_package_id WHERE rr.id=?""", (int(refund_id),))
        return await cur.fetchone()


async def get_monetization_integrity_report() -> dict[str, Any]:
    """Owner-safe aggregate audit; contains no payment identifiers or personal data."""
    from app.services.monetization_guard import access_descriptor_for_purchase_row

    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) AS n FROM purchase_access_claims WHERE status='processing' AND expires_at<=?", (utc_now(),))
        stale = int((await cur.fetchone())["n"] or 0)
        cur = await db.execute("SELECT COUNT(*) AS n FROM purchase_access_claims pac LEFT JOIN purchases p ON p.id=pac.purchase_id WHERE pac.status='active' AND (p.id IS NULL OR p.status NOT IN ('paid','canceling'))")
        orphan = int((await cur.fetchone())["n"] or 0)
        cur = await db.execute("SELECT COUNT(*) AS n FROM (SELECT telegram_payment_charge_id FROM purchases WHERE COALESCE(telegram_payment_charge_id,'')!='' GROUP BY telegram_payment_charge_id HAVING COUNT(*)>1)")
        duplicate_charges = int((await cur.fetchone())["n"] or 0)
        cur = await db.execute("SELECT COUNT(*) AS n FROM monetization_incidents WHERE status IN ('open','refund_failed')")
        incidents = int((await cur.fetchone())["n"] or 0)
        cur = await db.execute("SELECT COUNT(*) AS n FROM purchases p LEFT JOIN author_ledger al ON al.purchase_id=p.id WHERE p.status='paid' AND p.purchase_kind IN ('content','graphic_volume','chapter_package') AND p.amount_stars>0 AND p.book_id IS NOT NULL AND al.id IS NULL")
        ledger_missing = int((await cur.fetchone())["n"] or 0)
        cur = await db.execute("SELECT * FROM purchases WHERE status IN ('paid','canceling') ORDER BY id")
        purchase_rows = await cur.fetchall()
        cur = await db.execute("SELECT user_id,access_key,purchase_id,status FROM purchase_access_claims WHERE status='active'")
        claims = {(int(row["user_id"]), str(row["access_key"])): int(row["purchase_id"] or 0) for row in await cur.fetchall()}

    missing_claims = 0
    seen_access: dict[tuple[int, str], int] = {}
    duplicate_access_groups: set[tuple[int, str]] = set()
    for row in purchase_rows:
        descriptor = access_descriptor_for_purchase_row(row)
        if descriptor is None:
            continue
        key = (int(row["user_id"]), descriptor.key)
        if key in seen_access:
            duplicate_access_groups.add(key)
        else:
            seen_access[key] = int(row["id"])
        if key not in claims:
            missing_claims += 1

    critical = (stale, orphan, duplicate_charges, ledger_missing, missing_claims, len(duplicate_access_groups))
    return {
        "ok": not any(critical),
        "stale_processing_claims": stale,
        "orphan_active_claims": orphan,
        "duplicate_charge_groups": duplicate_charges,
        "duplicate_access_groups": len(duplicate_access_groups),
        "paid_access_without_claim": missing_claims,
        "open_incidents": incidents,
        "paid_content_without_ledger": ledger_missing,
    }


async def set_monetization_incident_result(charge_id: str, *, refunded: bool, details: str = "") -> None:
    now = utc_now()
    status = "resolved" if refunded else "refund_failed"
    async with connect() as db:
        await db.execute(
            """UPDATE monetization_incidents SET status=?,details_json=?,updated_at=?
               WHERE charge_id=? AND status='open'""",
            (status, json.dumps({"refund_result": "refunded" if refunded else "failed",
                                 "refund_details": str(details or "")[:1000]}, ensure_ascii=False),
             now, str(charge_id or "")),
        )
        await db.commit()


# v1.13.36 — безопасность, приватность и запросы субъекта данных
async def _ensure_v11336_privacy_schema(db: aiosqlite.Connection) -> None:
    cur = await db.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in await cur.fetchall()}
    if "account_status" not in columns:
        await _execute_schema_ddl(db, "ALTER TABLE users ADD COLUMN account_status TEXT NOT NULL DEFAULT 'active'")
    if "deleted_at" not in columns:
        await _execute_schema_ddl(db, "ALTER TABLE users ADD COLUMN deleted_at TEXT")
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_data_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            request_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'preview',
            confirmation_hash TEXT NOT NULL DEFAULT '',
            requested_at TEXT NOT NULL,
            expires_at TEXT,
            confirmed_at TEXT,
            completed_at TEXT,
            result_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_user_data_requests_user
            ON user_data_requests(user_id, request_type, status, requested_at);
        CREATE INDEX IF NOT EXISTS idx_users_account_status
            ON users(account_status, updated_at);
        """
    )


# v1.13.37 — индексы и настройки для библиотек 1000+ книг
async def _ensure_v11337_performance_schema(db: aiosqlite.Connection) -> None:
    """Add covering indexes for the hottest catalog, queue and moderation paths."""
    await db.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_books_publication_updated
            ON books(publication_status, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_books_publication_id
            ON books(publication_status, id DESC);
        CREATE INDEX IF NOT EXISTS idx_books_author_publication
            ON books(author_id, publication_status, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_books_author_updated
            ON books(author_id, updated_at DESC, id DESC, publication_status);
        CREATE INDEX IF NOT EXISTS idx_books_import_publication
            ON books(import_batch_id, publication_status, id);
        CREATE INDEX IF NOT EXISTS idx_chapters_book_number_id
            ON chapters(book_id, number, id);
        CREATE INDEX IF NOT EXISTS idx_chapters_book_status_number
            ON chapters(book_id, status, number, id);
        CREATE INDEX IF NOT EXISTS idx_audio_chapters_book_number_id
            ON audio_chapters(book_id, number, id);
        CREATE INDEX IF NOT EXISTS idx_audio_chapters_book_status_number
            ON audio_chapters(book_id, status, number, id);
        CREATE INDEX IF NOT EXISTS idx_reviews_book_status
            ON reviews(book_id, status, id);
        CREATE INDEX IF NOT EXISTS idx_purchases_status_book
            ON purchases(status, book_id, id);
        CREATE INDEX IF NOT EXISTS idx_purchases_status_chapter
            ON purchases(status, chapter_id, id);
        CREATE INDEX IF NOT EXISTS idx_purchases_status_audio
            ON purchases(status, audio_chapter_id, id);
        CREATE INDEX IF NOT EXISTS idx_purchases_status_graphic
            ON purchases(status, graphic_chapter_id, id);
        CREATE INDEX IF NOT EXISTS idx_purchases_user_status_created
            ON purchases(user_id, status, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_book_moderation_status_submitted
            ON book_moderation_queue(status, submitted_at, book_id);
        CREATE INDEX IF NOT EXISTS idx_reading_progress_user_updated_book
            ON reading_progress(user_id, updated_at DESC, book_id);
        CREATE INDEX IF NOT EXISTS idx_listening_progress_user_updated_audio
            ON listening_progress(user_id, updated_at DESC, audio_chapter_id);
        """
    )


# v1.14.0.15 — честная ротация продвижения каталога и ручные особые награды
async def _ensure_v114015_catalog_promotion_schema(db: aiosqlite.Connection) -> None:
    now = utc_now()
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog_promotions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_id INTEGER NOT NULL,
            user_id INTEGER,
            purchase_id INTEGER,
            source TEXT NOT NULL DEFAULT 'paid',
            amount_stars INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'invoice',
            duration_hours INTEGER NOT NULL DEFAULT 24,
            starts_at TEXT,
            expires_at TEXT NOT NULL,
            last_shown_at TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL,
            FOREIGN KEY(purchase_id) REFERENCES purchases(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_catalog_promotions_active
            ON catalog_promotions(status, expires_at, last_shown_at, id);
        CREATE INDEX IF NOT EXISTS idx_catalog_promotions_book
            ON catalog_promotions(book_id, status, expires_at, id);
        CREATE INDEX IF NOT EXISTS idx_catalog_promotions_user
            ON catalog_promotions(user_id, status, created_at DESC);
        """
    )
    for key, value in {
        "catalog_promotion_price_stars": "30",
        "catalog_promotion_duration_hours": "24",
        "catalog_promotion_max_active_per_author": "3",
        "catalog_promotion_slots_first_page": "2",
    }.items():
        await db.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING",
            (key, value, now),
        )
    await db.execute(
        "UPDATE catalog_promotions SET status='expired',updated_at=? WHERE status='active' AND expires_at<=?",
        (now, now),
    )
    await db.execute(
        "UPDATE catalog_promotions SET status='expired',updated_at=? WHERE status='invoice' AND expires_at<=?",
        (now, now),
    )


def _manual_achievement_slug(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if clean.startswith("manual_"):
        clean = clean[7:]
    normalized: list[str] = []
    separator = False
    for char in clean:
        if char.isalnum():
            normalized.append(char)
            separator = False
        elif not separator:
            normalized.append("_")
            separator = True
    slug = "".join(normalized).strip("_")[:44]
    return f"manual_{slug or 'special'}"


async def get_manual_achievement_definitions() -> list[dict[str, Any]]:
    raw = await get_setting(_MANUAL_ACHIEVEMENT_CATALOG_SETTING_KEY, "[]")
    try:
        source = json.loads(raw)
    except (TypeError, ValueError):
        source = []
    if not isinstance(source, list):
        source = []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in source:
        if not isinstance(item, dict):
            continue
        code = _manual_achievement_slug(item.get("code") or item.get("title"))
        if code in seen:
            continue
        seen.add(code)
        rarity = str(item.get("rarity") or "epic").strip().lower()
        if rarity not in _ACHIEVEMENT_RARITIES:
            rarity = "epic"
        result.append({
            "code": code,
            "title": str(item.get("title") or "Особая награда").strip()[:80] or "Особая награда",
            "description": str(item.get("description") or "Выдана владельцем VoxLyra.").strip()[:240],
            "icon": "✦",
            "icon_asset": "/media/achievements/owner_special.png",
            "group": str(item.get("group") or "reader").strip().lower() if str(item.get("group") or "reader").strip().lower() in {"reader", "author"} else "reader",
            "category": "manual",
            "rarity": rarity,
            "goal": 1,
            "custom_points": max(0, min(10000, int(item.get("custom_points") or 0))),
            "special": True,
            "manual": True,
            "active": bool(item.get("active", True)),
            "created_at": str(item.get("created_at") or ""),
        })
    return result


async def create_manual_achievement_definition(payload: dict[str, Any]) -> dict[str, Any]:
    title = str((payload or {}).get("title") or "").strip()
    description = str((payload or {}).get("description") or "").strip()
    if len(title) < 3:
        raise ValueError("Название особой награды должно содержать не менее трёх символов.")
    if len(description) < 8:
        raise ValueError("Добавьте понятное описание особой награды.")
    rarity = str((payload or {}).get("rarity") or "epic").strip().lower()
    if rarity not in _ACHIEVEMENT_RARITIES:
        raise ValueError("Неизвестная редкость награды.")
    try:
        custom_points = max(0, min(10000, int((payload or {}).get("custom_points") or 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Очки особой награды должны быть целым числом.") from exc
    group = str((payload or {}).get("group") or "reader").strip().lower()
    if group not in {"reader", "author"}:
        group = "reader"
    definitions = await get_manual_achievement_definitions()
    code = _manual_achievement_slug((payload or {}).get("code") or title)
    if code in _ACHIEVEMENT_CATALOG or code in _RARE_ACHIEVEMENT_CATALOG or any(item["code"] == code for item in definitions):
        raise ValueError("Награда с таким кодом уже существует. Измените название или код.")
    item = {
        "code": code,
        "title": title[:80],
        "description": description[:240],
        "rarity": rarity,
        "custom_points": custom_points,
        "group": group,
        "active": True,
        "created_at": utc_now(),
    }
    stored = [{key: value for key, value in definition.items() if key in {"code", "title", "description", "rarity", "custom_points", "group", "active", "created_at"}} for definition in definitions]
    stored.append(item)
    await set_setting(_MANUAL_ACHIEVEMENT_CATALOG_SETTING_KEY, json.dumps(stored, ensure_ascii=False, separators=(",", ":")))
    return next(definition for definition in await get_manual_achievement_definitions() if definition["code"] == code)


async def set_manual_achievement_active(code: str, active: bool) -> bool:
    clean = _manual_achievement_slug(code)
    definitions = await get_manual_achievement_definitions()
    changed = False
    stored: list[dict[str, Any]] = []
    for item in definitions:
        if item["code"] == clean:
            item["active"] = bool(active)
            changed = True
        stored.append({key: value for key, value in item.items() if key in {"code", "title", "description", "rarity", "custom_points", "group", "active", "created_at"}})
    if changed:
        await set_setting(_MANUAL_ACHIEVEMENT_CATALOG_SETTING_KEY, json.dumps(stored, ensure_ascii=False, separators=(",", ":")))
    return changed


async def grant_manual_achievement(user_id: int, code: str, reason: str = "") -> dict[str, Any]:
    clean = _manual_achievement_slug(code)
    definition = next((item for item in await get_manual_achievement_definitions() if item["code"] == clean and item.get("active", True)), None)
    if not definition:
        raise ValueError("Особая награда не найдена или отключена.")
    uid = int(user_id)
    now = utc_now()
    metadata = {
        "achievement": definition,
        "manual": True,
        "reason": str(reason or "").strip()[:300],
        "awarded_program_version": 3,
    }
    async with connect() as db:
        cur = await db.execute("SELECT id FROM users WHERE id=? AND account_status!='deleted'", (uid,))
        if not await cur.fetchone():
            raise ValueError("Пользователь не найден.")
        try:
            cur = await db.execute(
                "INSERT INTO user_achievements(user_id,achievement_code,progress_value,metadata_json,awarded_at) VALUES(?,?,?,?,?)",
                (uid, clean, 1, json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Эта награда уже выдана пользователю.") from exc
        await db.commit()
        return {"id": int(cur.lastrowid), "user_id": uid, "code": clean, "awarded_at": now, "achievement": definition}


async def revoke_manual_achievement(user_id: int, code: str) -> bool:
    uid = int(user_id)
    clean = _manual_achievement_slug(code)
    async with connect() as db:
        cur = await db.execute(
            "SELECT metadata_json FROM user_achievements WHERE user_id=? AND achievement_code=?",
            (uid, clean),
        )
        row = await cur.fetchone()
        if not row:
            return False
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError):
            metadata = {}
        if not bool(metadata.get("manual")):
            raise ValueError("Автоматические награды нельзя отзывать вручную.")
        await db.execute("DELETE FROM achievement_showcase WHERE user_id=? AND achievement_code=?", (uid, clean))
        cur = await db.execute("DELETE FROM user_achievements WHERE user_id=? AND achievement_code=?", (uid, clean))
        await db.commit()
        return cur.rowcount > 0


async def get_manual_achievement_admin_summary(limit: int = 30) -> dict[str, Any]:
    definitions = await get_manual_achievement_definitions()
    async with connect() as db:
        cur = await db.execute(
            "SELECT achievement_code,COUNT(*) AS awarded FROM user_achievements WHERE achievement_code LIKE 'manual_%' GROUP BY achievement_code"
        )
        counts = {str(row["achievement_code"]): int(row["awarded"] or 0) for row in await cur.fetchall()}
        cur = await db.execute(
            """
            SELECT l.*,u.telegram_id,u.username,u.full_name
            FROM audit_logs l LEFT JOIN users u ON u.id=l.actor_user_id
            WHERE l.action IN ('manual_achievement_granted','manual_achievement_revoked','manual_achievement_created')
            ORDER BY l.id DESC LIMIT ?
            """,
            (max(1, min(100, int(limit))),),
        )
        events = [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]
    return {
        "definitions": [{**item, "awarded": counts.get(str(item["code"]), 0)} for item in definitions],
        "events": events,
    }


async def get_catalog_promotion_settings() -> dict[str, int]:
    return {
        "price_stars": max(1, min(10000, int(await get_setting("catalog_promotion_price_stars", "30") or 30))),
        "duration_hours": max(1, min(720, int(await get_setting("catalog_promotion_duration_hours", "24") or 24))),
        "max_active_per_author": max(1, min(20, int(await get_setting("catalog_promotion_max_active_per_author", "3") or 3))),
        "slots_first_page": max(1, min(6, int(await get_setting("catalog_promotion_slots_first_page", "2") or 2))),
    }


async def set_catalog_promotion_settings(payload: dict[str, Any]) -> dict[str, int]:
    current = await get_catalog_promotion_settings()
    fields = {
        "price_stars": ("catalog_promotion_price_stars", 1, 10000),
        "duration_hours": ("catalog_promotion_duration_hours", 1, 720),
        "max_active_per_author": ("catalog_promotion_max_active_per_author", 1, 20),
        "slots_first_page": ("catalog_promotion_slots_first_page", 1, 6),
    }
    for public_key, (setting_key, minimum, maximum) in fields.items():
        if public_key not in (payload or {}):
            continue
        try:
            value = max(minimum, min(maximum, int((payload or {}).get(public_key))))
        except (TypeError, ValueError) as exc:
            raise ValueError("Настройки продвижения должны быть целыми числами.") from exc
        await set_setting(setting_key, str(value))
        current[public_key] = value
    return await get_catalog_promotion_settings()


async def _expire_catalog_promotions(db: aiosqlite.Connection) -> None:
    now = utc_now()
    await db.execute("UPDATE catalog_promotions SET status='expired',updated_at=? WHERE status IN ('active','invoice') AND expires_at<=?", (now, now))


async def get_catalog_promotion_availability(book_id: int, user_id: int, *, owner: bool = False) -> dict[str, Any]:
    bid, uid = int(book_id), int(user_id)
    settings_value = await get_catalog_promotion_settings()
    async with connect() as db:
        await _expire_catalog_promotions(db)
        cur = await db.execute(
            """
            SELECT b.id,b.title,b.publication_status,b.author_id,ap.user_id AS author_user_id
            FROM books b LEFT JOIN author_profiles ap ON ap.id=b.author_id WHERE b.id=?
            """,
            (bid,),
        )
        book = await cur.fetchone()
        if not book or str(book["publication_status"]) != "published":
            return {"allowed": False, "reason": "not_published"}
        if not owner and int(book["author_user_id"] or 0) != uid:
            return {"allowed": False, "reason": "not_owner"}
        cur = await db.execute(
            "SELECT * FROM catalog_promotions WHERE book_id=? AND status IN ('active','invoice') AND expires_at>? ORDER BY id DESC LIMIT 1",
            (bid, utc_now()),
        )
        active = await cur.fetchone()
        if active:
            return {"allowed": False, "reason": "already_active", "promotion": {key: active[key] for key in active.keys()}}
        if not owner and book["author_id"] is not None:
            cur = await db.execute(
                """
                SELECT COUNT(*) AS cnt FROM catalog_promotions cp
                JOIN books b ON b.id=cp.book_id
                WHERE b.author_id=? AND cp.status='active' AND cp.expires_at>?
                """,
                (int(book["author_id"]), utc_now()),
            )
            if int((await cur.fetchone())["cnt"] or 0) >= int(settings_value["max_active_per_author"]):
                return {"allowed": False, "reason": "author_limit"}
        await db.commit()
    return {"allowed": True, "reason": "ok", "book_id": bid, "title": str(book["title"]), **settings_value}


async def reserve_catalog_promotion(book_id: int, user_id: int, amount_stars: int) -> int:
    availability = await get_catalog_promotion_availability(book_id, user_id, owner=False)
    if not availability.get("allowed"):
        reasons = {
            "not_published": "Продвигать можно только опубликованную книгу.",
            "not_owner": "Платно продвигать можно только собственные книги.",
            "already_active": "Книга уже участвует в ротации каталога.",
            "author_limit": "Достигнут лимит одновременно продвигаемых книг автора.",
        }
        raise ValueError(reasons.get(str(availability.get("reason")), "Продвижение сейчас недоступно."))
    now_dt = datetime.now(timezone.utc)
    invoice_expires = (now_dt + timedelta(minutes=30)).isoformat()
    settings_value = await get_catalog_promotion_settings()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO catalog_promotions(book_id,user_id,source,amount_stars,status,duration_hours,expires_at,created_at,updated_at)
            VALUES(?,?,'paid',?,'invoice',?,?,?,?)
            """,
            (int(book_id), int(user_id), int(amount_stars), int(settings_value["duration_hours"]), invoice_expires, now_dt.isoformat(), now_dt.isoformat()),
        )
        await db.commit()
        return int(cur.lastrowid)


async def create_owner_catalog_promotion(book_id: int, user_id: int, duration_hours: int | None = None) -> dict[str, Any]:
    availability = await get_catalog_promotion_availability(book_id, user_id, owner=True)
    if not availability.get("allowed"):
        if availability.get("reason") == "already_active":
            raise ValueError("Книга уже участвует в ротации каталога.")
        raise ValueError("Продвигать можно только опубликованные книги.")
    settings_value = await get_catalog_promotion_settings()
    duration = int(duration_hours or settings_value["duration_hours"])
    duration = max(1, min(720, duration))
    now_dt = datetime.now(timezone.utc)
    expires = (now_dt + timedelta(hours=duration)).isoformat()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO catalog_promotions(book_id,user_id,source,amount_stars,status,duration_hours,starts_at,expires_at,created_at,updated_at)
            VALUES(?,?,'owner',0,'active',?,?,?,?,?)
            """,
            (int(book_id), int(user_id), duration, now_dt.isoformat(), expires, now_dt.isoformat(), now_dt.isoformat()),
        )
        await db.commit()
        promotion_id = int(cur.lastrowid)
    return dict(await get_catalog_promotion(promotion_id) or {})


async def get_catalog_promotion(promotion_id: int) -> aiosqlite.Row | None:
    async with connect() as db:
        await _expire_catalog_promotions(db)
        cur = await db.execute(
            """
            SELECT cp.*,b.title AS book_title,b.publication_status,b.author_id,ap.user_id AS author_user_id,
                   COALESCE(ap.pen_name,b.source_author_name) AS pen_name
            FROM catalog_promotions cp
            JOIN books b ON b.id=cp.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE cp.id=?
            """,
            (int(promotion_id),),
        )
        row = await cur.fetchone()
        await db.commit()
        return row


async def activate_catalog_promotion(promotion_id: int, purchase_id: int | None = None) -> bool:
    now_dt = datetime.now(timezone.utc)
    async with connect() as db:
        cur = await db.execute("SELECT duration_hours,status,expires_at FROM catalog_promotions WHERE id=?", (int(promotion_id),))
        row = await cur.fetchone()
        if not row or str(row["status"]) not in {"invoice", "active"}:
            return False
        expires = (now_dt + timedelta(hours=max(1, int(row["duration_hours"] or 24)))).isoformat()
        cur = await db.execute(
            """
            UPDATE catalog_promotions SET purchase_id=COALESCE(?,purchase_id),status='active',starts_at=COALESCE(starts_at,?),
                   expires_at=?,updated_at=? WHERE id=? AND status IN ('invoice','active')
            """,
            (int(purchase_id) if purchase_id else None, now_dt.isoformat(), expires, now_dt.isoformat(), int(promotion_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def cancel_catalog_promotion(promotion_id: int) -> bool:
    async with connect() as db:
        cur = await db.execute(
            "UPDATE catalog_promotions SET status='canceled',updated_at=? WHERE id=? AND status IN ('active','invoice')",
            (utc_now(), int(promotion_id)),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_catalog_promotions(*, limit: int = 50, active_only: bool = False) -> list[dict[str, Any]]:
    async with connect() as db:
        await _expire_catalog_promotions(db)
        where = "WHERE cp.status='active' AND cp.expires_at>?" if active_only else ""
        params: tuple[Any, ...] = (utc_now(), max(1, min(500, int(limit)))) if active_only else (max(1, min(500, int(limit))),)
        cur = await db.execute(
            f"""
            SELECT cp.*,b.title AS book_title,b.cover_path,b.content_type,b.publication_status,
                   COALESCE(ap.pen_name,b.source_author_name) AS pen_name,u.telegram_id,u.username,u.full_name
            FROM catalog_promotions cp
            JOIN books b ON b.id=cp.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            LEFT JOIN users u ON u.id=cp.user_id
            {where}
            ORDER BY CASE cp.status WHEN 'active' THEN 0 WHEN 'invoice' THEN 1 ELSE 2 END,
                     cp.expires_at DESC,cp.id DESC LIMIT ?
            """,
            params,
        )
        rows = [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]
        await db.commit()
        return rows


async def search_catalog_promotion_books(query: str = "", *, user_id: int | None = None, owner: bool = False, limit: int = 40) -> list[dict[str, Any]]:
    clean = str(query or "").strip().lower()
    clauses = ["b.publication_status='published'"]
    params: list[Any] = []
    if not owner:
        clauses.append("ap.user_id=?")
        params.append(int(user_id or 0))
    if clean:
        if clean.isdigit():
            clauses.append("(b.id=? OR lower(b.title) LIKE ? OR lower(COALESCE(ap.pen_name,b.source_author_name,'')) LIKE ?)")
            params.extend([int(clean), f"%{clean}%", f"%{clean}%"])
        else:
            clauses.append("(lower(b.title) LIKE ? OR lower(COALESCE(ap.pen_name,b.source_author_name,'')) LIKE ?)")
            params.extend([f"%{clean}%", f"%{clean}%"])
    params.append(max(1, min(100, int(limit))))
    async with connect() as db:
        await _expire_catalog_promotions(db)
        cur = await db.execute(
            f"""
            SELECT b.id,b.title,b.cover_path,b.content_type,b.publication_status,b.author_id,
                   COALESCE(ap.pen_name,b.source_author_name) AS pen_name,
                   EXISTS(SELECT 1 FROM catalog_promotions cp WHERE cp.book_id=b.id AND cp.status='active' AND cp.expires_at>?) AS promoted
            FROM books b LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE {' AND '.join(clauses)} ORDER BY b.updated_at DESC,b.id DESC LIMIT ?
            """,
            (utc_now(), *params),
        )
        rows = [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]
        await db.commit()
        return rows


async def get_active_catalog_promotion_for_book(book_id: int) -> dict[str, Any] | None:
    async with connect() as db:
        await _expire_catalog_promotions(db)
        cur = await db.execute(
            "SELECT * FROM catalog_promotions WHERE book_id=? AND status='active' AND expires_at>? ORDER BY last_shown_at IS NOT NULL,last_shown_at,id LIMIT 1",
            (int(book_id), utc_now()),
        )
        row = await cur.fetchone()
        await db.commit()
        return {key: row[key] for key in row.keys()} if row else None


async def record_catalog_promotion_click(book_id: int) -> None:
    async with connect() as db:
        await db.execute(
            "UPDATE catalog_promotions SET clicks=clicks+1,updated_at=? WHERE book_id=? AND status='active' AND expires_at>?",
            (utc_now(), int(book_id), utc_now()),
        )
        await db.commit()


async def _catalog_rows_unrotated(limit: int = 300, include_drafts: bool = False) -> list[dict[str, Any]]:
    status_filter = "b.publication_status != 'deleted'" if include_drafts else "b.publication_status = 'published'"
    chapter_status = "c.status != 'deleted'" if include_drafts else "c.status = 'published'"
    graphic_status = "gc.status != 'deleted'" if include_drafts else "gc.status = 'published'"
    audio_status = "ac.status != 'deleted'" if include_drafts else "ac.status = 'published'"
    async with connect() as db:
        await _expire_catalog_promotions(db)
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
                   (SELECT c.id FROM chapters c WHERE c.book_id=b.id AND {chapter_status} ORDER BY c.number,c.id LIMIT 1) AS first_chapter_id,
                   (SELECT gc.id FROM graphic_chapters gc WHERE gc.book_id=b.id AND {graphic_status} ORDER BY gc.number,gc.id LIMIT 1) AS first_graphic_chapter_id,
                   (SELECT ac.id FROM audio_chapters ac WHERE ac.book_id=b.id AND {audio_status} ORDER BY ac.number,ac.id LIMIT 1) AS first_audio_id,
                   (SELECT GROUP_CONCAT(v.option_label,'||') FROM book_option_values v WHERE v.book_id=b.id AND v.option_group='genres') AS genre_labels,
                   (SELECT COUNT(*) FROM bookmarks bm WHERE bm.book_id=b.id) AS bookmark_count,
                   (SELECT COUNT(DISTINCT rp.user_id) FROM reading_progress rp WHERE rp.book_id=b.id) AS reader_count,
                   (SELECT COUNT(*) FROM purchases p WHERE p.status='paid' AND (
                       p.book_id=b.id OR p.chapter_id IN (SELECT c3.id FROM chapters c3 WHERE c3.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT ac3.id FROM audio_chapters ac3 WHERE ac3.book_id=b.id) OR
                       p.graphic_chapter_id IN (SELECT gc3.id FROM graphic_chapters gc3 WHERE gc3.book_id=b.id)
                   )) AS purchase_count,
                   cp.id AS catalog_promotion_id,cp.source AS catalog_promotion_source,
                   cp.expires_at AS catalog_promotion_expires_at,cp.last_shown_at AS catalog_promotion_last_shown,
                   cp.impressions AS catalog_promotion_impressions
            FROM books b
            LEFT JOIN author_profiles a ON a.id=b.author_id
            LEFT JOIN catalog_promotions cp ON cp.id=(
                SELECT cp2.id FROM catalog_promotions cp2
                WHERE cp2.book_id=b.id AND cp2.status='active' AND cp2.expires_at>?
                ORDER BY cp2.last_shown_at IS NOT NULL,cp2.last_shown_at,cp2.id LIMIT 1
            )
            WHERE {status_filter}
            ORDER BY b.updated_at DESC,b.id DESC LIMIT ?
            """,
            (utc_now(), max(1, min(1000, int(limit)))),
        )
        rows = [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]
        await db.commit()
        return rows


def _catalog_organic_score(row: dict[str, Any]) -> float:
    rating = float(row.get("rating") or 0)
    reviews = int(row.get("reviews_count") or 0)
    purchases = int(row.get("purchase_count") or 0)
    readers = int(row.get("reader_count") or 0)
    bookmarks = int(row.get("bookmark_count") or 0)
    try:
        updated = datetime.fromisoformat(str(row.get("updated_at") or "").replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - updated).total_seconds() / 86400)
    except Exception:
        age_days = 365.0
    freshness = max(0.0, 40.0 - min(age_days, 40.0))
    return purchases * 24.0 + readers * 9.0 + bookmarks * 6.0 + reviews * 7.0 + rating * min(reviews, 30) * 1.4 + freshness


_previous_list_catalog_books_v114015 = list_catalog_books


async def list_catalog_books(limit: int = 50, include_drafts: bool = False) -> list[dict[str, Any]]:
    """Catalog with a limited fair promotion rotation, never permanent pinning.

    At most a small configured number of active promotions are inserted into the
    first page. All remaining positions are filled by the ordinary organic
    score, so paid or owner promotion cannot occupy the whole catalog.
    """
    requested = max(1, min(500, int(limit)))
    if include_drafts:
        return await _catalog_rows_unrotated(requested, include_drafts=True)
    rows = await _catalog_rows_unrotated(max(requested * 3, 180), include_drafts=False)
    organic = sorted(rows, key=lambda item: (_catalog_organic_score(item), int(item.get("id") or 0)), reverse=True)
    promoted = [item for item in organic if int(item.get("catalog_promotion_id") or 0) > 0]
    promoted.sort(key=lambda item: (
        1 if item.get("catalog_promotion_last_shown") else 0,
        str(item.get("catalog_promotion_last_shown") or ""),
        int(item.get("catalog_promotion_impressions") or 0),
        -_catalog_organic_score(item),
    ))
    settings_value = await get_catalog_promotion_settings()
    max_promoted_without_blocking_organic = max(0, min(requested - 1, max(1, requested // 5)))
    slot_count = min(int(settings_value["slots_first_page"]), len(promoted), max_promoted_without_blocking_organic)
    selected = promoted[:slot_count]
    selected_ids = {int(item["id"]) for item in selected}
    base = [item for item in organic if int(item["id"]) not in selected_ids]
    positions = [1, 5, 9, 13, 17, 21]
    result = list(base[:requested])
    for index, item in enumerate(selected):
        item = dict(item)
        item["catalog_promoted"] = True
        position = min(positions[index] if index < len(positions) else len(result), len(result))
        result.insert(position, item)
    result = result[:requested]
    shown_promotion_ids = [int(item.get("catalog_promotion_id") or 0) for item in result if int(item.get("catalog_promotion_id") or 0) > 0 and item.get("catalog_promoted")]
    if shown_promotion_ids:
        now = utc_now()
        async with connect() as db:
            placeholders = ",".join("?" for _ in shown_promotion_ids)
            await db.execute(
                f"UPDATE catalog_promotions SET impressions=impressions+1,last_shown_at=?,updated_at=? WHERE id IN ({placeholders})",
                (now, now, *shown_promotion_ids),
            )
            await db.commit()
    for item in result:
        item.setdefault("catalog_promoted", False)
    return result


_previous_get_purchase_target_v114015 = get_purchase_target


async def get_purchase_target(payload: str) -> dict[str, Any] | None:
    value = str(payload or "")
    canonical = value
    if value.startswith("vox:intent:"):
        intent = await get_payment_intent(value)
        if not intent or str(intent["status"] or "") not in {"active", "paid"}:
            return None
        canonical = str(intent["canonical_payload"] or "")
    parts = canonical.split(":")
    if len(parts) == 3 and parts[0] == "vox" and parts[1] == "catalog_promo" and parts[2].isdigit():
        promotion = await get_catalog_promotion(int(parts[2]))
        if not promotion or str(promotion["status"]) not in {"invoice", "active"}:
            return None
        if str(promotion["publication_status"]) != "published":
            return None
        return {
            "kind": "catalog_promo",
            "target_id": int(promotion["id"]),
            "promotion_id": int(promotion["id"]),
            "book_id": int(promotion["book_id"]),
            "title": f"Топ каталога: {promotion['book_title']}",
            "description": f"Продвижение книги «{promotion['book_title']}» в честной ротации каталога на {int(promotion['duration_hours'] or 24)} ч.",
            "book_title": str(promotion["book_title"]),
            "amount_stars": int(promotion["amount_stars"] or 0),
            "author_id": None,
            "promo_code": None,
            "discount_percent": 0,
        }
    return await _previous_get_purchase_target_v114015(value)


_previous_create_paid_purchase_v114015 = create_paid_purchase


async def create_paid_purchase(*, user_id: int, payload: str, amount_stars: int, telegram_payment_charge_id: str) -> int:
    purchase_id = await _previous_create_paid_purchase_v114015(
        user_id=int(user_id), payload=str(payload), amount_stars=int(amount_stars),
        telegram_payment_charge_id=str(telegram_payment_charge_id),
    )
    target = await get_purchase_target(str(payload))
    if target and str(target.get("kind") or "") == "catalog_promo":
        await activate_catalog_promotion(int(target["promotion_id"]), int(purchase_id))
        async with connect() as db:
            await db.execute("UPDATE purchases SET purchase_kind='catalog_promotion' WHERE id=?", (int(purchase_id),))
            await db.commit()
    return int(purchase_id)


# v1.14.0.16 — five reward levels and first expansion toward 100

# v1.14.0.23 — unified, full-database book search

def _book_word_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if min(len(left), len(right)) >= 3 and (left.startswith(right) or right.startswith(left)):
        return 0.95
    if min(len(left), len(right)) >= 3 and (left in right or right in left):
        return 0.88
    return SequenceMatcher(None, left, right).ratio()


def _book_search_score(row: dict[str, Any], query: str) -> float:
    normalized = normalize_book_search_text(query)
    if not normalized:
        return 1.0
    book_id = int(row.get("id") or 0)
    if normalized.isdigit() and int(normalized) == book_id:
        return 1_000_000.0

    title = normalize_book_search_text(row.get("title"))
    pen_name = normalize_book_search_text(row.get("pen_name"))
    source_author = normalize_book_search_text(row.get("source_author_name"))
    genres = normalize_book_search_text(row.get("genre_labels"))
    description = normalize_book_search_text(row.get("description"))
    source_name = normalize_book_search_text(row.get("source_name"))
    source_file_name = normalize_book_search_text(re.sub(r"\.[a-z0-9]{1,8}$", "", str(row.get("source_file_name") or ""), flags=re.IGNORECASE))
    authors = " ".join(part for part in (pen_name, source_author) if part)
    aliases = " ".join(part for part in (source_name, source_file_name) if part)
    combined = " ".join(part for part in (title, aliases, authors, genres, description) if part)

    if title == normalized:
        return 950_000.0
    if title.startswith(normalized):
        return 900_000.0 - min(len(title), 10_000)
    if normalized in title:
        return 850_000.0 - title.index(normalized)
    if aliases == normalized or any(alias == normalized for alias in (source_name, source_file_name) if alias):
        return 825_000.0
    if normalized in aliases:
        return 810_000.0 - aliases.index(normalized)
    if authors == normalized:
        return 800_000.0
    if any(author == normalized for author in (pen_name, source_author) if author):
        return 790_000.0
    if normalized in authors:
        return 760_000.0 - authors.index(normalized)
    if normalized in genres:
        return 710_000.0
    if normalized in combined:
        return 680_000.0

    query_words = normalized.split()
    if not query_words:
        return 0.0
    title_words = title.split()
    author_words = authors.split()
    genre_words = genres.split()
    combined_words = combined.split()

    # Word order does not matter.  Every requested word must either be present or
    # be a close spelling match. Short words are intentionally stricter to avoid
    # flooding results with unrelated books.
    def match_words(words: list[str], *, long_threshold: float, short_threshold: float) -> tuple[int, float]:
        matched = 0
        total = 0.0
        for query_word in query_words:
            best = max((_book_word_similarity(query_word, word) for word in words), default=0.0)
            threshold = short_threshold if len(query_word) <= 3 else long_threshold
            if best >= threshold:
                matched += 1
            total += best
        return matched, total / max(1, len(query_words))

    title_matched, title_similarity = match_words(title_words, long_threshold=0.70, short_threshold=0.92)
    if title_matched == len(query_words):
        return 620_000.0 + title_similarity * 10_000.0

    author_matched, author_similarity = match_words(author_words, long_threshold=0.74, short_threshold=0.94)
    if author_matched == len(query_words):
        return 570_000.0 + author_similarity * 10_000.0

    genre_matched, genre_similarity = match_words(genre_words, long_threshold=0.78, short_threshold=0.95)
    if genre_matched == len(query_words):
        return 520_000.0 + genre_similarity * 8_000.0

    combined_matched, combined_similarity = match_words(combined_words, long_threshold=0.76, short_threshold=0.95)
    if combined_matched == len(query_words):
        return 470_000.0 + combined_similarity * 8_000.0

    # Whole-phrase typo tolerance is useful for one long title fragment but is
    # disabled for very short queries where fuzzy matching would be noisy.
    if len(normalized) >= 5:
        title_ratio = SequenceMatcher(None, normalized, title).ratio() if title else 0.0
        author_ratio = SequenceMatcher(None, normalized, authors).ratio() if authors else 0.0
        if title_ratio >= 0.62:
            return 390_000.0 + title_ratio * 10_000.0
        if author_ratio >= 0.68:
            return 350_000.0 + author_ratio * 8_000.0
    return 0.0


async def _book_search_index(
    *,
    status_scope: str = "published",
    author_user_id: int | None = None,
    filter_code: str = "all",
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status_scope == "published":
        clauses.append("b.publication_status='published'")
    elif status_scope == "grantable":
        clauses.append("b.publication_status!='deleted'")
    else:
        clauses.append("b.publication_status!='deleted'")
    if author_user_id is not None:
        clauses.append("ap.user_id=?")
        params.append(int(author_user_id))

    clean_filter = str(filter_code or "all").strip().lower()
    if clean_filter == "graphic":
        clauses.append("b.content_type!='book'")
    elif clean_filter in {"comic", "manga", "manhwa", "webtoon", "graphic_novel"}:
        clauses.append("b.content_type=?")
        params.append(clean_filter)
    elif clean_filter == "audio":
        clauses.append("EXISTS(SELECT 1 FROM audio_chapters acf WHERE acf.book_id=b.id AND acf.status='published')")
    elif clean_filter == "free":
        clauses.append("((COALESCE(b.pricing_type,'')!='premium' AND COALESCE(b.price_stars,0)<=0) OR EXISTS(SELECT 1 FROM chapters cf WHERE cf.book_id=b.id AND cf.status='published' AND cf.is_free=1) OR EXISTS(SELECT 1 FROM graphic_chapters gcf WHERE gcf.book_id=b.id AND gcf.status='published' AND gcf.is_free=1))")
    elif clean_filter == "popular":
        clauses.append("(EXISTS(SELECT 1 FROM purchases pf WHERE pf.status='paid' AND (pf.book_id=b.id OR pf.chapter_id IN (SELECT id FROM chapters WHERE book_id=b.id) OR pf.audio_chapter_id IN (SELECT id FROM audio_chapters WHERE book_id=b.id) OR pf.graphic_chapter_id IN (SELECT id FROM graphic_chapters WHERE book_id=b.id))) OR EXISTS(SELECT 1 FROM reviews rf WHERE rf.book_id=b.id AND rf.status='published'))")

    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.id,b.title,b.description,b.content_type,b.publication_status,b.updated_at,b.created_at,
                   b.price_stars,b.pricing_type,b.cover_path,b.source_author_name,b.source_file_name,b.source_name,b.author_id,
                   COALESCE(ap.pen_name,b.source_author_name,'') AS pen_name,
                   COALESCE((SELECT GROUP_CONCAT(v.option_label,'||') FROM book_option_values v WHERE v.book_id=b.id AND v.option_group IN ('genres','tags')), '') AS genre_labels
            FROM books b
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE {' AND '.join(clauses)}
            """,
            tuple(params),
        )
        return [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]


def _rank_book_index(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    normalized = normalize_book_search_text(query)
    ranked: list[dict[str, Any]] = []
    if not normalized:
        return sorted(
            rows,
            key=lambda row: (str(row.get("updated_at") or row.get("created_at") or ""), int(row.get("id") or 0)),
            reverse=True,
        )
    for row in rows:
        score = _book_search_score(row, normalized)
        if score <= 0:
            continue
        item = dict(row)
        item["search_score"] = score
        ranked.append(item)
    ranked.sort(
        key=lambda row: (
            float(row.get("search_score") or 0),
            str(row.get("updated_at") or row.get("created_at") or ""),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )
    return ranked


async def _catalog_rows_for_ids(book_ids: list[int]) -> list[dict[str, Any]]:
    ids = [int(value) for value in book_ids if int(value) > 0]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.*, COALESCE(a.pen_name,b.source_author_name) AS pen_name,
                   COALESCE((SELECT AVG(r.rating) FROM reviews r WHERE r.book_id=b.id AND r.status='published'),0) AS rating,
                   (SELECT COUNT(*) FROM reviews r WHERE r.book_id=b.id AND r.status='published') AS reviews_count,
                   (SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') AS text_chapters_count,
                   (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published') AS graphic_chapters_count,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published') +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published')) AS chapters_count,
                   (SELECT COALESCE(SUM(gc.pages_count),0) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published') AS graphic_pages_count,
                   (SELECT COUNT(*) FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status='published') AS audio_count,
                   ((SELECT COUNT(*) FROM chapters c WHERE c.book_id=b.id AND c.status='published' AND c.is_free=1) +
                    (SELECT COUNT(*) FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published' AND gc.is_free=1)) AS free_chapters_count,
                   (SELECT c.id FROM chapters c WHERE c.book_id=b.id AND c.status='published' ORDER BY c.number,c.id LIMIT 1) AS first_chapter_id,
                   (SELECT gc.id FROM graphic_chapters gc WHERE gc.book_id=b.id AND gc.status='published' ORDER BY gc.number,gc.id LIMIT 1) AS first_graphic_chapter_id,
                   (SELECT ac.id FROM audio_chapters ac WHERE ac.book_id=b.id AND ac.status='published' ORDER BY ac.number,ac.id LIMIT 1) AS first_audio_id,
                   (SELECT GROUP_CONCAT(v.option_label,'||') FROM book_option_values v WHERE v.book_id=b.id AND v.option_group='genres') AS genre_labels,
                   (SELECT COUNT(*) FROM bookmarks bm WHERE bm.book_id=b.id) AS bookmark_count,
                   (SELECT COUNT(DISTINCT rp.user_id) FROM reading_progress rp WHERE rp.book_id=b.id) AS reader_count,
                   (SELECT COUNT(*) FROM purchases p WHERE p.status='paid' AND (
                       p.book_id=b.id OR p.chapter_id IN (SELECT c3.id FROM chapters c3 WHERE c3.book_id=b.id) OR
                       p.audio_chapter_id IN (SELECT ac3.id FROM audio_chapters ac3 WHERE ac3.book_id=b.id) OR
                       p.graphic_chapter_id IN (SELECT gc3.id FROM graphic_chapters gc3 WHERE gc3.book_id=b.id)
                   )) AS purchase_count
            FROM books b
            LEFT JOIN author_profiles a ON a.id=b.author_id
            WHERE b.id IN ({placeholders}) AND b.publication_status='published'
            """,
            tuple(ids),
        )
        rows = [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]
    order = {book_id: index for index, book_id in enumerate(ids)}
    rows.sort(key=lambda row: order.get(int(row.get("id") or 0), len(order)))
    for row in rows:
        row.setdefault("catalog_promoted", False)
    return rows



async def _direct_catalog_title_ids(query: str, *, filter_code: str = "all", limit: int = 100) -> list[int]:
    """Return exact/prefix title matches directly from SQLite.

    This is deliberately independent from the in-memory fuzzy ranker. It protects
    exact Cyrillic titles from stale indexes, invisible punctuation, imported
    filenames and future ranker regressions.
    """
    normalized = normalize_book_search_text(query)
    if not normalized:
        return []
    clauses = ["b.publication_status='published'"]
    params: list[Any] = []
    clean_filter = str(filter_code or "all").strip().lower()
    if clean_filter == "graphic":
        clauses.append("b.content_type!='book'")
    elif clean_filter in {"comic", "manga", "manhwa", "webtoon", "graphic_novel"}:
        clauses.append("b.content_type=?")
        params.append(clean_filter)
    elif clean_filter == "audio":
        clauses.append("EXISTS(SELECT 1 FROM audio_chapters acf WHERE acf.book_id=b.id AND acf.status='published')")
    elif clean_filter == "free":
        clauses.append("((COALESCE(b.pricing_type,'')!='premium' AND COALESCE(b.price_stars,0)<=0) OR EXISTS(SELECT 1 FROM chapters cf WHERE cf.book_id=b.id AND cf.status='published' AND cf.is_free=1) OR EXISTS(SELECT 1 FROM graphic_chapters gcf WHERE gcf.book_id=b.id AND gcf.status='published' AND gcf.is_free=1))")
    elif clean_filter == "popular":
        clauses.append("(EXISTS(SELECT 1 FROM purchases pf WHERE pf.status='paid' AND (pf.book_id=b.id OR pf.chapter_id IN (SELECT id FROM chapters WHERE book_id=b.id) OR pf.audio_chapter_id IN (SELECT id FROM audio_chapters WHERE book_id=b.id) OR pf.graphic_chapter_id IN (SELECT id FROM graphic_chapters WHERE book_id=b.id))) OR EXISTS(SELECT 1 FROM reviews rf WHERE rf.book_id=b.id AND rf.status='published'))")

    # Search title first, then original import label / filename. The CASE order
    # guarantees that a literal title is never displaced by a fuzzy result.
    params.extend([normalized, normalized, normalized, normalized, f"{normalized}%", f"%{normalized}%", max(1, min(250, int(limit)))])
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.id
            FROM books b
            WHERE {' AND '.join(clauses)}
              AND (
                    book_search_normalize(b.title)=?
                 OR book_search_normalize(COALESCE(b.source_name,''))=?
                 OR book_search_normalize(COALESCE(b.source_file_name,''))=?
                 OR CAST(b.id AS TEXT)=?
                 OR book_search_normalize(b.title) LIKE ?
                 OR book_search_normalize(b.title) LIKE ?
              )
            ORDER BY CASE
                WHEN book_search_normalize(b.title)=? THEN 0
                WHEN CAST(b.id AS TEXT)=? THEN 1
                WHEN book_search_normalize(COALESCE(b.source_name,''))=? THEN 2
                WHEN book_search_normalize(COALESCE(b.source_file_name,''))=? THEN 3
                WHEN book_search_normalize(b.title) LIKE ? THEN 4
                ELSE 5 END,
                b.updated_at DESC, b.id DESC
            LIMIT ?
            """,
            tuple(params[:-1] + [normalized, normalized, normalized, normalized, f"{normalized}%", params[-1]]),
        )
        return [int(row["id"]) for row in await cur.fetchall()]


async def find_hidden_exact_book_matches(query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Owner diagnostic for an exact title that exists but is not public."""
    normalized = normalize_book_search_text(query)
    if not normalized:
        return []
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT b.id,b.title,b.publication_status,b.source_author_name,
                   COALESCE(ap.pen_name,b.source_author_name,'') AS pen_name
            FROM books b
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE b.publication_status!='published'
              AND b.publication_status!='deleted'
              AND (
                    book_search_normalize(b.title)=?
                 OR book_search_normalize(COALESCE(b.source_name,''))=?
                 OR book_search_normalize(COALESCE(b.source_file_name,''))=?
                 OR CAST(b.id AS TEXT)=?
              )
            ORDER BY b.updated_at DESC,b.id DESC
            LIMIT ?
            """,
            (normalized, normalized, normalized, normalized, max(1, min(20, int(limit)))),
        )
        return [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]


async def search_catalog_books_page(
    query: str = "",
    *,
    filter_code: str = "all",
    page: int = 1,
    page_size: int = 30,
    exclude_ids: set[int] | list[int] | tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Search every published book with stable pagination and typo tolerance."""
    safe_page = max(1, int(page or 1))
    safe_size = max(6, min(60, int(page_size or 30)))
    index_rows = await _book_search_index(status_scope="published", filter_code=filter_code)
    ranked = _rank_book_index(index_rows, query)
    direct_ids = await _direct_catalog_title_ids(query, filter_code=filter_code) if str(query or "").strip() else []
    if direct_ids:
        by_id = {int(row.get("id") or 0): row for row in ranked}
        index_by_id = {int(row.get("id") or 0): row for row in index_rows}
        direct_rows: list[dict[str, Any]] = []
        for book_id in direct_ids:
            row = by_id.get(int(book_id)) or index_by_id.get(int(book_id))
            if row is None:
                continue
            item = dict(row)
            item["search_score"] = max(float(item.get("search_score") or 0), 990_000.0)
            direct_rows.append(item)
        direct_set = {int(row.get("id") or 0) for row in direct_rows}
        ranked = direct_rows + [row for row in ranked if int(row.get("id") or 0) not in direct_set]
    total = len(ranked)
    excluded = {int(value) for value in (exclude_ids or []) if int(value) > 0}
    if excluded:
        ranked = [row for row in ranked if int(row.get("id") or 0) not in excluded]
        start = 0
    else:
        start = (safe_page - 1) * safe_size
    selected = ranked[start:start + safe_size]
    items = await _catalog_rows_for_ids([int(row["id"]) for row in selected])
    score_by_id = {int(row["id"]): float(row.get("search_score") or 0) for row in selected}
    for item in items:
        item["search_score"] = score_by_id.get(int(item.get("id") or 0), 0.0)
    return {
        "items": items,
        "total": total,
        "page": safe_page,
        "page_size": safe_size,
        "has_more": len(excluded) + start + len(selected) < total,
        "query": str(query or "").strip(),
        "filter": str(filter_code or "all"),
    }


async def search_books(query: str, limit: int = 20) -> list[aiosqlite.Row]:
    """Owner/moderator search across every non-deleted book."""
    ranked = _rank_book_index(await _book_search_index(status_scope="all"), query)
    selected_ids = [int(row["id"]) for row in ranked[:max(1, min(250, int(limit)))]]
    if not selected_ids:
        return []
    placeholders = ",".join("?" for _ in selected_ids)
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.*, COALESCE(ap.pen_name,b.source_author_name) AS pen_name,
                   (SELECT c.id FROM chapters c WHERE c.book_id=b.id ORDER BY c.number,c.id LIMIT 1) AS first_chapter_id,
                   (SELECT gc.id FROM graphic_chapters gc WHERE gc.book_id=b.id ORDER BY gc.number,gc.id LIMIT 1) AS first_graphic_chapter_id
            FROM books b LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE b.id IN ({placeholders})
            """,
            tuple(selected_ids),
        )
        rows = list(await cur.fetchall())
    order = {book_id: index for index, book_id in enumerate(selected_ids)}
    rows.sort(key=lambda row: order.get(int(row["id"]), len(order)))
    return rows


async def list_grantable_books(query: str = "", limit: int | None = None) -> list[aiosqlite.Row]:
    """All non-deleted books, searchable by normalized title, both author fields and ID."""
    clean = str(query or "").strip()
    index_rows = await _book_search_index(status_scope="grantable")
    ranked = _rank_book_index(index_rows, clean)
    if limit is not None:
        ranked = ranked[:max(1, min(10000, int(limit)))]
    selected_ids = [int(row["id"]) for row in ranked]
    if not selected_ids:
        return []
    placeholders = ",".join("?" for _ in selected_ids)
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT b.id,b.title,b.publication_status,b.pricing_type,b.price_stars,b.source_author_name,
                   COALESCE(ap.pen_name,b.source_author_name,'') AS pen_name,
                   COUNT(CASE WHEN c.status!='deleted' THEN 1 END) AS chapters_count
            FROM books b
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            LEFT JOIN chapters c ON c.book_id=b.id
            WHERE b.id IN ({placeholders})
            GROUP BY b.id
            """,
            tuple(selected_ids),
        )
        rows = list(await cur.fetchall())
    order = {book_id: index for index, book_id in enumerate(selected_ids)}
    rows.sort(key=lambda row: order.get(int(row["id"]), len(order)))
    return rows


async def search_catalog_promotion_books(
    query: str = "",
    *,
    user_id: int | None = None,
    owner: bool = False,
    limit: int = 40,
) -> list[dict[str, Any]]:
    index_rows = await _book_search_index(
        status_scope="published",
        author_user_id=None if owner else int(user_id or 0),
    )
    ranked = _rank_book_index(index_rows, query)
    selected_ids = [int(row["id"]) for row in ranked[:max(1, min(200, int(limit)))]]
    if not selected_ids:
        return []
    placeholders = ",".join("?" for _ in selected_ids)
    now = utc_now()
    async with connect() as db:
        await _expire_catalog_promotions(db)
        cur = await db.execute(
            f"""
            SELECT b.id,b.title,b.cover_path,b.content_type,b.publication_status,b.author_id,b.source_author_name,
                   COALESCE(ap.pen_name,b.source_author_name,'') AS pen_name,
                   EXISTS(SELECT 1 FROM catalog_promotions cp WHERE cp.book_id=b.id AND cp.status='active' AND cp.expires_at>?) AS promoted
            FROM books b LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE b.id IN ({placeholders})
            """,
            (now, *selected_ids),
        )
        rows = [{key: row[key] for key in row.keys()} for row in await cur.fetchall()]
        await db.commit()
    order = {book_id: index for index, book_id in enumerate(selected_ids)}
    rows.sort(key=lambda row: order.get(int(row.get("id") or 0), len(order)))
    return rows
