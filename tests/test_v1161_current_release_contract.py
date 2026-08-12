from __future__ import annotations

import inspect
import json
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

    expected = "v1.16.1"
    assert OWNER_BUILD_VERSION == expected
    assert settings.PROJECT_VERSION == expected
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert expected in readme
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"PROJECT_VERSION={expected}" in env_example
    manifest = json.loads((ROOT / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["project"] == "VoxLyra"
    assert manifest["version"] == expected
    assert manifest["verification"]["github_actions_targeted"] is True
    assert manifest["verification"]["github_actions_full_suite"] is True
    features = manifest["features"]
    assert features["github_hidden_system_owner_tools"] is True
    assert features["github_owner_callback_resilience"] is True
    assert features["github_single_inventory_bulk_discovery"] is True
    assert features["github_public_raw_file_downloads"] is True
    assert features["github_callback_safe_package_ids"] is True
    assert features["github_archive_disk_reserve"] is True
    assert features["github_retry_exact_failed_revision"] is True
    assert features["vk_failed_wall_post_retry"] is True
    assert features["vk_historical_post_spam_guard"] is True


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


def test_vk_wall_publication_is_wired_into_shared_first_publish_and_safe_retry_flow():
    from app.services import publication

    source = inspect.getsource(publication.publish_book_and_channel)
    assert "post_book_to_vk_wall" in source
    assert "should_retry_vk_wall_post" in source
    assert "retry_vk = published_before" in source
    assert "if not published_before or retry_vk" in source
    assert "publish_book_content" in source


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
    bot = (ROOT / "app" / "bot.py").read_text(encoding="utf-8")
    assert "is_system_owner" in service
    assert "import_library_zip" in service
    assert "restore_import_replacement_backups" in service
    assert "finalize_import_replacement_backups" in service
    assert "manifest_json" in service
    assert "_diff_manifest" in service
    assert "ContextVar" in service
    assert "_DISCOVERY_CONTEXT" in service
    assert "raw.githubusercontent.com" in service
    assert "_require_archive_space" in service
    assert "allow_update=True" in service
    assert "Пакет изменился после неудачной попытки" in service
    assert "owner:system:diagnostics" in handler
    assert "owner:github_import" in handler
    assert 'Command("github_import")' in handler
    assert "F.from_user.id == settings.SYSTEM_OWNER_ID" in handler
    assert "ghimp:all" in handler
    assert "ghimp:retry" in handler
    assert "changes" in handler
    assert bot.index("dp.include_router(github_import.router)") < bot.index("dp.include_router(owner.router)")


def test_import_replacement_keeps_permanent_book_id_and_relationship_tables():
    from app.services import library_manager

    replace_source = inspect.getsource(library_manager._replace_book_from_candidate)
    restore_source = inspect.getsource(library_manager.restore_import_replacement_backups)
    restore_row_source = inspect.getsource(library_manager._restore_table_row)
    assert "UPDATE books" in replace_source
    assert "WHERE id=?" in replace_source
    assert "DELETE FROM books" not in replace_source
    assert "DELETE FROM books" not in restore_source
    for protected_table in ("purchases", "reading_progress", "bookmarks", "reviews"):
        assert f"DELETE FROM {protected_table}" not in replace_source
        assert f"DELETE FROM {protected_table}" not in restore_source
    assert "_restore_table_row(db, \"books\", book)" in restore_source
    assert "UPDATE {table}" in restore_row_source
    assert "WHERE {primary_key}=?" in restore_row_source
