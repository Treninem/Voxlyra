import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")


def test_personalized_feed_prefers_matching_unread_books_and_respects_dismiss(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "recommendations.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            list_personalized_books,
            record_recommendation_event,
            set_book_options,
            set_book_publication_status,
            set_bookmark,
            upsert_user,
        )

        await init_db()
        reader = await upsert_user(811001, "reader_rec", "Reader Rec")
        author_user = await upsert_user(811002, "author_rec", "Author Rec")
        await create_author_profile(author_user["id"], "Автор рекомендаций", "", "RU", True)
        profile = await get_author_profile(author_user["id"])

        async def make_book(title, genres, tropes, *, content_type="book"):
            book_id = await create_book(
                profile["id"], title, f"Описание {title}", "16+", "writing", False,
                "free", 0, content_type=content_type,
            )
            await add_manual_chapter(book_id, "Глава 1", "Текст главы. " * 40, True, 0)
            await set_book_options(book_id, "genres", genres)
            await set_book_options(book_id, "tropes", tropes)
            await set_book_publication_status(book_id, "published")
            return book_id

        source_id = await make_book("Источник фэнтези", ["fantasy"], ["academy", "magic"])
        close_id = await make_book("Похожая академия", ["fantasy"], ["academy", "magic"])
        partial_id = await make_book("Магический путь", ["fantasy"], ["magic"])
        distant_id = await make_book("Современный роман", ["romance"], ["office"])

        await set_bookmark(reader["id"], source_id, "favorite")
        items = await list_personalized_books(reader["id"], limit=10)
        ids = [int(item["id"]) for item in items]

        assert source_id not in ids
        assert close_id in ids and partial_id in ids and distant_id in ids
        assert ids.index(close_id) < ids.index(distant_id)
        close_item = next(item for item in items if int(item["id"]) == close_id)
        assert close_item["recommendation_personalized"] is True
        assert close_item["recommendation_reason"].startswith(("Похоже на", "Ваш интерес"))

        assert await record_recommendation_event(reader["id"], close_id, "dismiss")
        items_after = await list_personalized_books(reader["id"], limit=10)
        assert close_id not in {int(item["id"]) for item in items_after}

    asyncio.run(scenario())


def test_new_reader_gets_honest_starter_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "starter_recommendations.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            list_personalized_books,
            set_book_publication_status,
            upsert_user,
        )

        await init_db()
        reader = await upsert_user(812001, "new_reader", "New Reader")
        author_user = await upsert_user(812002, "new_author", "New Author")
        await create_author_profile(author_user["id"], "Новый автор", "", "RU", True)
        profile = await get_author_profile(author_user["id"])
        for index in range(3):
            book_id = await create_book(
                profile["id"], f"Новая книга {index}", "Описание", "12+", "writing", False,
                "free", 0,
            )
            await add_manual_chapter(book_id, "Начало", "Текст. " * 50, True, 0)
            await set_book_publication_status(book_id, "published")

        items = await list_personalized_books(reader["id"], limit=3)
        assert len(items) == 3
        assert all(item["recommendation_personalized"] is False for item in items)
        assert all(item["recommendation_reason"] for item in items)

    asyncio.run(scenario())


def test_recommendation_ui_and_api_contract_are_wired():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/catalog.html").read_text(encoding="utf-8")
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    webapp = (root / "app/webapp.py").read_text(encoding="utf-8")
    database = (root / "app/db.py").read_text(encoding="utf-8")

    assert 'id="forYouSection"' in template
    assert 'id="forYouShelf"' in template
    assert "loadForYouRecommendations" in script
    assert "data-recommendation-dismiss" in script
    assert '/api/recommendations/for-you' in webapp
    assert '/api/recommendations/events' in webapp
    assert "list_personalized_books" in database
    assert "recommendation_events" in database
