import pytest

from app.services import cross_platform_publication as cpp


def test_vk_book_link_targets_vk_mini_app(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_APP_ID", 123456)
    assert cpp.vk_book_url(42) == "https://vk.com/app123456#book_42"
    assert "t.me" not in cpp.vk_book_url(42)


def test_vk_post_uses_votes_and_never_mentions_stars(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_VOTES_PER_STAR", 2.0)
    text = cpp.build_vk_book_post(
        title="Книга", author="Автор", genres=["Фэнтези"], age_limit="16+",
        chapters_count=3, has_audio=False, description="Описание",
        pricing_type="full", price_stars=7,
        book_url="https://vk.com/app123#book_1",
    )
    assert "14 голосов VK" in text
    assert "Stars" not in text
    assert "Telegram" not in text
    assert "https://vk.com/app123#book_1" in text


def test_vk_publication_price_matches_checkout_rounding(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_VOTES_PER_STAR", 1.5)
    assert cpp.vk_votes_from_stars(3) == 5


def test_vk_publication_never_undercuts_checkout(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_VOTES_PER_STAR", 0.5)
    assert cpp.vk_votes_from_stars(10) == 10


def test_vk_chapter_pricing_is_vk_only(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_VOTES_PER_STAR", 2.0)
    text = cpp.build_vk_book_post(
        title="Книга", author="Автор", genres=[], age_limit="",
        chapters_count=1, has_audio=False, description="",
        pricing_type="chapters", price_stars=9,
        book_url="https://vk.com/app1#book_1",
    )
    assert "оплата голосами VK" in text
    assert "Вся книга" not in text
    assert "Stars" not in text


def test_vk_media_token_prefers_dedicated_user_token(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_GROUP_TOKEN", "group-token")
    monkeypatch.setenv("VK_MEDIA_TOKEN", "user-media-token")
    assert cpp._vk_media_token() == "user-media-token"


def test_vk_media_token_keeps_group_token_as_compatibility_fallback(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_GROUP_TOKEN", "group-token")
    monkeypatch.delenv("VK_MEDIA_TOKEN", raising=False)
    assert cpp._vk_media_token() == "group-token"


@pytest.mark.asyncio
async def test_vk_retry_only_follows_latest_failed_terminal_state(monkeypatch):
    async def failed(book_id):
        return "vk_wall_post_failed"

    async def sent(book_id):
        return "vk_wall_post_sent"

    async def absent(book_id):
        return ""

    monkeypatch.setattr(cpp, "_vk_wall_post_state", failed)
    assert await cpp.should_retry_vk_wall_post(77) is True
    monkeypatch.setattr(cpp, "_vk_wall_post_state", sent)
    assert await cpp.should_retry_vk_wall_post(77) is False
    monkeypatch.setattr(cpp, "_vk_wall_post_state", absent)
    assert await cpp.should_retry_vk_wall_post(77) is False


def _published_book():
    return {
        "title": "VK Test Book",
        "pen_name": "Автор",
        "description": "Описание книги",
        "publication_status": "published",
        "age_limit": "16+",
        "has_audio": 0,
        "pricing_type": "full",
        "price_stars": 7,
        "cover_file_id": "",
        "cover_path": "",
    }


async def _install_vk_book_fakes(monkeypatch, *, cover):
    async def fake_book(book_id):
        assert book_id == 77
        return _published_book()

    async def fake_options(book_id):
        return {"genres": ["Фэнтези", "Приключения"]}

    async def fake_chapters(book_id):
        return 12

    async def not_sent(book_id):
        return False

    async def fake_cover(**kwargs):
        return cover

    monkeypatch.setattr(cpp, "get_book", fake_book)
    monkeypatch.setattr(cpp, "get_book_options", fake_options)
    monkeypatch.setattr(cpp, "count_chapters_for_book", fake_chapters)
    monkeypatch.setattr(cpp, "_was_vk_wall_post_sent", not_sent)
    monkeypatch.setattr(cpp, "ensure_book_cover_file", fake_cover)


@pytest.mark.asyncio
async def test_vk_wall_post_requires_uploaded_cover_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_ENABLED", True)
    monkeypatch.setattr(cpp.settings, "VK_APP_ID", 123456)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_ID", 987654)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_TOKEN", "test-token")
    monkeypatch.setattr(cpp.settings, "VK_VOTES_PER_STAR", 2.0)

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    await _install_vk_book_fakes(monkeypatch, cover=cover)

    async def fake_upload(path):
        assert path == cover
        return "photo-987654_123"

    calls = []

    async def fake_vk(method, params, *, token=""):
        calls.append((method, params, token))
        return {"post_id": 55}

    audits = []

    async def fake_audit(*args):
        audits.append(args)

    monkeypatch.setattr(cpp, "_vk_upload_wall_cover", fake_upload)
    monkeypatch.setattr(cpp, "vk_api_call", fake_vk)
    monkeypatch.setattr(cpp, "add_audit", fake_audit)

    result = await cpp.post_book_to_vk_wall(77, actor_user_id=9)
    assert result == "sent"
    assert len(calls) == 1
    method, params, token = calls[0]
    assert method == "wall.post"
    assert params["owner_id"] == -987654
    assert params["from_group"] == 1
    assert params["attachments"] == "photo-987654_123"
    assert "https://vk.com/app123456#book_77" in params["message"]
    assert "14 голосов VK" in params["message"]
    assert "Stars" not in params["message"]
    assert token == "test-token"
    assert any(item[1] == "vk_wall_post_sent" for item in audits)


@pytest.mark.asyncio
async def test_vk_wall_does_not_publish_without_cover(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_ENABLED", True)
    monkeypatch.setattr(cpp.settings, "VK_APP_ID", 123456)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_ID", 987654)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_TOKEN", "test-token")
    await _install_vk_book_fakes(monkeypatch, cover=None)

    calls = []
    audits = []

    async def forbidden_vk(method, params, *, token=""):
        calls.append((method, params, token))
        raise AssertionError("wall.post must not run without a cover")

    async def fake_audit(*args):
        audits.append(args)

    monkeypatch.setattr(cpp, "vk_api_call", forbidden_vk)
    monkeypatch.setattr(cpp, "add_audit", fake_audit)

    assert await cpp.post_book_to_vk_wall(77, actor_user_id=9) == "failed"
    assert calls == []
    assert any(item[1] == "vk_wall_cover_failed" and item[5] == "cover_required" for item in audits)
    assert any(item[1] == "vk_wall_post_failed" and item[5] == "cover_required" for item in audits)


@pytest.mark.asyncio
async def test_vk_wall_does_not_publish_when_cover_upload_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_ENABLED", True)
    monkeypatch.setattr(cpp.settings, "VK_APP_ID", 123456)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_ID", 987654)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_TOKEN", "test-token")

    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"cover")
    await _install_vk_book_fakes(monkeypatch, cover=cover)

    async def broken_upload(path):
        raise RuntimeError("VK photo upload unavailable")

    async def forbidden_vk(*args, **kwargs):
        raise AssertionError("wall.post must not run after cover upload failure")

    audits = []

    async def fake_audit(*args):
        audits.append(args)

    monkeypatch.setattr(cpp, "_vk_upload_wall_cover", broken_upload)
    monkeypatch.setattr(cpp, "vk_api_call", forbidden_vk)
    monkeypatch.setattr(cpp, "add_audit", fake_audit)

    assert await cpp.post_book_to_vk_wall(77, actor_user_id=None) == "failed"
    assert any(item[1] == "vk_wall_cover_failed" for item in audits)
    assert any(item[1] == "vk_wall_post_failed" for item in audits)


@pytest.mark.asyncio
async def test_vk_wall_post_is_idempotent(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_ENABLED", True)
    monkeypatch.setattr(cpp.settings, "VK_APP_ID", 123456)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_ID", 987654)
    monkeypatch.setattr(cpp.settings, "VK_GROUP_TOKEN", "test-token")

    async def fake_book(book_id):
        return _published_book()

    async def already_sent(book_id):
        return True

    async def forbidden_vk(*args, **kwargs):
        raise AssertionError("wall.post must not repeat")

    monkeypatch.setattr(cpp, "get_book", fake_book)
    monkeypatch.setattr(cpp, "_was_vk_wall_post_sent", already_sent)
    monkeypatch.setattr(cpp, "vk_api_call", forbidden_vk)

    assert await cpp.post_book_to_vk_wall(77, actor_user_id=None) == "already_sent"
