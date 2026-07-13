from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import threading
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
    root = Path(settings.TTS_CACHE_DIR or 'storage/tts') / 'segments-v1110'
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



def _vosk_model_path() -> Path:
    return Path(settings.TTS_VOSK_MODEL_DIR or '/opt/voxlyra-voices/vosk') / str(
        settings.TTS_VOSK_MODEL_NAME or 'vosk-model-tts-ru-0.9-multi'
    )


_VOSK_PROFILE_LOCK = threading.Lock()
_VOSK_PROFILE_CACHE: dict[str, Any] | None = None
_VOSK_PROFILE_VERSION = 1
_VOSK_BENCHMARK_TEXT = (
    'Тихий вечер опустился на город. Герой остановился у окна, прислушался к дождю '
    'и спокойно сказал: «Завтра мы обязательно продолжим путь». Вдалеке пробили часы.'
)


def _vosk_profile_path() -> Path:
    return Path(settings.TTS_VOSK_PROFILE_PATH or 'storage/tts/vosk_voice_profile.json')


def _parse_vosk_candidates(value: str, fallback: tuple[int, ...]) -> tuple[int, ...]:
    result: list[int] = []
    for raw in str(value or '').replace(';', ',').split(','):
        raw = raw.strip()
        if not raw or not raw.lstrip('-').isdigit():
            continue
        speaker = max(0, min(4, int(raw)))
        if speaker not in result:
            result.append(speaker)
    return tuple(result) or fallback


def _default_vosk_selection() -> dict[str, int]:
    return {
        'female': max(0, min(4, int(settings.TTS_VOSK_FEMALE_SPEAKER))),
        'male': max(0, min(4, int(settings.TTS_VOSK_MALE_SPEAKER))),
    }


def _load_vosk_voice_profile(*, force: bool = False) -> dict[str, Any]:
    global _VOSK_PROFILE_CACHE
    with _VOSK_PROFILE_LOCK:
        if _VOSK_PROFILE_CACHE is not None and not force:
            return dict(_VOSK_PROFILE_CACHE)
        path = _vosk_profile_path()
        payload: dict[str, Any] = {}
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(raw, dict):
                payload = raw
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            payload = {}
        selected = dict(payload.get('selected') or {})
        defaults = _default_vosk_selection()
        for gender in ('female', 'male'):
            try:
                value = int(selected.get(gender, defaults[gender]))
            except (TypeError, ValueError):
                value = defaults[gender]
            selected[gender] = max(0, min(4, value))
        payload.update({
            'version': int(payload.get('version') or _VOSK_PROFILE_VERSION),
            'model': str(payload.get('model') or settings.TTS_VOSK_MODEL_NAME),
            'selected': selected,
            'source': str(payload.get('source') or 'defaults'),
            'benchmark': dict(payload.get('benchmark') or {'status': 'pending', 'candidates': []}),
        })
        _VOSK_PROFILE_CACHE = payload
        return dict(payload)


def _save_vosk_voice_profile(payload: dict[str, Any]) -> dict[str, Any]:
    global _VOSK_PROFILE_CACHE
    path = _vosk_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = dict(payload)
    clean['version'] = _VOSK_PROFILE_VERSION
    clean['model'] = str(settings.TTS_VOSK_MODEL_NAME)
    clean['updated_at'] = int(time.time())
    part = path.with_suffix(path.suffix + '.part')
    with _VOSK_PROFILE_LOCK:
        part.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(part, path)
        _VOSK_PROFILE_CACHE = clean
    return dict(clean)


def get_vosk_voice_profile() -> dict[str, Any]:
    return _load_vosk_voice_profile()


def set_vosk_voice_selection(gender: str, speaker_id: int) -> dict[str, Any]:
    gender = str(gender or '').strip().lower()
    if gender not in {'female', 'male'}:
        raise ValueError('Неизвестный тип голоса.')
    speaker = int(speaker_id)
    if speaker < 0 or speaker > 4:
        raise ValueError('Доступны голоса с номерами от 0 до 4.')
    profile = _load_vosk_voice_profile(force=True)
    selected = dict(profile.get('selected') or _default_vosk_selection())
    selected[gender] = speaker
    profile['selected'] = selected
    profile['source'] = 'manual'
    benchmark = dict(profile.get('benchmark') or {})
    benchmark['manual_override'] = True
    profile['benchmark'] = benchmark
    return _save_vosk_voice_profile(profile)


def vosk_sample_path(speaker_id: int) -> Path | None:
    speaker = int(speaker_id)
    if speaker < 0 or speaker > 4:
        return None
    candidate = _vosk_profile_path().parent / 'vosk_samples' / f'speaker_{speaker}.mp3'
    return candidate if candidate.is_file() and candidate.stat().st_size > 800 else None


def _vosk_speaker_for_voice(voice: str) -> int:
    selected_voice = validate_voice(voice)
    gender = str(TTS_VOICES.get(selected_voice, {}).get('gender') or 'female')
    profile = _load_vosk_voice_profile()
    selected = dict(profile.get('selected') or {})
    fallback = settings.TTS_VOSK_MALE_SPEAKER if gender == 'male' else settings.TTS_VOSK_FEMALE_SPEAKER
    try:
        raw = int(selected.get(gender, fallback))
    except (TypeError, ValueError):
        raw = int(fallback)
    return max(0, min(4, raw))


def _score_vosk_candidate(report: Any, *, expected_chars: int, elapsed_seconds: float) -> float:
    if not getattr(report, 'passed', False):
        return -1000.0 - 10.0 * len(getattr(report, 'issues', []) or [])
    duration = max(0.001, float(getattr(report, 'duration_ms', 0) or 0) / 1000.0)
    expected = max(1.0, float(expected_chars) / 12.0)
    duration_score = max(0.0, 35.0 - abs(duration - expected) / expected * 35.0)
    mean = getattr(report, 'mean_volume_db', None)
    peak = getattr(report, 'peak_volume_db', None)
    mean_score = 18.0 if mean is None else max(0.0, 18.0 - abs(float(mean) + 20.0) * 1.3)
    peak_score = 15.0 if peak is None else max(0.0, 15.0 - abs(float(peak) + 3.0) * 1.5)
    silence_ratio = float(getattr(report, 'silence_ratio', 0.0) or 0.0)
    max_silence = int(getattr(report, 'max_silence_ms', 0) or 0)
    silence_score = max(0.0, 22.0 - silence_ratio * 50.0 - max(0, max_silence - 1200) / 250.0)
    real_time_factor = elapsed_seconds / duration
    speed_score = max(0.0, 10.0 - max(0.0, real_time_factor - 1.0) * 4.0)
    return round(duration_score + mean_score + peak_score + silence_score + speed_score, 3)


class VoskSegmentProvider:
    """Локальный многоголосый русский ONNX-синтезатор.

    Модель загружается лениво и один раз на процесс. Она автоматически загружается в постоянную папку storage после запуска бота,
    не блокируя Redeploy. Владельцу не требуется отдельный сервер, URL, API-ключ
    или ручная установка.
    """

    name = 'vosk'
    _model: Any | None = None
    _synth: Any | None = None
    _load_lock = threading.Lock()
    _synth_lock = threading.Lock()
    _benchmark_lock = threading.Lock()
    _last_error = ''
    _benchmark_running = False

    async def status(self) -> TTSProviderStatus:
        dependency = importlib.util.find_spec('vosk_tts') is not None
        model_path = _vosk_model_path()
        model_ready = all((model_path / item).exists() for item in ('model.onnx', 'config.json', 'dictionary'))
        ffmpeg_ready = bool(shutil.which('ffmpeg'))
        enabled = bool(settings.TTS_ENABLED and settings.TTS_VOSK_ENABLED)
        available = bool(enabled and dependency and model_ready and ffmpeg_ready)
        warmed = bool(self.__class__._model is not None and self.__class__._synth is not None)
        profile = _load_vosk_voice_profile()
        benchmark = dict(profile.get('benchmark') or {})
        if not enabled:
            message = 'Локальный русский голос отключён в настройках'
        elif not dependency:
            message = 'Пакет vosk-tts не установлен'
        elif not model_ready:
            message = 'Русская модель Vosk ещё не загружена'
        elif not ffmpeg_ready:
            message = 'FFmpeg не установлен'
        elif self.__class__._last_error:
            message = self.__class__._last_error
        else:
            message = 'Локальный русский многоголосый движок готов'
        return TTSProviderStatus(
            self.name,
            available,
            warmed,
            message,
            {
                'model': str(settings.TTS_VOSK_MODEL_NAME),
                'model_path': str(model_path),
                'female_speaker': _vosk_speaker_for_voice('irina'),
                'male_speaker': _vosk_speaker_for_voice('dmitri'),
                'profile_source': str(profile.get('source') or 'defaults'),
                'profile_path': str(_vosk_profile_path()),
                'benchmark_status': str(benchmark.get('status') or 'pending'),
                'benchmark_error': str(benchmark.get('error') or ''),
                'benchmark_candidates': list(benchmark.get('candidates') or []),
                'benchmark_running': bool(self.__class__._benchmark_running),
                'local': True,
                'requires_url': False,
            },
        )

    async def warmup(self) -> None:
        if not settings.TTS_VOSK_ENABLED:
            return
        await asyncio.to_thread(self._ensure_loaded)
        if settings.TTS_VOSK_AUTO_SELECT:
            await asyncio.to_thread(self._benchmark_voices, False)

    async def benchmark(self, *, force: bool = False) -> dict[str, Any]:
        if not settings.TTS_VOSK_ENABLED:
            raise TTSProviderUnavailable('Локальный русский голос отключён.')
        timeout = max(30, min(600, int(settings.TTS_VOSK_BENCHMARK_TIMEOUT_SECONDS or 180)))
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._benchmark_voices, force), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TTSProviderUnavailable('Проверка голосов не успела завершиться.') from exc

    @classmethod
    def _benchmark_voices(cls, force: bool = False) -> dict[str, Any]:
        profile = _load_vosk_voice_profile(force=True)
        benchmark = dict(profile.get('benchmark') or {})
        if (
            not force
            and str(profile.get('model') or '') == str(settings.TTS_VOSK_MODEL_NAME)
            and str(benchmark.get('status') or '') == 'ready'
            and list(benchmark.get('candidates') or [])
        ):
            return profile
        with cls._benchmark_lock:
            profile = _load_vosk_voice_profile(force=True)
            benchmark = dict(profile.get('benchmark') or {})
            if (
                not force
                and str(profile.get('model') or '') == str(settings.TTS_VOSK_MODEL_NAME)
                and str(benchmark.get('status') or '') == 'ready'
                and list(benchmark.get('candidates') or [])
            ):
                return profile
            cls._benchmark_running = True
            try:
                _, synth = cls._ensure_loaded()
                female_candidates = _parse_vosk_candidates(settings.TTS_VOSK_FEMALE_CANDIDATES, (0, 1, 2))
                male_candidates = _parse_vosk_candidates(settings.TTS_VOSK_MALE_CANDIDATES, (3, 4))
                groups = {'female': female_candidates, 'male': male_candidates}
                sample_root = _vosk_profile_path().parent / 'vosk_samples'
                sample_root.mkdir(parents=True, exist_ok=True)
                records: list[dict[str, Any]] = []
                selected = _default_vosk_selection()
                with tempfile.TemporaryDirectory(prefix='vox_vosk_benchmark_') as temp_name:
                    temp = Path(temp_name)
                    for gender, candidates in groups.items():
                        best: tuple[float, int] | None = None
                        for speaker_id in candidates:
                            wav = temp / f'speaker_{speaker_id}.wav'
                            mp3 = sample_root / f'speaker_{speaker_id}.mp3'
                            started = time.perf_counter()
                            try:
                                with cls._synth_lock:
                                    synth.synth(
                                        _VOSK_BENCHMARK_TEXT, str(wav), speaker_id=speaker_id,
                                        noise_level=0.50, speech_rate=0.98, duration_noise_level=0.44, scale=0.94,
                                    )
                                elapsed = max(0.001, time.perf_counter() - started)
                                _finalize_audio(wav, mp3, sample_rate=22050)
                                report = inspect_audio_quality(mp3, expected_chars=len(_VOSK_BENCHMARK_TEXT), use_cache=False)
                                score = _score_vosk_candidate(report, expected_chars=len(_VOSK_BENCHMARK_TEXT), elapsed_seconds=elapsed)
                                record = {
                                    'gender_group': gender, 'speaker_id': int(speaker_id), 'score': score,
                                    'passed': bool(report.passed), 'duration_ms': int(report.duration_ms),
                                    'mean_volume_db': report.mean_volume_db, 'peak_volume_db': report.peak_volume_db,
                                    'silence_ratio': report.silence_ratio, 'max_silence_ms': report.max_silence_ms,
                                    'elapsed_ms': int(elapsed * 1000), 'issues': list(report.issues),
                                    'sample_ready': bool(mp3.exists() and mp3.stat().st_size > 800),
                                }
                            except Exception as exc:
                                record = {
                                    'gender_group': gender, 'speaker_id': int(speaker_id), 'score': -2000.0,
                                    'passed': False, 'duration_ms': 0, 'elapsed_ms': int((time.perf_counter() - started) * 1000),
                                    'issues': [str(exc)[:180]], 'sample_ready': False,
                                }
                            records.append(record)
                            if record['passed'] and (best is None or float(record['score']) > best[0]):
                                best = (float(record['score']), int(speaker_id))
                        if best is not None:
                            selected[gender] = best[1]
                profile.update({
                    'model': str(settings.TTS_VOSK_MODEL_NAME),
                    'selected': selected,
                    'source': 'automatic',
                    'benchmark': {
                        'status': 'ready', 'error': '', 'text_hash': hashlib.sha256(_VOSK_BENCHMARK_TEXT.encode('utf-8')).hexdigest(),
                        'candidates': records, 'sample_root': str(sample_root),
                    },
                })
                cls._last_error = ''
                return _save_vosk_voice_profile(profile)
            except Exception as exc:
                cls._last_error = f'Автопроверка голосов не завершена: {str(exc)[:120]}'
                profile['benchmark'] = {
                    **dict(profile.get('benchmark') or {}), 'status': 'failed', 'error': cls._last_error,
                }
                return _save_vosk_voice_profile(profile)
            finally:
                cls._benchmark_running = False

    @classmethod
    def _ensure_loaded(cls) -> tuple[Any, Any]:
        if cls._model is not None and cls._synth is not None:
            return cls._model, cls._synth
        with cls._load_lock:
            if cls._model is not None and cls._synth is not None:
                return cls._model, cls._synth
            try:
                os.environ.setdefault('VOSK_MODEL_PATH', str(Path(settings.TTS_VOSK_MODEL_DIR)))
                from vosk_tts import Model, Synth

                model_path = _vosk_model_path()
                if model_path.exists():
                    model = Model(model_path=str(model_path))
                else:
                    # Модель готовится отдельным фоновым процессом после запуска контейнера.
                    # Запрос читателя не должен зависать на скачивании большой модели.
                    raise TTSProviderUnavailable(
                        'Русская модель Vosk ещё загружается в фоне; временно используется резервный голос.'
                    )
                synth = Synth(model)
                cls._model = model
                cls._synth = synth
                cls._last_error = ''
                return model, synth
            except SystemExit as exc:
                cls._last_error = 'Не удалось автоматически получить русскую модель Vosk'
                raise TTSProviderUnavailable(cls._last_error) from exc
            except Exception as exc:
                cls._last_error = f'Vosk временно недоступен: {str(exc)[:120]}'
                raise TTSProviderUnavailable(cls._last_error) from exc

    async def synthesize(self, request: TTSSynthesisRequest) -> TTSSegmentAudio:
        if not settings.TTS_VOSK_ENABLED:
            raise TTSProviderUnavailable('Локальный русский голос отключён.')
        return await asyncio.to_thread(self._run, request)

    def _run(self, request: TTSSynthesisRequest) -> TTSSegmentAudio:
        target, digest = _cache_target(self.name, request)
        cached = _cached_audio(self.name, request, target, digest)
        if cached is not None:
            return cached
        _, synth = self._ensure_loaded()
        voice = validate_voice(request.voice)
        style = validate_style(request.style)
        profile = TTS_STYLES[style]
        quality_attempt = max(0, int(request.metadata.get('quality_attempt') or 0))

        # Небольшая вариативность сохраняет естественность, но параметры намеренно
        # спокойнее стандартных, чтобы не появлялись всхлипы, дрожание и растяжения.
        noise_level = {'calm': 0.48, 'natural': 0.55, 'expressive': 0.62}.get(style, 0.55)
        duration_noise = {'calm': 0.42, 'natural': 0.50, 'expressive': 0.58}.get(style, 0.50)
        noise_level = max(0.38, noise_level - 0.05 * quality_attempt)
        duration_noise = max(0.34, duration_noise - 0.06 * quality_attempt)
        speech_rate = max(0.72, min(1.12, 1.0 / float(profile['length_factor'])))
        if quality_attempt:
            speech_rate = max(0.72, speech_rate - 0.025 * quality_attempt)
        speaker_id = _vosk_speaker_for_voice(voice)

        temp_root = Path('storage/temp')
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='vox_vosk_segment_', dir=str(temp_root)) as temp_name:
            wav = Path(temp_name) / 'speech.wav'
            try:
                with self.__class__._synth_lock:
                    synth.synth(
                        request.segment.text,
                        str(wav),
                        speaker_id=speaker_id,
                        noise_level=noise_level,
                        speech_rate=speech_rate,
                        duration_noise_level=duration_noise,
                        scale=0.94,
                    )
                _finalize_audio(wav, target, sample_rate=22050)
            except (OSError, ValueError, TTSProviderError) as exc:
                raise TTSProviderError('Не удалось озвучить фрагмент локальным русским голосом.') from exc
        report = inspect_audio_quality(target, expected_chars=request.segment.chars, use_cache=False)
        return TTSSegmentAudio(target, self.name, int(report.duration_ms or _duration_ms(target)), digest, report.as_dict())


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
            'vosk': VoskSegmentProvider(),
            'moss': RemoteTTSProvider('moss', settings.TTS_MOSS_URL, settings.TTS_REMOTE_TOKEN),
            'piper': PiperSegmentProvider(),
        }

    async def warmup(self) -> None:
        await asyncio.gather(*(provider.warmup() for provider in self.providers.values()), return_exceptions=True)

    async def statuses(self) -> list[TTSProviderStatus]:
        return list(await asyncio.gather(*(provider.status() for provider in self.providers.values())))

    async def benchmark_vosk(self, *, force: bool = False) -> dict[str, Any]:
        provider = self.providers.get('vosk')
        if not isinstance(provider, VoskSegmentProvider):
            raise TTSProviderUnavailable('Локальный русский движок не найден.')
        return await provider.benchmark(force=force)

    def vosk_profile(self) -> dict[str, Any]:
        return get_vosk_voice_profile()

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
