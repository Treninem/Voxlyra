from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _key() -> bytes:
    configured = settings.DATA_ENCRYPTION_KEY.strip().encode("utf-8")
    if configured:
        try:
            Fernet(configured)
            return configured
        except Exception as exc:
            raise RuntimeError("DATA_ENCRYPTION_KEY должен быть корректным Fernet-ключом.") from exc
    seed = f"voxlyra:{settings.BOT_TOKEN}:{settings.COMIC_SIGNING_SECRET}:{settings.TTS_SIGNING_SECRET}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


def encrypt_text(value: str) -> str:
    clean = str(value or "")
    if not clean:
        return ""
    return Fernet(_key()).encrypt(clean.encode("utf-8")).decode("ascii")


def decrypt_text(value: str) -> str:
    clean = str(value or "")
    if not clean:
        return ""
    try:
        return Fernet(_key()).decrypt(clean.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        return ""


def mask_phone(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 7:
        return "номер не указан"
    return f"+{digits[0]} ••• •••-{digits[-4:-2]}-{digits[-2:]}"
