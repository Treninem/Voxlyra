from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_platform_pricing_adapter_loads_after_page_specific_scripts():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    page_scripts = "{% block scripts %}{% endblock %}"
    adapter = '<script src="/static/js/platform-pricing.js?v={{ asset_version }}"></script>'
    assert page_scripts in base
    assert adapter in base
    assert base.index(page_scripts) < base.index(adapter)


def test_vk_author_pricing_adapter_covers_every_sale_price_editor():
    template = (ROOT / "templates" / "author.html").read_text(encoding="utf-8")
    source = (ROOT / "static" / "js" / "platform-pricing.js").read_text(encoding="utf-8")
    ids = (
        "bookPriceInput",
        "graphicChapterPrice",
        "chapterPackagePriceInput",
        "graphicChapterEditPrice",
        "chapterBulkPriceInput",
        "chapterPriceInput",
    )
    for element_id in ids:
        assert f'id="{element_id}"' in template
        assert f"#{element_id}" in source
    assert "[data-volume-price]" in source


def test_vk_author_pricing_uses_same_round_up_direction_as_checkout():
    source = (ROOT / "static" / "js" / "platform-pricing.js").read_text(encoding="utf-8")
    assert "Math.ceil(value * ratio())" in source
    assert "Math.ceil(value / ratio())" in source
    assert 'meta[name="voxlyra-vk-votes-per-star"]' in source
    assert "temporary-canonical" in source
    assert "голосов VK" in source
    assert "эквивалент в Stars рассчитывается автоматически" in source


def test_adapter_only_changes_author_price_inputs_in_vk():
    source = (ROOT / "static" / "js" / "platform-pricing.js").read_text(encoding="utf-8")
    assert "window.voxPlatform() === 'vk'" in source
    assert "if (!isVK()) return;" in source
    assert "event.target.closest('#authorStudio')" in source
    assert "filter((input) => !input.closest('[hidden]'))" in source


def test_public_vk_price_and_checkout_still_share_one_converter(monkeypatch):
    from app.services import cross_platform_publication as publication
    from app.services import vk_payments

    monkeypatch.setattr(publication.settings, "VK_VOTES_PER_STAR", 2.0)
    monkeypatch.setattr(vk_payments.settings, "VK_VOTES_PER_STAR", 2.0)
    assert publication.vk_votes_from_stars(7) == 14
    assert publication.vk_votes_from_stars(7) == vk_payments.votes_for_stars(7)
