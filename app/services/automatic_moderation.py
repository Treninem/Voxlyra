from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.config import settings
from app.db import (
    connect,
    get_author_auto_moderation_stats,
    get_book,
    list_chapters_for_book,
    list_graphic_chapters_for_book,
    utc_now,
)
from app.services.duplicate_books import duplicate_warning_text, find_book_duplicates
from app.services.moderation_learning import get_trusted_moderation_categories, is_auto_moderation_enabled
from app.services.moderation_revisions import RevisionChangeSet, get_revision_change_set
from app.services.moderation_rulebook import scan_rulebook
from app.services.cover_storage import find_cover_file

_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)[^\s<>{}\[\]]+", re.IGNORECASE)
_ALLOWED_INTERNAL_URL_RE = re.compile(r"https?://(?:t\.me/)?(?:voxlyrabot|voxlyra\.bothost\.tech)(?:/|$)", re.IGNORECASE)
_PROFANITY_RE = re.compile(
    r"(?iu)(?<![а-яё])(?:"
    r"х(?:у[йея](?:[а-яё]*)?|ер(?:ня|ню|ней|ом|ы|у|а)?)|"
    r"пизд[а-яё]*|[её]б(?:а|л|у|ан)[а-яё]*|бля(?:д|т)[а-яё]*|"
    r"мудак[а-яё]*|сук(?:а|и|у|ой|е|ам|ами|ах)"
    r")(?![а-яё])"
)
_LONG_REPEAT_RE = re.compile(r"(.)\1{24,}", re.DOTALL)
_EXTERNAL_PAYMENT_RE = re.compile(
    r"(?iu)\b(?:перевед(?:и|ите)|оплат(?:и|ите)|скин(?:ь|ьте))\b.{0,60}"
    r"\b(?:на\s+карт(?:у|е)|сбер|тинькофф|qiwi|кошел[её]к)\b.{0,110}"
    r"(?:реквизит|номер\s+карт|пишите|обращайтесь|телеграм|telegram|@[a-z0-9_]{3,}|(?:\d[ -]?){8,}|по\s+ссылке)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class ModerationFinding:
    category: str
    severity: str
    reason: str
    matched_text: str
    context: str
    source_type: str = "metadata"
    source_id: int | None = None
    field_name: str = ""
    chapter_id: int | None = None
    chapter_number: int | None = None
    chapter_title: str = ""
    character_offset: int = 0
    line_number: int = 1
    content_hash: str = ""
    rule_id: str = ""
    confidence: str = "high"

    @property
    def scope(self) -> tuple[str, str]:
        if self.source_type == "metadata":
            return "metadata", self.field_name or "structure"
        return self.source_type, str(self.source_id or self.chapter_id or 0)


@dataclass(slots=True)
class AutoModerationResult:
    auto_publish: bool
    reasons: list[str]
    checked_chapters: int = 0
    total_characters: int = 0
    findings: list[ModerationFinding] = field(default_factory=list)
    changed_summary: dict[str, int] = field(default_factory=dict)
    revision_request_id: int | None = None

    @property
    def risk_level(self) -> str:
        return "clear" if self.auto_publish else "manual"


async def ensure_moderation_findings_schema() -> None:
    async with connect() as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS book_moderation_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_id INTEGER,
                chapter_number INTEGER,
                chapter_title TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'review',
                reason TEXT NOT NULL,
                matched_text TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                character_offset INTEGER NOT NULL DEFAULT 0,
                line_number INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                source_type TEXT NOT NULL DEFAULT 'text',
                source_id INTEGER,
                field_name TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                revision_request_id INTEGER,
                selected_for_revision INTEGER NOT NULL DEFAULT 0,
                rule_id TEXT NOT NULL DEFAULT '',
                confidence TEXT NOT NULL DEFAULT 'high',
                FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE,
                FOREIGN KEY(revision_request_id) REFERENCES book_revision_requests(id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_moderation_findings_book
                ON book_moderation_findings(book_id, status, chapter_number, character_offset);
            CREATE INDEX IF NOT EXISTS idx_moderation_findings_scope
                ON book_moderation_findings(book_id, status, source_type, source_id, field_name);
            """
        )
        cur = await db.execute("PRAGMA table_info(book_moderation_findings)")
        existing = {str(row[1]) for row in await cur.fetchall()}
        migrations = {
            "source_type": "ALTER TABLE book_moderation_findings ADD COLUMN source_type TEXT NOT NULL DEFAULT 'text'",
            "source_id": "ALTER TABLE book_moderation_findings ADD COLUMN source_id INTEGER",
            "field_name": "ALTER TABLE book_moderation_findings ADD COLUMN field_name TEXT NOT NULL DEFAULT ''",
            "content_hash": "ALTER TABLE book_moderation_findings ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''",
            "revision_request_id": "ALTER TABLE book_moderation_findings ADD COLUMN revision_request_id INTEGER",
            "selected_for_revision": "ALTER TABLE book_moderation_findings ADD COLUMN selected_for_revision INTEGER NOT NULL DEFAULT 0",
            "rule_id": "ALTER TABLE book_moderation_findings ADD COLUMN rule_id TEXT NOT NULL DEFAULT ''",
            "confidence": "ALTER TABLE book_moderation_findings ADD COLUMN confidence TEXT NOT NULL DEFAULT 'high'",
        }
        for column, sql in migrations.items():
            if column not in existing:
                await db.execute(sql)
        await db.execute(
            """
            UPDATE book_moderation_findings
            SET source_type=CASE WHEN chapter_id IS NULL THEN 'metadata' ELSE 'text' END,
                source_id=CASE WHEN chapter_id IS NULL THEN source_id ELSE chapter_id END,
                field_name=CASE WHEN chapter_id IS NULL AND COALESCE(field_name,'')='' THEN 'structure' ELSE COALESCE(field_name,'') END
            WHERE COALESCE(source_type,'')='' OR (source_id IS NULL AND chapter_id IS NOT NULL)
            """
        )
        # v1.14.0.20: the legacy scanner treated isolated ambiguous words
        # as advertising. Resolve those stale findings once; the context-aware
        # rulebook will add them again only when a real call-to-action exists.
        await db.execute(
            """
            UPDATE book_moderation_findings
            SET status='resolved', resolved_at=?
            WHERE status='open'
              AND category='promotion'
              AND reason LIKE 'Реклама или запрещённый призыв%'
              AND LOWER(TRIM(matched_text)) IN (
                  'ставка','ставки','казино','букмекер','наркотик','реклама',
                  'промокод','подпишись','подпишитесь','купить доступ'
              )
            """,
            (utc_now(),),
        )
        await db.commit()


def _context(text: str, start: int, end: int, radius: int = 140) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = " ".join(text[left:right].replace("\r", " ").replace("\n", " ").split())
    return ("…" if left else "") + snippet + ("…" if right < len(text) else "")


def _finding(
    pattern: re.Pattern[str],
    text: str,
    *,
    category: str,
    severity: str,
    reason: str,
    chapter: Any | None = None,
    source_type: str = "text",
    source_id: int | None = None,
    field_name: str = "",
    limit: int = 30,
    allow_internal: bool = False,
) -> list[ModerationFinding]:
    result: list[ModerationFinding] = []
    for match in pattern.finditer(text):
        if allow_internal and _ALLOWED_INTERNAL_URL_RE.search(match.group(0)):
            continue
        chapter_id = int(chapter["id"]) if chapter is not None and chapter["id"] is not None else None
        result.append(
            ModerationFinding(
                category=category,
                severity=severity,
                reason=reason,
                matched_text=match.group(0)[:300],
                context=_context(text, match.start(), match.end()),
                source_type=source_type,
                source_id=source_id if source_id is not None else chapter_id,
                field_name=field_name,
                chapter_id=chapter_id,
                chapter_number=int(chapter["number"] or 0) if chapter is not None else None,
                chapter_title=str(chapter["title"] or "") if chapter is not None else "",
                character_offset=match.start(),
                line_number=text.count("\n", 0, match.start()) + 1,
            )
        )
        if len(result) >= limit:
            break
    return result


def _rulebook_findings(
    text: str,
    *,
    scope: str,
    chapter: Any | None = None,
    source_type: str = "text",
    source_id: int | None = None,
    field_name: str = "",
) -> list[ModerationFinding]:
    """Convert deterministic knowledge-base matches into precise findings."""
    result: list[ModerationFinding] = []
    for item in scan_rulebook(text, scope=scope):
        chapter_id = int(chapter["id"]) if chapter is not None and chapter["id"] is not None else None
        result.append(
            ModerationFinding(
                category=item.category,
                severity=item.severity,
                reason=item.reason,
                matched_text=item.matched_text[:300],
                context=_context(text, item.start, item.end),
                source_type=source_type,
                source_id=source_id if source_id is not None else chapter_id,
                field_name=field_name,
                chapter_id=chapter_id,
                chapter_number=int(chapter["number"] or 0) if chapter is not None else None,
                chapter_title=str(chapter["title"] or "") if chapter is not None else "",
                character_offset=item.start,
                line_number=text.count("\n", 0, item.start) + 1,
                rule_id=item.rule_id,
                confidence=item.confidence,
            )
        )
    return result


def _simple_finding(
    *,
    category: str,
    severity: str,
    reason: str,
    source_type: str = "metadata",
    source_id: int | None = None,
    field_name: str = "structure",
    chapter: Any | None = None,
    matched_text: str = "",
    context: str = "",
) -> ModerationFinding:
    chapter_id = int(chapter["id"]) if chapter is not None and chapter["id"] is not None else None
    return ModerationFinding(
        category=category,
        severity=severity,
        reason=reason,
        matched_text=matched_text[:300],
        context=(context or reason)[:1200],
        source_type=source_type,
        source_id=source_id if source_id is not None else chapter_id,
        field_name=field_name,
        chapter_id=chapter_id,
        chapter_number=int(chapter["number"] or 0) if chapter is not None else None,
        chapter_title=str(chapter["title"] or "") if chapter is not None else "",
    )


async def replace_book_moderation_findings(
    book_id: int,
    findings: list[ModerationFinding],
    *,
    scopes: Iterable[tuple[str, str]] | None = None,
) -> None:
    """Replaces all findings or only findings belonging to changed scopes.

    Old rows are resolved instead of deleted, so moderation decisions retain an
    auditable history. During a revision, findings from unchanged chapters stay
    open until the author actually edits those chapters.
    """
    await ensure_moderation_findings_schema()
    now = utc_now()
    normalized_scopes = {(str(kind), str(identifier)) for kind, identifier in (scopes or [])}
    async with connect() as db:
        if scopes is None:
            await db.execute(
                "UPDATE book_moderation_findings SET status='resolved', resolved_at=? WHERE book_id=? AND status='open'",
                (now, int(book_id)),
            )
        else:
            for source_type, identifier in normalized_scopes:
                if source_type == "metadata":
                    await db.execute(
                        """
                        UPDATE book_moderation_findings SET status='resolved', resolved_at=?
                        WHERE book_id=? AND status='open' AND source_type='metadata' AND field_name=?
                        """,
                        (now, int(book_id), identifier),
                    )
                else:
                    try:
                        source_id = int(identifier)
                    except ValueError:
                        continue
                    await db.execute(
                        """
                        UPDATE book_moderation_findings SET status='resolved', resolved_at=?
                        WHERE book_id=? AND status='open' AND source_type=? AND COALESCE(source_id, chapter_id)=?
                        """,
                        (now, int(book_id), source_type, source_id),
                    )
        for item in findings[:1000]:
            await db.execute(
                """
                INSERT INTO book_moderation_findings(
                    book_id, chapter_id, chapter_number, chapter_title, category, severity,
                    reason, matched_text, context, character_offset, line_number, status, created_at,
                    source_type, source_id, field_name, content_hash, selected_for_revision,
                    rule_id, confidence
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)
                """,
                (
                    int(book_id), item.chapter_id, item.chapter_number, item.chapter_title,
                    item.category, item.severity, item.reason, item.matched_text, item.context,
                    item.character_offset, item.line_number, "open", now, item.source_type,
                    item.source_id, item.field_name, item.content_hash, item.rule_id, item.confidence,
                ),
            )
        await db.commit()


async def list_book_moderation_findings(
    book_id: int, *, limit: int = 50, offset: int = 0, include_resolved: bool = False
) -> list[Any]:
    await ensure_moderation_findings_schema()
    status_sql = "" if include_resolved else "AND status='open'"
    async with connect() as db:
        cur = await db.execute(
            f"""
            SELECT * FROM book_moderation_findings
            WHERE book_id=? {status_sql}
            ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,
                     selected_for_revision DESC,
                     CASE severity WHEN 'block' THEN 0 ELSE 1 END,
                     chapter_number, character_offset, id
            LIMIT ? OFFSET ?
            """,
            (int(book_id), max(1, min(500, int(limit))), max(0, int(offset))),
        )
        return await cur.fetchall()


async def count_book_moderation_findings(book_id: int) -> int:
    await ensure_moderation_findings_schema()
    async with connect() as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM book_moderation_findings WHERE book_id=? AND status='open'",
            (int(book_id),),
        )
        row = await cur.fetchone()
        return int(row[0] or 0)


async def resolve_book_moderation_findings(book_id: int) -> None:
    await ensure_moderation_findings_schema()
    async with connect() as db:
        await db.execute(
            "UPDATE book_moderation_findings SET status='resolved', resolved_at=? WHERE book_id=? AND status='open'",
            (utc_now(), int(book_id)),
        )
        await db.commit()


async def _open_findings(book_id: int) -> list[Any]:
    return await list_book_moderation_findings(int(book_id), limit=1000)


def _metadata_pattern_findings(field_name: str, text: str, age_digits: int) -> list[ModerationFinding]:
    label = {"title": "название", "description": "описание"}.get(field_name, field_name)
    result: list[ModerationFinding] = []
    result += _finding(
        _URL_RE, text, category="external_link", severity="block", reason=f"Внешняя ссылка в поле «{label}»",
        source_type="metadata", field_name=field_name, allow_internal=True,
    )
    result += _finding(
        _EXTERNAL_PAYMENT_RE, text, category="external_payment", severity="block",
        reason=f"Возможная оплата вне VoxLyra в поле «{label}»", source_type="metadata", field_name=field_name, limit=10,
    )
    result += _rulebook_findings(
        text, scope="metadata", source_type="metadata", field_name=field_name,
    )
    if age_digits < 18:
        result += _finding(
            _PROFANITY_RE, text, category="profanity_underage", severity="block",
            reason=f"Ненормативная лексика в поле «{label}» при рейтинге ниже 18+",
            source_type="metadata", field_name=field_name, limit=10,
        )
    return result


def _reason_summary(rows: list[Any]) -> tuple[list[str], list[str]]:
    block: list[str] = []
    review: list[str] = []
    for row in rows:
        location = ""
        source_type = str(row["source_type"] or "") if "source_type" in row.keys() else ""
        if source_type == "metadata":
            field_name = str(row["field_name"] or "поле") if "field_name" in row.keys() else "поле"
            location = f" ({field_name})"
        elif row["chapter_number"] is not None:
            location = f" (глава {int(row['chapter_number'])}, строка {int(row['line_number'] or 1)})"
        text = f"{str(row['reason'] or 'Требуется проверка')}{location}"
        target = block if str(row["severity"] or "review") == "block" else review
        if text not in target:
            target.append(text)
    return block, review


async def evaluate_book_for_auto_publication(
    book_id: int,
    *,
    actor_telegram_id: int | None = None,
    revision_mode: bool = False,
) -> AutoModerationResult:
    book = await get_book(int(book_id))
    if not book:
        return AutoModerationResult(False, ["Книга не найдена."])

    chapters = await list_chapters_for_book(int(book_id), published_only=False)
    graphic_chapters = await list_graphic_chapters_for_book(int(book_id), published_only=False)
    active_chapters = [row for row in chapters if str(row["status"] or "") != "deleted"]
    graphics = [row for row in graphic_chapters if str(row["status"] or "") != "deleted"]
    total_characters = sum(len(str(row["text"] or "").strip()) for row in active_chapters)
    total_graphic_pages = sum(int(row["pages_count"] or row["actual_pages_count"] or 0) for row in graphics)
    age_digits = int(re.sub(r"\D", "", str(book["age_limit"] or "0+")) or 0)
    is_graphic = str(book["content_type"] or "book") != "book"
    trusted_categories = await get_trusted_moderation_categories()

    change_set: RevisionChangeSet | None = None
    scopes_to_refresh: set[tuple[str, str]] | None = None
    scan_metadata_fields = {"title", "description", "age_limit", "cover", "content_type", "license", "structure"}
    text_to_scan = list(active_chapters)
    graphics_to_scan = list(graphics)

    if revision_mode:
        change_set = await get_revision_change_set(int(book_id))
        if change_set.has_baseline:
            if not change_set.has_changes:
                existing = await _open_findings(int(book_id))
                if existing:
                    reasons = ["После возврата на доработку изменения не обнаружены."]
                    block, review = _reason_summary(existing)
                    reasons.extend(block + review)
                    return AutoModerationResult(
                        False,
                        list(dict.fromkeys(reasons)),
                        checked_chapters=0,
                        total_characters=total_characters,
                        findings=[],
                        changed_summary=change_set.summary,
                        revision_request_id=change_set.request_id,
                    )
                # Legacy false positives may have been resolved by the new rulebook.
                # In that case allow a clean full recheck without forcing the
                # author to make a meaningless edit only to change the hash.
                scan_metadata_fields = {"title", "description", "age_limit", "cover", "content_type", "license", "structure"}
                text_to_scan = list(active_chapters)
                graphics_to_scan = list(graphics)
                scopes_to_refresh = None
            else:
                scan_metadata_fields = set(change_set.changed_metadata_fields)
                text_ids = set(change_set.changed_text_ids)
                graphic_ids = set(change_set.changed_graphic_ids)
                if {"age_limit", "content_type"} & scan_metadata_fields:
                    text_ids.update(int(row["id"]) for row in active_chapters)
                if "content_type" in scan_metadata_fields:
                    graphic_ids.update(int(row["id"]) for row in graphics)
                text_to_scan = [row for row in active_chapters if int(row["id"]) in text_ids]
                graphics_to_scan = [row for row in graphics if int(row["id"]) in graphic_ids]
                scopes_to_refresh = set(change_set.changed_scopes) | set(change_set.deleted_scopes)
                if text_ids or graphic_ids or ({"content_type"} & scan_metadata_fields):
                    scopes_to_refresh.add(("metadata", "structure"))
                if {"age_limit", "content_type"} & scan_metadata_fields:
                    scopes_to_refresh.update(("text", str(int(row["id"]))) for row in active_chapters)
                if "content_type" in scan_metadata_fields:
                    scopes_to_refresh.update(("graphic", str(int(row["id"]))) for row in graphics)

    findings: list[ModerationFinding] = []

    if "title" in scan_metadata_fields:
        title = str(book["title"] or "").strip()
        findings += _metadata_pattern_findings("title", title, age_digits)
        if len(title) < 2:
            findings.append(_simple_finding(category="missing_title", severity="block", reason="Название книги не заполнено.", field_name="title", matched_text=title))
    if "description" in scan_metadata_fields:
        description = str(book["description"] or "").strip()
        findings += _metadata_pattern_findings("description", description, age_digits)
        if len(description) < 60:
            findings.append(_simple_finding(category="short_description", severity="review", reason="Описание слишком короткое или отсутствует.", field_name="description", matched_text=description[:300]))
    if "cover" in scan_metadata_fields:
        cover_path = str(book["cover_path"] or "").strip()
        cover_file_id = str(book["cover_file_id"] or "").strip()
        local_cover = find_cover_file(int(book_id), cover_path)
        if not local_cover and not cover_file_id:
            findings.append(_simple_finding(
                category="missing_cover", severity="block",
                reason="Нужно загрузить обложку книги.", field_name="cover",
            ))

    if scopes_to_refresh is None or ("metadata", "structure") in scopes_to_refresh:
        if is_graphic:
            if not graphics or total_graphic_pages < 1:
                findings.append(_simple_finding(category="empty_graphic", severity="block", reason="В графическом произведении нет страниц для проверки.", field_name="structure"))
            if total_graphic_pages < 3:
                findings.append(_simple_finding(category="short_graphic", severity="review", reason="Слишком мало страниц для надёжной автоматической проверки.", field_name="structure"))
        else:
            if not active_chapters:
                findings.append(_simple_finding(category="empty_book", severity="block", reason="В книге нет глав для проверки.", field_name="structure"))
            elif total_characters < 2000:
                findings.append(_simple_finding(category="short_book", severity="review", reason="Объём книги слишком мал для надёжной автоматической проверки.", field_name="structure"))

    for chapter in text_to_scan:
        text = str(chapter["text"] or "")
        if 0 < len(text.strip()) < 300:
            findings.append(_simple_finding(
                category="short_chapter", severity="review", reason="Глава слишком короткая — проверьте структуру.",
                source_type="text", source_id=int(chapter["id"]), field_name="", chapter=chapter,
                matched_text=text[:300], context=text[:900],
            ))
        if not text:
            continue
        findings += _finding(_URL_RE, text, category="external_link", severity="block", reason="Внешняя ссылка", chapter=chapter, allow_internal=True)
        findings += _finding(_EXTERNAL_PAYMENT_RE, text, category="external_payment", severity="block", reason="Возможная оплата вне VoxLyra", chapter=chapter, limit=20)
        findings += _rulebook_findings(text, scope="text", chapter=chapter)
        findings += _finding(_LONG_REPEAT_RE, text, category="damaged_text", severity="block", reason="Повреждённый текст или спам", chapter=chapter, limit=10)
        profanity_category = "profanity_underage" if age_digits < 18 else "profanity"
        profanity_severity = "block" if age_digits < 18 else "review"
        findings += _finding(
            _PROFANITY_RE, text, category=profanity_category, severity=profanity_severity,
            reason="Ненормативная лексика при рейтинге ниже 18+" if age_digits < 18 else "Ненормативная лексика — проверьте контекст",
            chapter=chapter, limit=50,
        )

    for chapter in graphics_to_scan:
        pages = int(chapter["pages_count"] or chapter["actual_pages_count"] or 0)
        if pages < 1:
            findings.append(_simple_finding(
                category="empty_graphic_chapter", severity="block", reason="В графической главе нет страниц.",
                source_type="graphic", source_id=int(chapter["id"]), chapter=chapter,
            ))

    # Duplicate/title and author complaint checks are cheap and protect every submission.
    if scopes_to_refresh is None or "title" in scan_metadata_fields:
        matches = await find_book_duplicates(
            title=str(book["title"] or ""),
            author_id=int(book["author_id"]) if book["author_id"] is not None else None,
            exclude_book_id=int(book_id),
            source_file_hash=str(book["source_file_hash"] or ""),
        )
        if matches and not bool(book["duplicate_override"]):
            findings.append(_simple_finding(category="duplicate", severity="block", reason=duplicate_warning_text(matches), field_name="title"))
    stats = await get_author_auto_moderation_stats(
        int(book["author_id"]) if book["author_id"] is not None else None,
        int(book_id),
    )
    if stats["open_complaints"] > 0:
        findings.append(_simple_finding(category="open_complaints", severity="block", reason="У автора есть незавершённые жалобы.", field_name="structure"))
        if scopes_to_refresh is not None:
            scopes_to_refresh.add(("metadata", "structure"))

    visible_findings = [item for item in findings if item.severity == "block" or item.category not in trusted_categories]
    await replace_book_moderation_findings(int(book_id), visible_findings, scopes=scopes_to_refresh)
    open_rows = await _open_findings(int(book_id))
    block_reasons, review_reasons = _reason_summary(open_rows)

    enabled = await is_auto_moderation_enabled()
    is_owner_upload = actor_telegram_id is not None and int(actor_telegram_id) in settings.owner_ids
    reasons: list[str]
    if not enabled:
        reasons = block_reasons + review_reasons + ["Автомодерация отключена владельцем. Требуется ручная проверка."]
    elif revision_mode:
        reasons = block_reasons + review_reasons
        if change_set and change_set.requires_manual_confirmation:
            reasons.append("Модератор оставил отдельную ручную инструкцию — требуется подтверждение её выполнения.")
    elif is_owner_upload:
        reasons = block_reasons
    else:
        reasons = block_reasons + review_reasons + ["Требуется подтверждение модератора перед первой публикацией этой версии книги."]

    unique_reasons = list(dict.fromkeys(item.strip() for item in reasons if item.strip()))
    return AutoModerationResult(
        not unique_reasons,
        unique_reasons,
        checked_chapters=len(text_to_scan) + len(graphics_to_scan),
        total_characters=total_characters,
        findings=visible_findings,
        changed_summary=change_set.summary if change_set else {},
        revision_request_id=change_set.request_id if change_set else None,
    )


def evaluate_metadata_text(value: str, *, age_limit: str = "0+", field_name: str = "поле") -> list[str]:
    text = str(value or "").strip()
    reasons: list[str] = []
    links = [match for match in _URL_RE.finditer(text) if not _ALLOWED_INTERNAL_URL_RE.search(match.group(0))]
    if links:
        reasons.append(f"В поле «{field_name}» нельзя размещать внешние ссылки: {links[0].group(0)[:120]}")
    match = _EXTERNAL_PAYMENT_RE.search(text)
    if match:
        reasons.append(f"В поле «{field_name}» найдена просьба об оплате вне VoxLyra: {match.group(0)[:120]}")
    rule_matches = scan_rulebook(text, scope="metadata")
    if rule_matches:
        match = rule_matches[0]
        reasons.append(f"В поле «{field_name}» найдено подтверждённое нарушение: {match.reason} — {match.matched_text[:120]}")
    age_digits = int(re.sub(r"\D", "", age_limit or "0+") or 0)
    match = _PROFANITY_RE.search(text)
    if match and age_digits < 18:
        reasons.append(f"Ненормативная лексика допустима только для 18+: {match.group(0)}")
    return list(dict.fromkeys(reasons))
