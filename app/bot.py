import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.db import init_db
from app.handlers import author, legal, moderation, owner, payments, start
from app.middleware import BlockedUserMiddleware
from app.services.cover_storage import restore_missing_book_covers
from app.services.moderation_alerts import moderation_reminder_loop

logger = logging.getLogger(__name__)


async def run_bot() -> None:
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Fill .env or environment variables.")

    await init_db()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    blocked_guard = BlockedUserMiddleware()
    dp.message.outer_middleware(blocked_guard)
    dp.callback_query.outer_middleware(blocked_guard)
    dp.pre_checkout_query.outer_middleware(blocked_guard)

    dp.include_router(payments.router)
    dp.include_router(legal.router)
    dp.include_router(start.router)
    dp.include_router(author.router)
    dp.include_router(owner.router)
    dp.include_router(moderation.router)

    await bot.delete_webhook(drop_pending_updates=False)
    restored_covers, failed_covers = await restore_missing_book_covers(bot)
    if restored_covers or failed_covers:
        logger.info("Cover recovery completed: restored=%s failed=%s", restored_covers, failed_covers)
    logger.info("Bot started")
    reminder_task = asyncio.create_task(moderation_reminder_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
