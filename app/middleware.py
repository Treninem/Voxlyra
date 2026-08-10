from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject

from app.config import settings
from app.db import upsert_user


class SafeTelegramEditMiddleware(BaseMiddleware):
    """Treat a repeated press on an unchanged inline menu as a successful no-op."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer()
                except TelegramBadRequest:
                    pass
            return None


class BlockedUserMiddleware(BaseMiddleware):
    """Не пропускает заблокированного пользователя к командам, кнопкам и оплате."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = getattr(event, "from_user", None)
        if tg_user is None or tg_user.id in settings.owner_ids:
            return await handler(event, data)

        user = await upsert_user(tg_user.id, tg_user.username, tg_user.full_name)
        if not bool(user["is_blocked"]):
            return await handler(event, data)

        text = "Доступ к платформе ограничен. Обратитесь в поддержку."
        if isinstance(event, PreCheckoutQuery):
            await event.answer(ok=False, error_message=text)
        elif isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)
        return None
