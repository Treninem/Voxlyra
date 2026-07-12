import asyncio
from pathlib import Path

import pytest

pytest.importorskip("aiosqlite")


def test_premium_plan_activation_and_owner_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "premium.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import (
            activate_premium_subscription,
            get_premium_owner_summary,
            get_purchase_target,
            get_user_premium_status,
            init_db,
            list_premium_plans,
            upsert_user,
        )

        await init_db()
        plans = await list_premium_plans()
        assert len(plans) == 1
        plan = plans[0]
        assert plan["code"] == "monthly"
        assert int(plan["price_stars"]) > 0
        target = await get_purchase_target("vox:premium:monthly")
        assert target and target["kind"] == "premium"
        assert int(target["amount_stars"]) == int(plan["price_stars"])

        user = await upsert_user(881001, "premium_reader", "Premium Reader")
        subscription_id = await activate_premium_subscription(
            user_id=int(user["id"]),
            plan_code="monthly",
            amount_stars=int(plan["price_stars"]),
            telegram_payment_charge_id="premium-stage8-charge-1",
            is_recurring=True,
            is_first_recurring=True,
            invoice_payload="vox:premium:monthly",
        )
        assert subscription_id > 0
        status = await get_user_premium_status(int(user["id"]))
        assert status["active"] is True
        assert status["auto_renew"] is True
        assert status["is_recurring"] is True
        assert any(item["code"] == "priority_tts" for item in status["features"])
        summary = await get_premium_owner_summary()
        assert summary["active_users"] == 1
        assert summary["payments"] == 1
        assert summary["gross_stars"] == int(plan["price_stars"])

    asyncio.run(scenario())


def test_premium_payment_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "premium-idempotent.sqlite3"))
    from app.config import get_settings
    get_settings.cache_clear()

    async def scenario():
        from app.db import activate_premium_subscription, get_premium_plan, get_premium_owner_summary, init_db, upsert_user

        await init_db()
        plan = await get_premium_plan("monthly")
        before = await get_premium_owner_summary()
        user = await upsert_user(881002, "premium_repeat", "Premium Repeat")
        kwargs = dict(
            user_id=int(user["id"]), plan_code="monthly", amount_stars=int(plan["price_stars"]),
            telegram_payment_charge_id="same-premium-charge", is_recurring=True,
            invoice_payload="vox:premium:monthly",
        )
        first = await activate_premium_subscription(**kwargs)
        second = await activate_premium_subscription(**kwargs)
        assert first == second
        summary = await get_premium_owner_summary()
        assert summary["payments"] == int(before["payments"]) + 1

    asyncio.run(scenario())


def test_premium_quote_styles_are_real_png():
    from app.services.quote_cards import build_quote_card

    for style in ("standard", "aurora", "parchment"):
        image = build_quote_card(
            quote="Книга открылась, и над страницами вспыхнул мягкий свет.",
            book_title="VoxLyra",
            chapter_title="Глава 1",
            author_name="Автор",
            style=style,
        )
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(image) > 20_000


def test_premium_priority_is_applied_to_tts_sessions():
    from app.services.tts_sessions import ReaderTTSSession, TTSSessionManager
    from app.services.tts_queue import TTSGenerationQueue
    from app.services.tts_text import prepare_tts_chapter

    manager = TTSSessionManager(TTSGenerationQueue())
    prepared = prepare_tts_chapter("Первое предложение. Второе предложение.")
    base = dict(
        id="premium-priority", user_id=1, chapter_id=1, voice="irina", style="natural",
        high_quality=False, prepared=prepared, providers=("vosk", "piper"),
        created_at=1, expires_at=9999999999,
    )
    normal = ReaderTTSSession(**base, priority_boost=False)
    premium = ReaderTTSSession(**{**base, "id": "premium-priority-2"}, priority_boost=True)
    assert manager._priority(premium, 0, 0) < manager._priority(normal, 0, 0)


def test_stage8_ui_payment_and_owner_controls_are_wired():
    root = Path(__file__).resolve().parents[1]
    premium = (root / "templates/premium.html").read_text(encoding="utf-8")
    library = (root / "templates/library.html").read_text(encoding="utf-8")
    reader = (root / "templates/reader.html").read_text(encoding="utf-8")
    app_js = (root / "static/js/app.js").read_text(encoding="utf-8")
    control_js = (root / "static/js/control.js").read_text(encoding="utf-8")
    webapp = (root / "app/webapp.py").read_text(encoding="utf-8")
    payments = (root / "app/handlers/payments.py").read_text(encoding="utf-8")

    assert 'id="premiumPage"' in premium
    assert 'id="libraryPremiumBadge"' in library
    assert 'id="readerQuoteStyle"' in reader
    assert "initPremiumPage" in app_js
    assert "loadPremiumSettings" in control_js
    assert '/api/premium/checkout' in webapp
    assert '/api/control/premium' in webapp
    assert "activate_premium_subscription" in payments
