import asyncio

from aiogram.exceptions import TelegramBadRequest

from app.handlers.author import _safe_edit_reply_markup
from app.keyboards import multi_select_menu
from app.catalog_options import CONTENT_WARNINGS


class _AlreadyCurrentMessage:
    def __init__(self, reply_markup):
        self.reply_markup = reply_markup
        self.calls = 0

    async def edit_reply_markup(self, *, reply_markup):
        self.calls += 1
        raise AssertionError("Telegram API should not be called for identical markup")


class _RaceMessage:
    reply_markup = None

    def __init__(self):
        self.calls = 0

    async def edit_reply_markup(self, *, reply_markup):
        self.calls += 1
        raise TelegramBadRequest(
            method=None,
            message=(
                "Bad Request: message is not modified: specified new message content "
                "and reply markup are exactly the same as a current content and reply markup of the message"
            ),
        )


def test_safe_markup_edit_skips_identical_keyboard_without_api_call():
    markup = multi_select_menu("c", CONTENT_WARNINGS[:5], {"none"})
    message = _AlreadyCurrentMessage(markup)

    changed = asyncio.run(_safe_edit_reply_markup(message, reply_markup=markup))

    assert changed is False
    assert message.calls == 0


def test_safe_markup_edit_ignores_telegram_not_modified_race():
    markup = multi_select_menu("c", CONTENT_WARNINGS[:5], {"none"})
    message = _RaceMessage()

    changed = asyncio.run(_safe_edit_reply_markup(message, reply_markup=markup))

    assert changed is False
    assert message.calls == 1
