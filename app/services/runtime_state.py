from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_LOCK = threading.RLock()
_COMPONENT_NAMES = ("application", "database", "telegram", "vk")


def _empty_state() -> dict[str, Any]:
    return {
        "status": "not_started",
        "attempts": 0,
        "updated_at": "",
        "connected_at": "",
        "last_error": "",
        "retry_in_seconds": 0,
    }


_COMPONENTS: dict[str, dict[str, Any]] = {name: _empty_state() for name in _COMPONENT_NAMES}
# Backwards-compatible alias used by older diagnostics/tests.
_STATE = _COMPONENTS["telegram"]
_TOKEN_PATTERN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_SECRET_PATTERN = re.compile(r"(?i)(token|secret|key|password)(\s*[=:]\s*)([^\s,;]+)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _known_secrets() -> tuple[str, ...]:
    values: list[str] = []
    for name in (
        "BOT_TOKEN",
        "VK_APP_SECRET",
        "VK_SECURE_KEY",
        "VK_SERVICE_TOKEN",
        "VK_GROUP_TOKEN",
        "VK_PAYMENT_SECRET",
        "GITHUB_IMPORT_TOKEN",
        "GITHUB_SOURCE_WRITE_TOKEN",
        "COMIC_SIGNING_SECRET",
        "TTS_SIGNING_SECRET",
        "TTS_REMOTE_TOKEN",
        "PRIVACY_HASH_SECRET",
        "DATA_ENCRYPTION_KEY",
        "YOOKASSA_SECRET_KEY",
        "YOOKASSA_PAYOUT_SECRET_KEY",
        "YOOKASSA_WEBHOOK_TOKEN",
    ):
        value = str(getattr(settings, name, "") or "").strip()
        if len(value) >= 4:
            values.append(value)
    return tuple(sorted(set(values), key=len, reverse=True))


def redact_runtime_error(exc: BaseException | str | None) -> str:
    """Return a compact diagnostic string without leaking configured secrets."""
    if exc is None:
        return ""
    message = str(exc).strip().replace("\r", " ").replace("\n", " ")
    for secret in _known_secrets():
        message = message.replace(secret, "***")
    message = _TOKEN_PATTERN.sub("***", message)
    message = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}***", message)
    if not message:
        message = exc.__class__.__name__ if isinstance(exc, BaseException) else "unknown error"
    return message[:240]


# Historical private name retained because older modules may import it.
_safe_error = redact_runtime_error


def _component(name: str) -> dict[str, Any]:
    normalized = str(name or "").strip().lower()
    if normalized not in _COMPONENTS:
        raise ValueError(f"Unknown runtime component: {name!r}")
    return _COMPONENTS[normalized]


def _set_state(
    component: str,
    status: str,
    *,
    error: BaseException | str | None = None,
    retry_in_seconds: int = 0,
    count_attempt: bool = False,
    connected: bool = False,
) -> None:
    with _LOCK:
        state = _component(component)
        now = _now()
        state["status"] = status
        state["updated_at"] = now
        state["last_error"] = redact_runtime_error(error)
        state["retry_in_seconds"] = max(0, int(retry_in_seconds or 0))
        if count_attempt:
            state["attempts"] = int(state.get("attempts") or 0) + 1
        if connected:
            state["connected_at"] = now


def mark_component_starting(component: str) -> None:
    _set_state(component, "starting", count_attempt=True)


def mark_component_ready(component: str) -> None:
    _set_state(component, "ready", connected=True)


def mark_component_retrying(component: str, exc: BaseException | str, retry_in_seconds: int) -> None:
    _set_state(component, "retrying", error=exc, retry_in_seconds=max(1, int(retry_in_seconds)))


def mark_component_failed(component: str, exc: BaseException | str) -> None:
    _set_state(component, "failed", error=exc)


def mark_component_stopped(component: str) -> None:
    _set_state(component, "stopped")


def mark_component_disabled(component: str) -> None:
    _set_state(component, "disabled")


def component_runtime_snapshot(component: str) -> dict[str, Any]:
    with _LOCK:
        return dict(_component(component))


def runtime_snapshot() -> dict[str, dict[str, Any]]:
    with _LOCK:
        return {name: dict(state) for name, state in _COMPONENTS.items()}


def public_runtime_status() -> dict[str, str]:
    """Expose only non-sensitive component states for health/readiness output."""
    with _LOCK:
        return {name: str(state.get("status") or "unknown") for name, state in _COMPONENTS.items()}


def mark_bot_starting() -> None:
    mark_component_starting("telegram")


def mark_bot_connected() -> None:
    # Preserve the historical Telegram status string expected by diagnostics.
    _set_state("telegram", "connected", connected=True)


def mark_bot_retrying(exc: BaseException | str, retry_in_seconds: int) -> None:
    mark_component_retrying("telegram", exc, retry_in_seconds)


def mark_bot_stopped() -> None:
    mark_component_stopped("telegram")


def bot_runtime_snapshot() -> dict[str, Any]:
    return component_runtime_snapshot("telegram")
