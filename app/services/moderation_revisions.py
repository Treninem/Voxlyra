from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from app.db import connect, get_book, list_chapters_for_book, list_graphic_chapters_for_book, utc_now


@dataclass(slots=True)
class RevisionChangeSet:
    request_id: int | None
    baseline_snapshot_id: int | None
    has_baseline: bool
    changed_scopes: set[tuple[str, str]]
    deleted_scopes: set[tuple[str, str]]
    changed_metadata_fields: set[str]
    changed_text_ids: set[int]
    changed_graphic_ids: set[int]
    summary: dict[str, int]
    requires_manual_confirmation: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_scopes or self.deleted_scopes)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS book_moderation_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    snapshot_kind TEXT NOT NULL,
    actor_user_id INTEGER,
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_book_moderation_snapshots_book
    ON book_moderation_snapshots(book_id, id DESC);

CREATE TABLE IF NOT EXISTS book_moderation_snapshot_items (
    snapshot_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER,
    field_name TEXT NOT NULL DEFAULT '',
    item_number INTEGER,
    title TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    updated_at TEXT,
    PRIMARY KEY(snapshot_id, item_key),
    FOREIGN KEY(snapshot_id) REFERENCES book_moderation_snapshots(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_book_moderation_snapshot_items_source
    ON book_moderation_snapshot_items(snapshot_id, source_type, source_id);

CREATE TABLE IF NOT EXISTS book_revision_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL,
    baseline_snapshot_id INTEGER NOT NULL,
    actor_user_id INTEGER,
    reason TEXT NOT NULL,
    finding_ids_json TEXT NOT NULL DEFAULT '[]',
    requires_manual_confirmation INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resubmitted_at TEXT,
    resolved_at TEXT,
    resolution TEXT,
    FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
    FOREIGN KEY(baseline_snapshot_id) REFERENCES book_moderation_snapshots(id) ON DELETE CASCADE,
    FOREIGN KEY(actor_user_id) REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_book_revision_requests_book
    ON book_revision_requests(book_id, status, id DESC);
"""


async def ensure_moderation_revision_schema() -> None:
    async with connect() as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _current_items(book_id: int) -> dict[str, dict[str, Any]]:
    book = await get_book(int(book_id))
    if not book:
        return {}
    items: dict[str, dict[str, Any]] = {}

    metadata = {
        "title": str(book["title"] or ""),
        "description": str(book["description"] or ""),
        "age_limit": str(book["age_limit"] or "0+"),
        "cover": {
            "file_id": bool(str(book["cover_file_id"] or "").strip()),
            "path": bool(str(book["cover_path"] or "").strip()),
        },
        "content_type": str(book["content_type"] or "book"),
        "license": {
            "license_type": str(book["license_type"] or ""),
            "source_name": str(book["source_name"] or ""),
            "rights_checked": int(book["rights_checked"] or 0),
        },
    }
    for field_name, value in metadata.items():
        key = f"metadata:{field_name}"
        items[key] = {
            "item_key": key,
            "source_type": "metadata",
            "source_id": None,
            "field_name": field_name,
            "item_number": None,
            "title": field_name,
            "content_hash": _hash(value),
            "updated_at": str(book["updated_at"] or ""),
        }

    chapters = await list_chapters_for_book(int(book_id), published_only=False)
    for row in chapters:
        if str(row["status"] or "") == "deleted":
            continue
        chapter_id = int(row["id"])
        key = f"text:{chapter_id}"
        payload = {
            "number": int(row["number"] or 0),
            "title": str(row["title"] or ""),
            "text": str(row["text"] or ""),
            "status": str(row["status"] or ""),
        }
        items[key] = {
            "item_key": key,
            "source_type": "text",
            "source_id": chapter_id,
            "field_name": "",
            "item_number": int(row["number"] or 0),
            "title": str(row["title"] or ""),
            "content_hash": _hash(payload),
            "updated_at": str(row["updated_at"] or ""),
        }

    graphics = await list_graphic_chapters_for_book(int(book_id), published_only=False)
    async with connect() as db:
        for row in graphics:
            if str(row["status"] or "") == "deleted":
                continue
            graphic_id = int(row["id"])
            cur = await db.execute(
                """
                SELECT page_number, COALESCE(checksum,''), COALESCE(file_size,0),
                       COALESCE(width,0), COALESCE(height,0), COALESCE(updated_at,'')
                FROM graphic_pages
                WHERE graphic_chapter_id=?
                ORDER BY page_number, id
                """,
                (graphic_id,),
            )
            pages = [tuple(item) for item in await cur.fetchall()]
            key = f"graphic:{graphic_id}"
            payload = {
                "number": int(row["number"] or 0),
                "title": str(row["title"] or ""),
                "reading_mode": str(row["reading_mode"] or ""),
                "status": str(row["status"] or ""),
                "pages": pages,
            }
            items[key] = {
                "item_key": key,
                "source_type": "graphic",
                "source_id": graphic_id,
                "field_name": "",
                "item_number": int(row["number"] or 0),
                "title": str(row["title"] or ""),
                "content_hash": _hash(payload),
                "updated_at": str(row["updated_at"] or ""),
            }
    return items


async def capture_moderation_snapshot(
    book_id: int,
    *,
    snapshot_kind: str,
    actor_user_id: int | None = None,
    source: str = "",
) -> int:
    await ensure_moderation_revision_schema()
    items = await _current_items(int(book_id))
    now = utc_now()
    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO book_moderation_snapshots(book_id, snapshot_kind, actor_user_id, source, created_at)
            VALUES(?,?,?,?,?)
            """,
            (int(book_id), str(snapshot_kind)[:40], actor_user_id, str(source)[:120], now),
        )
        snapshot_id = int(cur.lastrowid)
        for item in items.values():
            await db.execute(
                """
                INSERT INTO book_moderation_snapshot_items(
                    snapshot_id, item_key, source_type, source_id, field_name,
                    item_number, title, content_hash, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    snapshot_id,
                    item["item_key"],
                    item["source_type"],
                    item["source_id"],
                    item["field_name"],
                    item["item_number"],
                    item["title"],
                    item["content_hash"],
                    item["updated_at"],
                ),
            )
        await db.commit()
    return snapshot_id


async def create_revision_request(
    book_id: int,
    *,
    actor_user_id: int | None,
    reason: str,
    finding_ids: Iterable[int] = (),
    requires_manual_confirmation: bool = False,
    source: str = "moderation",
) -> int:
    await ensure_moderation_revision_schema()
    baseline_id = await capture_moderation_snapshot(
        int(book_id), snapshot_kind="revision_baseline", actor_user_id=actor_user_id, source=source
    )
    requested_ids: set[int] = set()
    for value in finding_ids:
        try:
            finding_id = int(value)
        except (TypeError, ValueError):
            continue
        if finding_id > 0:
            requested_ids.add(finding_id)
    now = utc_now()
    async with connect() as db:
        # Only real open findings of this book may become revision requirements.
        # A moderator's unchecked findings are treated as reviewed/accepted and
        # are resolved, so they cannot silently block the author's resubmission.
        clean_ids: list[int] = []
        try:
            cur = await db.execute(
                "SELECT id FROM book_moderation_findings WHERE book_id=? AND status='open'",
                (int(book_id),),
            )
            open_ids = {int(row[0]) for row in await cur.fetchall()}
            clean_ids = sorted(open_ids & requested_ids)
        except Exception:
            clean_ids = []

        await db.execute(
            "UPDATE book_revision_requests SET status='superseded', resolved_at=?, resolution='superseded' WHERE book_id=? AND status IN ('open','resubmitted')",
            (now, int(book_id)),
        )
        cur = await db.execute(
            """
            INSERT INTO book_revision_requests(
                book_id, baseline_snapshot_id, actor_user_id, reason, finding_ids_json,
                requires_manual_confirmation, status, created_at
            ) VALUES(?,?,?,?,?,?, 'open', ?)
            """,
            (
                int(book_id), baseline_id, actor_user_id, str(reason)[:8000],
                json.dumps(clean_ids, ensure_ascii=False), 1 if requires_manual_confirmation else 0, now,
            ),
        )
        request_id = int(cur.lastrowid)
        try:
            await db.execute(
                """
                UPDATE book_moderation_findings
                SET selected_for_revision=0, revision_request_id=NULL
                WHERE book_id=? AND status='open'
                """,
                (int(book_id),),
            )
            if clean_ids:
                placeholders = ",".join("?" for _ in clean_ids)
                await db.execute(
                    f"""
                    UPDATE book_moderation_findings
                    SET selected_for_revision=1, revision_request_id=?
                    WHERE book_id=? AND status='open' AND id IN ({placeholders})
                    """,
                    (request_id, int(book_id), *clean_ids),
                )
                await db.execute(
                    f"""
                    UPDATE book_moderation_findings
                    SET status='resolved', resolved_at=?
                    WHERE book_id=? AND status='open' AND id NOT IN ({placeholders})
                    """,
                    (now, int(book_id), *clean_ids),
                )
            else:
                await db.execute(
                    "UPDATE book_moderation_findings SET status='resolved', resolved_at=? WHERE book_id=? AND status='open'",
                    (now, int(book_id)),
                )
        except Exception:
            # Older installations can create findings lazily; the revision
            # request itself remains valid even when there are no finding rows.
            pass
        await db.commit()
    return request_id


async def get_open_revision_request(book_id: int) -> Any | None:
    await ensure_moderation_revision_schema()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT * FROM book_revision_requests
            WHERE book_id=? AND status IN ('open','resubmitted')
            ORDER BY id DESC LIMIT 1
            """,
            (int(book_id),),
        )
        return await cur.fetchone()


async def mark_revision_resubmitted(book_id: int) -> None:
    await ensure_moderation_revision_schema()
    async with connect() as db:
        await db.execute(
            "UPDATE book_revision_requests SET status='resubmitted', resubmitted_at=? WHERE book_id=? AND status='open'",
            (utc_now(), int(book_id)),
        )
        await db.commit()


async def resolve_revision_request(book_id: int, resolution: str) -> None:
    await ensure_moderation_revision_schema()
    async with connect() as db:
        await db.execute(
            """
            UPDATE book_revision_requests
            SET status='resolved', resolved_at=?, resolution=?
            WHERE book_id=? AND status IN ('open','resubmitted')
            """,
            (utc_now(), str(resolution)[:64], int(book_id)),
        )
        await db.commit()


async def _snapshot_items(snapshot_id: int) -> dict[str, dict[str, Any]]:
    async with connect() as db:
        cur = await db.execute(
            "SELECT * FROM book_moderation_snapshot_items WHERE snapshot_id=?",
            (int(snapshot_id),),
        )
        return {str(row["item_key"]): dict(row) for row in await cur.fetchall()}


async def get_revision_change_set(book_id: int) -> RevisionChangeSet:
    request = await get_open_revision_request(int(book_id))
    if not request:
        return RevisionChangeSet(None, None, False, set(), set(), set(), set(), set(), {})
    baseline_id = int(request["baseline_snapshot_id"])
    before = await _snapshot_items(baseline_id)
    after = await _current_items(int(book_id))
    changed: set[tuple[str, str]] = set()
    deleted: set[tuple[str, str]] = set()
    metadata_fields: set[str] = set()
    text_ids: set[int] = set()
    graphic_ids: set[int] = set()

    for key, current in after.items():
        previous = before.get(key)
        if previous and str(previous["content_hash"]) == str(current["content_hash"]):
            continue
        source_type = str(current["source_type"])
        identifier = str(current["field_name"] if source_type == "metadata" else current["source_id"])
        changed.add((source_type, identifier))
        if source_type == "metadata":
            metadata_fields.add(identifier)
        elif source_type == "text":
            text_ids.add(int(current["source_id"]))
        elif source_type == "graphic":
            graphic_ids.add(int(current["source_id"]))

    for key, previous in before.items():
        if key in after:
            continue
        source_type = str(previous["source_type"])
        identifier = str(previous["field_name"] if source_type == "metadata" else previous["source_id"])
        deleted.add((source_type, identifier))
        if source_type == "metadata":
            metadata_fields.add(identifier)
        elif source_type == "text" and previous["source_id"] is not None:
            text_ids.add(int(previous["source_id"]))
        elif source_type == "graphic" and previous["source_id"] is not None:
            graphic_ids.add(int(previous["source_id"]))

    summary = {
        "metadata": len(metadata_fields),
        "text_chapters": len(text_ids),
        "graphic_chapters": len(graphic_ids),
        "deleted": len(deleted),
        "total": len(changed) + len(deleted),
    }
    return RevisionChangeSet(
        request_id=int(request["id"]),
        baseline_snapshot_id=baseline_id,
        has_baseline=True,
        changed_scopes=changed,
        deleted_scopes=deleted,
        changed_metadata_fields=metadata_fields,
        changed_text_ids=text_ids,
        changed_graphic_ids=graphic_ids,
        summary=summary,
        requires_manual_confirmation=bool(request["requires_manual_confirmation"]),
    )


async def list_revision_history(book_id: int, limit: int = 20) -> list[Any]:
    await ensure_moderation_revision_schema()
    async with connect() as db:
        cur = await db.execute(
            """
            SELECT r.*, u.full_name AS actor_name, u.username AS actor_username
            FROM book_revision_requests r
            LEFT JOIN users u ON u.id=r.actor_user_id
            WHERE r.book_id=?
            ORDER BY r.id DESC LIMIT ?
            """,
            (int(book_id), max(1, min(100, int(limit)))),
        )
        return await cur.fetchall()


async def get_author_moderation_state(book_id: int) -> dict[str, Any]:
    await ensure_moderation_revision_schema()
    request = await get_open_revision_request(int(book_id))
    change_set = await get_revision_change_set(int(book_id)) if request else None
    async with connect() as db:
        cur = await db.execute("SELECT * FROM book_moderation_queue WHERE book_id=?", (int(book_id),))
        queue = await cur.fetchone()
        try:
            cur = await db.execute(
                """
                SELECT id, source_type, source_id, field_name, chapter_id, chapter_number,
                       chapter_title, category, severity, reason, matched_text, context,
                       character_offset, line_number, selected_for_revision, created_at
                FROM book_moderation_findings
                WHERE book_id=? AND status='open'
                ORDER BY selected_for_revision DESC,
                         CASE severity WHEN 'block' THEN 0 ELSE 1 END,
                         chapter_number, character_offset, id
                LIMIT 500
                """,
                (int(book_id),),
            )
            findings = [dict(row) for row in await cur.fetchall()]
        except Exception:
            findings = []
    return {
        "queue": dict(queue) if queue else None,
        "revision": dict(request) if request else None,
        "changes": change_set.summary if change_set else None,
        "has_changes": bool(change_set and change_set.has_changes),
        "findings": findings,
    }


async def get_findings_for_revision_reason(book_id: int, finding_ids: Iterable[int]) -> list[dict[str, Any]]:
    await ensure_moderation_revision_schema()
    ids = sorted({int(value) for value in finding_ids if int(value) > 0})
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT * FROM book_moderation_findings
            WHERE book_id=? AND status='open' AND id IN ({placeholders})
            ORDER BY CASE severity WHEN 'block' THEN 0 ELSE 1 END, chapter_number, character_offset
            """,
            (int(book_id), *ids),
        )
        return [dict(row) for row in await cur.fetchall()]


def format_structured_revision_reason(manual_reason: str, findings: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    clean_manual = str(manual_reason or "").strip()
    if clean_manual:
        parts.append(clean_manual)
    if findings:
        parts.append("\nТочные места, которые нужно исправить:")
        for index, item in enumerate(findings[:40], start=1):
            if item.get("source_type") == "metadata":
                location = f"Метаданные: {item.get('field_name') or 'поле книги'}"
            elif item.get("chapter_number") is not None:
                location = f"Глава {item.get('chapter_number')}"
                if str(item.get("chapter_title") or "").strip():
                    location += f" — {item.get('chapter_title')}"
            else:
                location = "Произведение"
            line = int(item.get("line_number") or 1)
            fragment = str(item.get("matched_text") or item.get("context") or "").strip()
            fragment = " ".join(fragment.split())[:220]
            parts.append(f"{index}. {location}, строка {line}: {item.get('reason') or 'требуется исправление'}" + (f" — «{fragment}»" if fragment else ""))
    return "\n".join(parts).strip()[:8000]
