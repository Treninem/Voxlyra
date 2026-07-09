from __future__ import annotations

from html import escape


def _clean_line(value: str, fallback: str = "") -> str:
    text = " ".join(str(value or "").split())
    return text or fallback


def _short_description(value: str, limit: int = 260) -> str:
    text = _clean_line(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,.;:-") + "…"


def build_new_book_post(
    *,
    title: str,
    author: str,
    genres: list[str] | tuple[str, ...],
    age_limit: str,
    chapters_count: int,
    has_audio: bool,
    description: str,
    pricing_type: str,
    price_stars: int,
    book_url: str,
    repeated: bool = False,
) -> str:
    """Собирает компактную премиальную карточку книги для Telegram-канала.

    Текст рассчитан на подпись к изображению (лимит Telegram — 1024 символа).
    Ссылка присутствует и отдельной строкой, и на кнопке публикации.
    """
    safe_title = escape(_clean_line(title, "Новая книга"))
    safe_author = escape(_clean_line(author, "Автор не указан"))
    genre_text = ", ".join(_clean_line(item) for item in genres[:3] if _clean_line(item)) or "Истории"
    safe_genres = escape(genre_text)
    safe_age = escape(_clean_line(age_limit, "16+"))
    safe_description = escape(_short_description(description, 260))
    audio_line = "\n🎧 <b>Есть аудиоверсия</b>" if has_audio else ""
    if str(pricing_type or "free") == "free" or int(price_stars or 0) <= 0:
        price_line = "Бесплатно"
    else:
        price_line = f"{int(price_stars)} Stars"
    heading = "✨ <b>Снова в центре внимания</b>" if repeated else "✨ <b>Новая книга на Вокслире</b>"
    description_block = f"\n\n<i>{safe_description}</i>" if safe_description else ""
    link_block = f"\n\n🔗 <b>Открыть книгу:</b>\n{escape(book_url)}" if book_url else ""
    return (
        f"{heading}\n\n"
        f"📖 <b>{safe_title}</b>\n"
        f"✍️ {safe_author}\n\n"
        f"🏷 {safe_genres}\n"
        f"🔞 {safe_age}  ·  📚 {int(chapters_count)} глав\n"
        f"💎 {escape(price_line)}"
        f"{audio_line}"
        f"{description_block}"
        f"{link_block}"
    )[:1024]
