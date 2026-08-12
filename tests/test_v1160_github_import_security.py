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


def test_manifest_rejects_extra_checksum_entries():
    data = manifest()
    data["checksums"]["unused.bin"] = "a" * 64
    with pytest.raises(GitHubImportError, match="точно соответствовать"):
        validate_manifest(data, package_path="books/000001", commit_sha="a" * 40)


def test_manifest_rejects_path_traversal():
    data = manifest(files=["../secret", "book.epub"], checksums={"../secret": "a" * 64, "book.epub": "b" * 64})
    with pytest.raises(GitHubImportError):
        validate_manifest(data, package_path="books/000001", commit_sha="a" * 40)


def test_supported_content_types():
    for kind in ("book", "comics", "audiobook"):
        package = validate_manifest(manifest(content_type=kind), package_path="x", commit_sha="a" * 40)
        assert package.content_type == kind


def test_package_id_fits_longest_telegram_owner_callback():
    package_id = "x" * 51
    package = validate_manifest(
        manifest(package_id=package_id),
        package_path=f"books/{package_id}",
        commit_sha="a" * 40,
    )
    assert len(f"ghimp:update:{package.package_id}".encode("utf-8")) == 64
    with pytest.raises(GitHubImportError, match="package_id"):
        validate_manifest(
            manifest(package_id="x" * 52),
            package_path="books/too-long",
            commit_sha="a" * 40,
        )


def test_manifest_rejects_oversized_file_inventory():
    files = [f"pages/{index:05d}.webp" for index in range(20_001)]
    checksums = {name: "a" * 64 for name in files}
    with pytest.raises(GitHubImportError, match="слишком много файлов"):
        validate_manifest(
            manifest(files=files, checksums=checksums),
            package_path="books/000001",
            commit_sha="a" * 40,
        )


def test_manifest_rejects_empty_or_unbounded_version():
    for version in ("", "v" * 129):
        with pytest.raises(GitHubImportError, match="версия"):
            validate_manifest(
                manifest(version=version),
                package_path="books/000001",
                commit_sha="a" * 40,
            )
