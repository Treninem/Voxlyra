import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")


def test_local_book_assistant_answers_only_within_spoiler_boundary():
    from app.services.book_assistant import answer_question, build_chapter_analysis, build_recap

    chapters = [
        {"id": 1, "number": 1, "title": "Встреча", "text": "Арден встретил Миру у старого моста. Мира передала ему серебряный ключ."},
        {"id": 2, "number": 2, "title": "Ключ", "text": "Арден открыл ключом закрытую комнату. В комнате находилась карта северной башни."},
        {"id": 3, "number": 3, "title": "Тайна", "text": "В северной башне Арден узнает тайну Миры и находит корону."},
    ]
    analyzed = []
    for chapter in chapters:
        item = dict(chapter)
        item.update(build_chapter_analysis(chapter["text"]))
        analyzed.append(item)

    result = answer_question("Кто такая Мира?", analyzed, current_number=2)
    assert result["spoiler_limit"] == 2
    assert "Мира" in result["answer"]
    assert "корону" not in result["answer"]
    assert all(source["chapter_number"] <= 2 for source in result["sources"])

    recap = build_recap(analyzed, current_number=3, limit=2)
    assert [item["chapter_number"] for item in recap] == [1, 2]


def test_assistant_cache_tracks_text_digest_and_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "assistant.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            add_manual_chapter,
            create_author_profile,
            create_book,
            get_author_profile,
            get_book_assistant_cache,
            init_db,
            list_book_assistant_chapters,
            save_book_assistant_cache,
            set_book_publication_status,
            set_chapter_status,
            upsert_user,
        )
        from app.services.book_assistant import build_chapter_analysis

        await init_db()
        author_user = await upsert_user(831001, "assistant_author", "Assistant Author")
        await create_author_profile(author_user["id"], "Автор помощника", "", "RU", True)
        profile = await get_author_profile(author_user["id"])
        book_id = await create_book(profile["id"], "Книга помощника", "Описание", "12+", "writing", False, "free", 0)
        chapter_id = await add_manual_chapter(book_id, "Начало", "Лиана увидела Башню Света. Лиана вошла внутрь.", True, 0)
        await set_chapter_status(chapter_id, "published")
        await set_book_publication_status(book_id, "published")

        rows = await list_book_assistant_chapters(book_id, 1, limit=10)
        assert len(rows) == 1
        analysis = build_chapter_analysis(rows[0]["text"])
        await save_book_assistant_cache(
            chapter_id,
            analysis["digest"],
            analysis["summary"],
            analysis["characters"],
            analysis["terms"],
        )
        cached = await get_book_assistant_cache(chapter_id, analysis["digest"])
        assert cached is not None
        assert cached["summary"]
        assert isinstance(cached["characters"], list)
        assert await get_book_assistant_cache(chapter_id, "wrong-digest") is None

    asyncio.run(scenario())


def test_assistant_ui_and_api_contract_are_wired():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/reader.html").read_text(encoding="utf-8")
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    styles = (root / "static/css/style.css").read_text(encoding="utf-8")
    webapp = (root / "app/webapp.py").read_text(encoding="utf-8")
    database = (root / "app/db.py").read_text(encoding="utf-8")

    assert 'id="readerAssistantPanel"' in template
    assert 'id="readerAssistantQuestion"' in template
    assert "loadBookAssistantContext" in script
    assert "askBookAssistant" in script
    assert "/api/reader/{chapter_id}/assistant" in webapp
    assert "/api/reader/{chapter_id}/assistant/ask" in webapp
    assert "book_assistant_cache" in database
    assert ".reader-assistant-panel" in styles
