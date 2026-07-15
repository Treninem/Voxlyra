import asyncio
import logging

from app.db import settle_due_premium_author_pools

logger = logging.getLogger(__name__)


async def premium_author_settlement_loop() -> None:
    """Периодически закрывает завершившиеся оплаченные периоды Premium."""
    while True:
        try:
            result = await settle_due_premium_author_pools(limit=250)
            if result.get("processed"):
                logger.info("Premium author settlement: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Premium author settlement loop failed")
        await asyncio.sleep(6 * 60 * 60)
