"""Fresh-history guard for GitHub Import entry points.

Bulk discovery intentionally caches the remote inventory for speed. That cache must
not make package *status* stale after an overlapping import finishes: a second bulk
run waiting on the same package lock must re-read local import history before it
can decide that the package is still new.
"""

from __future__ import annotations

from functools import wraps
from typing import Awaitable, Callable, TypeVar

from app.services import github_import as _github_import

_T = TypeVar("_T")


def refresh_resolved_package_cache(
    func: Callable[..., Awaitable[_T]],
) -> Callable[..., Awaitable[_T]]:
    """Temporarily bypass a bulk run's pre-resolved status for one import.

    The remote inventory remains task-locally cached by ``_DISCOVERY_CONTEXT``;
    only ``_RESOLVED_PACKAGES`` is cleared. ``find_package`` therefore reuses the
    already downloaded inventory but re-applies current SQLite import history.
    """

    @wraps(func)
    async def wrapped(*args, **kwargs):
        token = _github_import._RESOLVED_PACKAGES.set(None)
        try:
            return await func(*args, **kwargs)
        finally:
            _github_import._RESOLVED_PACKAGES.reset(token)

    setattr(wrapped, "_voxlyra_refreshes_import_history", True)
    return wrapped


def install_github_import_freshness_guard() -> None:
    current = _github_import.import_package
    if getattr(current, "_voxlyra_refreshes_import_history", False):
        return
    _github_import.import_package = refresh_resolved_package_cache(current)


install_github_import_freshness_guard()
