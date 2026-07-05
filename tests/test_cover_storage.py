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
