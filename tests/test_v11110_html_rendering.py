from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_channel_publication_forces_html_parse_mode():
    source = (ROOT / "app" / "services" / "publication.py").read_text(encoding="utf-8")
    assert "from aiogram.enums import ParseMode" in source
    assert source.count("parse_mode=ParseMode.HTML") >= 3


def test_moderation_alerts_force_html_parse_mode():
    source = (ROOT / "app" / "services" / "moderation_alerts.py").read_text(encoding="utf-8")
    assert '"parse_mode": ParseMode.HTML' in source


def test_build_version_is_v11110():
    source = (ROOT / "app" / "build_info.py").read_text(encoding="utf-8")
    assert 'OWNER_BUILD_VERSION = "v1.11.12"' in source
