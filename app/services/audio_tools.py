from __future__ import annotations

import mimetypes
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".oga", ".wav"}
MAX_AUDIO_FILE_SIZE = 200 * 1024 * 1024
MAX_ZIP_AUDIO_FILES = 80


class AudioImportError(Exception):
    pass


@dataclass(slots=True)
class AudioInfo:
    title: str
    path: Path
    source_filename: str
    duration_seconds: int
    mime_type: str
    file_size: int


def is_supported_audio(filename: str) -> bool:
    return Path(filename).suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS


def safe_audio_title(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:120] or "Аудиоглава"


def probe_duration_seconds(path: str | Path) -> int:
    path = Path(path)
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(path))
        if audio is not None and getattr(audio, "info", None) is not None and getattr(audio.info, "length", None):
            return max(0, int(round(float(audio.info.length))))
    except Exception:
        pass

    if path.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(path), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 1
                return max(0, int(round(frames / float(rate))))
        except Exception:
            pass
    return 0


def inspect_audio_file(path: str | Path, source_filename: str | None = None, title: str | None = None) -> AudioInfo:
    path = Path(path)
    source = source_filename or path.name
    if not path.exists():
        raise AudioImportError("Аудиофайл не найден.")
    if not is_supported_audio(source):
        raise AudioImportError("Поддерживаются MP3, M4A, OGG и WAV.")
    size = path.stat().st_size
    if size <= 0:
        raise AudioImportError("Аудиофайл пустой.")
    if size > MAX_AUDIO_FILE_SIZE:
        raise AudioImportError("Аудиофайл слишком большой. Сейчас лимит 200 МБ на одну аудиоглаву.")
    mime_type = mimetypes.guess_type(source)[0] or "application/octet-stream"
    return AudioInfo(
        title=(title or safe_audio_title(source))[:160],
        path=path,
        source_filename=source,
        duration_seconds=probe_duration_seconds(path),
        mime_type=mime_type,
        file_size=size,
    )


def _safe_zip_members(zf: zipfile.ZipFile) -> Iterable[zipfile.ZipInfo]:
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.startswith("__MACOSX/"):
            continue
        if name.startswith("/") or ".." in Path(name).parts:
            continue
        if not is_supported_audio(name):
            continue
        yield info


def extract_audio_zip(zip_path: str | Path, output_dir: str | Path) -> list[AudioInfo]:
    zip_path = Path(zip_path)
    output_dir = Path(output_dir)
    if zip_path.suffix.lower() != ".zip":
        raise AudioImportError("Нужен ZIP-архив с аудиофайлами.")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(zip_path)
    except Exception as exc:
        raise AudioImportError("ZIP не удалось открыть. Архив повреждён или имеет неверный формат.") from exc

    result: list[AudioInfo] = []
    with zf:
        members = list(_safe_zip_members(zf))
        if not members:
            raise AudioImportError("В ZIP не найдено аудиофайлов MP3/M4A/OGG/WAV.")
        if len(members) > MAX_ZIP_AUDIO_FILES:
            raise AudioImportError(f"В ZIP слишком много аудиофайлов. Сейчас лимит {MAX_ZIP_AUDIO_FILES}.")
        for index, info in enumerate(members, 1):
            if info.file_size > MAX_AUDIO_FILE_SIZE:
                raise AudioImportError(f"Файл {info.filename} слишком большой.")
            ext = Path(info.filename).suffix.lower()
            safe_name = f"audio_{index:04d}{ext}"
            target = output_dir / safe_name
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            result.append(inspect_audio_file(target, source_filename=Path(info.filename).name))
    return result


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds == 0:
        return "не определена"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def build_audio_import_report(items: list[AudioInfo]) -> dict:
    total_seconds = sum(item.duration_seconds for item in items)
    total_size = sum(item.file_size for item in items)
    preview = [
        {
            "title": item.title,
            "duration": format_duration(item.duration_seconds),
            "size_mb": round(item.file_size / 1024 / 1024, 2),
        }
        for item in items[:10]
    ]
    problems: list[str] = []
    if any(item.duration_seconds == 0 for item in items):
        problems.append("У части файлов не удалось определить длительность. Их можно сохранить, но лучше проверить вручную.")
    return {
        "count": len(items),
        "total_seconds": total_seconds,
        "total_duration": format_duration(total_seconds),
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "preview": preview,
        "problems": problems,
    }
