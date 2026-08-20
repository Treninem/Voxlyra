from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_bottom_navigation_has_four_primary_destinations():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    nav = re.search(r'<nav class="bottom-nav".*?</nav>', base, flags=re.S)
    assert nav is not None
    markup = nav.group(0)
    assert markup.count('data-nav="') == 4
    for code in ("home", "books", "library", "settings"):
        assert f'data-nav="{code}"' in markup
    assert 'data-nav="comics"' not in markup
    assert 'data-nav="audio"' not in markup
    assert "miniapp-navigation.js" in base


def test_home_keeps_only_three_format_shortcuts_and_one_catalog_flow():
    catalog = (ROOT / "templates" / "catalog.html").read_text(encoding="utf-8")
    assert "home-illustrated-hero" in catalog
    assert "story-portals" in catalog
    assert len(re.findall(r'<a class="story-portal ', catalog)) == 3
    assert "Что будем читать?" in catalog
    assert 'id="all-books"' in catalog
    assert 'id="catalogSearch"' in catalog
    assert 'data-catalog-filter="graphic"' in catalog
    # Repeated showcase rows were removed; the same content remains reachable by
    # the three format shortcuts and the catalogue filters.
    assert "Сейчас читают" not in catalog
    assert "Бесплатные главы" not in catalog
    assert "С аудиоверсией</h2>" not in catalog


def test_library_prioritizes_continue_saved_history_and_purchases():
    library = (ROOT / "templates" / "library.html").read_text(encoding="utf-8")
    panel = library.index('id="libraryPage"')
    wallet = library.index('id="walletEntry"')
    achievements = library.index('id="libraryAchievementPanel"')
    assert panel < wallet < achievements
    for code in ("continue", "saved", "history", "purchases"):
        assert f'data-library-tab="{code}"' in library
    assert 'id="libraryMoreToggle"' in library
    assert 'id="librarySecondaryTabs"' in library
    for code in ("activity", "journal", "shelves", "notes", "subscriptions"):
        assert f'data-library-tab="{code}"' in library
    assert 'href="/settings"' in library


def test_compact_navigation_script_preserves_features_behind_progressive_disclosure():
    js = (ROOT / "static" / "js" / "miniapp-navigation.js").read_text(encoding="utf-8")
    for marker in (
        "initLibraryNavigation",
        "libraryMoreToggle",
        "librarySecondaryTabs",
        "initSettingsAccordion",
        "settings-group-title",
        "aria-expanded",
        "sectionForHash",
    ):
        assert marker in js
    assert "CSS.escape" not in js
    assert "\\p{L}" not in js


def test_hotfix_layer_enforces_four_column_nav_and_accordion_layout():
    css = (ROOT / "static" / "css" / "hotfix-v1117.css").read_text(encoding="utf-8")
    for marker in (
        "repeat(4, minmax(0, 1fr))",
        ".route-nav-home",
        ".story-portals-compact",
        ".library-tabs-simplified",
        ".library-more-toggle",
        ".settings-accordion-title",
    ):
        assert marker in css
