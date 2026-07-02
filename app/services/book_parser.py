import html
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


class BookParseError(RuntimeError):
    pass


@dataclass
class ParsedChapter:
    number: int
    title: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


SUPPORTED_BOOK_EXTENSIONS = {".txt", ".docx", ".fb2", ".epub", ".pdf", ".zip"}
_TEXT_EXTENSIONS = {".txt", ".md"}


def _clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "windows-1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def split_plain_text_to_chapters(text: str) -> list[ParsedChapter]:
    """Разбивка текста на главы.

    Поддерживает варианты:
    - Глава 1
    - Глава 1. Название
    - Глава 12: Название
    - Chapter 1
    - Часть 1 / Пролог / Эпилог как отдельные заголовки
    """
    text = _clean_text(text)
    if not text:
        return []

    pattern = re.compile(
        r"(?im)^\s*((?:глава|chapter)\s+\d{1,5}(?:\s*[.:—-]\s*[^\n]{1,140})?|(?:пролог|эпилог|часть\s+\d{1,5})(?:\s*[.:—-]\s*[^\n]{1,140})?)\s*$"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return [ParsedChapter(1, "Глава 1", text)]

    chapters: list[ParsedChapter] = []
    counter = 1
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        block = text[start:end].strip()
        lines = block.splitlines()
        title = lines[0].strip() if lines else f"Глава {counter}"
        body = _clean_text("\n".join(lines[1:]))
        number_match = re.search(r"\d+", title)
        number = int(number_match.group(0)) if number_match else counter
        if not body and idx + 1 < len(matches):
            body = ""
        chapters.append(ParsedChapter(number, title[:160], body))
        counter += 1

    return _renumber_if_needed(chapters)


def _renumber_if_needed(chapters: list[ParsedChapter]) -> list[ParsedChapter]:
    seen: set[int] = set()
    duplicated = False
    for ch in chapters:
        if ch.number in seen:
            duplicated = True
            break
        seen.add(ch.number)
    if not duplicated:
        return chapters
    return [ParsedChapter(i, ch.title, ch.text) for i, ch in enumerate(chapters, 1)]


def _docx_to_text(path: Path) -> str:
    try:
        from docx import Document
    except Exception as exc:  # pragma: no cover - depends on optional import
        raise BookParseError("Для DOCX нужна библиотека python-docx.") from exc
    doc = Document(str(path))
    blocks: list[str] = []
    for paragraph in doc.paragraphs:
        value = paragraph.text.strip()
        if value:
            blocks.append(value)
    return "\n\n".join(blocks)


def _fb2_to_text(path: Path) -> str:
    try:
        root = ET.fromstring(path.read_bytes())
    except Exception as exc:
        raise BookParseError("FB2 не удалось прочитать. Файл повреждён или имеет неверную структуру.") from exc

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"
    body = root.find(f".//{ns}body")
    if body is None:
        raise BookParseError("В FB2 не найден текст книги.")

    parts: list[str] = []
    for section in body.findall(f".//{ns}section"):
        title_parts = ["".join(p.itertext()).strip() for p in section.findall(f"./{ns}title/{ns}p")]
        title = " ".join(t for t in title_parts if t).strip()
        paragraphs = ["".join(p.itertext()).strip() for p in section.findall(f"./{ns}p")]
        paragraphs = [p for p in paragraphs if p]
        if title or paragraphs:
            if title:
                parts.append(title)
            parts.extend(paragraphs)
            parts.append("")
    if not parts:
        parts = ["".join(body.itertext())]
    return "\n".join(parts)


def _epub_to_text(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub
    except Exception as exc:  # pragma: no cover - depends on optional import
        raise BookParseError("Для EPUB нужны библиотеки ebooklib и beautifulsoup4.") from exc

    try:
        book = epub.read_epub(str(path))
    except Exception as exc:
        raise BookParseError("EPUB не удалось прочитать. Файл повреждён или защищён.") from exc

    blocks: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text("\n")
        text = _clean_text(html.unescape(text))
        if text:
            blocks.append(text)
    return "\n\n".join(blocks)


def _pdf_to_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - depends on optional import
        raise BookParseError("Для PDF нужна библиотека pypdf.") from exc
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise BookParseError("PDF не удалось открыть. Файл повреждён или защищён паролем.") from exc
    texts: list[str] = []
    for page in reader.pages:
        texts.append(page.extract_text() or "")
    text = "\n\n".join(texts)
    if not text.strip():
        raise BookParseError("В PDF не найден распознаваемый текст. Нужен текстовый PDF, не сканы.")
    return text


def _parse_single_file(path: Path) -> list[ParsedChapter]:
    ext = path.suffix.lower()
    if ext in _TEXT_EXTENSIONS:
        text = _read_text_file(path)
    elif ext == ".docx":
        text = _docx_to_text(path)
    elif ext == ".fb2":
        text = _fb2_to_text(path)
    elif ext == ".epub":
        text = _epub_to_text(path)
    elif ext == ".pdf":
        text = _pdf_to_text(path)
    else:
        raise BookParseError(f"Формат {ext or 'без расширения'} пока не поддерживается.")
    return split_plain_text_to_chapters(text)


def parse_book_file(path: str | Path, original_filename: str | None = None, temp_dir: str | Path | None = None) -> list[ParsedChapter]:
    path = Path(path)
    source_name = original_filename or path.name
    ext = Path(source_name).suffix.lower() or path.suffix.lower()
    if ext not in SUPPORTED_BOOK_EXTENSIONS:
        raise BookParseError("Поддерживаются TXT, DOCX, FB2, EPUB, PDF и ZIP.")
    if ext == ".zip":
        return _parse_zip(path, temp_dir=temp_dir)
    return _parse_single_file(path)


def _safe_zip_members(zf: zipfile.ZipFile) -> Iterable[zipfile.ZipInfo]:
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if info.is_dir() or name.startswith("__MACOSX/"):
            continue
        if "/." in name or name.startswith("."):
            continue
        ext = Path(name).suffix.lower()
        if ext in SUPPORTED_BOOK_EXTENSIONS - {".zip"} or ext in _TEXT_EXTENSIONS:
            yield info


def _parse_zip(path: Path, temp_dir: str | Path | None = None) -> list[ParsedChapter]:
    temp_root = Path(temp_dir or path.parent / "zip_extract")
    temp_root.mkdir(parents=True, exist_ok=True)
    chapters: list[ParsedChapter] = []
    try:
        with zipfile.ZipFile(path) as zf:
            members = sorted(_safe_zip_members(zf), key=lambda i: i.filename.lower())
            if not members:
                raise BookParseError("В ZIP не найдено файлов TXT, DOCX, FB2, EPUB или PDF.")
            for index, info in enumerate(members, 1):
                if info.file_size > 30 * 1024 * 1024:
                    raise BookParseError(f"Файл {info.filename} слишком большой для импорта.")
                safe_name = f"{index:04d}_{Path(info.filename).name}"
                out_path = temp_root / safe_name
                out_path.write_bytes(zf.read(info))
                parsed = _parse_single_file(out_path)
                if len(parsed) == 1 and parsed[0].title == "Глава 1":
                    title = Path(info.filename).stem.replace("_", " ").strip() or f"Глава {index}"
                    parsed = [ParsedChapter(index, title[:160], parsed[0].text)]
                chapters.extend(parsed)
    except zipfile.BadZipFile as exc:
        raise BookParseError("ZIP не удалось открыть. Архив повреждён.") from exc
    return [ParsedChapter(i, ch.title, ch.text) for i, ch in enumerate(chapters, 1)]


def detect_text_problems(text: str) -> list[str]:
    problems: list[str] = []
    if not text.strip():
        problems.append("Файл пустой")
    if "���" in text or "�" in text:
        problems.append("Есть битые символы")
    if len(text.strip()) < 500:
        problems.append("Текст слишком короткий")
    return problems


def build_import_report(chapters: list[ParsedChapter]) -> dict:
    total_chars = sum(len(ch.text) for ch in chapters)
    empty = [ch.number for ch in chapters if not ch.text.strip()]
    short = [ch.number for ch in chapters if 0 < len(ch.text.strip()) < 300]
    duplicate_text_numbers: list[int] = []
    seen_texts: set[str] = set()
    for ch in chapters:
        normalized = re.sub(r"\s+", " ", ch.text.strip().lower())[:2000]
        if normalized and normalized in seen_texts:
            duplicate_text_numbers.append(ch.number)
        elif normalized:
            seen_texts.add(normalized)

    problems: list[str] = []
    if not chapters:
        problems.append("Главы не найдены")
    if empty:
        problems.append(f"Пустые главы: {', '.join(map(str, empty[:10]))}")
    if short:
        problems.append(f"Очень короткие главы: {', '.join(map(str, short[:10]))}")
    if duplicate_text_numbers:
        problems.append(f"Возможные повторы глав: {', '.join(map(str, duplicate_text_numbers[:10]))}")
    if total_chars < 1000:
        problems.append("Общий объём слишком маленький")

    preview = []
    for ch in chapters[:8]:
        preview.append({"number": ch.number, "title": ch.title, "chars": len(ch.text)})

    return {
        "chapters_count": len(chapters),
        "total_chars": total_chars,
        "problems": problems,
        "preview": preview,
    }
