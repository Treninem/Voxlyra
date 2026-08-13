from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings
from app.db import connect
from app.services.cover_storage import ensure_book_cover_file
from app.services.github_source_publish import GitHubSourcePublishError, publish_source_package_zip

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTHOR_BOOK_STORAGE_ROOT = Path(str(settings.AUTHOR_BOOK_STORAGE_ROOT or "data/books"))
if not AUTHOR_BOOK_STORAGE_ROOT.is_absolute():
    AUTHOR_BOOK_STORAGE_ROOT = PROJECT_ROOT / AUTHOR_BOOK_STORAGE_ROOT
SYNC_ROOT = PROJECT_ROOT / "data" / "source_sync"
MARKER_PATH = SYNC_ROOT / "canonical_owner_sources_v1.json"


@dataclass(frozen=True, slots=True)
class CanonicalBookTarget:
    title: str
    package_id: str
    expected_chapters: int
    expected_source_sha256: str
    author: str = "Treninem"
    language: str = "ru"


TARGETS: tuple[CanonicalBookTarget, ...] = (
    CanonicalBookTarget(
        title="Счастье во мне",
        package_id="schastye-vo-mne-final",
        expected_chapters=170,
        expected_source_sha256="b48a2e65d852b9f5a5ab705850865d038558182351ae649f18215e825a325393",
    ),
    CanonicalBookTarget(
        title="Между двумя ответами",
        package_id="mezhdu-dvumya-otvetami-final",
        expected_chapters=1020,
        expected_source_sha256="3bd7213a458fb20d26d2175594ad949e43000e3c75e2905c48a5751b9f8bcb24",
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_marker() -> dict[str, Any]:
    try:
        value = json.loads(MARKER_PATH.read_text(encoding="utf-8"))
        return dict(value) if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_marker(value: dict[str, Any]) -> None:
    SYNC_ROOT.mkdir(parents=True, exist_ok=True)
    temp = MARKER_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(MARKER_PATH)


async def _find_book(target: CanonicalBookTarget):
    async with connect() as db:
        cur = await db.execute(
            """SELECT b.*, ap.pen_name, u.telegram_id
               FROM books b
               LEFT JOIN author_profiles ap ON ap.id=b.author_id
               LEFT JOIN users u ON u.id=ap.user_id
               WHERE b.title=?
                 AND b.publication_status<>'deleted'
                 AND b.writing_status='finished'
               ORDER BY CASE WHEN u.telegram_id=? THEN 0 ELSE 1 END,
                        b.updated_at DESC, b.id DESC
               LIMIT 1""",
            (target.title, int(settings.SYSTEM_OWNER_ID)),
        )
        return await cur.fetchone()


async def _chapters(book_id: int) -> list[dict[str, Any]]:
    async with connect() as db:
        cur = await db.execute(
            """SELECT number,title,text,status
               FROM chapters
               WHERE book_id=?
               ORDER BY number,id""",
            (int(book_id),),
        )
        return [dict(row) for row in await cur.fetchall()]


async def _genres(book_id: int) -> list[str]:
    async with connect() as db:
        cur = await db.execute(
            """SELECT option_label FROM book_option_values
               WHERE book_id=? AND option_group IN ('genre','genres','g')
               ORDER BY id""",
            (int(book_id),),
        )
        values = [str(row["option_label"] or "").strip() for row in await cur.fetchall()]
    result = [value for value in values if value]
    return result or ["Проза"]


def _candidate_source_paths(book) -> list[Path]:
    book_id = int(book["id"])
    folder = AUTHOR_BOOK_STORAGE_ROOT / str(book_id)
    candidates: list[Path] = []
    raw_source = str(book["source_file_name"] or "").strip()
    if raw_source:
        raw = Path(raw_source)
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.extend((PROJECT_ROOT / raw, folder / raw.name))
    if folder.is_dir():
        candidates.extend(sorted(folder.glob("*.fb2"), key=lambda p: p.stat().st_mtime, reverse=True))
        candidates.extend(sorted(folder.glob("upload_*"), key=lambda p: p.stat().st_mtime, reverse=True))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if resolved.is_file() and resolved.suffix.lower() == ".fb2" and resolved.stat().st_size > 0:
            unique.append(resolved)
    return unique


def _select_source_fb2(book, target: CanonicalBookTarget) -> tuple[Path | None, str]:
    fallback: tuple[Path | None, str] = (None, "")
    for candidate in _candidate_source_paths(book):
        digest = _sha256(candidate)
        if digest == target.expected_source_sha256:
            return candidate, digest
        if fallback[0] is None:
            fallback = (candidate, digest)
    return fallback


def _write_fb2_from_chapters(path: Path, *, title: str, author: str, language: str, chapters: list[dict[str, Any]]) -> None:
    def esc(value: object) -> str:
        return html.escape(str(value or ""), quote=False)

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">',
        '<description><title-info>',
        '<genre>prose_contemporary</genre>',
        f'<author><nickname>{esc(author)}</nickname></author>',
        f'<book-title>{esc(title)}</book-title>',
        f'<lang>{esc(language)}</lang>',
        '</title-info></description>',
        '<body>',
    ]
    for chapter in chapters:
        number = int(chapter.get("number") or 0)
        chapter_title = str(chapter.get("title") or f"Глава {number}").strip()
        lines.append('<section>')
        lines.append(f'<title><p>{esc(chapter_title)}</p></title>')
        text = str(chapter.get("text") or "")
        paragraphs = [part.strip() for part in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        for paragraph in paragraphs:
            if paragraph:
                lines.append(f'<p>{esc(paragraph)}</p>')
        lines.append('</section>')
    lines.extend(('</body>', '</FictionBook>'))
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_plain_text(path: Path, *, chapters: list[dict[str, Any]]) -> None:
    blocks: list[str] = []
    for chapter in chapters:
        number = int(chapter.get("number") or 0)
        title = str(chapter.get("title") or f"Глава {number}").strip()
        blocks.append(title)
        blocks.append(str(chapter.get("text") or "").strip())
    path.write_text("\n\n".join(blocks).strip() + "\n", encoding="utf-8")


def _fb2_text_fallback(source: Path) -> str:
    paragraphs: list[str] = []
    try:
        for _, elem in ET.iterparse(source, events=("end",)):
            if elem.tag.rsplit("}", 1)[-1] == "p":
                text = "".join(elem.itertext()).strip()
                if text:
                    paragraphs.append(text)
            elem.clear()
    except Exception:
        return ""
    return "\n\n".join(paragraphs).strip()


def _write_cover_jpeg(destination: Path, source: Path) -> None:
    if source.suffix.lower() in {".jpg", ".jpeg"}:
        shutil.copy2(source, destination)
        return
    from PIL import Image

    with Image.open(source) as image:
        image = image.convert("RGB")
        image.save(destination, format="JPEG", quality=94, optimize=True)


def _created_at() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


async def _build_package(target: CanonicalBookTarget, book, work: Path) -> tuple[Path, dict[str, Any]]:
    book_id = int(book["id"])
    chapters = await _chapters(book_id)
    source, source_sha = await asyncio.to_thread(_select_source_fb2, book, target)

    if source_sha != target.expected_source_sha256 and len(chapters) != target.expected_chapters:
        raise RuntimeError(
            f"{target.title}: нет подтверждённого финального FB2 и число глав {len(chapters)} != {target.expected_chapters}"
        )

    root = work / "books" / target.package_id
    root.mkdir(parents=True, exist_ok=True)
    book_fb2 = root / "book.fb2"
    source_mode = "original-final-fb2"
    if source is not None and source_sha == target.expected_source_sha256:
        await asyncio.to_thread(shutil.copy2, source, book_fb2)
    else:
        await asyncio.to_thread(
            _write_fb2_from_chapters,
            book_fb2,
            title=target.title,
            author=target.author,
            language=target.language,
            chapters=chapters,
        )
        source_sha = await asyncio.to_thread(_sha256, book_fb2)
        source_mode = "database-full-text-rebuild"

    text_path = root / "book.txt"
    if chapters:
        await asyncio.to_thread(_write_plain_text, text_path, chapters=chapters)
    else:
        plain = await asyncio.to_thread(_fb2_text_fallback, book_fb2)
        if not plain:
            raise RuntimeError(f"{target.title}: не удалось получить полный текст для book.txt")
        text_path.write_text(plain + "\n", encoding="utf-8")

    cover = await ensure_book_cover_file(
        book_id=book_id,
        cover_file_id=str(book["cover_file_id"] or ""),
        cover_path=str(book["cover_path"] or ""),
    )
    if cover is None or not cover.is_file():
        raise RuntimeError(f"{target.title}: локальная обложка не найдена")
    cover_jpg = root / "cover.jpg"
    await asyncio.to_thread(_write_cover_jpeg, cover_jpg, cover)

    description = str(book["description"] or "").strip()
    (root / "description.txt").write_text(description + ("\n" if description else ""), encoding="utf-8")
    genres = await _genres(book_id)
    metadata = {
        "title": target.title,
        "author": target.author,
        "description": description,
        "genre": genres,
        "age_rating": str(book["age_limit"] or "16+"),
        "language": target.language,
        "content_type": "book",
        "reading_mode": "text",
        "license": "platform_original",
        "rights_checked": True,
        "rights_holder_type": "person",
        "rights_holder": target.author,
        "source": "VoxLyra canonical system-owner author storage",
        "source_mode": source_mode,
        "source_book_id": book_id,
        "source_fb2_sha256": source_sha,
        "chapters": len(chapters) if chapters else target.expected_chapters,
        "status": "COMPLETED",
    }
    (root / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "LICENSE.txt").write_text(
        "VoxLyra platform original / author-owned work.\n"
        f"Title: {target.title}\nAuthor / rights holder: {target.author}\n"
        "The system owner authorized storage and publication of this canonical source package in Treninem/bookvoxlyra.\n",
        encoding="utf-8",
    )
    (root / "SOURCES.txt").write_text(
        f"Canonical title: {target.title}\n"
        f"VoxLyra book_id: {book_id}\n"
        f"Source mode: {source_mode}\n"
        f"FB2 SHA-256: {source_sha}\n"
        "Source: persistent VoxLyra author/library storage.\n",
        encoding="utf-8",
    )

    payload_names = [
        "book.fb2",
        "book.txt",
        "cover.jpg",
        "description.txt",
        "metadata.json",
        "LICENSE.txt",
        "SOURCES.txt",
    ]
    checksums = {name: await asyncio.to_thread(_sha256, root / name) for name in payload_names}
    fingerprint = hashlib.sha256(
        "\n".join(f"{name}:{checksums[name]}" for name in sorted(payload_names)).encode("utf-8")
    ).hexdigest()
    manifest = {
        "package_id": target.package_id,
        "content_type": "book",
        "title": target.title,
        "language": target.language,
        "version": f"canonical-{fingerprint[:20]}",
        "created_at": _created_at(),
        "files": payload_names,
        "checksums": checksums,
        "canonical": True,
        "payload_present": True,
        "import_enabled": True,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    archive = work / f"{target.package_id}.source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(root.iterdir()):
            output.write(path, f"books/{target.package_id}/{path.name}")
    return archive, {"fingerprint": fingerprint, "source_mode": source_mode, "book_id": book_id}


async def sync_canonical_owner_sources() -> list[dict[str, Any]]:
    """One-way, idempotent source sync for explicitly approved canonical works.

    Nothing is published unless the dedicated source-write bridge is enabled.
    The source package is validated and committed atomically by
    ``publish_source_package_zip``. A persistent fingerprint avoids duplicate
    commits after redeploys while still allowing a newer cover/text revision to
    replace the previous package.
    """
    if not bool(settings.GITHUB_SOURCE_WRITE_ENABLED) or not str(settings.GITHUB_SOURCE_WRITE_TOKEN or "").strip():
        logger.info("Canonical source sync skipped: GitHub source-write bridge is disabled")
        return []

    marker = await asyncio.to_thread(_load_marker)
    completed = dict(marker.get("completed") or {})
    results: list[dict[str, Any]] = []

    for target in TARGETS:
        try:
            book = await _find_book(target)
            if not book:
                results.append({"title": target.title, "status": "not-found-or-not-finished"})
                continue
            temp_parent = Path(str(settings.GITHUB_IMPORT_TEMP_ROOT or "storage/github_import"))
            temp_parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="canonical-source-", dir=temp_parent) as folder:
                archive, prepared = await _build_package(target, book, Path(folder))
                if str((completed.get(target.package_id) or {}).get("fingerprint") or "") == prepared["fingerprint"]:
                    results.append({"title": target.title, "status": "unchanged", **prepared})
                    continue
                publish = await publish_source_package_zip(int(settings.SYSTEM_OWNER_ID), archive)
                record = {
                    "fingerprint": prepared["fingerprint"],
                    "commit_sha": str(publish["commit_sha"]),
                    "book_id": prepared["book_id"],
                    "source_mode": prepared["source_mode"],
                    "synced_at": _created_at(),
                }
                completed[target.package_id] = record
                marker["completed"] = completed
                marker["updated_at"] = _created_at()
                await asyncio.to_thread(_save_marker, marker)
                results.append({"title": target.title, "status": "published", **record})
        except (GitHubSourcePublishError, OSError, RuntimeError, ValueError) as exc:
            logger.exception("Canonical source sync failed for %s", target.title)
            results.append({"title": target.title, "status": "error", "error": str(exc)[:500]})
        except Exception as exc:
            logger.exception("Unexpected canonical source sync failure for %s", target.title)
            results.append({"title": target.title, "status": "error", "error": str(exc)[:500]})
    return results
