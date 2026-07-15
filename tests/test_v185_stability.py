from __future__ import annotations

import asyncio
from pathlib import Path


def test_channel_post_uses_real_cover_file_id(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "channel_cover.sqlite3"))
    monkeypatch.setattr(settings, "CHANNEL_ID", "@voxlyra_test")
    monkeypatch.setattr(settings, "WEBAPP_URL", "https://voxlyra.example")

    class FakeBot:
        def __init__(self):
            self.photos = []
            self.messages = []

        async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, parse_mode=None):
            self.photos.append((chat_id, photo, caption, reply_markup, parse_mode))

        async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
            self.messages.append((chat_id, text, reply_markup, parse_mode))

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            publish_book_content,
            set_book_publication_status,
            upsert_user,
        )
        from app.services.publication import post_book_to_channel

        await init_db()
        user = await upsert_user(18501, "cover", "Автор")
        await create_author_profile(user["id"], "Автор", "", "RU", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(
            profile["id"], "Книга с обложкой", "Описание книги", "16+", "finished",
            False, "free", 0, cover_file_id="telegram-cover-file-id",
        )
        await add_manual_chapter(book_id, "Глава 1", "Текст " * 300)
        await set_book_publication_status(book_id, "published")
        await publish_book_content(book_id)

        bot = FakeBot()
        result = await post_book_to_channel(bot, book_id, actor_user_id=user["id"])
        assert result.channel_status == "sent"
        assert bot.photos
        assert bot.photos[0][1] == "telegram-cover-file-id"
        assert f"https://voxlyra.example/book/{book_id}" in bot.photos[0][2]
        assert str(bot.photos[0][4]).lower().endswith("html")
        assert not bot.messages

    asyncio.run(scenario())


def test_moderation_alert_reaches_owner_and_book_moderator(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "moderation.sqlite3"))
    monkeypatch.setattr(settings, "OWNER_IDS", "18510")

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, **kwargs):
            self.sent.append(kwargs)

    async def scenario():
        from app.db import (
            add_admin,
            add_manual_chapter,
            create_author_profile,
            create_book,
            enqueue_book_moderation,
            get_author_profile,
            init_db,
            set_book_publication_status,
            set_permission,
            upsert_user,
        )
        from app.services.moderation_alerts import notify_book_needs_moderation

        await init_db()
        owner = await upsert_user(18510, "owner", "Владелец")
        moderator = await upsert_user(18511, "moderator", "Модератор")
        author = await upsert_user(18512, "author", "Автор")
        await add_admin(moderator["id"], owner["id"])
        await set_permission(moderator["id"], "mod_books", True)
        await create_author_profile(author["id"], "Автор", "", "RU", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(profile["id"], "Книга на проверке", "Описание", "16+", "finished", False, "free", 0)
        await add_manual_chapter(book_id, "Глава 1", "Текст " * 300)
        await set_book_publication_status(book_id, "review")
        reasons = ["Требуется ручная проверка."]
        await enqueue_book_moderation(book_id, reasons)

        bot = FakeBot()
        result = await notify_book_needs_moderation(bot, book_id=book_id, reasons=reasons)
        recipients = {int(item["chat_id"]) for item in bot.sent}
        assert recipients == {18510, 18511}
        assert result == {"sent": 2, "failed": 0}
        assert all("Открыть проверку" in str(item.get("reply_markup")) for item in bot.sent)
        assert all(str(item.get("parse_mode")).lower().endswith("html") for item in bot.sent)

    asyncio.run(scenario())


def test_chapter_number_lookup_and_reader_control(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "chapter_jump.sqlite3"))

    async def prepare():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            publish_book_content,
            set_book_publication_status,
            upsert_user,
        )
        await init_db()
        user = await upsert_user(18520, "jump", "Автор")
        await create_author_profile(user["id"], "Автор", "", "RU", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(profile["id"], "Большая книга", "Описание", "12+", "finished", False, "free", 0)
        first = await add_manual_chapter(book_id, "Глава 1", "Первый текст " * 200)
        second = await add_manual_chapter(book_id, "Глава 2", "Второй текст " * 200)
        await set_book_publication_status(book_id, "published")
        await publish_book_content(book_id)
        return book_id, first, second

    book_id, _, second = asyncio.run(prepare())
    from app.webapp import create_app
    client = TestClient(create_app())
    response = client.get(f"/api/book/{book_id}/chapter-number/2")
    assert response.status_code == 200
    assert response.json()["chapter"]["id"] == second
    assert response.json()["reader_url"] == f"/reader/{second}"

    reader = client.get(f"/reader/{second}")
    assert reader.status_code == 200
    assert 'id="chapterJumpForm"' in reader.text
    assert 'inputmode="numeric"' in reader.text


def test_reader_progress_formula_ignores_page_chrome():
    root = Path(__file__).resolve().parents[1]
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    assert "const startScroll = Math.max(0, contentTop - toolbarHeight - 20)" in script
    assert "const naturalEnd = contentBottom - window.innerHeight" in script
    assert "completionLead" not in script
    assert "openChapterByNumber" in script


def test_cover_is_attempted_in_all_public_cards_and_author_cards():
    root = Path(__file__).resolve().parents[1]
    macros = (root / "templates/_macros.html").read_text(encoding="utf-8")
    app_js = (root / "static/js/app.js").read_text(encoding="utf-8")
    author_js = (root / "static/js/author.js").read_text(encoding="utf-8")
    assert 'src="/media/cover/{{ book.id }}' in macros
    assert "if (item.book_id)" in app_js
    assert 'data-author-cover-id="${Number(book.id)}"' in author_js
    assert "book.cover_path || book.cover_file_id" not in author_js
