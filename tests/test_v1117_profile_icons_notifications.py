from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_unique_hotfix_stylesheet_is_loaded_after_main_css():
    html = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    main_pos = html.index("/static/css/style.css?v={{ asset_version }}")
    hotfix_pos = html.index("/static/css/hotfix-v1117.css?v={{ asset_version }}")
    assert hotfix_pos > main_pos
    assert (ROOT / "static" / "css" / "hotfix-v1117.css").is_file()


def test_library_header_uses_separate_title_row_and_non_overlapping_grid():
    html = (ROOT / "templates" / "library.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "hotfix-v1117.css").read_text(encoding="utf-8")
    assert 'class="library-title-row"' in html
    assert "grid-template-columns: 68px minmax(0, 1fr) !important" in css
    assert "position: relative !important" in css
    assert ".library-profile-name" in css
    assert "display: block !important" in css


def test_all_icon_templates_use_current_asset_version_and_assets_are_packaged():
    for path in (ROOT / "templates").glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for part in text.split('src="')[1:]:
            src = part.split('"', 1)[0]
            if "/static/img/miniapp/icons/" in src:
                assert "?v={{ asset_version }}" in src, (path.name, src)
    icon_dir = ROOT / "static" / "img" / "miniapp" / "icons"
    assert len(list(icon_dir.glob("*.webp"))) >= 20


def test_telegram_avatar_is_returned_and_used_with_fallback():
    auth = (ROOT / "app" / "services" / "tma_auth.py").read_text(encoding="utf-8")
    webapp = (ROOT / "app" / "webapp.py").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "photo_url: str | None = None" in auth
    assert '"photo_url": user.photo_url' in webapp
    assert "telegramPhotoUrl" in js
    assert "showInitialFallback" in js


def test_manual_notifications_request_html_parse_mode():
    notifications = (ROOT / "app" / "services" / "notifications.py").read_text(encoding="utf-8")
    webapp = (ROOT / "app" / "webapp.py").read_text(encoding="utf-8")
    assert "parse_mode: ParseMode | str | None = None" in notifications
    assert 'kwargs["parse_mode"] = parse_mode' in notifications
    assert webapp.count("parse_mode=ParseMode.HTML") >= 3


def test_repeated_daily_bonus_tap_does_not_raise_message_not_modified():
    start = (ROOT / "app" / "handlers" / "start.py").read_text(encoding="utf-8")
    assert "TelegramBadRequest" in start
    assert '"message is not modified"' in start
    assert "await _safe_edit_text(call.message" in start


def test_unfinished_bonus_button_is_hidden_from_main_menu():
    keyboards = (ROOT / "app" / "keyboards.py").read_text(encoding="utf-8")
    main_menu_body = keyboards.split("def main_menu", 1)[1].split("def more_menu", 1)[0]
    assert 'callback_data="main:bonuses"' not in main_menu_body


def test_root_favicon_route_and_file_exist():
    webapp = (ROOT / "app" / "webapp.py").read_text(encoding="utf-8")
    assert '@app.get("/favicon.ico"' in webapp
    assert (ROOT / "static" / "favicon.ico").stat().st_size > 0


def test_build_version_bumped_to_v1117():
    build = (ROOT / "app" / "build_info.py").read_text(encoding="utf-8")
    assert 'OWNER_BUILD_VERSION = "v1.11.12"' in build
