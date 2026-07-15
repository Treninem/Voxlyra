from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_author_hero_has_dedicated_copy_container():
    html = (ROOT / "templates" / "author.html").read_text(encoding="utf-8")
    assert 'class="author-hero-copy"' in html


def test_author_mobile_layout_prevents_overlap_and_clipping():
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "VoxLyra v1.11.6 · полноценная мобильная сетка кабинета автора" in css
    assert ".author-studio-page .author-illustrated-hero" in css
    assert "position: static !important" in css
    assert "padding-bottom: calc(112px + env(safe-area-inset-bottom))" in css
    assert ".author-studio-page .analytics-row" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr)) !important" in css


def test_static_cache_version_is_at_least_v1116():
    build = (ROOT / "app" / "build_info.py").read_text(encoding="utf-8")
    assert 'OWNER_BUILD_VERSION = "v1.11.8"' in build
