from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.config import settings
from app.services.canonical_source_sync import TARGETS, _build_package, _find_book

router = APIRouter()
_TARGETS = {target.package_id: target for target in TARGETS}


@router.get("/api/canonical-source-export/{package_id}", include_in_schema=False)
async def canonical_source_export(package_id: str) -> Response:
    """Export only explicitly allow-listed canonical system-owner books.

    The destination repository is public content storage, so these two approved
    packages can be fetched server-to-server without exposing a write token.
    No arbitrary book id/title/path is accepted by this endpoint.
    """
    target = _TARGETS.get(str(package_id).strip())
    if target is None:
        raise HTTPException(status_code=404, detail="Canonical source package not found")

    book = await _find_book(target)
    if not book:
        raise HTTPException(status_code=404, detail="Canonical finished book is not present on this deployment")

    temp_parent = Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import")) / "canonical_exports"
    temp_parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=f"{target.package_id}-", dir=temp_parent) as folder:
            archive, _ = await _build_package(target, book, Path(folder))
            payload = await asyncio.to_thread(archive.read_bytes)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Canonical source export storage error") from exc

    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{target.package_id}.source.zip"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
