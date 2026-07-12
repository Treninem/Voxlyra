from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from app.config import settings
from app.services.reader_tts import validate_style, validate_voice
from app.services.tts_providers import TTSSegmentAudio, TTSSynthesisRequest
from app.services.tts_queue import TTSGenerationQueue, TTSJobPriority, TTSQueueJob, configured_provider_order, segment_job_key
from app.services.tts_text import PreparedTTSChapter, prepare_tts_chapter
from app.services.tts_quality import cleanup_segment_cache, migrate_old_tts_cache_once


class TTSSessionError(RuntimeError):
    pass


class TTSSessionNotFound(TTSSessionError):
    pass


class TTSSegmentNotReady(TTSSessionError):
    pass


@dataclass(slots=True)
class ReaderTTSSession:
    id: str
    user_id: int
    chapter_id: int
    voice: str
    style: str
    high_quality: bool
    prepared: PreparedTTSChapter
    providers: tuple[str, ...]
    created_at: int
    expires_at: int
    futures: dict[int, asyncio.Future[TTSSegmentAudio]] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    attempts: dict[int, int] = field(default_factory=dict)
    last_access_at: int = 0

    @property
    def segment_count(self) -> int:
        return len(self.prepared.segments)

    @property
    def chapter_digest(self) -> str:
        return hashlib.sha256(self.prepared.spoken_text.encode('utf-8')).hexdigest()


class TTSSessionManager:
    def __init__(self, queue: TTSGenerationQueue) -> None:
        self.queue = queue
        self._sessions: dict[str, ReaderTTSSession] = {}
        self._profile_index: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def _ttl(self) -> int:
        return max(900, min(86400, int(settings.TTS_SESSION_TTL_SECONDS or 7200)))

    def _profile_key(self, *, user_id: int, chapter_id: int, voice: str, style: str, high_quality: bool, chapter_digest: str) -> str:
        return ':'.join((str(user_id), str(chapter_id), validate_voice(voice), validate_style(style), 'hq' if high_quality else 'std', chapter_digest))

    async def start(self) -> None:
        cache_root = Path(settings.TTS_CACHE_DIR or 'storage/tts')
        await asyncio.to_thread(migrate_old_tts_cache_once, cache_root)
        await asyncio.to_thread(
            cleanup_segment_cache, cache_root / 'segments-v1105',
            max_age_days=settings.TTS_CACHE_DAYS, max_megabytes=settings.TTS_MAX_CACHE_MB,
        )
        await self.queue.start()
        try:
            await asyncio.wait_for(self.queue.registry.warmup(), timeout=20)
        except Exception:
            pass

    async def close(self) -> None:
        async with self._lock:
            self._sessions.clear()
            self._profile_index.clear()
        await self.queue.close()

    async def cleanup(self) -> int:
        now = int(time.time())
        async with self._lock:
            stale = [sid for sid, session in self._sessions.items() if session.expires_at <= now]
            for sid in stale:
                self._sessions.pop(sid, None)
                for key, value in list(self._profile_index.items()):
                    if value == sid:
                        self._profile_index.pop(key, None)
            return len(stale)

    async def create_session(self, *, user_id: int, chapter_id: int, text: str, voice: str, style: str, high_quality: bool = False, glossary: dict[str, str] | None = None, reuse: bool = True) -> ReaderTTSSession:
        await self.cleanup()
        prepared = prepare_tts_chapter(
            text, glossary=glossary,
            target_chars=settings.TTS_SEGMENT_TARGET_CHARS,
            max_chars=settings.TTS_SEGMENT_MAX_CHARS,
            first_max_chars=settings.TTS_FIRST_SEGMENT_MAX_CHARS,
        )
        selected_voice = validate_voice(voice)
        selected_style = validate_style(style)
        digest = hashlib.sha256(prepared.spoken_text.encode('utf-8')).hexdigest()
        profile_key = self._profile_key(user_id=user_id, chapter_id=chapter_id, voice=selected_voice, style=selected_style, high_quality=high_quality, chapter_digest=digest)
        now = int(time.time())
        async with self._lock:
            session = None
            if reuse:
                existing = self._sessions.get(self._profile_index.get(profile_key, ''))
                if existing and existing.expires_at > now:
                    session = existing
                    session.expires_at = now + self._ttl()
                    session.last_access_at = now
            if session is None:
                session = ReaderTTSSession(
                    id=uuid.uuid4().hex, user_id=int(user_id), chapter_id=int(chapter_id), voice=selected_voice,
                    style=selected_style, high_quality=bool(high_quality), prepared=prepared,
                    providers=configured_provider_order(high_quality=bool(high_quality)), created_at=now,
                    expires_at=now + self._ttl(), last_access_at=now,
                )
                self._sessions[session.id] = session
                self._profile_index[profile_key] = session.id
        await self.ensure_window(session.id, start_index=0, count=settings.TTS_SESSION_INITIAL_SEGMENTS)
        return session

    async def get(self, session_id: str, *, user_id: int | None = None) -> ReaderTTSSession:
        await self.cleanup()
        async with self._lock:
            session = self._sessions.get(str(session_id or ''))
            if not session:
                raise TTSSessionNotFound('Сессия озвучивания завершена. Запустите главу снова.')
            if user_id is not None and int(session.user_id) != int(user_id):
                raise TTSSessionNotFound('Сессия озвучивания не найдена.')
            now = int(time.time())
            session.last_access_at = now
            session.expires_at = now + self._ttl()
            return session

    def _priority(self, index: int, start_index: int) -> TTSJobPriority:
        if index == 0:
            return TTSJobPriority.FIRST_CURRENT
        if index <= max(2, start_index + 2):
            return TTSJobPriority.BUFFER_CURRENT
        return TTSJobPriority.REST_CURRENT

    def _capture(self, session: ReaderTTSSession, index: int, future: asyncio.Future[TTSSegmentAudio]) -> None:
        if future.cancelled():
            session.errors[index] = 'Генерация отменена.'
            return
        try:
            future.result()
        except Exception as exc:
            session.errors[index] = str(exc)[:300]
        else:
            session.errors.pop(index, None)

    async def _schedule(self, session: ReaderTTSSession, index: int, priority: TTSJobPriority) -> asyncio.Future[TTSSegmentAudio]:
        if not 0 <= index < session.segment_count:
            raise IndexError('Номер аудиофрагмента вне главы.')
        existing = session.futures.get(index)
        if existing and not existing.cancelled() and (not existing.done() or existing.exception() is None):
            return existing
        max_retries = max(0, min(5, int(settings.TTS_SEGMENT_SESSION_RETRIES or 2)))
        attempts = int(session.attempts.get(index) or 0)
        if existing and existing.done() and not existing.cancelled() and existing.exception() is not None and attempts > max_retries:
            return existing
        session.attempts[index] = attempts + 1
        segment = session.prepared.segments[index]
        request = TTSSynthesisRequest(
            session_id=session.id, chapter_id=session.chapter_id, segment=segment, voice=session.voice,
            style=session.style, sample_rate=24000,
            metadata={'high_quality': session.high_quality, 'segment_count': session.segment_count, 'is_first': index == 0},
        )
        future = await self.queue.submit(TTSQueueJob(
            key=segment_job_key(request), request=request, providers=session.providers,
            priority=priority, metadata={'session_id': session.id},
        ))
        session.futures[index] = future
        future.add_done_callback(lambda value, s=session, i=index: self._capture(s, i, value))
        return future

    async def ensure_window(self, session_id: str, *, start_index: int, count: int | None = None) -> ReaderTTSSession:
        session = await self.get(session_id)
        start = max(0, min(int(start_index), max(0, session.segment_count - 1)))
        size = max(1, min(32, int(count if count is not None else settings.TTS_SESSION_WINDOW_SEGMENTS)))
        for index in range(start, min(session.segment_count, start + size)):
            await self._schedule(session, index, self._priority(index, start))
        return session

    async def await_segment(self, session_id: str, index: int, *, user_id: int | None = None, timeout: float | None = None) -> TTSSegmentAudio:
        session = await self.get(session_id, user_id=user_id)
        future = await self._schedule(session, int(index), self._priority(int(index), int(index)))
        try:
            if timeout is None:
                return await asyncio.shield(future)
            return await asyncio.wait_for(asyncio.shield(future), timeout=max(0.1, float(timeout)))
        except asyncio.TimeoutError as exc:
            raise TTSSegmentNotReady('Аудиофрагмент ещё готовится.') from exc

    async def await_first(self, session_id: str, *, timeout: float | None = None) -> TTSSegmentAudio | None:
        try:
            return await self.await_segment(session_id, 0, timeout=timeout or settings.TTS_FIRST_SEGMENT_WAIT_SECONDS)
        except TTSSegmentNotReady:
            return None

    async def manifest(self, session_id: str, *, user_id: int, start_index: int = 0, count: int | None = None, include_urls: bool = True) -> dict[str, Any]:
        session = await self.get(session_id, user_id=user_id)
        start = max(0, min(int(start_index), max(0, session.segment_count - 1)))
        size = max(1, min(40, int(count if count is not None else settings.TTS_SESSION_WINDOW_SEGMENTS)))
        await self.ensure_window(session.id, start_index=start, count=size)
        end = min(session.segment_count, start + size)
        items: list[dict[str, Any]] = []
        ready_count = 0
        for index in range(start, end):
            segment = session.prepared.segments[index]
            future = session.futures.get(index)
            state = 'queued'
            provider = ''
            duration_ms = 0
            quality: dict[str, Any] = {}
            url = ''
            error = session.errors.get(index, '')
            if future is not None:
                if future.cancelled():
                    state = 'cancelled'
                elif not future.done():
                    request = TTSSynthesisRequest(session.id, session.chapter_id, segment, session.voice, session.style, 24000, {'high_quality': session.high_quality})
                    state = 'generating' if self.queue.is_running(segment_job_key(request)) else 'queued'
                else:
                    try:
                        audio = future.result()
                    except Exception as exc:
                        state = 'failed'
                        error = str(exc)[:300]
                    else:
                        state = 'ready'
                        ready_count += 1
                        provider = audio.provider
                        duration_ms = int(audio.duration_ms or 0)
                        quality = dict(audio.quality or {})
                        if include_urls:
                            url = build_segment_media_url(user_id=session.user_id, session_id=session.id, segment_index=index, segment_digest=segment.digest)
            items.append({
                'index': index, 'status': state, 'url': url, 'provider': provider, 'duration_ms': duration_ms,
                'kind': segment.kind, 'pause_ms_after': segment.pause_ms_after, 'chars': segment.chars,
                'digest': segment.digest, 'error': error,
                'quality': quality,
                'generation_attempts': int(session.attempts.get(index) or 0),
            })
        first = session.futures.get(0)
        first_ready = bool(first and first.done() and not first.cancelled() and first.exception() is None)
        snap = self.queue.snapshot()
        return {
            'session_id': session.id, 'chapter_id': session.chapter_id, 'chapter_digest': session.chapter_digest,
            'voice': session.voice, 'style': session.style, 'high_quality': session.high_quality,
            'provider_order': list(session.providers), 'segment_count': session.segment_count,
            'window_start': start, 'window_end': end, 'ready_in_window': ready_count,
            'first_ready': first_ready, 'status': 'ready' if first_ready else 'preparing',
            'expires_at': session.expires_at,
            'diagnostics': dict(session.prepared.diagnostics),
            'quality_control': {
                'enabled': True,
                'provider_retries': max(0, min(2, int(settings.TTS_QUALITY_RETRIES or 1))),
                'session_retries': max(0, min(5, int(settings.TTS_SEGMENT_SESSION_RETRIES or 2))),
            },
            'segments': items,
            'queue': {'queued': snap.queued, 'running': snap.running, 'completed': snap.completed, 'failed': snap.failed, 'deduplicated': snap.deduplicated, 'workers': snap.workers},
        }

    async def remove(self, session_id: str, *, user_id: int) -> bool:
        async with self._lock:
            session = self._sessions.get(str(session_id or ''))
            if not session or int(session.user_id) != int(user_id):
                return False
            self._sessions.pop(session.id, None)
            for key, value in list(self._profile_index.items()):
                if value == session.id:
                    self._profile_index.pop(key, None)
            return True


def _secret() -> bytes:
    raw = settings.TTS_SIGNING_SECRET.strip() or settings.BOT_TOKEN.strip() or 'voxlyra-segment-tts'
    return hashlib.sha256(raw.encode('utf-8')).digest()


def sign_segment_media_token(*, user_id: int, session_id: str, segment_index: int, segment_digest: str, expires_at: int) -> str:
    payload = ':'.join((str(user_id), session_id, str(segment_index), segment_digest, str(expires_at)))
    return hmac.new(_secret(), payload.encode('utf-8'), hashlib.sha256).hexdigest()


def validate_segment_media_token(*, user_id: int, session_id: str, segment_index: int, segment_digest: str, expires_at: int, signature: str) -> bool:
    now = int(time.time())
    if expires_at < now or expires_at > now + 86400:
        return False
    expected = sign_segment_media_token(user_id=user_id, session_id=session_id, segment_index=segment_index, segment_digest=segment_digest, expires_at=expires_at)
    return hmac.compare_digest(expected, str(signature or ''))


def build_segment_media_url(*, user_id: int, session_id: str, segment_index: int, segment_digest: str, lifetime_seconds: int = 3600) -> str:
    expires_at = int(time.time()) + max(300, min(86400, int(lifetime_seconds)))
    signature = sign_segment_media_token(user_id=user_id, session_id=session_id, segment_index=segment_index, segment_digest=segment_digest, expires_at=expires_at)
    query = urlencode({'uid': int(user_id), 'exp': expires_at, 'sig': signature})
    return f'/media/reader-tts/session/{session_id}/{int(segment_index)}.mp3?{query}'
