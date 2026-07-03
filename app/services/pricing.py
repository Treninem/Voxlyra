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
