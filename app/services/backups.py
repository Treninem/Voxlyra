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
        "format": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_version": str(settings.PROJECT_VERSION),
        "database": "data/voxlyra.sqlite3",
        "database_sha256": _sha256(db_path),
        "database_size": db_path.stat().st_size,
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
            infos = zf.infolist()
            names = [item.filename for item in infos]
            if MANIFEST_NAME not in names or "data/voxlyra.sqlite3" not in names:
                raise ValueError("Это не резервная копия VoxLyra")
            if len(infos) > max(100, int(settings.BACKUP_MAX_FILES)):
                raise ValueError("В резервной копии слишком много файлов")
            max_unpacked = max(64, int(settings.BACKUP_MAX_UNPACKED_MB)) * 1024 * 1024
            if sum(max(0, int(item.file_size)) for item in infos) > max_unpacked:
                raise ValueError("Резервная копия превышает разрешённый распакованный размер")
            if any(not _safe_member(name) for name in names):
                raise ValueError("Архив содержит небезопасные пути")
            for item in infos:
                mode = (item.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError("Архив содержит символическую ссылку")
            try:
                manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Манифест резервной копии повреждён") from exc
            if int(manifest.get("format") or 0) not in {1, 2}:
                raise ValueError("Формат резервной копии не поддерживается")
            zf.extractall(root)
        restored_db = root / "data/voxlyra.sqlite3"
        _validate_sqlite(restored_db)
        expected_db_hash = str(manifest.get("database_sha256") or "")
        if expected_db_hash and _sha256(restored_db) != expected_db_hash:
            raise ValueError("Контрольная сумма базы не совпадает с манифестом")
        current_db = Path(settings.DATABASE_PATH)
        current_db.parent.mkdir(parents=True, exist_ok=True)
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        safety = BACKUP_ROOT / f"before_restore_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sqlite3"
        had_current = current_db.exists()
        if had_current:
            shutil.copy2(current_db, safety)
        restored_files = 0
        try:
            staged_db = current_db.with_suffix(current_db.suffix + ".restore.tmp")
            shutil.copy2(restored_db, staged_db)
            os.replace(staged_db, current_db)
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
            _validate_sqlite(current_db)
        except Exception:
            if had_current and safety.exists():
                shutil.copy2(safety, current_db)
            elif current_db.exists():
                current_db.unlink(missing_ok=True)
            raise
        return {
            "storage_files": restored_files,
            "safety_copy": int(safety.exists()),
            "manifest_format": int(manifest.get("format") or 1),
        }


async def restore_backup(archive: Path) -> dict[str, int]:
    return await asyncio.to_thread(_restore_backup_sync, archive)


def prune_backups(keep_daily: int = 7) -> int:
    backups = list_backups()
    removed = 0
    for info in backups[max(1, keep_daily):]:
        info.path.unlink(missing_ok=True)
        removed += 1
    return removed
