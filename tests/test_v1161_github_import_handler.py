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


class FakeDirectMessage:
    def __init__(self, user_id: int) -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((str(text), reply_markup))


class FakeCall:
    def __init__(self, user_id: int, data: str = "ghimp:scan:1") -> None:
        self.from_user = SimpleNamespace(id=user_id)
        self.data = data
        self.message = FakeMessage()
        self.answers: list[tuple[str, bool]] = []

    async def answer(self, text: str = "", show_alert: bool = False):
        self.answers.append((str(text), bool(show_alert)))


def _callback_data(markup) -> list[str]:
    return [
        str(button.callback_data or "")
        for row in markup.inline_keyboard
        for button in row
    ]


@pytest.mark.asyncio
async def test_system_owner_system_screen_exposes_github_import_and_diagnostics(monkeypatch):
    monkeypatch.setattr(handler.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(handler.settings, "GITHUB_IMPORT_ENABLED", True)
    call = FakeCall(42, "owner:system")

    await handler.system_owner_tools(call)

    assert "Системные инструменты" in call.message.text
    assert "включён" in call.message.text
    callbacks = _callback_data(call.message.reply_markup)
    assert "owner:github_import" in callbacks
    assert "owner:system:diagnostics" in callbacks
    assert "owner:menu" in callbacks
    assert call.answers == [("", False)]


@pytest.mark.asyncio
async def test_system_owner_diagnostics_has_back_route(monkeypatch):
    monkeypatch.setattr(handler.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(handler, "format_diagnostics_for_owner", lambda: "DIAGNOSTICS")
    call = FakeCall(42, "owner:system:diagnostics")

    await handler.system_owner_diagnostics(call)

    assert call.message.text == "DIAGNOSTICS"
    callbacks = _callback_data(call.message.reply_markup)
    assert callbacks == ["owner:system", "owner:menu"]


@pytest.mark.asyncio
async def test_direct_github_import_command_is_silent_for_non_system_owner(monkeypatch):
    monkeypatch.setattr(handler.settings, "SYSTEM_OWNER_ID", 42)
    allowed = FakeDirectMessage(42)
    denied = FakeDirectMessage(43)

    await handler.direct_menu(allowed)
    await handler.direct_menu(denied)

    assert len(allowed.answers) == 1
    assert "GitHub Import" in allowed.answers[0][0]
    assert "owner:system" in _callback_data(allowed.answers[0][1])
    assert denied.answers == []


@pytest.mark.asyncio
async def test_system_owner_tools_denies_direct_function_call_for_non_owner(monkeypatch):
    monkeypatch.setattr(handler.settings, "SYSTEM_OWNER_ID", 42)
    call = FakeCall(43, "owner:system")

    await handler.system_owner_tools(call)

    assert call.message.text == ""
    assert call.answers == [("Недоступно", True)]


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
