"""Pure deterministic regression checks for comic auto-structure and controls."""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Page:
    number: int
    source_filename: str


class Settings:
    MAX_COMIC_PAGES = 500


class GraphicImportError(RuntimeError):
    pass


def load_splitter():
    source = (ROOT / "app/webapp.py").read_text("utf-8")
    tree = ast.parse(source)
    body = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") in {"_GRAPHIC_VOLUME_RE", "_GRAPHIC_CHAPTER_RE"} for target in node.targets):
            body.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "_split_graphic_work_pages":
            body.append(node)
    namespace = {
        "re": re, "replace": replace, "settings": Settings(),
        "PreparedGraphicPage": Page, "GraphicImportError": GraphicImportError,
    }
    exec(compile(ast.Module(body=body, type_ignores=[]), "comic_splitter", "exec"), namespace)
    return namespace["_split_graphic_work_pages"]


def main() -> None:
    split = load_splitter()
    scenarios = 0
    for total in range(1, 201):
        pages = [Page(i, f"page-{i:04d}.jpg") for i in range(1, total + 1)]
        groups = split(pages, start_volume=1, pages_per_chapter=17, chapters_per_volume=4)
        assert sum(len(group["pages"]) for group in groups) == total
        assert all([page.number for page in group["pages"]] == list(range(1, len(group["pages"]) + 1)) for group in groups)
        assert all(len(group["pages"]) <= 17 for group in groups)
        scenarios += 1

    marked = [
        Page(1, "Том 2/Глава 7/001.jpg"), Page(2, "Том 2/Глава 7/002.jpg"),
        Page(3, "Том 2/Глава 8/001.jpg"), Page(4, "Том 3/Глава 9/001.jpg"),
    ]
    groups = split(marked, start_volume=1, pages_per_chapter=1, chapters_per_volume=1)
    assert [(g["volume_number"], g["chapter_number"], len(g["pages"])) for g in groups] == [(2, 7, 2), (2, 8, 1), (3, 9, 1)]
    scenarios += 1

    control = (ROOT / "static/js/control.js").read_text("utf-8")
    server = (ROOT / "app/webapp.py").read_text("utf-8")
    manager = (ROOT / "app/services/library_manager.py").read_text("utf-8")
    assert "Опубликовать комикс" in control
    assert "owner_draft_publish" in server
    assert "UPDATE graphic_chapters SET status='published'" in manager
    assert "graphic_pages_count" in manager
    scenarios += 4
    print(f"COMICS_V1156_QA_OK scenarios={scenarios}")


if __name__ == "__main__":
    main()
