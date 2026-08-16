from __future__ import annotations

import asyncio
import gc
import json
import logging
import time
import uuid
from contextlib import AbstractAsyncContextManager
from typing import Any

import uvicorn

from app.build_info import OWNER_BUILD_VERSION
from app.config import settings
from app.services.runtime_state import (
    mark_bot_retrying,
    mark_component_disabled,
    mark_component_failed,
    mark_component_ready,
    mark_component_retrying,
    mark_component_starting,
    mark_component_stopped,
    public_runtime_status,
    redact_runtime_error,
)
from app.services.security import install_sensitive_log_filter

logger = logging.getLogger(__name__)


def _memory_snapshot() -> dict[str, int | str]:
    """Return lightweight Linux memory diagnostics without importing heavy modules."""
    result: dict[str, int | str] = {"rss_bytes": 0, "cgroup_current_bytes": 0, "cgroup_limit_bytes": 0}
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
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
                with open(candidate, "r", encoding="utf-8") as cgroup_file:
                    raw = cgroup_file.read().strip()
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


def _request_id(scope: dict[str, Any]) -> str:
    """Reuse a sane upstream request id or generate a compact local one."""
    for raw_name, raw_value in scope.get("headers") or ():
        if raw_name.lower() != b"x-request-id":
            continue
        try:
            value = raw_value.decode("ascii").strip()
        except (UnicodeDecodeError, AttributeError):
            break
        if 8 <= len(value) <= 128 and all(ch.isalnum() or ch in "-_.:/" for ch in value):
            return value
        break
    return uuid.uuid4().hex


class DeferredVoxLyraApplication:
    """Bind the HTTP port immediately, then load database and the full app safely.

    The shell is also the stable runtime boundary for liveness/readiness,
    correlation IDs and component diagnostics. Those endpoints remain available
    before, during and after the heavy FastAPI application bootstrap.
    """

    HEALTH_PATHS = {"/health", "/healthz"}
    READINESS_PATHS = {"/readiness", "/readyz"}

    def __init__(self) -> None:
        self.target: Any | None = None
        self.target_lifespan: AbstractAsyncContextManager[Any] | None = None
        self.bootstrap_task: asyncio.Task[Any] | None = None
        self.bot_task: asyncio.Task[Any] | None = None
        self.vk_bot_task: asyncio.Task[Any] | None = None
        self.stage = "starting"
        self.error = ""
        self.started_at = time.monotonic()
        self.database_ready = False
        self.application_ready = False

    def _runtime_payload(self) -> dict[str, Any]:
        elapsed = max(0, int(time.monotonic() - self.started_at))
        ready = bool(self.application_ready and self.database_ready and self.stage == "ready" and not self.error)
        payload: dict[str, Any] = {
            "ok": self.stage != "failed",
            "ready": ready,
            "version": OWNER_BUILD_VERSION,
            "process_ready": True,
            "application_ready": self.application_ready,
            "database_ready": self.database_ready,
            "startup_stage": self.stage,
            "uptime_seconds": elapsed,
            "components": public_runtime_status(),
        }
        if self.error:
            payload["startup_error"] = self.error
        return payload

    async def _serve_runtime_probe(self, path: str, send) -> None:
        payload = self._runtime_payload()
        if path in self.READINESS_PATHS:
            payload["ok"] = bool(payload["ready"])
            status = 200 if payload["ready"] else 503
        else:
            status = 200 if payload["ok"] else 503
        status_code, headers, body = _json_response(status, payload)
        await send({"type": "http.response.start", "status": status_code, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _serve_bootstrap_http(self, scope, receive, send) -> None:
        path = str(scope.get("path") or "/")
        if path in self.HEALTH_PATHS or path in self.READINESS_PATHS:
            await self._serve_runtime_probe(path, send)
            return
        payload = self._runtime_payload()
        if path == "/":
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
        from app.bot import run_bot

        if not str(settings.BOT_TOKEN or "").strip():
            mark_component_disabled("telegram")
            logger.warning("Telegram bot disabled: BOT_TOKEN is not configured")
            return

        delay = 3
        while True:
            try:
                await run_bot()
                error: BaseException | str = "Telegram polling stopped unexpectedly."
            except asyncio.CancelledError:
                mark_component_stopped("telegram")
                raise
            except Exception as exc:
                error = exc
                logger.exception("Telegram bot stopped; retrying in %s seconds", delay)
            mark_bot_retrying(error, delay)
            await asyncio.sleep(delay)
            delay = min(60, max(3, delay * 2))

    async def _supervise_vk_bot(self) -> None:
        from app.services.vk_api import run_vk_community_bot

        if not settings.VK_ENABLED or not settings.VK_GROUP_TOKEN or int(settings.VK_GROUP_ID or 0) <= 0:
            mark_component_disabled("vk")
            return

        delay = 3
        while True:
            try:
                mark_component_starting("vk")
                mark_component_ready("vk")
                await run_vk_community_bot()
                if not settings.VK_ENABLED or not settings.VK_GROUP_TOKEN:
                    mark_component_disabled("vk")
                    return
                error: BaseException | str = "VK Long Poll stopped unexpectedly."
            except asyncio.CancelledError:
                mark_component_stopped("vk")
                raise
            except Exception as exc:
                error = exc
                logger.exception("VK bot stopped; retrying in %s seconds", delay)
            mark_component_retrying("vk", error, delay)
            await asyncio.sleep(delay)
            delay = min(60, max(3, delay * 2))

    async def _bootstrap(self) -> None:
        current_component = "database"
        try:
            self.stage = "database"
            mark_component_starting("database")
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
            mark_component_ready("database")
            _release_unused_memory()
            logger.info("Deferred bootstrap: database ready; memory=%s", _memory_snapshot())

            current_component = "application"
            self.stage = "application"
            mark_component_starting("application")
            logger.info("Deferred bootstrap: loading full FastAPI application")
            from app.webapp import create_app
            from app.github_source_upload_web import router as github_source_upload_web_router

            application = create_app()
            application.include_router(github_source_upload_web_router)
            lifespan = application.router.lifespan_context(application)
            await lifespan.__aenter__()
            self.target_lifespan = lifespan
            self.target = application

            for _ in range(300):
                if bool(getattr(application.state, "database_ready", False)):
                    break
                await asyncio.sleep(0.1)

            self.stage = "workers"
            self.bot_task = asyncio.create_task(self._supervise_bot(), name="voxlyra-bot-supervisor")
            if settings.VK_ENABLED and settings.VK_GROUP_TOKEN and int(settings.VK_GROUP_ID or 0) > 0:
                self.vk_bot_task = asyncio.create_task(self._supervise_vk_bot(), name="voxlyra-vk-bot-supervisor")
            else:
                mark_component_disabled("vk")
            self.application_ready = True
            self.stage = "ready"
            mark_component_ready("application")
            logger.info("Deferred bootstrap complete; memory=%s", _memory_snapshot())
        except asyncio.CancelledError:
            mark_component_stopped(current_component)
            raise
        except Exception as exc:
            self.error = redact_runtime_error(exc)
            self.stage = "failed"
            mark_component_failed(current_component, exc)
            if current_component == "database":
                mark_component_failed("application", "database bootstrap failed")
            logger.exception("Deferred bootstrap failed")

    async def _shutdown(self) -> None:
        self.stage = "stopping"
        tasks = [task for task in (self.bot_task, self.vk_bot_task, self.bootstrap_task) if task is not None and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.target_lifespan is not None:
            try:
                await self.target_lifespan.__aexit__(None, None, None)
            except Exception:
                logger.exception("Full application lifespan shutdown failed")
        mark_component_stopped("application")
        mark_component_stopped("database")

    async def _call_http(self, scope, receive, send) -> None:
        path = str(scope.get("path") or "/")
        request_id = _request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        response_status = 0

        async def send_with_runtime_headers(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status") or 0)
                headers = list(message.get("headers") or [])
                existing = {name.lower() for name, _ in headers}
                if b"x-request-id" not in existing:
                    headers.append((b"x-request-id", request_id.encode("ascii", errors="ignore")))
                if b"x-voxlyra-version" not in existing:
                    headers.append((b"x-voxlyra-version", OWNER_BUILD_VERSION.encode("ascii", errors="ignore")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            if path in self.HEALTH_PATHS or path in self.READINESS_PATHS:
                await self._serve_runtime_probe(path, send_with_runtime_headers)
            elif self.target is not None:
                await self.target(scope, receive, send_with_runtime_headers)
            else:
                await self._serve_bootstrap_http(scope, receive, send_with_runtime_headers)
        except Exception:
            logger.exception("Unhandled HTTP request failure request_id=%s path=%s", request_id, path)
            raise
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            slow_threshold = max(250, int(getattr(settings, "RUNTIME_SLOW_REQUEST_MS", 2000) or 2000))
            if response_status >= 500 or elapsed_ms >= slow_threshold:
                logger.warning(
                    "HTTP request completed request_id=%s status=%s duration_ms=%.1f path=%s",
                    request_id,
                    response_status or "unknown",
                    elapsed_ms,
                    path,
                )

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

        if scope_type == "http":
            await self._call_http(scope, receive, send)
            return
        if scope_type == "websocket":
            target = self.target
            if target is not None:
                await target(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1013})
            return


def _uvicorn_limit(name: str, default: int, minimum: int) -> int:
    try:
        value = int(getattr(settings, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


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
        timeout_keep_alive=_uvicorn_limit("RUNTIME_KEEPALIVE_SECONDS", 10, 2),
        limit_concurrency=_uvicorn_limit("RUNTIME_MAX_CONCURRENCY", 256, 16),
        backlog=_uvicorn_limit("RUNTIME_LISTEN_BACKLOG", 512, 64),
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
