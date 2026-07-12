from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.services.reader_tts import prepare_literary_text


@dataclass(slots=True, frozen=True)
class TTSTextSegment:
    index: int
    text: str
    kind: str
    pause_ms_after: int
    chars: int
    digest: str


@dataclass(slots=True)
class PreparedTTSChapter:
    spoken_text: str
    segments: list[TTSTextSegment]
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _apply_glossary(text: str, glossary: dict[str, str] | None) -> str:
    result = text
    for source, spoken in sorted((glossary or {}).items(), key=lambda item: len(item[0]), reverse=True):
        source = str(source or '').strip()
        spoken = str(spoken or '').strip()
        if source and spoken:
            result = re.sub(rf'(?<!\w){re.escape(source)}(?!\w)', spoken, result, flags=re.IGNORECASE)
    return result


def _kind(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith(('—', '–', '-')) or re.match(r'^[«“"]', stripped):
        return 'dialogue'
    return 'narration'


def _pause(text: str, paragraph_end: bool) -> int:
    if paragraph_end:
        return 520
    if text.rstrip().endswith(('…', '?!', '!?')):
        return 380
    if text.rstrip().endswith(('?', '!')):
        return 300
    return 220


def _split_long(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence.strip()]
    parts = re.split(r'(?<=[,;:—–])\s+', sentence)
    result: list[str] = []
    current = ''
    for part in parts:
        part = part.strip()
        if not part:
            continue
        candidate = f'{current} {part}'.strip()
        if current and len(candidate) > max_chars:
            result.append(current)
            current = part
        else:
            current = candidate
    if current:
        result.append(current)
    final: list[str] = []
    for part in result:
        while len(part) > max_chars:
            cut = part.rfind(' ', 0, max_chars + 1)
            if cut < max_chars // 2:
                cut = max_chars
            final.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            final.append(part)
    return final


def prepare_tts_chapter(
    value: str,
    *,
    glossary: dict[str, str] | None = None,
    target_chars: int = 360,
    max_chars: int = 620,
    first_max_chars: int = 220,
) -> PreparedTTSChapter:
    prepared = _apply_glossary(prepare_literary_text(value), glossary)
    target_chars = max(120, min(900, int(target_chars)))
    max_chars = max(target_chars, min(1400, int(max_chars)))
    first_max_chars = max(80, min(max_chars, int(first_max_chars)))

    raw_units: list[tuple[str, bool]] = []
    paragraphs = [item.strip() for item in re.split(r'\n{2,}', prepared) if item.strip()]
    for paragraph in paragraphs:
        sentences = [item.strip() for item in re.split(r'(?<=[.!?…»”"])\s+(?=[А-ЯЁA-Z0-9«“"—–-])', paragraph) if item.strip()]
        if not sentences:
            sentences = [paragraph]
        for position, sentence in enumerate(sentences):
            for chunk in _split_long(sentence, max_chars):
                raw_units.append((chunk, position == len(sentences) - 1))

    merged: list[tuple[str, bool]] = []
    current = ''
    current_paragraph_end = False
    for unit, paragraph_end in raw_units:
        limit = first_max_chars if not merged and not current else target_chars
        candidate = f'{current} {unit}'.strip()
        if current and (len(candidate) > limit or _kind(current) != _kind(unit)):
            merged.append((current, current_paragraph_end))
            current = unit
            current_paragraph_end = paragraph_end
        else:
            current = candidate
            current_paragraph_end = paragraph_end
        if paragraph_end and len(current) >= max(120, limit // 2):
            merged.append((current, True))
            current = ''
            current_paragraph_end = False
    if current:
        merged.append((current, current_paragraph_end))

    segments: list[TTSTextSegment] = []
    for index, (text, paragraph_end) in enumerate(merged):
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]
        segments.append(TTSTextSegment(index, text, _kind(text), _pause(text, paragraph_end), len(text), digest))
    if not segments:
        raise ValueError('В главе нет текста для озвучивания.')
    return PreparedTTSChapter(
        spoken_text=prepared,
        segments=segments,
        diagnostics={
            'paragraphs': len(paragraphs),
            'segments': len(segments),
            'characters': len(prepared),
            'first_segment_chars': segments[0].chars,
            'max_segment_chars': max(item.chars for item in segments),
        },
    )
