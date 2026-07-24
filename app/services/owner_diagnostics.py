from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.build_info import owner_build_label
from app.config import settings
from app.db import connect, normalize_book_search_text
from app.services.cover_storage import find_cover_file
from app.services.diagnostics import diagnostics_summary

_LOCK = asyncio.Lock()
_LAST_REPORT: dict[str, Any] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _item(
    code: str,
    category: str,
    label: str,
    status: str,
    detail: str,
    *,
    affected_count: int = 0,
    affected: list[dict[str, Any]] | None = None,
    hint: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "category": category,
        "label": label,
        "status": status,
        "detail": detail,
        "affected_count": max(0, int(affected_count or 0)),
        "affected": affected or [],
        "hint": hint,
    }


async def _table_names(db) -> set[str]:
    cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {str(row[0]) for row in await cur.fetchall()}


async def _scalar(db, sql: str, params: tuple[Any, ...] = ()) -> int:
    cur = await db.execute(sql, params)
    row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def _rows(db, sql: str, params: tuple[Any, ...] = (), *, limit: int = 12) -> list[dict[str, Any]]:
    cur = await db.execute(sql, params)
    result = []
    for row in await cur.fetchmany(max(1, int(limit))):
        result.append({key: row[key] for key in row.keys()})
    return result


def _severity_for_count(count: int, *, warning_only: bool = False) -> str:
    if count <= 0:
        return "ok"
    return "warning" if warning_only else "error"


async def run_owner_diagnostics(*, trigger: str = "manual") -> dict[str, Any]:
    """Run a read-only integrity audit for the owner panel.

    The audit deliberately does not publish, reject, delete, repair or notify
    anything. It is safe to run after every redeploy and on a live database.
    """
    global _LAST_REPORT
    async with _LOCK:
        started_at = _utc_now()
        items: list[dict[str, Any]] = []

        static = diagnostics_summary()
        for entry in static.get("items", []):
            ok = bool(getattr(entry, "ok", False))
            items.append(_item(
                str(getattr(entry, "code", "config")),
                "Конфигурация",
                str(getattr(entry, "label", "Параметр")),
                "ok" if ok else "warning",
                "Настроено." if ok else "Требуется проверить настройку.",
                hint=str(getattr(entry, "hint", "") or ""),
            ))

        database_path = Path(str(settings.DATABASE_PATH or "data/voxlyra.sqlite3"))
        if not database_path.is_absolute():
            database_path = Path.cwd() / database_path
        data_root = database_path.parent
        try:
            data_root.mkdir(parents=True, exist_ok=True)
            writable = os.access(data_root, os.W_OK)
            disk = shutil.disk_usage(data_root)
            free_mb = int(disk.free // (1024 * 1024))
            items.append(_item(
                "data_writable",
                "Хранилище",
                "Постоянный каталог доступен для записи",
                "ok" if writable else "error",
                f"Каталог: {data_root.as_posix()} · свободно {free_mb} МБ.",
                hint="Проверьте подключение постоянного каталога data на Bothost." if not writable else "",
            ))
            reserve = max(64, int(settings.LIBRARY_IMPORT_MIN_FREE_DISK_MB or 128))
            items.append(_item(
                "disk_free",
                "Хранилище",
                "Достаточно свободного места",
                "ok" if free_mb >= reserve else "warning",
                f"Свободно {free_mb} МБ, резерв импорта {reserve} МБ.",
                hint="Освободите data/backups, старые архивы импорта или увеличьте диск." if free_mb < reserve else "",
            ))
        except OSError as exc:
            items.append(_item("storage_error", "Хранилище", "Проверка постоянного каталога", "error", str(exc)[:300]))

        try:
            async with connect() as db:
                cur = await db.execute("PRAGMA quick_check(1)")
                quick = await cur.fetchone()
                quick_text = str(quick[0] if quick else "unknown")
                items.append(_item(
                    "sqlite_quick_check",
                    "База данных",
                    "SQLite quick_check",
                    "ok" if quick_text.lower() == "ok" else "error",
                    quick_text,
                    hint="Сделайте резервную копию базы до любых восстановительных действий." if quick_text.lower() != "ok" else "",
                ))

                tables = await _table_names(db)
                required = {
                    "users", "author_profiles", "books", "chapters", "audio_chapters",
                    "graphic_chapters", "graphic_pages", "chapter_reactions",
                    "book_moderation_queue", "book_revision_requests",
                    "notification_deliveries", "purchase_access_claims", "settings",
                }
                missing = sorted(required - tables)
                items.append(_item(
                    "required_tables",
                    "База данных",
                    "Обязательные таблицы созданы",
                    "ok" if not missing else "error",
                    "Все таблицы на месте." if not missing else f"Отсутствуют: {', '.join(missing)}",
                    affected_count=len(missing),
                ))

                db_size_mb = int(database_path.stat().st_size // (1024 * 1024)) if database_path.is_file() else 0
                book_count = await _scalar(db, "SELECT COUNT(*) FROM books") if "books" in tables else 0
                chapter_count = await _scalar(db, "SELECT COUNT(*) FROM chapters") if "chapters" in tables else 0
                items.append(_item(
                    "database_summary",
                    "База данных",
                    "База открывается и читается",
                    "ok",
                    f"Размер {db_size_mb} МБ · книг {book_count} · текстовых глав {chapter_count}.",
                ))

                if {"chapters", "books"}.issubset(tables):
                    orphan_chapters = await _scalar(db, "SELECT COUNT(*) FROM chapters c LEFT JOIN books b ON b.id=c.book_id WHERE b.id IS NULL")
                    orphan_sample = await _rows(db, "SELECT c.id AS chapter_id, c.book_id, c.number FROM chapters c LEFT JOIN books b ON b.id=c.book_id WHERE b.id IS NULL ORDER BY c.id LIMIT 12") if orphan_chapters else []
                    items.append(_item(
                        "orphan_chapters", "Целостность контента", "Текстовые главы привязаны к книгам",
                        _severity_for_count(orphan_chapters),
                        "Нарушений нет." if not orphan_chapters else f"Найдено потерянных глав: {orphan_chapters}.",
                        affected_count=orphan_chapters, affected=orphan_sample,
                    ))
                    duplicate_numbers = await _scalar(db, "SELECT COUNT(*) FROM (SELECT book_id, number FROM chapters WHERE status!='deleted' GROUP BY book_id, number HAVING COUNT(*)>1)")
                    duplicate_sample = await _rows(db, "SELECT book_id, number, COUNT(*) AS copies FROM chapters WHERE status!='deleted' GROUP BY book_id, number HAVING COUNT(*)>1 ORDER BY copies DESC LIMIT 12") if duplicate_numbers else []
                    items.append(_item(
                        "duplicate_chapter_numbers", "Целостность контента", "Номера текстовых глав уникальны",
                        _severity_for_count(duplicate_numbers),
                        "Дубликатов нет." if not duplicate_numbers else f"Конфликтов номеров: {duplicate_numbers}.",
                        affected_count=duplicate_numbers, affected=duplicate_sample,
                    ))

                if {"audio_chapters", "books"}.issubset(tables):
                    orphan_audio = await _scalar(db, "SELECT COUNT(*) FROM audio_chapters a LEFT JOIN books b ON b.id=a.book_id WHERE b.id IS NULL")
                    items.append(_item(
                        "orphan_audio", "Целостность контента", "Аудиоглавы привязаны к книгам",
                        _severity_for_count(orphan_audio),
                        "Нарушений нет." if not orphan_audio else f"Потерянных аудиоглав: {orphan_audio}.",
                        affected_count=orphan_audio,
                    ))

                if {"graphic_chapters", "graphic_pages", "books"}.issubset(tables):
                    orphan_graphics = await _scalar(db, "SELECT COUNT(*) FROM graphic_chapters g LEFT JOIN books b ON b.id=g.book_id WHERE b.id IS NULL")
                    orphan_pages = await _scalar(db, "SELECT COUNT(*) FROM graphic_pages p LEFT JOIN graphic_chapters g ON g.id=p.graphic_chapter_id WHERE g.id IS NULL")
                    items.append(_item(
                        "orphan_graphics", "Целостность контента", "Графические главы и страницы связаны",
                        _severity_for_count(orphan_graphics + orphan_pages),
                        "Нарушений нет." if not (orphan_graphics + orphan_pages) else f"Потерянных глав: {orphan_graphics}, страниц: {orphan_pages}.",
                        affected_count=orphan_graphics + orphan_pages,
                    ))

                if {"books", "chapters", "audio_chapters", "graphic_chapters"}.issubset(tables):
                    no_content_sql = """
                        SELECT b.id AS book_id, b.title
                        FROM books b
                        WHERE b.publication_status='published'
                          AND NOT EXISTS (SELECT 1 FROM chapters c WHERE c.book_id=b.id AND c.status='published')
                          AND NOT EXISTS (SELECT 1 FROM audio_chapters a WHERE a.book_id=b.id AND a.status='published')
                          AND NOT EXISTS (SELECT 1 FROM graphic_chapters g WHERE g.book_id=b.id AND g.status='published')
                        ORDER BY b.id LIMIT 12
                    """
                    no_content_count = await _scalar(db, "SELECT COUNT(*) FROM (" + no_content_sql.replace("ORDER BY b.id LIMIT 12", "") + ")")
                    no_content_sample = await _rows(db, no_content_sql) if no_content_count else []
                    items.append(_item(
                        "published_without_content", "Каталог", "Опубликованные книги содержат опубликованный материал",
                        _severity_for_count(no_content_count),
                        "Все опубликованные книги доступны для чтения/прослушивания." if not no_content_count else f"Пустых опубликованных книг: {no_content_count}.",
                        affected_count=no_content_count, affected=no_content_sample,
                        hint="Откройте книгу в управлении и проверьте статусы её глав." if no_content_count else "",
                    ))

                if {"books", "book_moderation_queue", "chapters", "graphic_chapters", "audio_chapters"}.issubset(tables):
                    stuck_review_sql = """
                        SELECT b.id AS book_id, b.title, b.publication_status
                        FROM books b
                        LEFT JOIN book_moderation_queue q ON q.book_id=b.id AND q.status='pending'
                        WHERE b.publication_status='review' AND q.book_id IS NULL
                        ORDER BY b.id LIMIT 12
                    """
                    stuck_review_count = await _scalar(db, "SELECT COUNT(*) FROM books b LEFT JOIN book_moderation_queue q ON q.book_id=b.id AND q.status='pending' WHERE b.publication_status='review' AND q.book_id IS NULL")
                    items.append(_item(
                        "review_without_queue", "Модерация", "Книги на проверке имеют активное задание",
                        _severity_for_count(stuck_review_count),
                        "Зависших книг нет." if not stuck_review_count else f"Без задания модерации: {stuck_review_count}.",
                        affected_count=stuck_review_count,
                        affected=await _rows(db, stuck_review_sql) if stuck_review_count else [],
                        hint="Такие книги видны владельцу в разделе «Управление книгами» и должны быть опубликованы либо возвращены на доработку." if stuck_review_count else "",
                    ))

                    pending_content_condition = """
                        EXISTS (SELECT 1 FROM chapters c WHERE c.book_id=b.id AND c.status IN ('draft','review'))
                        OR EXISTS (SELECT 1 FROM graphic_chapters g WHERE g.book_id=b.id AND g.status IN ('draft','review'))
                        OR EXISTS (SELECT 1 FROM audio_chapters a WHERE a.book_id=b.id AND a.status IN ('draft','review'))
                    """
                    hidden_without_queue_count = await _scalar(db, f"""
                        SELECT COUNT(*) FROM books b
                        LEFT JOIN book_moderation_queue q ON q.book_id=b.id AND q.status='pending'
                        WHERE b.publication_status='published' AND ({pending_content_condition}) AND q.book_id IS NULL
                    """)
                    hidden_without_queue_sample = await _rows(db, f"""
                        SELECT b.id AS book_id, b.title FROM books b
                        LEFT JOIN book_moderation_queue q ON q.book_id=b.id AND q.status='pending'
                        WHERE b.publication_status='published' AND ({pending_content_condition}) AND q.book_id IS NULL
                        ORDER BY b.id LIMIT 12
                    """) if hidden_without_queue_count else []
                    items.append(_item(
                        "pending_content_without_queue", "Модерация", "Новые или изменённые главы не теряются из очереди",
                        _severity_for_count(hidden_without_queue_count),
                        "Весь скрытый материал находится в очереди." if not hidden_without_queue_count else f"Книг со скрытым материалом без задания: {hidden_without_queue_count}.",
                        affected_count=hidden_without_queue_count, affected=hidden_without_queue_sample,
                    ))

                    stale_queue_count = await _scalar(db, f"""
                        SELECT COUNT(*) FROM book_moderation_queue q JOIN books b ON b.id=q.book_id
                        WHERE q.status='pending'
                          AND b.publication_status NOT IN ('review','published')
                    """)
                    stale_queue_sample = await _rows(db, """
                        SELECT b.id AS book_id, b.title, b.publication_status, q.submitted_at
                        FROM book_moderation_queue q JOIN books b ON b.id=q.book_id
                        WHERE q.status='pending' AND b.publication_status NOT IN ('review','published')
                        ORDER BY q.submitted_at LIMIT 12
                    """) if stale_queue_count else []
                    items.append(_item(
                        "stale_moderation_queue", "Модерация", "В очереди нет черновиков и закрытых книг",
                        _severity_for_count(stale_queue_count, warning_only=True),
                        "Очередь согласована со статусами книг." if not stale_queue_count else f"Подозрительных заданий: {stale_queue_count}.",
                        affected_count=stale_queue_count, affected=stale_queue_sample,
                    ))

                if "books" in tables:
                    cur = await db.execute("SELECT id, title, normalized_title, cover_path, cover_file_id, publication_status FROM books WHERE publication_status!='deleted' ORDER BY id")
                    all_books = await cur.fetchall()
                    bad_search: list[dict[str, Any]] = []
                    missing_covers: list[dict[str, Any]] = []
                    coverless_published: list[dict[str, Any]] = []
                    for row in all_books:
                        expected = normalize_book_search_text(row["title"])
                        actual = normalize_book_search_text(row["normalized_title"])
                        if expected and actual != expected:
                            bad_search.append({"book_id": int(row["id"]), "title": row["title"]})
                        path_value = str(row["cover_path"] or "").strip()
                        file_id = str(row["cover_file_id"] or "").strip()
                        if path_value and not find_cover_file(int(row["id"]), path_value):
                            missing_covers.append({"book_id": int(row["id"]), "title": row["title"], "cover_path": path_value})
                        if str(row["publication_status"]) == "published" and not file_id and not find_cover_file(int(row["id"]), path_value):
                            coverless_published.append({"book_id": int(row["id"]), "title": row["title"]})
                    items.append(_item(
                        "search_index", "Поиск", "Индекс названий совпадает с книгами",
                        _severity_for_count(len(bad_search), warning_only=True),
                        "Индекс актуален." if not bad_search else f"Требуют переиндексации: {len(bad_search)}.",
                        affected_count=len(bad_search), affected=bad_search[:12],
                        hint="Поиск всё равно использует нормализацию на лету, но индекс стоит обновить при следующем техническом этапе." if bad_search else "",
                    ))
                    items.append(_item(
                        "missing_cover_files", "Обложки", "Сохранённые пути обложек существуют",
                        _severity_for_count(len(missing_covers)),
                        "Все локальные обложки найдены." if not missing_covers else f"Потерянных файлов обложек: {len(missing_covers)}.",
                        affected_count=len(missing_covers), affected=missing_covers[:12],
                    ))
                    items.append(_item(
                        "published_without_cover", "Обложки", "Опубликованные книги имеют обложку",
                        _severity_for_count(len(coverless_published), warning_only=True),
                        "У всех опубликованных книг есть обложка." if not coverless_published else f"Без обложки: {len(coverless_published)}.",
                        affected_count=len(coverless_published), affected=coverless_published[:12],
                    ))

                if {"chapter_reactions", "chapters", "users"}.issubset(tables):
                    orphan_reactions = await _scalar(db, """
                        SELECT COUNT(*) FROM chapter_reactions r
                        LEFT JOIN chapters c ON c.id=r.chapter_id
                        LEFT JOIN users u ON u.id=r.user_id
                        WHERE c.id IS NULL OR u.id IS NULL
                    """)
                    items.append(_item(
                        "reaction_integrity", "Действия читателей", "Реакции связаны с главами и пользователями",
                        _severity_for_count(orphan_reactions),
                        "Нарушений нет." if not orphan_reactions else f"Потерянных реакций: {orphan_reactions}.",
                        affected_count=orphan_reactions,
                    ))

                if "notification_deliveries" in tables:
                    failed_notifications = await _scalar(db, "SELECT COUNT(*) FROM notification_deliveries WHERE status IN ('failed','error')")
                    failed_sample = await _rows(db, "SELECT event_key, user_id, category, status, updated_at FROM notification_deliveries WHERE status IN ('failed','error') ORDER BY updated_at DESC LIMIT 12") if failed_notifications else []
                    items.append(_item(
                        "failed_notifications", "Уведомления", "Нет необработанных ошибок доставки",
                        _severity_for_count(failed_notifications, warning_only=True),
                        "Ошибок доставки не зафиксировано." if not failed_notifications else f"Ошибок доставки: {failed_notifications}.",
                        affected_count=failed_notifications, affected=failed_sample,
                        hint="Ошибка доставки одному пользователю не должна повторно публиковать книгу или главу." if failed_notifications else "",
                    ))

                if "purchase_access_claims" in tables:
                    duplicate_access = await _scalar(db, "SELECT COUNT(*) FROM (SELECT user_id, access_key FROM purchase_access_claims WHERE status='active' GROUP BY user_id, access_key HAVING COUNT(*)>1)")
                    items.append(_item(
                        "access_claim_integrity", "Платный доступ", "Нет двойных активных прав доступа",
                        _severity_for_count(duplicate_access),
                        "Двойных прав нет." if not duplicate_access else f"Дубликатов прав: {duplicate_access}.",
                        affected_count=duplicate_access,
                    ))
        except Exception as exc:
            items.append(_item(
                "database_exception", "База данных", "Глубокая проверка базы", "error",
                f"{type(exc).__name__}: {str(exc)[:500]}",
                hint="Скачайте резервную копию и передайте текст ошибки разработчику.",
            ))

        status_rank = {"ok": 0, "warning": 1, "error": 2}
        counts = {"ok": 0, "warning": 0, "error": 0}
        for entry in items:
            counts[str(entry["status"])] = counts.get(str(entry["status"]), 0) + 1
        overall = "error" if counts["error"] else "warning" if counts["warning"] else "ok"
        report = {
            "ok": overall == "ok",
            "status": overall,
            "trigger": str(trigger or "manual"),
            "build": owner_build_label(),
            "started_at": started_at,
            "completed_at": _utc_now(),
            "counts": counts,
            "total": len(items),
            "items": sorted(items, key=lambda value: (-status_rank.get(str(value["status"]), 0), str(value["category"]), str(value["label"]))),
        }
        _LAST_REPORT = report
        return report


def last_owner_diagnostics() -> dict[str, Any] | None:
    return _LAST_REPORT


def diagnostics_brief(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"status": "pending", "ok": False, "errors": 0, "warnings": 0, "completed_at": ""}
    counts = report.get("counts") or {}
    return {
        "status": str(report.get("status") or "pending"),
        "ok": bool(report.get("ok")),
        "errors": int(counts.get("error") or 0),
        "warnings": int(counts.get("warning") or 0),
        "completed_at": str(report.get("completed_at") or ""),
    }
