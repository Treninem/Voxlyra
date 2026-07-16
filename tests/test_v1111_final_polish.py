import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

ROOT = Path(__file__).resolve().parents[1]


def test_free_chapter_shell_never_offers_zero_star_purchase():
    template = (ROOT / "templates" / "reader.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    webapp = (ROOT / "app" / "webapp.py").read_text(encoding="utf-8")

    assert "elif chapter_is_free" in template
    assert "Глава бесплатная" in template
    assert "Откройте главу в Telegram" in template
    assert "can_buy_chapter" in template
    assert "purchase_url = _bot_purchase_url(\"chapter\", chapter_id) if can_buy_chapter else \"\"" in webapp
    assert "freeAccessExpected" in script
    assert "Покупка для этой главы не требуется" in script


def test_version_is_owner_only_and_static_cache_is_refreshed():
    build = (ROOT / "app" / "build_info.py").read_text(encoding="utf-8")
    owner = (ROOT / "app" / "handlers" / "owner.py").read_text(encoding="utf-8")
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")

    assert 'OWNER_BUILD_VERSION = "v1.11.12"' in build
    assert "owner_build_label()" in owner
    assert "OWNER_BUILD_VERSION" not in base
    assert "PUBLIC_VERSION_VISIBLE: bool = False" in config
    assert "asset_version" in base


def test_legal_acceptance_survives_routine_release_and_can_be_forced(tmp_path, monkeypatch):
    db_path = tmp_path / "legal.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app import db as db_module
        # Другие тесты могли импортировать app.db до смены DATABASE_PATH.
        # Привязываем модуль к актуальным настройкам из monkeypatch.
        monkeypatch.setattr(db_module, "settings", get_settings())
        from app.db import (
            accept_legal_document,
            get_missing_legal_documents,
            init_db,
            set_setting,
            upsert_user,
        )

        db_module._INITIALIZED_DATABASES.clear()
        await init_db()
        user = await upsert_user(7111001, "legal_user", "Legal User")
        await accept_legal_document(
            int(user["id"]), "terms", "2026-01-01", doc_hash="old-hash", source="test"
        )

        # Обычная новая сборка/изменение PDF не требует повторного подтверждения.
        missing = await get_missing_legal_documents(
            int(user["id"]), [("terms", "2026-07-10", "new-hash")]
        )
        assert missing == []

        # Существенное изменение включается явно для конкретной версии.
        await set_setting("legal_reaccept_terms_version", "2026-07-10")
        missing = await get_missing_legal_documents(
            int(user["id"]), [("terms", "2026-07-10", "new-hash")]
        )
        assert missing == ["terms"]

        await accept_legal_document(
            int(user["id"]), "terms", "2026-07-10", doc_hash="new-hash", source="test"
        )
        missing = await get_missing_legal_documents(
            int(user["id"]), [("terms", "2026-07-10", "new-hash")]
        )
        assert missing == []

    asyncio.run(scenario())


def test_migration_opens_all_chapters_of_zero_price_book(tmp_path, monkeypatch):
    db_path = tmp_path / "pricing_migration.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app import db as db_module
        from app.db import connect, init_db

        db_module._INITIALIZED_DATABASES.clear()
        await init_db()
        now = "2026-07-12T00:00:00+00:00"
        async with connect() as db:
            await db.execute(
                "INSERT INTO users(telegram_id, username, full_name, is_blocked, created_at, updated_at) VALUES(?,?,?,?,?,?)",
                (7111002, "author", "Author", 0, now, now),
            )
            user_id = (await (await db.execute("SELECT id FROM users WHERE telegram_id=?", (7111002,))).fetchone())["id"]
            await db.execute(
                "INSERT INTO author_profiles(user_id, pen_name, status, created_at, updated_at) VALUES(?,?,?,?,?)",
                (user_id, "Автор", "active", now, now),
            )
            author_id = (await (await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (user_id,))).fetchone())["id"]
            await db.execute(
                "INSERT INTO books(author_id,title,publication_status,pricing_type,price_stars,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (author_id, "Бесплатная книга", "published", "chapters", 0, now, now),
            )
            book_id = (await (await db.execute("SELECT id FROM books WHERE title='Бесплатная книга'")).fetchone())["id"]
            await db.execute(
                "INSERT INTO chapters(book_id,number,title,text,is_free,price_stars,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (book_id, 1, "Глава 1", "Текст", 0, 9, "published", now, now),
            )
            await db.commit()

        # Повторный Redeploy выполняет идемпотентную финальную миграцию.
        db_module._INITIALIZED_DATABASES.clear()
        await init_db()
        async with connect() as db:
            book = await (await db.execute("SELECT pricing_type, price_stars FROM books WHERE id=?", (book_id,))).fetchone()
            chapter = await (await db.execute("SELECT is_free, price_stars, saved_price_stars FROM chapters WHERE book_id=?", (book_id,))).fetchone()
        assert book["pricing_type"] == "free"
        assert int(book["price_stars"]) == 0
        assert int(chapter["is_free"]) == 1
        assert int(chapter["price_stars"]) == 0
        assert int(chapter["saved_price_stars"] or 0) == 9

    asyncio.run(scenario())
