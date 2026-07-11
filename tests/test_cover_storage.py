import asyncio
from pathlib import Path
from types import SimpleNamespace


class FakeBot:
    def __init__(self, payload: bytes = b"fake-jpeg-data") -> None:
        self.payload = payload
        self.downloaded_paths: list[str] = []

    async def get_file(self, file_id: str):
        assert file_id
        return SimpleNamespace(file_path="photos/telegram_cover.jpg")

    async def download_file(self, file_path: str, destination: Path):
        self.downloaded_paths.append(file_path)
        Path(destination).write_bytes(self.payload)
        return destination


def test_downloaded_cover_is_saved_and_linked_to_book(tmp_path, monkeypatch):
    database = tmp_path / "cover.sqlite3"

    from app.config import settings
    settings.DATABASE_PATH = str(database)

    import app.services.cover_storage as cover_storage
    monkeypatch.setattr(cover_storage, "COVER_ROOT", tmp_path / "covers")

    async def scenario():
        from app.db import (
            create_author_profile,
            create_book,
            get_author_profile,
            get_book,
            init_db,
            upsert_user,
        )

        await init_db()
        user = await upsert_user(9801, "cover_author", "Cover Author")
        await create_author_profile(user["id"], "Автор", "Описание", "RU", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(
            profile["id"],
            "Книга с обложкой",
            "Описание",
            "16+",
            "writing",
            False,
            "free",
            0,
            cover_file_id="telegram-file-id",
        )

        stored_path = await cover_storage.download_book_cover(FakeBot(), book_id, "telegram-file-id")
        book = await get_book(book_id)

        assert book["cover_file_id"] == "telegram-file-id"
        assert book["cover_path"] == stored_path
        assert Path(stored_path).read_bytes() == b"fake-jpeg-data"

    asyncio.run(scenario())


def test_old_cover_file_ids_are_restored(tmp_path, monkeypatch):
    database = tmp_path / "restore.sqlite3"

    from app.config import settings
    settings.DATABASE_PATH = str(database)

    import app.services.cover_storage as cover_storage
    monkeypatch.setattr(cover_storage, "COVER_ROOT", tmp_path / "covers")

    async def scenario():
        from app.db import (
            create_author_profile,
            create_book,
            get_author_profile,
            get_book,
            init_db,
            upsert_user,
        )

        await init_db()
        user = await upsert_user(9802, "restore_author", "Restore Author")
        await create_author_profile(user["id"], "Автор", "Описание", "RU", True)
        profile = await get_author_profile(user["id"])
        book_id = await create_book(
            profile["id"],
            "Старая книга",
            "Описание",
            "16+",
            "writing",
            False,
            "free",
            0,
            cover_file_id="old-telegram-file-id",
        )

        restored, failed = await cover_storage.restore_missing_book_covers(FakeBot())
        book = await get_book(book_id)

        assert (restored, failed) == (1, 0)
        assert book["cover_path"]
        assert Path(book["cover_path"]).exists()

    asyncio.run(scenario())


def test_draft_cover_is_available_only_to_its_author(tmp_path, monkeypatch):
    import asyncio
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.services.tma_auth import TMAUser, TMAAuthError
    import app.webapp as webapp_module

    database = tmp_path / "draft-cover.sqlite3"
    settings.DATABASE_PATH = str(database)
    cover_file = tmp_path / "draft-cover.jpg"
    cover_file.write_bytes(b"draft-cover-bytes")

    async def prepare():
        from app.db import (
            create_author_profile,
            create_book,
            get_author_profile,
            init_db,
            update_book_cover_path,
            upsert_user,
        )

        await init_db()
        owner = await upsert_user(9803, "draft_owner", "Draft Owner")
        stranger = await upsert_user(9804, "draft_stranger", "Draft Stranger")
        await create_author_profile(owner["id"], "Автор", "", "RU", True)
        await create_author_profile(stranger["id"], "Другой автор", "", "RU", True)
        profile = await get_author_profile(owner["id"])
        book_id = await create_book(
            profile["id"],
            "Черновик с обложкой",
            "Описание",
            "16+",
            "writing",
            False,
            "free",
            0,
            cover_file_id="draft-file-id",
        )
        await update_book_cover_path(book_id, str(cover_file))
        return int(book_id), int(owner["id"]), int(stranger["id"])

    book_id, owner_id, stranger_id = asyncio.run(prepare())

    async def fake_auth(raw: str):
        if not raw:
            raise TMAAuthError("Откройте раздел из Telegram")
        if raw == "owner":
            return TMAUser(owner_id, 9803, "draft_owner", "Draft Owner")
        return TMAUser(stranger_id, 9804, "draft_stranger", "Draft Stranger")

    monkeypatch.setattr(webapp_module, "authenticate_init_data", fake_auth)

    with TestClient(webapp_module.create_app()) as client:
        public = client.get(f"/media/cover/{book_id}")
        assert public.status_code == 404

        anonymous = client.get(f"/api/author/book/{book_id}/cover")
        assert anonymous.status_code == 401

        foreign = client.get(
            f"/api/author/book/{book_id}/cover",
            headers={"X-Telegram-Init-Data": "stranger"},
        )
        assert foreign.status_code == 404

        own = client.get(
            f"/api/author/book/{book_id}/cover",
            headers={"X-Telegram-Init-Data": "owner"},
        )
        assert own.status_code == 200
        assert own.content == b"draft-cover-bytes"
        assert own.headers["content-type"].startswith("image/jpeg")
        assert "no-store" in own.headers.get("cache-control", "")


def test_author_dashboard_renders_saved_cover_instead_of_permanent_letter():
    author_js = Path("static/js/author.js").read_text(encoding="utf-8")
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    assert "data-author-cover-id" in author_js
    assert "loadAuthorCovers" in author_js
    assert '"/api/author/book/{book_id}/cover"' in webapp
