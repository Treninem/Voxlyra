from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings


def test_account_reference_accepts_username_id_and_platform_links():
    from app.services.smart_account_link import normalize_account_reference

    assert normalize_account_reference("@Some_User", "telegram") == ("username", "some_user")
    assert normalize_account_reference("123456789", "telegram") == ("id", "123456789")
    assert normalize_account_reference("https://t.me/Some_User", "telegram") == ("username", "some_user")
    assert normalize_account_reference("https://vk.com/id987654", "vk") == ("id", "987654")
    assert normalize_account_reference("vk.com/Some.Name", "vk") == ("username", "some.name")


@pytest.mark.asyncio
async def test_confirmation_smart_merges_into_telegram_and_keeps_furthest_progress(tmp_path):
    from app.db import connect, init_db, upsert_user, utc_now
    from app.services.account_identity import identity_status, resolve_external_identity
    from app.services.smart_account_link import create_smart_link_request, confirm_smart_link_request

    settings.DATABASE_PATH = str(tmp_path / "smart-link.sqlite3")
    await init_db()

    tg = await upsert_user(telegram_id=111001, username="tg_owner", full_name="Telegram Owner")
    vk = await upsert_user(telegram_id=settings.vk_identity_id(222002), username="vk_owner", full_name="VK Owner")
    tg_id = int(tg["id"])
    vk_id = int(vk["id"])
    await resolve_external_identity("telegram", 111001, tg_id)
    await resolve_external_identity("vk", 222002, vk_id)

    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO books(title,created_at,updated_at) VALUES(?,?,?)",
            ("Merge book", now, now),
        )
        book_id = int(cur.lastrowid)
        cur = await db.execute(
            "INSERT INTO chapters(book_id,number,title,text,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (book_id, 1, "Chapter", "text", now, now),
        )
        chapter_id = int(cur.lastrowid)
        await db.execute(
            "INSERT INTO reading_progress(user_id,book_id,chapter_id,position_percent,updated_at) VALUES(?,?,?,?,?)",
            (tg_id, book_id, chapter_id, 35, now),
        )
        await db.execute(
            "INSERT INTO reading_progress(user_id,book_id,chapter_id,position_percent,updated_at) VALUES(?,?,?,?,?)",
            (vk_id, book_id, chapter_id, 82, now),
        )
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,?,?,?)",
            (tg_id, 10, now, now),
        )
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,?,?,?)",
            (vk_id, 7, now, now),
        )
        await db.execute(
            "INSERT INTO bonus_wallets(user_id,balance,created_at,updated_at) VALUES(?,?,?,?)",
            (tg_id, 100, now, now),
        )
        await db.execute(
            "INSERT INTO bonus_wallets(user_id,balance,created_at,updated_at) VALUES(?,?,?,?)",
            (vk_id, 40, now, now),
        )
        await db.execute(
            "INSERT INTO purchases(user_id,amount_stars,status,created_at) VALUES(?,?,?,?)",
            (vk_id, 5, "paid", now),
        )
        await db.commit()

    request = await create_smart_link_request(
        source_user_id=vk_id,
        source_platform="vk",
        source_external_id=222002,
        source_username="vk_owner",
        source_full_name="VK Owner",
        target_reference="111001",
    )
    result = await confirm_smart_link_request(
        token=request["token"],
        target_user_id=tg_id,
        target_platform="telegram",
        target_external_id=111001,
    )

    assert result["canonical_user_id"] == tg_id
    assert result["secondary_user_id"] == vk_id
    assert (await identity_status(tg_id))["linked"] is True

    async with connect() as db:
        progress = await (
            await db.execute(
                "SELECT user_id,position_percent FROM reading_progress WHERE chapter_id=?",
                (chapter_id,),
            )
        ).fetchall()
        assert [(int(row["user_id"]), int(row["position_percent"])) for row in progress] == [(tg_id, 82)]
        wallet = await (await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (tg_id,))).fetchone()
        assert int(wallet["balance_stars"]) == 17
        bonus = await (await db.execute("SELECT balance FROM bonus_wallets WHERE user_id=?", (tg_id,))).fetchone()
        assert int(bonus["balance"]) == 140
        assert await (await db.execute("SELECT id FROM bonus_wallets WHERE user_id=?", (vk_id,))).fetchone() is None
        purchase = await (await db.execute("SELECT user_id FROM purchases WHERE amount_stars=5", ())).fetchone()
        assert int(purchase["user_id"]) == tg_id
        identities = await (
            await db.execute(
                "SELECT platform,external_id,user_id FROM external_identities WHERE external_id IN ('111001','222002') ORDER BY platform"
            )
        ).fetchall()
        assert {int(row["user_id"]) for row in identities} == {tg_id}
        event = await (
            await db.execute(
                "SELECT strategy,canonical_user_id,secondary_user_id FROM account_merge_events ORDER BY id DESC LIMIT 1"
            )
        ).fetchone()
        assert str(event["strategy"]) == "smart_merge"
        assert int(event["canonical_user_id"]) == tg_id
        assert int(event["secondary_user_id"]) == vk_id


@pytest.mark.asyncio
async def test_wrong_second_account_cannot_confirm_request(tmp_path):
    from app.db import init_db, upsert_user
    from app.services.account_identity import AccountLinkError, identity_status, resolve_external_identity
    from app.services.smart_account_link import create_smart_link_request, confirm_smart_link_request, get_source_link_request

    settings.DATABASE_PATH = str(tmp_path / "wrong-target.sqlite3")
    await init_db()
    tg = await upsert_user(telegram_id=333003, username="tg_three", full_name="TG Three")
    vk = await upsert_user(telegram_id=settings.vk_identity_id(444004), username="vk_four", full_name="VK Four")
    wrong_vk = await upsert_user(telegram_id=settings.vk_identity_id(555005), username="vk_five", full_name="VK Five")
    tg_id, vk_id, wrong_vk_id = int(tg["id"]), int(vk["id"]), int(wrong_vk["id"])
    await resolve_external_identity("telegram", 333003, tg_id)
    await resolve_external_identity("vk", 444004, vk_id)
    await resolve_external_identity("vk", 555005, wrong_vk_id)

    request = await create_smart_link_request(
        source_user_id=tg_id,
        source_platform="telegram",
        source_external_id=333003,
        source_username="tg_three",
        source_full_name="TG Three",
        target_reference="444004",
    )
    with pytest.raises(AccountLinkError, match="другого аккаунта"):
        await confirm_smart_link_request(
            token=request["token"],
            target_user_id=wrong_vk_id,
            target_platform="vk",
            target_external_id=555005,
        )

    assert (await identity_status(tg_id))["linked"] is False
    assert (await get_source_link_request(tg_id, request["token"]))["status"] == "pending"


def test_settings_use_request_confirmation_instead_of_manual_merge_choice():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates" / "settings.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "account-linking.js").read_text(encoding="utf-8")
    router = (root / "app" / "account_link_web.py").read_text(encoding="utf-8")
    notifications = (root / "app" / "services" / "account_link_notifications.py").read_text(encoding="utf-8")
    vk_source = (root / "app" / "services" / "vk_api.py").read_text(encoding="utf-8")

    assert 'id="smartAccountLinkTarget"' in template
    assert '@username или ID' in template
    assert 'id="crossPlatformCreateCode"' not in template
    assert 'id="crossPlatformMergeChoice"' not in template
    assert 'account-linking.js?v={{ asset_version }}' in template
    assert "/api/account-link/request" in script
    assert "/api/account-link/incoming" in script
    assert "/confirm`" not in script  # endpoint is assembled from the requested action
    assert '/api/account-link/request/{token}/confirm' in router
    assert '/api/account-link/request/{token}/reject' in router
    assert 'result.get("delivery_status") == "sent"' in router
    assert "source_platform=? AND source_external_id=?" in router
    assert "Ничего не объединится автоматически по одному username или ID" in notifications
    assert "Проверить и подтвердить" in notifications
    assert "создайте код на одной платформе" not in vk_source.casefold()
    assert "По одному username или ID данные не сливаются" in vk_source
