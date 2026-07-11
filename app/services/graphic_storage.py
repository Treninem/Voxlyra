from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.config import settings


def graphic_storage_root() -> Path:
    """Корень страниц можно вынести на отдельный persistent volume хоста."""
    return Path(str(settings.COMIC_STORAGE_ROOT or "storage/comics"))


def safe_graphic_path(value: str, *, root: Path | None = None) -> Path | None:
    try:
        storage_root = (root or graphic_storage_root()).resolve()
        path = Path(str(value or "")).resolve()
        path.relative_to(storage_root)
    except (OSError, ValueError):
        return None
    return path


def parse_variants(value: Any, *, root: Path | None = None) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        raw = value
    else:
        try:
            raw = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return result
    for label, item in raw.items():
        if not isinstance(item, dict):
            continue
        path = safe_graphic_path(str(item.get("path") or ""), root=root)
        if not path:
            continue
        result[str(label)] = {
            "path": str(path),
            "width": max(0, int(item.get("width") or 0)),
            "height": max(0, int(item.get("height") or 0)),
            "file_size": max(0, int(item.get("file_size") or 0)),
            "checksum": str(item.get("checksum") or ""),
            "mime_type": str(item.get("mime_type") or "image/webp"),
        }
    return result


def variants_json(variants: dict[str, dict[str, Any]]) -> str:
    clean: dict[str, dict[str, Any]] = {}
    for label, item in variants.items():
        clean[str(label)] = {
            "path": str(item.get("path") or ""),
            "width": max(0, int(item.get("width") or 0)),
            "height": max(0, int(item.get("height") or 0)),
            "file_size": max(0, int(item.get("file_size") or 0)),
            "checksum": str(item.get("checksum") or ""),
            "mime_type": str(item.get("mime_type") or "image/webp"),
        }
    return json.dumps(clean, ensure_ascii=False, separators=(",", ":"))


def _prepared_variants(prepared: Any) -> dict[str, dict[str, Any]]:
    variants = getattr(prepared, "variants", None) or {}
    if not variants:
        variants = {
            "large": {
                "path": str(prepared.path),
                "width": int(prepared.width),
                "height": int(prepared.height),
                "file_size": int(prepared.file_size),
                "checksum": str(prepared.checksum),
                "mime_type": str(prepared.mime_type or "image/webp"),
            }
        }
    return {str(label): dict(item) for label, item in variants.items() if isinstance(item, dict)}


def install_prepared_page(prepared: Any, primary_target: Path) -> dict[str, Any]:
    """Атомарно устанавливает все размеры страницы и возвращает данные для БД."""
    source_variants = _prepared_variants(prepared)
    primary_target.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        source_variants.items(),
        key=lambda pair: (int(pair[1].get("width") or 0), str(pair[0])),
    )
    primary_label = ordered[-1][0]
    token = uuid.uuid4().hex
    stage_dir = primary_target.parent / f".{primary_target.stem}-stage-{token}"
    stage_dir.mkdir(parents=True, exist_ok=False)
    staged: dict[str, tuple[Path, Path, dict[str, Any]]] = {}
    backups: list[tuple[Path, Path]] = []
    try:
        for label, item in ordered:
            source = Path(str(item.get("path") or ""))
            if not source.is_file():
                raise FileNotFoundError(source)
            final = primary_target if label == primary_label else primary_target.with_name(
                f"{primary_target.stem}-{label}{primary_target.suffix}"
            )
            stage = stage_dir / final.name
            shutil.copy2(source, stage)
            staged[label] = (stage, final, item)

        # Убираем прежние файлы этой страницы в резерв до успешной установки всех размеров.
        candidates = [primary_target]
        candidates.extend(primary_target.parent.glob(f"{primary_target.stem}-*{primary_target.suffix}"))
        for old in candidates:
            if not old.is_file():
                continue
            backup = old.with_name(f".{old.name}.backup-{token}")
            os.replace(old, backup)
            backups.append((backup, old))

        result_variants: dict[str, dict[str, Any]] = {}
        for label, (stage, final, item) in staged.items():
            os.replace(stage, final)
            result_variants[label] = {
                "path": str(final),
                "width": int(item.get("width") or 0),
                "height": int(item.get("height") or 0),
                "file_size": int(final.stat().st_size),
                "checksum": str(item.get("checksum") or ""),
                "mime_type": str(item.get("mime_type") or "image/webp"),
            }
        for backup, _ in backups:
            backup.unlink(missing_ok=True)
    except Exception:
        for _, final, _ in staged.values():
            final.unlink(missing_ok=True)
        for backup, old in reversed(backups):
            if backup.exists():
                os.replace(backup, old)
        raise
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)

    primary = result_variants[primary_label]
    return {
        "file_path": str(primary_target),
        "mime_type": str(primary.get("mime_type") or "image/webp"),
        "width": int(primary.get("width") or 0),
        "height": int(primary.get("height") or 0),
        "file_size": int(primary.get("file_size") or 0),
        "checksum": str(primary.get("checksum") or ""),
        "variants": result_variants,
        "variants_json": variants_json(result_variants),
        "storage_backend": "local",
        "storage_key": str(primary_target),
    }


def select_page_variant(
    row: Any, requested: str = "auto", target_width: int = 0, *, root: Path | None = None
) -> dict[str, Any] | None:
    try:
        raw_variants = row["variants_json"]
    except Exception:
        raw_variants = "{}"
    variants = parse_variants(raw_variants, root=root)
    if variants:
        request = str(requested or "auto").lower()
        if request in variants:
            candidate = variants[request]
        else:
            ordered = sorted(variants.values(), key=lambda item: int(item.get("width") or 0))
            width = max(0, int(target_width or 0))
            candidate = ordered[-1]
            if width > 0:
                candidate = next((item for item in ordered if int(item.get("width") or 0) >= width), ordered[-1])
        path = safe_graphic_path(str(candidate.get("path") or ""), root=root)
        if path and path.is_file():
            return {**candidate, "path": path}

    try:
        legacy_path = safe_graphic_path(str(row["file_path"] or ""), root=root)
        if legacy_path and legacy_path.is_file():
            return {
                "path": legacy_path,
                "width": int(row["width"] or 0),
                "height": int(row["height"] or 0),
                "file_size": int(row["file_size"] or 0),
                "checksum": str(row["checksum"] or ""),
                "mime_type": str(row["mime_type"] or "image/webp"),
            }
    except Exception:
        return None
    return None


def public_variant_info(row: Any, *, root: Path | None = None) -> dict[str, dict[str, Any]]:
    try:
        variants = parse_variants(row["variants_json"], root=root)
    except Exception:
        variants = {}
    return {
        label: {
            "width": int(item.get("width") or 0),
            "height": int(item.get("height") or 0),
            "file_size": int(item.get("file_size") or 0),
            "checksum": str(item.get("checksum") or ""),
        }
        for label, item in variants.items()
    }


def delete_page_files(row: Any, *, root: Path | None = None) -> None:
    paths: set[Path] = set()
    try:
        primary = safe_graphic_path(str(row["file_path"] or ""), root=root)
        if primary:
            paths.add(primary)
    except Exception:
        pass
    try:
        for item in parse_variants(row["variants_json"]).values():
            path = safe_graphic_path(str(item.get("path") or ""), root=root)
            if path:
                paths.add(path)
    except Exception:
        pass
    for path in paths:
        path.unlink(missing_ok=True)
