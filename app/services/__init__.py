"""Shared service package guards.

GitHub Import is system-owner only, but Telegram callbacks can still be repeated or
a bulk run can overlap with a single-package action. The downloader uses a
deterministic temporary directory per package+commit, so two simultaneous imports
of the same package must not enter the workflow together.
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Awaitable, Callable, TypeVar

_T = TypeVar("_T")


def serialize_package_import(
    func: Callable[..., Awaitable[_T]],
) -> Callable[..., Awaitable[_T]]:
    """Serialize one async import function by package_id and refresh its status.

    Different packages remain parallel-capable. For the same package the lock
    covers discovery-status recheck, download, import, rollback/finalize and
    cleanup. A bulk run may carry a task-local resolved package snapshot created
    before it waited on the lock; clearing only that resolved-status cache after
    lock acquisition makes ``find_package`` re-apply current SQLite history while
    still reusing the already downloaded remote inventory.
    """
    locks: dict[str, asyncio.Lock] = {}

    @wraps(func)
    async def wrapped(identity_id: int, package_id: str, *args, **kwargs):
        key = str(package_id).strip()
        lock = locks.setdefault(key, asyncio.Lock())
        async with lock:
            token = _github_import._RESOLVED_PACKAGES.set(None)
            try:
                return await func(identity_id, package_id, *args, **kwargs)
            finally:
                _github_import._RESOLVED_PACKAGES.reset(token)

    setattr(wrapped, "_voxlyra_package_serialized", True)
    setattr(wrapped, "_voxlyra_package_history_fresh", True)
    setattr(wrapped, "_voxlyra_package_locks", locks)
    return wrapped


# Install the guard at package import time so every entry point (single import,
# bulk import, retry and future internal callers) sees the same wrapped module
# function. The original service remains the canonical business implementation.
from app.services import github_import as _github_import  # noqa: E402

if not getattr(_github_import.import_package, "_voxlyra_package_serialized", False):
    _github_import.import_package = serialize_package_import(_github_import.import_package)
