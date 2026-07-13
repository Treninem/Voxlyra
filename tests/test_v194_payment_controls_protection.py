from __future__ import annotations

import asyncio
from pathlib import Path


def test_v196_build_and_required_assets_exist():
    from app.build_info import OWNER_BUILD_NAME, OWNER_BUILD_VERSION

    assert OWNER_BUILD_VERSION in {"v1.9.6", "v1.9.7", "v1.9.8", "v1.9.9", "v1.10.0", "v1.10.1", "v1.10.2", "v1.10.3", "v1.10.4", "v1.10.5", "v1.11.0", "v1.11.1", "v1.11.2", "v1.11.3", "v1.11.4"}
    assert OWNER_BUILD_NAME
    for relative in (
        "app/services/payment_runtime.py",
        "docs/STARS_ONLY_PRICING_V1_9_6.md",
        "docs/STATUS_V1_9_6.md",
    ):
        assert Path(relative).is_file(), relative


def test_v196_runtime_uses_only_stars_and_two_rates(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "runtime.sqlite3"))

    async def scenario():
        from app.db import get_setting, init_db, set_setting
        from app.services.payment_runtime import (
            load_runtime_payment_settings,
            public_runtime_payment_settings,
            update_runtime_payment_settings,
        )

        await init_db()
        # Даже сохранённые старые флаги не должны включать ЮKassa.
        await set_setting("payments_yookassa_external_enabled", "1")
        await set_setting("payments_yookassa_payouts_enabled", "1")
        await update_runtime_payment_settings({
            "stars_enabled": True,
            "content_protection_enabled": True,
            "watermark_enabled": True,
            "buyer_star_rate_minor": 145,
            "author_star_rate_minor": 100,
        })
        cfg = await load_runtime_payment_settings()
        assert cfg.stars_enabled
        assert cfg.buyer_star_rate_minor == 145
        assert cfg.author_star_rate_minor == 100
        assert not cfg.yookassa_external_enabled
        assert not cfg.yookassa_payouts_enabled
        assert not cfg.payouts_ready
        assert await get_setting("payments_yookassa_external_enabled", "1") == "0"
        assert await get_setting("payments_yookassa_payouts_enabled", "1") == "0"
        public = await public_runtime_payment_settings()
        assert public["telegram_digital_policy"] == "stars_only"
        assert public["rate_spread_minor"] == 45

        try:
            await update_runtime_payment_settings({
                "buyer_star_rate_minor": 100,
                "author_star_rate_minor": 100,
            })
        except ValueError as exc:
            assert "выше" in str(exc)
        else:
            raise AssertionError("Одинаковые курсы не должны приниматься")

    asyncio.run(scenario())


def test_v196_owner_controls_have_only_stars_rates_and_protection():
    keyboard = Path("app/keyboards.py").read_text(encoding="utf-8")
    owner = Path("app/handlers/owner.py").read_text(encoding="utf-8")
    control = Path("static/js/control.js").read_text(encoding="utf-8")

    for key in (
        "stars_enabled",
        "buyer_star_rate_minor",
        "author_star_rate_minor",
        "content_protection_enabled",
        "watermark_enabled",
    ):
        assert key in keyboard or key in control
        assert key in owner or key in control
    assert "/api/control/payment-settings" in control
    assert "provider_token_test" not in control
    assert "provider_token_live" not in control
    assert "yookassa_external_enabled" not in control


def test_v196_stars_invoice_remains_xtr_without_provider_token():
    source = Path("app/handlers/payments.py").read_text(encoding="utf-8")
    assert "load_runtime_payment_settings" in source
    assert "stars_enabled" in source
    assert 'currency="XTR"' in source
    assert 'provider_token=""' in source
    assert "provider_token_test" not in source
    assert "YOOKASSA_SHOP_ID" not in source


def test_v194_content_protection_respects_author_download_permission():
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    reader = Path("static/js/app.js").read_text(encoding="utf-8")
    comic = Path("static/js/comic.js").read_text(encoding="utf-8")
    db = Path("app/db.py").read_text(encoding="utf-8")

    assert "/api/book/{book_id}/download.txt" in webapp
    assert "allow_download" in db
    assert "content_protection_enabled" in webapp
    assert "protected-content" in reader
    assert "downloadAllowedBook" in reader
    assert "contextmenu" in reader
    assert "watermark" in reader.lower()
    assert "persistentAllowed" in comic
    assert "allow_download" in comic


def test_v196_no_false_promise_of_screenshot_blocking():
    control = Path("static/js/control.js").read_text(encoding="utf-8")
    docs = Path("docs/PAYMENTS_AND_PROTECTION_V1_9_4.md").read_text(encoding="utf-8")
    assert "Абсолютно запретить системный скриншот" in control
    assert "невозможно гарантированно" in docs
