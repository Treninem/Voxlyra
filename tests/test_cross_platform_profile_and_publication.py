from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def _png_bytes(size: tuple[int, int] = (240, 180)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, (32, 48, 64)).save(output, format="PNG")
    return output.getvalue()


def test_custom_avatar_is_canonical_profile_scoped_and_normalized(tmp_path, monkeypatch):
    from app.services import profile_avatar

    root = tmp_path / "avatars" / "custom"
    monkeypatch.setattr(profile_avatar, "CUSTOM_AVATAR_ROOT", root)

    async def scenario():
        path = await profile_avatar.save_custom_profile_avatar(42, _png_bytes())
        assert path == root / "42.webp"
        assert profile_avatar.custom_profile_avatar(42) == path
        assert profile_avatar.custom_profile_avatar(43) is None
        with Image.open(path) as image:
            assert image.format == "WEBP"
            assert image.size == (512, 512)
        assert await profile_avatar.delete_custom_profile_avatar(42) is True
        assert profile_avatar.custom_profile_avatar(42) is None

    asyncio.run(scenario())


def test_personal_cabinet_exposes_custom_avatar_controls_and_platform_fallback():
    template = (ROOT / "templates" / "library.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "profile-avatar.js").read_text(encoding="utf-8")
    router = (ROOT / "app" / "profile_avatar_web.py").read_text(encoding="utf-8")

    for element_id in ("profileAvatarChoose", "profileAvatarReset", "profileAvatarFile", "profileAvatarHint"):
        assert f'id="{element_id}"' in template
    assert "image/jpeg,image/png,image/webp" in template
    assert "/api/me/custom-avatar" in script
    assert "Используется фото из" in script
    assert "общий аватар VoxLyra" in script
    assert 'custom_profile_avatar(user.app_user_id)' in router
    assert 'save_custom_profile_avatar(user.app_user_id, payload)' in router


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_platform", ["telegram", "vk"])
async def test_manual_repost_calls_telegram_and_vk_regardless_of_login(monkeypatch, entry_platform):
    from app import profile_avatar_web as web

    user = SimpleNamespace(app_user_id=71, telegram_id=2097006037, platform=entry_platform)
    calls: list[tuple] = []

    async def fake_owner(init_data):
        calls.append(("auth", init_data, entry_platform))
        return user

    async def fake_book(book_id):
        return {"id": int(book_id), "publication_status": "published"}

    async def fake_vk(book_id, *, actor_user_id, force=False):
        calls.append(("vk", int(book_id), int(actor_user_id), bool(force)))
        return "sent"

    async def fake_tg(bot, book_id, *, actor_user_id, force=False):
        calls.append(("telegram", int(book_id), int(actor_user_id), bool(force)))
        return SimpleNamespace(channel_status="sent", channel_error="")

    async def fake_record(book_id, user_id, *, sent, error=""):
        calls.append(("record", int(book_id), int(user_id), bool(sent), str(error)))

    class FakeSession:
        async def close(self):
            calls.append(("bot_closed",))

    class FakeBot:
        def __init__(self, token):
            calls.append(("bot", token))
            self.session = FakeSession()

    monkeypatch.setattr(web, "_current_owner", fake_owner)
    monkeypatch.setattr(web, "get_book", fake_book)
    monkeypatch.setattr(web, "post_book_to_vk_wall", fake_vk)
    monkeypatch.setattr(web, "post_book_to_channel", fake_tg)
    monkeypatch.setattr(web, "record_owner_channel_promotion", fake_record)
    monkeypatch.setattr(web, "Bot", FakeBot)
    monkeypatch.setattr(web.settings, "BOT_TOKEN", "test-token")

    result = await web.repost_book_to_all_platform_channels(55, "signed-launch-data")

    assert result["ok"] is True
    assert result["requested_from"] == entry_platform
    assert result["telegram"]["status"] == "sent"
    assert result["vk"]["status"] == "sent"
    assert ("telegram", 55, 71, True) in calls
    assert ("vk", 55, 71, True) in calls


def test_owner_repost_button_is_routed_to_cross_platform_endpoint_before_legacy_handler():
    control = (ROOT / "static" / "js" / "control.js").read_text(encoding="utf-8")
    adapter = (ROOT / "static" / "js" / "cross-platform-publication.js").read_text(encoding="utf-8")
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    router = (ROOT / "app" / "profile_avatar_web.py").read_text(encoding="utf-8")

    assert "book:repost:" in control
    assert '[data-action^="book:repost:"]' in adapter
    assert "event.stopImmediatePropagation()" in adapter
    assert "/api/control/repost-platforms/" in adapter
    assert '/api/control/repost-platforms/{book_id}' in router
    assert "post_book_to_vk_wall" in router
    assert "post_book_to_channel" in router
    assert "force=True" in router
    assert 'cross-platform-publication.js?v={{ asset_version }}' in base
