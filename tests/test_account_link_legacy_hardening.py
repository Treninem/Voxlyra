from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings


@pytest.mark.asyncio
async def test_smart_merge_invalidates_legacy_codes_and_blocks_new_codes(tmp_path):
    from app.db import connect, init_db, upsert_user
    from app.services.account_identity import AccountLinkError, create_link_code, resolve_external_identity
    from app.services.smart_account_link import create_smart_link_request, confirm_smart_link_request

    settings.DATABASE_PATH = str(tmp_path / "legacy-code-invalidation.sqlite3")
    await init_db()
    tg = await upsert_user(telegram_id=120001, username="tg_main", full_name="TG Main")
    vk = await upsert_user(telegram_id=settings.vk_identity_id(220002), username="vk_main", full_name="VK Main")
    tg_id, vk_id = int(tg["id"]), int(vk["id"])
    await resolve_external_identity("telegram", 120001, tg_id)
    await resolve_external_identity("vk", 220002, vk_id)

    await create_link_code(tg_id, "telegram")
    request = await create_smart_link_request(
        source_user_id=tg_id,
        source_platform="telegram",
        source_external_id=120001,
        source_username="tg_main",
        source_full_name="TG Main",
        target_reference="220002",
    )
    await confirm_smart_link_request(
        token=request["token"],
        target_user_id=vk_id,
        target_platform="vk",
        target_external_id=220002,
    )

    async with connect() as db:
        row = await (
            await db.execute(
                "SELECT used_at FROM account_link_codes WHERE source_user_id=? ORDER BY created_at DESC LIMIT 1",
                (tg_id,),
            )
        ).fetchone()
    assert row is not None
    assert str(row["used_at"] or "")

    with pytest.raises(AccountLinkError, match="уже связан"):
        await create_link_code(tg_id, "telegram")


@pytest.mark.asyncio
async def test_stale_legacy_code_cannot_attach_second_vk_to_linked_telegram(tmp_path):
    from app.db import connect, init_db, upsert_user
    from app.services.account_identity import AccountLinkError, consume_link_code, create_link_code, resolve_external_identity
    from app.services.smart_account_link import create_smart_link_request, confirm_smart_link_request

    settings.DATABASE_PATH = str(tmp_path / "legacy-multilink.sqlite3")
    await init_db()
    tg = await upsert_user(telegram_id=130001, username="tg_owner", full_name="TG Owner")
    vk_primary = await upsert_user(
        telegram_id=settings.vk_identity_id(230002), username="vk_primary", full_name="VK Primary"
    )
    vk_other = await upsert_user(
        telegram_id=settings.vk_identity_id(330003), username="vk_other", full_name="VK Other"
    )
    tg_id = int(tg["id"])
    vk_primary_id = int(vk_primary["id"])
    vk_other_id = int(vk_other["id"])
    await resolve_external_identity("telegram", 130001, tg_id)
    await resolve_external_identity("vk", 230002, vk_primary_id)
    await resolve_external_identity("vk", 330003, vk_other_id)

    legacy = await create_link_code(tg_id, "telegram")
    request = await create_smart_link_request(
        source_user_id=tg_id,
        source_platform="telegram",
        source_external_id=130001,
        source_username="tg_owner",
        source_full_name="TG Owner",
        target_reference="230002",
    )
    await confirm_smart_link_request(
        token=request["token"],
        target_user_id=vk_primary_id,
        target_platform="vk",
        target_external_id=230002,
    )

    # Simulate a stale pre-hardening database where an old code survived the
    # first link. The compatibility endpoint itself must still reject a second VK.
    future = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    async with connect() as db:
        await db.execute(
            "UPDATE account_link_codes SET used_at=NULL,expires_at=? WHERE source_user_id=?",
            (future, tg_id),
        )
        await db.commit()

    with pytest.raises(AccountLinkError, match="Исходный профиль уже связан"):
        await consume_link_code(
            current_user_id=vk_other_id,
            current_platform="vk",
            external_id=330003,
            code=legacy["code"],
            strategy="merge",
        )

    async with connect() as db:
        linked_vk = await (
            await db.execute(
                "SELECT external_id FROM external_identities WHERE user_id=? AND platform='vk' ORDER BY id",
                (tg_id,),
            )
        ).fetchall()
        other_mapping = await (
            await db.execute(
                "SELECT user_id FROM external_identities WHERE platform='vk' AND external_id='330003'"
            )
        ).fetchone()
    assert [str(row["external_id"]) for row in linked_vk] == ["230002"]
    assert int(other_mapping["user_id"]) == vk_other_id


@pytest.mark.asyncio
async def test_legacy_keep_vk_choice_is_forced_to_safe_telegram_canonical_merge(tmp_path):
    from app.db import connect, init_db, upsert_user, utc_now
    from app.services.account_identity import consume_link_code, create_link_code, identity_status, resolve_external_identity

    settings.DATABASE_PATH = str(tmp_path / "legacy-keep-vk.sqlite3")
    await init_db()
    tg = await upsert_user(telegram_id=140001, username="tg_history", full_name="TG History")
    vk = await upsert_user(telegram_id=settings.vk_identity_id(240002), username="vk_fresh", full_name="VK Fresh")
    tg_id, vk_id = int(tg["id"]), int(vk["id"])
    await resolve_external_identity("telegram", 140001, tg_id)
    await resolve_external_identity("vk", 240002, vk_id)
    now = utc_now()
    async with connect() as db:
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,?,?,?)",
            (tg_id, 11, now, now),
        )
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,?,?,?)",
            (vk_id, 4, now, now),
        )
        await db.commit()

    legacy = await create_link_code(tg_id, "telegram")
    result = await consume_link_code(
        current_user_id=vk_id,
        current_platform="vk",
        external_id=240002,
        code=legacy["code"],
        strategy="keep_vk",  # accepted from an old client, but no longer honored
    )

    assert result["canonical_user_id"] == tg_id
    assert (await identity_status(tg_id))["linked"] is True
    async with connect() as db:
        wallet = await (
            await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (tg_id,))
        ).fetchone()
        old_wallet = await (
            await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (vk_id,))
        ).fetchone()
    assert int(wallet["balance_stars"]) == 15
    assert old_wallet is None


@pytest.mark.asyncio
async def test_critical_merge_failure_rolls_back_identity_and_balances(tmp_path):
    from app.db import connect, init_db, upsert_user, utc_now
    from app.services.account_identity import identity_status, resolve_external_identity
    from app.services.smart_account_link import create_smart_link_request, confirm_smart_link_request, get_source_link_request

    settings.DATABASE_PATH = str(tmp_path / "atomic-merge.sqlite3")
    await init_db()
    tg = await upsert_user(telegram_id=150001, username="tg_atomic", full_name="TG Atomic")
    vk = await upsert_user(telegram_id=settings.vk_identity_id(250002), username="vk_atomic", full_name="VK Atomic")
    tg_id, vk_id = int(tg["id"]), int(vk["id"])
    await resolve_external_identity("telegram", 150001, tg_id)
    await resolve_external_identity("vk", 250002, vk_id)
    now = utc_now()
    async with connect() as db:
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,?,?,?)",
            (tg_id, 20, now, now),
        )
        await db.execute(
            "INSERT INTO reader_wallets(user_id,balance_stars,created_at,updated_at) VALUES(?,?,?,?)",
            (vk_id, 9, now, now),
        )
        await db.execute(
            f"""CREATE TRIGGER force_wallet_merge_failure
                BEFORE DELETE ON reader_wallets
                WHEN OLD.user_id={vk_id}
                BEGIN
                    SELECT RAISE(ABORT, 'forced wallet merge failure');
                END"""
        )
        await db.commit()

    request = await create_smart_link_request(
        source_user_id=vk_id,
        source_platform="vk",
        source_external_id=250002,
        source_username="vk_atomic",
        source_full_name="VK Atomic",
        target_reference="150001",
    )
    with pytest.raises(Exception, match="forced wallet merge failure"):
        await confirm_smart_link_request(
            token=request["token"],
            target_user_id=tg_id,
            target_platform="telegram",
            target_external_id=150001,
        )

    assert (await identity_status(tg_id))["linked"] is False
    assert (await identity_status(vk_id))["linked"] is False
    assert (await get_source_link_request(vk_id, request["token"]))["status"] == "pending"
    async with connect() as db:
        tg_wallet = await (
            await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (tg_id,))
        ).fetchone()
        vk_wallet = await (
            await db.execute("SELECT balance_stars FROM reader_wallets WHERE user_id=?", (vk_id,))
        ).fetchone()
    assert int(tg_wallet["balance_stars"]) == 20
    assert int(vk_wallet["balance_stars"]) == 9
