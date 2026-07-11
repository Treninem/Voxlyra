from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.config import settings
from app.db import connect


PERIODS: dict[str, tuple[str, int]] = {
    "day": ("дня", 1),
    "week": ("недели", 7),
    "month": ("месяца", 30),
    "year": ("года", 365),
}
CATEGORY_LABELS = {
    "book": "книг",
    "audio": "аудиокниг",
    "comic": "комиксов",
}

_CACHE_TTL_SECONDS = 300.0
_CACHE_LOCK = asyncio.Lock()
_CACHE_AT = 0.0
_CACHE: dict[str, dict[int, dict[str, dict[str, Any]]]] = {}
_CACHE_DB_PATH = ""


def _utc_cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))).isoformat()


def _stat_bucket() -> dict[str, float]:
    return {
        "transactions": 0.0,
        "buyers": 0.0,
        "stars": 0.0,
        "active_users": 0.0,
        "activity_marks": 0.0,
        "completions": 0.0,
        "bookmarks": 0.0,
        "reviews": 0.0,
        "rating_sum": 0.0,
    }


def _score(stats: dict[str, float]) -> float:
    """Weighted popularity score that rewards real use rather than empty clicks.

    Purchases are strongest, then unique readers/listeners, completed content,
    library additions and reviews. Repeated progress marks have a strict cap so
    a single user cannot push a work to the top by refreshing the reader.
    """
    active = max(0.0, stats["active_users"])
    capped_marks = min(max(0.0, stats["activity_marks"]), active * 12.0)
    capped_completions = min(max(0.0, stats["completions"]), active * 6.0)
    average_rating = stats["rating_sum"] / stats["reviews"] if stats["reviews"] else 0.0
    return round(
        stats["buyers"] * 55.0
        + stats["transactions"] * 18.0
        + min(stats["stars"], 5000.0) * 0.025
        + active * 22.0
        + capped_marks * 1.6
        + capped_completions * 6.0
        + stats["bookmarks"] * 8.0
        + stats["reviews"] * 10.0
        + average_rating * min(stats["reviews"], 25.0) * 1.5,
        3,
    )


async def _load_period_stats(cutoff: str) -> dict[str, dict[int, dict[str, float]]]:
    stats: dict[str, dict[int, dict[str, float]]] = {
        "book": defaultdict(_stat_bucket),
        "audio": defaultdict(_stat_bucket),
        "comic": defaultdict(_stat_bucket),
    }

    async with connect() as db:
        # Paid transactions. A purchase is assigned to the exact content kind.
        cur = await db.execute(
            """
            SELECT
                COALESCE(p.book_id, c.book_id, ac.book_id, gc.book_id, cp.book_id) AS book_id,
                CASE
                    WHEN p.audio_chapter_id IS NOT NULL THEN 'audio'
                    WHEN p.graphic_chapter_id IS NOT NULL OR p.graphic_volume_number IS NOT NULL THEN 'comic'
                    WHEN cp.content_scope='graphic' THEN 'comic'
                    WHEN COALESCE(b.content_type, 'book')!='book' THEN 'comic'
                    ELSE 'book'
                END AS category,
                COUNT(*) AS transactions,
                COUNT(DISTINCT p.user_id) AS buyers,
                COALESCE(SUM(p.amount_stars), 0) AS stars
            FROM purchases p
            LEFT JOIN chapters c ON c.id=p.chapter_id
            LEFT JOIN audio_chapters ac ON ac.id=p.audio_chapter_id
            LEFT JOIN graphic_chapters gc ON gc.id=p.graphic_chapter_id
            LEFT JOIN chapter_packages cp ON cp.id=p.chapter_package_id
            LEFT JOIN books b ON b.id=COALESCE(p.book_id, c.book_id, ac.book_id, gc.book_id, cp.book_id)
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE p.status='paid' AND p.created_at>=? AND b.publication_status='published'
              AND (ap.user_id IS NULL OR p.user_id!=ap.user_id)
            GROUP BY 1, 2
            """,
            (cutoff,),
        )
        for row in await cur.fetchall():
            book_id = int(row["book_id"] or 0)
            category = str(row["category"] or "book")
            if not book_id or category not in stats:
                continue
            bucket = stats[category][book_id]
            bucket["transactions"] += float(row["transactions"] or 0)
            bucket["buyers"] += float(row["buyers"] or 0)
            bucket["stars"] += float(row["stars"] or 0)

        # Text reading. One row exists per user and chapter, so chapter progress
        # naturally measures depth while unique users protects against refreshes.
        cur = await db.execute(
            """
            SELECT rp.book_id,
                   COUNT(DISTINCT rp.user_id) AS active_users,
                   COUNT(*) AS activity_marks,
                   SUM(CASE WHEN rp.position_percent>=90 THEN 1 ELSE 0 END) AS completions
            FROM reading_progress rp
            JOIN books b ON b.id=rp.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE rp.updated_at>=? AND b.publication_status='published' AND COALESCE(b.content_type,'book')='book'
              AND (ap.user_id IS NULL OR rp.user_id!=ap.user_id)
            GROUP BY rp.book_id
            """,
            (cutoff,),
        )
        for row in await cur.fetchall():
            bucket = stats["book"][int(row["book_id"])]
            bucket["active_users"] += float(row["active_users"] or 0)
            bucket["activity_marks"] += float(row["activity_marks"] or 0)
            bucket["completions"] += float(row["completions"] or 0)

        # Audio listening progress. Completion is counted at 90% of duration.
        cur = await db.execute(
            """
            SELECT ac.book_id,
                   COUNT(DISTINCT lp.user_id) AS active_users,
                   COUNT(*) AS activity_marks,
                   SUM(CASE WHEN ac.duration_seconds>0 AND lp.position_seconds>=ac.duration_seconds*0.9 THEN 1 ELSE 0 END) AS completions
            FROM listening_progress lp
            JOIN audio_chapters ac ON ac.id=lp.audio_chapter_id
            JOIN books b ON b.id=ac.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE lp.updated_at>=? AND ac.status='published' AND b.publication_status='published'
              AND (ap.user_id IS NULL OR lp.user_id!=ap.user_id)
            GROUP BY ac.book_id
            """,
            (cutoff,),
        )
        for row in await cur.fetchall():
            bucket = stats["audio"][int(row["book_id"])]
            bucket["active_users"] += float(row["active_users"] or 0)
            bucket["activity_marks"] += float(row["activity_marks"] or 0)
            bucket["completions"] += float(row["completions"] or 0)

        # Graphic reader events include page views, frame views and completion.
        cur = await db.execute(
            """
            SELECT gc.book_id,
                   COUNT(DISTINCT gre.user_id) AS active_users,
                   SUM(CASE WHEN gre.event_type IN ('open','page_view','frame_view') THEN 1 ELSE 0 END) AS activity_marks,
                   SUM(CASE WHEN gre.event_type='complete' THEN 1 ELSE 0 END) AS completions
            FROM graphic_reading_events gre
            JOIN graphic_chapters gc ON gc.id=gre.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE gre.created_at>=? AND gc.status='published' AND b.publication_status='published'
              AND (ap.user_id IS NULL OR gre.user_id!=ap.user_id)
            GROUP BY gc.book_id
            """,
            (cutoff,),
        )
        graphic_event_books: set[int] = set()
        for row in await cur.fetchall():
            book_id = int(row["book_id"])
            graphic_event_books.add(book_id)
            bucket = stats["comic"][book_id]
            bucket["active_users"] += float(row["active_users"] or 0)
            bucket["activity_marks"] += float(row["activity_marks"] or 0)
            bucket["completions"] += float(row["completions"] or 0)

        # Compatibility for graphic chapters read before detailed event tracking.
        cur = await db.execute(
            """
            SELECT gc.book_id,
                   COUNT(DISTINCT grp.user_id) AS active_users,
                   COUNT(*) AS activity_marks,
                   SUM(CASE WHEN gc.pages_count>0 AND grp.page_number>=gc.pages_count THEN 1 ELSE 0 END) AS completions
            FROM graphic_reading_progress grp
            JOIN graphic_chapters gc ON gc.id=grp.graphic_chapter_id
            JOIN books b ON b.id=gc.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE grp.updated_at>=? AND gc.status='published' AND b.publication_status='published'
              AND (ap.user_id IS NULL OR grp.user_id!=ap.user_id)
            GROUP BY gc.book_id
            """,
            (cutoff,),
        )
        for row in await cur.fetchall():
            book_id = int(row["book_id"])
            if book_id in graphic_event_books:
                continue
            bucket = stats["comic"][book_id]
            bucket["active_users"] += float(row["active_users"] or 0)
            bucket["activity_marks"] += float(row["activity_marks"] or 0)
            bucket["completions"] += float(row["completions"] or 0)

        # Library additions and reviews strengthen the main work category.
        cur = await db.execute(
            """
            SELECT b.id AS book_id,
                   CASE WHEN COALESCE(b.content_type,'book')='book' THEN 'book' ELSE 'comic' END AS category,
                   COUNT(DISTINCT bm.user_id) AS bookmarks
            FROM bookmarks bm
            JOIN books b ON b.id=bm.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE bm.updated_at>=? AND b.publication_status='published'
              AND (ap.user_id IS NULL OR bm.user_id!=ap.user_id)
            GROUP BY b.id, category
            """,
            (cutoff,),
        )
        for row in await cur.fetchall():
            stats[str(row["category"])][int(row["book_id"])]["bookmarks"] += float(row["bookmarks"] or 0)

        cur = await db.execute(
            """
            SELECT b.id AS book_id,
                   CASE WHEN COALESCE(b.content_type,'book')='book' THEN 'book' ELSE 'comic' END AS category,
                   COUNT(*) AS reviews,
                   COALESCE(SUM(r.rating),0) AS rating_sum
            FROM reviews r
            JOIN books b ON b.id=r.book_id
            LEFT JOIN author_profiles ap ON ap.id=b.author_id
            WHERE r.updated_at>=? AND r.status='published' AND b.publication_status='published'
              AND (ap.user_id IS NULL OR r.user_id!=ap.user_id)
            GROUP BY b.id, category
            """,
            (cutoff,),
        )
        for row in await cur.fetchall():
            bucket = stats[str(row["category"])][int(row["book_id"])]
            bucket["reviews"] += float(row["reviews"] or 0)
            bucket["rating_sum"] += float(row["rating_sum"] or 0)

    return stats


def _rank_period(stats: dict[str, dict[int, dict[str, float]]], period: str) -> dict[str, dict[int, dict[str, Any]]]:
    ranked: dict[str, dict[int, dict[str, Any]]] = {"book": {}, "audio": {}, "comic": {}}
    label = PERIODS[period][0]
    for category, by_book in stats.items():
        rows: list[tuple[int, float, dict[str, float]]] = []
        for book_id, item in by_book.items():
            score = _score(item)
            if score <= 0:
                continue
            rows.append((int(book_id), score, item))
        rows.sort(
            key=lambda value: (
                value[1],
                value[2]["buyers"],
                value[2]["active_users"],
                value[2]["transactions"],
                -value[0],
            ),
            reverse=True,
        )
        for index, (book_id, score, item) in enumerate(rows[:99], start=1):
            ranked[category][book_id] = {
                "rank": index,
                "period": period,
                "period_label": label,
                "category": category,
                "category_label": CATEGORY_LABELS[category],
                "score": score,
                "buyers": int(item["buyers"]),
                "active_users": int(item["active_users"]),
            }
    return ranked


async def get_rankings(*, force: bool = False) -> dict[str, dict[int, dict[str, dict[str, Any]]]]:
    global _CACHE, _CACHE_AT, _CACHE_DB_PATH
    database_path = str(settings.DATABASE_PATH or "")
    now = time.monotonic()
    if not force and _CACHE and _CACHE_DB_PATH == database_path and now - _CACHE_AT < _CACHE_TTL_SECONDS:
        return _CACHE
    async with _CACHE_LOCK:
        now = time.monotonic()
        if not force and _CACHE and _CACHE_DB_PATH == database_path and now - _CACHE_AT < _CACHE_TTL_SECONDS:
            return _CACHE
        result: dict[str, dict[int, dict[str, dict[str, Any]]]] = {
            "book": defaultdict(dict),
            "audio": defaultdict(dict),
            "comic": defaultdict(dict),
        }
        for period, (_, days) in PERIODS.items():
            period_rankings = _rank_period(await _load_period_stats(_utc_cutoff(days)), period)
            for category, by_book in period_rankings.items():
                for book_id, item in by_book.items():
                    result[category][book_id][period] = item
        _CACHE = {category: dict(items) for category, items in result.items()}
        _CACHE_AT = time.monotonic()
        _CACHE_DB_PATH = database_path
        return _CACHE


def _as_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {key: row[key] for key in row.keys()}
    except (AttributeError, TypeError):
        return dict(row)


def _category_for_book(row: dict[str, Any], requested: str) -> str:
    if requested in {"book", "audio", "comic"}:
        return requested
    return "book" if str(row.get("content_type") or "book") == "book" else "comic"


def _select_visible_badge(periods: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not periods:
        return None
    # Best place wins. If places are equal, the longer period is more prestigious.
    period_priority = {"year": 4, "month": 3, "week": 2, "day": 1}
    return min(
        periods.values(),
        key=lambda item: (int(item["rank"]), -period_priority.get(str(item["period"]), 0)),
    )


def _decorate(row: Any, category: str, rankings: dict[str, dict[int, dict[str, dict[str, Any]]]]) -> dict[str, Any]:
    data = _as_dict(row)
    if not data:
        return data
    selected_category = _category_for_book(data, category)
    periods = rankings.get(selected_category, {}).get(int(data.get("id") or 0), {})
    badge = _select_visible_badge(periods)
    data["ranking_category"] = selected_category
    data["ranking_periods"] = periods
    data["top_rank"] = int(badge["rank"]) if badge else 0
    data["top_period"] = str(badge["period"]) if badge else ""
    data["top_period_label"] = str(badge["period_label"]) if badge else ""
    data["top_score"] = float(badge["score"]) if badge else 0.0
    data["top_category_label"] = str(badge["category_label"]) if badge else ""
    if periods:
        ordered = [periods[key] for key in ("year", "month", "week", "day") if key in periods]
        data["top_tooltip"] = " · ".join(f"#{item['rank']} {item['period_label']}" for item in ordered)
    else:
        data["top_tooltip"] = ""
    return data


async def attach_rankings(rows: Iterable[Any], *, category: str = "auto") -> list[dict[str, Any]]:
    rankings = await get_rankings()
    return [_decorate(row, category, rankings) for row in rows]


async def attach_ranking(row: Any, *, category: str = "auto") -> dict[str, Any] | None:
    if row is None:
        return None
    rankings = await get_rankings()
    return _decorate(row, category, rankings)


async def invalidate_ranking_cache() -> None:
    global _CACHE, _CACHE_AT, _CACHE_DB_PATH
    async with _CACHE_LOCK:
        _CACHE = {}
        _CACHE_AT = 0.0
        _CACHE_DB_PATH = ""
