from __future__ import annotations

import hashlib
import posixpath
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree

import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.services.graphic_types import (
    GraphicImportError,
    PreparedGraphicPage,
    SUPPORTED_GRAPHIC_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
)


def _natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _safe_member_name(value: str) -> str:
    normalized = str(value or "").replace("\\", "/")
    if normalized.startswith("/"):
        raise GraphicImportError("В архиве обнаружен небезопасный путь.")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise GraphicImportError("В архиве обнаружен небезопасный путь.")
    return normalized


def _archive_limits() -> tuple[int, int, int]:
    max_pages = max(1, int(settings.MAX_COMIC_PAGES or 500))
    max_total = max(1, int(settings.MAX_COMIC_UNPACKED_MB or 1024)) * 1024 * 1024
    max_one = max(1, int(settings.MAX_COMIC_PAGE_MB or 30)) * 1024 * 1024
    return max_pages, max_total, max_one


def _image_candidates_from_zip(source: Path, extract_dir: Path) -> list[tuple[Path, str]]:
    max_pages, max_total, max_one = _archive_limits()
    candidates: list[tuple[str, zipfile.ZipInfo]] = []
    total_size = 0

    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise GraphicImportError("Архив повреждён или имеет неподдерживаемый формат.") from exc

    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            safe_name = _safe_member_name(info.filename)
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise GraphicImportError("Символические ссылки внутри архива запрещены.")
            suffix = Path(safe_name).suffix.lower()
            if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
                continue
            if info.file_size <= 0:
                continue
            if info.file_size > max_one:
                raise GraphicImportError(
                    f"Страница «{Path(safe_name).name}» больше допустимых {settings.MAX_COMIC_PAGE_MB} МБ."
                )
            total_size += int(info.file_size)
            if total_size > max_total:
                raise GraphicImportError(
                    f"После распаковки архив превышает допустимые {settings.MAX_COMIC_UNPACKED_MB} МБ."
                )
            candidates.append((safe_name, info))

        candidates.sort(key=lambda item: _natural_key(item[0]))
        if not candidates:
            raise GraphicImportError("В архиве не найдено изображений страниц.")
        if len(candidates) > max_pages:
            raise GraphicImportError(f"В одной главе можно загрузить не больше {max_pages} страниц.")

        result: list[tuple[Path, str]] = []
        for index, (safe_name, info) in enumerate(candidates, 1):
            destination = extract_dir / f"source-{index:05d}{Path(safe_name).suffix.lower()}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with archive.open(info) as src, destination.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise GraphicImportError(f"Не удалось извлечь страницу «{Path(safe_name).name}».") from exc
            result.append((destination, Path(safe_name).name))
        return result


def _image_candidates_from_libarchive(source: Path, extract_dir: Path) -> list[tuple[Path, str]]:
    """Извлекает CBR/RAR и 7Z через системную libarchive.

    В Docker-образ устанавливается libarchive13, а Python-обвязка подключается
    зависимостью libarchive-c. Импорт ленивый, чтобы обычные ZIP/PDF продолжали
    работать даже при неполной локальной установке разработчика.
    """

    try:
        import libarchive  # type: ignore
    except Exception as exc:  # pragma: no cover - зависит от окружения
        raise GraphicImportError(
            "Поддержка CBR/RAR и 7Z не установлена на сервере. Выполните обновление зависимостей и Redeploy."
        ) from exc

    max_pages, max_total, max_one = _archive_limits()
    extracted: list[tuple[Path, str]] = []
    total_size = 0
    try:
        with libarchive.file_reader(str(source)) as archive:
            for entry in archive:
                raw_name = getattr(entry, "pathname", "") or ""
                safe_name = _safe_member_name(raw_name)
                suffix = Path(safe_name).suffix.lower()
                if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
                    continue
                mode = int(getattr(entry, "mode", 0) or 0)
                filetype = int(getattr(entry, "filetype", 0) or 0)
                if stat.S_ISLNK(mode) or stat.S_ISLNK(filetype):
                    raise GraphicImportError("Символические ссылки внутри архива запрещены.")
                declared_size = max(0, int(getattr(entry, "size", 0) or 0))
                if declared_size > max_one:
                    raise GraphicImportError(
                        f"Страница «{Path(safe_name).name}» больше допустимых {settings.MAX_COMIC_PAGE_MB} МБ."
                    )
                if len(extracted) >= max_pages:
                    raise GraphicImportError(f"В одной главе можно загрузить не больше {max_pages} страниц.")
                destination = extract_dir / f"archive-{len(extracted) + 1:05d}{suffix}"
                destination.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                try:
                    with destination.open("wb") as stream:
                        for block in entry.get_blocks():
                            written += len(block)
                            total_size += len(block)
                            if written > max_one:
                                raise GraphicImportError(
                                    f"Страница «{Path(safe_name).name}» больше допустимых {settings.MAX_COMIC_PAGE_MB} МБ."
                                )
                            if total_size > max_total:
                                raise GraphicImportError(
                                    f"После распаковки архив превышает допустимые {settings.MAX_COMIC_UNPACKED_MB} МБ."
                                )
                            stream.write(block)
                except GraphicImportError:
                    destination.unlink(missing_ok=True)
                    raise
                if written <= 0:
                    destination.unlink(missing_ok=True)
                    continue
                extracted.append((destination, Path(safe_name).name))
    except GraphicImportError:
        raise
    except Exception as exc:  # pragma: no cover - конкретная ошибка зависит от libarchive
        raise GraphicImportError("Архив CBR/RAR или 7Z повреждён либо использует неподдерживаемое шифрование.") from exc

    if not extracted:
        raise GraphicImportError("В архиве не найдено изображений страниц.")
    extracted.sort(key=lambda item: _natural_key(item[1]))
    return extracted


def _zip_read_limited(archive: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise GraphicImportError("EPUB повреждён: отсутствует обязательный файл.") from exc
    if info.file_size > max_bytes:
        raise GraphicImportError("Служебный файл EPUB имеет небезопасно большой размер.")
    with archive.open(info) as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise GraphicImportError("Служебный файл EPUB имеет небезопасно большой размер.")
    return data


def _epub_image_references(archive: zipfile.ZipFile) -> list[str]:
    container_raw = _zip_read_limited(archive, "META-INF/container.xml", 2 * 1024 * 1024)
    try:
        container_root = ElementTree.fromstring(container_raw)
    except ElementTree.ParseError as exc:
        raise GraphicImportError("EPUB повреждён: не удалось прочитать container.xml.") from exc

    rootfile = next(
        (node.attrib.get("full-path", "") for node in container_root.iter() if node.tag.rsplit("}", 1)[-1] == "rootfile"),
        "",
    )
    opf_name = _safe_member_name(rootfile)
    opf_raw = _zip_read_limited(archive, opf_name, 8 * 1024 * 1024)
    try:
        opf_root = ElementTree.fromstring(opf_raw)
    except ElementTree.ParseError as exc:
        raise GraphicImportError("EPUB повреждён: не удалось прочитать пакет издания.") from exc

    opf_dir = posixpath.dirname(opf_name)
    manifest: dict[str, tuple[str, str]] = {}
    spine_ids: list[str] = []
    for node in opf_root.iter():
        local = node.tag.rsplit("}", 1)[-1]
        if local == "item":
            item_id = node.attrib.get("id", "")
            href = node.attrib.get("href", "")
            media_type = node.attrib.get("media-type", "")
            if item_id and href:
                full = _safe_member_name(posixpath.normpath(posixpath.join(opf_dir, href)))
                manifest[item_id] = (full, media_type)
        elif local == "itemref":
            idref = node.attrib.get("idref", "")
            if idref:
                spine_ids.append(idref)

    names = set(archive.namelist())
    ordered: list[str] = []
    seen: set[str] = set()

    def add_image(path: str) -> None:
        safe = _safe_member_name(path)
        if safe in names and Path(safe).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS and safe not in seen:
            seen.add(safe)
            ordered.append(safe)

    for item_id in spine_ids:
        item = manifest.get(item_id)
        if not item:
            continue
        href, media_type = item
        if media_type.startswith("image/"):
            add_image(href)
            continue
        if media_type not in {"application/xhtml+xml", "text/html", "application/xml"}:
            continue
        if href not in names:
            continue
        raw = _zip_read_limited(archive, href, 8 * 1024 * 1024)
        soup = BeautifulSoup(raw, "html.parser")
        doc_dir = posixpath.dirname(href)
        refs: list[str] = []
        for tag in soup.find_all(["img", "image", "object", "source"]):
            value = tag.get("src") or tag.get("href") or tag.get("xlink:href") or tag.get("data")
            if not value and tag.name == "source":
                srcset = str(tag.get("srcset") or "").split(",", 1)[0].strip().split(" ", 1)[0]
                value = srcset
            if value:
                clean = str(value).split("#", 1)[0].split("?", 1)[0]
                refs.append(posixpath.normpath(posixpath.join(doc_dir, clean)))
        for ref in refs:
            add_image(ref)

    if not ordered:
        for href, media_type in manifest.values():
            if media_type.startswith("image/"):
                add_image(href)
    return ordered


def _image_candidates_from_epub(source: Path, extract_dir: Path) -> list[tuple[Path, str]]:
    max_pages, max_total, max_one = _archive_limits()
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise GraphicImportError("EPUB повреждён или не является корректным архивом.") from exc

    with archive:
        references = _epub_image_references(archive)
        if not references:
            raise GraphicImportError(
                "В EPUB не найдено фиксированных страниц. Для обычной текстовой EPUB используйте импорт книги."
            )
        if len(references) > max_pages:
            raise GraphicImportError(f"В одной главе можно загрузить не больше {max_pages} страниц.")
        result: list[tuple[Path, str]] = []
        total_size = 0
        for index, name in enumerate(references, 1):
            try:
                info = archive.getinfo(name)
            except KeyError:
                continue
            if info.file_size <= 0:
                continue
            if info.file_size > max_one:
                raise GraphicImportError(
                    f"Страница «{Path(name).name}» больше допустимых {settings.MAX_COMIC_PAGE_MB} МБ."
                )
            total_size += int(info.file_size)
            if total_size > max_total:
                raise GraphicImportError(
                    f"После распаковки EPUB превышает допустимые {settings.MAX_COMIC_UNPACKED_MB} МБ."
                )
            destination = extract_dir / f"epub-{index:05d}{Path(name).suffix.lower()}"
            with archive.open(info) as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            result.append((destination, Path(name).name))
    if not result:
        raise GraphicImportError("Не удалось извлечь графические страницы из EPUB.")
    return result


def _render_pdf(source: Path, render_dir: Path) -> list[tuple[Path, str]]:
    max_pages = max(1, int(settings.MAX_COMIC_PAGES or 500))
    try:
        document = fitz.open(source)
    except Exception as exc:
        raise GraphicImportError("PDF повреждён или защищён паролем.") from exc

    with document:
        if document.needs_pass:
            raise GraphicImportError("PDF защищён паролем. Загрузите файл без защиты.")
        if document.page_count <= 0:
            raise GraphicImportError("В PDF нет страниц.")
        if document.page_count > max_pages:
            raise GraphicImportError(f"В одной главе можно загрузить не больше {max_pages} страниц.")

        paths: list[tuple[Path, str]] = []
        max_width = max(720, int(settings.COMIC_IMAGE_MAX_WIDTH or 1920))
        max_height = max(1200, int(settings.COMIC_IMAGE_MAX_HEIGHT or 12000))
        for index in range(document.page_count):
            page = document.load_page(index)
            rect = page.rect
            base_width = max(float(rect.width), 1.0)
            base_height = max(float(rect.height), 1.0)
            zoom = max(0.05, min(2.2, max_width / base_width, max_height / base_height))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            path = render_dir / f"pdf-{index + 1:05d}.png"
            pix.save(path)
            paths.append((path, f"Страница {index + 1}"))
        return paths


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_image(source: Path, source_filename: str, *, allow_long: bool) -> Image.Image:
    max_width = max(720, int(settings.COMIC_IMAGE_MAX_WIDTH or 1920))
    max_height = max(1200, int(settings.COMIC_IMAGE_MAX_HEIGHT or 12000))
    try:
        with Image.open(source) as opened:
            source_width, source_height = opened.size
            if source_width <= 0 or source_height <= 0 or source_width * source_height > 120_000_000:
                raise GraphicImportError(f"Страница «{source_filename}» имеет небезопасно большое разрешение.")
            opened.seek(0)  # GIF/TIFF: используем первый кадр как страницу.
            opened.load()
            image = ImageOps.exif_transpose(opened).copy()
    except GraphicImportError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise GraphicImportError(f"Не удалось прочитать страницу «{source_filename}».") from exc

    width, height = image.size
    if width < 40 or height < 40:
        raise GraphicImportError(f"Страница «{source_filename}» слишком маленькая.")
    if width > max_width:
        target_height = max(1, round(height * max_width / width))
        image = image.resize((max_width, target_height), Image.Resampling.LANCZOS)
    if not allow_long and image.height > max_height:
        target_width = max(1, round(image.width * max_height / image.height))
        image = image.resize((target_width, max_height), Image.Resampling.LANCZOS)
    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    return image


def _variant_widths() -> list[int]:
    raw = str(getattr(settings, "COMIC_VARIANT_WIDTHS", "720,1280,1920") or "720,1280,1920")
    widths: list[int] = []
    for item in raw.replace(";", ",").split(","):
        try:
            value = int(item.strip())
        except (TypeError, ValueError):
            continue
        if value >= 320 and value not in widths:
            widths.append(value)
    if not widths:
        widths = [720, 1280, max(1280, int(settings.COMIC_IMAGE_MAX_WIDTH or 1920))]
    return sorted(widths)[:4]


def _save_webp_file(image: Image.Image, destination: Path, source_filename: str) -> dict[str, Any]:
    quality = max(55, min(95, int(settings.COMIC_WEBP_QUALITY or 84)))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "WEBP", quality=quality, method=6)
    except (OSError, ValueError) as exc:
        raise GraphicImportError(f"Не удалось сохранить страницу «{source_filename}».") from exc
    size = destination.stat().st_size
    if size <= 0:
        raise GraphicImportError(f"Страница «{source_filename}» не сохранилась.")
    return {
        "path": str(destination),
        "width": int(image.width),
        "height": int(image.height),
        "file_size": int(size),
        "checksum": _sha256(destination),
        "mime_type": "image/webp",
    }


def _save_webp(image: Image.Image, destination: Path, source_filename: str) -> PreparedGraphicPage:
    """Сохраняет основную страницу и адаптивные копии для слабой сети и небольших экранов."""
    widths = _variant_widths()
    labels = ["small", "medium", "large", "xlarge"]
    variants: dict[str, dict[str, Any]] = {}
    original_width = int(image.width)
    for index, configured_width in enumerate(widths):
        label = labels[min(index, len(labels) - 1)]
        target_width = min(original_width, int(configured_width))
        if target_width < original_width:
            target_height = max(1, round(image.height * target_width / original_width))
            prepared_image = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
        else:
            prepared_image = image
        variant_path = destination if index == len(widths) - 1 else destination.with_name(f"{destination.stem}-{label}.webp")
        variants[label] = _save_webp_file(prepared_image, variant_path, source_filename)

    # Основным файлом остаётся самая крупная версия, чтобы старые главы и API были совместимы.
    primary_label = labels[min(len(widths) - 1, len(labels) - 1)]
    primary = variants[primary_label]
    return PreparedGraphicPage(
        number=0,
        path=Path(str(primary["path"])),
        source_filename=source_filename[:240],
        width=int(primary["width"]),
        height=int(primary["height"]),
        file_size=int(primary["file_size"]),
        checksum=str(primary["checksum"]),
        variants=variants,
    )


def _prepare_image_pages(
    source: Path,
    destination_dir: Path,
    source_filename: str,
    *,
    base_name: str,
    split_long_pages: bool,
) -> list[PreparedGraphicPage]:
    image = _normalize_image(source, source_filename, allow_long=split_long_pages)
    slice_height = max(1200, int(settings.COMIC_WEBTOON_SLICE_HEIGHT or 3600))
    if not split_long_pages or image.height <= slice_height:
        return [_save_webp(image, destination_dir / f"{base_name}.webp", source_filename)]

    pieces: list[PreparedGraphicPage] = []
    count = (image.height + slice_height - 1) // slice_height
    for index in range(count):
        top = index * slice_height
        bottom = min(image.height, top + slice_height)
        fragment = image.crop((0, top, image.width, bottom))
        fragment_name = f"{source_filename} · фрагмент {index + 1}/{count}"
        pieces.append(
            _save_webp(fragment, destination_dir / f"{base_name}-part-{index + 1:03d}.webp", fragment_name)
        )
    return pieces


def prepare_graphic_file(
    source_path: Path,
    original_name: str,
    work_dir: Path,
    *,
    split_long_pages: bool = False,
) -> list[PreparedGraphicPage]:
    """Проверяет файл и создаёт оптимизированные WebP-страницы во временной папке."""
    suffix = Path(original_name or source_path.name).suffix.lower()
    if suffix not in SUPPORTED_GRAPHIC_EXTENSIONS:
        raise GraphicImportError(
            "Поддерживаются PDF, CBZ/ZIP, CBR/RAR, 7Z, EPUB fixed-layout и изображения."
        )

    shutil.rmtree(work_dir, ignore_errors=True)
    source_dir = work_dir / "source"
    ready_dir = work_dir / "ready"
    source_dir.mkdir(parents=True, exist_ok=True)
    ready_dir.mkdir(parents=True, exist_ok=True)

    if suffix in {".cbz", ".zip"}:
        raw_items = _image_candidates_from_zip(source_path, source_dir)
    elif suffix in {".cbr", ".rar", ".7z"}:
        raw_items = _image_candidates_from_libarchive(source_path, source_dir)
    elif suffix == ".epub":
        raw_items = _image_candidates_from_epub(source_path, source_dir)
    elif suffix == ".pdf":
        raw_items = _render_pdf(source_path, source_dir)
    else:
        raw_items = [(source_path, Path(original_name or source_path.name).name)]

    max_pages = max(1, int(settings.MAX_COMIC_PAGES or 500))
    prepared: list[PreparedGraphicPage] = []
    seen_hashes: set[str] = set()
    for source_index, (raw_path, source_name) in enumerate(raw_items, 1):
        pages = _prepare_image_pages(
            raw_path,
            ready_dir,
            source_name,
            base_name=f"source-{source_index:05d}",
            split_long_pages=split_long_pages,
        )
        for page in pages:
            if len(prepared) >= max_pages:
                raise GraphicImportError(
                    f"После подготовки получилось больше {max_pages} страниц. Уменьшите главу или отключите нарезку вебтуна."
                )
            page.number = len(prepared) + 1
            if page.checksum in seen_hashes:
                # Повторы разрешены: в комиксах встречаются разделители и намеренные дубли.
                pass
            seen_hashes.add(page.checksum)
            prepared.append(page)

    if not prepared:
        raise GraphicImportError("Не удалось подготовить страницы.")
    return prepared


def prepare_graphic_images(
    images: Iterable[tuple[Path, str]],
    work_dir: Path,
    *,
    split_long_pages: bool = False,
) -> list[PreparedGraphicPage]:
    """Подготавливает набор отдельных изображений в естественном порядке."""
    pairs = sorted(list(images), key=lambda item: _natural_key(item[1]))
    max_pages = max(1, int(settings.MAX_COMIC_PAGES or 500))
    if not pairs:
        raise GraphicImportError("Выберите изображения страниц.")
    if len(pairs) > max_pages:
        raise GraphicImportError(f"В одной главе можно загрузить не больше {max_pages} страниц.")

    shutil.rmtree(work_dir, ignore_errors=True)
    ready_dir = work_dir / "ready"
    ready_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[PreparedGraphicPage] = []
    for source_index, (path, name) in enumerate(pairs, 1):
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
            raise GraphicImportError(f"Файл «{name}» не является поддерживаемым изображением.")
        pages = _prepare_image_pages(
            path,
            ready_dir,
            Path(name).name,
            base_name=f"source-{source_index:05d}",
            split_long_pages=split_long_pages,
        )
        for page in pages:
            if len(prepared) >= max_pages:
                raise GraphicImportError(
                    f"После подготовки получилось больше {max_pages} страниц. Уменьшите главу или отключите нарезку вебтуна."
                )
            page.number = len(prepared) + 1
            prepared.append(page)
    return prepared


def prepare_replacement_page(source: Path, original_name: str, work_dir: Path) -> PreparedGraphicPage:
    """Проверяет одиночную замену страницы и возвращает готовый WebP."""
    suffix = Path(original_name or source.name).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_EXTENSIONS:
        raise GraphicImportError("Для замены выберите JPG, PNG, WebP, AVIF, GIF, BMP или TIFF.")
    shutil.rmtree(work_dir, ignore_errors=True)
    ready = work_dir / "ready"
    ready.mkdir(parents=True, exist_ok=True)
    pages = _prepare_image_pages(
        source,
        ready,
        Path(original_name or source.name).name,
        base_name="replacement",
        split_long_pages=False,
    )
    page = pages[0]
    page.number = 1
    return page


def rotate_graphic_page_file(source: Path, destination: Path, degrees: int) -> PreparedGraphicPage:
    """Создаёт повёрнутую копию страницы; исходник не изменяется до успешной проверки."""
    normalized = int(degrees or 0) % 360
    if normalized not in {90, 180, 270}:
        raise GraphicImportError("Страницу можно повернуть на 90, 180 или 270 градусов.")
    image = _normalize_image(source, source.name, allow_long=True)
    # Pillow считает положительный угол против часовой стрелки.
    rotated = image.rotate(-normalized, expand=True)
    return _save_webp(rotated, destination, source.name)


def graphic_report(pages: list[PreparedGraphicPage]) -> dict[str, object]:
    total_size = sum(page.file_size for page in pages)
    portrait = sum(1 for page in pages if page.height >= page.width)
    landscape = len(pages) - portrait
    fragmented = sum(1 for page in pages if "· фрагмент " in page.source_filename)
    return {
        "pages_count": len(pages),
        "optimized_bytes": total_size,
        "portrait_pages": portrait,
        "landscape_pages": landscape,
        "webtoon_fragments": fragmented,
        "preview": [
            {
                "number": page.number,
                "width": page.width,
                "height": page.height,
                "source_filename": page.source_filename,
            }
            for page in pages[:12]
        ],
    }
