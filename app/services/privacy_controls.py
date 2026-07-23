from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import connect, utc_now
from app.services.security import hash_confirmation_token, make_confirmation_token


_EXPORT_VERSION = "1.0"


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [{key: row[key] for key in row.keys()} for row in rows]


async def _fetch_all(db, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur = await db.execute(sql, params)
    return _rows(await cur.fetchall())


def _masked_reference(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    return f"…{text[-8:]}" if len(text) > 8 else "[скрыто]"


async def get_privacy_overview(user_id: int) -> dict[str, Any]:
    async with connect() as db:
        cur = await db.execute("SELECT * FROM users WHERE id=?", (int(user_id),))
        user = await cur.fetchone()
        if not user:
            raise ValueError("Пользователь не найден.")
        counts: dict[str, int] = {}
        for key, table in {
            "purchases": "purchases",
            "bookmarks": "bookmarks",
            "reading_progress": "reading_progress",
            "listening_progress": "listening_progress",
            "graphic_progress": "graphic_reading_progress",
            "annotations": "reader_annotations",
            "journal": "reader_book_journal",
            "comments": "comments",
            "reviews": "reviews",
            "legal_acceptances": "legal_acceptances",
        }.items():
            cur = await db.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE user_id=?", (int(user_id),))
            counts[key] = int((await cur.fetchone())["count"])
        cur = await db.execute(
            "SELECT id, request_type, status, requested_at, confirmed_at, completed_at, expires_at, result_json "
            "FROM user_data_requests WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (int(user_id),),
        )
        requests = _rows(await cur.fetchall())
        for item in requests:
            try:
                item["result"] = json.loads(str(item.pop("result_json") or "{}"))
            except json.JSONDecodeError:
                item["result"] = {}
        return {
            "account": {
                "status": str(user["account_status"] or "active"),
                "created_at": user["created_at"],
                "deleted_at": user["deleted_at"],
            },
            "counts": counts,
            "requests": requests,
            "export_format": _EXPORT_VERSION,
            "retention_note": (
                "Покупки, возвраты, выплаты, акцепты документов и события безопасности могут сохраняться "
                "после удаления профиля в обязательном минимальном объёме."
            ),
        }


async def build_full_privacy_export(user_id: int) -> dict[str, Any]:
    generated_at = utc_now()
    async with connect() as db:
        cur = await db.execute(
            "SELECT id, telegram_id, username, full_name, account_status, created_at, updated_at, deleted_at "
            "FROM users WHERE id=?",
            (int(user_id),),
        )
        user = await cur.fetchone()
        if not user:
            raise ValueError("Пользователь не найден.")
        cur = await db.execute(
            "SELECT pen_name, bio, country, is_adult, status, trust_level, created_at, updated_at "
            "FROM author_profiles WHERE user_id=?",
            (int(user_id),),
        )
        author = await cur.fetchone()
        cur = await db.execute(
            "SELECT afp.legal_status, afp.country, afp.verification_status, afp.verified_at, afp.created_at, afp.updated_at "
            "FROM author_financial_profiles afp JOIN author_profiles ap ON ap.id=afp.author_id WHERE ap.user_id=?",
            (int(user_id),),
        )
        author_finance = await cur.fetchone()

        legal = await _fetch_all(db,
            "SELECT doc_code, doc_version, accepted_at, doc_hash, acceptance_source, withdrawn_at "
            "FROM legal_acceptances WHERE user_id=? ORDER BY accepted_at", (int(user_id),))
        preferences = await _fetch_all(db, "SELECT * FROM user_preferences WHERE user_id=?", (int(user_id),))
        bookmarks = await _fetch_all(db,
            "SELECT b.title, bm.status, bm.created_at, bm.updated_at FROM bookmarks bm "
            "JOIN books b ON b.id=bm.book_id WHERE bm.user_id=? ORDER BY bm.updated_at", (int(user_id),))
        reading = await _fetch_all(db,
            "SELECT b.title, c.number AS chapter_number, rp.position_percent, rp.updated_at "
            "FROM reading_progress rp JOIN books b ON b.id=rp.book_id JOIN chapters c ON c.id=rp.chapter_id "
            "WHERE rp.user_id=? ORDER BY rp.updated_at", (int(user_id),))
        listening = await _fetch_all(db,
            "SELECT b.title, ac.number AS chapter_number, lp.position_seconds, lp.updated_at "
            "FROM listening_progress lp JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id "
            "JOIN books b ON b.id=ac.book_id WHERE lp.user_id=? ORDER BY lp.updated_at", (int(user_id),))
        graphics = await _fetch_all(db,
            "SELECT b.title, gc.number AS chapter_number, grp.page_number, grp.updated_at "
            "FROM graphic_reading_progress grp JOIN graphic_chapters gc ON gc.id=grp.graphic_chapter_id "
            "JOIN books b ON b.id=gc.book_id WHERE grp.user_id=? ORDER BY grp.updated_at", (int(user_id),))
        annotations = await _fetch_all(db,
            "SELECT b.title, c.number AS chapter_number, ra.annotation_type, ra.selected_text, ra.note_text, "
            "ra.color, ra.created_at, ra.updated_at FROM reader_annotations ra "
            "JOIN books b ON b.id=ra.book_id LEFT JOIN chapters c ON c.id=ra.chapter_id "
            "WHERE ra.user_id=? ORDER BY ra.created_at", (int(user_id),))
        journal = await _fetch_all(db,
            "SELECT b.title, rbj.status, rbj.started_on, rbj.finished_on, rbj.impression, "
            "rbj.private_rating, rbj.created_at, rbj.updated_at FROM reader_book_journal rbj "
            "JOIN books b ON b.id=rbj.book_id WHERE rbj.user_id=? ORDER BY rbj.updated_at", (int(user_id),))
        cycles = await _fetch_all(db,
            "SELECT b.title, rbc.cycle_number, rbc.status, rbc.started_on, rbc.finished_on, rbc.note, "
            "rbc.created_at, rbc.updated_at FROM reader_book_cycles rbc JOIN books b ON b.id=rbc.book_id "
            "WHERE rbc.user_id=? ORDER BY b.title, rbc.cycle_number", (int(user_id),))
        reviews = await _fetch_all(db,
            "SELECT b.title, r.rating, r.text, r.status, r.created_at, r.updated_at FROM reviews r "
            "JOIN books b ON b.id=r.book_id WHERE r.user_id=? ORDER BY r.created_at", (int(user_id),))
        comments = await _fetch_all(db,
            "SELECT b.title, c.number AS chapter_number, cm.text, cm.status, cm.created_at, cm.updated_at "
            "FROM comments cm JOIN books b ON b.id=cm.book_id JOIN chapters c ON c.id=cm.chapter_id "
            "WHERE cm.user_id=? ORDER BY cm.created_at", (int(user_id),))
        complaints = await _fetch_all(db,
            "SELECT target_type, target_id, reason, status, created_at, updated_at FROM complaints "
            "WHERE user_id=? ORDER BY created_at", (int(user_id),))
        achievements = await _fetch_all(db,
            "SELECT achievement_code, progress_value, awarded_at FROM user_achievements WHERE user_id=? ORDER BY awarded_at", (int(user_id),))
        achievement_showcase = await _fetch_all(db,
            "SELECT position, achievement_code, updated_at FROM achievement_showcase WHERE user_id=? ORDER BY position", (int(user_id),))
        purchases = await _fetch_all(db,
            "SELECT p.id, p.purchase_kind, p.amount_stars, p.original_amount_stars, p.wallet_stars_used, "
            "p.bonus_points_used, p.funding_method, p.status, p.telegram_payment_charge_id, p.created_at, "
            "b.title AS book_title, c.number AS chapter_number, ac.number AS audio_chapter_number, "
            "gc.number AS graphic_chapter_number, p.graphic_volume_number "
            "FROM purchases p LEFT JOIN books b ON b.id=p.book_id LEFT JOIN chapters c ON c.id=p.chapter_id "
            "LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id "
            "LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id WHERE p.user_id=? ORDER BY p.created_at", (int(user_id),))
        for item in purchases:
            item["payment_reference"] = _masked_reference(item.pop("telegram_payment_charge_id", ""))
        premium = await _fetch_all(db,
            "SELECT plan_code, status, started_at, expires_at, is_recurring, auto_renew, canceled_at, source, "
            "created_at, updated_at FROM premium_subscriptions WHERE user_id=? ORDER BY created_at", (int(user_id),))
        shelves = await _fetch_all(db,
            "SELECT us.name, us.icon, b.title, usb.created_at FROM user_shelves us "
            "LEFT JOIN user_shelf_books usb ON usb.shelf_id=us.id LEFT JOIN books b ON b.id=usb.book_id "
            "WHERE us.user_id=? ORDER BY us.position, us.id, usb.position, usb.created_at", (int(user_id),))

    return {
        "format": "voxlyra-personal-data-export",
        "version": _EXPORT_VERSION,
        "generated_at": generated_at,
        "scope": "Данные принадлежат только владельцу Telegram-профиля, запросившему экспорт.",
        "account": {key: user[key] for key in user.keys()},
        "author_profile": ({key: author[key] for key in author.keys()} if author else None),
        "author_finance_status": ({key: author_finance[key] for key in author_finance.keys()} if author_finance else None),
        "legal_acceptances": legal,
        "preferences": preferences[0] if preferences else {},
        "library": {"bookmarks": bookmarks, "shelves": shelves},
        "progress": {"reading": reading, "listening": listening, "graphics": graphics},
        "private_materials": {"annotations": annotations, "journal": journal, "cycles": cycles},
        "public_activity": {"reviews": reviews, "comments": comments},
        "support": {"complaints": complaints},
        "achievements": {"earned": achievements, "showcase": achievement_showcase},
        "payments": {"purchases": purchases, "premium": premium},
        "excluded": [
            "BOT_TOKEN и ключи шифрования", "полные платёжные charge ID", "внутренние файловые пути",
            "данные других пользователей", "служебные резервные копии сервера",
        ],
    }


async def create_deletion_preview(user_id: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=30)
    token = make_confirmation_token()
    async with connect() as db:
        cur = await db.execute("SELECT telegram_id, account_status FROM users WHERE id=?", (int(user_id),))
        user = await cur.fetchone()
        if not user:
            raise ValueError("Пользователь не найден.")
        if str(user["account_status"] or "active") == "deleted":
            raise ValueError("Профиль уже удалён.")
        cur = await db.execute("SELECT is_active FROM admin_staff WHERE user_id=?", (int(user_id),))
        staff = await cur.fetchone()
        cur = await db.execute(
            "SELECT ap.id, ap.status, COUNT(b.id) AS books_count FROM author_profiles ap "
            "LEFT JOIN books b ON b.author_id=ap.id AND b.publication_status!='deleted' "
            "WHERE ap.user_id=? GROUP BY ap.id", (int(user_id),))
        author = await cur.fetchone()
        blockers: list[str] = []
        if staff and int(staff["is_active"] or 0) == 1:
            blockers.append("Активная роль сотрудника должна быть снята владельцем.")
        if author and int(author["books_count"] or 0) > 0:
            blockers.append("У профиля автора есть произведения. Сначала передайте права или обратитесь в поддержку.")
        cur = await db.execute(
            "SELECT COUNT(*) AS count FROM refund_requests rr JOIN purchases p ON p.id=rr.purchase_id "
            "WHERE p.user_id=? AND rr.status IN ('new','review','approved')", (int(user_id),))
        if int((await cur.fetchone())["count"] or 0) > 0:
            blockers.append("Есть незавершённый запрос возврата.")
        cur = await db.execute(
            "SELECT COUNT(*) AS count FROM premium_subscriptions "
            "WHERE user_id=? AND status='active' AND auto_renew=1", (int(user_id),))
        if int((await cur.fetchone())["count"] or 0) > 0:
            blockers.append("Сначала отключите автопродление активной Premium-подписки.")
        cur = await db.execute(
            "INSERT INTO user_data_requests(user_id, request_type, status, confirmation_hash, requested_at, expires_at, result_json) "
            "VALUES(?, 'delete', 'preview', ?, ?, ?, ?)",
            (int(user_id), hash_confirmation_token(token), now.isoformat(), expires.isoformat(), json.dumps({"blockers": blockers}, ensure_ascii=False)),
        )
        request_id = int(cur.lastrowid)
        await db.commit()
    return {
        "request_id": request_id,
        "confirmation_token": token,
        "expires_at": expires.isoformat(),
        "can_delete_now": not blockers,
        "blockers": blockers,
        "will_delete": [
            "имя и username в профиле", "настройки и уведомления", "библиотека, прогресс, история, заметки и дневник",
            "отзывы и комментарии", "персональные рекомендации и достижения",
        ],
        "will_keep_minimized": [
            "покупки и возвраты", "юридически значимые согласия", "события безопасности и финансовый учёт",
        ],
    }


async def confirm_account_deletion(user_id: int, request_id: int, token: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT * FROM user_data_requests WHERE id=? AND user_id=? AND request_type='delete'",
            (int(request_id), int(user_id)),
        )
        request = await cur.fetchone()
        if not request or str(request["status"]) != "preview":
            await db.rollback()
            raise ValueError("Подтверждение уже использовано или не найдено.")
        try:
            expires = datetime.fromisoformat(str(request["expires_at"]).replace("Z", "+00:00"))
        except ValueError:
            expires = now - timedelta(seconds=1)
        if expires < now or hash_confirmation_token(token) != str(request["confirmation_hash"]):
            await db.rollback()
            raise ValueError("Подтверждение истекло или не прошло проверку.")
        result = json.loads(str(request["result_json"] or "{}"))
        blockers = list(result.get("blockers") or [])
        if blockers:
            await db.rollback()
            raise ValueError("Автоматическое удаление недоступно: " + " ".join(blockers))

        tables = [
            "user_preferences", "bookmarks", "book_subscriptions", "author_subscriptions",
            "reading_progress", "listening_progress", "graphic_reading_progress", "tts_progress",
            "progress_sync_state", "reading_history", "reader_annotations", "reader_book_cycles",
            "reader_book_journal", "reader_year_list_items", "reader_activity_daily",
            "reader_activity_targets", "reader_goal_settings", "reader_notification_settings",
            "smart_notification_state", "recommendation_events", "achievement_showcase", "user_achievements",
            "chapter_reactions", "comment_likes", "graphic_page_bookmarks", "graphic_reading_events",
            "notification_deliveries", "premium_content_events", "reader_ad_events",
            "reader_journal_import_previews", "reader_journal_import_runs",
            "graphic_page_comments", "ad_campaign_events",
        ]
        deleted_counts: dict[str, int] = {}
        for table in tables:
            cur = await db.execute(f"DELETE FROM {table} WHERE user_id=?", (int(user_id),))
            if cur.rowcount:
                deleted_counts[table] = int(cur.rowcount)
        # Shelves cascade to their items.
        cur = await db.execute("DELETE FROM user_shelves WHERE user_id=?", (int(user_id),))
        if cur.rowcount:
            deleted_counts["user_shelves"] = int(cur.rowcount)
        # Public messages are removed, while complaints and finance records remain for disputes.
        for table in ("comments", "reviews"):
            cur = await db.execute(f"DELETE FROM {table} WHERE user_id=?", (int(user_id),))
            if cur.rowcount:
                deleted_counts[table] = int(cur.rowcount)
        cur = await db.execute("SELECT id FROM author_profiles WHERE user_id=?", (int(user_id),))
        author = await cur.fetchone()
        if author:
            await db.execute("DELETE FROM author_profiles WHERE id=?", (int(author["id"]),))
            deleted_counts["author_profiles"] = 1
        deleted_at = now.isoformat()
        await db.execute(
            "UPDATE users SET username=NULL, full_name='Удалённый пользователь', is_blocked=1, "
            "account_status='deleted', deleted_at=?, updated_at=? WHERE id=?",
            (deleted_at, deleted_at, int(user_id)),
        )
        final_result = {"deleted_counts": deleted_counts, "retained": ["payments", "legal", "security"], "deleted_at": deleted_at}
        await db.execute(
            "UPDATE user_data_requests SET status='completed', confirmed_at=?, completed_at=?, result_json=? WHERE id=?",
            (deleted_at, deleted_at, json.dumps(final_result, ensure_ascii=False), int(request_id)),
        )
        await db.commit()
        return final_result
