from __future__ import annotations

from dataclasses import dataclass

from app.db import (
    get_ad_campaign, get_audio_chapter, get_book, get_chapter, get_channel_promotion,
    get_promo_for_book, has_purchase_access, has_graphic_volume_purchase, get_graphic_chapter, get_graphic_volume,
    get_chapter_package,
)


@dataclass(frozen=True)
class PayTarget:
    kind: str
    target_id: int
    title: str
    description: str
    amount_stars: int
    payload: str


def make_payload(kind: str, target_id: int, promo_code: str | None = None, amount_stars: int | None = None) -> str:
    if kind == "ad_budget":
        if amount_stars is None:
            raise ValueError("amount_stars required for ad budget payload")
        return f"vox:ad_budget:{int(target_id)}:{int(amount_stars)}"
    if kind == "channel_promo":
        return f"vox:channel_promo:{int(target_id)}"
    if kind == "graphic_volume":
        if amount_stars is None:
            raise ValueError("book_id required for graphic volume payload")
        return f"vox:graphic_volume:{int(amount_stars)}:{int(target_id)}"
    if kind == "chapter_package":
        return f"vox:chapter_package:{int(target_id)}"
    base = f"vox:{kind}:{int(target_id)}"
    if promo_code:
        return f"{base}:promo:{promo_code.strip().upper()}"
    return base


def _apply_discount(amount: int, discount_percent: int) -> int:
    amount = max(0, int(amount))
    discount_percent = max(0, min(100, int(discount_percent)))
    if discount_percent >= 100:
        return 0
    return max(1, int(round(amount * (100 - discount_percent) / 100)))


async def build_pay_target(kind: str, target_id: int, user_id: int | None = None,
                           promo_code: str | None = None, amount_stars: int | None = None) -> PayTarget | None:
    if kind == "channel_promo":
        promotion = await get_channel_promotion(target_id)
        if not promotion or promotion["status"] not in {"invoice", "paid", "failed"}:
            return None
        amount = int(promotion["amount_stars"] or 0)
        if amount <= 0 or promotion["publication_status"] != "published":
            return None
        title = "Публикация книги в канале"
        description = f"Повторный пост книги «{promotion['book_title']}». Не чаще одного раза в 30 дней."
        return PayTarget(kind, target_id, title[:32], description[:255], amount, make_payload(kind, target_id))

    if kind == "ad_budget":
        campaign = await get_ad_campaign(target_id)
        if not campaign:
            return None
        amount = int(amount_stars or 0)
        if amount <= 0:
            return None
        title = "Пополнение рекламы"
        description = f"Продвижение книги: {campaign['book_title']}"
        return PayTarget(kind, target_id, title[:32], description[:255], amount, make_payload(kind, target_id, amount_stars=amount))

    book_id = None
    base_amount = 0
    title = ""
    description = ""
    if kind == "chapter":
        chapter = await get_chapter(target_id)
        if not chapter:
            return None
        book_id = int(chapter["book_id"])
        book_price = int(chapter["book_price_stars"] or 0)
        mode = "free" if book_price <= 0 else ("chapters" if str(chapter["pricing_type"] or "") == "chapters" else "whole_book")
        if mode == "free" or int(chapter["is_free"] or 0) == 1:
            return PayTarget(kind, target_id, chapter["title"], "Глава бесплатная", 0, make_payload(kind, target_id))
        if user_id is not None and await has_purchase_access(user_id, chapter_id=target_id):
            return PayTarget(kind, target_id, chapter["title"], "Доступ уже открыт", 0, make_payload(kind, target_id))
        if mode != "chapters" or int(chapter["price_stars"] or 0) <= 0:
            return PayTarget(
                kind, target_id, chapter["title"],
                "Эта глава открывается после покупки всей книги и отдельно не продаётся.",
                0, make_payload(kind, target_id),
            )
        title = f"Глава: {chapter['title']}"
        description = f"{chapter['book_title']} · покупка только этой текстовой главы"
        base_amount = int(chapter["price_stars"])

    elif kind == "audio":
        audio = await get_audio_chapter(target_id)
        if not audio:
            return None
        book_id = int(audio["book_id"])
        if int(audio["is_free"] or 0) == 1 or int(audio["price_stars"] or 0) <= 0:
            return PayTarget(kind, target_id, audio["title"], "Аудиоглава бесплатная", 0, make_payload(kind, target_id))
        if user_id is not None and await has_purchase_access(user_id, audio_chapter_id=target_id):
            return PayTarget(kind, target_id, audio["title"], "Доступ уже открыт", 0, make_payload(kind, target_id))
        title = f"Аудио: {audio['title']}"
        description = f"{audio['book_title']} · доступ к аудиоглаве"
        base_amount = int(audio["price_stars"])

    elif kind == "graphic":
        chapter = await get_graphic_chapter(target_id)
        if not chapter:
            return None
        book_id = int(chapter["book_id"])
        if str(chapter["status"] or "") != "published" or str(chapter["publication_status"] or "") != "published":
            return None
        if int(chapter["is_free"] or 0) == 1 or int(chapter["price_stars"] or 0) <= 0:
            return PayTarget(kind, target_id, chapter["title"], "Графическая глава бесплатная", 0, make_payload(kind, target_id))
        if user_id is not None and await has_purchase_access(user_id, graphic_chapter_id=target_id):
            return PayTarget(kind, target_id, chapter["title"], "Доступ уже открыт", 0, make_payload(kind, target_id))
        title = f"Глава: {chapter['title']}"
        description = f"{chapter['book_title']} · доступ к графической главе"
        base_amount = int(chapter["price_stars"])

    elif kind == "graphic_volume":
        # target_id — номер тома, amount_stars здесь временно используется как book_id
        if amount_stars is None:
            return None
        volume = await get_graphic_volume(int(amount_stars), target_id)
        if not volume or str(volume["publication_status"] or "") != "published":
            return None
        book_id = int(volume["book_id"])
        if int(volume["is_free"] or 0) == 1 or int(volume["price_stars"] or 0) <= 0:
            return PayTarget(kind, target_id, f"Том {target_id}", "Том бесплатный", 0, make_payload(kind, target_id, amount_stars=book_id))
        if user_id is not None and await has_graphic_volume_purchase(user_id, book_id, target_id):
            return PayTarget(kind, target_id, f"Том {target_id}", "Доступ уже открыт", 0, make_payload(kind, target_id, amount_stars=book_id))
        title = f"Том {target_id}: {volume['book_title']}"
        description = f"Полный доступ к тому {target_id} графического произведения"
        base_amount = int(volume["price_stars"])

    elif kind == "chapter_package":
        package = await get_chapter_package(target_id)
        if not package:
            return None
        book_id = int(package["book_id"])
        if int(package["is_active"] or 0) != 1 or str(package["publication_status"] or "") != "published":
            return None
        title = str(package["title"] or f"Пакет на {int(package['chapters_count'] or 0)} глав")
        description = (
            f"{package['book_title']} · {int(package['chapters_count'] or 0)} любых платных глав "
            "на выбор читателя"
        )
        base_amount = int(package["price_stars"] or 0)
        if base_amount <= 0:
            return None

    elif kind == "book":
        book = await get_book(target_id)
        if not book:
            return None
        book_id = int(book["id"])
        if int(book["price_stars"] or 0) <= 0:
            return PayTarget(kind, target_id, book["title"], "Покупка всей книги не включена", 0, make_payload(kind, target_id))
        if user_id is not None and await has_purchase_access(user_id, book_id=target_id):
            return PayTarget(kind, target_id, book["title"], "Доступ уже открыт", 0, make_payload(kind, target_id))
        title = f"Книга: {book['title']}"
        description = "Полный доступ к книге"
        base_amount = int(book["price_stars"])
    else:
        return None

    promo_payload = None
    amount = base_amount
    if promo_code and book_id is not None:
        promo = await get_promo_for_book(promo_code, book_id)
        if promo:
            amount = _apply_discount(base_amount, int(promo["discount_percent"] or 0))
            promo_payload = str(promo["code"])
            description = f"{description}. Промокод {promo['code']}: скидка {promo['discount_percent']}%."
    if kind == "graphic_volume":
        return PayTarget(kind, target_id, title[:32], description[:255], amount, make_payload(kind, target_id, amount_stars=book_id))
    return PayTarget(kind, target_id, title[:32], description[:255], amount, make_payload(kind, target_id, promo_payload))


def describe_purchase_row(row) -> str:
    if row["purchase_kind"] == "chapter_package":
        title = row["chapter_package_title"] if "chapter_package_title" in row.keys() else "Пакет глав"
        remaining = row["chapter_package_remaining"] if "chapter_package_remaining" in row.keys() else None
        target = f"Пакет глав: {title}" + (f" · осталось {remaining}" if remaining is not None else "")
    elif row["purchase_kind"] == "ad_budget":
        target = "Пополнение рекламы"
    elif row["purchase_kind"] == "channel_promotion":
        target = f"Публикация в канале: {row['book_title'] or 'книга'}"
    elif "graphic_volume_number" in row.keys() and row["graphic_volume_number"]:
        title = row["graphic_volume_title"] if "graphic_volume_title" in row.keys() else ""
        target = f"Том {row['graphic_volume_number']}: {title or row['book_title'] or 'произведение'}"
    elif "graphic_chapter_title" in row.keys() and row["graphic_chapter_title"]:
        target = f"Графическая глава: {row['graphic_chapter_title']}"
    elif row["chapter_title"]:
        target = f"Глава: {row['chapter_title']}"
    elif row["audio_title"]:
        target = f"Аудио: {row['audio_title']}"
    elif row["book_title"]:
        target = f"Книга: {row['book_title']}"
    else:
        target = "Покупка"
    status = {
        "paid": "оплачено",
        "refunded": "возврат",
        "disputed": "спор",
    }.get(row["status"], row["status"])
    return f"{target} · {row['amount_stars']} Stars · {status}"
