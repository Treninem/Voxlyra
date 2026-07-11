from __future__ import annotations

import asyncio
from pathlib import Path


def test_v196_comics_status_is_honest():
    status = Path("docs/STATUS_V1_9_6.md").read_text(encoding="utf-8")
    assert "около 55%" in status
    assert "Осталось 3 крупных этапа" in status


def test_v196_purchase_freezes_author_rate_per_sale(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "sales.sqlite3"))

    async def scenario():
        from app.db import connect, init_db, set_setting

        await init_db()
        await set_setting("payments_stars_author_rate_minor", "100")
        async with connect() as db:
            cur = await db.execute("PRAGMA table_info(author_ledger)")
            columns = {row[1] for row in await cur.fetchall()}
            assert {"settlement_rate_minor", "net_minor"}.issubset(columns)
            cur = await db.execute("PRAGMA table_info(author_payout_requests)")
            payout_columns = {row[1] for row in await cur.fetchall()}
            assert {"amount_minor", "settlement_note"}.issubset(payout_columns)

    asyncio.run(scenario())


def test_v196_active_owner_ui_has_no_yookassa_keys():
    source = Path("static/js/control.js").read_text(encoding="utf-8")
    payment_section = source[source.index("async function loadPaymentSettings"):source.index("async function refreshDashboard")]
    assert "yookassa_" not in payment_section.lower()
    assert "buyer_star_rate_minor" in payment_section
    assert "author_star_rate_minor" in payment_section
