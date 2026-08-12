from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import vk_publication as vp


class BookRow(dict):
    def __getattr__(self, name):
        return self[name]


def _book():
    return BookRow(
        id=77,
        title="VK Test Book",
        pen_name="Автор",
        description="Описание книги",
        publication_status="published",
    )


def test_vk_book_url_targets_book_route(monkeypatch):
    monkeypatch.setattr(vp.settings, "VK_APP_ID", 123456)
    assert vp.vk_book_url(77) == "https://vk.com/app123456#book_77"
    assert vp.vk_book_url(0) == ""


@pytest.mark.asyncio
async def test_vk_wall_publication_uses_group_wall_and_mini_app_link(monkeypatch):
    monkeypatch.setattr(vp.settings, "VK_ENABLED", True)
    monkeypatch.setattr(vp.settings, "VK_APP_ID", 123456)
    monkeypatch.setattr(vp.settings, "VK_GROUP_ID", 987654)
    monkeypatch.setattr(vp.settings, "VK_GROUP_TOKEN", "test-token")

    async def fake_book(book_id):
        assert book_id == 77
        return _book()

    async def fake_options(book_id):
        return {"genres": ["Фэнтези", "Приключения"]}

    async def fake_chapters(book_id):
        return 12

    async def not_sent(book_id):
        return False

    calls = []
    async def fake_vk(method, params, *, token=""):
        calls.append((method, params, token))
        return {"post_id": 55}

    audits = []
    async def fake_audit(*args):
        audits.append(args)

    monkeypatch.setattr(vp, "get_book", fake_book)
    monkeypatch.setattr(vp, "get_book_options", fake_options)
    monkeypatch.setattr(vp, "count_chapters_for_book", fake_chapters)
    monkeypatch.setattr(vp, "_was_vk_wall_post_sent", not_sent)
    monkeypatch.setattr(vp, "vk_api_call", fake_vk)
    monkeypatch.setattr(vp, "add_audit", fake_audit)

    result = await vp.post_book_to_vk_wall(77, actor_user_id=9)
    assert result.sent is True
    assert result.post_id == 55
    assert len(calls) == 1
    method, params, token = calls[0]
    assert method == "wall.post"
    assert params["owner_id"] == -987654
    assert params["from_group"] == 1
    assert "https://vk.com/app123456#book_77" in params["message"]
    assert token == "test-token"
    assert any(item[1] == "vk_wall_post_sent" for item in audits)


@pytest.mark.asyncio
async def test_vk_wall_publication_is_idempotent(monkeypatch):
    monkeypatch.setattr(vp.settings, "VK_ENABLED", True)
    monkeypatch.setattr(vp.settings, "VK_APP_ID", 123456)
    monkeypatch.setattr(vp.settings, "VK_GROUP_ID", 987654)
    monkeypatch.setattr(vp.settings, "VK_GROUP_TOKEN", "test-token")

    async def fake_book(book_id):
        return _book()

    async def already_sent(book_id):
        return True

    async def forbidden_vk(*args, **kwargs):
        raise AssertionError("wall.post must not repeat")

    monkeypatch.setattr(vp, "get_book", fake_book)
    monkeypatch.setattr(vp, "_was_vk_wall_post_sent", already_sent)
    monkeypatch.setattr(vp, "vk_api_call", forbidden_vk)

    result = await vp.post_book_to_vk_wall(77)
    assert result.status == "already_sent"


@pytest.mark.asyncio
async def test_vk_wall_failure_is_audited_and_not_raised(monkeypatch):
    monkeypatch.setattr(vp.settings, "VK_ENABLED", True)
    monkeypatch.setattr(vp.settings, "VK_APP_ID", 123456)
    monkeypatch.setattr(vp.settings, "VK_GROUP_ID", 987654)
    monkeypatch.setattr(vp.settings, "VK_GROUP_TOKEN", "test-token")

    async def fake_book(book_id):
        return _book()

    async def fake_options(book_id):
        return {"genres": []}

    async def fake_chapters(book_id):
        return 1

    async def not_sent(book_id):
        return False

    async def broken_vk(*args, **kwargs):
        raise RuntimeError("VK unavailable")

    audits = []
    async def fake_audit(*args):
        audits.append(args)

    monkeypatch.setattr(vp, "get_book", fake_book)
    monkeypatch.setattr(vp, "get_book_options", fake_options)
    monkeypatch.setattr(vp, "count_chapters_for_book", fake_chapters)
    monkeypatch.setattr(vp, "_was_vk_wall_post_sent", not_sent)
    monkeypatch.setattr(vp, "vk_api_call", broken_vk)
    monkeypatch.setattr(vp, "add_audit", fake_audit)

    result = await vp.post_book_to_vk_wall(77)
    assert result.status == "failed"
    assert "VK unavailable" in result.error
    assert any(item[1] == "vk_wall_post_failed" for item in audits)
