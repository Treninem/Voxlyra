from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import connect, get_user_by_id, utc_now
from app.services.account_identity import AccountLinkError, _account_summary, _merge_reader_data, ensure_identity_schema

logger = logging.getLogger(__name__)
_REQUEST_TTL_MINUTES = 10
_REQUEST_REUSE_SECONDS = 30
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def normalize_account_reference(raw: str, platform: str) -> tuple[str, str]:
    """Return (kind, value) for a Telegram/VK username or numeric id."""
    value = str(raw or "").strip()
    if not value:
        raise AccountLinkError("Введите @username или ID аккаунта на второй платформе.")
    value = re.sub(r"^https?://", "", value, flags=re.IGNORECASE).strip().strip("/")
    lower = value.casefold()
    for prefix in ("t.me/", "telegram.me/", "telegram.dog/", "vk.com/", "m.vk.com/"):
        if lower.startswith(prefix):
            value = value[len(prefix):].split("?", 1)[0].split("#", 1)[0].strip("/")
            break
    value = value.strip().lstrip("@").strip()
    platform = str(platform or "").strip().lower()
    if platform == "vk" and re.fullmatch(r"id\d+", value, flags=re.IGNORECASE):
        value = value[2:]
    if value.isdigit():
        numeric = str(int(value))
        if int(numeric) <= 0:
            raise AccountLinkError("ID аккаунта должен быть положительным числом.")
        return "id", numeric
    if not _USERNAME_RE.fullmatch(value):
        raise AccountLinkError("Не удалось распознать аккаунт. Введите @username или числовой ID.")
    return "username", value.casefold()


async def ensure_smart_link_schema() -> None:
    await ensure_identity_schema()
    async with connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS account_link_requests (
                token TEXT PRIMARY KEY,
                source_user_id INTEGER NOT NULL,
                source_platform TEXT NOT NULL,
                source_external_id TEXT NOT NULL,
                source_label TEXT NOT NULL DEFAULT '',
                target_platform TEXT NOT NULL,
                target_external_id TEXT NOT NULL,
                target_user_id INTEGER,
                target_label TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                delivery_status TEXT NOT NULL DEFAULT 'pending',
                delivery_error TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL,
                decided_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(source_user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_account_link_requests_source
                ON account_link_requests(source_user_id, status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_account_link_requests_target
                ON account_link_requests(target_platform, target_external_id, status, created_at DESC);
            """
        )
        await db.commit()


async def _expire_requests(db: Any, now: str) -> None:
    await db.execute(
        """UPDATE account_link_requests
           SET status='expired',updated_at=?
           WHERE status='pending' AND expires_at<=?""",
        (now, now),
    )


async def _identity_user_id(db: Any, platform: str, external_id: str) -> int | None:
    row = await (
        await db.execute(
            "SELECT user_id FROM external_identities WHERE platform=? AND external_id=? LIMIT 1",
            (str(platform), str(external_id)),
        )
    ).fetchone()
    return int(row["user_id"]) if row else None


async def _user_label(db: Any, user_id: int | None, fallback: str = "") -> str:
    if not user_id:
        return str(fallback or "")
    row = await (await db.execute("SELECT username,full_name FROM users WHERE id=?", (int(user_id),))).fetchone()
    if not row:
        return str(fallback or "")
    username = str(row["username"] or "").strip()
    if username:
        return f"@{username.lstrip('@')}"
    return str(row["full_name"] or fallback or "").strip()


async def _resolve_db_username(db: Any, platform: str, username: str) -> tuple[str, int, str] | None:
    row = await (
        await db.execute(
            """SELECT ei.external_id,ei.user_id,u.username,u.full_name
               FROM external_identities ei
               JOIN users u ON u.id=ei.user_id
               WHERE ei.platform=? AND unicode_casefold(COALESCE(u.username,''))=?
               ORDER BY ei.id DESC LIMIT 1""",
            (str(platform), str(username).casefold()),
        )
    ).fetchone()
    if not row:
        return None
    label = f"@{str(row['username'] or username).lstrip('@')}" if str(row["username"] or "").strip() else str(row["full_name"] or username)
    return str(row["external_id"]), int(row["user_id"]), label


async def resolve_target_account(platform: str, raw_reference: str) -> dict[str, Any]:
    """Resolve the opposite platform account without treating the input as proof of ownership."""
    await ensure_smart_link_schema()
    platform = str(platform or "").strip().lower()
    if platform not in {"telegram", "vk"}:
        raise AccountLinkError("Не удалось определить платформу для привязки.")
    kind, value = normalize_account_reference(raw_reference, platform)
    async with connect() as db:
        if kind == "id":
            user_id = await _identity_user_id(db, platform, value)
            label = await _user_label(db, user_id, f"{platform.upper()} ID {value}")
            return {"platform": platform, "external_id": value, "user_id": user_id, "label": label}
        resolved = await _resolve_db_username(db, platform, value)
        if resolved:
            external_id, user_id, label = resolved
            return {"platform": platform, "external_id": external_id, "user_id": user_id, "label": label}

    if platform == "telegram":
        raise AccountLinkError(
            "Telegram @username пока не найден в VoxLyra. Откройте бота с этого Telegram-аккаунта хотя бы один раз или используйте числовой Telegram ID."
        )

    # VK can resolve screen_name through the official users.get API even when
    # the user has not opened VoxLyra yet. Ownership is still proven only later
    # by a signed VK Mini App session on that exact id.
    try:
        from app.services.vk_api import vk_api_call

        rows = await vk_api_call("users.get", {"user_ids": value, "fields": "screen_name"})
    except Exception as exc:
        logger.warning("Could not resolve VK account reference %s: %s", value, exc)
        rows = None
    if not isinstance(rows, list) or not rows:
        raise AccountLinkError("VK-аккаунт не найден. Проверьте @username или ID.")
    row = dict(rows[0])
    vk_id = int(row.get("id") or 0)
    if vk_id <= 0:
        raise AccountLinkError("VK-аккаунт не найден. Проверьте @username или ID.")
    screen = str(row.get("screen_name") or value).strip()
    name = " ".join(filter(None, [row.get("first_name"), row.get("last_name")])).strip()
    async with connect() as db:
        user_id = await _identity_user_id(db, "vk", str(vk_id))
    return {
        "platform": "vk",
        "external_id": str(vk_id),
        "user_id": user_id,
        "label": f"@{screen}" if screen else (name or f"VK ID {vk_id}"),
    }


async def _platform_identity(db: Any, user_id: int, platform: str) -> str:
    row = await (
        await db.execute(
            "SELECT external_id FROM external_identities WHERE user_id=? AND platform=? ORDER BY id LIMIT 1",
            (int(user_id), str(platform)),
        )
    ).fetchone()
    return str(row["external_id"]) if row else ""


async def create_smart_link_request(
    *,
    source_user_id: int,
    source_platform: str,
    source_external_id: int | str,
    source_username: str | None,
    source_full_name: str | None,
    target_reference: str,
) -> dict[str, Any]:
    await ensure_smart_link_schema()
    source_platform = str(source_platform or "").strip().lower()
    if source_platform not in {"telegram", "vk"}:
        raise AccountLinkError("Откройте привязку из Telegram или VK.")
    target_platform = "vk" if source_platform == "telegram" else "telegram"
    source_external = str(source_external_id or "").strip()
    if not source_external:
        raise AccountLinkError("Не удалось определить текущий аккаунт.")
    target = await resolve_target_account(target_platform, target_reference)
    if target["external_id"] == source_external and target_platform == source_platform:
        raise AccountLinkError("Нельзя привязать аккаунт к самому себе.")

    now_dt = _now_dt()
    now = now_dt.isoformat()
    expires_at = (now_dt + timedelta(minutes=_REQUEST_TTL_MINUTES)).isoformat()
    source_label = (
        f"@{str(source_username).lstrip('@')}" if str(source_username or "").strip()
        else str(source_full_name or f"{source_platform.upper()} ID {source_external}").strip()
    )
    async with connect() as db:
        await _expire_requests(db, now)
        source_other = await _platform_identity(db, int(source_user_id), target_platform)
        if source_other:
            if source_other == str(target["external_id"]):
                return {"ok": True, "already_linked": True, "target_platform": target_platform}
            raise AccountLinkError("Этот профиль VoxLyra уже связан с другим аккаунтом второй платформы.")

        target_user_id = int(target["user_id"]) if target.get("user_id") else None
        if target_user_id:
            target_other = await _platform_identity(db, target_user_id, source_platform)
            if target_other and target_user_id != int(source_user_id):
                raise AccountLinkError("Указанный аккаунт уже связан с другим профилем VoxLyra.")
            if target_user_id == int(source_user_id):
                return {"ok": True, "already_linked": True, "target_platform": target_platform}

        recent = await (
            await db.execute(
                """SELECT * FROM account_link_requests
                   WHERE source_user_id=? AND target_platform=? AND target_external_id=?
                     AND status='pending' AND created_at>? ORDER BY created_at DESC LIMIT 1""",
                (
                    int(source_user_id), target_platform, str(target["external_id"]),
                    (now_dt - timedelta(seconds=_REQUEST_REUSE_SECONDS)).isoformat(),
                ),
            )
        ).fetchone()
        if recent:
            return _request_dict(recent)

        # One live outgoing request per profile. A stale request to the wrong id
        # must not remain confirmable after the user changes their mind.
        await db.execute(
            """UPDATE account_link_requests SET status='cancelled',decided_at=?,updated_at=?
               WHERE source_user_id=? AND status='pending'""",
            (now, now, int(source_user_id)),
        )
        token = secrets.token_hex(16)
        await db.execute(
            """INSERT INTO account_link_requests(
                   token,source_user_id,source_platform,source_external_id,source_label,
                   target_platform,target_external_id,target_user_id,target_label,status,
                   delivery_status,delivery_error,expires_at,decided_at,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,'pending','pending','',?,NULL,?,?)""",
            (
                token, int(source_user_id), source_platform, source_external, source_label[:120],
                target_platform, str(target["external_id"]), target_user_id,
                str(target.get("label") or "")[:120], expires_at, now, now,
            ),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM account_link_requests WHERE token=?", (token,))).fetchone()
    return _request_dict(row)


def _request_dict(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "ok": True,
        "token": str(row["token"]),
        "source_user_id": int(row["source_user_id"]),
        "source_platform": str(row["source_platform"]),
        "source_external_id": str(row["source_external_id"]),
        "source_label": str(row["source_label"] or ""),
        "target_platform": str(row["target_platform"]),
        "target_external_id": str(row["target_external_id"]),
        "target_user_id": int(row["target_user_id"]) if row["target_user_id"] is not None else None,
        "target_label": str(row["target_label"] or ""),
        "status": str(row["status"]),
        "delivery_status": str(row["delivery_status"]),
        "delivery_error": str(row["delivery_error"] or ""),
        "expires_at": str(row["expires_at"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


async def set_link_request_delivery(token: str, *, delivered: bool, error: str = "") -> None:
    await ensure_smart_link_schema()
    now = utc_now()
    async with connect() as db:
        await db.execute(
            """UPDATE account_link_requests
               SET delivery_status=?,delivery_error=?,updated_at=?
               WHERE token=? AND status='pending'""",
            ("sent" if delivered else "failed", str(error or "")[:500], now, str(token)),
        )
        await db.commit()


async def get_source_link_request(source_user_id: int, token: str) -> dict[str, Any] | None:
    await ensure_smart_link_schema()
    now = utc_now()
    async with connect() as db:
        await _expire_requests(db, now)
        await db.commit()
        row = await (
            await db.execute(
                "SELECT * FROM account_link_requests WHERE token=? AND source_user_id=? LIMIT 1",
                (str(token), int(source_user_id)),
            )
        ).fetchone()
    return _request_dict(row) if row else None


async def get_incoming_link_request(*, target_platform: str, target_external_id: int | str) -> dict[str, Any] | None:
    await ensure_smart_link_schema()
    now = utc_now()
    async with connect() as db:
        await _expire_requests(db, now)
        await db.commit()
        row = await (
            await db.execute(
                """SELECT * FROM account_link_requests
                   WHERE target_platform=? AND target_external_id=? AND status='pending'
                   ORDER BY created_at DESC LIMIT 1""",
                (str(target_platform), str(target_external_id)),
            )
        ).fetchone()
    return _request_dict(row) if row else None


async def _copy_secondary_avatar_if_needed(secondary_user_id: int, canonical_user_id: int) -> None:
    if int(secondary_user_id) == int(canonical_user_id):
        return
    try:
        from app.services.profile_avatar import CUSTOM_AVATAR_ROOT, custom_profile_avatar

        source = custom_profile_avatar(int(secondary_user_id))
        destination = CUSTOM_AVATAR_ROOT / f"{int(canonical_user_id)}.webp"
        if not source or destination.is_file():
            return

        def copy_avatar() -> None:
            CUSTOM_AVATAR_ROOT.mkdir(parents=True, exist_ok=True)
            temporary = CUSTOM_AVATAR_ROOT / f".{int(canonical_user_id)}.merge.webp.part"
            try:
                shutil.copy2(source, temporary)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(copy_avatar)
    except Exception:
        logger.exception(
            "Could not preserve custom avatar during account merge secondary=%s canonical=%s",
            int(secondary_user_id), int(canonical_user_id),
        )


async def confirm_smart_link_request(
    *, token: str, target_user_id: int, target_platform: str, target_external_id: int | str,
) -> dict[str, Any]:
    await ensure_smart_link_schema()
    target_platform = str(target_platform or "").strip().lower()
    target_external = str(target_external_id or "").strip()
    now = utc_now()
    canonical_user_id = int(target_user_id)
    secondary_user_id = int(target_user_id)
    result_request: dict[str, Any] = {}
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await _expire_requests(db, now)
        row = await (await db.execute("SELECT * FROM account_link_requests WHERE token=? LIMIT 1", (str(token),))).fetchone()
        if not row or str(row["status"]) != "pending" or str(row["expires_at"]) <= now:
            await db.rollback()
            raise AccountLinkError("Запрос уже завершён или истёк.")
        if str(row["target_platform"]) != target_platform or str(row["target_external_id"]) != target_external:
            await db.rollback()
            raise AccountLinkError("Этот запрос предназначен для другого аккаунта.")

        source_user_id = int(row["source_user_id"])
        source_platform = str(row["source_platform"])
        source_external = str(row["source_external_id"])
        if source_platform == target_platform:
            await db.rollback()
            raise AccountLinkError("Для объединения нужны аккаунты разных платформ.")

        mapped_source = await _identity_user_id(db, source_platform, source_external)
        if mapped_source is not None and mapped_source != source_user_id:
            await db.rollback()
            raise AccountLinkError("Исходный аккаунт уже изменил привязку. Создайте новый запрос.")

        source_other = await _platform_identity(db, source_user_id, target_platform)
        target_other = await _platform_identity(db, int(target_user_id), source_platform)
        if source_other and source_other != target_external:
            await db.rollback()
            raise AccountLinkError("Исходный профиль уже связан с другим аккаунтом.")
        if target_other and target_other != source_external and int(target_user_id) != source_user_id:
            await db.rollback()
            raise AccountLinkError("Подтверждающий профиль уже связан с другим аккаунтом.")

        telegram_user_id = source_user_id if source_platform == "telegram" else int(target_user_id)
        vk_user_id = source_user_id if source_platform == "vk" else int(target_user_id)
        canonical_user_id = telegram_user_id
        secondary_user_id = vk_user_id if canonical_user_id == telegram_user_id else telegram_user_id
        snapshot = {
            "source": await _account_summary(db, source_user_id),
            "target": await _account_summary(db, int(target_user_id)),
        }
        if source_user_id != int(target_user_id):
            await _merge_reader_data(db, secondary_user_id, canonical_user_id)

        # Bind exactly the two identities that both owners have proven. We do
        # not infer ownership from the entered username/id itself.
        for platform, external in (
            (source_platform, source_external),
            (target_platform, target_external),
        ):
            await db.execute(
                """INSERT INTO external_identities(platform,external_id,user_id,created_at,updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(platform,external_id) DO UPDATE SET
                       user_id=excluded.user_id,updated_at=excluded.updated_at""",
                (platform, external, canonical_user_id, now, now),
            )

        await db.execute(
            """UPDATE account_link_requests
               SET target_user_id=?,status='confirmed',decided_at=?,updated_at=?
               WHERE token=? AND status='pending'""",
            (int(target_user_id), now, now, str(token)),
        )
        await db.execute(
            """UPDATE account_link_requests SET status='superseded',decided_at=?,updated_at=?
               WHERE token<>? AND status='pending' AND (
                   source_user_id IN (?,?) OR
                   (target_platform=? AND target_external_id=?) OR
                   (target_platform=? AND target_external_id=?))""",
            (
                now, now, str(token), source_user_id, int(target_user_id),
                source_platform, source_external, target_platform, target_external,
            ),
        )
        await db.execute(
            """INSERT INTO account_merge_events(
                   canonical_user_id,secondary_user_id,strategy,source_platform,current_platform,snapshot_json,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                canonical_user_id, secondary_user_id, "smart_merge",
                source_platform, target_platform, json.dumps(snapshot, ensure_ascii=False), now,
            ),
        )
        await db.commit()
        result_request = _request_dict(row)

    await _copy_secondary_avatar_if_needed(secondary_user_id, canonical_user_id)
    user = await get_user_by_id(canonical_user_id)
    return {
        "ok": True,
        "status": "confirmed",
        "canonical_user_id": canonical_user_id,
        "secondary_user_id": secondary_user_id,
        "telegram_id": int(user["telegram_id"]) if user else 0,
        "source_platform": result_request.get("source_platform", ""),
        "source_external_id": result_request.get("source_external_id", ""),
        "source_label": result_request.get("source_label", ""),
        "target_platform": target_platform,
        "target_external_id": target_external,
    }


async def reject_smart_link_request(*, token: str, target_platform: str, target_external_id: int | str) -> dict[str, Any]:
    await ensure_smart_link_schema()
    now = utc_now()
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        await _expire_requests(db, now)
        row = await (await db.execute("SELECT * FROM account_link_requests WHERE token=? LIMIT 1", (str(token),))).fetchone()
        if not row or str(row["status"]) != "pending":
            await db.rollback()
            raise AccountLinkError("Запрос уже завершён или истёк.")
        if str(row["target_platform"]) != str(target_platform) or str(row["target_external_id"]) != str(target_external_id):
            await db.rollback()
            raise AccountLinkError("Этот запрос предназначен для другого аккаунта.")
        await db.execute(
            "UPDATE account_link_requests SET status='rejected',decided_at=?,updated_at=? WHERE token=?",
            (now, now, str(token)),
        )
        await db.commit()
        request = _request_dict(row)
    return {"ok": True, "status": "rejected", **request}


async def cancel_smart_link_request(*, token: str, source_user_id: int) -> dict[str, Any]:
    await ensure_smart_link_schema()
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """UPDATE account_link_requests SET status='cancelled',decided_at=?,updated_at=?
               WHERE token=? AND source_user_id=? AND status='pending'""",
            (now, now, str(token), int(source_user_id)),
        )
        await db.commit()
        if cur.rowcount <= 0:
            raise AccountLinkError("Активный запрос не найден.")
    return {"ok": True, "status": "cancelled"}
