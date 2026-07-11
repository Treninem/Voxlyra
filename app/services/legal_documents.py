from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from app.legal_texts import LEGAL_DOCS, LegalDoc, get_doc


LEGAL_STORAGE_ROOT = Path("storage/legal")
_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@lru_cache(maxsize=1)
def _font_names() -> tuple[str, str]:
    regular_name = "VoxlyraDejaVu"
    bold_name = "VoxlyraDejaVuBold"
    regular = Path(_FONT_REGULAR)
    bold = Path(_FONT_BOLD)
    if not regular.is_file() or not bold.is_file():
        raise RuntimeError("Не найдены шрифты DejaVu Sans для юридических PDF.")
    pdfmetrics.registerFont(TTFont(regular_name, str(regular)))
    pdfmetrics.registerFont(TTFont(bold_name, str(bold)))
    return regular_name, bold_name


def _safe_name(filename: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename or "document.pdf")
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def _footer(canvas, document, doc: LegalDoc) -> None:
    regular, _ = _font_names()
    canvas.saveState()
    canvas.setFont(regular, 7)
    canvas.setFillColor(colors.HexColor("#626277"))
    width, _ = A4
    canvas.drawString(18 * mm, 11 * mm, f"Вокслира · редакция {doc.version} · SHA-256 {doc.digest[:16]}…")
    canvas.drawRightString(width - 18 * mm, 11 * mm, f"Страница {document.page}")
    canvas.restoreState()


def _build_story(doc: LegalDoc):
    regular, bold = _font_names()
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "VoxlyraTitle",
        parent=styles["Title"],
        fontName=bold,
        fontSize=16,
        leading=21,
        alignment=TA_CENTER,
        spaceAfter=8 * mm,
        textColor=colors.HexColor("#18162d"),
    )
    meta = ParagraphStyle(
        "VoxlyraMeta",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=8.5,
        leading=12,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#5c5870"),
        spaceAfter=7 * mm,
    )
    body = ParagraphStyle(
        "VoxlyraBody",
        parent=styles["BodyText"],
        fontName=regular,
        fontSize=9.4,
        leading=14.2,
        alignment=TA_LEFT,
        spaceAfter=3.5 * mm,
        textColor=colors.HexColor("#1f1e28"),
    )
    heading = ParagraphStyle(
        "VoxlyraHeading",
        parent=body,
        fontName=bold,
        fontSize=10.4,
        leading=15,
        spaceBefore=3.5 * mm,
        spaceAfter=2 * mm,
        textColor=colors.HexColor("#2d255d"),
    )
    note = ParagraphStyle(
        "VoxlyraNote",
        parent=body,
        fontSize=8.3,
        leading=12.5,
        textColor=colors.HexColor("#5c5870"),
        borderColor=colors.HexColor("#d6d2e5"),
        borderWidth=0.5,
        borderPadding=3 * mm,
        backColor=colors.HexColor("#f7f5fb"),
    )

    story = [
        Paragraph(doc.title, title),
        Paragraph(
            f"Редакция: {doc.version}<br/>Контрольная сумма документа: {doc.digest}<br/>"
            "Документ сформирован платформой Вокслира. Сохраните файл для ознакомления.",
            meta,
        ),
    ]

    lines = doc.plain_text.splitlines()
    for raw in lines:
        text = raw.strip()
        if not text:
            story.append(Spacer(1, 1.5 * mm))
            continue
        if text == doc.title or text.startswith("Редакция:"):
            continue
        if text.startswith("Внимание:"):
            story.append(Paragraph(text, note))
            continue
        is_heading = bool(re.match(r"^\d+\.\s+\S", text)) or text in {
            "Общие положения", "Реквизиты Оператора", "Заключительные положения"
        }
        story.append(Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), heading if is_heading else body))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "Акцепт или согласие фиксируется отдельно в интерфейсе бота. Само получение PDF не означает принятия документа.",
        note,
    ))
    return story


def ensure_legal_pdf(code: str, *, force: bool = False) -> Path:
    doc = get_doc(code)
    if not doc:
        raise KeyError(f"Неизвестный юридический документ: {code}")
    LEGAL_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    path = LEGAL_STORAGE_ROOT / _safe_name(doc.filename)
    digest_path = path.with_suffix(path.suffix + ".sha256")
    if not force and path.is_file() and digest_path.is_file() and digest_path.read_text(encoding="utf-8").strip() == doc.digest:
        return path

    pdf = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=doc.title,
        author="Вокслира",
        subject=f"Юридический документ, редакция {doc.version}",
    )
    pdf.build(
        _build_story(doc),
        onFirstPage=lambda canvas, document: _footer(canvas, document, doc),
        onLaterPages=lambda canvas, document: _footer(canvas, document, doc),
    )
    digest_path.write_text(doc.digest, encoding="utf-8")
    return path


def ensure_all_legal_pdfs(*, force: bool = False) -> dict[str, Path]:
    return {code: ensure_legal_pdf(code, force=force) for code in LEGAL_DOCS}
