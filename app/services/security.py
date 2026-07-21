from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Iterable
from urllib.parse import urlparse

from app.config import settings


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(bot[_-]?token|secret|authorization|x-telegram-init-data|initData)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(telegram_payment_charge_id|provider_payment_charge_id)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(inn|phone|email)\s*[:=]\s*([^\s,;]+)"),
)


def redact_sensitive_text(value: object) -> str:
    text = str(value or "")
    token = str(settings.BOT_TOKEN or "").strip()
    if token:
        text = text.replace(token, "[REDACTED_BOT_TOKEN]")
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    # Telegram bot tokens can leak in third-party exception URLs.
    text = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b", "[REDACTED_TELEGRAM_TOKEN]", text)
    return text


class SensitiveDataFilter(logging.Filter):
    """Redacts credentials and personal identifiers before records reach handlers."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_sensitive_text(record.msg)
            if record.args:
                def safe_arg(value):
                    if isinstance(value, str) or isinstance(value, BaseException):
                        return redact_sensitive_text(value)
                    return value
                if isinstance(record.args, dict):
                    record.args = {key: safe_arg(value) for key, value in record.args.items()}
                else:
                    record.args = tuple(safe_arg(value) for value in record.args)
        except Exception:
            # Logging must never stop the application.
            pass
        return True


def install_sensitive_log_filter() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(item, SensitiveDataFilter) for item in handler.filters):
            handler.addFilter(SensitiveDataFilter())


def _configured_origins() -> set[str]:
    origins: set[str] = set()
    for raw in str(settings.CORS_ALLOWED_ORIGINS or "").replace(";", ",").split(","):
        value = raw.strip().rstrip("/")
        if value and value != "*":
            origins.add(value)
    webapp_url = str(settings.WEBAPP_URL or "").strip()
    if webapp_url:
        parsed = urlparse(webapp_url)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    if settings.SECURITY_ALLOW_LOCAL_ORIGINS:
        origins.update({
            "http://localhost", "http://127.0.0.1",
            "http://localhost:3000", "http://127.0.0.1:3000",
            "http://localhost:8000", "http://127.0.0.1:8000",
        })
    return origins


def cors_origins() -> list[str]:
    return sorted(_configured_origins())


def origin_is_allowed(origin: str | None, host_url: str = "") -> bool:
    if not origin:
        return True
    origin = origin.strip().rstrip("/")
    if origin in _configured_origins():
        return True
    if host_url:
        parsed = urlparse(host_url)
        own_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        if origin == own_origin:
            return True
    return False



def pseudonymous_log_id(value: object) -> str:
    secret = str(settings.PRIVACY_HASH_SECRET or settings.BOT_TOKEN or "voxlyra")
    return hashlib.sha256(f"{secret}:{value}".encode("utf-8")).hexdigest()[:12]

def request_ip_hash(ip: str | None) -> str:
    source = str(ip or "unknown").strip()
    secret = str(settings.PRIVACY_HASH_SECRET or settings.COMIC_SIGNING_SECRET or settings.BOT_TOKEN or "voxlyra")
    return hashlib.sha256(f"{secret}:{source}".encode("utf-8")).hexdigest()


def make_confirmation_token() -> str:
    return secrets.token_urlsafe(32)


def hash_confirmation_token(token: str) -> str:
    secret = str(settings.PRIVACY_HASH_SECRET or settings.BOT_TOKEN or "voxlyra")
    return hashlib.sha256(f"{secret}:{token}".encode("utf-8")).hexdigest()


@dataclass
class _RateState:
    events: deque[float]


class RequestRateLimiter:
    """Small process-local limiter for sensitive API endpoints.

    It complements Telegram initData verification. It does not replace platform or
    reverse-proxy rate limits, but prevents accidental loops and basic request floods.
    """

    def __init__(self) -> None:
        self._events: dict[str, _RateState] = defaultdict(lambda: _RateState(deque()))
        self._seen_ids: OrderedDict[str, float] = OrderedDict()
        self._lock = Lock()

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        floor = now - max(1, int(window_seconds))
        with self._lock:
            state = self._events[key]
            while state.events and state.events[0] < floor:
                state.events.popleft()
            if len(state.events) >= max(1, int(limit)):
                return False
            state.events.append(now)
            return True

    def claim_request_id(self, subject: str, request_id: str, *, ttl_seconds: int = 900) -> bool:
        request_id = str(request_id or "").strip()
        if not request_id or len(request_id) > 160:
            return False
        now = time.monotonic()
        key = hashlib.sha256(f"{subject}:{request_id}".encode("utf-8")).hexdigest()
        with self._lock:
            while self._seen_ids:
                first_key, timestamp = next(iter(self._seen_ids.items()))
                if timestamp >= now - max(30, int(ttl_seconds)):
                    break
                self._seen_ids.pop(first_key, None)
            if key in self._seen_ids:
                return False
            self._seen_ids[key] = now
            if len(self._seen_ids) > 10000:
                self._seen_ids.popitem(last=False)
            return True


request_limiter = RequestRateLimiter()


def security_headers() -> dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
        "X-Permitted-Cross-Domain-Policies": "none",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "base-uri 'self'; object-src 'none'; form-action 'self'; "
            "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; "
            "img-src 'self' data: blob: https:; media-src 'self' blob: https:; "
            "connect-src 'self' https://api.telegram.org https://*.telegram.org; "
            "script-src 'self' 'unsafe-inline' https://telegram.org; "
            "style-src 'self' 'unsafe-inline'; font-src 'self' data:; worker-src 'self' blob:"
        ),
    }
