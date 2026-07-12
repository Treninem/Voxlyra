from __future__ import annotations

import asyncio
from pathlib import Path

import httpx


def test_v193_build_assets_and_dependencies_exist():
    from app.build_info import OWNER_BUILD_NAME, OWNER_BUILD_VERSION

    assert OWNER_BUILD_VERSION in {"v1.9.3", "v1.9.4", "v1.9.5", "v1.9.6", "v1.9.7", "v1.9.8", "v1.9.9", "v1.10.0", "v1.10.1", "v1.10.2", "v1.10.3", "v1.10.4", "v1.10.5"}
    assert OWNER_BUILD_NAME
    for relative in (
        "app/services/legal_documents.py",
        "app/services/secure_fields.py",
        "app/services/yookassa_payouts.py",
        "templates/legal.html",
        "docs/LEGAL_YOOKASSA_STAGE_V1_9_3.md",
        "docs/STATUS_V1_9_3.md",
    ):
        assert Path(relative).is_file(), relative
    requirements = Path("requirements.txt").read_text(encoding="utf-8")
    assert "reportlab" in requirements
    assert "cryptography" in requirements
    assert "httpx>=" in requirements


def test_v193_required_documents_are_separate():
    from app.legal_texts import LEGAL_DOCS, REQUIRED_FOR_AUTHOR, REQUIRED_ON_START

    assert REQUIRED_ON_START == ["terms", "privacy", "personal_data_consent"]
    assert REQUIRED_FOR_AUTHOR == ["author_license", "author_data_consent"]
    assert LEGAL_DOCS["terms"].digest != LEGAL_DOCS["personal_data_consent"].digest
    assert LEGAL_DOCS["author_license"].digest != LEGAL_DOCS["author_data_consent"].digest
    assert LEGAL_DOCS["personal_data_consent"].consent_kind == "consent"


def test_v193_commission_model_is_from_final_visible_price():
    from app.services.pricing import final_price_for_desired_net, split_platform_commission

    split = split_platform_commission(1000, 20)
    assert split == {
        "gross_minor": 1000,
        "commission_percent": 20,
        "commission_minor": 200,
        "author_minor": 800,
    }
    assert final_price_for_desired_net(1000, 20) == 1250
    assert split_platform_commission(1250, 20)["author_minor"] == 1000


def test_v193_legal_pdf_is_generated_with_cyrillic_font(tmp_path, monkeypatch):
    import app.services.legal_documents as legal_documents

    monkeypatch.setattr(legal_documents, "LEGAL_STORAGE_ROOT", tmp_path)
    path = legal_documents.ensure_legal_pdf("terms", force=True)
    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF")
    assert path.stat().st_size > 20_000
    assert path.with_suffix(".pdf.sha256").read_text(encoding="utf-8").strip()


def test_v193_database_records_hash_source_and_withdrawal(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "legal.sqlite3"))

    async def scenario():
        from app.db import (
            accept_legal_document,
            get_legal_acceptances,
            has_accepted_legal_document,
            init_db,
            upsert_user,
            withdraw_legal_document,
        )
        from app.legal_texts import LEGAL_DOCS

        await init_db()
        user = await upsert_user(19301, "reader", "Reader")
        doc = LEGAL_DOCS["terms"]
        await accept_legal_document(
            user["id"], doc.code, doc.version,
            doc_hash=doc.digest, source="telegram_bot", telegram_message_id=77,
        )
        assert await has_accepted_legal_document(user["id"], doc.code, doc.version, doc.digest)
        rows = await get_legal_acceptances(user["id"])
        assert rows[0]["doc_hash"] == doc.digest
        assert rows[0]["acceptance_source"] == "telegram_bot"
        assert rows[0]["telegram_message_id"] == 77
        assert await withdraw_legal_document(user["id"], doc.code, doc.version)
        assert not await has_accepted_legal_document(user["id"], doc.code, doc.version, doc.digest)

    asyncio.run(scenario())


def test_v193_rub_ledger_uses_20_percent_and_14_day_hold(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "rub.sqlite3"))

    async def scenario():
        from app.db import (
            credit_author_rub_ledger,
            create_author_profile,
            get_author_financial_profile,
            get_author_profile,
            get_author_rub_finance_summary,
            init_db,
            upsert_author_financial_profile,
            upsert_user,
        )

        await init_db()
        user = await upsert_user(19302, "author", "Author")
        await create_author_profile(user["id"], "Автор", "", "RU", True)
        author = await get_author_profile(user["id"])
        await credit_author_rub_ledger(
            author["id"], source_kind="chapter", source_id=1,
            payment_id="pay-193", gross_minor=1000,
            commission_percent=20, hold_days=14,
        )
        summary = await get_author_rub_finance_summary(user["id"])
        assert summary["gross_minor"] == 1000
        assert summary["commission_minor"] == 200
        assert summary["held_minor"] == 800
        assert await upsert_author_financial_profile(
            user["id"], legal_status="self_employed", legal_name="Иванов Иван Иванович",
            inn="123456789012", sbp_phone_encrypted="encrypted", sbp_bank_id="100000000001",
            sbp_bank_name="Тестовый банк",
        )
        profile = await get_author_financial_profile(user["id"])
        assert profile["verification_status"] == "pending"

    asyncio.run(scenario())


def test_v196_rollback_disables_yookassa_payout_transport(monkeypatch, tmp_path):
    from app.config import settings
    from app.services.yookassa_payouts import YooKassaPayoutError, create_sbp_payout, normalize_phone

    assert normalize_phone("8 (999) 123-45-67") == "+79991234567"
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "disabled-payouts.sqlite3"))

    async def scenario():
        from app.db import init_db

        await init_db()
        try:
            await create_sbp_payout(
                amount_minor=10000,
                phone="+79991234567",
                bank_id="100000000001",
                description="Тест",
                idempotence_key="idem-disabled",
            )
        except YooKassaPayoutError as exc:
            assert "не подключены" in str(exc)
        else:
            raise AssertionError("ЮKassa не должна отправлять выплаты в сборке Stars-only")

    asyncio.run(scenario())

def test_v193_public_legal_pages_and_pdf_route(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "web.sqlite3"))
    from app.webapp import create_app

    with TestClient(create_app()) as client:
        listing = client.get("/legal")
        assert listing.status_code == 200
        assert "Документы Вокслиры" in listing.text
        page = client.get("/legal/fees_payouts")
        assert page.status_code == 200
        assert "20%" in page.text
        pdf = client.get("/legal/terms.pdf")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.content.startswith(b"%PDF")


def test_v193_telegram_digital_payments_remain_stars_only():
    source = Path("app/handlers/payments.py").read_text(encoding="utf-8")
    assert 'currency="XTR"' in source
    assert 'provider_token=""' in source
    assert "YOOKASSA_SHOP_ID" not in source


def test_v196_rollback_disables_sbp_directory(monkeypatch, tmp_path):
    from app.config import settings
    from app.services.yookassa_payouts import YooKassaPayoutError, list_sbp_banks

    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "disabled-banks.sqlite3"))

    async def scenario():
        from app.db import init_db

        await init_db()
        try:
            await list_sbp_banks()
        except YooKassaPayoutError as exc:
            assert "не подключены" in str(exc)
        else:
            raise AssertionError("Справочник СБП не должен загружаться при отключённой ЮKassa")

    asyncio.run(scenario())

def test_v193_author_financial_profile_ui_is_present():
    template = Path("templates/author.html").read_text(encoding="utf-8")
    script = Path("static/js/author.js").read_text(encoding="utf-8")
    for marker in (
        'id="authorFinancialProfileForm"',
        'id="authorSbpBank"',
        'id="authorRequestPayout"',
        'id="authorPayoutAvailable"',
    ):
        assert marker in template
    assert "/api/author/financial-profile" in script
    assert "/api/author/rub-payouts" in script
    assert "/api/author/sbp-banks" in script
