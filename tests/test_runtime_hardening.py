from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


async def _http_call(application, path: str, headers: list[tuple[bytes, bytes]] | None = None):
    messages: list[dict] = []
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 3000),
    }
    await application(scope, receive, send)
    start = next(item for item in messages if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return start, json.loads(body.decode("utf-8"))


@pytest.mark.asyncio
async def test_readiness_is_strict_while_liveness_stays_available_during_bootstrap():
    from main import DeferredVoxLyraApplication

    application = DeferredVoxLyraApplication()
    health_start, health = await _http_call(application, "/health")
    ready_start, readiness = await _http_call(application, "/readiness")

    assert health_start["status"] == 200
    assert health["ok"] is True
    assert health["ready"] is False
    assert ready_start["status"] == 503
    assert readiness["ok"] is False
    assert readiness["ready"] is False

    application.database_ready = True
    application.application_ready = True
    application.stage = "ready"
    ready_start, readiness = await _http_call(application, "/readyz")
    assert ready_start["status"] == 200
    assert readiness["ok"] is True
    assert readiness["ready"] is True


@pytest.mark.asyncio
async def test_terminal_bootstrap_failure_fails_liveness_and_never_leaks_raw_error():
    from app.services.runtime_state import redact_runtime_error
    from main import DeferredVoxLyraApplication

    application = DeferredVoxLyraApplication()
    application.stage = "failed"
    application.error = redact_runtime_error("token=super-secret-value database unavailable")
    start, payload = await _http_call(application, "/healthz")
    assert start["status"] == 503
    assert payload["ok"] is False
    assert "super-secret-value" not in payload["startup_error"]
    assert "***" in payload["startup_error"]


@pytest.mark.asyncio
async def test_runtime_boundary_adds_correlation_and_build_headers():
    from app.build_info import OWNER_BUILD_VERSION
    from main import DeferredVoxLyraApplication

    application = DeferredVoxLyraApplication()
    start, _ = await _http_call(application, "/health", [(b"x-request-id", b"request-12345678")])
    headers = dict(start["headers"])
    assert headers[b"x-request-id"] == b"request-12345678"
    assert headers[b"x-voxlyra-version"] == OWNER_BUILD_VERSION.encode("ascii")


@pytest.mark.asyncio
async def test_runtime_boundary_rejects_unsafe_upstream_request_id():
    from main import DeferredVoxLyraApplication

    application = DeferredVoxLyraApplication()
    start, _ = await _http_call(application, "/health", [(b"x-request-id", b"bad id with spaces")])
    headers = dict(start["headers"])
    generated = headers[b"x-request-id"].decode("ascii")
    assert generated != "bad id with spaces"
    assert len(generated) == 32


def test_runtime_state_tracks_components_and_redacts_configured_secrets(monkeypatch):
    from app.config import settings
    from app.services.runtime_state import (
        mark_component_failed,
        mark_component_ready,
        mark_component_starting,
        runtime_snapshot,
    )

    monkeypatch.setattr(settings, "GITHUB_SOURCE_WRITE_TOKEN", "github-secret-123")
    mark_component_starting("database")
    mark_component_ready("database")
    mark_component_failed("vk", "token=github-secret-123 failed")
    snapshot = runtime_snapshot()
    assert snapshot["database"]["status"] == "ready"
    assert snapshot["database"]["attempts"] >= 1
    assert snapshot["vk"]["status"] == "failed"
    assert "github-secret-123" not in snapshot["vk"]["last_error"]


def test_preflight_accepts_writable_runtime_layout(tmp_path, monkeypatch):
    from app.config import settings
    from scripts.runtime_preflight import PERSISTENT_SETTING_NAMES, run_preflight

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "db" / "voxlyra.sqlite3"))
    monkeypatch.setattr(settings, "RUNTIME_MIN_FREE_DISK_MB", 16)
    for index, name in enumerate(PERSISTENT_SETTING_NAMES):
        monkeypatch.setattr(settings, name, str(tmp_path / f"persistent-{index}"))

    results = run_preflight(create_paths=True)
    critical_failures = [item for item in results if not item.ok and item.severity == "critical"]
    assert critical_failures == []
    assert (tmp_path / "db").is_dir()


def test_container_and_startup_contract_use_shared_runtime_checks():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    start = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")

    assert "python scripts/healthcheck.py --path /health" in dockerfile
    assert "python -m compileall -q app scripts main.py" in dockerfile
    assert "set -eu" in start
    assert "python scripts/runtime_preflight.py" in start
    for name in (
        "RUNTIME_MIN_FREE_DISK_MB",
        "RUNTIME_SLOW_REQUEST_MS",
        "RUNTIME_MAX_CONCURRENCY",
        "RUNTIME_KEEPALIVE_SECONDS",
        "RUNTIME_LISTEN_BACKLOG",
    ):
        assert name in env_example
        assert name in config
