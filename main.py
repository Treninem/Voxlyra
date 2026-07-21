from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import time
from contextlib import AbstractAsyncContextManager
from typing import Any

import uvicorn

from app.config import settings
from app.services.runtime_state import mark_bot_retrying
from app.services.security import install_sensitive_log_filter

logger = logging.getLogger(__name__)


def _memory_snapshot() -> dict[str, int | str]:
    """Return lightweight Linux memory diagnostics without importing heavy modules."""
    result: dict[str, int | str] = {"rss_bytes": 0, "cgroup_current_bytes": 0, "cgroup_limit_bytes": 0}
    try:
        for line in open("/proc/self/status", "r", encoding="utf-8"):
            if line.startswith("VmRSS:"):
                result["rss_bytes"] = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    for key, candidates in {
        "cgroup_current_bytes": ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        "cgroup_limit_bytes": ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    }.items():
        for candidate in candidates:
            try:
                raw = open(candidate, "r", encoding="utf-8").read().strip()
                if raw and raw != "max":
                    numeric = int(raw)
                    result[key] = "unlimited" if key == "cgroup_limit_bytes" and numeric >= (1 << 60) else numeric
                elif raw == "max":
                    result[key] = "unlimited"
                break
            except (OSError, ValueError):
                continue
    return result


def _release_unused_memory() -> None:
    """Return transient migration allocations to the container when possible."""
    gc.collect()
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        trim = getattr(libc, "malloc_trim", None)
        if trim is not None:
            trim(0)
    except Exception:
        pass


def _json_response(status: int, payload: dict[str, Any]) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    return status, headers, body


class DeferredVoxLyraApplication:
    """Bind the HTTP port with a tiny ASGI shell, then load the full app.

    The previous build imported Telegram, PDF, image and OCR stacks before SQLite
    initialization. On memory-constrained Bothost plans the extra database cache
    could cross the container limit and the platform killed PID 1 without a Python
    traceback. This shell keeps the process footprint small while the database is
    migrated, then enters the normal FastAPI lifespan and starts Telegram polling.
    """

    def __init__(self) -> None:
        self.target: Any | None = None
        self.target_lifespan: AbstractAsyncContextManager[Any] | None = None
        self.bootstrap_task: asyncio.Task[Any] | None = None
        self.bot_task: asyncio.Task[Any] | None = None
        self.stage = "starting"
        self.error = ""
        self.started_at = time.monotonic()
        self.database_ready = False
        self.application_ready = False

    async def _serve_bootstrap_http(self, scope, receive, send) -> None:
        path = str(scope.get("path") or "/")
        elapsed = int(time.monotonic() - self.started_at)
        payload = {
            "ok": True,
            "process_ready": True,
            "application_ready": self.application_ready,
            "database_ready": self.database_ready,
            "startup_stage": self.stage,
            "startup_elapsed_seconds": elapsed,
        }
        if self.error:
            payload["startup_error"] = self.error[:240]
        if path in {"/health", "/readiness"}:
            status, headers, body = _json_response(200, payload)
        elif path == "/":
            body = (
                "<!doctype html><html lang='ru'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>VoxLyra</title><body style='font-family:sans-serif;background:#11152d;color:#fff;padding:24px'>"
                "<h1>VoxLyra запускается</h1><p>Подготавливается база данных. Обновите страницу через несколько секунд.</p>"
                "</body></html>"
            ).encode("utf-8")
            status = 200
            headers = [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ]
        else:
            status, headers, body = _json_response(
                503,
                {
                    "ok": False,
                    "detail": "VoxLyra ещё запускается. Повторите запрос через несколько секунд.",
                    **payload,
                },
            )
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _supervise_bot(self) -> None:
        # Import the Telegram router tree only after SQLite is ready. This avoids
        # holding aiogram + import/image stacks in memory during the migration.
        from app.bot import run_bot

        delay = 3
        while True:
            try:
                await run_bot()
                error: BaseException | str = "Telegram polling stopped unexpectedly."
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc
                logger.exception("Telegram bot stopped; retrying in %s seconds", delay)
            mark_bot_retrying(error, delay)
            await asyncio.sleep(delay)
            delay = min(60, max(3, delay * 2))

    async def _bootstrap(self) -> None:
        try:
            self.stage = "database"
            logger.info("Deferred bootstrap: database initialization started; memory=%s", _memory_snapshot())
            from app.db import init_db

            database_task = asyncio.create_task(init_db(), name="voxlyra-minimal-database-bootstrap")
            while not database_task.done():
                done, _ = await asyncio.wait({database_task}, timeout=10)
                if done:
                    break
                logger.info(
                    "Deferred bootstrap: database initialization still running (%s seconds); memory=%s",
                    int(time.monotonic() - self.started_at),
                    _memory_snapshot(),
                )
            await database_task
            self.database_ready = True
            _release_unused_memory()
            logger.info("Deferred bootstrap: database ready; memory=%s", _memory_snapshot())

            self.stage = "application"
            logger.info("Deferred bootstrap: loading full FastAPI application")
            from app.webapp import create_app

            application = create_app()
            lifespan = application.router.lifespan_context(application)
            await lifespan.__aenter__()
            self.target_lifespan = lifespan
            self.target = application

            # The full app uses the same in-process init_db lock/cache. Its own
            # background bootstrap finishes quickly after the minimal migration.
            for _ in range(300):
                if bool(getattr(application.state, "database_ready", False)):
                    break
                await asyncio.sleep(0.1)

            self.stage = "telegram"
            self.bot_task = asyncio.create_task(self._supervise_bot(), name="voxlyra-bot-supervisor")
            self.application_ready = True
            self.stage = "ready"
            logger.info("Deferred bootstrap complete; memory=%s", _memory_snapshot())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.error = str(exc)[:240]
            self.stage = "failed"
            logger.exception("Deferred bootstrap failed")

    async def _shutdown(self) -> None:
        tasks = [task for task in (self.bot_task, self.bootstrap_task) if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.target_lifespan is not None:
            try:
                await self.target_lifespan.__aexit__(None, None, None)
            except Exception:
                logger.exception("Full application lifespan shutdown failed")

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    self.bootstrap_task = asyncio.create_task(self._bootstrap(), name="voxlyra-deferred-bootstrap")
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await self._shutdown()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        target = self.target
        if target is not None:
            await target(scope, receive, send)
            return
        if scope_type == "http":
            await self._serve_bootstrap_http(scope, receive, send)
            return
        if scope_type == "websocket":
            await send({"type": "websocket.close", "code": 1013})
            return


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    install_sensitive_log_filter()
    application = DeferredVoxLyraApplication()
    config = uvicorn.Config(
        application,
        host="0.0.0.0",
        port=settings.PORT,
        log_level="info",
        lifespan="on",
        loop="asyncio",
        http="h11",
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
