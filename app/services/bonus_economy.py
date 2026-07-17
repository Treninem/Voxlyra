from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db import get_setting, set_setting

BONUS_POINTS_PER_STAR_DEFAULT = 100
REFERRAL_PERCENT_OF_BONUS_DEFAULT = 30
TOPUP_PACKAGES_DEFAULT = (50, 100, 250, 500, 1000)


@dataclass(frozen=True)
class RevenueSplitSettings:
    author_percent: int
    platform_percent: int
    bonus_percent: int
    points_per_star: int
    referral_percent_of_bonus: int
    topup_packages: tuple[int, ...]

    @property
    def non_author_percent(self) -> int:
        return self.platform_percent + self.bonus_percent


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return int(default)


def _parse_packages(value: Any) -> tuple[int, ...]:
    if isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = str(value or "").replace(";", ",").split(",")
    parsed: list[int] = []
    for part in parts:
        text = str(part).strip()
        if not text.isdigit():
            continue
        amount = int(text)
        if 1 <= amount <= 10000 and amount not in parsed:
            parsed.append(amount)
    return tuple(sorted(parsed)) or TOPUP_PACKAGES_DEFAULT


async def load_revenue_split_settings() -> RevenueSplitSettings:
    author = _bounded_int(await get_setting("revenue_author_percent", "80"), 80, 1, 99)
    platform = _bounded_int(await get_setting("revenue_platform_percent", "19"), 19, 0, 99)
    bonus = _bounded_int(await get_setting("revenue_bonus_percent", "1"), 1, 0, 25)
    if author + platform + bonus != 100:
        author, platform, bonus = 80, 19, 1
    # Курс уже начисленных баллов нельзя менять задним числом: иначе владелец
    # случайно обесценит или переоценит накопления пользователей. Поэтому курс
    # и реферальная доля фиксированы кодом; в меню меняются только 80/19/1.
    points = BONUS_POINTS_PER_STAR_DEFAULT
    referral = REFERRAL_PERCENT_OF_BONUS_DEFAULT
    packages = _parse_packages(await get_setting("wallet_topup_packages", ",".join(map(str, TOPUP_PACKAGES_DEFAULT))))
    return RevenueSplitSettings(
        author_percent=author,
        platform_percent=platform,
        bonus_percent=bonus,
        points_per_star=points,
        referral_percent_of_bonus=referral,
        topup_packages=packages,
    )


async def public_revenue_split_settings() -> dict[str, Any]:
    cfg = await load_revenue_split_settings()
    return {
        "author_percent": cfg.author_percent,
        "platform_percent": cfg.platform_percent,
        "bonus_percent": cfg.bonus_percent,
        "points_per_star": cfg.points_per_star,
        "referral_percent_of_bonus": cfg.referral_percent_of_bonus,
        "buyer_percent_of_bonus": 100 - cfg.referral_percent_of_bonus,
        "topup_packages": list(cfg.topup_packages),
        "bonus_star_value": 1 / cfg.points_per_star,
        "course_is_fixed": True,
        "referral_split_is_fixed": True,
        "rule": "author + platform + bonus = 100",
    }


async def update_revenue_split_settings(payload: dict[str, Any]) -> dict[str, Any]:
    current = await load_revenue_split_settings()
    author = _bounded_int(payload.get("author_percent", current.author_percent), current.author_percent, 1, 99)
    platform = _bounded_int(payload.get("platform_percent", current.platform_percent), current.platform_percent, 0, 99)
    bonus = _bounded_int(payload.get("bonus_percent", current.bonus_percent), current.bonus_percent, 0, 25)
    if author + platform + bonus != 100:
        raise ValueError("Доля автора, платформы и бонусов должна в сумме давать ровно 100%.")
    if author < 50:
        raise ValueError("Доля автора не может быть ниже 50%.")
    points = BONUS_POINTS_PER_STAR_DEFAULT
    referral = REFERRAL_PERCENT_OF_BONUS_DEFAULT
    packages = _parse_packages(payload.get("topup_packages", current.topup_packages))
    await set_setting("revenue_author_percent", str(author))
    await set_setting("revenue_platform_percent", str(platform))
    await set_setting("revenue_bonus_percent", str(bonus))
    await set_setting("bonus_points_per_star", str(points))
    await set_setting("referral_percent_of_bonus", str(referral))
    await set_setting("wallet_topup_packages", ",".join(map(str, packages)))
    # Старые комиссии синхронизируются с общей долей, чтобы старые экраны и
    # незатронутые контуры не показывали противоречивые цифры.
    total_non_author = platform + bonus
    await set_setting("commission_books", str(total_non_author))
    await set_setting("commission_audio", str(total_non_author))
    return await public_revenue_split_settings()


def allocate_revenue_stars(
    total_stars: int,
    *,
    author_percent: int,
    platform_percent: int,
    bonus_percent: int,
) -> dict[str, int]:
    """Split integer Stars with the largest-remainder method.

    The result contains only whole Stars and always sums exactly to total_stars.
    Tie priority favors the bonus reserve, then the platform, then the author;
    this prevents the small bonus share from being permanently rounded to zero.
    """
    total = max(0, int(total_stars))
    shares = {
        "author_stars": max(0, int(author_percent)),
        "platform_stars": max(0, int(platform_percent)),
        "bonus_pool_stars": max(0, int(bonus_percent)),
    }
    if sum(shares.values()) != 100:
        raise ValueError("Revenue shares must sum to 100")
    if total == 0:
        return {key: 0 for key in shares}
    allocated: dict[str, int] = {}
    ranking: list[tuple[int, int, str]] = []
    assigned = 0
    tie_priority = {"bonus_pool_stars": 0, "platform_stars": 1, "author_stars": 2}
    for key, percent in shares.items():
        base, remainder = divmod(total * percent, 100)
        allocated[key] = int(base)
        assigned += int(base)
        ranking.append((int(remainder), tie_priority[key], key))
    ranking.sort(key=lambda item: (-item[0], item[1]))
    for index in range(total - assigned):
        allocated[ranking[index % len(ranking)][2]] += 1
    return allocated


def topup_bonus_points(
    amount_stars: int,
    *,
    bonus_percent: int,
    points_per_star: int,
    referral_percent_of_bonus: int,
    has_referrer: bool,
) -> dict[str, int]:
    """Return fully-backed bonus points for a successful top-up.

    Example with defaults: 100 Stars -> 100 points = 1 Star of discount.
    With a referrer, 70 points go to the buyer and 30 to the inviter.
    """
    amount = max(0, int(amount_stars))
    points_rate = max(1, int(points_per_star))
    total_points = amount * max(0, int(bonus_percent)) * points_rate // 100
    if total_points <= 0:
        return {"total_points": 0, "buyer_points": 0, "referrer_points": 0}
    if not has_referrer:
        return {"total_points": total_points, "buyer_points": total_points, "referrer_points": 0}
    ref_pct = max(0, min(100, int(referral_percent_of_bonus)))
    referrer = total_points * ref_pct // 100
    buyer = total_points - referrer
    return {"total_points": total_points, "buyer_points": buyer, "referrer_points": referrer}


def bonus_discount_limit(
    price_stars: int,
    available_bonus_points: int,
    *,
    points_per_star: int,
    author_percent: int,
    platform_percent: int,
    bonus_percent: int,
) -> dict[str, int]:
    split = allocate_revenue_stars(
        price_stars,
        author_percent=author_percent,
        platform_percent=platform_percent,
        bonus_percent=bonus_percent,
    )
    available_stars = max(0, int(available_bonus_points)) // max(1, int(points_per_star))
    non_author = split["platform_stars"] + split["bonus_pool_stars"]
    discount = min(available_stars, non_author)
    return {
        **split,
        "available_bonus_stars": available_stars,
        "max_bonus_stars": non_author,
        "bonus_stars_used": discount,
        "wallet_stars_needed": max(0, int(price_stars) - discount),
    }
