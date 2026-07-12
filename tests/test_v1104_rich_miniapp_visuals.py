from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "static" / "img" / "miniapp"


def test_v1104_visual_assets_are_local_webp_and_present():
    root_assets = {"hero-main.webp", "menu-background.webp", "splash-v.webp"}
    icons = {
        "home.webp", "reading.webp", "audio.webp", "comics.webp", "library.webp",
        "new.webp", "profile.webp", "author.webp", "bookmark.webp", "gift.webp",
        "search.webp", "comments.webp", "rating.webp", "premium.webp", "coins.webp", "settings.webp",
    }
    empty = {"no-books.webp", "no-bookmarks.webp", "history-empty.webp", "nothing-found.webp", "chapter-loading.webp", "moderation.webp"}
    sections = {"reading.webp", "search.webp", "bookmarks.webp", "library.webp", "audio-stories.webp", "comics.webp", "universe.webp"}

    for name in root_assets:
        assert (ASSETS / name).is_file()
    assert icons.issubset({path.name for path in (ASSETS / "icons").glob("*.webp")})
    assert empty == {path.name for path in (ASSETS / "empty").glob("*.webp")}
    assert sections == {path.name for path in (ASSETS / "sections").glob("*.webp")}

    for path in [*(ASSETS / name for name in root_assets), *(ASSETS / "icons").glob("*.webp"), *(ASSETS / "empty").glob("*.webp"), *(ASSETS / "sections").glob("*.webp")]:
        data = path.read_bytes()
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WEBP"
        assert len(data) < 180_000


def test_v1104_templates_integrate_splash_icons_and_empty_states():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'id="voxSplash"' in base
    assert "splash-v.webp" in base
    assert "icons/home.webp" in base
    assert "icons/settings.webp" in base

    catalog = (ROOT / "templates" / "catalog.html").read_text(encoding="utf-8")
    assert "hero-main" not in catalog  # mapped through CSS, not duplicated in markup
    assert "story-portal-icon" in catalog
    assert "empty_state('nothing-found'" in catalog

    library = (ROOT / "templates" / "library.html").read_text(encoding="utf-8")
    assert "profile-medallion" in library
    assert "chapter-loading" in library

    author = (ROOT / "templates" / "author.html").read_text(encoding="utf-8")
    assert 'id="moderationSuccess"' in author
    assert "empty/moderation.webp" in author


def test_v1104_css_and_js_enable_motion_depth_frames_and_seasons():
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for marker in (
        ".vox-splash", ".app-ambient", "hero-main.webp", "menu-background.webp",
        ".illustrated-empty", "--book-cover-image", ".profile-frame-options",
        'data-season="new-year"', "prefers-reduced-motion",
    ):
        assert marker in css
    for marker in ("initVoxSplash", "profileFrame", "seasonalDecor", "emptyStateMarkup"):
        assert marker in js
