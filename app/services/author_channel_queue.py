from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.db import connect, get_setting, set_setting, utc_now


async def ensure_author_channel_queue_schema() -> None:
    async with connect() as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS author_channel_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL UNIQUE,
                author_id INTEGER,
                actor_user_id INTEGER,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                last_error TEXT
            )"""
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_author_channel_queue_due ON author_channel_queue(status, next_attempt_at)"
        )
        await db.commit()


async def get_author_channel_settings() -> dict[str, int]:
    return {
        "enabled": 1 if str(await get_setting("author_channel_auto_post", "1")) == "1" else 0,
        "interval_minutes": max(1, min(10080, int(await get_setting("author_channel_interval_minutes", "30") or 30))),
        "posts_per_run": max(1, min(20, int(await get_setting("author_channel_posts_per_run", "2") or 2))),
    }


async def update_author_channel_settings(*, enabled: int | None = None, interval_minutes: int | None = None, posts_per_run: int | None = None) -> dict[str, int]:
    current = await get_author_channel_settings()
    if enabled is not None:
        current["enabled"] = 1 if enabled else 0
        await set_setting("author_channel_auto_post", str(current["enabled"]))
    if interval_minutes is not None:
        current["interval_minutes"] = max(1, min(10080, int(interval_minutes)))
        await set_setting("author_channel_interval_minutes", str(current["interval_minutes"]))
        await ensure_author_channel_queue_schema()
        next_run = (
            datetime.now(timezone.utc) + timedelta(minutes=current["interval_minutes"])
        ).replace(microsecond=0).isoformat()
        async with connect() as db:
            await db.execute(
                "UPDATE author_channel_queue SET next_attempt_at=? WHERE status='queued'",
                (next_run,),
            )
            await db.commit()
    if posts_per_run is not None:
        current["posts_per_run"] = max(1, min(20, int(posts_per_run)))
        await set_setting("author_channel_posts_per_run", str(current["posts_per_run"]))
    return current


async def enqueue_author_channel_post(book_id: int, *, author_id: int | None, actor_user_id: int | None) -> bool:
    await ensure_author_channel_queue_schema()
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """INSERT OR IGNORE INTO author_channel_queue(
                book_id, author_id, actor_user_id, status, attempts, next_attempt_at, created_at
            ) VALUES(?, ?, ?, 'queued', 0, ?, ?)""",
            (int(book_id), int(author_id) if author_id is not None else None, actor_user_id, now, now),
        )
        await db.commit()
        return int(cur.rowcount or 0) > 0


async def get_author_channel_status() -> dict[str, int]:
    await ensure_author_channel_queue_schema()
    settings = await get_author_channel_settings()
    async with connect() as db:
        counts = {}
        for status in ("queued", "sent", "failed"):
            cur = await db.execute("SELECT COUNT(*) FROM author_channel_queue WHERE status=?", (status,))
            counts[status] = int((await cur.fetchone())[0] or 0)
    return {**settings, **counts}


async def retry_failed_author_posts() -> int:
    await ensure_author_channel_queue_schema()
    async with connect() as db:
        cur = await db.execute(
            "UPDATE author_channel_queue SET status='queued', attempts=0, next_attempt_at=?, last_error=NULL WHERE status='failed'",
            (utc_now(),),
        )
        await db.commit()
        return int(cur.rowcount or 0)


async def process_author_channel_queue(bot) -> int:
    from app.services.publication import post_book_to_channel

    await ensure_author_channel_queue_schema()
    cfg = await get_author_channel_settings()
    if not cfg["enabled"]:
        return 0
    now = datetime.now(timezone.utc)
    now_text = now.replace(microsecond=0).isoformat()
    next_run = (now + timedelta(minutes=cfg["interval_minutes"])).replace(microsecond=0).isoformat()
    async with connect() as db:
        # Round-robin по авторам: сначала самая старая публикация каждого автора.
        cur = await db.execute(
            """SELECT q.id, q.book_id, q.actor_user_id
               FROM author_channel_queue q
               WHERE q.status='queued' AND q.next_attempt_at<=?
                 AND q.id=(SELECT MIN(q2.id) FROM author_channel_queue q2
                           WHERE q2.status='queued' AND q2.next_attempt_at<=?
                             AND COALESCE(q2.author_id, -q2.book_id)=COALESCE(q.author_id, -q.book_id))
               ORDER BY q.id LIMIT ?""",
            (now_text, now_text, cfg["posts_per_run"]),
        )
        rows = await cur.fetchall()
    if not rows:
        return 0
    sent = 0
    for row in rows:
        queue_id, book_id = int(row["id"]), int(row["book_id"])
        actor_id = int(row["actor_user_id"]) if row["actor_user_id"] is not None else None
        try:
            result = await post_book_to_channel(bot, book_id, actor_user_id=actor_id, force=False)
            if result.channel_status in {"sent", "already_sent"}:
                async with connect() as db:
                    await db.execute("UPDATE author_channel_queue SET status='sent', sent_at=?, last_error=NULL WHERE id=?", (utc_now(), queue_id))
                    await db.commit()
                sent += 1
            elif result.channel_status == "not_configured":
                async with connect() as db:
                    await db.execute("UPDATE author_channel_queue SET next_attempt_at=?, last_error=? WHERE id=?", (next_run, "CHANNEL_ID не настроен", queue_id))
                    await db.commit()
            else:
                raise RuntimeError(result.channel_error or result.channel_status)
        except Exception as exc:
            async with connect() as db:
                cur = await db.execute("SELECT attempts FROM author_channel_queue WHERE id=?", (queue_id,))
                attempts = int((await cur.fetchone())[0] or 0) + 1
                status = "failed" if attempts >= 5 else "queued"
                await db.execute("UPDATE author_channel_queue SET status=?, attempts=?, next_attempt_at=?, last_error=? WHERE id=?", (status, attempts, next_run, str(exc)[:1000], queue_id))
                await db.commit()
    async with connect() as db:
        await db.execute("UPDATE author_channel_queue SET next_attempt_at=? WHERE status='queued' AND next_attempt_at<=?", (next_run, now_text))
        await db.commit()
    return sent


async def author_channel_scheduler_loop(bot) -> None:
    while True:
        try:
            await process_author_channel_queue(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(30)
