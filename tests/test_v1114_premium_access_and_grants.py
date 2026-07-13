import asyncio
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_chapter_selection_parser_accepts_mixed_ranges_and_deduplicates():
    from app.services.access_grants import ChapterSelectionError, parse_chapter_selection

    parsed = parse_chapter_selection("98 34,36,38; 56–67 33 36")
    assert parsed.numbers[0] == 33
    assert parsed.numbers[-1] == 98
    assert len(parsed.numbers) == 17
    assert parsed.normalized == "33-34, 36, 38, 56-67, 98"

    with pytest.raises(ChapterSelectionError):
        parse_chapter_selection("")
    with pytest.raises(ChapterSelectionError):
        parse_chapter_selection("0, 2")
    with pytest.raises(ChapterSelectionError):
        parse_chapter_selection("1-1000000")


def test_premium_books_survive_redeploy_and_manual_access_works(tmp_path, monkeypatch):
    pytest.importorskip("aiosqlite")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "premium_access.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app import db as db_module

        monkeypatch.setattr(db_module, "settings", get_settings())
        db_module._INITIALIZED_DATABASES.clear()
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            get_book,
            get_user_premium_status,
            grant_manual_chapter_access,
            grant_premium_manually,
            init_db,
            list_chapters_for_book,
            update_book_price,
            upsert_user,
            user_can_access_chapter,
        )

        await init_db()
        author = await upsert_user(81114001, "premium_author", "Premium Author")
        await create_author_profile(author["id"], "Автор Premium", "Описание", "RU", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Книга по подписке", "Описание", "16+", "writing", False,
            "free", 0,
        )
        # Переход из бесплатного режима в Premium не должен быть уничтожен миграцией нулевой цены.
        assert await update_book_price(book_id, author["id"], "premium", 0)
        book = await get_book(book_id)
        assert book["pricing_type"] == "premium"
        assert int(book["price_stars"] or 0) == 0

        free_id = await add_manual_chapter(
            book_id, "Ознакомительная", "Бесплатный текст. " * 20, is_free=True, price_stars=0
        )
        premium_id = await add_manual_chapter(
            book_id, "Для подписчиков", "Закрытый текст. " * 20, is_free=False, price_stars=0
        )
        chapters = await list_chapters_for_book(book_id)
        assert int(chapters[0]["is_free"]) == 1
        assert int(chapters[1]["is_free"]) == 0
        assert int(chapters[1]["price_stars"]) == 0

        reader = await upsert_user(81114002, "reader_one", "Reader One")
        assert await user_can_access_chapter(reader["id"], free_id)
        assert not await user_can_access_chapter(reader["id"], premium_id)

        premium = await grant_premium_manually(
            user_id=reader["id"], duration_days=30,
            granted_by_user_id=author["id"], note="Тестовая компенсация",
        )
        assert premium["subscription_id"] > 0
        assert await user_can_access_chapter(reader["id"], premium_id)
        status = await get_user_premium_status(reader["id"])
        assert status["active"] is True
        assert status["source"] == "manual"

        second_reader = await upsert_user(81114003, "reader_two", "Reader Two")
        assert not await user_can_access_chapter(second_reader["id"], premium_id)
        grant = await grant_manual_chapter_access(
            user_id=second_reader["id"], book_id=book_id, chapter_ids=[premium_id],
            granted_by_user_id=author["id"], duration_days=7, note="Сбой оплаты",
        )
        assert grant["granted"] == 1
        assert await user_can_access_chapter(second_reader["id"], premium_id)

        # Повторный Redeploy не превращает Premium-книгу с нулевой прямой ценой в бесплатную.
        db_module._INITIALIZED_DATABASES.clear()
        await init_db()
        book = await get_book(book_id)
        chapters = await list_chapters_for_book(book_id)
        assert book["pricing_type"] == "premium"
        assert int(chapters[1]["is_free"]) == 0
        assert await user_can_access_chapter(reader["id"], premium_id)
        assert await user_can_access_chapter(second_reader["id"], premium_id)

    asyncio.run(scenario())


def test_access_permission_is_delegable_but_endpoints_are_protected():
    from app.permissions import DELEGABLE_PERMISSION_CODES, PERMISSION_BY_CODE

    assert "grant_access" in DELEGABLE_PERMISSION_CODES
    assert PERMISSION_BY_CODE["grant_access"].owner_only is False

    webapp = (ROOT / "app" / "webapp.py").read_text(encoding="utf-8")
    assert webapp.count('control_session(x_telegram_init_data, "grant_access")') >= 6
    assert "/api/control/access/chapters/grant" in webapp
    assert "/api/control/access/premium/grant" in webapp
    assert "grant_manual_chapter_access" in webapp
    assert "grant_premium_manually" in webapp


def test_premium_author_mode_and_phone_layout_are_present():
    author_template = (ROOT / "templates" / "author.html").read_text(encoding="utf-8")
    author_script = (ROOT / "static" / "js" / "author.js").read_text(encoding="utf-8")
    book_template = (ROOT / "templates" / "book.html").read_text(encoding="utf-8")
    premium_template = (ROOT / "templates" / "premium.html").read_text(encoding="utf-8")
    control_script = (ROOT / "static" / "js" / "control.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")

    assert 'option value="premium"' in author_template
    assert "Чтение по подписке VoxLyra Premium" in author_template
    assert "mode === 'premium'" in author_script
    assert "Доступна по VoxLyra Premium" in book_template
    assert "автор специально включил в подписку" in premium_template
    assert "98 34,36,38" in control_script
    assert "access-control-shell" in control_script
    assert ".access-grant-grid" in css
    assert "overflow-x:hidden" in css
    assert "@media (max-width: 700px)" in css
    assert ".control-page .app-shell" in css
