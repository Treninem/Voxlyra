from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import fitz
import pytest
from jinja2 import Environment, FileSystemLoader
from PIL import Image


def _make_image(path: Path, size: tuple[int, int] = (180, 260), value: int = 120) -> None:
    Image.new("RGB", size, (value, value // 2, 255 - value)).save(path)


def test_v191_build_and_reader_assets_exist():
    from app.build_info import OWNER_BUILD_NAME, OWNER_BUILD_VERSION

    assert OWNER_BUILD_VERSION in {"v1.9.1", "v1.9.2", "v1.9.3", "v1.9.4", "v1.9.5", "v1.9.6", "v1.9.7", "v1.9.8", "v1.9.9", "v1.10.0", "v1.10.1", "v1.10.2", "v1.10.3", "v1.10.4", "v1.10.5", "v1.11.0", "v1.11.1", "v1.11.2", "v1.11.3", "v1.11.4", "v1.11.5", "v1.11.6", "v1.11.7", "v1.11.8", "v1.11.9", "v1.11.11", "v1.11.12"}
    assert OWNER_BUILD_NAME
    for relative in (
        "templates/comic_reader.html",
        "static/js/comic.js",
        "app/services/graphic_import.py",
        "docs/COMICS_STAGE1_V1_9_1.md",
        "docs/STATUS_V1_9_1.md",
    ):
        assert Path(relative).is_file(), relative


def test_v191_templates_compile():
    environment = Environment(loader=FileSystemLoader("templates"), autoescape=True)
    for name in ("catalog.html", "book.html", "author.html", "comic_reader.html", "_macros.html"):
        environment.get_template(name)


def test_v191_graphic_images_are_naturally_sorted_and_optimized(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.graphic_import import prepare_graphic_images

    monkeypatch.setattr(settings, "COMIC_IMAGE_MAX_WIDTH", 800)
    source = tmp_path / "source"
    source.mkdir()
    inputs = []
    for name, value in (("page10.png", 100), ("page2.png", 120), ("page1.png", 140)):
        path = source / name
        _make_image(path, size=(1200, 1800), value=value)
        inputs.append((path, name))

    pages = prepare_graphic_images(inputs, tmp_path / "prepared")
    assert [page.source_filename for page in pages] == ["page1.png", "page2.png", "page10.png"]
    assert [page.number for page in pages] == [1, 2, 3]
    assert all(page.path.suffix == ".webp" and page.path.is_file() for page in pages)
    assert all(page.width <= 800 for page in pages)
    assert all(len(page.checksum) == 64 for page in pages)


def test_v191_cbz_rejects_path_traversal(tmp_path):
    from app.services.graphic_import import GraphicImportError, prepare_graphic_file

    image = tmp_path / "page.png"
    _make_image(image)
    archive = tmp_path / "unsafe.cbz"
    with zipfile.ZipFile(archive, "w") as target:
        target.write(image, "../page.png")

    with pytest.raises(GraphicImportError, match="небезопасный путь"):
        prepare_graphic_file(archive, archive.name, tmp_path / "work")


def test_v191_pdf_is_rendered_to_pages(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.graphic_import import prepare_graphic_file

    monkeypatch.setattr(settings, "COMIC_IMAGE_MAX_WIDTH", 720)
    pdf = tmp_path / "chapter.pdf"
    document = fitz.open()
    for text in ("Первая", "Вторая"):
        page = document.new_page(width=360, height=540)
        page.insert_text((50, 80), text)
    document.save(pdf)
    document.close()

    pages = prepare_graphic_file(pdf, pdf.name, tmp_path / "pdf-work")
    assert len(pages) == 2
    assert all(page.width > 0 and page.height > 0 for page in pages)
    assert all(page.path.is_file() for page in pages)


def test_v191_database_graphic_chapter_catalog_and_progress(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "graphic.sqlite3"))

    async def scenario():
        from app.db import (
            add_graphic_pages,
            create_author_profile,
            create_book,
            create_graphic_chapter_record,
            get_author_profile,
            get_graphic_reading_progress,
            init_db,
            list_catalog_books,
            list_graphic_chapters_for_book,
            publish_book_content,
            save_graphic_reading_progress,
            set_book_publication_status,
            upsert_user,
        )

        await init_db()
        author = await upsert_user(19101, "graphic_author", "Graphic Author")
        await create_author_profile(author["id"], "Художник", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Лунная манга", "Описание графического произведения " * 4,
            "12+", "writing", False, "free", 0, content_type="manga", reading_mode="rtl",
        )
        chapter_id = await create_graphic_chapter_record(book_id, "Глава 1", reading_mode="inherit")
        saved = await add_graphic_pages(chapter_id, [
            {
                "number": 1, "file_path": "storage/comics/1/1/page-00001.webp",
                "source_filename": "1.png", "mime_type": "image/webp",
                "width": 1000, "height": 1600, "file_size": 1234, "checksum": "a" * 64,
            },
            {
                "number": 2, "file_path": "storage/comics/1/1/page-00002.webp",
                "source_filename": "2.png", "mime_type": "image/webp",
                "width": 1000, "height": 1600, "file_size": 1235, "checksum": "b" * 64,
            },
        ])
        assert saved == 2
        await set_book_publication_status(book_id, "published")
        published = await publish_book_content(book_id)
        assert published["graphics"] == 1

        chapters = await list_graphic_chapters_for_book(book_id, published_only=True)
        assert len(chapters) == 1
        assert chapters[0]["pages_count"] == 2

        catalog = await list_catalog_books()
        row = next(item for item in catalog if item["id"] == book_id)
        assert row["content_type"] == "manga"
        assert row["reading_mode"] == "rtl"
        assert row["first_graphic_chapter_id"] == chapter_id
        assert row["graphic_pages_count"] == 2

        assert await get_graphic_reading_progress(author["id"], chapter_id) == 1
        await save_graphic_reading_progress(author["id"], chapter_id, 2)
        assert await get_graphic_reading_progress(author["id"], chapter_id) == 2

    asyncio.run(scenario())


def test_v191_routes_and_device_cache_are_wired():
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    comic_js = Path("static/js/comic.js").read_text(encoding="utf-8")
    author_js = Path("static/js/author.js").read_text(encoding="utf-8")
    assert '@app.get("/comic/{graphic_chapter_id}"' in webapp
    assert '@app.get("/api/comic/{graphic_chapter_id}"' in webapp
    assert '@app.get("/media/comic/{graphic_chapter_id}/{page_number}"' in webapp
    assert 'indexedDB.open(GRAPHIC_DB_NAME' in comic_js
    assert 'loadCachedGraphicMeta' in comic_js
    assert '/api/author/projects' in author_js
    assert '/graphic/images' in author_js
