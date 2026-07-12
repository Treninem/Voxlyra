import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")


async def _make_free_published_chapter(seed: int):
    from app.db import (
        add_manual_chapter,
        create_author_profile,
        create_book,
        get_author_profile,
        set_book_publication_status,
        set_chapter_status,
        upsert_user,
    )

    author_user = await upsert_user(seed, f"author_social_{seed}", f"Author Social {seed}")
    await create_author_profile(author_user["id"], "Автор обсуждений", "", "RU", True)
    profile = await get_author_profile(author_user["id"])
    book_id = await create_book(
        profile["id"], "Книга обсуждений", "Описание", "16+", "writing", False, "free", 0,
    )
    chapter_id = await add_manual_chapter(book_id, "Глава 1", "Текст главы. " * 40, True, 0)
    await set_chapter_status(chapter_id, "published")
    await set_book_publication_status(book_id, "published")
    return book_id, chapter_id


def test_comments_support_replies_spoilers_likes_and_flat_threading(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "social_comments.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            add_comment,
            init_db,
            list_comments_for_chapter,
            toggle_comment_like,
            upsert_user,
        )

        await init_db()
        _, chapter_id = await _make_free_published_chapter(821001)
        first = await upsert_user(821002, "reader_one", "Reader One")
        second = await upsert_user(821003, "reader_two", "Reader Two")
        root_id = await add_comment(first["id"], chapter_id, "Важный поворот", is_spoiler=True)
        reply_id = await add_comment(second["id"], chapter_id, "Согласен с вами", parent_id=root_id)
        nested_reply_id = await add_comment(first["id"], chapter_id, "Спасибо за ответ", parent_id=reply_id)

        result = await toggle_comment_like(second["id"], root_id)
        assert result == {"liked": True, "like_count": 1}
        rows = await list_comments_for_chapter(chapter_id, 100, viewer_user_id=second["id"])
        by_id = {int(row["id"]): row for row in rows}
        assert int(by_id[root_id]["is_spoiler"]) == 1
        assert int(by_id[root_id]["viewer_liked"]) == 1
        assert int(by_id[root_id]["like_count"]) == 1
        assert int(by_id[reply_id]["parent_id"]) == root_id
        # Ответ на ответ прикрепляется к корню: глубина не растёт бесконечно.
        assert int(by_id[nested_reply_id]["parent_id"]) == root_id

        result = await toggle_comment_like(second["id"], root_id)
        assert result == {"liked": False, "like_count": 0}

    asyncio.run(scenario())


def test_chapter_reaction_is_single_switchable_choice(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "social_reactions.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app.db import init_db, list_chapter_reactions, set_chapter_reaction, upsert_user

        await init_db()
        _, chapter_id = await _make_free_published_chapter(822001)
        reader = await upsert_user(822002, "reaction_reader", "Reaction Reader")

        first = await set_chapter_reaction(reader["id"], chapter_id, "fire")
        assert first["selected"] == "fire"
        assert first["counts"]["fire"] == 1

        switched = await set_chapter_reaction(reader["id"], chapter_id, "heart")
        assert switched["selected"] == "heart"
        assert switched["counts"]["fire"] == 0
        assert switched["counts"]["heart"] == 1

        removed = await set_chapter_reaction(reader["id"], chapter_id, "heart")
        assert removed["selected"] is None
        assert removed["counts"]["heart"] == 0
        assert (await list_chapter_reactions(chapter_id, reader["id"]))["selected"] is None

    asyncio.run(scenario())


def test_comment_reports_are_deduplicated_and_own_report_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "social_reports.sqlite3"))
    from app.config import get_settings

    get_settings.cache_clear()

    async def scenario():
        from app.db import add_comment, init_db, report_comment, upsert_user

        await init_db()
        _, chapter_id = await _make_free_published_chapter(823001)
        owner = await upsert_user(823002, "comment_owner", "Comment Owner")
        reporter = await upsert_user(823003, "comment_reporter", "Comment Reporter")
        comment_id = await add_comment(owner["id"], chapter_id, "Комментарий для проверки")

        first = await report_comment(reporter["id"], comment_id, "Оскорбление участника")
        second = await report_comment(reporter["id"], comment_id, "Повторная жалоба")
        assert first["created"] is True
        assert second["created"] is False
        assert first["complaint_id"] == second["complaint_id"]

        with pytest.raises(ValueError, match="own comment"):
            await report_comment(owner["id"], comment_id, "На самого себя")

    asyncio.run(scenario())


def test_social_discussion_ui_and_api_contract_are_wired():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/reader.html").read_text(encoding="utf-8")
    script = (root / "static/js/app.js").read_text(encoding="utf-8")
    styles = (root / "static/css/style.css").read_text(encoding="utf-8")
    webapp = (root / "app/webapp.py").read_text(encoding="utf-8")
    database = (root / "app/db.py").read_text(encoding="utf-8")

    assert 'id="chapterReactionList"' in template
    assert 'id="commentSpoiler"' in template
    assert 'id="commentReplyBar"' in template
    assert "data-comment-like" in script
    assert "data-comment-report" in script
    assert "data-comment-spoiler-reveal" in script
    assert "/api/comments/{comment_id}/like" in webapp
    assert "/api/comments/{comment_id}/report" in webapp
    assert "/api/reader/{chapter_id}/reactions" in webapp
    assert "comment_likes" in database
    assert "chapter_reactions" in database
    assert ".comment-spoiler-cover" in styles
