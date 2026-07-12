from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.services.reader_tts import TTS_STYLES, TTS_VOICES, ReaderTTSError, _voice_model_files, _voice_sample_rate, validate_style, validate_voice
from app.services.tts_text import TTSTextSegment
from app.services.tts_quality import cleanup_segment_cache, inspect_audio_quality, remove_audio_with_sidecar


class TTSProviderError(RuntimeError):
    pass


class TTSProviderUnavailable(TTSProviderError):
    pass


@dataclass(slots=True, frozen=True)
class TTSSynthesisRequest:
    session_id: str
    chapter_id: int
    segment: TTSTextSegment
    voice: str
    style: str
    sample_rate: int = 24000
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TTSSegmentAudio:
    path: Path
    provider: str
    duration_ms: int
    cache_key: str
    quality: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TTSProviderStatus:
    name: str
    available: bool
    warmed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _segment_cache_root() -> Path:
    root = Path(settings.TTS_CACHE_DIR or 'storage/tts') / 'segments-v1105'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _duration_ms(path: Path) -> int:
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        return 0
    try:
        value = subprocess.check_output(
            [ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(path)],
            text=True,
            timeout=30,
        ).strip()
        return max(0, int(float(value) * 1000))
    except Exception:
        return 0


def _cache_target(provider: str, request: TTSSynthesisRequest) -> tuple[Path, str]:
    payload = '\0'.join((provider, validate_voice(request.voice), validate_style(request.style), request.segment.digest, request.segment.text))
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    folder = _segment_cache_root() / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{digest}.mp3', digest


def _finalize_audio(source: Path, target: Path, *, sample_rate: int) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise TTSProviderUnavailable("FFmpeg не установлен.")
    target.parent.mkdir(parents=True, exist_ok=True)
    output_rate = max(16000, min(48000, int(sample_rate or 24000)))
    lowpass = max(7000, min(11500, int(output_rate / 2 - 500)))
    part = target.with_name(f"{target.stem}.part{target.suffix}")
    try:
        subprocess.run([
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
            "-map_metadata", "-1", "-vn",
            "-af", f"aresample=async=1:first_pts=0,highpass=f=45,lowpass=f={lowpass},alimiter=limit=0.97",
            "-ac", "1", "-ar", str(output_rate),
            "-codec:a", "libmp3lame", "-b:a", "96k", "-f", "mp3", str(part),
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if not part.exists() or part.stat().st_size <= 800:
            raise TTSProviderError("Фрагмент озвучивания повреждён.")
        os.replace(part, target)
    except (subprocess.SubprocessError, OSError, TTSProviderError) as exc:
        try:
            part.unlink()
        except OSError:
            pass
        if isinstance(exc, TTSProviderError):
            raise
        raise TTSProviderError("Не удалось подготовить звуковой фрагмент.") from exc


def _cached_audio(provider: str, request: TTSSynthesisRequest, target: Path, digest: str) -> TTSSegmentAudio | None:
    if not target.exists() or target.stat().st_size <= 800:
        return None
    report = inspect_audio_quality(target, expected_chars=request.segment.chars)
    if not report.passed:
        remove_audio_with_sidecar(target)
        return None
    os.utime(target, None)
    return TTSSegmentAudio(target, provider, int(report.duration_ms or _duration_ms(target)), digest, report.as_dict())


class PiperSegmentProvider:
    name = 'piper'

    async def status(self) -> TTSProviderStatus:
        piper = shutil.which('piper')
        ffmpeg = shutil.which('ffmpeg')
        models = all(_voice_model_files(code)[0].exists() and _voice_model_files(code)[1].exists() for code in TTS_VOICES)
        available = bool(settings.TTS_ENABLED and piper and ffmpeg and models)
        return TTSProviderStatus(self.name, available, available, 'Локальный резервный голос готов' if available else 'Piper или модели не установлены')

    async def warmup(self) -> None:
        return None

    async def synthesize(self, request: TTSSynthesisRequest) -> TTSSegmentAudio:
        status = await self.status()
        if not status.available:
            raise TTSProviderUnavailable(status.message)
        return await asyncio.to_thread(self._run, request)

    def _run(self, request: TTSSynthesisRequest) -> TTSSegmentAudio:
        target, digest = _cache_target(self.name, request)
        cached = _cached_audio(self.name, request, target, digest)
        if cached is not None:
            return cached
        voice = validate_voice(request.voice)
        style = validate_style(request.style)
        profile = TTS_STYLES[style]
        model, config = _voice_model_files(voice)
        piper = shutil.which('piper')
        ffmpeg = shutil.which('ffmpeg')
        if not piper or not ffmpeg:
            raise TTSProviderUnavailable('Piper или FFmpeg не установлен.')
        temp_root = Path('storage/temp')
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='vox_tts_segment_', dir=str(temp_root)) as temp_name:
            temp = Path(temp_name)
            wav = temp / 'speech.wav'
            # Для фрагментов уменьшаем случайность: это убирает дрожание и «плачущий» оттенок.
            quality_attempt = max(0, int(request.metadata.get('quality_attempt') or 0))
            noise_scale = min(float(profile['noise_scale']), 0.50 if style != 'expressive' else 0.54)
            noise_w = min(float(profile['noise_w_scale']), 0.60 if style != 'expressive' else 0.65)
            if quality_attempt:
                noise_scale = max(0.38, noise_scale - 0.06 * quality_attempt)
                noise_w = max(0.48, noise_w - 0.07 * quality_attempt)
            length_scale = float(profile['length_factor']) * (1.0 + 0.025 * quality_attempt)
            cmd = [
                piper, '--model', str(model), '--config', str(config), '--output-file', str(wav),
                '--length-scale', f'{length_scale:.3f}', '--noise-scale', f'{noise_scale:.3f}',
                '--noise-w-scale', f'{noise_w:.3f}', '--sentence-silence', '0.12',
            ]
            try:
                subprocess.run(cmd, input=request.segment.text.encode('utf-8'), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
                _finalize_audio(wav, target, sample_rate=_voice_sample_rate(voice))
            except (subprocess.SubprocessError, OSError, TTSProviderError) as exc:
                raise TTSProviderError('Не удалось озвучить фрагмент.') from exc
        report = inspect_audio_quality(target, expected_chars=request.segment.chars, use_cache=False)
        return TTSSegmentAudio(target, self.name, int(report.duration_ms or _duration_ms(target)), digest, report.as_dict())


class RemoteTTSProvider:
    def __init__(self, name: str, base_url: str, token: str = '') -> None:
        self.name = name
        self.base_url = base_url.rstrip('/')
        self.token = token
        self._warmed = False
        self._unavailable_until = 0.0
        self._last_error = ''

    async def status(self) -> TTSProviderStatus:
        if not self.base_url:
            return TTSProviderStatus(self.name, False, False, 'Адрес сервера не настроен')
        if time.monotonic() < self._unavailable_until:
            return TTSProviderStatus(self.name, False, self._warmed, self._last_error or 'Сервер временно на паузе после ошибки')
        try:
            headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
            async with httpx.AsyncClient(timeout=4) as client:
                response = await client.get(f'{self.base_url}/health', headers=headers)
            available = response.status_code < 400
            if available:
                self._unavailable_until = 0.0
                self._last_error = ''
            return TTSProviderStatus(self.name, available, self._warmed, 'Готов' if available else f'HTTP {response.status_code}')
        except Exception as exc:
            self._last_error = str(exc)[:160]
            return TTSProviderStatus(self.name, False, self._warmed, self._last_error)

    async def warmup(self) -> None:
        status = await self.status()
        self._warmed = status.available

    async def synthesize(self, request: TTSSynthesisRequest) -> TTSSegmentAudio:
        target, digest = _cache_target(self.name, request)
        cached = _cached_audio(self.name, request, target, digest)
        if cached is not None:
            return cached
        if not self.base_url:
            raise TTSProviderUnavailable(f'{self.name} не настроен.')
        if time.monotonic() < self._unavailable_until:
            raise TTSProviderUnavailable(self._last_error or f'{self.name} временно отключён после ошибки.')
        headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
        payload = {
            'text': request.segment.text,
            'voice': validate_voice(request.voice),
            'style': validate_style(request.style),
            'format': 'mp3',
            'sample_rate': request.sample_rate,
            'stream': False,
            'segment_kind': request.segment.kind,
            'quality_attempt': int(request.metadata.get('quality_attempt') or 0),
            'stable_narration': True,
        }
        try:
            read_timeout = float(
                settings.TTS_REMOTE_FIRST_TIMEOUT_SECONDS
                if bool(request.metadata.get('is_first'))
                else settings.TTS_REMOTE_TIMEOUT_SECONDS
            )
            timeout = httpx.Timeout(max(5.0, read_timeout), connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f'{self.base_url}/synthesize', json=payload, headers=headers)
            response.raise_for_status()
            audio = response.content
            self._unavailable_until = 0.0
            self._last_error = ''
        except Exception as exc:
            self._last_error = f'{self.name} временно недоступен.'
            self._unavailable_until = time.monotonic() + max(15, int(settings.TTS_REMOTE_COOLDOWN_SECONDS or 60))
            raise TTSProviderUnavailable(self._last_error) from exc
        if len(audio) <= 800:
            raise TTSProviderError(f'{self.name} вернул повреждённый звук.')
        temp_root = Path('storage/temp')
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f'vox_{self.name}_segment_', dir=str(temp_root)) as temp_name:
            raw = Path(temp_name) / 'remote-audio.bin'
            raw.write_bytes(audio)
            _finalize_audio(raw, target, sample_rate=request.sample_rate)
        report = inspect_audio_quality(target, expected_chars=request.segment.chars, use_cache=False)
        return TTSSegmentAudio(target, self.name, int(report.duration_ms or _duration_ms(target)), digest, report.as_dict())


class TTSProviderRegistry:
    def __init__(self) -> None:
        self._last_cleanup = 0.0
        self.providers = {
            'qwen': RemoteTTSProvider('qwen', settings.TTS_QWEN_URL, settings.TTS_REMOTE_TOKEN),
            'moss': RemoteTTSProvider('moss', settings.TTS_MOSS_URL, settings.TTS_REMOTE_TOKEN),
            'piper': PiperSegmentProvider(),
        }

    async def warmup(self) -> None:
        await asyncio.gather(*(provider.warmup() for provider in self.providers.values()), return_exceptions=True)

    async def statuses(self) -> list[TTSProviderStatus]:
        return list(await asyncio.gather(*(provider.status() for provider in self.providers.values())))

    async def synthesize(self, request: TTSSynthesisRequest, order: tuple[str, ...]) -> TTSSegmentAudio:
        errors: list[str] = []
        retries = max(0, min(2, int(settings.TTS_QUALITY_RETRIES or 1)))
        for name in order:
            provider = self.providers.get(name)
            if not provider:
                continue
            for attempt in range(retries + 1):
                attempt_request = replace(
                    request,
                    metadata={**dict(request.metadata), 'quality_attempt': attempt, 'provider': name},
                )
                try:
                    audio = await provider.synthesize(attempt_request)
                    report = inspect_audio_quality(audio.path, expected_chars=request.segment.chars)
                    if not report.passed:
                        remove_audio_with_sidecar(audio.path)
                        raise TTSProviderError('Автопроверка обнаружила искажённый или оборванный фрагмент.')
                    audio.duration_ms = int(report.duration_ms or audio.duration_ms)
                    audio.quality = report.as_dict()
                    now = time.monotonic()
                    if now - self._last_cleanup > 300:
                        self._last_cleanup = now
                        await asyncio.to_thread(
                            cleanup_segment_cache, _segment_cache_root(),
                            max_age_days=settings.TTS_CACHE_DAYS, max_megabytes=settings.TTS_MAX_CACHE_MB,
                        )
                    return audio
                except (TTSProviderError, ReaderTTSError) as exc:
                    errors.append(f'{name}[{attempt + 1}]: {exc}')
        raise TTSProviderUnavailable('; '.join(errors[-8:]) or 'Нет доступного голосового движка.')
