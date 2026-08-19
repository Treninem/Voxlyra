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
                "SELECT user_id FROM external_identities WHERE platform='vk' AND external_id='330003'",
            )
        ).fetchone()
    assert [str(row["external_id"]) for row in linked_vk] == ["230002"]
    assert int(other_mapping["user_id"]) == vk_other_id
