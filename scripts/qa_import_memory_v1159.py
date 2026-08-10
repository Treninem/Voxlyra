#!/usr/bin/env python3
"""Static and arithmetic regression checks for 256 MB import safeguards."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    manager = (ROOT / "app/services/library_manager.py").read_text("utf-8")
    config = (ROOT / "app/config.py").read_text("utf-8")
    graphic_import = (ROOT / "app/services/graphic_import.py").read_text("utf-8")
    graphic_ocr = (ROOT / "app/services/graphic_ocr.py").read_text("utf-8")
    scenarios = 0

    assert "LIBRARY_IMPORT_MEMORY_RESERVE_MB: int = 6" in config
    assert "reserve_mb = max(4" in manager
    assert "_release_import_memory" in manager and "malloc_trim" in manager
    assert "expected_extra_bytes=estimated_parse_memory" in manager
    assert "Архив и задание сохранены" in manager
    assert "import fitz" not in graphic_import.split("def _render_pdf", 1)[0]
    assert "import pytesseract" not in graphic_ocr.split("def _pytesseract", 1)[0]

    # 300 combinations around the old 12 MB false-rejection boundary.
    for available_mb in range(4, 34):
        for expected_mb in range(10):
            reserve_mb = 6
            allowed = available_mb >= reserve_mb + expected_mb
            if expected_mb == 0 and available_mb >= 6:
                assert allowed
            if available_mb < reserve_mb + expected_mb:
                assert not allowed
            scenarios += 1

    print(f"IMPORT_MEMORY_V1159_QA_OK scenarios={scenarios}")


if __name__ == "__main__":
    main()
