import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.db import init_db
from app.handlers import author, legal, moderation, owner, payments, start, library_manager
from app.middleware import BlockedUserMiddleware
from app.services.cover_storage import restore_missing_book_covers
from app.services.moderation_alerts import moderation_reminder_loop
from app.services.smart_notifications import smart_reader_reminder_loop
from app.services.premium_settlements import premium_author_settlement_loop
from app.services.library_manager import library_channel_scheduler_loop
from app.services.library_import_queue import library_import_worker_loop
from app.services.author_channel_queue import author_channel_scheduler_loop, ensure_author_channel_queue_schema
from app.services.runtime_performance import runtime_maintenance_loop
from app.services.runtime_state import mark_bot_connected, mark_bot_starting, mark_bot_stopped

logger = logging.getLogger(__name__)


_DISPATCHER: Dispatcher | None = None


def _dispatcher() -> Dispatcher:
    """Build the aiogram router tree once per process.

    Aiogram routers keep their parent reference. Recreating Dispatcher after a
    transient Telegram failure raises `Router is already attached`, so retries
    must reuse the original tree.
    """
    global _DISPATCHER
    if _DISPATCHER is not None:
        return _DISPATCHER
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
    dp.include_router(library_manager.router)
    dp.include_router(moderation.router)
    _DISPATCHER = dp
    return dp




async def _supervise_library_import_worker(bot: Bot) -> None:
    """Keep the persistent import queue alive even if one startup pass fails."""
    delay = 2
    while True:
        try:
            await library_import_worker_loop(bot)
            raise RuntimeError("Library import worker stopped unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Library import worker crashed; restarting in %s seconds", delay)
            await asyncio.sleep(delay)
            delay = min(30, max(2, delay * 2))

async def run_bot() -> None:
    mark_bot_starting()
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty. Fill .env or environment variables.")

    await init_db()
    await ensure_author_channel_queue_schema()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    background_tasks: list[asyncio.Task] = []
    try:
        try:
            identity = await bot.get_me()
            if identity.username:
                settings.BOT_USERNAME = identity.username
        except Exception as exc:
            # Username discovery is optional. Polling may still recover if Telegram
            # had a short network interruption during deployment.
            logger.warning("Could not resolve bot username: %s", exc)

        dp = _dispatcher()

        # If this network call fails, main.supervise_bot retries the entire bot
        # without terminating the Mini App HTTP server.
        await bot.delete_webhook(drop_pending_updates=False)
        mark_bot_connected()

        restored_covers, failed_covers = await restore_missing_book_covers(bot)
        if restored_covers or failed_covers:
            logger.info(
                "Cover recovery completed: restored=%s failed=%s",
                restored_covers,
                failed_covers,
            )
        logger.info("Bot started")

        background_tasks = [
            asyncio.create_task(moderation_reminder_loop(bot), name="moderation-reminders"),
            asyncio.create_task(smart_reader_reminder_loop(bot), name="reader-reminders"),
            asyncio.create_task(premium_author_settlement_loop(), name="premium-settlements"),
            asyncio.create_task(library_channel_scheduler_loop(bot), name="library-channel"),
            asyncio.create_task(author_channel_scheduler_loop(bot), name="author-channel"),
            asyncio.create_task(_supervise_library_import_worker(bot), name="library-import-supervisor"),
            asyncio.create_task(runtime_maintenance_loop(), name="runtime-maintenance"),
        ]
        await dp.start_polling(bot)
    finally:
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await bot.session.close()
        mark_bot_stopped()
