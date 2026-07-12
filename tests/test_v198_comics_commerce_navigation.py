from __future__ import annotations

import asyncio
from pathlib import Path


def test_v198_build_navigation_and_stage4_assets_exist():
    from app.build_info import OWNER_BUILD_NAME, OWNER_BUILD_VERSION

    assert OWNER_BUILD_VERSION in {"v1.9.8", "v1.9.9", "v1.10.0", "v1.10.1", "v1.10.2", "v1.10.3", "v1.10.4", "v1.10.5", "v1.11.0", "v1.11.1"}
    assert "комикс" in OWNER_BUILD_NAME.lower()
    for relative in (
        "docs/COMICS_STAGE4_V1_9_8.md",
        "docs/STATUS_V1_9_8.md",
        "templates/comic_reader.html",
        "static/js/comic.js",
    ):
        assert Path(relative).is_file(), relative

    base = Path("templates/base.html").read_text(encoding="utf-8")
    app_js = Path("static/js/app.js").read_text(encoding="utf-8")
    keyboards = Path("app/keyboards.py").read_text(encoding="utf-8")
    assert "routeBackButton" in base and "route-nav-home" in base
    assert "tg?.BackButton?.show" in app_js
    assert "К моим книгам" in keyboards and "Главное меню" in keyboards


def test_v198_graphic_schema_volume_preview_and_page_reports(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "v198.sqlite3"))

    async def scenario():
        import aiosqlite
        from app.db import (
            add_graphic_pages,
            create_author_profile,
            create_book,
            create_graphic_chapter_record,
            create_graphic_page_report,
            get_author_profile,
            get_graphic_chapter,
            init_db,
            list_graphic_page_reports,
            list_graphic_pages,
            list_graphic_volumes_for_book,
            moderate_graphic_page,
            set_graphic_chapter_preview_for_author,
            upsert_graphic_volume_for_author,
            upsert_user,
        )

        await init_db()
        async with aiosqlite.connect(settings.DATABASE_PATH) as db:
            chapter_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(graphic_chapters)")).fetchall()}
            page_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(graphic_pages)")).fetchall()}
            purchase_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(purchases)")).fetchall()}
        assert {"preview_pages", "moderation_status", "moderation_note"} <= chapter_columns
        assert {"moderation_status", "moderation_note"} <= page_columns
        assert "graphic_volume_number" in purchase_columns

        author = await upsert_user(19801, "artist198", "Artist 198")
        reader = await upsert_user(19802, "reader198", "Reader 198")
        await create_author_profile(author["id"], "Художник", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Графический том", "Описание " * 20, "12+", "writing", False,
            "chapters", 0, content_type="comic", reading_mode="ltr",
        )
        chapter_id = await create_graphic_chapter_record(
            book_id, "Глава 1", price_stars=5, is_free=False,
            volume_number=1, volume_title="Начало", preview_pages=2,
        )
        await add_graphic_pages(chapter_id, [
            {"number": 1, "file_path": "storage/comics/1.webp", "source_filename": "1.png", "mime_type": "image/webp", "width": 100, "height": 200, "file_size": 10, "checksum": "a" * 64},
            {"number": 2, "file_path": "storage/comics/2.webp", "source_filename": "2.png", "mime_type": "image/webp", "width": 100, "height": 200, "file_size": 10, "checksum": "b" * 64},
        ])
        assert await set_graphic_chapter_preview_for_author(chapter_id, author["id"], 1)
        chapter = await get_graphic_chapter(chapter_id)
        assert int(chapter["preview_pages"]) == 1

        assert await upsert_graphic_volume_for_author(book_id, 1, author["id"], title="Начало", price_stars=8, is_free=False)
        volumes = await list_graphic_volumes_for_book(book_id)
        assert len(volumes) == 1 and int(volumes[0]["price_stars"]) == 8

        page = (await list_graphic_pages(chapter_id))[0]
        report_id = await create_graphic_page_report(reader["id"], int(page["id"]), "Страница отображается в неверном порядке")
        reports = await list_graphic_page_reports("new")
        assert reports and int(reports[0]["id"]) == report_id
        assert await moderate_graphic_page(int(page["id"]), author["id"], decision="reject", note="Нужно заменить страницу")
        assert not await list_graphic_page_reports("new")

    asyncio.run(scenario())


def test_v198_graphic_chapter_and_volume_stars_purchase(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "payments.sqlite3"))

    async def scenario():
        from app.db import (
            create_author_profile,
            create_book,
            create_graphic_chapter_record,
            create_paid_purchase,
            get_author_profile,
            has_graphic_volume_purchase,
            init_db,
            set_book_publication_status,
            set_graphic_chapter_status,
            upsert_graphic_volume_for_author,
            upsert_user,
            user_can_access_graphic,
        )
        from app.services.payments import build_pay_target

        await init_db()
        author = await upsert_user(19811, "author198", "Author 198")
        buyer_chapter = await upsert_user(19812, "buyer_chapter", "Buyer Chapter")
        buyer_volume = await upsert_user(19813, "buyer_volume", "Buyer Volume")
        await create_author_profile(author["id"], "Автор", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Манга", "Описание " * 20, "12+", "writing", False,
            "chapters", 0, content_type="manga", reading_mode="rtl",
        )
        chapter1 = await create_graphic_chapter_record(book_id, "Глава 1", is_free=False, price_stars=5, volume_number=1, preview_pages=2)
        chapter2 = await create_graphic_chapter_record(book_id, "Глава 2", is_free=False, price_stars=6, volume_number=1, preview_pages=2)
        await set_book_publication_status(book_id, "published")
        await set_graphic_chapter_status(chapter1, "published")
        await set_graphic_chapter_status(chapter2, "published")
        assert await upsert_graphic_volume_for_author(book_id, 1, author["id"], title="Первый том", price_stars=9, is_free=False)

        chapter_target = await build_pay_target("graphic", chapter1, user_id=buyer_chapter["id"])
        assert chapter_target and chapter_target.amount_stars == 5
        await create_paid_purchase(
            user_id=buyer_chapter["id"], payload=chapter_target.payload,
            amount_stars=5, telegram_payment_charge_id="charge-v198-chapter",
        )
        assert await user_can_access_graphic(buyer_chapter["id"], chapter1)
        assert not await user_can_access_graphic(buyer_chapter["id"], chapter2)
        assert not await has_graphic_volume_purchase(buyer_chapter["id"], book_id, 1)

        volume_target = await build_pay_target("graphic_volume", 1, user_id=buyer_volume["id"], amount_stars=book_id)
        assert volume_target and volume_target.amount_stars == 9
        await create_paid_purchase(
            user_id=buyer_volume["id"], payload=volume_target.payload,
            amount_stars=9, telegram_payment_charge_id="charge-v198-volume",
        )
        assert await has_graphic_volume_purchase(buyer_volume["id"], book_id, 1)
        assert await user_can_access_graphic(buyer_volume["id"], chapter1)
        assert await user_can_access_graphic(buyer_volume["id"], chapter2)

    asyncio.run(scenario())


def test_v198_preview_moderation_and_author_controls_are_wired():
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    comic_js = Path("static/js/comic.js").read_text(encoding="utf-8")
    author_js = Path("static/js/author.js").read_text(encoding="utf-8")
    author_html = Path("templates/author.html").read_text(encoding="utf-8")
    book_html = Path("templates/book.html").read_text(encoding="utf-8")
    control_js = Path("static/js/control.js").read_text(encoding="utf-8")

    for token in (
        '/api/comic/page/{graphic_page_id}/report',
        '/api/control/graphic-page-reports',
        '/api/control/graphic-page/{graphic_page_id}/{action}',
        'preview_only',
        '_bot_purchase_url("graphic"',
    ):
        assert token in webapp
    assert "data-report-graphic-page" in comic_js
    assert "graphicPreviewNotice" in Path("templates/comic_reader.html").read_text(encoding="utf-8")
    assert "graphicPreviewPages" in author_html and "graphicChapterEditPreview" in author_html
    assert "renderGraphicVolumes" in author_js and "data-save-graphic-volume" in author_js
    assert "buy_graphic_" in book_html and "buy_volume_" in book_html
    assert "loadGraphicPages" in control_js
