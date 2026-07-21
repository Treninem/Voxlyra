import asyncio
import logging

import uvicorn

from app.bot import run_bot
from app.config import settings
from app.webapp import create_app
from app.services.runtime_state import mark_bot_retrying
from app.services.security import install_sensitive_log_filter

logger = logging.getLogger(__name__)


async def run_webapp() -> None:
    config = uvicorn.Config(
        create_app(),
        host="0.0.0.0",
        port=settings.PORT,
        log_level="info",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def supervise_bot() -> None:
    """Keep Telegram polling recoverable without taking the Mini App offline.

    A transient Telegram/DNS failure during deployment must not terminate the HTTP
    process. The supervisor retries with bounded exponential backoff while the
    web application and health endpoints remain available to Bothost.
    """
    delay = 3
    while True:
        try:
            await run_bot()
            # Polling normally runs until cancellation. An unexpected clean return
            # is treated as a recoverable disconnect.
            error: BaseException | str = "Telegram polling stopped unexpectedly."
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = exc
            logger.exception("Telegram bot stopped; retrying in %s seconds", delay)
        mark_bot_retrying(error, delay)
        await asyncio.sleep(delay)
        delay = min(60, max(3, delay * 2))


async def run_services() -> None:
    if not settings.RUN_WEBAPP:
        await supervise_bot()
        return

    web_task = asyncio.create_task(run_webapp(), name="voxlyra-webapp")
    bot_task = asyncio.create_task(supervise_bot(), name="voxlyra-bot-supervisor")
    tasks = {web_task, bot_task}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc
        # A normal web-server exit is a deployment shutdown. Stop polling too.
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    install_sensitive_log_filter()
    await run_services()


if __name__ == "__main__":
    asyncio.run(main())
