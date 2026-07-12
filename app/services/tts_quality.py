from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AudioQualityReport:
    passed: bool
    inspected: bool
    duration_ms: int = 0
    sample_rate: int = 0
    channels: int = 0
    codec: str = ""
    format_name: str = ""
    size_bytes: int = 0
    mean_volume_db: float | None = None
    peak_volume_db: float | None = None
    max_silence_ms: int = 0
    silence_ratio: float = 0.0
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["issues"] = list(self.issues)
        return payload


_FLOAT_RE = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _quality_sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".quality.json")


def remove_audio_with_sidecar(path: Path) -> None:
    for item in (path, _quality_sidecar(path)):
        try:
            item.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _read_cached_report(path: Path, expected_chars: int) -> AudioQualityReport | None:
    sidecar = _quality_sidecar(path)
    try:
        audio_stat = path.stat()
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        if int(payload.get("audio_size") or -1) != int(audio_stat.st_size):
            return None
        if int(payload.get("expected_chars") or -1) != int(expected_chars):
            return None
        report_data = payload.get("report") or {}
        return AudioQualityReport(
            passed=bool(report_data.get("passed")),
            inspected=bool(report_data.get("inspected")),
            duration_ms=int(report_data.get("duration_ms") or 0),
            sample_rate=int(report_data.get("sample_rate") or 0),
            channels=int(report_data.get("channels") or 0),
            codec=str(report_data.get("codec") or ""),
            format_name=str(report_data.get("format_name") or ""),
            size_bytes=int(report_data.get("size_bytes") or 0),
            mean_volume_db=(None if report_data.get("mean_volume_db") is None else float(report_data.get("mean_volume_db"))),
            peak_volume_db=(None if report_data.get("peak_volume_db") is None else float(report_data.get("peak_volume_db"))),
            max_silence_ms=int(report_data.get("max_silence_ms") or 0),
            silence_ratio=float(report_data.get("silence_ratio") or 0.0),
            issues=[str(item) for item in report_data.get("issues") or []],
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_cached_report(path: Path, expected_chars: int, report: AudioQualityReport) -> None:
    sidecar = _quality_sidecar(path)
    try:
        stat = path.stat()
        payload = {
            "version": 2,
            "created_at": int(time.time()),
            "audio_size": int(stat.st_size),
            "audio_mtime_ns": int(stat.st_mtime_ns),
            "expected_chars": int(expected_chars),
            "report": report.as_dict(),
        }
        part = sidecar.with_suffix(sidecar.suffix + ".part")
        part.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(part, sidecar)
    except OSError:
        pass


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    try:
        raw = subprocess.check_output(
            [
                ffprobe,
                "-v", "error",
                "-show_entries", "format=duration,format_name,size:stream=codec_type,codec_name,sample_rate,channels,duration",
                "-of", "json",
                str(path),
            ],
            text=True,
            timeout=25,
            stderr=subprocess.STDOUT,
        )
        return json.loads(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}


def _analyse_levels_and_silence(path: Path, duration_seconds: float) -> tuple[float | None, float | None, int, float]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or duration_seconds <= 0:
        return None, None, 0, 0.0
    try:
        completed = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-nostats", "-i", str(path),
                "-af", "silencedetect=noise=-46dB:d=0.55,volumedetect",
                "-f", "null", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45,
            check=False,
        )
        output = completed.stderr or ""
    except (OSError, subprocess.SubprocessError):
        return None, None, 0, 0.0

    mean_match = re.search(rf"mean_volume:\s*({_FLOAT_RE})\s*dB", output)
    peak_match = re.search(rf"max_volume:\s*({_FLOAT_RE})\s*dB", output)
    mean_db = _safe_float(mean_match.group(1)) if mean_match else None
    peak_db = _safe_float(peak_match.group(1)) if peak_match else None

    intervals: list[tuple[float, float]] = []
    pending_start: float | None = None
    for line in output.splitlines():
        start_match = re.search(rf"silence_start:\s*({_FLOAT_RE})", line)
        if start_match:
            pending_start = max(0.0, _safe_float(start_match.group(1)))
        end_match = re.search(rf"silence_end:\s*({_FLOAT_RE})", line)
        if end_match:
            end = min(duration_seconds, max(0.0, _safe_float(end_match.group(1))))
            start = pending_start if pending_start is not None else max(0.0, end)
            if end > start:
                intervals.append((start, end))
            pending_start = None
    if pending_start is not None and duration_seconds > pending_start:
        intervals.append((pending_start, duration_seconds))

    total_silence = sum(max(0.0, end - start) for start, end in intervals)
    max_silence = max((end - start for start, end in intervals), default=0.0)
    ratio = min(1.0, total_silence / duration_seconds) if duration_seconds > 0 else 0.0
    return mean_db, peak_db, int(max_silence * 1000), ratio


def inspect_audio_quality(path: Path, *, expected_chars: int, use_cache: bool = True) -> AudioQualityReport:
    expected_chars = max(1, int(expected_chars or 1))
    try:
        size = int(path.stat().st_size)
    except OSError:
        return AudioQualityReport(False, False, issues=["audio_missing"])

    if use_cache:
        cached = _read_cached_report(path, expected_chars)
        if cached is not None:
            return cached

    issues: list[str] = []
    if size < 800:
        issues.append("audio_too_small")

    probe = _probe(path)
    streams = [item for item in probe.get("streams") or [] if str(item.get("codec_type") or "") == "audio"]
    stream = streams[0] if streams else {}
    fmt = probe.get("format") or {}
    duration_seconds = _safe_float(stream.get("duration"), _safe_float(fmt.get("duration"), 0.0))
    duration_ms = max(0, int(duration_seconds * 1000))
    sample_rate = int(_safe_float(stream.get("sample_rate"), 0.0))
    channels = int(_safe_float(stream.get("channels"), 0.0))
    codec = str(stream.get("codec_name") or "")
    format_name = str(fmt.get("format_name") or "")
    inspected = bool(probe and streams)

    if not inspected:
        issues.append("ffprobe_unavailable_or_invalid")
    else:
        if duration_seconds < 0.35:
            issues.append("duration_too_short")
        minimum_expected = max(0.35, expected_chars / 34.0)
        maximum_expected = max(6.0, expected_chars / 2.8)
        if duration_seconds + 0.05 < minimum_expected:
            issues.append("speech_unrealistically_fast_or_truncated")
        if duration_seconds > maximum_expected:
            issues.append("speech_unrealistically_slow_or_stuck")
        if sample_rate and sample_rate < 16000:
            issues.append("sample_rate_too_low")
        if channels not in (1, 2):
            issues.append("invalid_channel_count")
        if not codec:
            issues.append("missing_audio_codec")

    mean_db, peak_db, max_silence_ms, silence_ratio = _analyse_levels_and_silence(path, duration_seconds)
    if mean_db is not None and mean_db < -43.0:
        issues.append("audio_too_quiet")
    if peak_db is not None and peak_db > 0.2:
        issues.append("audio_clipping")
    if duration_seconds >= 2.0:
        if silence_ratio > 0.58:
            issues.append("too_much_silence")
        if max_silence_ms > max(4200, int(duration_seconds * 0.55 * 1000)):
            issues.append("long_silence_or_stall")

    # Отсутствие ffprobe не блокирует резервный голос: размер файла всё равно проверен.
    blocking = [item for item in issues if item != "ffprobe_unavailable_or_invalid"]
    report = AudioQualityReport(
        passed=not blocking,
        inspected=inspected,
        duration_ms=duration_ms,
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
        format_name=format_name,
        size_bytes=size,
        mean_volume_db=mean_db,
        peak_volume_db=peak_db,
        max_silence_ms=max_silence_ms,
        silence_ratio=round(silence_ratio, 4),
        issues=issues,
    )
    _write_cached_report(path, expected_chars, report)
    return report


def cleanup_segment_cache(root: Path, *, max_age_days: int, max_megabytes: int) -> dict[str, int]:
    root.mkdir(parents=True, exist_ok=True)
    now = time.time()
    max_age = max(1, int(max_age_days or 3)) * 86400
    max_bytes = max(128, int(max_megabytes or 512)) * 1024 * 1024
    removed_files = 0
    removed_bytes = 0

    # Незавершённые и устаревшие файлы удаляются вместе с отчётами качества.
    for path in list(root.rglob("*.part")):
        try:
            stat = path.stat()
            if now - stat.st_mtime > 1800:
                removed_bytes += stat.st_size
                path.unlink()
                removed_files += 1
        except OSError:
            pass

    audio_files: list[Path] = []
    total = 0
    for path in root.rglob("*.mp3"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime > max_age:
            removed_bytes += stat.st_size
            remove_audio_with_sidecar(path)
            removed_files += 1
            continue
        audio_files.append(path)
        total += stat.st_size

    if total > max_bytes:
        audio_files.sort(key=lambda item: item.stat().st_mtime)
        for path in audio_files:
            if total <= max_bytes:
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            remove_audio_with_sidecar(path)
            total -= size
            removed_bytes += size
            removed_files += 1

    # Сиротские отчёты качества не должны копиться.
    for sidecar in root.rglob("*.quality.json"):
        audio_name = sidecar.name.removesuffix(".quality.json")
        audio = sidecar.with_name(audio_name)
        if audio.exists():
            continue
        try:
            removed_bytes += sidecar.stat().st_size
            sidecar.unlink()
            removed_files += 1
        except OSError:
            pass

    return {"removed_files": removed_files, "removed_bytes": removed_bytes}


def migrate_old_tts_cache_once(root: Path) -> dict[str, int | bool]:
    """Один раз очищает старые цельные и ранние сегментные записи.

    База, книги и загруженные авторами аудиоглавы не затрагиваются: очищается только
    автоматически созданный кэш `storage/tts`.
    """
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".v1105_quality_cache_v2"
    if marker.exists():
        return {"migrated": False, "removed_files": 0, "removed_bytes": 0}

    removed_files = 0
    removed_bytes = 0
    targets = [path for path in root.glob("chapter_*") if path.is_dir()]
    segment_root = root / "segments-v1105"
    if segment_root.exists():
        targets.append(segment_root)
    for target in targets:
        for path in target.rglob("*"):
            if path.is_file():
                try:
                    removed_bytes += path.stat().st_size
                    removed_files += 1
                except OSError:
                    pass
        try:
            shutil.rmtree(target)
        except OSError:
            pass

    try:
        marker.write_text(
            json.dumps({"migrated_at": int(time.time()), "removed_files": removed_files, "removed_bytes": removed_bytes}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return {"migrated": True, "removed_files": removed_files, "removed_bytes": removed_bytes}
