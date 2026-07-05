import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.db import init_db
from app.handlers import author, legal, moderation, owner, payments, start
from app.middleware import BlockedUserMiddleware

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
    logger.info("Bot started")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
