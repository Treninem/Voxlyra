from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import UploadFile

from app.config import settings
from app.services.book_parser import SUPPORTED_BOOK_EXTENSIONS
from app.services.graphic_types import SUPPORTED_GRAPHIC_EXTENSIONS

UPLOAD_ROOT = Path(str(getattr(settings, "CHUNK_UPLOAD_ROOT", "data/chunked_uploads") or "data/chunked_uploads"))
LEGACY_UPLOAD_ROOT = Path("storage/temp/chunked_book_uploads")
CHUNK_SIZE_BYTES = 6 * 1024 * 1024
MAX_CHUNKS = 10000
MIN_FREE_SPACE_BYTES = 32 * 1024 * 1024
STALE_UPLOAD_SECONDS = 24 * 60 * 60
_CHUNK_WRITE_SEMAPHORE = asyncio.Semaphore(
    max(1, int(getattr(settings, "CHUNK_UPLOAD_MAX_CONCURRENCY", 4) or 4))
)


class ChunkedUploadError(RuntimeError):
    pass


def _format_mb(value: int) -> str:
    return f"{max(0, int(value)) / 1024 / 1024:.1f} МБ"


def _migrate_legacy_upload_root() -> int:
    """Best-effort migration for in-place updates from the old storage path."""
    try:
        if LEGACY_UPLOAD_ROOT.resolve() == UPLOAD_ROOT.resolve() or not LEGACY_UPLOAD_ROOT.is_dir():
            return 0
    except OSError:
        return 0
    moved = 0
    for folder in list(LEGACY_UPLOAD_ROOT.iterdir()):
        if not folder.is_dir():
            continue
        target = UPLOAD_ROOT / folder.name
        if target.exists():
            continue
        try:
            os.replace(folder, target)
            moved += 1
        except OSError:
            try:
                shutil.copytree(folder, target)
                shutil.rmtree(folder, ignore_errors=True)
                moved += 1
            except OSError:
                shutil.rmtree(target, ignore_errors=True)
    return moved


def _ensure_upload_root() -> None:
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    _migrate_legacy_upload_root()


def _write_meta_file(folder: Path, meta: dict[str, Any]) -> None:
    """Atomically persist upload state and translate storage errors."""
    temp_path = folder / "meta.json.tmp"
    final_path = folder / "meta.json"
    try:
        temp_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(final_path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        raise _storage_write_error(exc) from exc


def _read_meta_file(folder: Path) -> dict[str, Any] | None:
    path = folder / "meta.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, dict) else None
    except Exception:
        return None


def cleanup_stale_uploads(*, max_age_seconds: int = STALE_UPLOAD_SECONDS) -> int:
    # Удаляет забытые части загрузок после обрыва или перезапуска.
    _ensure_upload_root()
    now = time.time()
    removed = 0
    for folder in UPLOAD_ROOT.iterdir():
        if not folder.is_dir():
            continue
        try:
            updated_at = folder.stat().st_mtime
            meta_path = folder / "meta.json"
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    raw_updated = str(meta.get("updated_at") or "")
                    if raw_updated:
                        updated_at = datetime.fromisoformat(
                            raw_updated.replace("Z", "+00:00")
                        ).timestamp()
                except Exception:
                    pass
            if now - updated_at < max(300, int(max_age_seconds)):
                continue
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed


def active_upload_count() -> int:
    """Return the number of live resumable upload folders."""
    _ensure_upload_root()
    count = 0
    for folder in UPLOAD_ROOT.iterdir():
        if folder.is_dir() and (folder / "meta.json").is_file():
            count += 1
    return count


def _ensure_upload_capacity() -> None:
    maximum = max(1, int(getattr(settings, "LIBRARY_IMPORT_MAX_ACTIVE_UPLOADS", 4) or 4))
    current = active_upload_count()
    if current >= maximum:
        raise ChunkedUploadError(
            f"Одновременно уже выполняется {current} крупных загрузок. "
            "Дождитесь завершения одной из них или продолжите ранее начатую загрузку."
        )


def _disk_reserve_bytes() -> int:
    configured = max(0, int(getattr(settings, "LIBRARY_IMPORT_MIN_FREE_DISK_MB", 256) or 0))
    return max(MIN_FREE_SPACE_BYTES, configured * 1024 * 1024)


def _ensure_free_space(required_bytes: int, *, operation: str) -> None:
    _ensure_upload_root()
    try:
        free = int(shutil.disk_usage(UPLOAD_ROOT).free)
    except OSError:
        return
    required = max(0, int(required_bytes))
    if free < required:
        raise ChunkedUploadError(
            f"На сервере недостаточно свободного места для {operation}. "
            f"Свободно {_format_mb(free)}, требуется не менее {_format_mb(required)}. "
            "Удалите старые временные файлы или увеличьте диск и повторите попытку."
        )


def _storage_write_error(exc: OSError) -> ChunkedUploadError:
    if exc.errno == errno.ENOSPC or "no space left" in str(exc).lower():
        return ChunkedUploadError(
            "На сервере закончилось свободное место. Незавершённая загрузка будет очищена. "
            "Освободите место или увеличьте диск и повторите попытку."
        )
    return ChunkedUploadError(f"Не удалось сохранить часть файла: {exc}")


def _safe_filename(value: str) -> str:
    name = Path(value or "book.txt").name
    stem = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._ -]+", "_", name).strip(" .")
    return (stem or "book.txt")[:180]


def _upload_dir(upload_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", upload_id or ""):
        raise ChunkedUploadError("Загрузка не найдена. Начните её заново.")
    return UPLOAD_ROOT / upload_id


def _create_upload(
    *,
    user_id: int,
    book_id: int,
    filename: str,
    total_size: int,
    allowed_extensions: set[str],
    max_mb: int,
    kind: str,
    error_text: str,
) -> dict[str, Any]:
    safe_name = _safe_filename(filename)
    ext = Path(safe_name).suffix.lower()
    if ext not in allowed_extensions:
        raise ChunkedUploadError(error_text)
    if total_size <= 0:
        raise ChunkedUploadError("Не удалось определить размер файла.")
    if max_mb > 0 and total_size > max_mb * 1024 * 1024:
        raise ChunkedUploadError(f"Файл превышает допустимый размер {max_mb} МБ.")

    cleanup_stale_uploads()
    _ensure_upload_capacity()
    _ensure_free_space(
        int(total_size) + _disk_reserve_bytes(),
        operation="загрузки архива",
    )

    upload_id = uuid.uuid4().hex
    folder = _upload_dir(upload_id)
    meta = {
        "upload_id": upload_id,
        "user_id": int(user_id),
        "book_id": int(book_id),
        "filename": safe_name,
        "total_size": int(total_size),
        "kind": kind,
        "received": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        folder.mkdir(parents=True, exist_ok=False)
        _write_meta_file(folder, meta)
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise _storage_write_error(exc) from exc
    except ChunkedUploadError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return meta


def create_upload(*, user_id: int, book_id: int, filename: str, total_size: int) -> dict[str, Any]:
    return _create_upload(
        user_id=user_id,
        book_id=book_id,
        filename=filename,
        total_size=total_size,
        allowed_extensions=set(SUPPORTED_BOOK_EXTENSIONS),
        max_mb=int(settings.MAX_BOOK_UPLOAD_MB or 0),
        kind="book",
        error_text="Поддерживаются TXT, DOCX, FB2, EPUB, PDF и ZIP.",
    )


def create_graphic_upload(*, user_id: int, book_id: int, filename: str, total_size: int) -> dict[str, Any]:
    return _create_upload(
        user_id=user_id,
        book_id=book_id,
        filename=filename,
        total_size=total_size,
        allowed_extensions=set(SUPPORTED_GRAPHIC_EXTENSIONS),
        max_mb=int(settings.MAX_COMIC_UPLOAD_MB or 0),
        kind="graphic",
        error_text="Поддерживаются PDF, CBZ, ZIP, JPG, PNG, WebP и AVIF.",
    )


def create_library_import_upload(
    *, user_id: int, filename: str, total_size: int, max_mb: int
) -> dict[str, Any]:
    return _create_upload(
        user_id=user_id,
        book_id=0,
        filename=filename,
        total_size=total_size,
        allowed_extensions={".zip"},
        max_mb=max(0, int(max_mb or 0)),
        kind="library_import",
        error_text="Для импорта библиотеки нужен ZIP-архив.",
    )


def create_or_resume_library_import_upload(
    *,
    user_id: int,
    filename: str,
    total_size: int,
    max_mb: int,
    resume_upload_id: str = "",
) -> tuple[dict[str, Any], bool]:
    """Resume one matching upload and discard obsolete sessions for the same admin."""
    safe_name = _safe_filename(filename)
    requested_id = str(resume_upload_id or "").strip()
    _ensure_upload_root()
    cleanup_stale_uploads(max_age_seconds=STALE_UPLOAD_SECONDS)

    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    for folder in UPLOAD_ROOT.iterdir():
        if not folder.is_dir():
            continue
        meta = _read_meta_file(folder)
        if not meta:
            continue
        if (
            int(meta.get("user_id") or 0) == int(user_id)
            and int(meta.get("book_id") or 0) == 0
            and str(meta.get("kind") or "") == "library_import"
        ):
            try:
                updated = datetime.fromisoformat(
                    str(meta.get("updated_at") or "").replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                try:
                    updated = folder.stat().st_mtime
                except OSError:
                    updated = 0.0
            candidates.append((updated, folder, meta))

    selected: tuple[Path, dict[str, Any]] | None = None
    for _, folder, meta in sorted(candidates, key=lambda item: item[0], reverse=True):
        if requested_id and str(meta.get("upload_id") or "") != requested_id:
            continue
        if (
            str(meta.get("filename") or "") == safe_name
            and int(meta.get("total_size") or 0) == int(total_size)
        ):
            selected = (folder, meta)
            break

    if selected is None and not requested_id:
        for _, folder, meta in sorted(candidates, key=lambda item: item[0], reverse=True):
            if (
                str(meta.get("filename") or "") == safe_name
                and int(meta.get("total_size") or 0) == int(total_size)
            ):
                selected = (folder, meta)
                break

    keep_folder = selected[0] if selected else None
    for _, folder, _ in candidates:
        if keep_folder is not None and folder == keep_folder:
            continue
        shutil.rmtree(folder, ignore_errors=True)

    if selected is not None:
        folder, meta = selected
        # A previously assembled file cannot be resumed as chunks.
        assembled = folder / _safe_filename(str(meta.get("filename") or safe_name))
        if assembled.exists():
            shutil.rmtree(folder, ignore_errors=True)
        else:
            return meta, True

    meta = create_library_import_upload(
        user_id=user_id,
        filename=safe_name,
        total_size=total_size,
        max_mb=max_mb,
    )
    return meta, False


def claim_upload_finish(
    upload_id: str, *, user_id: int, book_id: int, stale_seconds: int = 10 * 60
) -> bool:
    """Межпроцессная защита от одновременной сборки одного набора частей."""
    load_upload(upload_id, user_id=user_id, book_id=book_id)
    folder = _upload_dir(upload_id)
    lock_path = folder / "finish.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            if time.time() - lock_path.stat().st_mtime > max(60, int(stale_seconds)):
                lock_path.unlink(missing_ok=True)
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            else:
                return False
        except (FileExistsError, OSError):
            return False
    try:
        os.write(descriptor, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}".encode("utf-8"))
    finally:
        os.close(descriptor)
    return True


def load_upload(upload_id: str, *, user_id: int, book_id: int) -> dict[str, Any]:
    folder = _upload_dir(upload_id)
    path = folder / "meta.json"
    if not path.exists():
        raise ChunkedUploadError("Загрузка не найдена. Начните её заново.")
    meta = _read_meta_file(folder)
    if meta is None:
        raise ChunkedUploadError("Не удалось продолжить загрузку. Начните её заново.")
    if int(meta.get("user_id") or 0) != int(user_id) or int(meta.get("book_id") or 0) != int(book_id):
        raise ChunkedUploadError("Эта загрузка вам недоступна.")
    return meta


def _save_meta(upload_id: str, meta: dict[str, Any]) -> None:
    folder = _upload_dir(upload_id)
    _write_meta_file(folder, meta)


async def save_chunk(
    upload_id: str,
    *,
    user_id: int,
    book_id: int,
    index: int,
    total_chunks: int,
    chunk: UploadFile,
) -> dict[str, Any]:
    if index < 0 or total_chunks <= 0 or total_chunks > MAX_CHUNKS or index >= total_chunks:
        raise ChunkedUploadError("Не удалось принять часть файла. Начните загрузку заново.")
    meta = load_upload(upload_id, user_id=user_id, book_id=book_id)
    folder = _upload_dir(upload_id)
    part_path = folder / f"{index:06d}.part"
    written = 0
    try:
        await asyncio.to_thread(
            _ensure_free_space,
            max(2 * 1024 * 1024, _disk_reserve_bytes()),
            operation="приёма следующей части архива",
        )
        async with _CHUNK_WRITE_SEMAPHORE:
            async with aiofiles.open(part_path, "wb") as destination:
                while True:
                    data = await chunk.read(1024 * 1024)
                    if not data:
                        break
                    written += len(data)
                    if written > CHUNK_SIZE_BYTES + 1024:
                        raise ChunkedUploadError("Часть файла оказалась слишком большой. Повторите загрузку.")
                    await destination.write(data)
    except OSError as exc:
        await asyncio.to_thread(part_path.unlink, missing_ok=True)
        raise _storage_write_error(exc) from exc
    except Exception:
        await asyncio.to_thread(part_path.unlink, missing_ok=True)
        raise
    finally:
        await chunk.close()

    received = {int(value) for value in meta.get("received", [])}
    received.add(index)
    meta["received"] = sorted(received)
    meta["total_chunks"] = int(total_chunks)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(_save_meta, upload_id, meta)
    return {"received_chunks": len(received), "total_chunks": total_chunks, "chunk_bytes": written}



def get_upload_status(upload_id: str, *, user_id: int, book_id: int) -> dict[str, Any]:
    meta = load_upload(upload_id, user_id=user_id, book_id=book_id)
    total_chunks = max(0, int(meta.get("total_chunks") or 0))
    received = sorted({int(value) for value in meta.get("received", []) if int(value) >= 0})
    missing = [index for index in range(total_chunks) if index not in set(received)] if total_chunks else []
    total_size = max(0, int(meta.get("total_size") or 0))
    received_bytes = 0
    folder = _upload_dir(upload_id)
    for index in received:
        part = folder / f"{index:06d}.part"
        if part.is_file():
            received_bytes += int(part.stat().st_size)
    return {
        "upload_id": str(meta.get("upload_id") or upload_id),
        "filename": str(meta.get("filename") or ""),
        "total_size": total_size,
        "total_chunks": total_chunks,
        "received": received,
        "missing": missing,
        "received_bytes": min(received_bytes, total_size) if total_size else received_bytes,
        "progress_percent": round((received_bytes / total_size) * 100, 1) if total_size else 0.0,
        "updated_at": str(meta.get("updated_at") or ""),
    }

def assemble_upload(upload_id: str, *, user_id: int, book_id: int, total_chunks: int) -> tuple[Path, dict[str, Any]]:
    meta = load_upload(upload_id, user_id=user_id, book_id=book_id)
    if int(meta.get("total_chunks") or total_chunks) != int(total_chunks):
        raise ChunkedUploadError("Количество частей файла не совпало. Повторите загрузку.")
    received = {int(value) for value in meta.get("received", [])}
    expected = set(range(total_chunks))
    if received != expected:
        raise ChunkedUploadError("Загружены не все части файла. Повторите попытку.")

    folder = _upload_dir(upload_id)
    final_path = folder / _safe_filename(str(meta["filename"]))
    final_path.unlink(missing_ok=True)
    part_sizes: list[int] = []
    for index in range(total_chunks):
        part_path = folder / f"{index:06d}.part"
        if not part_path.exists():
            raise ChunkedUploadError("Не найдена часть файла. Повторите загрузку.")
        part_sizes.append(int(part_path.stat().st_size))

    # Части удаляются сразу после добавления в итоговый файл. Поэтому во время
    # сборки на диске находится примерно один размер ZIP, а не две полные копии.
    _ensure_free_space(
        max(part_sizes or [0]) + 8 * 1024 * 1024,
        operation="сборки архива",
    )
    total_written = 0
    try:
        with final_path.open("wb") as destination:
            for index in range(total_chunks):
                part_path = folder / f"{index:06d}.part"
                part_size = int(part_path.stat().st_size)
                with part_path.open("rb") as source:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                total_written += part_size
                part_path.unlink(missing_ok=True)
    except OSError as exc:
        final_path.unlink(missing_ok=True)
        raise _storage_write_error(exc) from exc

    expected_size = int(meta.get("total_size") or 0)
    if expected_size and total_written != expected_size:
        final_path.unlink(missing_ok=True)
        raise ChunkedUploadError("Файл загрузился не полностью. Повторите попытку.")
    return final_path, meta


def cleanup_upload(upload_id: str) -> None:
    try:
        shutil.rmtree(_upload_dir(upload_id), ignore_errors=True)
    except ChunkedUploadError:
        pass
