from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.config import settings
from app.services.audio_tools import probe_duration_seconds

TTS_CACHE_VERSION = "1"
TTS_VOICES: dict[str, dict[str, str | int]] = {
    "anna": {"label": "Анна", "engine": "ru+f3", "gender": "female", "pitch": 52},
    "elena": {"label": "Елена", "engine": "ru+f4", "gender": "female", "pitch": 58},
    "alexey": {"label": "Алексей", "engine": "ru+m3", "gender": "male", "pitch": 42},
    "mikhail": {"label": "Михаил", "engine": "ru+m7", "gender": "male", "pitch": 36},
}
DEFAULT_TTS_VOICE = "anna"
MAX_TTS_TEXT_CHARS = 350_000

_locks: dict[str, asyncio.Lock] = {}


class ReaderTTSError(RuntimeError):
    pass


@dataclass(slots=True)
class TTSAsset:
    path: Path
    duration_seconds: int
    voice: str
    text_hash: str


def available_voices() -> list[dict[str, Any]]:
    return [
        {"code": code, "label": str(meta["label"]), "gender": str(meta["gender"])}
        for code, meta in TTS_VOICES.items()
    ]


def validate_voice(value: str | None) -> str:
    voice = str(value or DEFAULT_TTS_VOICE).strip().lower()
    return voice if voice in TTS_VOICES else DEFAULT_TTS_VOICE


def clean_chapter_text(value: str) -> str:
    """Возвращает только литературный текст главы без HTML и интерфейсных элементов.

    В функцию передаётся исключительно поле chapters.text. Заголовки страницы, кнопки,
    рекламные блоки и комментарии в этот поток технически не попадают.
    """
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for tag in soup.find_all(["script", "style", "button", "nav", "aside", "form", "input", "select", "textarea", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00ad", "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > MAX_TTS_TEXT_CHARS:
        raise ReaderTTSError("Глава слишком большая для озвучивания одним файлом.")
    if len(text) < 2:
        raise ReaderTTSError("В главе нет текста для озвучивания.")
    return text


def tts_engine_status() -> dict[str, Any]:
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg = shutil.which("ffmpeg")
    enabled = bool(settings.TTS_ENABLED and espeak and ffmpeg)
    return {
        "enabled": enabled,
        "espeak": bool(espeak),
        "ffmpeg": bool(ffmpeg),
        "message": "Озвучивание готово" if enabled else "Локальный движок озвучивания пока недоступен",
    }


def _cache_root() -> Path:
    root = Path(settings.TTS_CACHE_DIR or "storage/tts")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _text_hash(text: str, voice: str) -> str:
    payload = f"{TTS_CACHE_VERSION}\0{voice}\0{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cache_path(chapter_id: int, text: str, voice: str) -> Path:
    voice = validate_voice(voice)
    digest = _text_hash(text, voice)
    folder = _cache_root() / f"chapter_{int(chapter_id)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}_{voice}.mp3"


def _run_generation(chapter_id: int, text: str, voice: str) -> TTSAsset:
    status = tts_engine_status()
    if not status["enabled"]:
        raise ReaderTTSError(status["message"])
    clean = clean_chapter_text(text)
    voice = validate_voice(voice)
    target = cache_path(chapter_id, clean, voice)
    digest = _text_hash(clean, voice)
    if target.exists() and target.stat().st_size > 1024:
        os.utime(target, None)
        return TTSAsset(target, probe_duration_seconds(target), voice, digest)

    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    ffmpeg = shutil.which("ffmpeg")
    assert espeak and ffmpeg
    meta = TTS_VOICES[voice]
    target.parent.mkdir(parents=True, exist_ok=True)

    temp_root = Path("storage/temp")
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="voxlyra_tts_", dir=str(temp_root)) as tmp_name:
        tmp = Path(tmp_name)
        text_file = tmp / "chapter.txt"
        wav_file = tmp / "chapter.wav"
        mp3_file = tmp / "chapter.mp3"
        text_file.write_text(clean, encoding="utf-8")
        speak_cmd = [
            espeak,
            "-v", str(meta["engine"]),
            "-s", "168",
            "-p", str(meta["pitch"]),
            "-a", "155",
            "-f", str(text_file),
            "-w", str(wav_file),
        ]
        try:
            subprocess.run(speak_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(wav_file), "-ac", "1", "-ar", "24000",
                    "-codec:a", "libmp3lame", "-b:a", "64k", str(mp3_file),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=900,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ReaderTTSError("Не удалось создать озвучивание этой главы.") from exc
        if not mp3_file.exists() or mp3_file.stat().st_size <= 1024:
            raise ReaderTTSError("Озвучивание создалось некорректно.")
        temp_target = target.with_suffix(".part")
        shutil.copyfile(mp3_file, temp_target)
        os.replace(temp_target, target)

    cleanup_tts_cache()
    return TTSAsset(target, probe_duration_seconds(target), voice, digest)


async def generate_chapter_tts(chapter_id: int, text: str, voice: str | None = None) -> TTSAsset:
    clean = clean_chapter_text(text)
    selected = validate_voice(voice)
    key = f"{chapter_id}:{_text_hash(clean, selected)}"
    lock = _locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            return await asyncio.to_thread(_run_generation, chapter_id, clean, selected)
    finally:
        if not lock.locked():
            _locks.pop(key, None)


def cleanup_tts_cache() -> None:
    root = _cache_root()
    max_age = max(1, int(settings.TTS_CACHE_DAYS or 30)) * 86400
    max_bytes = max(128, int(settings.TTS_MAX_CACHE_MB or 2048)) * 1024 * 1024
    now = time.time()
    files: list[Path] = []
    total = 0
    for path in root.rglob("*.mp3"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime > max_age:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        files.append(path)
        total += stat.st_size
    if total <= max_bytes:
        return
    files.sort(key=lambda item: item.stat().st_mtime)
    for path in files:
        if total <= max_bytes:
            break
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            continue


def _signing_secret() -> bytes:
    raw = settings.TTS_SIGNING_SECRET.strip() or settings.BOT_TOKEN.strip() or "voxlyra-local-tts"
    return hashlib.sha256(raw.encode("utf-8")).digest()


def sign_media_token(*, user_id: int, chapter_id: int, voice: str, expires_at: int) -> str:
    payload = f"{int(user_id)}:{int(chapter_id)}:{validate_voice(voice)}:{int(expires_at)}"
    return hmac.new(_signing_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_media_token(*, user_id: int, chapter_id: int, voice: str, expires_at: int, signature: str) -> bool:
    if int(expires_at) < int(time.time()) or int(expires_at) > int(time.time()) + 86400:
        return False
    expected = sign_media_token(user_id=user_id, chapter_id=chapter_id, voice=voice, expires_at=expires_at)
    return hmac.compare_digest(expected, str(signature or ""))


def build_media_url(*, user_id: int, chapter_id: int, voice: str, lifetime_seconds: int = 86400) -> str:
    from urllib.parse import urlencode

    expires_at = int(time.time()) + max(300, min(86400, int(lifetime_seconds)))
    selected = validate_voice(voice)
    signature = sign_media_token(
        user_id=user_id,
        chapter_id=chapter_id,
        voice=selected,
        expires_at=expires_at,
    )
    query = urlencode({"uid": int(user_id), "voice": selected, "exp": expires_at, "sig": signature})
    return f"/media/reader-tts/{int(chapter_id)}.mp3?{query}"
