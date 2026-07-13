from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest
from PIL import Image


def _image(path: Path, size: tuple[int, int] = (240, 360), color=(80, 45, 180)) -> None:
    Image.new("RGB", size, color).save(path)


def test_v192_build_and_stage2_assets_exist():
    from app.build_info import OWNER_BUILD_NAME, OWNER_BUILD_VERSION

    assert OWNER_BUILD_VERSION in {"v1.9.2", "v1.9.3", "v1.9.4", "v1.9.5", "v1.9.6", "v1.9.7", "v1.9.8", "v1.9.9", "v1.10.0", "v1.10.1", "v1.10.2", "v1.10.3", "v1.10.4", "v1.10.5", "v1.11.0", "v1.11.1", "v1.11.2", "v1.11.3", "v1.11.4"}
    assert OWNER_BUILD_NAME
    for relative in (
        "docs/COMICS_STAGE2_V1_9_2.md",
        "docs/STATUS_V1_9_2.md",
        "app/services/graphic_import.py",
        "templates/author.html",
        "static/js/author.js",
    ):
        assert Path(relative).is_file(), relative


def test_v192_fixed_layout_epub_is_imported_in_spine_order(tmp_path):
    from app.services.graphic_import import prepare_graphic_file

    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    _image(first, color=(120, 30, 40))
    _image(second, color=(30, 120, 40))
    epub = tmp_path / "chapter.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        archive.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<manifest><item id="p2" href="p2.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="p1" href="p1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="i1" href="images/first.jpg" media-type="image/jpeg"/>'
            '<item id="i2" href="images/second.jpg" media-type="image/jpeg"/></manifest>'
            '<spine><itemref idref="p1"/><itemref idref="p2"/></spine></package>',
        )
        archive.writestr("OEBPS/p1.xhtml", '<html><body><img src="images/first.jpg"/></body></html>')
        archive.writestr("OEBPS/p2.xhtml", '<html><body><img src="images/second.jpg"/></body></html>')
        archive.write(first, "OEBPS/images/first.jpg")
        archive.write(second, "OEBPS/images/second.jpg")

    pages = prepare_graphic_file(epub, epub.name, tmp_path / "prepared")
    assert [page.source_filename for page in pages] == ["first.jpg", "second.jpg"]
    assert [page.number for page in pages] == [1, 2]
    assert all(page.path.is_file() and page.path.suffix == ".webp" for page in pages)


def test_v192_7z_is_extracted_and_naturally_sorted(tmp_path):
    libarchive = pytest.importorskip("libarchive")
    from app.services.graphic_import import prepare_graphic_file

    images = []
    for name in ("page10.png", "page2.png", "page1.png"):
        path = tmp_path / name
        _image(path)
        images.append(path)
    archive_path = tmp_path / "chapter.7z"
    with libarchive.file_writer(str(archive_path), "7zip") as archive:
        archive.add_files(*(str(path) for path in images))

    pages = prepare_graphic_file(archive_path, archive_path.name, tmp_path / "7z-work")
    assert [page.source_filename for page in pages] == ["page1.png", "page2.png", "page10.png"]


def test_v192_long_webtoon_page_is_split_without_resizing_width(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.graphic_import import prepare_graphic_file

    monkeypatch.setattr(settings, "COMIC_IMAGE_MAX_WIDTH", 1000)
    monkeypatch.setattr(settings, "COMIC_WEBTOON_SLICE_HEIGHT", 2400)
    long_page = tmp_path / "long.png"
    _image(long_page, size=(640, 6100))

    pages = prepare_graphic_file(
        long_page,
        long_page.name,
        tmp_path / "webtoon-work",
        split_long_pages=True,
    )
    assert len(pages) == 3
    assert [page.height for page in pages] == [2400, 2400, 1300]
    assert all(page.width == 640 for page in pages)
    assert all("фрагмент" in page.source_filename for page in pages)


def test_v192_page_rotation_and_replacement_helpers(tmp_path):
    from app.services.graphic_import import prepare_replacement_page, rotate_graphic_page_file

    source = tmp_path / "source.png"
    _image(source, size=(320, 500))
    replacement = prepare_replacement_page(source, source.name, tmp_path / "replacement")
    assert replacement.width == 320 and replacement.height == 500
    rotated = rotate_graphic_page_file(replacement.path, tmp_path / "rotated.webp", 90)
    assert rotated.width == 500 and rotated.height == 320
    assert len(rotated.checksum) == 64


def test_v192_database_reorder_replace_metadata_and_delete(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "editor.sqlite3"))

    async def scenario():
        from app.db import (
            add_graphic_pages,
            create_author_profile,
            create_book,
            create_graphic_chapter_record,
            delete_graphic_page_for_author,
            get_author_profile,
            get_graphic_page,
            init_db,
            list_graphic_pages,
            list_graphic_pages_for_author,
            reorder_graphic_pages_for_author,
            update_graphic_page_file_for_author,
            upsert_user,
        )

        await init_db()
        author = await upsert_user(19201, "artist", "Artist")
        stranger = await upsert_user(19202, "stranger", "Stranger")
        await create_author_profile(author["id"], "Художник", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "Манхва", "Описание " * 20, "12+", "writing", False,
            "free", 0, content_type="manhwa", reading_mode="vertical",
        )
        chapter_id = await create_graphic_chapter_record(book_id, "Глава")
        await add_graphic_pages(chapter_id, [
            {"number": 1, "file_path": "storage/comics/a.webp", "source_filename": "a.png", "mime_type": "image/webp", "width": 100, "height": 200, "file_size": 10, "checksum": "a" * 64},
            {"number": 2, "file_path": "storage/comics/b.webp", "source_filename": "b.png", "mime_type": "image/webp", "width": 100, "height": 200, "file_size": 11, "checksum": "b" * 64},
            {"number": 3, "file_path": "storage/comics/c.webp", "source_filename": "c.png", "mime_type": "image/webp", "width": 100, "height": 200, "file_size": 12, "checksum": "c" * 64},
        ])
        owned = await list_graphic_pages_for_author(chapter_id, author["id"])
        assert owned is not None and len(owned) == 3
        assert await list_graphic_pages_for_author(chapter_id, stranger["id"]) is None
        ids = [int(row["id"]) for row in owned]
        assert await reorder_graphic_pages_for_author(chapter_id, author["id"], [ids[2], ids[0], ids[1]])
        reordered = await list_graphic_pages(chapter_id)
        assert [row["source_filename"] for row in reordered] == ["c.png", "a.png", "b.png"]

        first_id = int(reordered[0]["id"])
        assert await update_graphic_page_file_for_author(
            first_id, author["id"], source_filename="new.webp", mime_type="image/webp",
            width=777, height=888, file_size=999, checksum="d" * 64,
        )
        updated = await get_graphic_page(first_id)
        assert updated["width"] == 777 and updated["source_filename"] == "new.webp"

        deleted = await delete_graphic_page_for_author(int(reordered[1]["id"]), author["id"])
        assert deleted and deleted["source_filename"] == "a.png"
        remaining = await list_graphic_pages(chapter_id)
        assert [row["page_number"] for row in remaining] == [1, 2]
        assert [row["source_filename"] for row in remaining] == ["new.webp", "b.png"]

        await delete_graphic_page_for_author(int(remaining[1]["id"]), author["id"])
        last = await list_graphic_pages(chapter_id)
        blocked = await delete_graphic_page_for_author(int(last[0]["id"]), author["id"])
        assert blocked == {"error": "last_page"}

    asyncio.run(scenario())


def test_v192_routes_editor_and_dependencies_are_wired():
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    author_js = Path("static/js/author.js").read_text(encoding="utf-8")
    author_html = Path("templates/author.html").read_text(encoding="utf-8")
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    for route in (
        '/api/author/graphic-chapter/{graphic_chapter_id}/pages',
        '/api/author/graphic-chapter/{graphic_chapter_id}/pages/reorder',
        '/api/author/graphic-page/{graphic_page_id}/rotate',
        '/api/author/graphic-page/{graphic_page_id}/replace',
        '/api/author/graphic-page/{graphic_page_id}',
    ):
        assert route in webapp
    assert "loadGraphicPageEditor" in author_js
    assert "saveGraphicPageOrder" in author_js
    assert "graphicPageReplacementInput" in author_html
    assert ".cbr,.rar,.7z,.epub" in author_html
    assert "libarchive-c" in requirements
    assert "libarchive13" in dockerfile


def test_v192_author_page_editor_api_flow(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.services.tma_auth import TMAUser

    database = tmp_path / "api-editor.sqlite3"
    comic_root = tmp_path / "storage" / "comics"
    comic_root.mkdir(parents=True)
    monkeypatch.setattr(settings, "DATABASE_PATH", str(database))

    async def prepare():
        from app.db import (
            add_graphic_pages,
            create_author_profile,
            create_book,
            create_graphic_chapter_record,
            get_author_profile,
            init_db,
            upsert_user,
        )

        await init_db()
        author = await upsert_user(19211, "api_artist", "API Artist")
        await create_author_profile(author["id"], "API Художник", "", "", True)
        profile = await get_author_profile(author["id"])
        book_id = await create_book(
            profile["id"], "API Комикс", "Описание " * 20, "12+", "writing", False,
            "free", 0, content_type="comic", reading_mode="ltr",
        )
        chapter_id = await create_graphic_chapter_record(book_id, "Глава 1")
        chapter_dir = comic_root / str(book_id) / str(chapter_id)
        chapter_dir.mkdir(parents=True)
        rows = []
        for index, size in enumerate(((200, 300), (220, 320), (240, 340)), 1):
            path = chapter_dir / f"page-{index:05d}.webp"
            Image.new("RGB", size, (20 * index, 40, 120)).save(path, "WEBP")
            rows.append({
                "number": index,
                "file_path": str(path),
                "source_filename": f"{index}.png",
                "mime_type": "image/webp",
                "width": size[0],
                "height": size[1],
                "file_size": path.stat().st_size,
                "checksum": str(index) * 64,
            })
        await add_graphic_pages(chapter_id, rows)
        return author, chapter_id

    author, chapter_id = asyncio.run(prepare())

    import app.webapp as webapp_module

    async def fake_auth(_: str):
        return TMAUser(int(author["id"]), int(author["telegram_id"]), author["username"], author["full_name"])

    monkeypatch.setattr(webapp_module, "authenticate_init_data", fake_auth)
    monkeypatch.setattr(webapp_module, "GRAPHIC_STORAGE_ROOT", comic_root)

    with TestClient(webapp_module.create_app()) as client:
        headers = {"X-Telegram-Init-Data": "author"}
        loaded = client.get(f"/api/author/graphic-chapter/{chapter_id}/pages", headers=headers)
        assert loaded.status_code == 200
        pages = loaded.json()["pages"]
        assert len(pages) == 3

        reordered = client.post(
            f"/api/author/graphic-chapter/{chapter_id}/pages/reorder",
            headers=headers,
            json={"page_ids": [pages[2]["id"], pages[0]["id"], pages[1]["id"]]},
        )
        assert reordered.status_code == 200
        pages = reordered.json()["pages"]
        assert [page["source_filename"] for page in pages] == ["3.png", "1.png", "2.png"]

        rotate = client.post(
            f"/api/author/graphic-page/{pages[0]['id']}/rotate",
            headers=headers,
            json={"degrees": 90},
        )
        assert rotate.status_code == 200
        assert rotate.json()["page"]["width"] == 340
        assert rotate.json()["page"]["height"] == 240

        replacement = tmp_path / "replacement.png"
        _image(replacement, size=(333, 444), color=(200, 100, 30))
        with replacement.open("rb") as stream:
            replaced = client.post(
                f"/api/author/graphic-page/{pages[1]['id']}/replace",
                headers=headers,
                files={"file": ("replacement.png", stream, "image/png")},
            )
        assert replaced.status_code == 200
        assert replaced.json()["page"]["width"] == 333
        assert replaced.json()["page"]["height"] == 444

        deleted = client.delete(f"/api/author/graphic-page/{pages[2]['id']}", headers=headers)
        assert deleted.status_code == 200
        assert [page["number"] for page in deleted.json()["pages"]] == [1, 2]
