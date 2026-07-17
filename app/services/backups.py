from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

BACKUP_ROOT = Path("storage/backups")
MANIFEST_NAME = "voxlyra_backup_manifest.json"


@dataclass(slots=True)
class BackupInfo:
    path: Path
    created_at: str
    size_bytes: int
    sha256: str


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _validate_sqlite(path: Path) -> None:
    con = sqlite3.connect(str(path))
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise ValueError(f"Ошибка целостности базы: {row[0] if row else 'нет ответа'}")
    finally:
        con.close()


def _create_backup_sync(include_storage: bool = True) -> BackupInfo:
    db_path = Path(settings.DATABASE_PATH)
    if not db_path.exists():
        raise FileNotFoundError(f"База не найдена: {db_path}")
    _validate_sqlite(db_path)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    target = BACKUP_ROOT / f"voxlyra_backup_{stamp}.zip"
    temp = target.with_suffix(".tmp")
    manifest = {
        "format": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": "data/voxlyra.sqlite3",
        "includes_storage": bool(include_storage),
    }
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.write(db_path, "data/voxlyra.sqlite3")
        if include_storage:
            storage = Path("storage")
            if storage.exists():
                for item in storage.rglob("*"):
                    if not item.is_file() or BACKUP_ROOT in item.parents:
                        continue
                    zf.write(item, item.as_posix())
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
    os.replace(temp, target)
    return BackupInfo(target, manifest["created_at"], target.stat().st_size, _sha256(target))


async def create_backup(include_storage: bool = True) -> BackupInfo:
    return await asyncio.to_thread(_create_backup_sync, include_storage)


def list_backups() -> list[BackupInfo]:
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    result: list[BackupInfo] = []
    for path in sorted(BACKUP_ROOT.glob("voxlyra_backup_*.zip"), reverse=True):
        result.append(BackupInfo(path, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(), path.stat().st_size, ""))
    return result


def _safe_member(name: str) -> bool:
    p = Path(name)
    return not p.is_absolute() and ".." not in p.parts and not name.startswith(("/", "\\"))


def _restore_backup_sync(archive: Path) -> dict[str, int]:
    if not zipfile.is_zipfile(archive):
        raise ValueError("Файл не является ZIP-архивом")
    with tempfile.TemporaryDirectory(prefix="voxlyra_restore_") as td:
        root = Path(td)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if MANIFEST_NAME not in names or "data/voxlyra.sqlite3" not in names:
                raise ValueError("Это не резервная копия VoxLyra")
            if any(not _safe_member(name) for name in names):
                raise ValueError("Архив содержит небезопасные пути")
            zf.extractall(root)
        restored_db = root / "data/voxlyra.sqlite3"
        _validate_sqlite(restored_db)
        current_db = Path(settings.DATABASE_PATH)
        current_db.parent.mkdir(parents=True, exist_ok=True)
        safety = BACKUP_ROOT / f"before_restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sqlite3"
        if current_db.exists():
            shutil.copy2(current_db, safety)
        os.replace(restored_db, current_db)
        restored_files = 0
        restored_storage = root / "storage"
        if restored_storage.exists():
            destination = Path("storage")
            for item in restored_storage.rglob("*"):
                if item.is_file() and "backups" not in item.parts:
                    rel = item.relative_to(restored_storage)
                    out = destination / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, out)
                    restored_files += 1
        return {"storage_files": restored_files, "safety_copy": int(safety.exists())}


async def restore_backup(archive: Path) -> dict[str, int]:
    return await asyncio.to_thread(_restore_backup_sync, archive)


def prune_backups(keep_daily: int = 7) -> int:
    backups = list_backups()
    removed = 0
    for info in backups[max(1, keep_daily):]:
        info.path.unlink(missing_ok=True)
        removed += 1
    return removed
