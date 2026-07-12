from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from app.config import settings
from app.services.tts_providers import TTSProviderRegistry, TTSSegmentAudio, TTSSynthesisRequest


class TTSJobPriority(IntEnum):
    FIRST_CURRENT = 0
    BUFFER_CURRENT = 10
    REST_CURRENT = 20
    FIRST_NEXT = 30
    BACKGROUND_NEXT = 40


@dataclass(slots=True)
class TTSQueueJob:
    key: str
    request: TTSSynthesisRequest
    providers: tuple[str, ...]
    priority: TTSJobPriority
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TTSQueueSnapshot:
    queued: int
    running: int
    completed: int
    failed: int
    deduplicated: int
    workers: int


def configured_provider_order(*, high_quality: bool = False) -> tuple[str, ...]:
    raw = settings.TTS_PROVIDER_ORDER_HQ if high_quality else settings.TTS_PROVIDER_ORDER
    order = tuple(item.strip().lower() for item in str(raw or '').split(',') if item.strip())
    return order or (('moss', 'qwen', 'vosk', 'piper') if high_quality else ('vosk', 'moss', 'qwen', 'piper'))


def segment_job_key(request: TTSSynthesisRequest) -> str:
    quality = 'hq' if bool(request.metadata.get('high_quality')) else 'std'
    return ':'.join((quality, request.voice, request.style, request.segment.digest))


class TTSGenerationQueue:
    def __init__(self, registry: TTSProviderRegistry | None = None) -> None:
        self.registry = registry or TTSProviderRegistry()
        self._queue: asyncio.PriorityQueue[tuple[int, int, TTSQueueJob]] = asyncio.PriorityQueue()
        self._counter = itertools.count()
        self._futures: dict[str, asyncio.Future[TTSSegmentAudio]] = {}
        self._running: set[str] = set()
        self._workers: list[asyncio.Task[None]] = []
        self._completed = 0
        self._failed = 0
        self._deduplicated = 0

    async def start(self) -> None:
        if self._workers:
            return
        count = max(1, min(8, int(settings.TTS_WORKERS or 2)))
        self._workers = [asyncio.create_task(self._worker(), name=f'tts-worker-{index}') for index in range(count)]

    async def close(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(self, job: TTSQueueJob) -> asyncio.Future[TTSSegmentAudio]:
        existing = self._futures.get(job.key)
        if existing and not existing.cancelled():
            if not existing.done() or existing.exception() is None:
                self._deduplicated += 1
                return existing
            self._futures.pop(job.key, None)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TTSSegmentAudio] = loop.create_future()
        self._futures[job.key] = future
        await self._queue.put((int(job.priority), next(self._counter), job))
        return future

    async def _worker(self) -> None:
        while True:
            _, _, job = await self._queue.get()
            future = self._futures.get(job.key)
            if not future or future.cancelled() or future.done():
                self._queue.task_done()
                continue
            self._running.add(job.key)
            try:
                audio = await self.registry.synthesize(job.request, job.providers)
                if not future.done():
                    future.set_result(audio)
                self._completed += 1
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
                self._failed += 1
            finally:
                self._running.discard(job.key)
                self._queue.task_done()

    def is_running(self, key: str) -> bool:
        return key in self._running

    def snapshot(self) -> TTSQueueSnapshot:
        return TTSQueueSnapshot(self._queue.qsize(), len(self._running), self._completed, self._failed, self._deduplicated, len(self._workers))


def build_default_generation_queue() -> TTSGenerationQueue:
    return TTSGenerationQueue()
