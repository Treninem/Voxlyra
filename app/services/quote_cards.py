from __future__ import annotations

import hashlib
import io
import math
import random
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


CARD_SIZE = (1080, 1350)
MAX_QUOTE_LENGTH = 480
QUOTE_STYLES = {"standard", "aurora", "parchment"}


def normalize_quote(value: object) -> str:
    text = " ".join(str(value or "").replace("\u00a0", " ").split())
    return text[:MAX_QUOTE_LENGTH].strip()


def quote_belongs_to_text(quote: str, chapter_text: str) -> bool:
    needle = normalize_quote(quote).casefold()
    haystack = " ".join(str(chapter_text or "").replace("\u00a0", " ").split()).casefold()
    return bool(needle) and len(needle) >= 20 and needle in haystack


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap_by_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join([*current, word])
        box = draw.textbbox((0, 0), trial, font=font)
        if current and box[2] - box[0] > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def build_quote_card(*, quote: str, book_title: str, chapter_title: str, author_name: str, style: str = "standard") -> bytes:
    quote = normalize_quote(quote)
    if not quote:
        raise ValueError("Пустая цитата")
    style = str(style or "standard").strip().lower()
    if style not in QUOTE_STYLES:
        style = "standard"

    width, height = CARD_SIZE
    image = Image.new("RGB", CARD_SIZE)
    pixels = image.load()
    gradients = {
        "standard": ((9, 11, 28), (31, 19, 66)),
        "aurora": ((4, 20, 35), (38, 19, 73)),
        "parchment": ((35, 23, 22), (83, 52, 31)),
    }
    start, end = gradients[style]
    for y in range(height):
        ratio = y / max(1, height - 1)
        r = int(start[0] + (end[0] - start[0]) * ratio)
        g = int(start[1] + (end[1] - start[1]) * ratio)
        b = int(start[2] + (end[2] - start[2]) * ratio)
        for x in range(width):
            vignette = ((x - width / 2) / (width / 2)) ** 2
            pixels[x, y] = (max(0, r - int(7 * vignette)), max(0, g - int(4 * vignette)), max(0, b - int(2 * vignette)))

    draw = ImageDraw.Draw(image, "RGBA")
    seed = int(hashlib.sha256(f"{book_title}|{chapter_title}|{quote}".encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)

    # Спокойные звёзды и мягкие орбиты в фирменной гамме.
    for _ in range(95):
        x = rng.randint(50, width - 50)
        y = rng.randint(40, height - 130)
        radius = rng.choice([1, 1, 2, 2, 3])
        alpha = rng.randint(35, 145)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(222, 188, 94, alpha))
    for offset, alpha in [(0, 80), (34, 48), (72, 30)]:
        draw.arc((80 - offset, 120 + offset, width - 80 + offset, height - 110 - offset), 205, 338, fill=(221, 177, 72, alpha), width=3)

    panel_fill = {
        "standard": (16, 17, 49, 220),
        "aurora": (9, 31, 49, 218),
        "parchment": (55, 34, 28, 226),
    }[style]
    inner_line = {
        "standard": (114, 73, 189, 130),
        "aurora": (77, 184, 190, 135),
        "parchment": (192, 124, 72, 145),
    }[style]
    draw.rounded_rectangle((70, 80, width - 70, height - 90), radius=52, fill=panel_fill, outline=(219, 178, 75, 190), width=3)
    draw.rounded_rectangle((94, 104, width - 94, height - 114), radius=42, outline=inner_line, width=2)

    label_font = _font(31, bold=True)
    title_font = _font(46, bold=True)
    meta_font = _font(29)
    footer_font = _font(32, bold=True)

    draw.text((130, 145), "VOXLYRA · ЦИТАТА", font=label_font, fill=(221, 184, 88, 255))
    draw.text((130, 215), str(book_title or "Книга")[:80], font=title_font, fill=(249, 246, 255, 255))
    meta = " · ".join(part for part in [str(chapter_title or "")[:90], str(author_name or "")[:70]] if part)
    draw.text((130, 285), meta, font=meta_font, fill=(190, 181, 221, 255))

    quote_font_size = 52
    wrapped = ""
    quote_font = _font(quote_font_size)
    max_width = width - 290
    max_height = 700
    while quote_font_size >= 34:
        quote_font = _font(quote_font_size)
        wrapped = _wrap_by_pixels(draw, quote, quote_font, max_width)
        box = draw.multiline_textbbox((0, 0), wrapped, font=quote_font, spacing=18)
        if box[3] - box[1] <= max_height:
            break
        quote_font_size -= 2

    quote_box = draw.multiline_textbbox((0, 0), wrapped, font=quote_font, spacing=18, align="left")
    quote_height = quote_box[3] - quote_box[1]
    quote_y = max(405, int((height - quote_height) / 2) - 5)
    draw.text((114, quote_y - 82), "“", font=_font(118, bold=True), fill=(202, 158, 67, 210))
    draw.multiline_text((145, quote_y), wrapped, font=quote_font, fill=(245, 241, 255, 255), spacing=18)
    draw.text((width - 190, quote_y + quote_height - 20), "”", font=_font(118, bold=True), fill=(202, 158, 67, 210))

    line_y = height - 235
    draw.line((130, line_y, width - 130, line_y), fill=(221, 184, 88, 120), width=2)
    draw.text((130, line_y + 38), "Читайте и слушайте истории в VoxLyra", font=footer_font, fill=(231, 224, 247, 255))
    draw.text((130, line_y + 92), "Книги · аудио · комиксы", font=meta_font, fill=(163, 145, 207, 255))

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
