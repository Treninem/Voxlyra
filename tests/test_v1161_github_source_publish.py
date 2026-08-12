from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.services import github_source_publish as source


def _build_source_zip(tmp_path: Path, *, package_id: str = "sample-book") -> Path:
    payloads = {
        "metadata.json": json.dumps(
            {
                "title": "Sample",
                "author": "Owner",
                "license": "platform_original",
                "rights_checked": True,
                "rights_holder": "Owner",
                "rights_holder_type": "person",
                "file": "book.epub",
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        "description.txt": "Описание".encode("utf-8"),
        "LICENSE.txt": b"platform original provenance",
        "SOURCES.txt": b"owner source package",
        "book.epub": b"PK\x03\x04fake-epub-content",
    }
    checksums = {name: hashlib.sha256(raw).hexdigest() for name, raw in payloads.items()}
    manifest = {
        "package_id": package_id,
        "content_type": "book",
        "title": "Sample",
        "language": "ru",
        "version": "1.0",
        "created_at": "2026-08-13T00:00:00Z",
        "files": list(payloads),
        "checksums": checksums,
    }
    archive_path = tmp_path / "source-ready.zip"
    root = f"books/{package_id}"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{root}/manifest.json", json.dumps(manifest, ensure_ascii=False))
        for name, raw in payloads.items():
            archive.writestr(f"{root}/{name}", raw)
    return archive_path


def test_inspect_source_zip_verifies_manifest_hashes_and_real_payload(tmp_path):
    archive = _build_source_zip(tmp_path)
    package = source.inspect_source_package_zip(archive)
    assert package.package_id == "sample-book"
    assert package.package_path == "books/sample-book"
    assert package.content_type == "book"
    assert package.file_count == 5
    assert "book.epub" in package.members


def test_source_zip_rejects_manifest_folder_mismatch(tmp_path):
    archive = _build_source_zip(tmp_path, package_id="sample-book")
    rewritten = tmp_path / "mismatch.zip"
    with zipfile.ZipFile(archive) as incoming, zipfile.ZipFile(rewritten, "w") as outgoing:
        for info in incoming.infolist():
            raw = incoming.read(info.filename)
            if info.filename.endswith("manifest.json"):
                data = json.loads(raw.decode("utf-8"))
                data["package_id"] = "different-id"
                raw = json.dumps(data).encode("utf-8")
            outgoing.writestr(info.filename, raw)
    with pytest.raises(source.GitHubSourcePublishError, match="package_id"):
        source.inspect_source_package_zip(rewritten)


def test_source_zip_rejects_undeclared_file(tmp_path):
    archive = _build_source_zip(tmp_path)
    rewritten = tmp_path / "extra.zip"
    with zipfile.ZipFile(archive) as incoming, zipfile.ZipFile(rewritten, "w") as outgoing:
        for info in incoming.infolist():
            outgoing.writestr(info.filename, incoming.read(info.filename))
        outgoing.writestr("books/sample-book/stale.bin", b"stale")
    with pytest.raises(source.GitHubSourcePublishError, match="не совпадает"):
        source.inspect_source_package_zip(rewritten)


def test_import_index_switches_only_matching_package_to_enabled(tmp_path):
    package = source.inspect_source_package_zip(_build_source_zip(tmp_path))
    index = {
        "schema_version": 1,
        "packages": [
            {"path": "books/sample-book", "enabled": False, "reason": "pending"},
            {"path": "books/other", "manifest_path": "books/other/manifest.json", "enabled": False},
        ],
    }
    updated = source.build_enabled_import_index(index, package)
    assert index["packages"][0]["enabled"] is False
    assert updated["packages"][0] == {
        "path": "books/sample-book",
        "manifest_path": "books/sample-book/manifest.json",
        "enabled": True,
    }
    assert updated["packages"][1]["enabled"] is False


@pytest.mark.asyncio
async def test_publish_is_atomic_and_deletes_stale_package_files(monkeypatch, tmp_path):
    archive = _build_source_zip(tmp_path)
    monkeypatch.setattr(source.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(source.settings, "GITHUB_SOURCE_WRITE_ENABLED", True)
    monkeypatch.setattr(source.settings, "GITHUB_SOURCE_WRITE_TOKEN", "secret-write-token")
    monkeypatch.setattr(source.settings, "GITHUB_IMPORT_REPOSITORY", "Treninem/bookvoxlyra")
    monkeypatch.setattr(source.settings, "GITHUB_IMPORT_BRANCH", "main")
    monkeypatch.setattr(source.settings, "GITHUB_IMPORT_ROOT", "")

    base_commit = "a" * 40
    base_tree = "b" * 40
    new_tree = "c" * 40
    new_commit = "d" * 40
    calls: list[tuple[str, str, dict | None]] = []
    created_tree_entries = []

    async def fake_request(client, method, url, *, json_body=None, params=None):
        nonlocal created_tree_entries
        calls.append((method, url, json_body))
        if "/git/ref/heads/" in url:
            return {"object": {"sha": base_commit}}
        if f"/git/commits/{base_commit}" in url:
            return {"tree": {"sha": base_tree}}
        if f"/git/trees/{base_tree}" in url:
            return {
                "truncated": False,
                "tree": [
                    {"type": "blob", "path": "books/sample-book/old.bin"},
                    {"type": "blob", "path": "books/sample-book/metadata.json"},
                    {"type": "blob", "path": "books/other/keep.bin"},
                ],
            }
        if "/contents/manifests/import_index.json" in url:
            raw = json.dumps(
                {
                    "schema_version": 1,
                    "packages": [
                        {"path": "books/sample-book", "enabled": False, "reason": "pending"}
                    ],
                }
            ).encode("utf-8")
            import base64
            return {"encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}
        if url.endswith("/git/trees") and method == "POST":
            created_tree_entries = list(json_body["tree"])
            return {"sha": new_tree}
        if url.endswith("/git/commits") and method == "POST":
            assert json_body["parents"] == [base_commit]
            assert json_body["tree"] == new_tree
            return {"sha": new_commit}
        if "/git/refs/heads/" in url and method == "PATCH":
            assert json_body == {"sha": new_commit, "force": False}
            return {"object": {"sha": new_commit}}
        raise AssertionError((method, url, json_body, params))

    counter = 0

    async def fake_blob(client, api_base, raw):
        nonlocal counter
        counter += 1
        return f"{counter:040x}"[-40:]

    monkeypatch.setattr(source, "_request_json", fake_request)
    monkeypatch.setattr(source, "_create_blob", fake_blob)
    result = await source.publish_source_package_zip(42, archive)

    assert result["commit_sha"] == new_commit
    assert result["enabled"] is True
    assert any(item["path"] == "books/sample-book/old.bin" and item["sha"] is None for item in created_tree_entries)
    assert any(item["path"] == "manifests/import_index.json" and item["sha"] for item in created_tree_entries)
    assert sum(1 for method, url, _ in calls if method == "PATCH" and "/git/refs/heads/" in url) == 1


@pytest.mark.asyncio
async def test_source_write_is_non_delegable_and_disabled_by_default(monkeypatch, tmp_path):
    archive = _build_source_zip(tmp_path)
    monkeypatch.setattr(source.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(source.settings, "GITHUB_SOURCE_WRITE_ENABLED", False)
    monkeypatch.setattr(source.settings, "GITHUB_SOURCE_WRITE_TOKEN", "")
    with pytest.raises(Exception):
        await source.publish_source_package_zip(43, archive)
    with pytest.raises(source.GitHubSourcePublishError, match="выключена"):
        await source.publish_source_package_zip(42, archive)
