from __future__ import annotations

import asyncio

import pytest

from app.services import github_import as gi
from app.services import serialize_package_import


def test_production_import_entry_serializes_and_refreshes_history():
    assert getattr(gi.import_package, "_voxlyra_package_serialized", False) is True
    assert getattr(gi.import_package, "_voxlyra_package_history_fresh", False) is True


@pytest.mark.asyncio
async def test_package_lock_clears_only_resolved_status_and_restores_context():
    stale = {"pkg": object()}
    inventory = {"inventory": {"items": []}, "identity_id": 42}
    resolved_token = gi._RESOLVED_PACKAGES.set(stale)
    discovery_token = gi._DISCOVERY_CONTEXT.set(inventory)
    seen = {}

    async def fake_import(identity_id: int, package_id: str):
        seen["resolved"] = gi._RESOLVED_PACKAGES.get()
        seen["discovery"] = gi._DISCOVERY_CONTEXT.get()
        return package_id

    guarded = serialize_package_import(fake_import)
    try:
        assert await guarded(42, "pkg") == "pkg"
        assert seen["resolved"] is None
        assert seen["discovery"] is inventory
        assert gi._RESOLVED_PACKAGES.get() is stale
        assert gi._DISCOVERY_CONTEXT.get() is inventory
    finally:
        gi._DISCOVERY_CONTEXT.reset(discovery_token)
        gi._RESOLVED_PACKAGES.reset(resolved_token)


@pytest.mark.asyncio
async def test_same_package_is_serialized_but_different_packages_can_overlap():
    active: set[str] = set()
    same_key_overlap = False
    different_key_overlap = False
    gate = asyncio.Event()

    async def fake_import(identity_id: int, package_id: str):
        nonlocal same_key_overlap, different_key_overlap
        key = str(package_id)
        if key in active:
            same_key_overlap = True
        if active and key not in active:
            different_key_overlap = True
        active.add(key)
        if key == "a":
            gate.set()
        await asyncio.sleep(0.03)
        active.remove(key)
        return key

    guarded = serialize_package_import(fake_import)
    first = asyncio.create_task(guarded(42, "a"))
    await gate.wait()
    second_same = asyncio.create_task(guarded(42, "a"))
    different = asyncio.create_task(guarded(42, "b"))
    assert sorted(await asyncio.gather(first, second_same, different)) == ["a", "a", "b"]
    assert same_key_overlap is False
    assert different_key_overlap is True
