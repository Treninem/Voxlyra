import json
import uuid
from pathlib import Path

from app.services.book_parser import ParsedChapter


TEMP_DIR = Path("storage/temp")


def save_import_preview(chapters: list[ParsedChapter]) -> str:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMP_DIR / f"chapters_import_{uuid.uuid4().hex}.json"
    path.write_text(
        json.dumps([ch.to_dict() for ch in chapters], ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def load_import_preview(path: str) -> list[ParsedChapter]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    data = json.loads(file_path.read_text(encoding="utf-8"))
    return [ParsedChapter(int(item["number"]), str(item["title"]), str(item["text"])) for item in data]


def delete_import_preview(path: str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
