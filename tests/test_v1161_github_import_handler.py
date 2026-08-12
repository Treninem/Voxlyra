from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.handlers import github_import as handler


class FakeMessage:
    def __init__(self) -> None:
        self.text = ""
        self.reply_markup = None

    async def edit_text(self, text, reply_markup=None):
        self.text = str(text)
        self.reply_markup = reply_markup


class FakeCall:
    def __init__(self, user_id: int, data: str = "ghimp:scan:1") -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.message = FakeMessage()
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False):
        self.answers.append((str(text), bool(show_alert)))


@pytest.mark.asyncio
async def test_scan_turns_unexpected_network_failure_into_owner_message(monkeypatch):
    monkeypatch.setattr(handler.settings, "SYSTEM_OWNER_ID", 42)

    async def broken_discovery(*args, **kwargs):
        raise RuntimeError("network connection interrupted")

    monkeypatch.setattr(handler, "discover_packages", broken_discovery)
    call = FakeCall(42)

    await handler.scan(call)

    assert "Ошибка GitHub" in call.message.text
    assert "network connection interrupted" in call.message.text
    assert call.answers == [("", False)]


@pytest.mark.asyncio
async def test_non_owner_scan_is_denied_before_github_access(monkeypatch):
    monkeypatch.setattr(handler.settings, "SYSTEM_OWNER_ID", 42)

    async def forbidden_discovery(*args, **kwargs):
        raise AssertionError("non-owner must never reach GitHub discovery")

    monkeypatch.setattr(handler, "discover_packages", forbidden_discovery)
    call = FakeCall(43)

    await handler.scan(call)

    assert call.message.text == ""
    assert call.answers == [("Недоступно", True)]
