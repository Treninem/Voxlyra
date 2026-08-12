from __future__ import annotations

import pytest

import app.handlers  # installs the production freshness bootstrap
from app.services import github_import as gi
from app.services.github_import_freshness import refresh_resolved_package_cache


def test_production_import_entry_refreshes_stale_bulk_status():
    assert getattr(gi.import_package, "_voxlyra_refreshes_import_history", False) is True
    assert getattr(gi.import_package, "_voxlyra_package_serialized", False) is True


@pytest.mark.asyncio
async def test_freshness_guard_clears_only_resolved_status_and_restores_context():
    stale = {"pkg": object()}
    inventory = {"inventory": {"items": []}, "identity_id": 42}
    resolved_token = gi._RESOLVED_PACKAGES.set(stale)
    discovery_token = gi._DISCOVERY_CONTEXT.set(inventory)
    seen = {}

    async def fake_import(identity_id: int, package_id: str):
        seen["resolved"] = gi._RESOLVED_PACKAGES.get()
        seen["discovery"] = gi._DISCOVERY_CONTEXT.get()
        return package_id

    guarded = refresh_resolved_package_cache(fake_import)
    try:
        assert await guarded(42, "pkg") == "pkg"
        assert seen["resolved"] is None
        assert seen["discovery"] is inventory
        assert gi._RESOLVED_PACKAGES.get() is stale
        assert gi._DISCOVERY_CONTEXT.get() is inventory
    finally:
        gi._DISCOVERY_CONTEXT.reset(discovery_token)
        gi._RESOLVED_PACKAGES.reset(resolved_token)
