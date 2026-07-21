from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


UNIQUE_ACCESS_KINDS = {"book", "chapter", "audio", "graphic", "graphic_volume"}


@dataclass(frozen=True)
class AccessDescriptor:
    key: str
    conflict_keys: tuple[str, ...]
    kind: str
    book_id: int | None
    target_id: int | None
    volume_number: int | None = None


def _positive(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def access_descriptor_for_target(target: Mapping[str, Any] | None) -> AccessDescriptor | None:
    if not target:
        return None
    kind = str(target.get("kind") or "").strip().lower()
    if kind not in UNIQUE_ACCESS_KINDS:
        return None
    book_id = _positive(target.get("book_id"))
    target_id = _positive(target.get("target_id"))
    if kind == "book" and book_id:
        key = f"book:{book_id}"
        return AccessDescriptor(key, (key,), kind, book_id, book_id)
    if kind == "chapter" and target_id and book_id:
        key = f"chapter:{target_id}"
        return AccessDescriptor(key, (key, f"book:{book_id}"), kind, book_id, target_id)
    if kind == "audio" and target_id and book_id:
        key = f"audio:{target_id}"
        return AccessDescriptor(key, (key, f"book:{book_id}"), kind, book_id, target_id)
    if kind == "graphic" and target_id and book_id:
        volume = _positive(target.get("volume_number"))
        conflicts = [f"graphic:{target_id}", f"book:{book_id}"]
        if volume:
            conflicts.insert(1, f"graphic_volume:{book_id}:{volume}")
        return AccessDescriptor(conflicts[0], tuple(conflicts), kind, book_id, target_id, volume)
    if kind == "graphic_volume" and book_id:
        volume = _positive(target.get("volume_number") or target.get("target_id"))
        if not volume:
            return None
        key = f"graphic_volume:{book_id}:{volume}"
        return AccessDescriptor(key, (key, f"book:{book_id}"), kind, book_id, volume, volume)
    return None


def _value(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(key, default)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def access_descriptor_for_purchase_row(row: Mapping[str, Any]) -> AccessDescriptor | None:
    kind = str(_value(row, "purchase_kind", "content") or "content")
    book_id = _positive(_value(row, "book_id"))
    chapter_id = _positive(_value(row, "chapter_id"))
    audio_id = _positive(_value(row, "audio_chapter_id"))
    graphic_id = _positive(_value(row, "graphic_chapter_id"))
    volume = _positive(_value(row, "graphic_volume_number"))

    if kind == "graphic_volume" and book_id and volume:
        return AccessDescriptor(
            f"graphic_volume:{book_id}:{volume}",
            (f"graphic_volume:{book_id}:{volume}", f"book:{book_id}"),
            "graphic_volume", book_id, volume, volume,
        )
    if graphic_id:
        return AccessDescriptor(f"graphic:{graphic_id}", (f"graphic:{graphic_id}",), "graphic", book_id, graphic_id, volume)
    if chapter_id:
        return AccessDescriptor(f"chapter:{chapter_id}", (f"chapter:{chapter_id}",), "chapter", book_id, chapter_id)
    if audio_id:
        return AccessDescriptor(f"audio:{audio_id}", (f"audio:{audio_id}",), "audio", book_id, audio_id)
    if kind == "content" and book_id and not any((chapter_id, audio_id, graphic_id, volume)):
        return AccessDescriptor(f"book:{book_id}", (f"book:{book_id}",), "book", book_id, book_id)
    return None
