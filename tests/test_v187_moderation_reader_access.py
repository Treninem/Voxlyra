from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse


def test_owner_and_book_moderator_can_review_paid_text_and_tts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "v187.sqlite3"))
    monkeypatch.setattr(settings, "OWNER_IDS", "18701")
    monkeypatch.setattr(settings, "TTS_SIGNING_SECRET", "v187-test-secret")

    async def prepare():
        from app.db import (
            add_admin,
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            publish_book_content,
            set_book_publication_status,
            set_permission,
            upsert_user,
        )

        await init_db()
        owner = await upsert_user(18701, "owner", "Владелец")
        moderator = await upsert_user(18702, "moderator", "Модератор")
        admin = await upsert_user(18703, "admin", "Администратор")
        reader = await upsert_user(18704, "reader", "Читатель")
        author = await upsert_user(18705, "author", "Автор")
        await add_admin(moderator["id"], owner["id"])
        await set_permission(moderator["id"], "mod_books", True)
        await add_admin(admin["id"], owner["id"])
        await set_permission(admin["id"], "stats", True)
        await create_author_profile(author["id"], "Автор", "", "RU", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Платная книга", "Описание", "18+", "finished",
            False, "chapters", 10,
        )
        chapter_id = await add_manual_chapter(
            book_id, "Платная глава", "Содержимое платной главы. " * 120,
            is_free=False, price_stars=10,
        )
        await set_book_publication_status(book_id, "published")
        await publish_book_content(book_id)

        review_book_id = await create_book(
            profile["id"], "Книга на проверке", "Описание", "18+", "finished",
            False, "chapters", 10,
        )
        review_chapter_id = await add_manual_chapter(
            review_book_id, "Черновая платная глава", "Текст для ручной проверки. " * 120,
            is_free=False, price_stars=10,
        )
        await set_book_publication_status(review_book_id, "review")
        return {
            "owner": owner,
            "moderator": moderator,
            "admin": admin,
            "reader": reader,
            "chapter_id": chapter_id,
            "review_chapter_id": review_chapter_id,
            "review_book_id": review_book_id,
        }

    data = asyncio.run(prepare())
    fake_mp3 = tmp_path / "moderation.mp3"
    fake_mp3.write_bytes(b"ID3" + b"0" * 4096)

    import app.webapp as webapp_module
    from app.services.reader_tts import TTSAsset
    from app.services.tma_auth import TMAUser

    users = {
        key: TMAUser(int(row["id"]), int(row["telegram_id"]), row["username"], row["full_name"])
        for key, row in data.items()
        if key in {"owner", "moderator", "admin", "reader"}
    }

    async def fake_auth(init_data: str):
        return users[init_data]

    async def fake_generate(chapter_id_arg: int, text: str, voice: str, rate=1.0, style="expressive"):
        assert chapter_id_arg in {data["chapter_id"], data["review_chapter_id"]}
        assert "глав" in text.lower() or "провер" in text.lower()
        return TTSAsset(fake_mp3, 30, voice, "hash")

    monkeypatch.setattr(webapp_module, "authenticate_init_data", fake_auth)
    monkeypatch.setattr(webapp_module, "generate_chapter_tts", fake_generate)
    client = TestClient(webapp_module.create_app())

    for role in ("owner", "moderator"):
        response = client.get(
            f"/api/reader/{data['chapter_id']}",
            headers={"X-Telegram-Init-Data": role},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["allowed"] is True
        assert payload["moderation_access"] is True
        assert payload["purchase_url"] == ""
        assert "Содержимое платной главы" in payload["chapter"]["text"]

        tts = client.get(
            f"/api/reader/{data['chapter_id']}/tts?voice=alexey",
            headers={"X-Telegram-Init-Data": role},
        )
        assert tts.status_code == 200
        assert tts.json()["moderation_access"] is True
        assert tts.json()["audio_url"].startswith(f"/media/reader-tts/{data['chapter_id']}.mp3?")

    # Подписанный MP3 повторно проверяет действующее право. После отзыва права
    # ранее выданная ссылка перестаёт открываться.
    moderator_tts = client.get(
        f"/api/reader/{data['chapter_id']}/tts?voice=anna",
        headers={"X-Telegram-Init-Data": "moderator"},
    ).json()
    media_before = client.get(moderator_tts["audio_url"])
    assert media_before.status_code == 200

    async def revoke_and_restore():
        from app.db import set_permission
        await set_permission(data["moderator"]["id"], "mod_books", False)

    asyncio.run(revoke_and_restore())
    media_after = client.get(moderator_tts["audio_url"])
    assert media_after.status_code == 403

    async def restore_permission():
        from app.db import set_permission
        await set_permission(data["moderator"]["id"], "mod_books", True)

    asyncio.run(restore_permission())

    # Сотрудник с правом проверки видит и ещё не опубликованную книгу из очереди review.
    review = client.get(
        f"/api/reader/{data['review_chapter_id']}",
        headers={"X-Telegram-Init-Data": "moderator"},
    )
    assert review.status_code == 200
    assert review.json()["moderation_access"] is True
    assert "Текст для ручной проверки" in review.json()["chapter"]["text"]

    jump = client.get(
        f"/api/book/{data['review_book_id']}/chapter-number/1",
        headers={"X-Telegram-Init-Data": "moderator"},
    )
    assert jump.status_code == 200
    assert jump.json()["chapter"]["id"] == data["review_chapter_id"]

    # Администратор без mod_books и обычный читатель не получают служебный доступ.
    for role in ("admin", "reader"):
        locked = client.get(
            f"/api/reader/{data['chapter_id']}",
            headers={"X-Telegram-Init-Data": role},
        )
        assert locked.status_code == 200
        assert locked.json()["allowed"] is False
        assert locked.json()["moderation_access"] is False
        assert locked.json()["chapter"]["text"] == ""
        denied_tts = client.get(
            f"/api/reader/{data['chapter_id']}/tts",
            headers={"X-Telegram-Init-Data": role},
        )
        assert denied_tts.status_code == 403
        hidden_review = client.get(
            f"/api/reader/{data['review_chapter_id']}",
            headers={"X-Telegram-Init-Data": role},
        )
        assert hidden_review.status_code == 404

    async def verify_audit_and_queue():
        from app.db import list_audit, list_books_for_moderation

        audit = await list_audit(limit=30)
        actions = {row["action"] for row in audit}
        assert "moderation_chapter_read" in actions
        assert "moderation_chapter_tts" in actions
        queue = await list_books_for_moderation()
        current = next(row for row in queue if int(row["id"]) == data["review_book_id"])
        assert int(current["first_chapter_id"]) == data["review_chapter_id"]
        assert int(current["chapters_count"]) == 1

    asyncio.run(verify_audit_and_queue())


def test_moderation_reader_ui_has_clear_service_mode():
    root = Path(__file__).resolve().parents[1]
    reader = (root / "templates/reader.html").read_text(encoding="utf-8")
    app_js = (root / "static/js/app.js").read_text(encoding="utf-8")
    control_js = (root / "static/js/control.js").read_text(encoding="utf-8")

    assert 'id="readerModerationNotice"' in reader
    assert "Служебный режим проверки" in reader
    assert "data.moderation_access" in app_js
    assert "Открыто в служебном режиме проверки" in app_js
    assert "Читать книгу" in control_js
    assert "first_chapter_id" in control_js
