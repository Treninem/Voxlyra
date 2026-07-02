from pathlib import Path

from app.services.book_parser import build_import_report, detect_text_problems, parse_book_file, split_plain_text_to_chapters
from app.services.pricing import recommend_book_price, recommend_price


def test_recommend_price_basic():
    assert recommend_price(10) == 30
    assert recommend_price(10, finished=True) >= 36
    assert recommend_price(10, has_audio=True) >= 45


def test_recommend_book_price_free():
    assert recommend_book_price("короткое описание", "free") == 0


def test_split_plain_text_to_chapters():
    text = "Глава 1\nТекст первой главы\n\nГлава 2. Продолжение\nТекст второй главы"
    chapters = split_plain_text_to_chapters(text)
    assert len(chapters) == 2
    assert chapters[0].number == 1
    assert "первой" in chapters[0].text
    assert chapters[1].number == 2


def test_detect_text_problems():
    assert "Файл пустой" in detect_text_problems("   ")
    assert "Есть битые символы" in detect_text_problems("abc � xyz")


def test_parse_txt_file(tmp_path: Path):
    path = tmp_path / "book.txt"
    path.write_text("Глава 1\n" + "текст " * 120 + "\n\nГлава 2\n" + "дальше " * 120, encoding="utf-8")
    chapters = parse_book_file(path)
    assert len(chapters) == 2
    assert chapters[0].title.startswith("Глава 1")


def test_import_report_flags_short_chapter():
    chapters = split_plain_text_to_chapters("Глава 1\nкоротко\n\nГлава 2\n" + "нормально " * 80)
    report = build_import_report(chapters)
    assert report["chapters_count"] == 2
    assert any("корот" in problem.lower() for problem in report["problems"])

from app.services.audio_tools import build_audio_import_report, format_duration, inspect_audio_file, is_supported_audio


def test_audio_extensions_and_duration_format(tmp_path: Path):
    assert is_supported_audio("chapter.mp3")
    assert is_supported_audio("voice.M4A")
    assert not is_supported_audio("cover.png")
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"


def test_audio_report_for_dummy_file(tmp_path: Path):
    path = tmp_path / "chapter.mp3"
    path.write_bytes(b"ID3" + b"0" * 1024)
    info = inspect_audio_file(path, source_filename="chapter.mp3", title="Глава 1")
    report = build_audio_import_report([info])
    assert report["count"] == 1
    assert report["total_size_mb"] >= 0
    assert report["preview"][0]["title"] == "Глава 1"

import asyncio
import os


def test_payment_ledger_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "pay.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            init_db,
            upsert_user,
            create_author_profile,
            get_author_profile,
            create_book,
            add_manual_chapter,
            create_paid_purchase,
            has_purchase_access,
            get_author_finance_summary,
            create_refund_request,
            list_refund_requests,
            mark_purchase_refunded,
        )
        await init_db()
        author_user = await upsert_user(9001, "author", "Author")
        reader = await upsert_user(9002, "reader", "Reader")
        await create_author_profile(author_user["id"], "Автор", "Описание", "RU", True)
        profile = await get_author_profile(author_user["id"])
        book_id = await create_book(profile["id"], "Книга", "Описание книги", "16+", "writing", False, "chapters", 5)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 300, is_free=False, price_stars=5)
        purchase_id = await create_paid_purchase(
            user_id=reader["id"],
            payload=f"vox:chapter:{chapter_id}",
            amount_stars=5,
            telegram_payment_charge_id="charge_test",
        )
        assert await has_purchase_access(reader["id"], chapter_id=chapter_id)
        summary = await get_author_finance_summary(author_user["id"])
        assert summary["gross"] == 5
        refund_id = await create_refund_request(purchase_id, reader["id"], "Причина тестового возврата")
        refunds = await list_refund_requests()
        assert refunds[0]["id"] == refund_id
        await mark_purchase_refunded(purchase_id)
        summary_after = await get_author_finance_summary(author_user["id"])
        assert summary_after["refunded"] == 5

    asyncio.run(scenario())


def test_catalog_choices_include_checkbox_topics():
    from app.catalog_options import GENRES, TROPES
    genre_labels = {item.label for item in GENRES}
    trope_labels = {item.label for item in TROPES}
    assert "Драма" in genre_labels
    assert "Тёмный хоррор" in genre_labels
    assert "Детектив" in genre_labels
    assert "Повороты сюжета" in trope_labels


def test_book_options_and_contextual_ads(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ads.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            init_db,
            upsert_user,
            create_author_profile,
            get_author_profile,
            create_book,
            set_book_options,
            set_book_publication_status,
            list_contextual_book_ads,
            get_book_options,
            get_reader_ad_settings,
        )
        await init_db()
        user = await upsert_user(9101, "author_ads", "Author Ads")
        await create_author_profile(user["id"], "Автор рекламы", "Описание", "RU", True)
        profile = await get_author_profile(user["id"])
        source_id = await create_book(profile["id"], "Источник", "Описание книги", "16+", "writing", False, "free", 0)
        promoted_id = await create_book(profile["id"], "Похожая", "Описание книги", "16+", "writing", False, "free", 0)
        await set_book_options(source_id, "genres", ["drama", "dark_horror", "detective"])
        await set_book_options(source_id, "tropes", ["plot_twists", "dark_secret"])
        await set_book_options(promoted_id, "genres", ["dark_horror", "detective"])
        await set_book_options(promoted_id, "tropes", ["plot_twists"])
        await set_book_publication_status(promoted_id, "published")
        options = await get_book_options(source_id)
        assert "Тёмный хоррор" in options["genres"]
        ads = await list_contextual_book_ads(source_id)
        assert ads and ads[0]["id"] == promoted_id
        settings = await get_reader_ad_settings()
        assert settings["enabled"] is True

    import asyncio
    asyncio.run(scenario())


def test_reader_progress_bookmark_review_comments(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "reader.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            add_comment,
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            get_bookmark,
            get_reading_progress,
            get_user_review,
            init_db,
            list_comments_for_chapter,
            list_reviews_for_book,
            list_user_bookmarks,
            save_reading_progress,
            set_bookmark,
            upsert_review,
            upsert_user,
            user_can_access_chapter,
        )
        await init_db()
        author_user = await upsert_user(9201, "stage7_author", "Stage7 Author")
        reader = await upsert_user(9202, "stage7_reader", "Stage7 Reader")
        await create_author_profile(author_user["id"], "Автор", "Описание", "RU", True)
        profile = await get_author_profile(author_user["id"])
        book_id = await create_book(profile["id"], "Книга", "Описание", "16+", "writing", False, "free", 0)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 300, is_free=True, price_stars=0)
        assert await user_can_access_chapter(reader["id"], chapter_id)
        await save_reading_progress(reader["id"], chapter_id, 42)
        assert await get_reading_progress(reader["id"], chapter_id) == 42
        await set_bookmark(reader["id"], book_id, "favorite")
        assert (await get_bookmark(reader["id"], book_id))["status"] == "favorite"
        assert len(await list_user_bookmarks(reader["id"])) == 1
        await upsert_review(reader["id"], book_id, 5, "Отличная книга")
        assert (await get_user_review(reader["id"], book_id))["rating"] == 5
        assert len(await list_reviews_for_book(book_id)) == 1
        await add_comment(reader["id"], chapter_id, "Комментарий")
        assert len(await list_comments_for_chapter(chapter_id)) == 1

    asyncio.run(scenario())


def test_stage8_bonus_ads_promo_and_moderation(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stage8.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "stage8.sqlite3")

    async def scenario():
        from app.db import (
            add_comment,
            add_manual_chapter,
            claim_daily_bonus,
            create_ad_campaign,
            create_author_profile,
            create_book,
            create_promo_code,
            get_ad_campaign,
            get_author_profile,
            get_bonus_balance,
            get_comment_for_moderation,
            get_promo_code,
            get_review_for_moderation,
            init_db,
            list_active_ad_campaigns,
            list_author_ad_campaigns,
            list_author_promo_codes,
            list_moderation_comments,
            list_moderation_reviews,
            set_book_options,
            set_book_publication_status,
            set_comment_status,
            set_review_status,
            upsert_review,
            upsert_user,
        )
        await init_db()
        author_user = await upsert_user(9301, "stage8_author", "Stage8 Author")
        reader = await upsert_user(9302, "stage8_reader", "Stage8 Reader")
        received, amount, balance = await claim_daily_bonus(reader["id"])
        assert received is True
        assert balance == amount
        received_again, _, same_balance = await claim_daily_bonus(reader["id"])
        assert received_again is False
        assert same_balance == await get_bonus_balance(reader["id"])

        await create_author_profile(author_user["id"], "Автор", "Описание", "RU", True)
        profile = await get_author_profile(author_user["id"])
        book_id = await create_book(profile["id"], "Книга", "Описание", "16+", "writing", False, "free", 0)
        await set_book_options(book_id, "genres", ["detective", "dark_horror"])
        await set_book_options(book_id, "tropes", ["plot_twists"])
        await set_book_publication_status(book_id, "published")
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 300, is_free=True, price_stars=0)

        campaign_id = await create_ad_campaign(author_user["id"], book_id, "Тест рекламы", "reader_both", 100)
        assert (await get_ad_campaign(campaign_id))["status"] == "running"
        assert len(await list_author_ad_campaigns(author_user["id"])) == 1
        assert len(await list_active_ad_campaigns()) == 1

        promo_id = await create_promo_code(author_user["id"], book_id, "START50", 50, 10)
        assert promo_id > 0
        assert (await get_promo_code("start50"))["discount_percent"] == 50
        assert len(await list_author_promo_codes(author_user["id"])) == 1

        await upsert_review(reader["id"], book_id, 4, "Хороший отзыв")
        reviews = await list_moderation_reviews()
        assert reviews and reviews[0]["rating"] == 4
        review_id = reviews[0]["id"]
        assert (await get_review_for_moderation(review_id))["book_title"] == "Книга"
        await set_review_status(review_id, "hidden")
        assert len(await list_moderation_reviews()) == 0

        comment_id = await add_comment(reader["id"], chapter_id, "Комментарий")
        assert (await get_comment_for_moderation(comment_id))["chapter_title"] == "Глава 1"
        assert len(await list_moderation_comments()) == 1
        await set_comment_status(comment_id, "hidden")
        assert len(await list_moderation_comments()) == 0

    import asyncio
    asyncio.run(scenario())


def test_stage10_author_payout_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stage10.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "stage10.sqlite3")

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_author_payout_request,
            create_book,
            create_paid_purchase,
            get_author_finance_summary,
            get_author_profile,
            get_payout_request,
            init_db,
            list_author_payout_requests,
            list_payout_requests,
            set_author_payout_method,
            set_payout_request_status,
            set_setting,
            upsert_user,
        )
        await init_db()
        await set_setting("hold_days_default", "0")
        await set_setting("payout_min_stars", "1")
        author_user = await upsert_user(9401, "stage10_author", "Stage10 Author")
        reader = await upsert_user(9402, "stage10_reader", "Stage10 Reader")
        admin = await upsert_user(9403, "stage10_admin", "Stage10 Admin")
        await create_author_profile(author_user["id"], "Автор выплат", "Описание", "RU", True)
        profile = await get_author_profile(author_user["id"])
        book_id = await create_book(profile["id"], "Платная книга", "Описание", "16+", "writing", False, "chapters", 10)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 300, is_free=False, price_stars=10)
        await create_paid_purchase(user_id=reader["id"], payload=f"vox:chapter:{chapter_id}", amount_stars=10, telegram_payment_charge_id="charge_stage10")
        summary = await get_author_finance_summary(author_user["id"])
        assert summary["available"] == 8
        await set_author_payout_method(author_user["id"], "TON", "TON: EQ_TEST_WALLET")
        payout_id = await create_author_payout_request(author_user["id"])
        assert payout_id > 0
        summary_requested = await get_author_finance_summary(author_user["id"])
        assert summary_requested["requested"] == 8
        assert len(await list_author_payout_requests(author_user["id"])) == 1
        assert len(await list_payout_requests("new")) == 1
        assert (await get_payout_request(payout_id))["amount_stars"] == 8
        assert await set_payout_request_status(payout_id, "approved", admin["id"], "ok")
        assert await set_payout_request_status(payout_id, "paid", admin["id"], "paid")
        summary_paid = await get_author_finance_summary(author_user["id"])
        assert summary_paid["paid"] == 8

    import asyncio
    asyncio.run(scenario())



def test_stage11_legal_docs_present():
    from app.legal_texts import LEGAL_DOCS, REQUIRED_FOR_AUTHOR, REQUIRED_ON_START
    assert "terms" in LEGAL_DOCS
    assert "privacy" in REQUIRED_ON_START
    assert "authors" in REQUIRED_FOR_AUTHOR
    assert len(LEGAL_DOCS["refunds"].body) > 200


def test_stage11_legal_acceptance(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "legal.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "legal.sqlite3")

    async def scenario():
        from app.db import accept_legal_document, get_legal_acceptances, has_accepted_legal_document, init_db, upsert_user
        await init_db()
        user = await upsert_user(9501, "legal_user", "Legal User")
        await accept_legal_document(user["id"], "terms", "2026-07-01")
        assert await has_accepted_legal_document(user["id"], "terms", "2026-07-01")
        rows = await get_legal_acceptances(user["id"])
        assert rows and rows[0]["doc_code"] == "terms"

    import asyncio
    asyncio.run(scenario())


def test_stage12_diagnostics_contains_core_checks():
    from app.services.diagnostics import collect_diagnostics, diagnostics_summary
    items = collect_diagnostics()
    codes = {item.code for item in items}
    assert "bot_token" in codes
    assert "webapp_url" in codes
    assert "database_path" in codes
    summary = diagnostics_summary()
    assert summary["total"] >= 8


def test_final_docs_exist():
    from pathlib import Path
    for name in ["FINAL_START_GUIDE.md", "INTERFACE_MAP.md", "FINAL_TEST_PLAN.md"]:
        path = Path("docs") / name
        assert path.exists()
        assert len(path.read_text(encoding="utf-8")) > 500
