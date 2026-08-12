from app.services import cross_platform_publication as cpp


def test_vk_book_link_targets_vk_mini_app(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_APP_ID", 123456)
    assert cpp.vk_book_url(42) == "https://vk.com/app123456#book_42"
    assert "t.me" not in cpp.vk_book_url(42)


def test_vk_post_uses_votes_and_never_mentions_stars(monkeypatch):
    monkeypatch.setattr(cpp.settings, "VK_VOTES_PER_STAR", 2.0)
    text = cpp.build_vk_book_post(
        title="Книга", author="Автор", genres=["Фэнтези"], age_limit="16+",
        chapters_count=3, has_audio=False, description="Описание",
        pricing_type="full", price_stars=7,
        book_url="https://vk.com/app123#book_1",
    )
    assert "14 голосов VK" in text
    assert "Stars" not in text
    assert "Telegram" not in text
    assert "https://vk.com/app123#book_1" in text


def test_vk_chapter_pricing_is_vk_only():
    text = cpp.build_vk_book_post(
        title="Книга", author="Автор", genres=[], age_limit="",
        chapters_count=1, has_audio=False, description="",
        pricing_type="chapters", price_stars=0,
        book_url="https://vk.com/app1#book_1",
    )
    assert "оплата голосами VK" in text
    assert "Stars" not in text
