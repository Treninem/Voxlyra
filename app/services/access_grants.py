from __future__ import annotations

import re
from dataclasses import dataclass


class ChapterSelectionError(ValueError):
    """Пользователь ввёл некорректный список глав."""


@dataclass(frozen=True)
class ChapterSelection:
    numbers: tuple[int, ...]
    normalized: str


_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-"})
_ALLOWED = re.compile(r"^[0-9,;\s\-]+$")
_TOKEN = re.compile(r"^(\d+)(?:-(\d+))?$")


def _compress(numbers: list[int]) -> str:
    if not numbers:
        return ""
    parts: list[str] = []
    start = previous = numbers[0]
    for value in numbers[1:]:
        if value == previous + 1:
            previous = value
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(parts)


def parse_chapter_selection(raw: str, *, max_chapters: int = 5000, max_number: int = 1_000_000) -> ChapterSelection:
    """Разбирает ``33, 56-67 98 34,36,38`` в отсортированный набор.

    Поддерживаются пробелы, запятые, точки с запятой и разные виды тире.
    Значения удаляются от повторов. Ограничение защищает базу от случайного
    ввода огромного диапазона.
    """
    value = str(raw or "").translate(_DASHES).strip()
    if not value:
        raise ChapterSelectionError("Укажите номер главы или диапазон, например 33, 56-67 или 1-100.")
    if not _ALLOWED.fullmatch(value):
        raise ChapterSelectionError("Используйте только номера, пробелы, запятые и диапазоны через дефис.")

    tokens = [token for token in re.split(r"[,;\s]+", value) if token]
    selected: set[int] = set()
    for token in tokens:
        match = _TOKEN.fullmatch(token)
        if not match:
            raise ChapterSelectionError(f"Не удалось разобрать «{token}».")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < 1 or start > max_number or end > max_number:
            raise ChapterSelectionError(f"Номера глав должны быть от 1 до {max_number}.")
        if start > end:
            start, end = end, start
        if end - start + 1 > max_chapters:
            raise ChapterSelectionError(f"Один диапазон не может содержать больше {max_chapters} глав.")
        selected.update(range(start, end + 1))
        if len(selected) > max_chapters:
            raise ChapterSelectionError(f"За один раз можно открыть не больше {max_chapters} глав.")

    numbers = sorted(selected)
    return ChapterSelection(tuple(numbers), _compress(numbers))
