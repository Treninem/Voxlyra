from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_library_profile_uses_independent_icon_and_cache_version():
    html = (ROOT / "templates/library.html").read_text(encoding="utf-8")
    assert "icons/profile.webp?v={{ asset_version }}" in html
    assert "id=\"libraryProfileIcon\"" in html
    assert "id=\"libraryProfileName\"" in html
    assert "voxlyra-mark.webp\" alt=\"Вокслира\" id=\"libraryProfileFallback" not in html

def test_library_profile_has_image_error_fallback():
    js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    assert "showInitialFallback" in js
    assert "profileIcon.addEventListener('error'" in js
    assert "profileHeading.textContent = `Моё" not in js

def test_mobile_library_layout_override_exists():
    css = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
    assert "VoxLyra v1.11.5 · исправление профиля" in css
    assert ".library-illustrated-hero" in css
    assert "grid-template-columns: 66px minmax(0, 1fr)" in css
