from __future__ import annotations

import gzip
import json
import re
import uuid
from pathlib import Path

from app.services.book_parser import ParsedChapter

PREVIEW_ROOT = Path("storage/temp/web_book_imports")


def save_web_import_preview(
    chapters: list[ParsedChapter],
    *,
    user_id: int,
    book_id: int,
    original_name: str,
) -> str:
    PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "user_id": int(user_id),
        "book_id": int(book_id),
        "original_name": str(original_name)[:180],
        "chapters": [chapter.to_dict() for chapter in chapters],
    }
    path = PREVIEW_ROOT / f"{token}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=6) as file:
        json.dump(payload, file, ensure_ascii=False)
    return token


def load_web_import_preview(token: str, *, user_id: int, book_id: int) -> tuple[list[ParsedChapter], str]:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return [], ""
    path = PREVIEW_ROOT / f"{token}.json.gz"
    if not path.exists():
        return [], ""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as file:
            payload = json.load(file)
    except Exception:
        return [], ""
    if int(payload.get("user_id") or 0) != int(user_id) or int(payload.get("book_id") or 0) != int(book_id):
        return [], ""
    chapters = [
        ParsedChapter(int(item["number"]), str(item["title"]), str(item["text"]))
        for item in payload.get("chapters", [])
    ]
    return chapters, str(payload.get("original_name") or "")


def delete_web_import_preview(token: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return
    (PREVIEW_ROOT / f"{token}.json.gz").unlink(missing_ok=True)
