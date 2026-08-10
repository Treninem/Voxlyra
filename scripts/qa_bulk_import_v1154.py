"""600 deterministic archive-structure tests for Books/Comics bulk import."""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from app.services.library_manager import _inspect_comic_archive, _inspect_library_archive


def make_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def main() -> None:
    max_unpacked = 64 * 1024 * 1024
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        checked = 0
        # 300 valid variants: prefixes, numeric names, mixed Books+Comics,
        # flat chapters and volume layouts are all routed correctly.
        for number in range(300):
            prefix = "" if number % 3 == 0 else ("Library/" if number % 3 == 1 else "Export/Current/")
            work = f"{number + 1:03d}"
            members = {
                f"{prefix}Comics/{work}/metadata.json": b"{}",
                f"{prefix}Comics/{work}/cover.jpg": b"cover",
                f"{prefix}Comics/{work}/Chapters/001/001.jpg": b"page",
            }
            if number % 2:
                members[f"{prefix}Books/{work}/metadata.json"] = b"{}"
                members[f"{prefix}Books/{work}/book.txt"] = b"text"
            path = root / f"valid-{number}.zip"
            make_zip(path, members)
            comics = _inspect_comic_archive(path, max_unpacked)
            books = _inspect_library_archive(path, max_unpacked)
            assert len(comics) == 1 and comics[0]["name"] == work
            assert len(books) == (1 if number % 2 else 0)
            checked += 1

        # 300 invalid/adversarial variants: traversal must always be rejected,
        # regardless of slash style, nesting or plausible neighboring files.
        attacks = ["../escape", "Comics/../../escape", "/absolute", "Books/001/../../../escape"]
        for number in range(300):
            attack = attacks[number % len(attacks)]
            path = root / f"invalid-{number}.zip"
            make_zip(path, {attack: b"bad", f"Comics/{number:03d}/metadata.json": b"{}"})
            rejected = False
            try:
                _inspect_comic_archive(path, max_unpacked)
            except ValueError:
                rejected = True
            assert rejected
            checked += 1
    assert checked == 600
    print("OK: 600 bulk-import archive scenarios")


if __name__ == "__main__":
    main()
