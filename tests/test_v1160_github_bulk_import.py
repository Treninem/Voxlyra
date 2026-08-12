import pytest

from app.services import github_import as gi


@pytest.mark.asyncio
async def test_bulk_imports_only_new_books_and_comics(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    packages = [
        gi.GitHubPackage("b1", "book", "Book", "ru", "1", "now", (), {}, "books/b1", "a"*40, "new"),
        gi.GitHubPackage("c1", "comics", "Comic", "ru", "1", "now", (), {}, "comics/c1", "a"*40, "new"),
        gi.GitHubPackage("a1", "audiobook", "Audio", "ru", "1", "now", (), {}, "audiobooks/a1", "a"*40, "new"),
        gi.GitHubPackage("u1", "book", "Update", "ru", "2", "now", (), {}, "books/u1", "a"*40, "update", "1"),
    ]
    async def discover(*args, **kwargs): return {"items": packages, "page": 1, "page_size": 100, "total": 4}
    calls=[]
    async def do_import(identity_id, pid, **kwargs): calls.append(pid); return {"status":"success"}
    monkeypatch.setattr(gi, "discover_packages", discover)
    monkeypatch.setattr(gi, "import_package", do_import)
    result=await gi.import_all_new(42)
    assert calls == ["b1", "c1"]
    assert result["success"] == 2
    assert result["updates"] == ["u1"]
    assert result["audio_skipped"] == ["a1"]


@pytest.mark.asyncio
async def test_bulk_import_continues_after_one_failure(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    packages=[gi.GitHubPackage(x,"book",x,"ru","1","now",(),{},f"books/{x}","a"*40,"new") for x in ("one","two")]
    async def discover(*args, **kwargs): return {"items":packages,"page":1,"page_size":100,"total":2}
    async def do_import(identity_id,pid,**kwargs):
        if pid=="one": raise gi.GitHubImportError("broken")
        return {"status":"success"}
    monkeypatch.setattr(gi,"discover_packages",discover); monkeypatch.setattr(gi,"import_package",do_import)
    result=await gi.import_all_new(42)
    assert result["failed"]==1 and result["success"]==1
    assert result["errors"][0]["package_id"]=="one"


@pytest.mark.asyncio
async def test_non_owner_cannot_bulk_import(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    with pytest.raises(gi.GitHubImportForbidden):
        await gi.import_all_new(43)
