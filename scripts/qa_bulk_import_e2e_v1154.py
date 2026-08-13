"""End-to-end mixed graphic import, database routing and rollback test."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings


async def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        os.chdir(root)
        settings.DATABASE_PATH = str(root / "qa.sqlite3")
        settings.LIBRARY_STORAGE_ROOT = str(root / "library")
        settings.LIBRARY_IMPORT_WORK_ROOT = str(root / "work")
        settings.COMIC_STORAGE_ROOT = str(root / "comics")
        settings.BOOK_COVER_STORAGE_ROOT = str(root / "covers")
        from app.db import connect, init_db, upsert_user
        from app.services.library_manager import ensure_library_schema, import_library_zip, rollback_batch_drafts

        await init_db()
        await ensure_library_schema()
        user = await upsert_user(telegram_id=1154, username="qa", full_name="QA")
        source = root / "source"
        types = ["comic", "manga", "manhwa", "webtoon", "graphic_novel"]
        modes = ["ltr", "rtl", "vertical", "single", "spread"]
        for index in range(50):
            folder = source / "Comics" / f"{index + 1:03d}"
            chapter = folder / "Chapters" / "001"
            chapter.mkdir(parents=True)
            metadata = {
                "title": f"Graphic QA {index + 1}", "author": "VoxLyra QA",
                "genre": ["QA"], "age_rating": "16+", "license": "platform_original",
                "rights_checked": True, "description": "QA", "content_type": types[index % 5],
                "reading_mode": modes[index % 5],
            }
            (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            Image.new("RGB", (600, 900), (index * 7 % 255, 20, 40)).save(folder / "cover.jpg")
            Image.new("RGB", (720, 1200), (20, index * 9 % 255, 60)).save(chapter / "001.png")
        archive_path = root / "graphics.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in source.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(source))
        result = await import_library_zip(archive_path, archive_path.name, int(user["id"]))
        assert result.added == 50 and not result.errors
        async with connect() as db:
            books = int((await (await db.execute("SELECT COUNT(*) FROM books WHERE publication_status='draft'")).fetchone())[0])
            chapters = int((await (await db.execute("SELECT COUNT(*) FROM graphic_chapters WHERE status='draft'")).fetchone())[0])
            pages = int((await (await db.execute("SELECT COUNT(*) FROM graphic_pages")).fetchone())[0])
            genres = int((await (await db.execute(
                "SELECT COUNT(*) FROM book_option_values WHERE option_group='genres' AND option_label='QA'"
            )).fetchone())[0])
        assert (books, chapters, pages, genres) == (50, 50, 50, 50)
        rollback = await rollback_batch_drafts(result.batch_id)
        assert rollback["books"] == 50 and rollback["chapters"] == 50
        print("OK: 50 end-to-end graphic works, routing, pages and rollback")


if __name__ == "__main__":
    asyncio.run(main())
