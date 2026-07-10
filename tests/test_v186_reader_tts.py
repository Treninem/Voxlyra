from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def test_tts_cleans_only_chapter_text():
    from app.services.reader_tts import clean_chapter_text

    text = clean_chapter_text(
        "<p>Первый абзац книги.</p><button>Купить</button><aside>Реклама</aside>"
        "<p>Второй абзац книги.</p><script>alert(1)</script>"
    )
    assert "Первый абзац книги" in text
    assert "Второй абзац книги" in text
    assert "Купить" not in text
    assert "Реклама" not in text
    assert "alert" not in text


def test_tts_voices_and_signed_media_url(monkeypatch):
    from app.config import settings
    from app.services.reader_tts import available_voices, build_media_url, validate_media_token

    monkeypatch.setattr(settings, "TTS_SIGNING_SECRET", "test-secret")
    voices = available_voices()
    assert len(voices) >= 4
    assert {item["gender"] for item in voices} == {"female", "male"}

    url = build_media_url(user_id=7, chapter_id=11, voice="anna", lifetime_seconds=600)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.path == "/media/reader-tts/11.mp3"
    assert validate_media_token(
        user_id=7,
        chapter_id=11,
        voice="anna",
        expires_at=int(query["exp"][0]),
        signature=query["sig"][0],
    )
    assert not validate_media_token(
        user_id=8,
        chapter_id=11,
        voice="anna",
        expires_at=int(query["exp"][0]),
        signature=query["sig"][0],
    )


def test_tts_progress_is_saved_separately_by_voice(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "tts.sqlite3"))

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            get_tts_progress,
            init_db,
            save_tts_progress,
            upsert_user,
        )

        await init_db()
        user = await upsert_user(18601, "tts", "Читатель")
        await create_author_profile(user["id"], "Автор", "", "RU", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(profile["id"], "Книга", "Описание", "12+", "finished", False, "free", 0)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "Текст главы " * 100)
        await save_tts_progress(user["id"], chapter_id, 125, "anna")
        await save_tts_progress(user["id"], chapter_id, 42, "alexey")
        assert await get_tts_progress(user["id"], chapter_id, "anna") == 125
        assert await get_tts_progress(user["id"], chapter_id, "alexey") == 42

    asyncio.run(scenario())


def test_reader_has_background_tts_controls():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/reader.html").read_text(encoding="utf-8")
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")

    assert 'id="readerTtsPlayer"' in template
    assert 'id="readerTtsVoice"' in template
    assert 'id="readerTtsRate"' in template
    assert 'id="readerTtsAutoNext"' in template
    assert "navigator.mediaSession" in script
    assert "loadReaderTtsChapter(nextId, true)" in script
    assert "saveReaderTtsProgress" in script
    assert "espeak-ng" in dockerfile
    assert "ffmpeg" in dockerfile


def test_tts_api_returns_signed_audio_for_accessible_chapter(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "tts_api.sqlite3"))
    monkeypatch.setattr(settings, "TTS_SIGNING_SECRET", "api-test-secret")

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
        author = await upsert_user(18610, "author", "Автор")
        reader = await upsert_user(18611, "reader", "Читатель")
        await create_author_profile(author["id"], "Автор", "", "RU", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(profile["id"], "Книга TTS", "Описание", "12+", "finished", False, "free", 0)
        chapter_id = await add_manual_chapter(book_id, "Глава 1", "Только текст книги. " * 100)
        await set_book_publication_status(book_id, "published")
        await publish_book_content(book_id)
        return reader, chapter_id

    reader, chapter_id = asyncio.run(prepare())
    fake_mp3 = tmp_path / "fake.mp3"
    fake_mp3.write_bytes(b"ID3" + b"0" * 2048)

    import app.webapp as webapp_module
    from app.services.reader_tts import TTSAsset
    from app.services.tma_auth import TMAUser

    async def fake_auth(_: str):
        return TMAUser(int(reader["id"]), int(reader["telegram_id"]), reader["username"], reader["full_name"])

    async def fake_generate(chapter_id_arg: int, text: str, voice: str):
        assert chapter_id_arg == chapter_id
        assert "Только текст книги" in text
        return TTSAsset(fake_mp3, 25, voice, "hash")

    monkeypatch.setattr(webapp_module, "authenticate_init_data", fake_auth)
    monkeypatch.setattr(webapp_module, "generate_chapter_tts", fake_generate)
    client = TestClient(webapp_module.create_app())
    response = client.get(
        f"/api/reader/{chapter_id}/tts?voice=alexey",
        headers={"X-Telegram-Init-Data": "test"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["voice"] == "alexey"
    assert data["chapter"]["id"] == chapter_id
    assert data["audio_url"].startswith(f"/media/reader-tts/{chapter_id}.mp3?")
