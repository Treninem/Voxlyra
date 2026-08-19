from __future__ import annotations

import json

import pytest

from app.config import settings


@pytest.mark.asyncio
@pytest.mark.parametrize("source_platform", ["telegram", "vk"])
async def test_linked_telegram_and_vk_are_one_canonical_account_from_both_entry_points(
    tmp_path, monkeypatch, source_platform: str
):
    from app.db import (
        connect,
        get_user_preferences,
        init_db,
        set_user_preference,
        upsert_user,
    )
    from app.services import tma_auth, vk_api
    from app.services.account_identity import identity_status, resolve_external_identity
    from app.services.smart_account_link import create_smart_link_request, confirm_smart_link_request

    settings.DATABASE_PATH = str(tmp_path / f"one-account-{source_platform}.sqlite3")
    await init_db()

    tg_external = 160001
    vk_external = 260002
    tg = await upsert_user(tg_external, "tg_common", "TG Common")
    vk = await upsert_user(settings.vk_identity_id(vk_external), "vk_common", "VK Common")
    tg_id = int(tg["id"])
    vk_id = int(vk["id"])

    await resolve_external_identity("telegram", tg_external, tg_id)
    await resolve_external_identity("vk", vk_external, vk_id)

    if source_platform == "telegram":
        request = await create_smart_link_request(
            source_user_id=tg_id,
            source_platform="telegram",
            source_external_id=tg_external,
            source_username="tg_common",
            source_full_name="TG Common",
            target_reference=str(vk_external),
        )
        result = await confirm_smart_link_request(
            token=request["token"],
            target_user_id=vk_id,
            target_platform="vk",
            target_external_id=vk_external,
        )
    else:
        request = await create_smart_link_request(
            source_user_id=vk_id,
            source_platform="vk",
            source_external_id=vk_external,
            source_username="vk_common",
            source_full_name="VK Common",
            target_reference=str(tg_external),
        )
        result = await confirm_smart_link_request(
            token=request["token"],
            target_user_id=tg_id,
            target_platform="telegram",
            target_external_id=tg_external,
        )

    # Linking direction must never change the canonical account: Telegram is the
    # stable legacy row and both verified platform identities point to it.
    assert int(result["canonical_user_id"]) == tg_id
    assert await resolve_external_identity("telegram", tg_external, tg_id) == tg_id
    assert await resolve_external_identity("vk", vk_external, vk_id) == tg_id

    status = await identity_status(tg_id)
    assert status["linked"] is True
    assert status["telegram"] is True
    assert status["vk"] is True

    async with connect() as db:
        rows = await (
            await db.execute(
                """SELECT platform,external_id,user_id
                   FROM external_identities
                   WHERE (platform='telegram' AND external_id=?)
                      OR (platform='vk' AND external_id=?)
                   ORDER BY platform""",
                (str(tg_external), str(vk_external)),
            )
        ).fetchall()
    assert len(rows) == 2
    assert {int(row["user_id"]) for row in rows} == {tg_id}

    # Exercise both real Mini App authentication entry points after the merge.
    # Signature verification itself has separate tests; here we isolate identity
    # routing and prove that signed TG and VK sessions receive one app_user_id.
    monkeypatch.setattr(
        tma_auth,
        "_validate_init_data_raw",
        lambda *_args, **_kwargs: {
            "user": json.dumps(
                {
                    "id": tg_external,
                    "username": "tg_common",
                    "first_name": "TG",
                    "last_name": "Common",
                }
            )
        },
    )
    monkeypatch.setattr(
        tma_auth,
        "_validate_vk_launch_params",
        lambda _raw: {"vk_user_id": str(vk_external)},
    )

    async def fake_vk_profile(_vk_id: int):
        return {
            "id": vk_external,
            "screen_name": "vk_common",
            "first_name": "VK",
            "last_name": "Common",
        }

    monkeypatch.setattr(tma_auth, "get_vk_user_profile", fake_vk_profile)

    tg_session = await tma_auth._authenticate_telegram_init_data("signed-telegram-session")
    vk_session = await tma_auth._authenticate_vk_launch_data("signed-vk-session")

    assert tg_session.app_user_id == tg_id
    assert vk_session.app_user_id == tg_id
    assert tg_session.app_user_id == vk_session.app_user_id
    if vk_external not in settings.vk_owner_ids:
        assert tg_session.telegram_id == tg_external
        assert vk_session.telegram_id == tg_external

    # The VK community bot must resolve to exactly the same account as both Mini Apps.
    vk_bot_user_id, vk_bot_user = await vk_api._vk_resolve_app_user(vk_external)
    assert vk_bot_user_id == tg_id
    assert int(vk_bot_user["id"]) == tg_id

    # Shared state written from the VK session must be immediately visible from
    # Telegram because both sides operate on one canonical user_id.
    await set_user_preference(vk_session.app_user_id, "theme", "dark")
    tg_preferences = await get_user_preferences(tg_session.app_user_id)
    assert tg_preferences["theme"] == "dark"
