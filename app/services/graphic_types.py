from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_GRAPHIC_EXTENSIONS = {
    ".pdf",
    ".cbz",
    ".zip",
    ".cbr",
    ".rar",
    ".7z",
    ".epub",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".avif",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}

SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".avif",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}


class GraphicImportError(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedGraphicPage:
    number: int
    path: Path
    source_filename: str
    width: int
    height: int
    file_size: int
    checksum: str
    mime_type: str = "image/webp"
    variants: dict[str, dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "path": str(self.path),
            "source_filename": self.source_filename,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
            "checksum": self.checksum,
            "mime_type": self.mime_type,
            "variants": self.variants or {},
        }
