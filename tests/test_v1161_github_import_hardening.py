from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import github_import as gi


def _package(package_id: str = "pkg-1") -> gi.GitHubPackage:
    return gi.GitHubPackage(
        package_id=package_id,
        content_type="book",
        title="Test Book",
        language="ru",
        version="1.0",
        created_at="2026-08-12T00:00:00Z",
        files=("metadata.json",),
        checksums={"metadata.json": "0" * 64},
        path=f"books/{package_id}",
        commit_sha="a" * 40,
        status="new",
    )


def test_cleanup_package_is_idempotent(tmp_path):
    target = tmp_path / "work"
    target.mkdir()
    (target / "x.txt").write_text("x", encoding="utf-8")
    gi.cleanup_package(target)
    gi.cleanup_package(target)
    assert not target.exists()


def test_manifest_diff_reports_added_removed_and_changed_files():
    package = _package("diff")
    package.version = "1.1"
    package.commit_sha = "b" * 40
    package.files = ("metadata.json", "Chapters/012/page.webp")
    package.checksums = {
        "metadata.json": "1" * 64,
        "Chapters/012/page.webp": "2" * 64,
    }
    previous = json.dumps(
        {
            "version": "1.0",
            "commit_sha": "a" * 40,
            "files": ["metadata.json", "cover.jpg"],
            "checksums": {"metadata.json": "0" * 64, "cover.jpg": "3" * 64},
        }
    )
    changes = gi._diff_manifest(previous, package)
    assert "+ Chapters/012/page.webp" in changes
    assert "- cover.jpg" in changes
    assert "~ metadata.json" in changes


def test_manifest_diff_handles_history_created_before_snapshot_support():
    package = _package("legacy-history")
    changes = gi._diff_manifest("{}", package)
    # Old rows had no snapshot, therefore every currently declared file is
    # conservatively shown as new rather than pretending an exact old diff.
    assert changes == ("+ metadata.json",)


@pytest.mark.asyncio
async def test_download_rejects_low_disk_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(gi.settings, "GITHUB_IMPORT_TEMP_ROOT", str(tmp_path / "github"))
    monkeypatch.setattr(gi.settings, "GITHUB_IMPORT_MIN_FREE_DISK_MB", 256)
    monkeypatch.setattr(gi.shutil, "disk_usage", lambda _: SimpleNamespace(total=1, used=1, free=0))
    with pytest.raises(gi.GitHubImportError, match="Недостаточно свободного места"):
        await gi.download_package(42, _package())
    assert not (tmp_path / "github").exists()


@pytest.mark.asyncio
async def test_missing_remote_file_removes_partial_package(monkeypatch, tmp_path):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(gi.settings, "GITHUB_IMPORT_TEMP_ROOT", str(tmp_path / "github"))
    monkeypatch.setattr(gi.settings, "GITHUB_IMPORT_MIN_FREE_DISK_MB", 0)

    async def missing(*args, **kwargs):
        return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(gi, "_get_json", missing)
    monkeypatch.setattr(gi.httpx, "AsyncClient", FakeClient)

    package = _package("missing")
    with pytest.raises(gi.GitHubImportError, match="Отсутствует файл: metadata.json"):
        await gi.download_package(42, package)
    root = tmp_path / "github"
    assert not root.exists() or not any(root.iterdir())


@pytest.mark.asyncio
async def test_interrupted_stream_removes_partial_file_and_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(gi.settings, "GITHUB_IMPORT_TEMP_ROOT", str(tmp_path / "github"))
    monkeypatch.setattr(gi.settings, "GITHUB_IMPORT_MIN_FREE_DISK_MB", 0)

    async def remote_meta(*args, **kwargs):
        return {"type": "file", "download_url": "https://download.invalid/metadata.json"}

    class InterruptedResponse:
        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size):
            yield b"partial-data"
            raise RuntimeError("network connection interrupted")

    class StreamContext:
        async def __aenter__(self):
            return InterruptedResponse()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return StreamContext()

    monkeypatch.setattr(gi, "_get_json", remote_meta)
    monkeypatch.setattr(gi.httpx, "AsyncClient", FakeClient)

    package = _package("interrupted")
    with pytest.raises(RuntimeError, match="interrupted"):
        await gi.download_package(42, package)
    root = tmp_path / "github"
    assert not root.exists() or not any(root.iterdir())


@pytest.mark.asyncio
async def test_update_requires_explicit_owner_confirmation(monkeypatch):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    package = _package()
    package.status = "update"
    package.current_version = "0.9"

    async def fake_find(*args, **kwargs):
        return package

    async def forbidden_download(*args, **kwargs):
        raise AssertionError("update must not download before explicit confirmation")

    monkeypatch.setattr(gi, "find_package", fake_find)
    monkeypatch.setattr(gi, "download_package", forbidden_download)
    result = await gi.import_package(42, package.package_id, allow_update=False)
    assert result["status"] == "update_available"
    assert result["package"].current_version == "0.9"


@pytest.mark.asyncio
async def test_failed_existing_import_restores_backup_and_cleans_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    package = _package("rollback")
    work = tmp_path / "work"
    work.mkdir()
    (work / "metadata.json").write_text("{}", encoding="utf-8")

    async def fake_find(*args, **kwargs):
        return package

    async def fake_download(*args, **kwargs):
        return work

    async def fake_import(*args, **kwargs):
        return SimpleNamespace(
            batch_id=77,
            errors=[SimpleNamespace(reasons=["broken import"])],
            book_ids=[],
            added=0,
            replaced=0,
            duplicates=0,
        )

    restored = []
    finalized = []
    recorded = []

    async def fake_restore(batch_id):
        restored.append(batch_id)

    async def fake_finalize(batch_id):
        finalized.append(batch_id)

    async def fake_record(pkg, **kwargs):
        recorded.append(kwargs)

    import app.services.library_manager as library_manager
    monkeypatch.setattr(gi, "find_package", fake_find)
    monkeypatch.setattr(gi, "download_package", fake_download)
    monkeypatch.setattr(gi, "record_import", fake_record)
    monkeypatch.setattr(library_manager, "import_library_zip", fake_import)
    monkeypatch.setattr(library_manager, "restore_import_replacement_backups", fake_restore)
    monkeypatch.setattr(library_manager, "finalize_import_replacement_backups", fake_finalize)

    with pytest.raises(gi.GitHubImportError, match="broken import"):
        await gi.import_package(42, package.package_id)

    assert restored == [77]
    assert finalized == []
    assert recorded and recorded[-1]["status"] == "failed"
    assert not work.exists()
    assert not list(tmp_path.glob("*.voxlyra.zip"))


@pytest.mark.asyncio
async def test_success_finalizes_backup_records_id_and_cleans_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    package = _package("success")
    work = tmp_path / "work-success"
    work.mkdir()
    (work / "metadata.json").write_text("{}", encoding="utf-8")

    async def fake_find(*args, **kwargs):
        return package

    async def fake_download(*args, **kwargs):
        return work

    async def fake_import(*args, **kwargs):
        return SimpleNamespace(
            batch_id=88,
            errors=[],
            book_ids=[321],
            added=0,
            replaced=1,
            duplicates=0,
        )

    finalized = []
    restored = []
    recorded = []

    async def fake_finalize(batch_id):
        finalized.append(batch_id)

    async def fake_restore(batch_id):
        restored.append(batch_id)

    async def fake_record(pkg, **kwargs):
        recorded.append(kwargs)

    import app.services.library_manager as library_manager
    monkeypatch.setattr(gi, "find_package", fake_find)
    monkeypatch.setattr(gi, "download_package", fake_download)
    monkeypatch.setattr(gi, "record_import", fake_record)
    monkeypatch.setattr(library_manager, "import_library_zip", fake_import)
    monkeypatch.setattr(library_manager, "restore_import_replacement_backups", fake_restore)
    monkeypatch.setattr(library_manager, "finalize_import_replacement_backups", fake_finalize)

    result = await gi.import_package(42, package.package_id)
    assert result["status"] == "success"
    assert result["book_ids"] == [321]
    assert result["replaced"] == 1
    assert finalized == [88]
    assert restored == []
    assert recorded and recorded[-1]["status"] == "success"
    assert recorded[-1]["book_id"] == 321
    assert not work.exists()
    assert not list(tmp_path.glob("*.voxlyra.zip"))
