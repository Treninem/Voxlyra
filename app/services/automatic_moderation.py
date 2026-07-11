from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import settings
from app.db import (
    get_author_auto_moderation_stats,
    get_book,
    list_chapters_for_book,
    list_graphic_chapters_for_book,
)
from app.services.duplicate_books import duplicate_warning_text, find_book_duplicates

_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
_LONG_REPEAT_RE = re.compile(r"(.)\1{24,}", re.DOTALL)
_EXTERNAL_PAYMENT_RE = re.compile(
    r"(?:перевед(?:и|ите)|оплат(?:а|ить)|скин(?:ь|ьте)).{0,45}(?:карт(?:у|е)|сбер|тинькофф|qiwi|кошел[её]к)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(slots=True)
class AutoModerationResult:
    auto_publish: bool
    reasons: list[str]
    checked_chapters: int = 0
    total_characters: int = 0

    @property
    def risk_level(self) -> str:
        return "clear" if self.auto_publish else "manual"


async def evaluate_book_for_auto_publication(
    book_id: int,
    *,
    actor_telegram_id: int | None = None,
) -> AutoModerationResult:
    """Консервативная проверка: сомнение ведёт к человеку, но не к автоблокировке."""
    book = await get_book(int(book_id))
    if not book:
        return AutoModerationResult(False, ["Книга не найдена."])

    chapters = await list_chapters_for_book(int(book_id), published_only=False)
    graphic_chapters = await list_graphic_chapters_for_book(int(book_id), published_only=False)
    texts = [str(row["text"] or "") for row in chapters if str(row["status"] or "") != "deleted"]
    graphics = [row for row in graphic_chapters if str(row["status"] or "") != "deleted"]
    total_characters = sum(len(text.strip()) for text in texts)
    total_graphic_pages = sum(int(row["pages_count"] or row["actual_pages_count"] or 0) for row in graphics)
    hard_reasons: list[str] = []
    review_reasons: list[str] = []

    is_graphic = str(book["content_type"] or "book") != "book"
    if is_graphic:
        if not graphics or total_graphic_pages < 1:
            hard_reasons.append("В графическом произведении нет страниц для проверки.")
        if any(int(row["pages_count"] or row["actual_pages_count"] or 0) < 1 for row in graphics):
            hard_reasons.append("Есть пустая графическая глава.")
        if total_graphic_pages < 3:
            review_reasons.append("Слишком мало страниц для надёжной автоматической проверки.")
    else:
        if not texts:
            hard_reasons.append("В книге нет глав для проверки.")
        elif total_characters < 2000:
            review_reasons.append("Объём книги слишком мал для надёжной автоматической проверки.")
        if any(0 < len(text.strip()) < 300 for text in texts):
            review_reasons.append("Есть слишком короткая глава — нужна ручная проверка структуры.")

    if len(str(book["title"] or "").strip()) < 2:
        hard_reasons.append("Название книги не заполнено.")
    if len(str(book["description"] or "").strip()) < 60:
        review_reasons.append("Описание слишком короткое или отсутствует.")
    if not str(book["cover_path"] or "").strip() and not str(book["cover_file_id"] or "").strip():
        review_reasons.append("Обложка не загружена.")

    combined = "\n".join(texts)
    if combined:
        if len(_URL_RE.findall(combined)) > 4:
            hard_reasons.append("В тексте много внешних ссылок.")
        if _LONG_REPEAT_RE.search(combined):
            hard_reasons.append("Обнаружены длинные повторяющиеся символы, похожие на повреждение или спам.")
        if _EXTERNAL_PAYMENT_RE.search(combined):
            hard_reasons.append("Найдена возможная просьба об оплате вне платформы.")

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

    is_owner_upload = actor_telegram_id is not None and int(actor_telegram_id) in settings.owner_ids
    if is_owner_upload:
        # Владелец уже является ответственным проверяющим. Недостающая обложка или короткое
        # описание не мешают публикации, но технические и антиспам-нарушения всё равно останавливают её.
        reasons = hard_reasons
    else:
        # Правила и регулярные выражения не гарантируют распознавание всех нарушений
        # художественного текста. Поэтому обычная книга проходит предварительную проверку,
        # а окончательное решение принимает человек. Это исключает ложную автоблокировку.
        reasons = hard_reasons + review_reasons
        reasons.append("Требуется подтверждение модератора перед публикацией этой версии книги.")

    unique_reasons = list(dict.fromkeys(item.strip() for item in reasons if item.strip()))
    return AutoModerationResult(
        auto_publish=is_owner_upload and not unique_reasons,
        reasons=unique_reasons,
        checked_chapters=len(texts) + len(graphics),
        total_characters=total_characters,
    )
