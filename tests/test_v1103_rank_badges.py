from pathlib import Path

from app.services.rankings import _score, _select_visible_badge


def test_ranking_score_prefers_purchases_and_real_readers():
    empty = {
        "transactions": 0.0,
        "buyers": 0.0,
        "stars": 0.0,
        "active_users": 0.0,
        "activity_marks": 0.0,
        "completions": 0.0,
        "bookmarks": 0.0,
        "reviews": 0.0,
        "rating_sum": 0.0,
    }
    read = dict(empty, active_users=10.0, activity_marks=80.0, completions=4.0)
    paid = dict(read, transactions=5.0, buyers=5.0, stars=250.0)
    assert _score(empty) == 0
    assert _score(read) > 0
    assert _score(paid) > _score(read)


def test_visible_badge_uses_best_place_then_longer_period():
    periods = {
        "year": {"rank": 2, "period": "year"},
        "month": {"rank": 1, "period": "month"},
        "day": {"rank": 1, "period": "day"},
    }
    selected = _select_visible_badge(periods)
    assert selected is periods["month"]


def test_book_cards_show_rank_at_right_side():
    macro = Path("templates/_macros.html").read_text(encoding="utf-8")
    css = Path("static/css/style.css").read_text(encoding="utf-8")
    webapp = Path("app/webapp.py").read_text(encoding="utf-8")
    assert "book-meta-line" in macro
    assert "#{{ book.top_rank }} {{ book.top_period_label }}" in macro
    for period in ("year", "month", "week", "day"):
        assert f".top-rank-{period}" in css
    assert "attach_rankings" in webapp
    assert 'category="audio"' in webapp
    assert 'category="comic"' in webapp
