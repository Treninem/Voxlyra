from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass

from app.config import settings


class LibraryImportUploadTokenError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LibraryImportUploadToken:
    telegram_id: int
    chat_id: int
    progress_message_id: int
    expires_at: int
    nonce: str


def _secret() -> bytes:
    value = (
        settings.BOT_TOKEN.strip()
        or settings.COMIC_SIGNING_SECRET.strip()
        or settings.TTS_SIGNING_SECRET.strip()
    )
    if not value:
        raise LibraryImportUploadTokenError("Не настроен секрет защищённой загрузки")
    return hashlib.sha256(("voxlyra-library-import:" + value).encode("utf-8")).digest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode((value + padding).encode("ascii"))
    except Exception as exc:
        raise LibraryImportUploadTokenError("Ссылка загрузки повреждена") from exc


def create_library_import_upload_token(
    *,
    telegram_id: int,
    chat_id: int,
    progress_message_id: int,
    ttl_seconds: int = 60 * 60,
) -> str:
    now = int(time.time())
    payload = {
        "v": 1,
        "telegram_id": int(telegram_id),
        "chat_id": int(chat_id),
        "progress_message_id": int(progress_message_id),
        "expires_at": now + max(300, min(int(ttl_seconds), 24 * 60 * 60)),
        "nonce": uuid.uuid4().hex,
    }
    encoded = _b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _b64encode(hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_library_import_upload_token(token: str) -> LibraryImportUploadToken:
    try:
        encoded, received_signature = str(token or "").split(".", 1)
    except ValueError as exc:
        raise LibraryImportUploadTokenError("Ссылка загрузки недействительна") from exc
    expected_signature = _b64encode(
        hmac.new(_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(received_signature, expected_signature):
        raise LibraryImportUploadTokenError("Ссылка загрузки не прошла проверку")
    try:
        payload = json.loads(_b64decode(encoded).decode("utf-8"))
        result = LibraryImportUploadToken(
            telegram_id=int(payload["telegram_id"]),
            chat_id=int(payload["chat_id"]),
            progress_message_id=int(payload["progress_message_id"]),
            expires_at=int(payload["expires_at"]),
            nonce=str(payload["nonce"]),
        )
    except Exception as exc:
        raise LibraryImportUploadTokenError("Ссылка загрузки повреждена") from exc
    if int(payload.get("v") or 0) != 1:
        raise LibraryImportUploadTokenError("Версия ссылки загрузки не поддерживается")
    if result.expires_at < int(time.time()):
        raise LibraryImportUploadTokenError("Ссылка загрузки устарела. Отправьте ZIP боту ещё раз")
    if result.telegram_id <= 0 or result.chat_id == 0 or result.progress_message_id <= 0:
        raise LibraryImportUploadTokenError("Ссылка загрузки содержит неверные данные")
    if len(result.nonce) != 32:
        raise LibraryImportUploadTokenError("Ссылка загрузки содержит неверные данные")
    return result
