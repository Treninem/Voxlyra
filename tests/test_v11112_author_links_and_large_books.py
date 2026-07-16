import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")

ROOT = Path(__file__).resolve().parents[1]


def test_channel_book_link_launches_main_mini_app(monkeypatch):
    from app.services import publication

    monkeypatch.setattr(publication.settings, "BOT_USERNAME", "@VoxlyraBot")
    monkeypatch.setattr(publication.settings, "WEBAPP_URL", "https://voxlyra.example")

    assert publication._book_link(27) == "https://t.me/VoxlyraBot?startapp=book_27"


def test_large_author_book_summary_does_not_return_all_chapter_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "large-author-book.sqlite3"))
    from app.config import get_settings
    from app import db as db_module

    get_settings.cache_clear()
    monkeypatch.setattr(db_module, "settings", get_settings())
    db_module._INITIALIZED_DATABASES.clear()

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            list_author_chapter_summaries,
            upsert_user,
        )

        await init_db()
        user = await upsert_user(91112001, "large_author", "Large Author")
        await create_author_profile(user["id"], "Большой автор", "", "RU", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(profile["id"], "Тысяча глав", "", "16+", "finished", False, "free", 0)
        long_text = "Полноразмерный текст главы. " * 400
        for number in range(1, 121):
            await add_manual_chapter(book_id, f"Глава {number}", long_text, is_free=True, price_stars=0)

        rows = await list_author_chapter_summaries(book_id)
        assert len(rows) == 120
        assert "text" not in rows[0].keys()
        assert {"id", "book_id", "number", "title", "status"}.issubset(rows[0].keys())

    asyncio.run(scenario())


def test_author_ui_lazy_loads_one_chapter_and_paginates_large_lists():
    author_js = (ROOT / "static" / "js" / "author.js").read_text(encoding="utf-8")
    webapp = (ROOT / "app" / "webapp.py").read_text(encoding="utf-8")

    assert "AUTHOR_CHAPTERS_PER_PAGE = 100" in author_js
    assert "/api/author/chapter/${Number(chapter.id)}" in author_js
    assert 'data-author-chapter-page' in author_js
    assert '@app.get("/api/author/chapter/{chapter_id}")' in webapp
    assert "list_author_chapter_summaries(book_id)" in webapp


def test_old_web_book_links_are_handed_to_telegram_and_startapp_routes_back_to_book():
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")

    assert "tgWebAppStartParam" in app_js
    assert "unsafe.start_param" in app_js
    assert "?startapp=book_${bookId}" in app_js
    assert "window.location.replace(`/book/${bookId}${window.location.search || ''}${window.location.hash || ''}`)" in app_js
    assert 'meta name="voxlyra-bot-username"' in base



def test_channel_caption_has_no_blue_text_url_and_button_keeps_deep_link(monkeypatch):
    from app.services import publication
    from app.services.channel import build_new_book_post

    monkeypatch.setattr(publication.settings, "BOT_USERNAME", "VoxlyraBot")
    link = publication._book_link(42)
    caption = build_new_book_post(
        title="Книга", author="Автор", genres=["Роман"], age_limit="12+",
        chapters_count=10, has_audio=False, description="Описание",
        pricing_type="free", price_stars=0, book_url=link,
    )
    markup = publication._channel_markup(42)

    assert link not in caption
    assert "Открыть книгу:" not in caption
    assert markup.inline_keyboard[0][0].url == "https://t.me/VoxlyraBot?startapp=book_42"

def test_build_version_is_v11112():
    source = (ROOT / "app" / "build_info.py").read_text(encoding="utf-8")
    assert 'OWNER_BUILD_VERSION = "v1.11.12"' in source
