import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

ROOT = Path(__file__).resolve().parents[1]


def test_integer_stars_use_largest_remainders():
    from app.db import allocate_integer_stars

    result = allocate_integer_stars(69, {1: 50, 2: 20, 3: 15, 4: 10, 5: 5})
    assert result == {1: 35, 2: 14, 3: 10, 4: 7, 5: 3}
    assert sum(result.values()) == 69
    assert all(isinstance(value, int) for value in result.values())


def test_paid_premium_period_creates_and_settles_author_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "premium-revenue.sqlite3"))
    from app.config import get_settings
    from app import db as db_module

    get_settings.cache_clear()
    monkeypatch.setattr(db_module, "settings", get_settings())
    db_module._INITIALIZED_DATABASES.clear()

    async def scenario():
        from app.db import (
            activate_premium_subscription,
            add_manual_chapter,
            connect,
            create_author_profile,
            create_book,
            get_author_finance_summary,
            get_author_profile,
            get_premium_plan,
            init_db,
            record_premium_content_event,
            settle_due_premium_author_pools,
            update_book_price,
            upsert_user,
        )

        await init_db()
        reader = await upsert_user(91118001, "premium_reader", "Premium Reader")
        author_a = await upsert_user(91118002, "author_a", "Author A")
        author_b = await upsert_user(91118003, "author_b", "Author B")
        await create_author_profile(author_a["id"], "Автор А", "", "RU", True)
        await create_author_profile(author_b["id"], "Автор Б", "", "RU", True)
        profile_a = await get_author_profile(author_a["id"])
        profile_b = await get_author_profile(author_b["id"])

        book_a = await create_book(profile_a["id"], "Premium A", "", "16+", "writing", False, "free", 0)
        book_b = await create_book(profile_b["id"], "Premium B", "", "16+", "writing", False, "free", 0)
        assert await update_book_price(book_a, author_a["id"], "premium", 0)
        assert await update_book_price(book_b, author_b["id"], "premium", 0)
        chapter_a = await add_manual_chapter(book_a, "A1", "Текст A " * 100, is_free=False, price_stars=0)
        chapter_b = await add_manual_chapter(book_b, "B1", "Текст B " * 100, is_free=False, price_stars=0)

        plan = await get_premium_plan("monthly")
        await activate_premium_subscription(
            user_id=int(reader["id"]),
            plan_code="monthly",
            amount_stars=int(plan["price_stars"]),
            telegram_payment_charge_id="premium-revenue-charge",
            invoice_payload="vox:premium:monthly",
        )

        # Автор A: открытие + дочитывание = 3 балла. Автор B: открытие = 1 балл.
        assert await record_premium_content_event(reader["id"], chapter_a, "open")
        assert await record_premium_content_event(reader["id"], chapter_a, "complete")
        assert await record_premium_content_event(reader["id"], chapter_b, "open")

        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        async with connect() as db:
            await db.execute("UPDATE premium_author_pools SET period_end_at=? WHERE status='pending'", (past,))
            await db.commit()

        settled = await settle_due_premium_author_pools(now_at=datetime.now(timezone.utc).isoformat())
        assert settled["settled"] == 1
        assert settled["allocated_stars"] == 69

        finance_a = await get_author_finance_summary(author_a["id"])
        finance_b = await get_author_finance_summary(author_b["id"])
        assert finance_a["premium_total"] == 52
        assert finance_b["premium_total"] == 17
        assert finance_a["premium_total"] + finance_b["premium_total"] == 69

        # Повторный расчёт идемпотентен и ничего не удваивает.
        again = await settle_due_premium_author_pools(now_at=datetime.now(timezone.utc).isoformat())
        assert again["processed"] == 0
        assert (await get_author_finance_summary(author_a["id"]))["premium_total"] == 52

    asyncio.run(scenario())


def test_premium_revenue_is_visible_in_owner_and_author_interfaces():
    db = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    control = (ROOT / "static" / "js" / "control.js").read_text(encoding="utf-8")
    author = (ROOT / "static" / "js" / "author.js").read_text(encoding="utf-8")
    legal = (ROOT / "app" / "legal_texts.py").read_text(encoding="utf-8")

    assert "premium_author_pools" in db
    assert "allocate_integer_stars" in db
    assert "author_pool_percent" in control
    assert "Доход Premium" in author
    assert "метод наибольших остатков" in legal
