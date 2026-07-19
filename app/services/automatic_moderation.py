from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

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
from app.services.moderation_learning import (
    get_trusted_moderation_categories,
    is_auto_moderation_enabled,
)

_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)[^\s<>{}\[\]]+", re.IGNORECASE)
_ALLOWED_INTERNAL_URL_RE = re.compile(r"https?://(?:t\.me/)?(?:voxlyrabot|voxlyra\.bothost\.tech)(?:/|$)", re.IGNORECASE)
_PROFANITY_RE = re.compile(r"(?iu)(?<![а-яё])(?:х(?:у[йея]|ер)|пизд|еб(?:а|л|у|ан)|бля(?:д|т)|мудак|сук[аи])")
_FORBIDDEN_PROMO_RE = re.compile(r"(?iu)(?:подпиш(?:ись|итесь)|реклама|промокод|казино|ставки|букмекер|наркотик|купить\s+доступ)")
_LONG_REPEAT_RE = re.compile(r"(.)\1{24,}", re.DOTALL)
_EXTERNAL_PAYMENT_RE = re.compile(
    r"(?:перевед(?:и|ите)|оплат(?:а|ить)|скин(?:ь|ьте)).{0,45}(?:карт(?:у|е)|сбер|тинькофф|qiwi|кошел[её]к)",
    re.IGNORECASE | re.DOTALL,
)

@dataclass(slots=True)
class ModerationFinding:
    category: str
    severity: str
    reason: str
    matched_text: str
    context: str
    chapter_id: int | None = None
    chapter_number: int | None = None
    chapter_title: str = ""
    character_offset: int = 0
    line_number: int = 1

@dataclass(slots=True)
class AutoModerationResult:
    auto_publish: bool
    reasons: list[str]
    checked_chapters: int = 0
    total_characters: int = 0
    findings: list[ModerationFinding] = field(default_factory=list)

    @property
    def risk_level(self) -> str:
        return "clear" if self.auto_publish else "manual"

async def ensure_moderation_findings_schema() -> None:
    async with connect() as db:
        await db.executescript("""
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
            FOREIGN KEY(book_id) REFERENCES books(id) ON DELETE CASCADE,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_moderation_findings_book
            ON book_moderation_findings(book_id, status, chapter_number, character_offset);
        """)
        await db.commit()


def _context(text: str, start: int, end: int, radius: int = 140) -> str:
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    snippet = " ".join(text[left:right].replace("\r", " ").replace("\n", " ").split())
    return ("…" if left else "") + snippet + ("…" if right < len(text) else "")


def _finding(pattern: re.Pattern[str], text: str, *, category: str, severity: str, reason: str,
             chapter: Any, limit: int = 30, allow_internal: bool = False) -> list[ModerationFinding]:
    result: list[ModerationFinding] = []
    for match in pattern.finditer(text):
        if allow_internal and _ALLOWED_INTERNAL_URL_RE.search(match.group(0)):
            continue
        result.append(ModerationFinding(
            category=category,
            severity=severity,
            reason=reason,
            matched_text=match.group(0)[:300],
            context=_context(text, match.start(), match.end()),
            chapter_id=int(chapter["id"]) if chapter and chapter["id"] is not None else None,
            chapter_number=int(chapter["number"] or 0) if chapter else None,
            chapter_title=str(chapter["title"] or "") if chapter else "",
            character_offset=match.start(),
            line_number=text.count("\n", 0, match.start()) + 1,
        ))
        if len(result) >= limit:
            break
    return result

async def replace_book_moderation_findings(book_id: int, findings: list[ModerationFinding]) -> None:
    await ensure_moderation_findings_schema()
    async with connect() as db:
        await db.execute("DELETE FROM book_moderation_findings WHERE book_id=? AND status='open'", (int(book_id),))
        for item in findings[:500]:
            await db.execute("""
                INSERT INTO book_moderation_findings(
                    book_id, chapter_id, chapter_number, chapter_title, category, severity,
                    reason, matched_text, context, character_offset, line_number, status, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (int(book_id), item.chapter_id, item.chapter_number, item.chapter_title,
                  item.category, item.severity, item.reason, item.matched_text, item.context,
                  item.character_offset, item.line_number, 'open', utc_now()))
        await db.commit()

async def list_book_moderation_findings(book_id: int, *, limit: int = 50, offset: int = 0) -> list[Any]:
    await ensure_moderation_findings_schema()
    async with connect() as db:
        cur = await db.execute("""
            SELECT * FROM book_moderation_findings
            WHERE book_id=? AND status='open'
            ORDER BY CASE severity WHEN 'block' THEN 0 ELSE 1 END, chapter_number, character_offset
            LIMIT ? OFFSET ?
        """, (int(book_id), max(1, min(100, int(limit))), max(0, int(offset))))
        return await cur.fetchall()

async def count_book_moderation_findings(book_id: int) -> int:
    await ensure_moderation_findings_schema()
    async with connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM book_moderation_findings WHERE book_id=? AND status='open'", (int(book_id),))
        row = await cur.fetchone()
        return int(row[0] or 0)

async def resolve_book_moderation_findings(book_id: int) -> None:
    await ensure_moderation_findings_schema()
    async with connect() as db:
        await db.execute("UPDATE book_moderation_findings SET status='resolved', resolved_at=? WHERE book_id=? AND status='open'", (utc_now(), int(book_id)))
        await db.commit()

async def evaluate_book_for_auto_publication(book_id: int, *, actor_telegram_id: int | None = None,
                                               revision_mode: bool = False) -> AutoModerationResult:
    book = await get_book(int(book_id))
    if not book:
        return AutoModerationResult(False, ["Книга не найдена."])

    chapters = await list_chapters_for_book(int(book_id), published_only=False)
    graphic_chapters = await list_graphic_chapters_for_book(int(book_id), published_only=False)
    active_chapters = [row for row in chapters if str(row["status"] or "") != "deleted"]
    texts = [str(row["text"] or "") for row in active_chapters]
    graphics = [row for row in graphic_chapters if str(row["status"] or "") != "deleted"]
    total_characters = sum(len(text.strip()) for text in texts)
    total_graphic_pages = sum(int(row["pages_count"] or row["actual_pages_count"] or 0) for row in graphics)
    hard_reasons: list[str] = []
    review_signals: list[tuple[str, str]] = []
    findings: list[ModerationFinding] = []

    is_graphic = str(book["content_type"] or "book") != "book"
    if is_graphic:
        if not graphics or total_graphic_pages < 1:
            hard_reasons.append("В графическом произведении нет страниц для проверки.")
        if any(int(row["pages_count"] or row["actual_pages_count"] or 0) < 1 for row in graphics):
            hard_reasons.append("Есть пустая графическая глава.")
        if total_graphic_pages < 3:
            review_signals.append(("short_graphic", "Слишком мало страниц для надёжной автоматической проверки."))
    else:
        if not texts:
            hard_reasons.append("В книге нет глав для проверки.")
        elif total_characters < 2000:
            review_signals.append(("short_book", "Объём книги слишком мал для надёжной автоматической проверки."))
        if any(0 < len(text.strip()) < 300 for text in texts):
            review_signals.append(("short_chapter", "Есть слишком короткая глава — нужна ручная проверка структуры."))

    if len(str(book["title"] or "").strip()) < 2:
        hard_reasons.append("Название книги не заполнено.")
    if len(str(book["description"] or "").strip()) < 60:
        review_signals.append(("short_description", "Описание слишком короткое или отсутствует."))
    if not str(book["cover_path"] or "").strip() and not str(book["cover_file_id"] or "").strip():
        review_signals.append(("missing_cover", "Обложка не загружена."))

    age_digits = int(re.sub(r"\D", "", str(book["age_limit"] or "0+")) or 0)
    for chapter, text in zip(active_chapters, texts):
        if not text:
            continue
        findings += _finding(_URL_RE, text, category="external_link", severity="block",
            reason="Внешняя ссылка", chapter=chapter, allow_internal=True)
        findings += _finding(_EXTERNAL_PAYMENT_RE, text, category="external_payment", severity="block",
            reason="Возможная оплата вне VoxLyra", chapter=chapter, limit=20)
        findings += _finding(_FORBIDDEN_PROMO_RE, text, category="promotion", severity="block",
            reason="Реклама или запрещённый призыв", chapter=chapter, limit=30)
        findings += _finding(_LONG_REPEAT_RE, text, category="damaged_text", severity="block",
            reason="Повреждённый текст или спам", chapter=chapter, limit=10)
        profanity_category = "profanity_underage" if age_digits < 18 else "profanity"
        profanity_severity = "block" if age_digits < 18 else "review"
        findings += _finding(_PROFANITY_RE, text, category=profanity_category, severity=profanity_severity,
            reason=("Ненормативная лексика при рейтинге ниже 18+" if age_digits < 18 else "Высокая плотность ненормативной лексики"),
            chapter=chapter, limit=50)

    categories = {f.category for f in findings}
    if "external_link" in categories:
        hard_reasons.append("В тексте найдены внешние ссылки. Откройте список совпадений.")
    if "external_payment" in categories:
        hard_reasons.append("Найдена возможная просьба об оплате вне платформы. Откройте список совпадений.")
    if "promotion" in categories:
        hard_reasons.append("Обнаружены рекламные или запрещённые призывы. Откройте список совпадений.")
    if "damaged_text" in categories:
        hard_reasons.append("Обнаружен повреждённый текст или спам. Откройте список совпадений.")
    profanity = [f for f in findings if f.category in {"profanity", "profanity_underage"}]
    if profanity and age_digits < 18:
        hard_reasons.append("Обнаружена ненормативная лексика при возрастном ограничении ниже 18+. Откройте список совпадений.")
    elif len(profanity) > max(30, total_characters // 1500):
        review_signals.append(("profanity", "Слишком высокая плотность ненормативной лексики — нужна ручная проверка контекста."))

    matches = await find_book_duplicates(
        title=str(book["title"] or ""),
        author_id=int(book["author_id"]) if book["author_id"] is not None else None,
        exclude_book_id=int(book_id),
        source_file_hash=str(book["source_file_hash"] or ""),
    )
    if matches and not bool(book["duplicate_override"]):
        hard_reasons.append(duplicate_warning_text(matches))
    stats = await get_author_auto_moderation_stats(
        int(book["author_id"]) if book["author_id"] is not None else None,
        int(book_id),
    )
    if stats["open_complaints"] > 0:
        hard_reasons.append("У автора есть незавершённые жалобы.")

    trusted_categories = await get_trusted_moderation_categories()
    visible_findings = [
        item for item in findings
        if item.severity == "block" or item.category not in trusted_categories
    ]
    for category, reason in review_signals:
        if category in trusted_categories:
            continue
        visible_findings.append(ModerationFinding(
            category=category,
            severity="review",
            reason=reason,
            matched_text="",
            context=reason,
        ))
    await replace_book_moderation_findings(int(book_id), visible_findings)
    review_reasons = [reason for category, reason in review_signals if category not in trusted_categories]
    enabled = await is_auto_moderation_enabled()
    is_owner_upload = actor_telegram_id is not None and int(actor_telegram_id) in settings.owner_ids

    if not enabled:
        reasons = hard_reasons + review_reasons + ["Автомодерация отключена владельцем. Требуется ручная проверка."]
        can_auto_publish = False
    elif is_owner_upload:
        reasons = hard_reasons
        can_auto_publish = not reasons
    else:
        reasons = hard_reasons + review_reasons
        if not revision_mode:
            reasons.append("Требуется подтверждение модератора перед первой публикацией этой версии книги.")
        can_auto_publish = not reasons

    unique_reasons = list(dict.fromkeys(item.strip() for item in reasons if item.strip()))
    return AutoModerationResult(
        can_auto_publish and not unique_reasons,
        unique_reasons,
        checked_chapters=len(texts) + len(graphics),
        total_characters=total_characters,
        findings=visible_findings,
    )


def evaluate_metadata_text(value: str, *, age_limit: str = "0+", field_name: str = "поле") -> list[str]:
    text = str(value or "").strip(); reasons: list[str] = []
    links = [m for m in _URL_RE.finditer(text) if not _ALLOWED_INTERNAL_URL_RE.search(m.group(0))]
    if links: reasons.append(f"В поле «{field_name}» нельзя размещать внешние ссылки: {links[0].group(0)[:120]}")
    match = _EXTERNAL_PAYMENT_RE.search(text)
    if match: reasons.append(f"В поле «{field_name}» найдена просьба об оплате вне VoxLyra: {match.group(0)[:120]}")
    match = _FORBIDDEN_PROMO_RE.search(text)
    if match: reasons.append(f"В поле «{field_name}» обнаружена реклама или запрещённый призыв: {match.group(0)[:120]}")
    age_digits = int(re.sub(r"\D", "", age_limit or "0+") or 0)
    match = _PROFANITY_RE.search(text)
    if match and age_digits < 18: reasons.append(f"Ненормативная лексика допустима только для 18+: {match.group(0)}")
    return list(dict.fromkeys(reasons))
