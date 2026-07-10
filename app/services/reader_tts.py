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

# Версия кэша повышена, чтобы старые ускоренные и искажённые MP3 не использовались.
TTS_CACHE_VERSION = "2-piper-literary"
TTS_VOICES: dict[str, dict[str, str]] = {
    "irina": {
        "label": "Ирина · женский",
        "model": "ru_RU-irina-medium.onnx",
        "gender": "female",
    },
    "dmitri": {
        "label": "Дмитрий · мужской",
        "model": "ru_RU-dmitri-medium.onnx",
        "gender": "male",
    },
}
VOICE_ALIASES = {
    "anna": "irina",
    "elena": "irina",
    "alexey": "dmitri",
    "mikhail": "dmitri",
}
TTS_STYLES: dict[str, dict[str, float | str]] = {
    "natural": {
        "label": "Естественно",
        "length_factor": 1.00,
        "noise_scale": 0.62,
        "noise_w_scale": 0.78,
        "sentence_silence": 0.32,
    },
    "expressive": {
        "label": "С выражением",
        "length_factor": 1.06,
        "noise_scale": 0.70,
        "noise_w_scale": 0.90,
        "sentence_silence": 0.42,
    },
    "calm": {
        "label": "Спокойно",
        "length_factor": 1.13,
        "noise_scale": 0.54,
        "noise_w_scale": 0.66,
        "sentence_silence": 0.48,
    },
}
TTS_RATES = (0.75, 0.90, 1.00, 1.15, 1.30, 1.45)
DEFAULT_TTS_VOICE = "irina"
DEFAULT_TTS_STYLE = "expressive"
DEFAULT_TTS_RATE = 1.0
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
    rate: float = DEFAULT_TTS_RATE
    style: str = DEFAULT_TTS_STYLE


def available_voices() -> list[dict[str, Any]]:
    return [
        {"code": code, "label": str(meta["label"]), "gender": str(meta["gender"])}
        for code, meta in TTS_VOICES.items()
    ]


def available_styles() -> list[dict[str, Any]]:
    return [
        {"code": code, "label": str(meta["label"])}
        for code, meta in TTS_STYLES.items()
    ]


def available_rates() -> list[float]:
    return list(TTS_RATES)


def validate_voice(value: str | None) -> str:
    voice = str(value or DEFAULT_TTS_VOICE).strip().lower()
    voice = VOICE_ALIASES.get(voice, voice)
    return voice if voice in TTS_VOICES else DEFAULT_TTS_VOICE


def validate_style(value: str | None) -> str:
    style = str(value or DEFAULT_TTS_STYLE).strip().lower()
    return style if style in TTS_STYLES else DEFAULT_TTS_STYLE


def validate_rate(value: float | str | None) -> float:
    try:
        rate = float(value if value is not None else DEFAULT_TTS_RATE)
    except (TypeError, ValueError):
        rate = DEFAULT_TTS_RATE
    return min(TTS_RATES, key=lambda item: abs(item - rate))


def clean_chapter_text(value: str) -> str:
    """Возвращает только литературный текст главы без HTML и интерфейсных элементов."""
    soup = BeautifulSoup(str(value or ""), "html.parser")
    for tag in soup.find_all([
        "script", "style", "button", "nav", "aside", "form", "input", "select",
        "textarea", "iframe", "audio", "video", "figure", "figcaption",
    ]):
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


def prepare_literary_text(value: str) -> str:
    """Подготавливает прозу для нейросетевого чтения, не меняя смысл текста.

    Сохраняются диалоги и авторская пунктуация. Добавляются только безопасные паузы
    между абзацами и исправляются технические пробелы, из-за которых синтезатор
    проглатывает окончания или читает несколько предложений слитно.
    """
    clean = clean_chapter_text(value)
    clean = clean.replace("...", "…")
    clean = re.sub(r"\.{4,}", "…", clean)
    clean = re.sub(r"([!?]){3,}", lambda match: match.group(1) * 2, clean)
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"([,.;:!?])(?=[А-Яа-яЁёA-Za-z])", r"\1 ", clean)
    clean = re.sub(r"(?m)^[-–]\s*", "— ", clean)

    paragraphs: list[str] = []
    for raw in re.split(r"\n+", clean):
        paragraph = re.sub(r"\s+", " ", raw).strip()
        if not paragraph:
            continue
        # Отдельный декоративный разделитель превращается в паузу, а не произносится.
        if re.fullmatch(r"[*#=_~•·\-–— ]{3,}", paragraph):
            paragraphs.append("…")
            continue
        # Короткие заголовочные строки получают естественную финальную паузу.
        if len(paragraph) <= 90 and not re.search(r"[.!?…»\"]$", paragraph):
            paragraph += "."
        paragraphs.append(paragraph)

    prepared = "\n\n".join(paragraphs).strip()
    if len(prepared) < 2:
        raise ReaderTTSError("В главе нет текста для озвучивания.")
    return prepared


def _model_root() -> Path:
    return Path(settings.TTS_MODEL_DIR or "/opt/voxlyra-voices")


def _voice_model_files(voice: str) -> tuple[Path, Path]:
    selected = validate_voice(voice)
    model = _model_root() / str(TTS_VOICES[selected]["model"])
    return model, Path(str(model) + ".json")


def tts_engine_status() -> dict[str, Any]:
    piper = shutil.which("piper")
    ffmpeg = shutil.which("ffmpeg")
    models: dict[str, bool] = {}
    for code in TTS_VOICES:
        model, config = _voice_model_files(code)
        models[code] = bool(
            model.exists() and model.stat().st_size > 1024
            and config.exists() and config.stat().st_size > 0
        )
    enabled = bool(settings.TTS_ENABLED and piper and ffmpeg and all(models.values()))
    return {
        "enabled": enabled,
        "engine": "piper",
        "piper": bool(piper),
        "ffmpeg": bool(ffmpeg),
        "models": models,
        "message": (
            "Естественное озвучивание готово"
            if enabled
            else "Модели естественного озвучивания не установлены. Выполните Redeploy."
        ),
    }


def _cache_root() -> Path:
    root = Path(settings.TTS_CACHE_DIR or "storage/tts")
    root.mkdir(parents=True, exist_ok=True)
    return root


def tts_profile_key(voice: str, rate: float | str | None = None, style: str | None = None) -> str:
    return f"{validate_voice(voice)}:{validate_style(style)}:{validate_rate(rate):.2f}"


def _text_hash(text: str, voice: str, rate: float, style: str) -> str:
    payload = f"{TTS_CACHE_VERSION}\0{voice}\0{style}\0{rate:.2f}\0{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cache_path(
    chapter_id: int,
    text: str,
    voice: str,
    rate: float | str | None = None,
    style: str | None = None,
) -> Path:
    selected_voice = validate_voice(voice)
    selected_rate = validate_rate(rate)
    selected_style = validate_style(style)
    digest = _text_hash(text, selected_voice, selected_rate, selected_style)
    folder = _cache_root() / f"chapter_{int(chapter_id)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}_{selected_voice}_{selected_style}_{selected_rate:.2f}.mp3"


def _run_generation(
    chapter_id: int,
    text: str,
    voice: str,
    rate: float = DEFAULT_TTS_RATE,
    style: str = DEFAULT_TTS_STYLE,
) -> TTSAsset:
    status = tts_engine_status()
    if not status["enabled"]:
        raise ReaderTTSError(str(status["message"]))

    prepared = prepare_literary_text(text)
    selected_voice = validate_voice(voice)
    selected_rate = validate_rate(rate)
    selected_style = validate_style(style)
    target = cache_path(chapter_id, prepared, selected_voice, selected_rate, selected_style)
    digest = _text_hash(prepared, selected_voice, selected_rate, selected_style)
    if target.exists() and target.stat().st_size > 1024:
        os.utime(target, None)
        return TTSAsset(
            target,
            probe_duration_seconds(target),
            selected_voice,
            digest,
            selected_rate,
            selected_style,
        )

    piper = shutil.which("piper")
    ffmpeg = shutil.which("ffmpeg")
    model, config = _voice_model_files(selected_voice)
    if not piper or not ffmpeg or not model.exists() or not config.exists():
        raise ReaderTTSError("Естественное озвучивание не установлено. Выполните Redeploy.")

    profile = TTS_STYLES[selected_style]
    # Скорость формируется самим синтезатором. Поэтому голос не становится писклявым,
    # глухим или смазанным, как при ускорении уже готового MP3 в браузере.
    length_scale = max(0.72, min(1.60, float(profile["length_factor"]) / selected_rate))
    target.parent.mkdir(parents=True, exist_ok=True)

    temp_root = Path("storage/temp")
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="voxlyra_tts_", dir=str(temp_root)) as tmp_name:
        tmp = Path(tmp_name)
        text_file = tmp / "chapter.txt"
        wav_file = tmp / "chapter.wav"
        mp3_file = tmp / "chapter.mp3"
        text_file.write_text(prepared, encoding="utf-8")
        speak_cmd = [
            piper,
            "--model", str(model),
            "--config", str(config),
            "--input-file", str(text_file),
            "--output-file", str(wav_file),
            "--length-scale", f"{length_scale:.3f}",
            "--noise-scale", f"{float(profile['noise_scale']):.3f}",
            "--noise-w-scale", f"{float(profile['noise_w_scale']):.3f}",
            "--sentence-silence", f"{float(profile['sentence_silence']):.3f}",
        ]
        try:
            subprocess.run(speak_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
            subprocess.run(
                [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(wav_file),
                    "-af", "highpass=f=55,lowpass=f=11500,loudnorm=I=-18:LRA=7:TP=-2",
                    "-ac", "1", "-ar", "24000",
                    "-codec:a", "libmp3lame", "-b:a", "96k", str(mp3_file),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=1800,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ReaderTTSError("Не удалось создать естественное озвучивание этой главы.") from exc
        if not mp3_file.exists() or mp3_file.stat().st_size <= 1024:
            raise ReaderTTSError("Озвучивание создалось некорректно.")
        temp_target = target.with_suffix(".part")
        shutil.copyfile(mp3_file, temp_target)
        os.replace(temp_target, target)

    cleanup_tts_cache()
    return TTSAsset(
        target,
        probe_duration_seconds(target),
        selected_voice,
        digest,
        selected_rate,
        selected_style,
    )


async def generate_chapter_tts(
    chapter_id: int,
    text: str,
    voice: str | None = None,
    rate: float | str | None = None,
    style: str | None = None,
) -> TTSAsset:
    prepared = prepare_literary_text(text)
    selected_voice = validate_voice(voice)
    selected_rate = validate_rate(rate)
    selected_style = validate_style(style)
    key = f"{chapter_id}:{_text_hash(prepared, selected_voice, selected_rate, selected_style)}"
    lock = _locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            return await asyncio.to_thread(
                _run_generation,
                chapter_id,
                prepared,
                selected_voice,
                selected_rate,
                selected_style,
            )
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


def sign_media_token(
    *,
    user_id: int,
    chapter_id: int,
    voice: str,
    expires_at: int,
    rate: float | str | None = None,
    style: str | None = None,
) -> str:
    payload = (
        f"{int(user_id)}:{int(chapter_id)}:{validate_voice(voice)}:"
        f"{validate_rate(rate):.2f}:{validate_style(style)}:{int(expires_at)}"
    )
    return hmac.new(_signing_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def validate_media_token(
    *,
    user_id: int,
    chapter_id: int,
    voice: str,
    expires_at: int,
    signature: str,
    rate: float | str | None = None,
    style: str | None = None,
) -> bool:
    if int(expires_at) < int(time.time()) or int(expires_at) > int(time.time()) + 86400:
        return False
    expected = sign_media_token(
        user_id=user_id,
        chapter_id=chapter_id,
        voice=voice,
        rate=rate,
        style=style,
        expires_at=expires_at,
    )
    return hmac.compare_digest(expected, str(signature or ""))


def build_media_url(
    *,
    user_id: int,
    chapter_id: int,
    voice: str,
    rate: float | str | None = None,
    style: str | None = None,
    lifetime_seconds: int = 86400,
) -> str:
    from urllib.parse import urlencode

    expires_at = int(time.time()) + max(300, min(86400, int(lifetime_seconds)))
    selected_voice = validate_voice(voice)
    selected_rate = validate_rate(rate)
    selected_style = validate_style(style)
    signature = sign_media_token(
        user_id=user_id,
        chapter_id=chapter_id,
        voice=selected_voice,
        rate=selected_rate,
        style=selected_style,
        expires_at=expires_at,
    )
    query = urlencode({
        "uid": int(user_id),
        "voice": selected_voice,
        "rate": f"{selected_rate:.2f}",
        "style": selected_style,
        "exp": expires_at,
        "sig": signature,
    })
    return f"/media/reader-tts/{int(chapter_id)}.mp3?{query}"
