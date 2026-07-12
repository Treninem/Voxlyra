import asyncio

import pytest

pytest.importorskip("aiosqlite")


def test_free_whole_book_and_chapter_sale_modes_are_consistent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pricing.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            get_book,
            get_book_pricing_state,
            init_db,
            list_chapters_for_book,
            restore_saved_chapter_prices,
            update_book_price,
            update_chapter_access_range,
            upsert_user,
        )

        await init_db()
        author = await upsert_user(111001, "price_author", "Price Author")
        await create_author_profile(author["id"], "Автор цен", "Описание", "RU", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Книга с понятными ценами", "Описание", "16+", "writing", False,
            "chapters", 120,
        )
        for number in range(1, 6):
            await add_manual_chapter(
                book_id, f"Глава {number}", f"Текст главы {number}. " * 30,
                is_free=number == 1, price_stars=0 if number == 1 else 5,
            )

        # В режиме продажи по главам диапазон можно продавать отдельно.
        result = await update_chapter_access_range(book_id, author["id"], 2, 4, "chapter", 7)
        assert result["updated"] == 3
        chapters = await list_chapters_for_book(book_id)
        assert [int(row["price_stars"]) for row in chapters] == [0, 7, 7, 7, 5]
        assert int((await get_book(book_id))["price_stars"]) == 120

        # В режиме «только вся книга» отдельные цены отключаются, но бесплатная глава остаётся ознакомительной.
        assert await update_book_price(book_id, author["id"], "whole_book", 120)
        book = await get_book(book_id)
        assert book["pricing_type"] == "whole_book"
        chapters = await list_chapters_for_book(book_id)
        assert [int(row["price_stars"]) for row in chapters] == [0, 0, 0, 0, 0]
        assert int(chapters[0]["is_free"]) == 1
        blocked = await update_chapter_access_range(book_id, author["id"], 2, 3, "chapter", 9)
        assert blocked["reason"] == "chapter_sales_disabled"

        # 0 Stars делает бесплатными всю книгу и все главы и скрывает продажу глав.
        assert await update_book_price(book_id, author["id"], "free", 0)
        state = await get_book_pricing_state(book_id)
        assert state["mode"] == "free"
        chapters = await list_chapters_for_book(book_id)
        assert all(int(row["is_free"]) == 1 and int(row["price_stars"]) == 0 for row in chapters)
        blocked = await update_chapter_access_range(book_id, author["id"], 2, 4, "chapter", 8)
        assert blocked["reason"] == "book_is_free"

        # При повторном включении продажи по главам старые цены не возвращаются сами.
        assert await update_book_price(book_id, author["id"], "chapters", 140)
        chapters = await list_chapters_for_book(book_id)
        assert all(int(row["price_stars"]) == 0 for row in chapters)
        restored = await restore_saved_chapter_prices(book_id, author["id"])
        assert restored["updated"] >= 1
        chapters = await list_chapters_for_book(book_id)
        assert any(int(row["price_stars"]) > 0 for row in chapters)

    asyncio.run(scenario())
