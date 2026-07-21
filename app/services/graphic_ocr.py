from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

try:
    import pytesseract
except Exception:  # pragma: no cover - optional runtime fallback
    pytesseract = None


class GraphicOCRError(RuntimeError):
    pass


def ocr_engine_available() -> bool:
    return bool(pytesseract is not None and shutil.which("tesseract"))


def available_ocr_languages() -> list[str]:
    if not ocr_engine_available():
        return []
    try:
        return sorted({str(item).strip().lower() for item in pytesseract.get_languages(config="") if str(item).strip()})
    except Exception:
        return []


def _resolve_ocr_languages(requested: str) -> str:
    installed = set(available_ocr_languages())
    wanted = [part.strip().lower() for part in str(requested or "rus+eng").split("+") if part.strip()]
    usable = [part for part in wanted if part in installed]
    if not usable:
        usable = [part for part in ("rus", "eng") if part in installed]
    if not usable and installed:
        usable = [sorted(installed)[0]]
    if not usable:
        raise GraphicOCRError("Tesseract установлен без языковых данных OCR")
    return "+".join(dict.fromkeys(usable))


def _extract_ocr_candidate(image: Image.Image, *, languages: str, psm: int) -> dict[str, Any]:
    data = pytesseract.image_to_data(
        image,
        lang=languages,
        config=f"--oem 1 --psm {int(psm)}",
        output_type=pytesseract.Output.DICT,
    )
    words: list[str] = []
    confidences: list[float] = []
    for raw_text, raw_conf in zip(data.get("text", []), data.get("conf", [])):
        text = str(raw_text or "").strip()
        if not text:
            continue
        try:
            confidence = float(raw_conf)
        except (TypeError, ValueError):
            confidence = -1
        if confidence >= 0:
            confidences.append(confidence)
        words.append(text)
    text = " ".join(words).strip()
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    score = len(text) + confidence * max(1, len(words)) / 10
    return {"text": text, "confidence": round(confidence, 2), "score": score, "psm": int(psm)}


def recognize_graphic_text(path: Path, languages: str = "rus+eng") -> dict[str, Any]:
    if not ocr_engine_available():
        raise GraphicOCRError("Локальный OCR пока недоступен на сервере")
    source = Path(path)
    if not source.is_file():
        raise GraphicOCRError("Файл страницы не найден")
    resolved_languages = _resolve_ocr_languages(languages)
    try:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert("L")
            max_side = max(image.size)
            if max_side > 3200:
                scale = 3200 / max_side
                image = image.resize(
                    (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                )
            # Нормализация контраста помогает и сканам, и цветным облакам комиксов.
            image = ImageOps.autocontrast(image, cutoff=1)
            candidates = [
                _extract_ocr_candidate(image, languages=resolved_languages, psm=6),
                _extract_ocr_candidate(image, languages=resolved_languages, psm=11),
            ]
    except GraphicOCRError:
        raise
    except Exception as exc:
        raise GraphicOCRError("Не удалось распознать текст на странице") from exc

    best = max(candidates, key=lambda item: float(item.get("score") or 0))
    return {
        "text": str(best.get("text") or ""),
        "confidence": float(best.get("confidence") or 0),
        "languages": resolved_languages,
        "strategy": f"psm-{int(best.get('psm') or 6)}",
    }


def _ranges_from_blank_flags(flags: list[bool], minimum_content: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(flags):
        if flags[index]:
            run_start = index
            while index < len(flags) and flags[index]:
                index += 1
            if run_start - start >= minimum_content:
                ranges.append((start, run_start))
            start = index
        else:
            index += 1
    if len(flags) - start >= minimum_content:
        ranges.append((start, len(flags)))
    return ranges


def suggest_graphic_frames(path: Path, max_frames: int = 24) -> list[dict[str, float]]:
    """Предлагает кадры по светлым межкадровым промежуткам.

    Это намеренно простой локальный алгоритм без внешних API. Автор может затем
    исправить границы вручную. Координаты возвращаются в диапазоне 0..1.
    """
    source = Path(path)
    if not source.is_file():
        raise GraphicOCRError("Файл страницы не найден")
    try:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert("L")
            scale = min(1.0, 900 / max(image.size))
            if scale < 1:
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
            pixels = image.load()
            width, height = image.size
            row_blank = []
            for y in range(height):
                light = sum(1 for x in range(width) if pixels[x, y] >= 245)
                row_blank.append(light / max(1, width) >= 0.985)
            row_ranges = _ranges_from_blank_flags(row_blank, max(24, height // 18))
            if not row_ranges:
                row_ranges = [(0, height)]

            frames: list[dict[str, float]] = []
            for top, bottom in row_ranges:
                band_height = max(1, bottom - top)
                col_blank = []
                for x in range(width):
                    light = sum(1 for y in range(top, bottom) if pixels[x, y] >= 245)
                    col_blank.append(light / band_height >= 0.985)
                col_ranges = _ranges_from_blank_flags(col_blank, max(24, width // 16))
                if not col_ranges:
                    col_ranges = [(0, width)]
                for left, right in col_ranges:
                    if len(frames) >= max_frames:
                        break
                    pad_x = min(6, left)
                    pad_y = min(6, top)
                    x0 = max(0, left - pad_x)
                    y0 = max(0, top - pad_y)
                    x1 = min(width, right + 6)
                    y1 = min(height, bottom + 6)
                    if (x1 - x0) * (y1 - y0) < width * height * 0.015:
                        continue
                    frames.append({
                        "x": round(x0 / width, 5),
                        "y": round(y0 / height, 5),
                        "width": round((x1 - x0) / width, 5),
                        "height": round((y1 - y0) / height, 5),
                    })
            if not frames:
                frames = [{"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}]
            return frames[:max_frames]
    except GraphicOCRError:
        raise
    except Exception as exc:
        raise GraphicOCRError("Не удалось определить кадры страницы") from exc
