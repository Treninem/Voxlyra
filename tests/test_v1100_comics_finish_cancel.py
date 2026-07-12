from __future__ import annotations

import asyncio
from pathlib import Path


def test_v1100_release_assets_and_interfaces_exist():
    from app.build_info import OWNER_BUILD_NAME, OWNER_BUILD_VERSION

    assert OWNER_BUILD_VERSION in {"v1.10.0", "v1.10.1", "v1.10.2", "v1.10.3", "v1.10.4", "v1.10.5"}
    assert "комикс" in OWNER_BUILD_NAME.lower()
    for relative in (
        "app/services/graphic_ocr.py",
        "templates/comic_reader.html",
        "templates/author.html",
        "static/js/comic.js",
        "static/js/author.js",
    ):
        assert Path(relative).is_file(), relative
    comic = Path("static/js/comic.js").read_text(encoding="utf-8")
    author = Path("static/js/author.js").read_text(encoding="utf-8")
    payments = Path("app/handlers/payments.py").read_text(encoding="utf-8")
    control = Path("static/js/control.js").read_text(encoding="utf-8")
    assert "graphicFrameViewer" in comic
    assert "graphicSearchResults" in comic
    assert "processGraphicChapterPages" in author
    assert "payment:cancel:" in payments and "refund_star_payment" in payments
    assert "graphiccomment:publish" in control


def test_v1100_payment_intent_and_safe_cancel_flow(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "cancel.sqlite3"))

    async def scenario():
        from app.db import (
            add_manual_chapter,
            cancel_payment_intent,
            create_author_profile,
            create_book,
            create_immediate_cancel_request,
            create_paid_purchase,
            create_payment_intent,
            finalize_refund,
            get_author_profile,
            get_immediate_purchase_cancel_eligibility,
            get_payment_intent,
            has_purchase_access,
            init_db,
            mark_purchase_access_used,
            set_book_publication_status,
            set_chapter_status,
            upsert_user,
            validate_payment_intent,
        )
        from app.services.payments import build_pay_target

        await init_db()
        author = await upsert_user(110001, "author1100", "Author 1100")
        reader = await upsert_user(110002, "reader1100", "Reader 1100")
        stranger = await upsert_user(110003, "stranger1100", "Stranger 1100")
        await create_author_profile(author["id"], "Автор", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Книга отмен", "Описание " * 20, "12+", "writing", False,
            "chapters", 0,
        )
        chapter_id = await add_manual_chapter(
            book_id, "Платная глава", "текст " * 100, is_free=False, price_stars=7
        )
        await set_book_publication_status(book_id, "published")
        await set_chapter_status(chapter_id, "published")
        target = await build_pay_target("chapter", chapter_id, user_id=reader["id"])
        assert target and target.amount_stars == 7

        canceled = await create_payment_intent(reader["id"], target.payload, 7)
        assert await validate_payment_intent(canceled["payload"], reader["id"], 7)
        assert not await validate_payment_intent(canceled["payload"], stranger["id"], 7)
        assert await cancel_payment_intent(canceled["token"], reader["id"])
        assert not await validate_payment_intent(canceled["payload"], reader["id"], 7)
        try:
            await create_paid_purchase(
                user_id=reader["id"], payload=canceled["payload"], amount_stars=7,
                telegram_payment_charge_id="charge-canceled-1100",
            )
        except ValueError as exc:
            assert "отмен" in str(exc).lower()
        else:
            raise AssertionError("Отменённый счёт нельзя оплачивать")

        active = await create_payment_intent(reader["id"], target.payload, 7)
        purchase_id = await create_paid_purchase(
            user_id=reader["id"], payload=active["payload"], amount_stars=7,
            telegram_payment_charge_id="charge-paid-1100",
        )
        intent_row = await get_payment_intent(active["token"])
        assert intent_row and intent_row["status"] == "paid"
        assert await has_purchase_access(reader["id"], chapter_id=chapter_id)

        eligibility = await get_immediate_purchase_cancel_eligibility(purchase_id, reader["id"])
        assert eligibility["allowed"] and int(eligibility["minutes_left"]) >= 1
        refund_id = await create_immediate_cancel_request(purchase_id, reader["id"])
        assert refund_id > 0
        assert await finalize_refund(refund_id, author["id"], "Автоматическая отмена тест")
        assert not await has_purchase_access(reader["id"], chapter_id=chapter_id)
        assert not (await get_immediate_purchase_cancel_eligibility(purchase_id, reader["id"]))["allowed"]

        used_intent = await create_payment_intent(reader["id"], target.payload, 7)
        used_purchase_id = await create_paid_purchase(
            user_id=reader["id"], payload=used_intent["payload"], amount_stars=7,
            telegram_payment_charge_id="charge-used-1100",
        )
        marked_id = await mark_purchase_access_used(reader["id"], chapter_id=chapter_id)
        assert marked_id == used_purchase_id
        used_eligibility = await get_immediate_purchase_cancel_eligibility(used_purchase_id, reader["id"])
        assert not used_eligibility["allowed"]
        assert "использ" in used_eligibility["reason"].lower()

    asyncio.run(scenario())


def test_v1100_graphic_layers_search_bookmarks_comments_and_statistics(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "graphics.sqlite3"))

    async def scenario():
        from app.db import (
            add_graphic_page_comment,
            add_graphic_pages,
            create_author_profile,
            create_book,
            create_graphic_chapter_record,
            get_author_profile,
            get_graphic_chapter_statistics,
            get_graphic_reader_layers,
            init_db,
            list_graphic_page_comments_for_moderation,
            list_graphic_pages,
            list_user_graphic_bookmarks,
            record_graphic_reading_event,
            replace_graphic_frames_for_author,
            replace_graphic_translation_regions_for_author,
            search_graphic_book_text,
            set_book_publication_status,
            set_graphic_chapter_status,
            set_graphic_page_comment_status,
            toggle_graphic_page_bookmark,
            upsert_graphic_page_text,
            upsert_user,
        )

        await init_db()
        author = await upsert_user(110011, "artist1100", "Artist 1100")
        reader = await upsert_user(110012, "reader1100g", "Reader 1100G")
        moderator = await upsert_user(110013, "mod1100", "Moderator 1100")
        await create_author_profile(author["id"], "Художник", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Манга с поиском", "Описание " * 20, "12+", "writing", False,
            "chapters", 0, content_type="manga", reading_mode="rtl",
        )
        await set_book_publication_status(book_id, "published")
        chapter_id = await create_graphic_chapter_record(
            book_id, "Глава 1", is_free=True, volume_number=1, preview_pages=2
        )
        await set_graphic_chapter_status(chapter_id, "published")
        await add_graphic_pages(chapter_id, [
            {"number": 1, "file_path": "storage/comics/1100-1.webp", "source_filename": "1.png", "mime_type": "image/webp", "width": 1000, "height": 1600, "file_size": 100, "checksum": "1" * 64},
            {"number": 2, "file_path": "storage/comics/1100-2.webp", "source_filename": "2.png", "mime_type": "image/webp", "width": 1000, "height": 1600, "file_size": 100, "checksum": "2" * 64},
        ])
        pages = await list_graphic_pages(chapter_id)
        page_id = int(pages[0]["id"])

        assert await upsert_graphic_page_text(
            page_id, author["id"], language_code="ru", text_kind="ocr",
            text="Герой открывает древнюю дверь", confidence=91,
        )
        assert await replace_graphic_translation_regions_for_author(
            page_id, author["id"], "ru",
            [{"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.15, "text": "Открой дверь"}],
        )
        assert await replace_graphic_frames_for_author(
            page_id, author["id"],
            [{"x": 0, "y": 0, "width": 1, "height": 0.45}, {"x": 0, "y": 0.5, "width": 1, "height": 0.5}],
        )
        search = await search_graphic_book_text(book_id, "древнюю")
        assert len(search) == 1 and int(search[0]["graphic_page_id"]) == page_id

        assert await toggle_graphic_page_bookmark(reader["id"], page_id)
        bookmarks = await list_user_graphic_bookmarks(reader["id"], book_id)
        assert len(bookmarks) == 1
        layers = await get_graphic_reader_layers([page_id], "ru", reader["id"])
        assert layers[page_id]["bookmarked"] is True
        assert len(layers[page_id]["translations"]) == 1
        assert len(layers[page_id]["frames"]) == 2

        comment_id = await add_graphic_page_comment(reader["id"], page_id, "Очень важный кадр")
        pending = await list_graphic_page_comments_for_moderation("pending")
        assert any(int(row["id"]) == comment_id for row in pending)
        assert await set_graphic_page_comment_status(comment_id, moderator["id"], "published")
        layers = await get_graphic_reader_layers([page_id], "ru", reader["id"])
        assert layers[page_id]["comments"][0]["text"] == "Очень важный кадр"

        await record_graphic_reading_event(reader["id"], chapter_id, "open", session_key="s1")
        await record_graphic_reading_event(reader["id"], chapter_id, "page_view", graphic_page_id=page_id, session_key="s1")
        await record_graphic_reading_event(reader["id"], chapter_id, "frame_view", graphic_page_id=page_id, session_key="s1")
        await record_graphic_reading_event(reader["id"], chapter_id, "complete", session_key="s1")
        stats = await get_graphic_chapter_statistics(chapter_id)
        assert stats["unique_openers"] == 1
        assert stats["page_views"] == 1
        assert stats["frame_views"] == 1
        assert stats["completers"] == 1

    asyncio.run(scenario())


def test_v1100_local_frame_suggestion(tmp_path):
    from PIL import Image, ImageDraw
    from app.services.graphic_ocr import suggest_graphic_frames

    target = tmp_path / "page.png"
    image = Image.new("RGB", (600, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 580, 390), fill="black")
    draw.rectangle((20, 470, 580, 880), fill="black")
    image.save(target)
    frames = suggest_graphic_frames(target)
    assert 1 <= len(frames) <= 24
    assert all(0 <= frame["x"] <= 1 and 0 < frame["width"] <= 1 for frame in frames)
