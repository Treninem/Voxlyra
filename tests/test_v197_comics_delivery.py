from __future__ import annotations

import asyncio
import json
from pathlib import Path

from PIL import Image


def test_v197_build_assets_and_reader_controls_exist():
    from app.build_info import OWNER_BUILD_NAME, OWNER_BUILD_VERSION

    assert OWNER_BUILD_VERSION in {"v1.9.7", "v1.9.8", "v1.9.9", "v1.10.0", "v1.10.1", "v1.10.2", "v1.10.3", "v1.10.4"}
    assert "комикс" in OWNER_BUILD_NAME.lower()
    for relative in (
        "app/services/graphic_storage.py",
        "static/js/comic-sw.js",
        "docs/COMICS_STAGE3_V1_9_7.md",
        "docs/STATUS_V1_9_7.md",
    ):
        assert Path(relative).is_file(), relative

    html = Path("templates/comic_reader.html").read_text(encoding="utf-8")
    js = Path("static/js/comic.js").read_text(encoding="utf-8")
    for control in (
        "graphicCacheChapter",
        "graphicCacheVolume",
        "graphicClearChapterCache",
        "graphicClearVolumeCache",
        "graphicNetworkStatus",
        "graphicCacheStatus",
    ):
        assert control in html
        assert control in js
    assert "/offline-manifest" in js
    assert "/comic-sw.js" in js


def test_v197_import_builds_adaptive_variants_and_installs_them(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.graphic_import import prepare_graphic_images
    from app.services.graphic_storage import install_prepared_page, select_page_variant

    monkeypatch.setattr(settings, "COMIC_VARIANT_WIDTHS", "480,960,1440")
    source = tmp_path / "page.png"
    Image.new("RGB", (1600, 2400), (55, 30, 130)).save(source)
    pages = prepare_graphic_images([(source, "page.png")], tmp_path / "work")
    assert len(pages) == 1
    page = pages[0]
    assert page.variants and set(page.variants) == {"small", "medium", "large"}
    widths = [int(item["width"]) for item in page.variants.values()]
    assert widths == [480, 960, 1440]
    assert all(Path(str(item["path"])).is_file() for item in page.variants.values())

    storage_root = tmp_path / "storage" / "comics"
    target = storage_root / "1" / "2" / "page-00001.webp"
    installed = install_prepared_page(page, target)
    variants = json.loads(installed["variants_json"])
    assert len(variants) == 3
    assert target.is_file()
    assert all(Path(str(item["path"])).is_file() for item in variants.values())

    row = {
        "variants_json": installed["variants_json"],
        "file_path": installed["file_path"],
        "width": installed["width"],
        "height": installed["height"],
        "file_size": installed["file_size"],
        "checksum": installed["checksum"],
        "mime_type": installed["mime_type"],
    }
    small = select_page_variant(row, requested="small", root=storage_root)
    automatic = select_page_variant(row, requested="auto", target_width=900, root=storage_root)
    assert small and int(small["width"]) == 480
    assert automatic and int(automatic["width"]) == 960


def test_v197_database_migrates_variants_and_volume_fields(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "v197.sqlite3"))

    async def scenario():
        import aiosqlite
        from app.db import (
            create_author_profile,
            create_book,
            create_graphic_chapter_record,
            get_author_profile,
            get_graphic_chapter,
            init_db,
            list_graphic_chapters_for_book,
            upsert_user,
        )

        await init_db()
        async with aiosqlite.connect(settings.DATABASE_PATH) as db:
            page_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(graphic_pages)")).fetchall()}
            chapter_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(graphic_chapters)")).fetchall()}
        assert {"variants_json", "storage_backend", "storage_key"} <= page_columns
        assert {"volume_number", "volume_title"} <= chapter_columns

        user = await upsert_user(19701, "stage3", "Stage Three")
        await create_author_profile(user["id"], "Автор", "", "", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(
            profile["id"], "Тома", "Описание " * 20, "12+", "writing", False,
            "free", 0, content_type="manga", reading_mode="rtl",
        )
        later = await create_graphic_chapter_record(
            book_id, "Глава 2", volume_number=2, volume_title="Продолжение"
        )
        earlier = await create_graphic_chapter_record(
            book_id, "Глава 5", volume_number=1, volume_title="Начало"
        )
        chapter = await get_graphic_chapter(later)
        assert int(chapter["volume_number"]) == 2
        assert chapter["volume_title"] == "Продолжение"
        ordered = await list_graphic_chapters_for_book(book_id)
        assert [int(row["id"]) for row in ordered] == [earlier, later]

    asyncio.run(scenario())


def test_v197_resumable_upload_and_delivery_wiring():
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    author_js = Path("static/js/author.js").read_text(encoding="utf-8")
    comic_js = Path("static/js/comic.js").read_text(encoding="utf-8")
    env = Path(".env.example").read_text(encoding="utf-8")

    for value in (
        "resume_upload_id",
        "/graphic/upload/{upload_id}/status",
        "/graphic/upload/{upload_id}",
        "/offline-manifest",
        "variant: str = \"auto\"",
    ):
        assert value in webapp
    assert "resume_upload_id" in author_js
    assert "new Set((Array.isArray(start.received)" in author_js
    assert "COMIC_VARIANT_WIDTHS" in env
    assert "COMIC_DEVICE_CACHE_MAX_MB" in env
    assert "enforceGraphicCacheLimits" in comic_js
    assert "navigator.connection" in comic_js
