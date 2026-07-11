def recommend_price(base_units: int, finished: bool = False, has_audio: bool = False) -> int:
    """Черновой расчёт цены в Stars.

    base_units — условный объём: главы, тысячи знаков или минуты аудио.
    Формула учитывает базовый объём и тип продажи. Владелец платформы может менять правила цены через настройки комиссий и тарифов.
    """
    price = max(0, base_units) * 3
    if finished:
        price = int(price * 1.2)
    if has_audio:
        price = int(price * 1.5)
    return max(price, 0)


def recommend_book_price(description: str, pricing_type: str) -> int:
    words = len((description or "").split())
    base = max(20, words // 5)
    if pricing_type == "chapters":
        return max(3, min(15, base // 4))
    if pricing_type == "whole_book":
        return max(50, min(500, base * 2))
    if pricing_type == "subscription":
        return max(30, min(300, base))
    return 0


def split_platform_commission(final_minor: int, commission_percent: int = 20) -> dict[str, int]:
    """Разделяет уже показанную покупателю итоговую цену.

    Цена не увеличивается скрыто в момент оплаты. Комиссия округляется до
    ближайшей минимальной денежной единицы, остаток принадлежит автору.
    """
    gross = max(0, int(final_minor))
    percent = max(0, min(100, int(commission_percent)))
    commission = (gross * percent + 50) // 100
    return {
        "gross_minor": gross,
        "commission_percent": percent,
        "commission_minor": commission,
        "author_minor": max(0, gross - commission),
    }


def final_price_for_desired_net(desired_net_minor: int, commission_percent: int = 20) -> int:
    """Минимальная итоговая цена, дающая автору не меньше желаемой суммы."""
    from math import ceil

    desired = max(0, int(desired_net_minor))
    percent = max(0, min(99, int(commission_percent)))
    if desired == 0:
        return 0
    candidate = ceil(desired * 100 / (100 - percent))
    while split_platform_commission(candidate, percent)["author_minor"] < desired:
        candidate += 1
    return candidate


def rubles_to_stars(rubles_minor: int, rubles_per_star_minor: int) -> int:
    """Переводит цену в копейках в целое число Stars по внутреннему курсу платформы.

    Это расчётный эквивалент каталога, а не гарантированная стоимость покупки Stars
    конкретным пользователем: она может отличаться из-за платформы, налогов и сборов.
    """
    from math import ceil
    amount = max(0, int(rubles_minor))
    rate = max(1, int(rubles_per_star_minor))
    return 0 if amount == 0 else max(1, ceil(amount / rate))


def stars_to_rubles_minor(stars: int, rubles_per_star_minor: int) -> int:
    """Возвращает расчётный рублёвый эквивалент цены в Stars."""
    return max(0, int(stars)) * max(1, int(rubles_per_star_minor))


def dual_price(stars: int, rubles_per_star_minor: int) -> dict[str, int]:
    stars_value = max(0, int(stars))
    return {
        "stars": stars_value,
        "rubles_minor": stars_to_rubles_minor(stars_value, rubles_per_star_minor),
        "rate_minor": max(1, int(rubles_per_star_minor)),
    }


def buyer_price_estimate_minor(stars: int, buyer_rate_minor: int) -> int:
    """Ориентировочная рублёвая стоимость для покупателя.

    Это не кассовая сумма и не обещанный курс Telegram: покупатель фактически
    оплачивает Stars, а стоимость их приобретения определяет Telegram.
    """
    return max(0, int(stars)) * max(1, int(buyer_rate_minor))


def author_settlement_preview(
    stars: int,
    commission_percent: int,
    author_rate_minor: int,
) -> dict[str, int]:
    """Прозрачный расчёт вознаграждения автора по двум ступеням.

    Сначала из цены в Stars удерживается комиссия платформы. Затем чистые Stars
    автора переводятся в рублёвое обязательство по курсу, зафиксированному в
    момент продажи.
    """
    gross_stars = max(0, int(stars))
    percent = max(0, min(100, int(commission_percent)))
    commission_stars = int(round(gross_stars * percent / 100))
    net_stars = max(0, gross_stars - commission_stars)
    rate = max(1, int(author_rate_minor))
    return {
        "gross_stars": gross_stars,
        "commission_percent": percent,
        "commission_stars": commission_stars,
        "net_stars": net_stars,
        "author_rate_minor": rate,
        "author_net_minor": net_stars * rate,
    }


def two_rate_price(
    stars: int,
    buyer_rate_minor: int,
    author_rate_minor: int,
    commission_percent: int = 20,
) -> dict[str, int]:
    """Полная витринная раскладка без скрытой рублёвой оплаты."""
    settlement = author_settlement_preview(stars, commission_percent, author_rate_minor)
    return {
        **settlement,
        "buyer_rate_minor": max(1, int(buyer_rate_minor)),
        "buyer_estimate_minor": buyer_price_estimate_minor(stars, buyer_rate_minor),
        "spread_minor_per_star": max(0, int(buyer_rate_minor) - int(author_rate_minor)),
    }
