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


def test_v173_author_and_owner_dashboards(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "dashboards.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "dashboards.sqlite3")

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_dashboard_stats,
            get_author_profile,
            get_owner_today_stats,
            init_db,
            list_catalog_books,
            set_book_publication_status,
            upsert_user,
        )
        await init_db()
        author_user = await upsert_user(9701, "dashboard_author", "Dashboard Author")
        await create_author_profile(author_user["id"], "Автор витрины", "", "", True)
        profile = await get_author_profile(author_user["id"])
        draft_id = await create_book(profile["id"], "Черновик", "", "16+", "writing", False, "free", 0)
        published_id = await create_book(profile["id"], "Опубликована", "", "12+", "writing", False, "free", 0)
        await add_manual_chapter(published_id, "Глава 1", "текст " * 50)
        await set_book_publication_status(published_id, "published")
        stats = await get_author_dashboard_stats(author_user["id"])
        assert stats["books_total"] == 2
        assert stats["books_draft"] == 1
        assert stats["books_published"] == 1
        assert stats["chapters"] == 1
        public_books = await list_catalog_books(include_drafts=False)
        assert [row["id"] for row in public_books] == [published_id]
        assert all(row["id"] != draft_id for row in public_books)
        owner = await get_owner_today_stats()
        assert owner["new_users"] >= 1
        assert owner["new_books"] >= 2

    import asyncio
    asyncio.run(scenario())


def test_v173_promo_card_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "promo_card.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "promo_card.sqlite3")

    async def scenario():
        from app.db import (
            create_author_profile,
            create_book,
            create_promo_code,
            get_author_profile,
            get_author_promo_code,
            init_db,
            set_author_promo_status,
            upsert_user,
        )
        await init_db()
        user = await upsert_user(9702, "promo_author", "Promo Author")
        await create_author_profile(user["id"], "Промо Автор", "", "", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(profile["id"], "Промокнига", "", "12+", "writing", False, "free", 0)
        promo_id = await create_promo_code(user["id"], book_id, "WELCOME25", 25, 50)
        promo = await get_author_promo_code(user["id"], promo_id)
        assert promo and promo["status"] == "active"
        assert await set_author_promo_status(user["id"], promo_id, "paused")
        promo = await get_author_promo_code(user["id"], promo_id)
        assert promo["status"] == "paused"

    import asyncio
    asyncio.run(scenario())


def test_user_visible_handlers_do_not_expose_service_variable_names():
    from pathlib import Path
    visible_files = [
        Path("app/handlers/start.py"),
        Path("app/handlers/author.py"),
        Path("app/handlers/payments.py"),
        Path("app/handlers/moderation.py"),
        Path("app/handlers/legal.py"),
    ]
    forbidden = ("CHANNEL_ID:", "WEBAPP_URL:", "BOT_TOKEN:")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in visible_files)
    for token in forbidden:
        assert token not in combined


def test_v174_public_navigation_library_and_similar(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "v174.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "v174.sqlite3")

    async def scenario():
        from app.db import (
            add_audio_chapter,
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_adjacent_audio_chapters,
            get_adjacent_chapters,
            get_author_profile,
            init_db,
            list_audio_chapters_for_book,
            list_catalog_books,
            list_chapters_for_book,
            list_similar_books,
            list_user_bookmarks,
            list_user_continue_listening,
            list_user_continue_reading,
            save_listening_progress,
            save_reading_progress,
            set_audio_chapter_status,
            set_book_options,
            set_book_publication_status,
            set_bookmark,
            set_chapter_status,
            upsert_user,
        )
        await init_db()
        author = await upsert_user(9801, "v174_author", "V174 Author")
        reader = await upsert_user(9802, "v174_reader", "V174 Reader")
        await create_author_profile(author["id"], "Автор", "", "", True)
        profile = await get_author_profile(author["id"])

        book_id = await create_book(profile["id"], "Основная", "Описание", "16+", "writing", False, "free", 0)
        similar_id = await create_book(profile["id"], "Похожая", "Описание", "16+", "writing", False, "free", 0)
        hidden_id = await create_book(profile["id"], "Скрытая", "Описание", "16+", "writing", False, "free", 0)
        await set_book_options(book_id, "genres", ["detective", "dark_horror"])
        await set_book_options(similar_id, "genres", ["detective"])
        await set_book_publication_status(book_id, "published")
        await set_book_publication_status(similar_id, "published")

        chapter_1 = await add_manual_chapter(book_id, "Глава 1", "текст " * 100, True, 0)
        chapter_2 = await add_manual_chapter(book_id, "Глава 2", "черновик " * 100, True, 0)
        chapter_3 = await add_manual_chapter(book_id, "Глава 3", "текст " * 100, False, 5)
        await set_chapter_status(chapter_1, "published")
        await set_chapter_status(chapter_3, "published")

        public_chapters = await list_chapters_for_book(book_id, published_only=True)
        assert [row["id"] for row in public_chapters] == [chapter_1, chapter_3]
        adjacent = await get_adjacent_chapters(chapter_1)
        assert adjacent["previous"] is None
        assert adjacent["next"]["id"] == chapter_3

        audio_1 = await add_audio_chapter(book_id, "Аудио 1", None, None, 600, "Диктор", None, "audio/mpeg", 0, True, 0)
        audio_2 = await add_audio_chapter(book_id, "Аудио 2", None, None, 700, "Диктор", None, "audio/mpeg", 0, True, 0)
        await set_audio_chapter_status(audio_1, "published")
        await set_audio_chapter_status(audio_2, "published")
        assert len(await list_audio_chapters_for_book(book_id, published_only=True)) == 2
        assert (await get_adjacent_audio_chapters(audio_1))["next"]["id"] == audio_2

        await save_reading_progress(reader["id"], chapter_1, 44)
        await save_listening_progress(reader["id"], audio_1, 120)
        await set_bookmark(reader["id"], book_id, "favorite")
        await set_bookmark(reader["id"], hidden_id, "reading")
        reading = await list_user_continue_reading(reader["id"])
        listening = await list_user_continue_listening(reader["id"])
        bookmarks = await list_user_bookmarks(reader["id"], published_only=True)
        assert reading and reading[0]["chapter_id"] == chapter_1
        assert listening and listening[0]["audio_chapter_id"] == audio_1
        assert [row["book_id"] for row in bookmarks] == [book_id]

        catalog = await list_catalog_books()
        main = next(row for row in catalog if row["id"] == book_id)
        assert main["chapters_count"] == 2
        assert main["audio_count"] == 2
        similar = await list_similar_books(book_id)
        assert similar and similar[0]["id"] == similar_id

    import asyncio
    asyncio.run(scenario())


def test_v174_templates_compile_and_have_real_controls():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    env = Environment(loader=FileSystemLoader("templates"), undefined=StrictUndefined)
    names = [
        "base.html", "catalog.html", "book.html", "reader.html", "audio.html",
        "audio_player.html", "library.html", "settings.html", "_macros.html",
    ]
    for name in names:
        env.get_template(name)
    catalog = Path("templates/catalog.html").read_text(encoding="utf-8")
    reader = Path("templates/reader.html").read_text(encoding="utf-8")
    player = Path("templates/audio_player.html").read_text(encoding="utf-8")
    assert "catalogSearch" in catalog
    assert "data-catalog-filter" in catalog
    assert "previous_chapter" in reader and "next_chapter" in reader
    assert "data-sleep-minutes" in player
    combined = "\n".join(Path("templates").glob("*.html").__iter__().__str__() for _ in [])
    assert "раздел в разработке" not in (catalog + reader + player).lower()


def test_v175_chunked_book_upload_roundtrip(tmp_path, monkeypatch):
    import asyncio
    from io import BytesIO
    from starlette.datastructures import UploadFile
    from app.services import chunked_upload

    monkeypatch.setattr(chunked_upload, "UPLOAD_ROOT", tmp_path / "uploads")
    chunked_upload.settings.MAX_BOOK_UPLOAD_MB = 0
    payload = ("Глава 1\n" + "текст " * 200).encode("utf-8")
    meta = chunked_upload.create_upload(user_id=1, book_id=2, filename="book.fb2", total_size=len(payload))

    async def scenario():
        upload = UploadFile(file=BytesIO(payload), filename="part.bin")
        result = await chunked_upload.save_chunk(
            meta["upload_id"], user_id=1, book_id=2, index=0, total_chunks=1, chunk=upload
        )
        assert result["received_chunks"] == 1

    asyncio.run(scenario())
    path, loaded = chunked_upload.assemble_upload(meta["upload_id"], user_id=1, book_id=2, total_chunks=1)
    assert path.read_bytes() == payload
    assert loaded["filename"] == "book.fb2"
    chunked_upload.cleanup_upload(meta["upload_id"])
    assert not (tmp_path / "uploads" / meta["upload_id"]).exists()


def test_v175_web_import_preview_is_scoped(tmp_path, monkeypatch):
    from app.services.book_parser import ParsedChapter
    from app.services import web_import_store

    monkeypatch.setattr(web_import_store, "PREVIEW_ROOT", tmp_path / "previews")
    token = web_import_store.save_web_import_preview(
        [ParsedChapter(1, "Глава 1", "текст " * 100)],
        user_id=10,
        book_id=20,
        original_name="book.fb2",
    )
    chapters, name = web_import_store.load_web_import_preview(token, user_id=10, book_id=20)
    assert len(chapters) == 1
    assert name == "book.fb2"
    foreign, _ = web_import_store.load_web_import_preview(token, user_id=11, book_id=20)
    assert foreign == []
    web_import_store.delete_web_import_preview(token)


def test_v175_author_studio_edit_and_safe_delete(tmp_path):
    import asyncio
    from app.config import settings

    settings.DATABASE_PATH = str(tmp_path / "author_studio.sqlite3")

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            get_book,
            init_db,
            list_author_books_with_counts,
            list_chapters_for_book,
            set_book_publication_status,
            soft_delete_book,
            soft_delete_chapter_for_author,
            update_author_book_fields,
            upsert_user,
        )
        await init_db()
        user = await upsert_user(9901, "studio_author", "Studio Author")
        await create_author_profile(user["id"], "Автор студии", "", "", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(profile["id"], "Старая книга", "Описание", "16+", "writing", False, "free", 0)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 100)
        await set_book_publication_status(book_id, "published")
        assert await update_author_book_fields(book_id, user["id"], {"title": "Новое название", "price_stars": 15, "pricing_type": "whole_book"})
        book = await get_book(book_id)
        assert book["title"] == "Новое название"
        assert book["publication_status"] == "review"
        rows = await list_author_books_with_counts(user["id"])
        assert rows[0]["chapters_count"] == 1
        assert await soft_delete_chapter_for_author(chapter_id, user["id"])
        assert await list_chapters_for_book(book_id) == []
        assert await soft_delete_book(book_id, user["id"])

    asyncio.run(scenario())


def test_v175_author_studio_files_and_no_old_50mb_guard():
    author_template = Path("templates/author.html").read_text(encoding="utf-8")
    author_js = Path("static/js/author.js").read_text(encoding="utf-8")
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    handler = Path("app/handlers/author.py").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert "Кабинет автора" in author_template
    assert "upload/start" in author_js
    assert "/api/author/book/{book_id}/upload/{upload_id}/chunk" in webapp
    assert "Сейчас лимит 50 МБ" not in handler
    assert "python-multipart" in requirements


def test_v176_payment_idempotency_refund_guards_and_payout_transitions(tmp_path):
    import asyncio
    import pytest
    from app.config import settings

    settings.DATABASE_PATH = str(tmp_path / "v176_finance.sqlite3")

    async def scenario():
        from app.db import (
            add_manual_chapter,
            connect,
            create_author_payout_request,
            create_author_profile,
            create_book,
            create_paid_purchase,
            create_refund_request,
            finalize_refund,
            get_author_profile,
            has_purchase_access,
            init_db,
            set_author_payout_method,
            set_payout_request_status,
            set_setting,
            upsert_user,
        )
        await init_db()
        await set_setting("hold_days_default", "0")
        await set_setting("payout_min_stars", "1")
        author_user = await upsert_user(9961, "safe_author", "Safe Author")
        reader = await upsert_user(9962, "safe_reader", "Safe Reader")
        stranger = await upsert_user(9963, "stranger", "Stranger")
        admin = await upsert_user(9964, "finance_admin", "Finance Admin")
        await create_author_profile(author_user["id"], "Надёжный автор", "", "", True)
        profile = await get_author_profile(author_user["id"])
        book_id = await create_book(profile["id"], "Проверка оплаты", "Описание", "16+", "writing", False, "chapters", 10)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 200, False, 10)

        purchase_id = await create_paid_purchase(
            user_id=reader["id"], payload=f"vox:chapter:{chapter_id}", amount_stars=10,
            telegram_payment_charge_id="unique_charge_v176",
        )
        duplicate_id = await create_paid_purchase(
            user_id=reader["id"], payload=f"vox:chapter:{chapter_id}", amount_stars=10,
            telegram_payment_charge_id="unique_charge_v176",
        )
        assert duplicate_id == purchase_id
        async with connect() as db:
            cur = await db.execute("SELECT COUNT(*) FROM purchases WHERE telegram_payment_charge_id='unique_charge_v176'")
            assert (await cur.fetchone())[0] == 1
            cur = await db.execute("SELECT COUNT(*) FROM author_ledger WHERE purchase_id=?", (purchase_id,))
            assert (await cur.fetchone())[0] == 1

        with pytest.raises(ValueError):
            await create_paid_purchase(
                user_id=reader["id"], payload=f"vox:chapter:{chapter_id}", amount_stars=9,
                telegram_payment_charge_id="wrong_amount_v176",
            )
        with pytest.raises(ValueError):
            await create_refund_request(purchase_id, stranger["id"], "Подробная причина возврата от чужого пользователя")

        refund_id = await create_refund_request(purchase_id, reader["id"], "Книга была приобретена по ошибке, прошу вернуть оплату")
        with pytest.raises(ValueError):
            await create_refund_request(purchase_id, reader["id"], "Повторный запрос на возврат по той же покупке")
        assert await finalize_refund(refund_id, admin["id"])
        assert not await has_purchase_access(reader["id"], chapter_id=chapter_id)

        second_purchase = await create_paid_purchase(
            user_id=reader["id"], payload=f"vox:chapter:{chapter_id}", amount_stars=10,
            telegram_payment_charge_id="second_charge_v176",
        )
        assert second_purchase > 0
        await set_author_payout_method(author_user["id"], "TON", "TON: EQ_V176_TEST_WALLET")
        payout_id = await create_author_payout_request(author_user["id"])
        assert not await set_payout_request_status(payout_id, "paid", admin["id"], "Нельзя платить без одобрения")
        assert await set_payout_request_status(payout_id, "approved", admin["id"], "Проверено")
        assert await set_payout_request_status(payout_id, "paid", admin["id"], "Переведено")

    asyncio.run(scenario())


def test_v176_control_center_files_and_routes():
    from pathlib import Path
    from app.webapp import create_app

    assert Path("templates/control.html").exists()
    assert Path("static/js/control.js").exists()
    app = create_app()
    paths = {route.path for route in app.routes}
    for path in {
        "/control",
        "/api/control/dashboard",
        "/api/control/books",
        "/api/control/refunds",
        "/api/control/payouts",
    }:
        assert path in paths


def test_v176_control_dashboard_owner_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.services.tma_auth import TMAUser
    import app.webapp as webapp_module

    settings.DATABASE_PATH = str(tmp_path / "v176_control.sqlite3")
    settings.OWNER_IDS = "9971"

    async def fake_auth(raw: str):
        from app.services.tma_auth import TMAAuthError
        if not raw:
            raise TMAAuthError("Откройте раздел из Telegram")
        from app.db import upsert_user
        row = await upsert_user(9971, "owner_v176", "Owner V176")
        return TMAUser(int(row["id"]), 9971, "owner_v176", "Owner V176")

    monkeypatch.setattr(webapp_module, "authenticate_init_data", fake_auth)
    with TestClient(webapp_module.create_app()) as client:
        denied = client.get("/api/control/dashboard")
        assert denied.status_code == 401
        response = client.get("/api/control/dashboard", headers={"X-Telegram-Init-Data": "signed"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["role"] == "owner"
        assert "books_review" in payload["queues"]


def test_v176_role_based_miniapp_entries_are_hidden_by_default():
    library = Path("templates/library.html").read_text(encoding="utf-8")
    base = Path("templates/base.html").read_text(encoding="utf-8")
    app_js = Path("static/js/app.js").read_text(encoding="utf-8")
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    assert 'id="authorStudioEntry"' in library and 'href="/author" hidden' in library
    assert 'id="controlCenterEntry"' in library and 'href="/control" hidden' in library
    assert "data.author?.enabled" in app_js
    assert "data.control?.enabled" in app_js
    assert '"author": {' in webapp and '"control": {' in webapp
    assert "<span>Настройки</span>" in base
    assert "<span>Стиль</span>" not in base


def test_v177_notification_messages_are_clean():
    from app.services.notifications import (
        book_moderation_message,
        complaint_message,
        content_hidden_message,
        payout_message,
        refund_message,
    )

    messages = [
        book_moderation_message("Тестовая книга", "published"),
        book_moderation_message("Тестовая книга", "rejected"),
        content_hidden_message("comment", "Тестовая книга", "Глава 1"),
        content_hidden_message("review", "Тестовая книга"),
        complaint_message("pending"),
        complaint_message("closed"),
        refund_message("refunded", 25),
        refund_message("rejected", 25, "Запрос не прошёл проверку"),
        payout_message("approved", 100),
        payout_message("paid", 100),
        payout_message("frozen", 100),
        payout_message("new", 100),
        payout_message("rejected", 100, "Нужно уточнить реквизиты"),
    ]
    assert all(message.strip() for message in messages)
    combined = "\n".join(messages)
    for forbidden in ("BOT_TOKEN", "DATABASE_PATH", "PROJECT_VERSION", "traceback", "exception"):
        assert forbidden.lower() not in combined.lower()


def test_v177_notifications_respect_user_preference(tmp_path, monkeypatch):
    import asyncio
    from app.config import settings
    import app.services.notifications as notifications

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "notifications.sqlite3"))
    monkeypatch.setattr(settings, "BOT_TOKEN", "123456:TEST_TOKEN")

    class ForbiddenBot:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Bot must not be created when notifications are disabled")

    monkeypatch.setattr(notifications, "Bot", ForbiddenBot)

    async def scenario():
        from app.db import init_db, set_user_preference, upsert_user

        await init_db()
        user = await upsert_user(9981, "notify_off", "Notify Off")
        await set_user_preference(user["id"], "notifications", "0")
        result = await notifications.send_user_notification(
            app_user_id=user["id"],
            telegram_id=user["telegram_id"],
            text="Проверка",
        )
        assert result == "disabled"

    asyncio.run(scenario())


def test_v177_notification_targets_resolve_from_database(tmp_path, monkeypatch):
    import asyncio
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "notification_targets.sqlite3"))

    async def scenario():
        from app.db import (
            add_comment,
            add_manual_chapter,
            create_author_profile,
            create_book,
            create_complaint,
            get_author_profile,
            get_book,
            get_comment_for_moderation,
            get_complaint,
            get_review_for_moderation,
            init_db,
            upsert_review,
            upsert_user,
        )

        await init_db()
        author = await upsert_user(9982, "notify_author", "Notify Author")
        reader = await upsert_user(9983, "notify_reader", "Notify Reader")
        await create_author_profile(author["id"], "Автор уведомлений", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(profile["id"], "Книга уведомлений", "", "12+", "writing", False, "free", 0)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 100)
        comment_id = await add_comment(reader["id"], chapter_id, "Комментарий")
        await upsert_review(reader["id"], book_id, 5, "Отзыв")
        review = await get_review_for_moderation(1)
        complaint_id = await create_complaint(reader["id"], "book", str(book_id), "Причина жалобы")

        book = await get_book(book_id)
        comment = await get_comment_for_moderation(comment_id)
        complaint = await get_complaint(complaint_id)
        assert book["author_user_id"] == author["id"]
        assert book["author_telegram_id"] == author["telegram_id"]
        assert comment["telegram_id"] == reader["telegram_id"]
        assert review["telegram_id"] == reader["telegram_id"]
        assert complaint["telegram_id"] == reader["telegram_id"]

    asyncio.run(scenario())


def test_v177_main_menu_hides_miniapp_entries_without_url(monkeypatch):
    from app.config import settings
    from app.keyboards import main_menu

    monkeypatch.setattr(settings, "WEBAPP_URL", "")
    markup = main_menu(is_owner=False, has_admin_access=False, has_author_profile=False)
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert "📚 Читать" not in labels
    assert "🎧 Слушать" not in labels
    assert "⭐ Моё" in labels
    assert "✍️ Автору" in labels


def test_v177_notifications_reuse_active_bot(monkeypatch):
    import asyncio
    from app.config import settings
    from app.services.notifications import send_user_notification

    monkeypatch.setattr(settings, "BOT_TOKEN", "123456:TEST_TOKEN")

    class ActiveBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    async def scenario():
        bot = ActiveBot()
        result = await send_user_notification(
            app_user_id=None,
            telegram_id=9984,
            text="Готово",
            bot=bot,
        )
        assert result == "sent"
        assert bot.sent == [{"chat_id": 9984, "text": "Готово", "disable_web_page_preview": True}]

    asyncio.run(scenario())


def test_catalog_books_include_premium_card_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "premium_catalog.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "premium_catalog.sqlite3")

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            list_catalog_books,
            set_book_options,
            set_book_publication_status,
            set_chapter_status,
            upsert_user,
        )
        await init_db()
        user = await upsert_user(99001, "premium_catalog_author", "Premium Catalog")
        await create_author_profile(user["id"], "Автор витрины", "Описание", "RU", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(profile["id"], "Книга витрины", "Описание", "16+", "writing", False, "chapters", 30)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 250, is_free=True, price_stars=0)
        await set_chapter_status(chapter_id, "published")
        await set_book_options(book_id, "genres", ["dark_horror", "detective"])
        await set_book_publication_status(book_id, "published")
        rows = await list_catalog_books()
        row = rows[0]
        assert row["first_chapter_id"] == chapter_id
        assert row["chapters_count"] == 1
        assert row["free_chapters_count"] == 1
        assert "Тёмный хоррор" in row["genre_labels"]
        assert "Детектив" in row["genre_labels"]

    asyncio.run(scenario())


def test_v179_notification_categories_and_duplicate_protection(tmp_path, monkeypatch):
    import asyncio
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "v179_notifications.sqlite3"))
    monkeypatch.setattr(settings, "BOT_TOKEN", "123456:TEST_TOKEN")

    class ActiveBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    async def scenario():
        from app.db import (
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            set_book_publication_status,
            set_bookmark,
            set_user_preference,
            upsert_user,
        )
        from app.services.notifications import notify_book_followers

        await init_db()
        author = await upsert_user(17901, "stage7_author", "Stage 7 Author")
        reader = await upsert_user(17902, "stage7_reader", "Stage 7 Reader")
        await create_author_profile(author["id"], "Автор", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(profile["id"], "Книга уведомлений", "", "12+", "writing", False, "free", 0)
        await set_book_publication_status(book_id, "published")
        await set_bookmark(reader["id"], book_id, "reading")

        bot = ActiveBot()
        await set_user_preference(reader["id"], "notifications_chapters", "0")
        disabled = await notify_book_followers(
            book_id=book_id,
            event_key="chapter:701:published",
            category="chapters",
            text="Новая глава",
            bot=bot,
        )
        assert disabled["disabled"] == 1
        assert not bot.sent

        await set_user_preference(reader["id"], "notifications_chapters", "1")
        sent = await notify_book_followers(
            book_id=book_id,
            event_key="chapter:702:published",
            category="chapters",
            text="Новая глава",
            bot=bot,
        )
        assert sent["sent"] == 1
        assert len(bot.sent) == 1

        duplicate = await notify_book_followers(
            book_id=book_id,
            event_key="chapter:702:published",
            category="chapters",
            text="Новая глава",
            bot=bot,
        )
        assert duplicate["duplicate"] == 1
        assert len(bot.sent) == 1

        discount = await notify_book_followers(
            book_id=book_id,
            event_key="discount:77:created",
            category="discounts",
            text="Скидка",
            bot=bot,
        )
        assert discount["sent"] == 1
        assert len(bot.sent) == 2
        assert all(item["chat_id"] == reader["telegram_id"] for item in bot.sent)

    asyncio.run(scenario())


def test_v179_publish_book_content_releases_prepared_chapters(tmp_path, monkeypatch):
    import asyncio
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "v179_publish.sqlite3"))

    async def scenario():
        from app.db import (
            add_audio_chapter,
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_audio_chapter,
            get_author_profile,
            get_chapter,
            init_db,
            publish_book_content,
            set_book_publication_status,
            upsert_user,
        )

        await init_db()
        author = await upsert_user(17911, "publish_author", "Publish Author")
        await create_author_profile(author["id"], "Автор", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(profile["id"], "Готовая книга", "", "12+", "writing", False, "free", 0)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "текст " * 100)
        audio_id = await add_audio_chapter(book_id, "Аудио 1", None, None, 120, None, None, "audio/mpeg", 0, True, 0)
        assert (await get_chapter(chapter_id))["status"] == "draft"
        assert (await get_audio_chapter(audio_id))["status"] == "draft"

        await set_book_publication_status(book_id, "published")
        result = await publish_book_content(book_id)
        assert result == {"chapters": 1, "audio": 1}
        assert (await get_chapter(chapter_id))["status"] == "published"
        assert (await get_audio_chapter(audio_id))["status"] == "published"

    asyncio.run(scenario())


def test_v179_settings_show_and_save_notification_categories():
    from pathlib import Path

    template = Path("templates/settings.html").read_text(encoding="utf-8")
    app_js = Path("static/js/app.js").read_text(encoding="utf-8")
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    keyboards = Path("app/keyboards.py").read_text(encoding="utf-8")

    for label in ("Новые главы", "Новые аудиоглавы", "Скидки"):
        assert label in template
    for key in ("notificationChapters", "notificationAudio", "notificationDiscounts"):
        assert key in app_js
    for db_key in ("notifications_chapters", "notifications_audio", "notifications_discounts"):
        assert db_key in app_js
        assert db_key in webapp
        assert db_key in keyboards
    assert '@app.patch("/api/preferences")' in webapp
    assert '@app.delete("/api/preferences")' in webapp


def _telegram_init_data(bot_token: str, telegram_id: int, *, auth_date: int | None = None, username: str = "reader") -> str:
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    timestamp = int(time.time()) if auth_date is None else int(auth_date)
    pairs = {
        "auth_date": str(timestamp),
        "query_id": f"query-{telegram_id}",
        "user": json.dumps(
            {"id": telegram_id, "first_name": "Тест", "username": username},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_v180_owner_only_permissions_cannot_be_delegated(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "permissions.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "permissions.sqlite3")

    async def scenario():
        import pytest
        from app.db import add_admin, connect, get_admin_permissions, init_db, set_permission, upsert_user, utc_now

        await init_db()
        owner = await upsert_user(9801, "owner", "Владелец")
        admin = await upsert_user(9802, "admin", "Администратор")
        await add_admin(admin["id"], owner["id"])
        await set_permission(admin["id"], "mod_books", True)
        assert await get_admin_permissions(admin["id"]) == {"mod_books"}
        with pytest.raises(ValueError):
            await set_permission(admin["id"], "payouts", True)

        # Даже устаревшая запись из старой базы не должна вернуть владельческое право.
        async with connect() as db:
            cur = await db.execute("SELECT id FROM admin_staff WHERE user_id=?", (admin["id"],))
            staff = await cur.fetchone()
            await db.execute(
                "INSERT INTO admin_permissions(admin_id, permission_code, allowed, updated_at) VALUES(?, 'payouts', 1, ?)",
                (staff["id"], utc_now()),
            )
            await db.commit()
        assert await get_admin_permissions(admin["id"]) == {"mod_books"}

    import asyncio
    asyncio.run(scenario())

    from app.keyboards import admin_card_menu
    markup = admin_card_menu(1, set())
    labels = {button.text for row in markup.inline_keyboard for button in row}
    assert not any("Выплаты авторам" in label for label in labels)
    assert not any("Изменение комиссии" in label for label in labels)
    assert not any("Настройки платформы" in label for label in labels)


def test_v180_tma_rejects_bad_time_and_blocked_user(tmp_path, monkeypatch):
    import asyncio
    import time
    import pytest

    token = "123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI"
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "auth.sqlite3"))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(tmp_path / "auth.sqlite3")
    settings.BOT_TOKEN = token
    settings.OWNER_IDS = "9801"

    from app.services.tma_auth import TMAAuthError, _validate_init_data_raw, authenticate_init_data

    valid = _telegram_init_data(token, 9803)
    assert _validate_init_data_raw(valid, token)["auth_date"]

    future = _telegram_init_data(token, 9803, auth_date=int(time.time()) + 3600)
    with pytest.raises(TMAAuthError):
        _validate_init_data_raw(future, token)

    stale = _telegram_init_data(token, 9803, auth_date=int(time.time()) - 90000)
    with pytest.raises(TMAAuthError):
        _validate_init_data_raw(stale, token)

    async def scenario():
        from app.db import init_db, set_user_blocked, upsert_user

        await init_db()
        reader = await upsert_user(9803, "reader", "Читатель")
        await set_user_blocked(reader["id"], True)
        with pytest.raises(TMAAuthError):
            await authenticate_init_data(valid)

    asyncio.run(scenario())


def test_v180_cross_flow_public_private_and_paid_content(tmp_path, monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient

    token = "123456:abcdefghijklmnopqrstuvwxyzABCDEFGHI"
    db_path = tmp_path / "e2e.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    from app.config import get_settings, settings
    get_settings.cache_clear()
    settings.DATABASE_PATH = str(db_path)
    settings.BOT_TOKEN = token
    settings.OWNER_IDS = "9810"
    settings.BOT_USERNAME = "voxlyra_test_bot"
    settings.WEBAPP_URL = "https://voxlyra.example"
    settings.CHANNEL_ID = ""

    ids: dict[str, int] = {}

    async def seed():
        from app.db import (
            add_admin,
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            set_book_publication_status,
            set_chapter_status,
            set_permission,
            upsert_user,
        )

        await init_db()
        owner = await upsert_user(9810, "owner", "Владелец")
        author_user = await upsert_user(9811, "author", "Автор")
        other_author = await upsert_user(9812, "other", "Другой автор")
        reader = await upsert_user(9813, "reader", "Читатель")
        moderator = await upsert_user(9814, "moderator", "Модератор")
        await add_admin(moderator["id"], owner["id"])
        await set_permission(moderator["id"], "mod_books", True)
        await create_author_profile(author_user["id"], "Автор", "Описание", "RU", True)
        await create_author_profile(other_author["id"], "Другой", "Описание", "RU", True)
        profile = await get_author_profile(author_user["id"])
        book_id = await create_book(profile["id"], "Закрытая книга", "Описание", "16+", "finished", False, "chapters", 9)
        secret = "СЕКРЕТНЫЙ_ТЕКСТ_ПЛАТНОЙ_ГЛАВЫ " + "текст " * 100
        chapter_id = await add_manual_chapter(book_id, "Платная глава", secret, is_free=False, price_stars=9)
        await set_book_publication_status(book_id, "published")
        await set_chapter_status(chapter_id, "published")
        ids.update(
            owner=owner["id"],
            author=author_user["id"],
            other=other_author["id"],
            reader=reader["id"],
            moderator=moderator["id"],
            book=book_id,
            chapter=chapter_id,
        )

    asyncio.run(seed())

    from app.webapp import create_app
    app = create_app()
    owner_header = {"X-Telegram-Init-Data": _telegram_init_data(token, 9810, username="owner")}
    author_header = {"X-Telegram-Init-Data": _telegram_init_data(token, 9811, username="author")}
    other_header = {"X-Telegram-Init-Data": _telegram_init_data(token, 9812, username="other")}
    reader_header = {"X-Telegram-Init-Data": _telegram_init_data(token, 9813, username="reader")}
    moderator_header = {"X-Telegram-Init-Data": _telegram_init_data(token, 9814, username="moderator")}

    with TestClient(app) as client:
        for path in ["/", "/catalog", "/audio", "/settings", "/library", "/author", "/control"]:
            assert client.get(path).status_code == 200
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        readiness = client.get("/readiness")
        assert readiness.status_code == 200
        assert set(readiness.json()) == {"ok"}

        html = client.get("/").text
        assert settings.PROJECT_VERSION not in html
        assert "BOT_TOKEN" not in html
        assert "OWNER_IDS" not in html
        assert "DATABASE_PATH" not in html

        reader_html = client.get(f"/reader/{ids['chapter']}").text
        assert "СЕКРЕТНЫЙ_ТЕКСТ_ПЛАТНОЙ_ГЛАВЫ" not in reader_html
        assert client.get(f"/api/reader/{ids['chapter']}").status_code == 401

        closed = client.get(f"/api/reader/{ids['chapter']}", headers=reader_header)
        assert closed.status_code == 200
        assert closed.json()["allowed"] is False
        assert closed.json()["chapter"]["text"] == ""

        owner_dashboard = client.get("/api/control/dashboard", headers=owner_header)
        assert owner_dashboard.status_code == 200
        assert owner_dashboard.json()["role"] == "owner"

        moderator_dashboard = client.get("/api/control/dashboard", headers=moderator_header)
        assert moderator_dashboard.status_code == 200
        assert moderator_dashboard.json()["permissions"] == ["mod_books"]
        assert client.get("/api/control/payouts", headers=moderator_header).status_code == 403

        # Другой автор не может менять чужую книгу.
        denied = client.patch(
            f"/api/author/book/{ids['book']}",
            headers=other_header,
            json={"title": "Чужое изменение"},
        )
        assert denied.status_code == 404
        own = client.get(f"/api/author/book/{ids['book']}", headers=author_header)
        assert own.status_code == 200

        async def buy():
            from app.db import create_paid_purchase
            await create_paid_purchase(
                user_id=ids["reader"],
                payload=f"vox:chapter:{ids['chapter']}",
                amount_stars=9,
                telegram_payment_charge_id="stage8-e2e-charge",
            )

        asyncio.run(buy())
        opened = client.get(f"/api/reader/{ids['chapter']}", headers=reader_header)
        assert opened.status_code == 200
        assert opened.json()["allowed"] is True
        assert "СЕКРЕТНЫЙ_ТЕКСТ_ПЛАТНОЙ_ГЛАВЫ" in opened.json()["chapter"]["text"]

        async def block_reader():
            from app.db import set_user_blocked
            await set_user_blocked(ids["reader"], True)

        asyncio.run(block_reader())
        blocked = client.get("/api/me", headers=reader_header)
        assert blocked.status_code == 401
        assert "ограничен" in blocked.json()["detail"].lower()


def test_v180_bot_registers_blocked_user_guard():
    source = Path("app/bot.py").read_text(encoding="utf-8")
    assert "BlockedUserMiddleware" in source
    assert "dp.message.outer_middleware" in source
    assert "dp.callback_query.outer_middleware" in source
    assert "dp.pre_checkout_query.outer_middleware" in source


def test_v180_no_public_english_media_errors_or_version_query():
    web = Path("app/webapp.py").read_text(encoding="utf-8")
    base = Path("templates/base.html").read_text(encoding="utf-8")
    assert "Audio not found" not in web
    assert "Audio requires purchase" not in web
    assert "Audio file not found" not in web
    assert "project_version" not in base


def test_v180_hidden_elements_cannot_be_overridden_by_component_css():
    css = Path("static/css/style.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css
    settings_template = Path("templates/settings.html").read_text(encoding="utf-8")
    assert "Настройки — Вокслира" in settings_template


def test_v180_owner_handler_definitions_are_unique():
    import ast
    from collections import Counter

    tree = ast.parse(Path("app/handlers/owner.py").read_text(encoding="utf-8"))
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    duplicates = {name for name, count in Counter(names).items() if count > 1}
    assert duplicates == set()
