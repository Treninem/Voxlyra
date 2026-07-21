from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "status": "not_started",
    "attempts": 0,
    "updated_at": "",
    "connected_at": "",
    "last_error": "",
    "retry_in_seconds": 0,
}

_TOKEN_PATTERN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_error(exc: BaseException | str | None) -> str:
    if exc is None:
        return ""
    message = str(exc).strip().replace("\r", " ").replace("\n", " ")
    token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
    if token:
        message = message.replace(token, "***")
    message = _TOKEN_PATTERN.sub("***", message)
    if not message:
        message = exc.__class__.__name__ if isinstance(exc, BaseException) else "unknown error"
    return message[:240]


def mark_bot_starting() -> None:
    with _LOCK:
        _STATE["status"] = "starting"
        _STATE["attempts"] = int(_STATE.get("attempts") or 0) + 1
        _STATE["updated_at"] = _now()
        _STATE["last_error"] = ""
        _STATE["retry_in_seconds"] = 0


def mark_bot_connected() -> None:
    with _LOCK:
        now = _now()
        _STATE["status"] = "connected"
        _STATE["updated_at"] = now
        _STATE["connected_at"] = now
        _STATE["last_error"] = ""
        _STATE["retry_in_seconds"] = 0


def mark_bot_retrying(exc: BaseException | str, retry_in_seconds: int) -> None:
    with _LOCK:
        _STATE["status"] = "retrying"
        _STATE["updated_at"] = _now()
        _STATE["last_error"] = _safe_error(exc)
        _STATE["retry_in_seconds"] = max(1, int(retry_in_seconds))


def mark_bot_stopped() -> None:
    with _LOCK:
        _STATE["status"] = "stopped"
        _STATE["updated_at"] = _now()
        _STATE["retry_in_seconds"] = 0


def bot_runtime_snapshot() -> dict[str, Any]:
    with _LOCK:
        return dict(_STATE)
