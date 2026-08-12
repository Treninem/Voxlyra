from __future__ import annotations

import asyncio

import pytest

from app.services import github_import as gi
from app.services import serialize_package_import


def test_runtime_github_import_is_serialized_by_package():
    assert getattr(gi.import_package, "_voxlyra_package_serialized", False) is True


@pytest.mark.asyncio
async def test_same_package_cannot_overlap_but_different_packages_can():
    active_by_package: dict[str, int] = {}
    peak_by_package: dict[str, int] = {}
    entered_two_packages = asyncio.Event()
    active_packages: set[str] = set()
    global_peak = 0
    global_active = 0

    async def fake_import(identity_id: int, package_id: str, *, delay: float = 0.03):
        nonlocal global_active, global_peak
        active_by_package[package_id] = active_by_package.get(package_id, 0) + 1
        peak_by_package[package_id] = max(
            peak_by_package.get(package_id, 0), active_by_package[package_id]
        )
        global_active += 1
        global_peak = max(global_peak, global_active)
        active_packages.add(package_id)
        if len(active_packages) >= 2:
            entered_two_packages.set()
        try:
            await asyncio.sleep(delay)
            return package_id
        finally:
            active_by_package[package_id] -= 1
            global_active -= 1
            if active_by_package[package_id] == 0:
                active_packages.discard(package_id)

    guarded = serialize_package_import(fake_import)
    results = await asyncio.gather(
        guarded(1, "same", delay=0.05),
        guarded(1, "same", delay=0.05),
        guarded(1, "other", delay=0.05),
    )

    assert sorted(results) == ["other", "same", "same"]
    assert peak_by_package["same"] == 1
    assert peak_by_package["other"] == 1
    assert entered_two_packages.is_set()
    assert global_peak >= 2
