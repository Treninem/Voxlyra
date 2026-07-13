from __future__ import annotations

import asyncio
from pathlib import Path


def test_operator_requisites_are_complete_and_not_placeholders():
    from app.legal_texts import LEGAL_DOCS, OPERATOR_FULL_NAME, operator_is_configured

    assert operator_is_configured() is True
    assert OPERATOR_FULL_NAME == "Тренин Евгений Максимович"
    texts = "\n".join(doc.plain_text for doc in {item.code: item for item in LEGAL_DOCS.values()}.values())
    for value in (
        "332201556141",
        "info@voxlyra.ru",
        "@Treninem",
        "Налог на профессиональный доход",
        "не зарегистрированное в качестве индивидуального предпринимателя",
    ):
        assert value in texts
    for forbidden in ("[ИНН]", "[наименование", "является шаблоном", "ИП/ООО/иной статус"):
        assert forbidden not in texts


def test_legal_documents_are_consolidated_and_legacy_links_work():
    from app.legal_texts import LEGAL_DOCS, all_docs

    assert len(all_docs()) == 5
    assert LEGAL_DOCS["refunds"] is LEGAL_DOCS["terms"]
    assert LEGAL_DOCS["fees_payouts"] is LEGAL_DOCS["author_license"]
    assert LEGAL_DOCS["copyright"] is LEGAL_DOCS["author_license"]
    assert LEGAL_DOCS["content"] is LEGAL_DOCS["author_license"]
    assert len(LEGAL_DOCS["terms"].plain_text) > 7000
    assert len(LEGAL_DOCS["author_license"].plain_text) > 8000


def test_legal_menu_from_document_sends_new_text_message():
    from app.handlers.legal import _edit_text_or_send

    class FakeMediaMessage:
        text = None

        def __init__(self):
            self.answer_calls = 0
            self.edit_calls = 0

        async def answer(self, text, reply_markup=None):
            self.answer_calls += 1
            return self

        async def edit_text(self, text, reply_markup=None):
            self.edit_calls += 1
            raise AssertionError("edit_text must not be called for a PDF message")

    message = FakeMediaMessage()
    asyncio.run(_edit_text_or_send(message, "Меню"))
    assert message.answer_calls == 1
    assert message.edit_calls == 0


def test_legal_menu_from_text_edits_in_place():
    from app.handlers.legal import _edit_text_or_send

    class FakeTextMessage:
        text = "Старое меню"

        def __init__(self):
            self.answer_calls = 0
            self.edit_calls = 0

        async def answer(self, text, reply_markup=None):
            self.answer_calls += 1
            return self

        async def edit_text(self, text, reply_markup=None):
            self.edit_calls += 1
            return self

    message = FakeTextMessage()
    asyncio.run(_edit_text_or_send(message, "Новое меню"))
    assert message.edit_calls == 1
    assert message.answer_calls == 0


def test_pdf_layout_version_is_bumped_and_menu_is_shorter():
    service = Path("app/services/legal_documents.py").read_text(encoding="utf-8")
    keyboard = Path("app/keyboards.py").read_text(encoding="utf-8")
    assert 'LEGAL_PDF_LAYOUT_VERSION = "3"' in service
    assert "Оферта и правила доступа" in keyboard
    assert "Комиссии и выплаты" not in keyboard[keyboard.index("def legal_menu"):keyboard.index("def legal_doc_menu")]
