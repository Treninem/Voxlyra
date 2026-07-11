from app.services.pricing import (
    author_settlement_preview,
    buyer_price_estimate_minor,
    dual_price,
    rubles_to_stars,
    stars_to_rubles_minor,
    two_rate_price,
)


def test_rubles_to_stars_rounds_up():
    assert rubles_to_stars(1000, 200) == 5
    assert rubles_to_stars(1001, 200) == 6


def test_stars_to_rubles_minor():
    assert stars_to_rubles_minor(5, 200) == 1000


def test_legacy_dual_price_helper_remains_compatible():
    assert dual_price(7, 250) == {"stars": 7, "rubles_minor": 1750, "rate_minor": 250}


def test_v196_two_rate_example_keeps_commission_separate():
    result = two_rate_price(10, buyer_rate_minor=145, author_rate_minor=100, commission_percent=20)
    assert result["buyer_estimate_minor"] == 1450
    assert result["commission_stars"] == 2
    assert result["net_stars"] == 8
    assert result["author_net_minor"] == 800
    assert result["spread_minor_per_star"] == 45


def test_v196_author_rate_applies_only_after_commission():
    result = author_settlement_preview(25, 20, 100)
    assert result["commission_stars"] == 5
    assert result["net_stars"] == 20
    assert result["author_net_minor"] == 2000
    assert buyer_price_estimate_minor(25, 145) == 3625
