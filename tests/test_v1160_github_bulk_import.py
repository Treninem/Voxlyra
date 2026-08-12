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
async def test_bulk_import_reuses_already_discovered_packages(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    packages = [
        gi.GitHubPackage(x, "book", x, "ru", "1", "now", (), {}, f"books/{x}", "a"*40, "new")
        for x in ("one", "two", "three")
    ]
    discovery_calls = 0

    async def discover(*args, **kwargs):
        nonlocal discovery_calls
        discovery_calls += 1
        return {"items": packages, "page": 1, "page_size": 100, "total": len(packages)}

    async def import_using_public_lookup(identity_id, package_id, **kwargs):
        resolved = await gi.find_package(identity_id, package_id)
        assert resolved.package_id == package_id
        return {"status": "success"}

    monkeypatch.setattr(gi, "discover_packages", discover)
    monkeypatch.setattr(gi, "import_package", import_using_public_lookup)

    result = await gi.import_all_new(42)
    assert result["success"] == 3
    assert discovery_calls == 1
    assert gi._RESOLVED_PACKAGES.get() is None
    assert gi._DISCOVERY_CONTEXT.get() is None


@pytest.mark.asyncio
async def test_bulk_max_packages_is_exact_not_page_sized(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    packages = [
        gi.GitHubPackage(x, "book", x, "ru", "1", "now", (), {}, f"books/{x}", "a"*40, "new")
        for x in ("one", "two", "three")
    ]
    calls = []

    async def discover(*args, **kwargs):
        return {"items": packages, "page": 1, "page_size": 100, "total": 3}

    async def do_import(identity_id, package_id, **kwargs):
        calls.append(package_id)
        return {"status": "success"}

    monkeypatch.setattr(gi, "discover_packages", discover)
    monkeypatch.setattr(gi, "import_package", do_import)

    result = await gi.import_all_new(42, max_packages=1)
    assert result["total"] == 1
    assert result["success"] == 1
    assert calls == ["one"]


@pytest.mark.asyncio
async def test_zero_bulk_limit_does_not_scan_or_import(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)

    async def forbidden(*args, **kwargs):
        raise AssertionError("zero limit must not touch GitHub")

    monkeypatch.setattr(gi, "discover_packages", forbidden)
    monkeypatch.setattr(gi, "import_package", forbidden)
    result = await gi.import_all_new(42, max_packages=0)
    assert result["total"] == 0
    assert result["success"] == 0


@pytest.mark.asyncio
async def test_non_owner_cannot_bulk_import(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    with pytest.raises(gi.GitHubImportForbidden):
        await gi.import_all_new(43)
