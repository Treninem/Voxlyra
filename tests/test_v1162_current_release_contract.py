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


def test_current_build_version_and_release_metadata_are_aligned():
    from app.build_info import OWNER_BUILD_VERSION
    from app.config import settings

    expected = "v1.16.2"
    assert OWNER_BUILD_VERSION == expected
    assert settings.PROJECT_VERSION == expected
    assert expected in (ROOT / "README.md").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert f"PROJECT_VERSION={expected}" in env_example
    assert "GITHUB_SOURCE_WRITE_ENABLED=false" in env_example
    assert "GITHUB_SOURCE_WRITE_TOKEN=" in env_example
    manifest = json.loads((ROOT / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["project"] == "VoxLyra"
    assert manifest["version"] == expected
    assert manifest["verification"]["github_actions_targeted"] is True
    assert manifest["verification"]["github_actions_full_suite"] is True
    features = manifest["features"]
    for name in (
        "github_hidden_system_owner_tools",
        "github_owner_callback_resilience",
        "github_import_env_ui_kill_switch",
        "github_single_inventory_bulk_discovery",
        "github_public_raw_file_downloads",
        "github_strict_manifest_checksums",
        "github_manifest_rights_evidence_required",
        "github_manifest_content_payload_required",
        "github_callback_safe_package_ids",
        "github_archive_disk_reserve",
        "github_retry_exact_failed_revision",
        "github_same_package_serialization",
        "github_history_refresh_after_package_lock",
        "github_source_write_bridge",
        "github_source_write_separate_token",
        "github_source_write_atomic_commit",
        "github_source_write_owner_only",
        "github_source_write_zip_validation",
        "github_source_direct_upload",
        "github_source_upload_signed_token",
        "github_source_upload_resumable_chunks",
        "github_source_upload_telegram_limit_bypass",
        "vk_failed_wall_post_retry",
        "vk_historical_post_spam_guard",
        "vk_publication_single_canonical_service",
        "vk_publication_compatibility_shim_complete",
    ):
        assert features[name] is True, name


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


def test_vk_publication_uses_one_canonical_service_with_complete_compatibility_surface():
    from app.services import cross_platform_publication as canonical
    from app.services import vk_publication as compat

    for name in (
        "build_vk_book_post",
        "post_book_to_vk_wall",
        "should_retry_vk_wall_post",
        "vk_book_url",
        "vk_votes_from_stars",
    ):
        assert getattr(compat, name) is getattr(canonical, name)


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


def test_github_import_and_source_publish_stay_owner_only_and_use_existing_pipeline():
    service = (ROOT / "app" / "services" / "github_import.py").read_text(encoding="utf-8")
    service_bootstrap = (ROOT / "app" / "services" / "__init__.py").read_text(encoding="utf-8")
    handler = (ROOT / "app" / "handlers" / "github_import.py").read_text(encoding="utf-8")
    source_service = (ROOT / "app" / "services" / "github_source_publish.py").read_text(encoding="utf-8")
    source_handler = (ROOT / "app" / "handlers" / "github_source_publish.py").read_text(encoding="utf-8")
    source_upload = (ROOT / "app" / "services" / "github_source_upload.py").read_text(encoding="utf-8")
    source_web = (ROOT / "app" / "github_source_upload_web.py").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    bot = (ROOT / "app" / "bot.py").read_text(encoding="utf-8")
    assert "is_system_owner" in service
    assert "import_library_zip" in service
    assert "restore_import_replacement_backups" in service
    assert "finalize_import_replacement_backups" in service
    assert "ContextVar" in service and "_DISCOVERY_CONTEXT" in service
    assert "raw.githubusercontent.com" in service
    assert "_require_archive_space" in service
    assert "LICENSE.txt" in service and "SOURCES.txt" in service
    assert "allow_update=True" in service
    assert "serialize_package_import" in service_bootstrap
    assert "asyncio.Lock" in service_bootstrap
    assert "_RESOLVED_PACKAGES.set(None)" in service_bootstrap
    assert "owner:system:diagnostics" in handler
    assert "owner:github_import" in handler
    assert "owner:github_source_publish" in handler
    assert 'Command("github_import")' in handler
    assert "F.from_user.id == settings.SYSTEM_OWNER_ID" in handler
    assert "GITHUB_IMPORT_ENABLED" in handler
    assert "GITHUB_SOURCE_WRITE_TOKEN" in source_service
    assert '"force": False' in source_service
    assert "build_enabled_import_index" in source_service
    assert "inspect_source_package_zip" in source_service
    assert "existing_paths - incoming_paths" in source_service
    assert 'Command("github_source_publish")' in source_handler
    assert "create_github_source_upload_token" in source_handler
    assert "🌐 Загрузить ZIP напрямую" in source_handler
    assert "github_source_publish" in source_upload
    assert "hmac.compare_digest" in source_upload
    assert "SOURCE_UPLOAD_CHUNK_SIZE_BYTES" in source_upload
    assert 'router.post("/api/github-source-upload/{upload_id}/finish"' in source_web
    assert "publish_source_package_zip" in source_web
    assert "application.include_router(github_source_upload_web_router)" in main
    assert bot.index("dp.include_router(github_source_publish.router)") < bot.index("dp.include_router(owner.router)")
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
