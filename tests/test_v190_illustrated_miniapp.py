from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "static" / "img" / "miniapp"


def test_illustrated_assets_are_local_optimized_webp_files():
    expected = {
        "scene-library.webp",
        "scene-audio.webp",
        "scene-reading.webp",
        "scene-stories.webp",
        "voxlyra-mark.webp",
        "voxlyra-v.webp",
    }
    assert expected.issubset({path.name for path in ASSET_DIR.glob("*.webp")})
    total = 0
    for name in expected:
        data = (ASSET_DIR / name).read_bytes()
        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WEBP"
        assert 10_000 < len(data) < 250_000
        total += len(data)
    assert total < 650_000


def test_main_sections_use_the_new_illustrations():
    templates = {
        "catalog.html": ["home-illustrated-hero", "story-portals", "voxlyra-mark.webp"],
        "audio.html": ["audio-illustrated-hero", "voxlyra-v.webp"],
        "library.html": ["library-illustrated-hero", "voxlyra-mark.webp"],
        "settings.html": ["settings-illustrated-hero", "voxlyra-v.webp"],
        "author.html": ["author-illustrated-hero", "voxlyra-v.webp"],
    }
    for name, markers in templates.items():
        text = (ROOT / "templates" / name).read_text(encoding="utf-8")
        for marker in markers:
            assert marker in text


def test_css_maps_each_scene_and_keeps_mobile_layout():
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    for marker in (
        "scene-library.webp",
        "scene-audio.webp",
        "scene-reading.webp",
        "scene-stories.webp",
        ".story-portals",
        ".home-illustrated-hero",
        "@media (max-width: 560px)",
    ):
        assert marker in css
