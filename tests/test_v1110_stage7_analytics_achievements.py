import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")


async def _seed_author_book(seed: int):
    from app.db import (
        add_manual_chapter,
        create_author_profile,
        create_book,
        get_author_profile,
        set_book_publication_status,
        set_chapter_status,
        upsert_user,
    )

    author_user = await upsert_user(seed, f"stage7_author_{seed}", f"Stage7 Author {seed}")
    await create_author_profile(author_user["id"], "Автор аналитики", "", "RU", True)
    profile = await get_author_profile(author_user["id"])
    book_id = await create_book(profile["id"], "Книга аналитики", "Описание", "16+", "writing", False, "free", 0)
    chapters = []
    for number in range(1, 4):
        chapter_id = await add_manual_chapter(book_id, f"Глава {number}", (f"Текст главы {number}. " * 80), True, 0)
        await set_chapter_status(chapter_id, "published")
        chapters.append(chapter_id)
    await set_book_publication_status(book_id, "published")
    return author_user, book_id, chapters


def test_author_analytics_reports_readers_dropoff_and_sales(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "analytics.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            connect,
            get_author_analytics,
            init_db,
            save_reading_progress,
            set_bookmark,
            upsert_review,
            upsert_user,
            utc_now,
        )

        await init_db()
        author, book_id, chapters = await _seed_author_book(841001)
        reader_one = await upsert_user(841002, "reader_analytics_1", "Reader One")
        reader_two = await upsert_user(841003, "reader_analytics_2", "Reader Two")
        await save_reading_progress(reader_one["id"], chapters[0], 100)
        await save_reading_progress(reader_one["id"], chapters[1], 30)
        await save_reading_progress(reader_two["id"], chapters[0], 100)
        await set_bookmark(reader_one["id"], book_id, "favorite")
        await upsert_review(reader_one["id"], book_id, 5, "Отлично")
        async with connect() as db:
            await db.execute(
                "INSERT INTO purchases(user_id, book_id, amount_stars, status, created_at, purchase_kind) VALUES(?, ?, 25, 'paid', ?, 'content')",
                (reader_one["id"], book_id, utc_now()),
            )
            await db.commit()

        result = await get_author_analytics(author["id"], 30)
        assert result["summary"]["unique_readers"] == 2
        assert result["summary"]["completed_chapters"] == 2
        assert result["summary"]["library_additions"] == 1
        assert result["summary"]["sales_count"] == 1
        assert result["summary"]["revenue_stars"] == 25
        assert result["books"][0]["readers"] == 2
        chapter_two = next(item for item in result["dropoff"] if item["number"] == 2)
        assert chapter_two["started"] == 1
        assert chapter_two["completed"] == 0
        assert chapter_two["completion_rate"] == 0

    asyncio.run(scenario())


def test_achievements_are_awarded_once_and_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "achievements.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import init_db, save_reading_progress, set_bookmark, sync_user_achievements, upsert_review, upsert_user

        await init_db()
        _, book_id, chapters = await _seed_author_book(842001)
        reader = await upsert_user(842002, "achievement_reader", "Achievement Reader")
        await save_reading_progress(reader["id"], chapters[0], 100)
        await upsert_review(reader["id"], book_id, 5, "Первый отзыв")
        for index in range(10):
            # Для коллекционера нужны разные книги; создаём минимальные опубликованные книги того же автора.
            if index == 0:
                target = book_id
            else:
                from app.db import create_book, get_author_profile, set_book_publication_status
                author_profile = await get_author_profile((await upsert_user(842001, "stage7_author_842001", "Stage7 Author 842001"))["id"])
                target = await create_book(author_profile["id"], f"Книга {index}", "", "12+", "writing", False, "free", 0)
                await set_book_publication_status(target, "published")
            await set_bookmark(reader["id"], target, "planned")

        first = await sync_user_achievements(reader["id"])
        codes = {item["code"] for item in first["new"]}
        assert {"first_chapter", "collector", "first_review"}.issubset(codes)
        second = await sync_user_achievements(reader["id"])
        assert second["new"] == []
        assert {"first_chapter", "collector", "first_review"}.issubset({item["code"] for item in second["items"]})

    asyncio.run(scenario())


def test_smart_reminders_require_inactivity_and_respect_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "reminders.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            connect,
            init_db,
            list_smart_reader_reminder_candidates,
            mark_smart_notification_sent,
            save_reading_progress,
            upsert_user,
        )

        await init_db()
        _, book_id, chapters = await _seed_author_book(843001)
        reader = await upsert_user(843002, "reminder_reader", "Reminder Reader")
        await save_reading_progress(reader["id"], chapters[0], 45)
        old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        async with connect() as db:
            await db.execute("UPDATE reading_progress SET updated_at=? WHERE user_id=?", (old, reader["id"]))
            await db.commit()

        candidates = await list_smart_reader_reminder_candidates()
        assert any(int(row["user_id"]) == int(reader["id"]) and int(row["book_id"]) == book_id for row in candidates)
        await mark_smart_notification_sent(reader["id"], "continue_reading", str(book_id))
        candidates_after = await list_smart_reader_reminder_candidates()
        assert not any(int(row["user_id"]) == int(reader["id"]) and int(row["book_id"]) == book_id for row in candidates_after)

    asyncio.run(scenario())


def test_quote_card_is_real_png_and_rejects_foreign_text():
    from app.services.quote_cards import build_quote_card, quote_belongs_to_text

    chapter = "Арден остановился у старого моста и увидел свет в тумане. Потом он сделал шаг вперёд."
    quote = "Арден остановился у старого моста и увидел свет в тумане."
    assert quote_belongs_to_text(quote, chapter)
    assert not quote_belongs_to_text("Этого предложения в главе никогда не было.", chapter)
    image = build_quote_card(quote=quote, book_title="Эхо", chapter_title="Глава 1", author_name="Автор")
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(image) > 20_000


def test_stage7_ui_and_api_contract_are_wired():
    root = Path(__file__).resolve().parents[1]
    author_template = (root / "templates/author.html").read_text(encoding="utf-8")
    library_template = (root / "templates/library.html").read_text(encoding="utf-8")
    reader_template = (root / "templates/reader.html").read_text(encoding="utf-8")
    author_js = (root / "static/js/author.js").read_text(encoding="utf-8")
    app_js = (root / "static/js/app.js").read_text(encoding="utf-8")
    webapp = (root / "app/webapp.py").read_text(encoding="utf-8")
    database = (root / "app/db.py").read_text(encoding="utf-8")
    bot = (root / "app/bot.py").read_text(encoding="utf-8")

    assert 'id="authorAnalyticsPanel"' in author_template
    assert 'id="libraryAchievements"' in library_template
    assert 'id="readerQuotePanel"' in reader_template
    assert "renderAuthorAnalytics" in author_js
    assert "createReaderQuoteCard" in app_js
    assert '/api/author/analytics' in webapp
    assert '/api/reader/{chapter_id}/quote-card' in webapp
    assert "user_achievements" in database
    assert "smart_notification_state" in database
    assert "smart_reader_reminder_loop" in bot
