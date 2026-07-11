from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.config import settings
from app.services.book_parser import SUPPORTED_BOOK_EXTENSIONS
from app.services.graphic_import import SUPPORTED_GRAPHIC_EXTENSIONS

UPLOAD_ROOT = Path("storage/temp/chunked_book_uploads")
CHUNK_SIZE_BYTES = 6 * 1024 * 1024
MAX_CHUNKS = 10000


class ChunkedUploadError(RuntimeError):
    pass


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

    upload_id = uuid.uuid4().hex
    folder = _upload_dir(upload_id)
    folder.mkdir(parents=True, exist_ok=False)
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
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
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


def load_upload(upload_id: str, *, user_id: int, book_id: int) -> dict[str, Any]:
    folder = _upload_dir(upload_id)
    path = folder / "meta.json"
    if not path.exists():
        raise ChunkedUploadError("Загрузка не найдена. Начните её заново.")
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ChunkedUploadError("Не удалось продолжить загрузку. Начните её заново.") from exc
    if int(meta.get("user_id") or 0) != int(user_id) or int(meta.get("book_id") or 0) != int(book_id):
        raise ChunkedUploadError("Эта загрузка вам недоступна.")
    return meta


def _save_meta(upload_id: str, meta: dict[str, Any]) -> None:
    folder = _upload_dir(upload_id)
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


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
        with part_path.open("wb") as destination:
            while True:
                data = await chunk.read(1024 * 1024)
                if not data:
                    break
                written += len(data)
                if written > CHUNK_SIZE_BYTES + 1024:
                    raise ChunkedUploadError("Часть файла оказалась слишком большой. Повторите загрузку.")
                destination.write(data)
    except Exception:
        part_path.unlink(missing_ok=True)
        raise
    finally:
        await chunk.close()

    received = {int(value) for value in meta.get("received", [])}
    received.add(index)
    meta["received"] = sorted(received)
    meta["total_chunks"] = int(total_chunks)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_meta(upload_id, meta)
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
    total_written = 0
    with final_path.open("wb") as destination:
        for index in range(total_chunks):
            part_path = folder / f"{index:06d}.part"
            if not part_path.exists():
                raise ChunkedUploadError("Не найдена часть файла. Повторите загрузку.")
            with part_path.open("rb") as source:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            total_written += part_path.stat().st_size

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
