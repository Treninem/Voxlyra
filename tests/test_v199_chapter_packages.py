from __future__ import annotations

import asyncio
from pathlib import Path


def test_v199_chapter_package_assets_and_routes_exist():
    assert Path("docs/CHAPTER_PACKAGES_V1_9_9.md").is_file()
    author_html = Path("templates/author.html").read_text(encoding="utf-8")
    book_html = Path("templates/book.html").read_text(encoding="utf-8")
    author_js = Path("static/js/author.js").read_text(encoding="utf-8")
    app_js = Path("static/js/app.js").read_text(encoding="utf-8")
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    handlers = Path("app/handlers/payments.py").read_text(encoding="utf-8")
    assert "chapterPackageManager" in author_html
    assert "buy_package_" in book_html
    assert "renderChapterPackages" in author_js
    assert "unlockChapterWithPackage" in app_js
    assert "/unlock-package" in webapp
    assert "buy_package_" in handlers


def test_v199_flexible_chapter_package_flow(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "packages.sqlite3"))

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            create_chapter_package_for_author,
            create_paid_purchase,
            create_refund_request,
            get_author_profile,
            get_user_chapter_credit_summary,
            has_purchase_access,
            init_db,
            list_user_chapter_package_balances,
            redeem_chapter_package_credit,
            set_book_publication_status,
            set_chapter_status,
            update_chapter_package_for_author,
            upsert_user,
        )
        from app.services.payments import build_pay_target

        await init_db()
        author = await upsert_user(19901, "author199", "Author 199")
        reader = await upsert_user(19902, "reader199", "Reader 199")
        reader_unused = await upsert_user(19903, "reader_unused199", "Reader Unused")
        await create_author_profile(author["id"], "Автор пакетов", "", "RU", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Книга с пакетами", "Описание " * 20, "16+", "writing", False,
            "chapters", 120,
        )
        chapters = []
        for number in range(1, 7):
            chapter_id = await add_manual_chapter(
                book_id, f"Глава {number}", "текст " * 80,
                is_free=False, price_stars=5,
            )
            await set_chapter_status(chapter_id, "published")
            chapters.append(chapter_id)
        await set_book_publication_status(book_id, "published")

        package_id = await create_chapter_package_for_author(
            book_id, author["id"], title="Три любые главы",
            chapters_count=3, price_stars=12, content_scope="text",
        )
        target = await build_pay_target("chapter_package", package_id, user_id=reader["id"])
        assert target and target.amount_stars == 12
        assert target.payload == f"vox:chapter_package:{package_id}"

        purchase_id = await create_paid_purchase(
            user_id=reader["id"], payload=target.payload,
            amount_stars=12, telegram_payment_charge_id="charge-package-199",
        )
        summary = await get_user_chapter_credit_summary(reader["id"], book_id, "text")
        assert summary["remaining"] == 3

        # Читатель открывает не подряд: последнюю, вторую и пятую.
        first = await redeem_chapter_package_credit(reader["id"], chapter_id=chapters[5])
        second = await redeem_chapter_package_credit(reader["id"], chapter_id=chapters[1])
        third = await redeem_chapter_package_credit(reader["id"], chapter_id=chapters[4])
        assert [first["remaining"], second["remaining"], third["remaining"]] == [2, 1, 0]
        assert await has_purchase_access(reader["id"], chapter_id=chapters[5])
        assert await has_purchase_access(reader["id"], chapter_id=chapters[1])
        assert await has_purchase_access(reader["id"], chapter_id=chapters[4])
        assert not await has_purchase_access(reader["id"], chapter_id=chapters[0])

        try:
            await redeem_chapter_package_credit(reader["id"], chapter_id=chapters[0])
        except ValueError as exc:
            assert "не осталось" in str(exc)
        else:
            raise AssertionError("Пустой пакет не должен открывать новую главу")

        # Изменение пакета автором не меняет уже купленный баланс.
        assert await update_chapter_package_for_author(
            package_id, author["id"], title="Пять глав теперь",
            chapters_count=5, price_stars=18, content_scope="text", is_active=True,
        )
        balances = await list_user_chapter_package_balances(reader["id"], book_id=book_id)
        assert int(balances[0]["total_credits"]) == 3
        try:
            await create_refund_request(purchase_id, reader["id"], "Пакет уже частично использован, проверка")
        except ValueError as exc:
            assert "использования" in str(exc)
        else:
            raise AssertionError("Использованный пакет нельзя возвращать автоматически")

        # Полностью неиспользованный пакет можно отправить на возврат.
        fresh_target = await build_pay_target("chapter_package", package_id, user_id=reader_unused["id"])
        fresh_purchase = await create_paid_purchase(
            user_id=reader_unused["id"], payload=fresh_target.payload,
            amount_stars=18, telegram_payment_charge_id="charge-package-unused-199",
        )
        refund_id = await create_refund_request(
            fresh_purchase, reader_unused["id"], "Пакет не использован, прошу оформить возврат"
        )
        assert refund_id > 0

    asyncio.run(scenario())
