from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pricing_ui_is_explicit_and_consistent():
    author_html = (ROOT / "templates" / "author.html").read_text(encoding="utf-8")
    book_html = (ROOT / "templates" / "book.html").read_text(encoding="utf-8")
    macros = (ROOT / "templates" / "_macros.html").read_text(encoding="utf-8")
    author_js = (ROOT / "static" / "js" / "author.js").read_text(encoding="utf-8")

    assert "Условия доступа и цена всей книги" in author_html
    assert "Доступ одной главы или диапазона" in author_html
    assert "Цена только этой главы" in author_html
    assert "0 Stars означает полностью бесплатную книгу" in author_html
    assert "Только вся книга целиком" in author_html
    assert "Вся книга и выбранные главы отдельно" in author_html
    assert "Цена этой главы:" in book_html
    assert "Купить всю {% if is_graphic %}графическую историю{% else %}книгу{% endif %}" in book_html
    assert "Вся книга:" in macros
    assert "/chapter-prices" in author_js
    assert "pricingMode === 'free' ? 100000 : 3" in author_js
    assert "access_mode: accessMode" in author_js
    assert "confirm_make_free" in author_js


def test_pricing_database_contract_enforces_the_three_modes():
    db_source = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
    assert "async def update_book_price" in db_source
    assert "async def update_chapter_access_range" in db_source
    assert 'reason": "book_is_free"' in db_source
    assert 'reason": "chapter_sales_disabled"' in db_source
    assert "async def restore_saved_chapter_prices" in db_source
