"""Compositing the final image, clipboard and file output."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QRect
from PyQt6.QtGui import QGuiApplication, QImage, QPainter

from .capture import logical_to_pixel_rect
from .tools import Annotation, paint_annotations

SUPPORTED_SUFFIXES = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}


class StorageError(Exception):
    """Raised when saving or copying fails; the message is user-facing."""


def default_filename(now: datetime | None = None, ext: str = "png") -> str:
    """``screenshot_YYYY-MM-DD_HH-MM-SS.png``."""
    now = now or datetime.now()
    return f"screenshot_{now:%Y-%m-%d_%H-%M-%S}.{ext}"


def compose_image(
    source: QImage,
    selection: QRect,
    annotations: list[Annotation],
    sx: float,
    sy: float,
) -> QImage:
    """Crop ``selection`` (logical coords) out of ``source`` and bake annotations in.

    Args:
        source: Full snapshot in physical pixels.
        selection: Selected region in logical coordinates.
        annotations: Annotations in the same logical coordinates.
        sx, sy: Physical pixels per logical unit.

    Returns:
        An image whose size is exactly the selected pixel rectangle.
    """
    pixel_rect = logical_to_pixel_rect(selection, sx, sy, source.width(), source.height())
    if pixel_rect.isEmpty():
        raise StorageError("The selected area is empty.")
    out = source.copy(pixel_rect)
    if annotations:
        painter = QPainter(out)
        try:
            painter.translate(-pixel_rect.x(), -pixel_rect.y())
            painter.scale(sx, sy)
            painter.setClipRect(selection)  # annotations never leak outside the selection
            paint_annotations(painter, annotations)
        finally:
            painter.end()
    return out


def qimage_to_pil(image: QImage) -> Image.Image:
    """Convert a ``QImage`` to a Pillow RGBA image without precision loss."""
    img = image.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    return Image.frombuffer(
        "RGBA", (img.width(), img.height()), bytes(ptr), "raw", "RGBA", img.bytesPerLine(), 1
    )


def save_image(image: QImage, path: str | Path) -> Path:
    """Save as PNG or JPEG depending on the suffix (``.png`` is added if missing).

    Raises:
        StorageError: unsupported format, missing/unwritable folder, or disk error.
    """
    target = Path(path)
    if not target.suffix:
        target = target.with_suffix(".png")
    fmt = SUPPORTED_SUFFIXES.get(target.suffix.lower())
    if fmt is None:
        raise StorageError(f"Unsupported file type '{target.suffix}'. Use .png or .jpg.")
    if not target.parent.is_dir():
        raise StorageError(f"The folder '{target.parent}' does not exist.")
    pil = qimage_to_pil(image)
    try:
        if fmt == "JPEG":
            pil.convert("RGB").save(target, "JPEG", quality=95)
        else:
            pil.save(target, "PNG", optimize=True)
    except PermissionError as exc:
        raise StorageError(f"Permission denied writing '{target}'.") from exc
    except OSError as exc:
        raise StorageError(f"Could not save '{target}': {exc.strerror or exc}") from exc
    return target


def copy_to_clipboard(image: QImage) -> None:
    """Place the image on the system clipboard."""
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        raise StorageError("The system clipboard is not available.")
    clipboard.setImage(image)
