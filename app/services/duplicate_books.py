from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from app.db import list_books_for_duplicate_check


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    book_id: int
    title: str
    author_name: str
    reason: str
    severity: str
    similarity: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "author_name": self.author_name,
            "reason": self.reason,
            "severity": self.severity,
            "similarity": round(self.similarity, 3),
        }


def normalize_book_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("ё", "е")
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    # Слова из одного символа и декоративные номера не должны создавать ложные различия.
    return " ".join(part for part in text.split() if len(part) > 1 or part.isdigit())


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def find_book_duplicates(
    *,
    title: object,
    author_id: int | None,
    exclude_book_id: int | None = None,
    source_file_hash: str | None = None,
    similarity_threshold: float = 0.88,
    limit: int = 6,
) -> list[DuplicateMatch]:
    normalized = normalize_book_title(title)
    file_hash = str(source_file_hash or "").strip().lower()
    rows = await list_books_for_duplicate_check(exclude_book_id=exclude_book_id)
    matches: list[DuplicateMatch] = []
    seen: set[int] = set()

    for row in rows:
        candidate_id = int(row["id"])
        candidate_title = str(row["title"] or "")
        candidate_author_id = int(row["author_id"]) if row["author_id"] is not None else None
        candidate_normalized = normalize_book_title(candidate_title)
        candidate_hash = str(row["source_file_hash"] or "").strip().lower()
        same_author = author_id is not None and candidate_author_id == int(author_id)

        reason = ""
        severity = "warning"
        similarity = 0.0
        if file_hash and candidate_hash and file_hash == candidate_hash:
            reason = "полностью совпадает загруженный файл"
            severity = "block"
            similarity = 1.0
        elif normalized and candidate_normalized == normalized:
            reason = "совпадают название и автор" if same_author else "точно совпадает название"
            severity = "block" if same_author else "warning"
            similarity = 1.0
        elif normalized and candidate_normalized:
            similarity = SequenceMatcher(None, normalized, candidate_normalized).ratio()
            if similarity >= similarity_threshold:
                reason = "названия очень похожи"
                severity = "warning"
            else:
                continue
        else:
            continue

        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        matches.append(
            DuplicateMatch(
                book_id=candidate_id,
                title=candidate_title,
                author_name=str(row["pen_name"] or "Автор не указан"),
                reason=reason,
                severity=severity,
                similarity=similarity,
            )
        )

    matches.sort(key=lambda item: (item.severity != "block", -item.similarity, item.book_id))
    return matches[: max(1, int(limit))]


def duplicate_warning_text(matches: list[DuplicateMatch]) -> str:
    if not matches:
        return ""
    lines = [
        "<b>⚠️ Похоже, такая книга уже есть</b>",
        "",
        "Вокслира нашла совпадения и остановила автоматическую публикацию, чтобы не создавать копии:",
    ]
    for match in matches[:6]:
        lines.append(
            f"• «{match.title}» — {match.author_name}; {match.reason}."
        )
    lines.extend(
        [
            "",
            "Если это новая редакция или действительно другая книга, подтвердите продолжение. "
            "Иначе вернитесь и измените название либо выберите другой файл.",
        ]
    )
    return "\n".join(lines)
