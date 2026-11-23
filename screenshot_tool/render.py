"""Headless image helpers shared by the evidence API, the CLI and the MCP server.

* :func:`load_image` accepts bytes, file paths, ``QImage`` or Pillow images.
* :func:`apply_annotations` draws boxes, arrows, labels, highlights, step badges and
  redactions described as plain dictionaries (what an LLM agent sends), using the
  same painters as the interactive overlay.
* :func:`stamp_footer` appends an evidence bar (test case, timestamp, URL...).
"""

from __future__ import annotations

import io
import math
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Union

from PIL import Image
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from .qt_runtime import ensure_gui_app
from .storage import qimage_to_pil
from .tools import (
    Annotation,
    ArrowAnnotation,
    RectAnnotation,
    RedactAnnotation,
    StepBadgeAnnotation,
    TextAnnotation,
    paint_annotations,
)

ImageSource = Union[bytes, bytearray, memoryview, str, os.PathLike, QImage, Image.Image]

ANNOTATION_TYPES = ("rect", "highlight", "arrow", "text", "step", "redact")
DEFAULT_ANNOTATION_COLOR = "#ff3b30"
DEFAULT_HIGHLIGHT_COLOR = "#ffcc00"
MAX_IMAGE_BYTES = 200 * 1024 * 1024
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ImageError(ValueError):
    """Invalid image input or annotation; the message is user-facing."""


# ---------------------------------------------------------------- conversion
def pil_to_qimage(pil: Image.Image) -> QImage:
    """Convert a Pillow image to a detached ``QImage`` (RGBA8888)."""
    rgba = pil.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    return QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def load_image(source: ImageSource) -> QImage:
    """Decode ``source`` into a ``QImage``.

    Args:
        source: Encoded image bytes (PNG, JPEG, WebP...), a file path, a ``QImage``
            or a Pillow image.

    Raises:
        ImageError: missing file, unreadable or empty image.
    """
    if isinstance(source, QImage):
        if source.isNull():
            raise ImageError("The image is empty.")
        return source.copy()
    if isinstance(source, Image.Image):
        return pil_to_qimage(source)
    if isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        origin = "image data"
    elif isinstance(source, (str, os.PathLike)):
        path = Path(source).expanduser()
        if not path.is_file():
            raise ImageError(f"Image file not found: {path}")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ImageError(f"Image file is too large (>{MAX_IMAGE_BYTES // 2**20} MB): {path}")
        data = path.read_bytes()
        origin = str(path)
    else:
        raise ImageError(f"Unsupported image source: {type(source).__name__}")
    try:
        with Image.open(io.BytesIO(data)) as pil:
            pil.load()
            return pil_to_qimage(pil)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageError(f"Could not read {origin} as an image: {exc}") from exc


def encode_png(image: QImage) -> bytes:
    """Encode losslessly as PNG (fast compression; opaque images are stored as RGB)."""
    pil = qimage_to_pil(image)
    if pil.getchannel("A").getextrema() == (255, 255):
        pil = pil.convert("RGB")
    buf = io.BytesIO()
    pil.save(buf, "PNG", compress_level=6)
    return buf.getvalue()


def preview_png(image: QImage, max_side: int = 1280) -> bytes:
    """PNG bytes of ``image`` scaled down so its longest side is at most ``max_side``."""
    if max(image.width(), image.height()) > max_side:
        image = image.scaled(
            max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return encode_png(image)


# --------------------------------------------------------------- annotations
def _num(spec: Mapping[str, Any], key: str, required: bool = True, default: float = 0.0) -> float:
    value = spec.get(key)
    if value is None:
        if required:
            raise ImageError(f"Annotation '{spec.get('type')}' needs '{key}'.")
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ImageError(f"Annotation field '{key}' must be a number, got {value!r}.")
    return float(value)


def _color(spec: Mapping[str, Any], default: str) -> QColor:
    value = spec.get("color") or default
    if not isinstance(value, str) or not _COLOR_RE.match(value):
        raise ImageError(f"Annotation color must look like '#rrggbb', got {value!r}.")
    return QColor(value)


def _box(spec: Mapping[str, Any]) -> tuple[QPointF, QPointF]:
    x, y = _num(spec, "x"), _num(spec, "y")
    w, h = _num(spec, "width"), _num(spec, "height")
    if abs(w) < 1 or abs(h) < 1:
        raise ImageError(f"Annotation '{spec.get('type')}' needs a non-zero width and height.")
    return QPointF(x, y), QPointF(x + w, y + h)


class _Highlight(RectAnnotation):
    """Translucent filled box with a thin outline (a 'highlighter' rectangle)."""

    def paint(self, p: QPainter) -> None:
        fill = QColor(self.color)
        fill.setAlpha(80)
        p.setBrush(QBrush(fill))
        p.setPen(QPen(self.color, max(1.0, self.width / 2)))
        p.drawRect(QRectF(self.p0, self.p1).normalized())


def _is_light(color: QColor) -> bool:
    return (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) > 170


class _LabelTag(Annotation):
    """A filled tag carrying ``text`` just above (or, at the top edge, inside) ``anchor``."""

    def __init__(self, text: str, anchor: QRectF, color: QColor, pixel_size: float) -> None:
        super().__init__(color, 0)
        self.text = text
        self.anchor = QRectF(anchor)
        self.pixel_size = int(max(12, min(28, pixel_size)))

    def paint(self, p: QPainter) -> None:
        font = QFont()
        font.setPixelSize(self.pixel_size)
        font.setBold(True)
        fm = QFontMetrics(font)
        pad = max(3, self.pixel_size // 4)
        w = fm.horizontalAdvance(self.text) + 2 * pad
        h = fm.height() + pad
        top = self.anchor.top() - h - 2
        if top < 0:
            top = self.anchor.top() + 2
        tag = QRectF(self.anchor.left(), top, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self.color))
        p.drawRoundedRect(tag, 3, 3)
        p.setFont(font)
        p.setPen(QColor("black") if _is_light(self.color) else QColor("white"))
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, self.text)


def build_annotations(specs: Iterable[Mapping[str, Any]], default_width: float = 4) -> list[Annotation]:
    """Turn plain annotation dictionaries into drawable annotations.

    Each dictionary has a ``type`` plus coordinates in image pixels:

    * ``rect`` / ``highlight`` / ``redact``: ``x``, ``y``, ``width``, ``height``
      (``rect`` and ``highlight`` accept an optional ``label``)
    * ``arrow``: from ``x``, ``y`` to ``to_x``, ``to_y`` (optional ``label`` at the start)
    * ``text``: ``x``, ``y`` (top-left) and ``text``
    * ``step``: centre ``x``, ``y``; ``number`` defaults to the next badge number

    Optional for all: ``color`` (``#rrggbb``) and ``thickness`` (1-50).

    Raises:
        ImageError: unknown type or missing/invalid fields.
    """
    out: list[Annotation] = []
    badge_count = 0
    for raw in specs:
        spec = dict(raw.items()) if isinstance(raw, Mapping) else None
        if spec is None:
            raise ImageError(f"Each annotation must be an object, got {raw!r}.")
        kind = str(spec.get("type", "")).strip().lower()
        if kind not in ANNOTATION_TYPES:
            raise ImageError(f"Unknown annotation type {spec.get('type')!r}; use one of {', '.join(ANNOTATION_TYPES)}.")
        width = _num(spec, "thickness", required=False, default=default_width)
        width = max(1.0, min(50.0, width))
        label = str(spec.get("label") or "").strip()

        if kind in ("rect", "highlight"):
            p0, p1 = _box(spec)
            color = _color(spec, DEFAULT_ANNOTATION_COLOR if kind == "rect" else DEFAULT_HIGHLIGHT_COLOR)
            item = RectAnnotation(p0, color, width) if kind == "rect" else _Highlight(p0, color, width)
            item.update_drag(p1)
            out.append(item)
            if label:
                out.append(_LabelTag(label, QRectF(p0, p1).normalized(), color, 10 + width * 2))
        elif kind == "redact":
            p0, p1 = _box(spec)
            item = RedactAnnotation(p0, _color(spec, "#111111"))
            item.update_drag(p1)
            out.append(item)
        elif kind == "arrow":
            start = QPointF(_num(spec, "x"), _num(spec, "y"))
            end = QPointF(_num(spec, "to_x"), _num(spec, "to_y"))
            color = _color(spec, DEFAULT_ANNOTATION_COLOR)
            item = ArrowAnnotation(start, color, width)
            item.update_drag(end)
            out.append(item)
            if label:
                out.append(_LabelTag(label, QRectF(start, start), color, 10 + width * 2))
        elif kind == "text":
            text = str(spec.get("text") or "").strip()
            if not text:
                raise ImageError("Annotation 'text' needs a non-empty 'text'.")
            out.append(TextAnnotation(QPointF(_num(spec, "x"), _num(spec, "y")),
                                      _color(spec, DEFAULT_ANNOTATION_COLOR), width, text))
        else:  # step badge
            badge_count += 1
            number = spec.get("number")
            if number is None:
                number = badge_count
            if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number <= 999:
                raise ImageError("Annotation 'step' needs an integer 'number' between 0 and 999.")
            out.append(StepBadgeAnnotation(QPointF(_num(spec, "x"), _num(spec, "y")),
                                           _color(spec, DEFAULT_ANNOTATION_COLOR), width, number))
    return out


def apply_annotations(image: QImage, specs: Iterable[Mapping[str, Any]], default_width: float = 4) -> QImage:
    """Return a copy of ``image`` with the annotation dictionaries painted on it."""
    items = build_annotations(specs, default_width)
    out = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if not items:
        return out
    ensure_gui_app()  # text and badges need fonts
    painter = QPainter(out)
    try:
        paint_annotations(painter, items)
    finally:
        painter.end()
    return out


# ------------------------------------------------------------------- footer
def stamp_footer(image: QImage, left: str, right: str = "", second: str = "") -> QImage:
    """Append a dark evidence bar below ``image`` (the screenshot itself is untouched).

    Args:
        left: Main line, e.g. ``"TC-101 · Login flow"`` (elided if too long).
        right: Right-aligned text on the main line, e.g. the capture time.
        second: Optional smaller second line, e.g. the URL and tester.
    """
    ensure_gui_app()
    w = max(1, image.width())
    font = QFont()
    font.setPixelSize(int(max(11, min(18, w / 90))))
    fm = QFontMetrics(font)
    pad = max(5, font.pixelSize() // 2)
    line_h = fm.height()
    lines = 2 if second else 1
    bar_h = 2 * pad + lines * line_h + (pad // 2 if lines == 2 else 0)

    out = QImage(w, image.height() + bar_h, QImage.Format.Format_RGB32)
    out.fill(QColor("white"))  # behind any transparent pixels of the screenshot
    p = QPainter(out)
    try:
        p.drawImage(0, 0, image)
        p.fillRect(0, image.height(), w, bar_h, QColor("#1c1c1e"))
        p.setFont(font)
        baseline = image.height() + pad + fm.ascent()
        right_w = fm.horizontalAdvance(right) + 2 * pad if right else 0
        if right and right_w < w - 2 * pad:
            p.setPen(QColor("#aeaeb2"))
            p.drawText(w - pad - fm.horizontalAdvance(right), baseline, right)
        else:
            right_w = 0
        p.setPen(QColor("#f2f2f7"))
        p.drawText(pad, baseline, fm.elidedText(left, Qt.TextElideMode.ElideRight, max(0, w - 2 * pad - right_w)))
        if second:
            p.setPen(QColor("#aeaeb2"))
            p.drawText(pad, baseline + line_h + pad // 2,
                       fm.elidedText(second, Qt.TextElideMode.ElideMiddle, max(0, w - 2 * pad)))
    finally:
        p.end()
    return out
