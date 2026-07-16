from app.catalog_options import (
    AUDIENCES,
    BOOK_TYPES,
    CONTENT_WARNINGS,
    GENRES,
    TROPES,
    choices_in_section,
    recommended_audience_codes,
    recommended_genre_codes,
    recommended_trope_codes,
    recommended_warning_codes,
    sections_for_prefix,
    suggested_age_limit,
)
from app.keyboards import multi_select_menu, single_select_menu


def test_catalog_is_broad_and_unique():
    groups = [BOOK_TYPES, GENRES, TROPES, AUDIENCES, CONTENT_WARNINGS]
    minimums = [25, 220, 210, 45, 75]
    for items, minimum in zip(groups, minimums):
        assert len(items) >= minimum
        assert len({item.code for item in items}) == len(items)
        assert len({item.label for item in items}) == len(items)


def test_genre_recommendations_change_with_book_type():
    novel = recommended_genre_codes(["novel"])
    guide = recommended_genre_codes(["guide"])
    picture = recommended_genre_codes(["children_picture"])
    assert "fantasy" in novel
    assert "self_dev" in guide
    assert "picture_book" in picture
    assert novel != guide != picture


def test_tropes_audience_and_warnings_follow_selected_genres():
    fantasy_tropes = recommended_trope_codes(["novel"], ["dark_fantasy"])
    nonfiction_tropes = recommended_trope_codes(["guide"], ["self_dev"])
    assert "forbidden_magic" in fantasy_tropes
    assert "practical_steps" in nonfiction_tropes

    romance_audience = recommended_audience_codes(["novel"], ["romance"])
    assert "romance_fans" in romance_audience

    warnings = recommended_warning_codes(["dark_horror"], ["serial_killer"])
    assert {"none", "horror", "death", "blood"}.issubset(warnings)


def test_sections_keep_everything_reachable_and_selected_reviewable():
    all_genres = choices_in_section("g", GENRES, "all")
    assert len(all_genres) == len(GENRES)
    assert [item.label for item in all_genres] == sorted(
        (item.label for item in GENRES), key=str.casefold
    )

    selected = choices_in_section("g", GENRES, "selected", selected={"fantasy", "romance"})
    assert {item.code for item in selected} == {"fantasy", "romance"}
    assert any(section.code == "selected" for section in sections_for_prefix("g", {"fantasy"}))


def test_age_hint_is_conservative_but_not_forced():
    assert suggested_age_limit(["explicit_sex"], ["romance"]) == "18+"
    assert suggested_age_limit(["selfharm"], ["drama"]) == "16+"
    assert suggested_age_limit(["violence"], ["adventure"]) == "12+"
    assert suggested_age_limit(["none"], ["picture_book"]) == "6+"


def test_type_menu_is_paginated_and_smart_menu_has_sections():
    type_menu = single_select_menu("type", BOOK_TYPES, page=0, per_page=9)
    callbacks = [button.callback_data for row in type_menu.inline_keyboard for button in row]
    assert "single:type:p:1" in callbacks

    recommended = choices_in_section("g", GENRES, "recommended", book_type_codes=["novel"])
    smart_menu = multi_select_menu(
        "g",
        recommended,
        {"fantasy"},
        section_code="recommended",
        section_label="Подходящее",
        sections=sections_for_prefix("g", {"fantasy"}),
    )
    texts = [button.text for row in smart_menu.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in smart_menu.inline_keyboard for button in row]
    assert any(text.startswith("📂 Раздел:") for text in texts)
    assert any("выбрано 1" in text for text in texts)
    assert "sel:g:m" in callbacks


def test_v1119_release_metadata_and_docs_exist():
    from pathlib import Path
    from app.build_info import OWNER_BUILD_VERSION

    root = Path(__file__).resolve().parents[1]
    assert OWNER_BUILD_VERSION == "v1.11.12"
    assert (root / "docs" / "ADAPTIVE_CATALOG_V1_11_9.md").exists()
    assert (root / "docs" / "FINAL_TEST_REPORT_V1_11_9.md").exists()
    assert '"version": "v1.11.12"' in (root / "RELEASE_MANIFEST.json").read_text(encoding="utf-8")


class _FakeState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.current_state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, value):
        self.current_state = value


class _FakeMessage:
    def __init__(self):
        self.reply_markup = None
        self.text = None

    async def edit_reply_markup(self, *, reply_markup):
        self.reply_markup = reply_markup

    async def edit_text(self, text, *, reply_markup):
        self.text = text
        self.reply_markup = reply_markup


class _FakeCall:
    def __init__(self, data):
        self.data = data
        self.message = _FakeMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def test_no_warnings_is_mutually_exclusive():
    import asyncio
    from app.handlers.author import AddBook, _handle_multiselect

    async def scenario():
        state = _FakeState({
            "book_type": ["novel"],
            "selected_g": ["horror"],
            "selected_t": [],
            "selected_a": [],
            "selected_c": [],
            "section_c": "recommended",
            "section_menu_c": False,
            "page_c": 0,
        })
        await _handle_multiselect(
            _FakeCall("sel:c:t:none"), state, prefix="c", next_state=AddBook.age_limit
        )
        assert state.data["selected_c"] == ["none"]

        await _handle_multiselect(
            _FakeCall("sel:c:t:violence"), state, prefix="c", next_state=AddBook.age_limit
        )
        assert state.data["selected_c"] == ["violence"]

        await _handle_multiselect(
            _FakeCall("sel:c:t:none"), state, prefix="c", next_state=AddBook.age_limit
        )
        assert state.data["selected_c"] == ["none"]

    asyncio.run(scenario())
