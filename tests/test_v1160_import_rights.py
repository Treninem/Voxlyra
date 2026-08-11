from pathlib import Path

from app.services.library_manager import _validate_import_rights


def _evidence(folder: Path) -> None:
    (folder / "LICENSE.txt").write_text("licence evidence", encoding="utf-8")
    (folder / "SOURCES.txt").write_text("https://example.invalid/source", encoding="utf-8")


def test_cc_by_with_commercial_derivatives_is_allowed(tmp_path: Path) -> None:
    _evidence(tmp_path)
    metadata = {
        "license": "creative_commons",
        "license_code": "CC-BY-4.0",
        "rights_checked": True,
        "commercial_use": True,
        "derivatives_allowed": True,
        "source": "https://example.invalid/source",
    }
    assert _validate_import_rights(metadata, tmp_path) == []


def test_nc_and_nd_licenses_are_rejected(tmp_path: Path) -> None:
    _evidence(tmp_path)
    for code in ("CC-BY-NC-4.0", "CC-BY-ND-4.0", "CC-BY-NC-SA-4.0"):
        metadata = {
            "license": "creative_commons",
            "license_code": code,
            "rights_checked": True,
            "commercial_use": True,
            "derivatives_allowed": True,
            "source": "https://example.invalid/source",
        }
        assert any("NC и ND запрещены" in reason for reason in _validate_import_rights(metadata, tmp_path))


def test_external_work_requires_evidence_files_and_source(tmp_path: Path) -> None:
    metadata = {"license": "public_domain", "rights_checked": True}
    reasons = _validate_import_rights(metadata, tmp_path)
    assert any("source" in reason for reason in reasons)
    assert any("LICENSE.txt" in reason for reason in reasons)
    assert any("SOURCES.txt" in reason for reason in reasons)


def test_author_permission_requires_reference(tmp_path: Path) -> None:
    _evidence(tmp_path)
    metadata = {
        "license": "author_permission",
        "rights_checked": True,
        "source": "https://example.invalid/source",
    }
    assert any("permission_reference" in reason for reason in _validate_import_rights(metadata, tmp_path))
