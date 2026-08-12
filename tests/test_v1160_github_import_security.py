import hashlib

import pytest

from app.services.github_import import (
    GitHubImportError,
    GitHubImportForbidden,
    require_system_owner,
    validate_manifest,
)


def manifest(**overrides):
    payload = {
        "package_id": "000001",
        "content_type": "book",
        "title": "Test",
        "language": "ru",
        "version": "1.0",
        "files": ["metadata.json", "book.epub"],
        "checksums": {
            "metadata.json": hashlib.sha256(b"meta").hexdigest(),
            "book.epub": hashlib.sha256(b"book").hexdigest(),
        },
        "created_at": "2026-08-12T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_only_system_owner_has_github_import_access(monkeypatch):
    from app.services import github_import as gi
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    require_system_owner(42)
    with pytest.raises(GitHubImportForbidden):
        require_system_owner(43)


def test_admin_style_owner_list_does_not_grant_github_import(monkeypatch):
    from app.services import github_import as gi
    monkeypatch.setattr(gi.settings, "SYSTEM_OWNER_ID", 42)
    monkeypatch.setattr(gi.settings, "OWNER_IDS", "43,44")
    with pytest.raises(GitHubImportForbidden):
        require_system_owner(43)


def test_valid_manifest():
    package = validate_manifest(manifest(), package_path="books/000001", commit_sha="a" * 40)
    assert package.package_id == "000001"
    assert package.content_type == "book"


def test_manifest_requires_fields():
    data = manifest()
    del data["checksums"]
    with pytest.raises(GitHubImportError):
        validate_manifest(data, package_path="books/000001", commit_sha="a" * 40)


def test_manifest_rejects_missing_checksum():
    data = manifest(checksums={"metadata.json": "a" * 64})
    with pytest.raises(GitHubImportError):
        validate_manifest(data, package_path="books/000001", commit_sha="a" * 40)


def test_manifest_rejects_path_traversal():
    data = manifest(files=["../secret", "book.epub"], checksums={"../secret": "a" * 64, "book.epub": "b" * 64})
    with pytest.raises(GitHubImportError):
        validate_manifest(data, package_path="books/000001", commit_sha="a" * 40)


def test_supported_content_types():
    for kind in ("book", "comics", "audiobook"):
        package = validate_manifest(manifest(content_type=kind), package_path="x", commit_sha="a" * 40)
        assert package.content_type == kind
