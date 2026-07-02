import asyncio
import logging

import uvicorn

from app.bot import run_bot
from app.config import settings
from app.webapp import create_app


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


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    if settings.RUN_WEBAPP:
        await asyncio.gather(run_bot(), run_webapp())
    else:
        await run_bot()


if __name__ == "__main__":
    asyncio.run(main())
