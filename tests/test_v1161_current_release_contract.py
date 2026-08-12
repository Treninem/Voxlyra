from __future__ import annotations

from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_current_build_version_and_readme_are_aligned():
    from app.build_info import OWNER_BUILD_VERSION
    from app.config import settings

    assert OWNER_BUILD_VERSION == "v1.16.1"
    assert settings.PROJECT_VERSION == "v1.16.1"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "v1.16.1" in readme


def test_canonical_avatar_assets_only_live_under_static():
    for name in ("bot_avatar.png", "channel_avatar.png"):
        path = ROOT / "static" / "img" / name
        assert path.is_file(), path
        width, height = _png_size(path)
        assert width == height
        assert width >= 512
    assert not (ROOT / "voxlyra_bot_avatar_final.png").exists()
    assert not (ROOT / "voxlyra_channel_avatar_final.png").exists()


def test_platform_launch_routes_are_current_and_not_old_literal_snapshots():
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "function voxStartParamForRoute" in app_js
    assert "function voxTelegramLaunchUrl" in app_js
    assert "?startapp=${encodeURIComponent(startParam)}" in app_js
    assert "function voxVKLaunchUrl" in app_js
    assert "tgWebAppStartParam" in app_js
    assert "unsafe.start_param" in app_js


def test_telegram_publication_and_notifications_use_mini_app_deep_links(monkeypatch):
    from app.config import settings
    from app.services import publication
    from app.services.notifications import book_open_markup

    monkeypatch.setattr(settings, "BOT_USERNAME", "VoxlyraBot")
    monkeypatch.setattr(settings, "WEBAPP_URL", "")
    assert publication._book_link(42) == "https://t.me/VoxlyraBot?startapp=book_42"
    markup = book_open_markup(42)
    assert markup is not None
    assert markup.inline_keyboard[0][0].url == "https://t.me/VoxlyraBot?startapp=book_42"


def test_cross_platform_commerce_contract_is_explicit():
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert "VKWebAppShowOrderBox" in app_js
    assert "VK_VOTES_PER_STAR" in config
    assert "VK_PAYMENT_SECRET" in config
    assert "LabeledPrice" in (ROOT / "app" / "webapp.py").read_text(encoding="utf-8")


def test_current_legal_model_names_platform_native_payments():
    from app.legal_texts import LEGAL_DOCS

    terms = LEGAL_DOCS["terms"]
    author = LEGAL_DOCS["author_license"]
    combined = f"{terms.title}\n{terms.body}\n{author.title}\n{author.body}"
    assert "Telegram Stars" in combined
    assert "голос" in combined.lower()
    assert "VK" in combined
    assert LEGAL_DOCS["fees_payouts"] is LEGAL_DOCS["author_license"]


def test_github_import_stays_owner_only_and_uses_existing_pipeline():
    service = (ROOT / "app" / "services" / "github_import.py").read_text(encoding="utf-8")
    handler = (ROOT / "app" / "handlers" / "github_import.py").read_text(encoding="utf-8")
    assert "is_system_owner" in service
    assert "import_library_zip" in service
    assert "restore_import_replacement_backups" in service
    assert "finalize_import_replacement_backups" in service
    assert "manifest_json" in service
    assert "_diff_manifest" in service
    assert "ghimp:all" in handler
    assert "ghimp:retry" in handler
    assert "changes" in handler
