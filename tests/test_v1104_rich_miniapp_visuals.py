from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "img" / "miniapp"
ICON_NAMES = {
    "home.webp",
    "reading.webp",
    "audio.webp",
    "comics.webp",
    "library.webp",
    "new.webp",
    "profile.webp",
    "author.webp",
    "bookmark.webp",
    "gift.webp",
    "search.webp",
    "comments.webp",
    "rating.webp",
    "coins.webp",
    "premium.webp",
    "settings.webp",
    "control.webp",
    "create-book.webp",
    "create-comic.webp",
    "moderator.webp",
}


def test_v1104_visual_assets_are_local_webp_and_present():
    root_assets = {"hero-main.webp", "menu-background.webp", "splash-v.webp"}
    empty = {
        "no-books.webp",
        "no-bookmarks.webp",
        "history-empty.webp",
        "nothing-found.webp",
        "chapter-loading.webp",
        "moderation.webp",
    }
    sections = {
        "reading.webp",
        "search.webp",
        "bookmarks.webp",
        "library.webp",
        "audio-stories.webp",
        "comics.webp",
        "universe.webp",
    }

    for name in root_assets:
        assert (ASSETS / name).is_file()
    assert ICON_NAMES == {path.name for path in (ASSETS / "icons").glob("*.webp")}
    assert empty == {path.name for path in (ASSETS / "empty").glob("*.webp")}
    assert sections == {path.name for path in (ASSETS / "sections").glob("*.webp")}

    paths = [
        *(ASSETS / name for name in root_assets),
        *(ASSETS / "icons").glob("*.webp"),
        *(ASSETS / "empty").glob("*.webp"),
        *(ASSETS / "sections").glob("*.webp"),
    ]
    for path in paths:
        data = path.read_bytes()
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WEBP"
        assert len(data) < 180_000


def test_v1104_independent_icons_have_equal_canvas_and_safe_margins():
    for path in sorted((ASSETS / "icons").glob("*.webp")):
        with Image.open(path) as image:
            assert image.size == (512, 512), path.name
            assert image.mode == "RGBA", path.name
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            assert bbox is not None, path.name
            left, top, right, bottom = bbox
            # Every icon is a standalone composition with the same safe area.
            assert left >= 20 and top >= 20, (path.name, bbox)
            assert right <= 492 and bottom <= 492, (path.name, bbox)


def test_v1104_templates_integrate_splash_icons_and_empty_states():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'id="voxSplash"' in base
    assert "splash-v.webp" in base
    assert "icons/home.webp?v={{ asset_version }}" in base
    assert "icons/settings.webp?v={{ asset_version }}" in base

    catalog = (ROOT / "templates" / "catalog.html").read_text(encoding="utf-8")
    assert "hero-main" not in catalog  # mapped through CSS, not duplicated in markup
    assert "story-portal-icon" in catalog
    assert "empty_state('nothing-found'" in catalog

    library = (ROOT / "templates" / "library.html").read_text(encoding="utf-8")
    assert "profile-medallion" in library
    assert "chapter-loading" in library
    assert "icons/control.webp?v={{ asset_version }}" in library
    assert "control-center-entry" in library

    author = (ROOT / "templates" / "author.html").read_text(encoding="utf-8")
    assert 'id="moderationSuccess"' in author
    assert "empty/moderation.webp" in author
    assert "icons/create-book.webp?v={{ asset_version }}" in author
    assert "icons/create-comic.webp?v={{ asset_version }}" in author

    settings = (ROOT / "templates" / "settings.html").read_text(encoding="utf-8")
    assert "icons/moderator.webp?v={{ asset_version }}" in settings
    assert 'data-profile-frame="moderator"' in settings


def test_v1104_icon_rendering_preserves_full_canvas():
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert 'img[src*="/static/img/miniapp/icons/"]' in css
    for selector in (
        ".story-portal-icon",
        ".bottom-nav a img",
        ".search-box > img",
        ".author-studio-entry > img",
        ".author-create-choice img",
        ".profile-frame-preview img",
    ):
        start = css.index(selector)
        block = css[start:css.index("}", start) + 1]
        assert "object-fit: contain" in block, selector
        assert "object-position: center" in block, selector


def test_v1104_css_and_js_enable_motion_depth_frames_and_seasons():
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for marker in (
        ".vox-splash",
        ".app-ambient",
        "hero-main.webp",
        "menu-background.webp",
        ".illustrated-empty",
        "--book-cover-image",
        ".profile-frame-options",
        'data-season="new-year"',
        "prefers-reduced-motion",
    ):
        assert marker in css
    for marker in ("initVoxSplash", "profileFrame", "seasonalDecor", "emptyStateMarkup"):
        assert marker in js
