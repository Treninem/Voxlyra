from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

SOURCE_UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024
SOURCE_UPLOAD_TTL_SECONDS = 2 * 60 * 60
SOURCE_UPLOAD_STALE_SECONDS = 24 * 60 * 60
SOURCE_FINISH_LOCK_STALE_SECONDS = 15 * 60
_SOURCE_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class GitHubSourceUploadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GitHubSourceUploadToken:
    telegram_id: int
    chat_id: int
    expires_at: int
    nonce: str


def _root() -> Path:
    return Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import")) / "source_web_uploads"


def _secret() -> bytes:
    value = (
        str(settings.BOT_TOKEN or "").strip()
        or str(settings.COMIC_SIGNING_SECRET or "").strip()
        or str(settings.TTS_SIGNING_SECRET or "").strip()
    )
    if not value:
        raise GitHubSourceUploadError("Не настроен секрет защищённой source-загрузки")
    return hashlib.sha256(("voxlyra-github-source-upload:" + value).encode("utf-8")).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
    except Exception as exc:
        raise GitHubSourceUploadError("Ссылка source-загрузки повреждена") from exc


def create_github_source_upload_token(
    *, telegram_id: int, chat_id: int, ttl_seconds: int = SOURCE_UPLOAD_TTL_SECONDS
) -> str:
    if not settings.is_system_owner(int(telegram_id)):
        raise GitHubSourceUploadError("Source-загрузка доступна только системному владельцу")
    now = int(time.time())
    payload = {
        "v": 1,
        "purpose": "github_source_publish",
        "telegram_id": int(telegram_id),
        "chat_id": int(chat_id),
        "expires_at": now + max(300, min(int(ttl_seconds), 24 * 60 * 60)),
        "nonce": uuid.uuid4().hex,
    }
    encoded = _b64encode(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = _b64encode(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_github_source_upload_token(token: str) -> GitHubSourceUploadToken:
    try:
        encoded, received = str(token or "").split(".", 1)
    except ValueError as exc:
        raise GitHubSourceUploadError("Ссылка source-загрузки недействительна") from exc
    expected = _b64encode(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(received, expected):
        raise GitHubSourceUploadError("Ссылка source-загрузки не прошла проверку")
    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        result = GitHubSourceUploadToken(
            telegram_id=int(payload["telegram_id"]),
            chat_id=int(payload["chat_id"]),
            expires_at=int(payload["expires_at"]),
            nonce=str(payload["nonce"]),
        )
    except Exception as exc:
        if isinstance(exc, GitHubSourceUploadError):
            raise
        raise GitHubSourceUploadError("Ссылка source-загрузки повреждена") from exc
    if int(payload.get("v") or 0) != 1 or payload.get("purpose") != "github_source_publish":
        raise GitHubSourceUploadError("Назначение ссылки source-загрузки не поддерживается")
    if result.expires_at < int(time.time()):
        raise GitHubSourceUploadError("Ссылка source-загрузки устарела. Откройте её из бота заново")
    if not settings.is_system_owner(result.telegram_id):
        raise GitHubSourceUploadError("Source-загрузка доступна только системному владельцу")
    if result.chat_id == 0 or not re.fullmatch(r"[0-9a-f]{32}", result.nonce):
        raise GitHubSourceUploadError("Ссылка source-загрузки содержит неверные данные")
    return result


def _safe_filename(value: str) -> str:
    name = Path(str(value or "source.zip")).name.strip()
    name = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._ -]+", "_", name).strip(" .")
    return (name or "source.zip")[:180]


def _session_dir(upload_id: str) -> Path:
    if not _SOURCE_UPLOAD_ID_RE.fullmatch(str(upload_id or "")):
        raise GitHubSourceUploadError("Source-загрузка не найдена")
    return _root() / upload_id


def _meta_path(upload_id: str) -> Path:
    return _session_dir(upload_id) / "meta.json"


def _write_meta(folder: Path, meta: dict[str, Any]) -> None:
    temp = folder / "meta.json.tmp"
    try:
        temp.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, folder / "meta.json")
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise GitHubSourceUploadError(f"Не удалось сохранить состояние source-загрузки: {exc}") from exc


def _read_meta(upload_id: str) -> dict[str, Any]:
    path = _meta_path(upload_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GitHubSourceUploadError("Source-загрузка не найдена или уже очищена") from exc
    except Exception as exc:
        raise GitHubSourceUploadError("Состояние source-загрузки повреждено") from exc
    if not isinstance(data, dict):
        raise GitHubSourceUploadError("Состояние source-загрузки повреждено")
    return data


def cleanup_stale_github_source_uploads(*, max_age_seconds: int = SOURCE_UPLOAD_STALE_SECONDS) -> int:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    removed = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        try:
            lock = folder / "finish.lock"
            if lock.is_file():
                lock_age = now - lock.stat().st_mtime
                # An active atomic publish owns the session. Only an abandoned
                # finish lock may be removed by stale-session maintenance.
                if lock_age <= SOURCE_FINISH_LOCK_STALE_SECONDS:
                    continue
            age = now - folder.stat().st_mtime
            meta = folder / "meta.json"
            if meta.is_file():
                try:
                    payload = json.loads(meta.read_text(encoding="utf-8"))
                    updated = str(payload.get("updated_at") or "")
                    if updated:
                        age = now - datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp()
                except Exception:
                    pass
            if age >= max(300, int(max_age_seconds)):
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def _reserve_bytes() -> int:
    return max(32, int(settings.GITHUB_IMPORT_MIN_FREE_DISK_MB or 256)) * 1024 * 1024


def _ensure_disk(required_bytes: int) -> None:
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        free = int(shutil.disk_usage(root).free)
    except OSError:
        return
    required = max(0, int(required_bytes)) + _reserve_bytes()
    if free < required:
        raise GitHubSourceUploadError(
            f"Недостаточно места для source-загрузки: свободно {free // 1024 // 1024} МБ, "
            f"нужно не менее {required // 1024 // 1024} МБ"
        )


def create_github_source_upload(
    *, token: GitHubSourceUploadToken, filename: str, total_size: int
) -> dict[str, Any]:
    if not bool(settings.GITHUB_SOURCE_WRITE_ENABLED):
        raise GitHubSourceUploadError("Source ZIP → GitHub выключен")
    if not str(settings.GITHUB_SOURCE_WRITE_TOKEN or "").strip():
        raise GitHubSourceUploadError("GITHUB_SOURCE_WRITE_TOKEN не настроен")
    safe_name = _safe_filename(filename)
    if not safe_name.lower().endswith(".zip"):
        raise GitHubSourceUploadError("Для source-публикации нужен ZIP-файл")
    size = int(total_size or 0)
    if size <= 0:
        raise GitHubSourceUploadError("Не удалось определить размер ZIP")
    maximum = max(1, int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB or 512)) * 1024 * 1024
    if size > maximum:
        raise GitHubSourceUploadError(
            f"ZIP превышает source-write лимит {int(settings.GITHUB_SOURCE_WRITE_MAX_PACKAGE_MB)} МБ"
        )
    cleanup_stale_github_source_uploads()
    _ensure_disk(size)
    root = _root()
    upload_id = uuid.uuid4().hex
    folder = root / upload_id
    folder.mkdir(parents=True, exist_ok=False)
    meta = {
        "v": 1,
        "kind": "github_source_publish",
        "upload_id": upload_id,
        "telegram_id": token.telegram_id,
        "chat_id": token.chat_id,
        "token_nonce": token.nonce,
        "filename": safe_name,
        "total_size": size,
        "chunk_size": SOURCE_UPLOAD_CHUNK_SIZE_BYTES,
        "received": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _write_meta(folder, meta)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return meta


def load_github_source_upload(upload_id: str, *, token: GitHubSourceUploadToken) -> dict[str, Any]:
    meta = _read_meta(upload_id)
    if (
        meta.get("kind") != "github_source_publish"
        or int(meta.get("telegram_id") or 0) != token.telegram_id
        or str(meta.get("token_nonce") or "") != token.nonce
    ):
        raise GitHubSourceUploadError("Эта source-загрузка недоступна")
    return meta


def save_github_source_chunk(
    upload_id: str, *, token: GitHubSourceUploadToken, index: int, data: bytes
) -> dict[str, Any]:
    meta = load_github_source_upload(upload_id, token=token)
    index = int(index)
    total = int(meta["total_size"])
    chunk_size = int(meta["chunk_size"])
    total_chunks = (total + chunk_size - 1) // chunk_size
    if index < 0 or index >= total_chunks:
        raise GitHubSourceUploadError("Неверный номер части source-загрузки")
    expected = min(chunk_size, total - index * chunk_size)
    if len(data) != expected:
        raise GitHubSourceUploadError(
            f"Размер части #{index + 1} не совпадает: получено {len(data)}, ожидалось {expected}"
        )
    _ensure_disk(len(data))
    folder = _session_dir(upload_id)
    final = folder / f"part-{index:06d}.bin"
    temp = folder / f"part-{index:06d}.tmp"
    try:
        temp.write_bytes(data)
        os.replace(temp, final)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise GitHubSourceUploadError(f"Не удалось сохранить часть source-загрузки: {exc}") from exc
    received = {int(value) for value in meta.get("received", []) if str(value).isdigit()}
    received.add(index)
    meta["received"] = sorted(received)
    meta["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_meta(folder, meta)
    return meta


def assemble_github_source_upload(upload_id: str, *, token: GitHubSourceUploadToken) -> Path:
    meta = load_github_source_upload(upload_id, token=token)
    folder = _session_dir(upload_id)
    total = int(meta["total_size"])
    chunk_size = int(meta["chunk_size"])
    total_chunks = (total + chunk_size - 1) // chunk_size
    received = {int(value) for value in meta.get("received", []) if str(value).isdigit()}
    missing = [index for index in range(total_chunks) if index not in received]
    if missing:
        raise GitHubSourceUploadError(f"Не загружено частей: {len(missing)}")
    _ensure_disk(total)
    assembled = folder / "source-package.zip"
    temp = folder / "source-package.zip.tmp"
    try:
        with temp.open("wb") as output:
            for index in range(total_chunks):
                part = folder / f"part-{index:06d}.bin"
                if not part.is_file():
                    raise GitHubSourceUploadError(f"Потеряна часть #{index + 1}")
                with part.open("rb") as stream:
                    shutil.copyfileobj(stream, output, length=1024 * 1024)
        if temp.stat().st_size != total:
            raise GitHubSourceUploadError("Собранный source ZIP имеет неверный размер")
        os.replace(temp, assembled)
    except GitHubSourceUploadError:
        temp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise GitHubSourceUploadError(f"Не удалось собрать source ZIP: {exc}") from exc
    return assembled


def claim_github_source_finish(upload_id: str, *, token: GitHubSourceUploadToken) -> bool:
    load_github_source_upload(upload_id, token=token)
    lock = _session_dir(upload_id) / "finish.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            if time.time() - lock.stat().st_mtime > SOURCE_FINISH_LOCK_STALE_SECONDS:
                lock.unlink(missing_ok=True)
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            else:
                return False
        except OSError:
            return False
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
    finally:
        os.close(descriptor)
    return True


def release_github_source_finish(upload_id: str) -> None:
    try:
        (_session_dir(upload_id) / "finish.lock").unlink(missing_ok=True)
    except GitHubSourceUploadError:
        pass


def cleanup_github_source_upload(upload_id: str) -> None:
    try:
        shutil.rmtree(_session_dir(upload_id), ignore_errors=True)
    except GitHubSourceUploadError:
        pass
