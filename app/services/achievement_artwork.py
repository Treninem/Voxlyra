from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from PIL import Image, UnidentifiedImageError

from app.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_ROOT = PROJECT_ROOT / "static" / "img" / "achievements"
MANIFEST_JSON = PROJECT_ROOT / "app" / "data" / "achievement_art_manifest.json"
MANIFEST_MD = PROJECT_ROOT / "app" / "data" / "achievement_art_manifest.md"
OVERRIDE_ROOT = Path(str(settings.ACHIEVEMENT_ARTWORK_STORAGE_ROOT or "data/achievement_artwork"))
if not OVERRIDE_ROOT.is_absolute():
    OVERRIDE_ROOT = PROJECT_ROOT / OVERRIDE_ROOT
BACKUP_ROOT = OVERRIDE_ROOT / "backups"

MAX_PNG_BYTES = 16 * 1024 * 1024
MAX_ZIP_BYTES = 256 * 1024 * 1024
MAX_ZIP_UNPACKED_BYTES = 768 * 1024 * 1024
MAX_ZIP_FILES = 120
EXPECTED_SIZE = (1024, 1024)
_ALLOWED_MODES = {"RGB", "RGBA", "P"}


class AchievementArtworkError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_artwork_manifest() -> dict[str, Any]:
    try:
        payload = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeError("Манифест изображений наград повреждён или отсутствует.") from exc
    items = payload.get("items")
    if not isinstance(items, list):
        raise RuntimeError("Манифест изображений наград не содержит список items.")
    return payload


@lru_cache(maxsize=1)
def replaceable_items_by_filename() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in load_artwork_manifest().get("items", []):
        if not isinstance(raw, dict):
            continue
        filename = safe_filename(raw.get("filename"))
        if filename:
            result[filename] = dict(raw)
    return result


@lru_cache(maxsize=1)
def replaceable_items_by_code() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in replaceable_items_by_filename().values():
        code = str(item.get("code") or "").strip()
        if code:
            result[code] = dict(item)
    return result


def safe_filename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or PurePosixPath(text).name != text:
        return ""
    if not text.lower().endswith(".png"):
        return ""
    if len(text) > 160 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in text):
        return ""
    return text


def bundled_path(filename: str) -> Path | None:
    clean = safe_filename(filename)
    if not clean:
        return None
    path = (BUNDLED_ROOT / clean).resolve()
    try:
        path.relative_to(BUNDLED_ROOT.resolve())
    except ValueError:
        return None
    return path if path.is_file() and path.stat().st_size > 0 else None


def override_path(filename: str) -> Path | None:
    clean = safe_filename(filename)
    if not clean:
        return None
    path = (OVERRIDE_ROOT / clean).resolve()
    try:
        path.relative_to(OVERRIDE_ROOT.resolve())
    except ValueError:
        return None
    return path if path.is_file() and path.stat().st_size > 0 else None


def effective_path(filename: str) -> Path | None:
    return override_path(filename) or bundled_path(filename)


def effective_version(filename: str) -> str:
    path = effective_path(filename)
    if not path:
        return "missing"
    stat = path.stat()
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def public_url(filename: str) -> str:
    clean = safe_filename(filename)
    if not clean:
        return ""
    return f"/media/achievements/{clean}?v={effective_version(clean)}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_png_path(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise AchievementArtworkError("Файл изображения не найден.")
    size_bytes = path.stat().st_size
    if size_bytes <= 0:
        raise AchievementArtworkError("PNG-файл пуст.")
    if size_bytes > MAX_PNG_BYTES:
        raise AchievementArtworkError("PNG превышает допустимый размер 16 МБ.")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if str(image.format or "").upper() != "PNG":
                raise AchievementArtworkError("Допускается только настоящий PNG, а не файл с переименованным расширением.")
            if tuple(image.size) != EXPECTED_SIZE:
                raise AchievementArtworkError(
                    f"Размер изображения должен быть строго {EXPECTED_SIZE[0]}×{EXPECTED_SIZE[1]} px; получено {image.size[0]}×{image.size[1]} px."
                )
            if image.mode not in _ALLOWED_MODES:
                raise AchievementArtworkError(
                    f"Цветовой режим {image.mode} не поддерживается. Используйте RGB, RGBA или индексированный PNG в sRGB."
                )
            if bool(getattr(image, "is_animated", False)):
                raise AchievementArtworkError("Анимированный PNG не поддерживается.")
            mode = str(image.mode)
            has_alpha = mode == "RGBA" or (mode == "P" and "transparency" in image.info)
    except AchievementArtworkError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise AchievementArtworkError("Файл повреждён или не является корректным PNG.") from exc
    return {
        "width": EXPECTED_SIZE[0],
        "height": EXPECTED_SIZE[1],
        "mode": mode,
        "has_alpha": has_alpha,
        "size_bytes": size_bytes,
        "sha256": _sha256(path),
    }


def _backup_existing(filename: str) -> Path | None:
    existing = override_path(filename)
    if not existing:
        return None
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = __import__("datetime").datetime.utcnow().strftime("%Y%m%dT%H%M%S%f")
    destination = BACKUP_ROOT / f"{Path(filename).stem}.{stamp}.png"
    shutil.copy2(existing, destination)
    backups = sorted(BACKUP_ROOT.glob(f"{Path(filename).stem}.*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in backups[5:]:
        stale.unlink(missing_ok=True)
    return destination


def install_png(filename: str, source_path: Path) -> dict[str, Any]:
    clean = safe_filename(filename)
    item = replaceable_items_by_filename().get(clean)
    if not item:
        raise AchievementArtworkError("Эта награда защищена или отсутствует в списке заменяемых изображений.")
    metadata = _validate_png_path(source_path)
    OVERRIDE_ROOT.mkdir(parents=True, exist_ok=True)
    destination = OVERRIDE_ROOT / clean
    temporary = OVERRIDE_ROOT / f".{clean}.{os.getpid()}.part"
    temporary.unlink(missing_ok=True)
    backup = _backup_existing(clean)
    try:
        shutil.copyfile(source_path, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "code": str(item.get("code") or ""),
        "filename": clean,
        "title": str(item.get("title") or ""),
        "backup_created": bool(backup),
        "url": public_url(clean),
        **metadata,
    }


def remove_override(filename: str) -> bool:
    clean = safe_filename(filename)
    if clean not in replaceable_items_by_filename():
        raise AchievementArtworkError("Эта награда защищена или отсутствует в списке заменяемых изображений.")
    existing = override_path(clean)
    if not existing:
        return False
    _backup_existing(clean)
    existing.unlink(missing_ok=True)
    return True


def import_zip(zip_path: Path) -> dict[str, Any]:
    if not zip_path.is_file() or zip_path.stat().st_size <= 0:
        raise AchievementArtworkError("ZIP-файл пуст или не найден.")
    if zip_path.stat().st_size > MAX_ZIP_BYTES:
        raise AchievementArtworkError("ZIP превышает допустимый размер 256 МБ.")
    if not zipfile.is_zipfile(zip_path):
        raise AchievementArtworkError("Файл не является корректным ZIP-архивом.")

    allowed = replaceable_items_by_filename()
    selected: list[tuple[zipfile.ZipInfo, str]] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    total_unpacked = 0
    with zipfile.ZipFile(zip_path) as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(entries) > MAX_ZIP_FILES:
            raise AchievementArtworkError(f"В ZIP слишком много файлов: максимум {MAX_ZIP_FILES}.")
        for entry in entries:
            total_unpacked += max(0, int(entry.file_size or 0))
            if total_unpacked > MAX_ZIP_UNPACKED_BYTES:
                raise AchievementArtworkError("Распакованный объём ZIP превышает 768 МБ.")
            raw_name = str(entry.filename or "").replace("\\", "/")
            filename = safe_filename(PurePosixPath(raw_name).name)
            if not filename:
                errors.append({"filename": raw_name or "?", "error": "Пропущен: требуется PNG с безопасным именем."})
                continue
            if filename in seen:
                errors.append({"filename": filename, "error": "Дубликат имени в ZIP."})
                continue
            seen.add(filename)
            if filename not in allowed:
                errors.append({"filename": filename, "error": "Файл не относится к заменяемым наградам или награда защищена."})
                continue
            if int(entry.file_size or 0) > MAX_PNG_BYTES:
                errors.append({"filename": filename, "error": "PNG превышает 16 МБ."})
                continue
            selected.append((entry, filename))

        validated: list[tuple[str, Path]] = []
        temp_dir = Path(tempfile.mkdtemp(prefix="voxlyra-achievement-art-"))
        try:
            for entry, filename in selected:
                target = temp_dir / filename
                try:
                    with archive.open(entry) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    _validate_png_path(target)
                    validated.append((filename, target))
                except (AchievementArtworkError, OSError, zipfile.BadZipFile) as exc:
                    errors.append({"filename": filename, "error": str(exc)})
            installed = [install_png(filename, target) for filename, target in validated]
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    return {"installed": installed, "errors": errors, "installed_count": len(installed), "error_count": len(errors)}


def build_status(catalog_items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    replaceable_by_code = replaceable_items_by_code()
    items: list[dict[str, Any]] = []
    protected = 0
    awaiting = 0
    custom = 0
    missing = 0
    ready = 0
    for raw in catalog_items:
        code = str(raw.get("code") or "").strip()
        icon_asset = str(raw.get("icon_asset") or "")
        filename = safe_filename(PurePosixPath(icon_asset.split("?", 1)[0]).name)
        manifest_item = replaceable_by_code.get(code)
        is_replaceable = bool(manifest_item)
        if manifest_item:
            filename = safe_filename(manifest_item.get("filename")) or filename
        overridden = bool(filename and override_path(filename))
        effective = effective_path(filename) if filename else None
        if not is_replaceable:
            status = "protected"
            protected += 1
        elif overridden:
            status = "custom"
            custom += 1
        else:
            status = "awaiting"
            awaiting += 1
        if not effective:
            status = "missing"
            missing += 1
        elif status in {"protected", "custom"}:
            ready += 1
        item = {
            "code": code,
            "title": str(raw.get("title") or (manifest_item or {}).get("title") or code),
            "description": str(raw.get("description") or (manifest_item or {}).get("condition") or ""),
            "condition": str((manifest_item or {}).get("condition") or raw.get("description") or ""),
            "composition": str((manifest_item or {}).get("composition") or ""),
            "filename": filename,
            "tier": str((manifest_item or {}).get("tier") or raw.get("tier_label") or raw.get("rarity") or ""),
            "goal": int((manifest_item or {}).get("goal") or raw.get("goal") or 1),
            "replaceable": is_replaceable,
            "status": status,
            "overridden": overridden,
            "source": "custom" if overridden else "bundled",
            "url": public_url(filename) if effective and filename else "",
            "size_bytes": int(effective.stat().st_size) if effective else 0,
        }
        items.append(item)
    order = {"awaiting": 0, "missing": 1, "custom": 2, "protected": 3}
    items.sort(key=lambda item: (order.get(str(item.get("status")), 9), str(item.get("tier")), str(item.get("title"))))
    return {
        "summary": {
            "total": len(items),
            "protected": protected,
            "replaceable": sum(1 for item in items if item["replaceable"]),
            "custom": custom,
            "awaiting": awaiting,
            "missing": missing,
            "ready": ready,
        },
        "items": items,
        "spec": dict(load_artwork_manifest().get("image_spec") or {}),
        "palettes": dict(load_artwork_manifest().get("tier_palettes") or {}),
    }


def manifest_file(format_name: str) -> Path:
    return MANIFEST_JSON if str(format_name).lower() == "json" else MANIFEST_MD
