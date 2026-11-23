"""Annotation primitives: arrow, rectangle, pen, marker, text, redaction, step
badges, and undo history.

Every annotation paints itself in *logical* coordinates through a ``QPainter``.
The overlay uses the same code for live preview and for the final render (under a
scale transform), so the saved image always matches what was on screen.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from enum import Enum

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)

Point = tuple[float, float]


class ToolKind(str, Enum):
    """Available annotation tools."""

    ARROW = "arrow"
    RECT = "rect"
    PEN = "pen"
    MARKER = "marker"
    TEXT = "text"
    REDACT = "redact"
    STEP = "step"


REDACT_COLOR = QColor("#111111")

def arrow_geometry(start: Point, end: Point, width: float) -> tuple[Point, list[Point]]:
    """Compute arrow geometry for a line of the given thickness.

    The head scales with ``width`` (4x, minimum 12 px) and is capped at the arrow
    length so very short arrows stay arrows. The shaft stops slightly *inside* the
    head so a thick line never pokes past the tip.

    Returns:
        ``(shaft_end, [tip, left_corner, right_corner])``. The head list is empty
        for a zero-length arrow.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return end, []
    ux, uy = dx / length, dy / length
    head_len = min(max(width * 4.0, 12.0), length)
    half = head_len * 0.45
    bx, by = end[0] - ux * head_len, end[1] - uy * head_len  # centre of head base
    nx, ny = -uy, ux  # unit normal
    left = (bx + nx * half, by + ny * half)
    right = (bx - nx * half, by - ny * half)
    shaft_end = (end[0] - ux * head_len * 0.8, end[1] - uy * head_len * 0.8)
    return shaft_end, [end, left, right]


def text_font(width: float) -> QFont:
    """Font used for text annotations; size follows the thickness setting."""
    font = QFont()
    font.setPixelSize(int(14 + width * 2.5))
    return font


class Annotation(ABC):
    """Base class for a drawable annotation."""

    def __init__(self, color: QColor, width: float) -> None:
        self.color = QColor(color)
        self.width = float(width)

    @abstractmethod
    def paint(self, p: QPainter) -> None:
        """Draw onto ``p`` (already antialiased, in logical coordinates)."""

    def update_drag(self, pos: QPointF) -> None:
        """Called while the mouse moves during creation."""

    def is_valid(self) -> bool:
        """False if the finished annotation is degenerate and should be dropped."""
        return True


class ArrowAnnotation(Annotation):
    """Arrow from ``start`` to ``end`` with a filled, thickness-scaled head."""

    def __init__(self, pos: QPointF, color: QColor, width: float) -> None:
        super().__init__(color, width)
        self.start = QPointF(pos)
        self.end = QPointF(pos)

    def update_drag(self, pos: QPointF) -> None:
        self.end = QPointF(pos)

    def is_valid(self) -> bool:
        return math.hypot(self.end.x() - self.start.x(), self.end.y() - self.start.y()) >= 3

    def paint(self, p: QPainter) -> None:
        shaft_end, head = arrow_geometry(
            (self.start.x(), self.start.y()), (self.end.x(), self.end.y()), self.width
        )
        if not head:
            return
        pen = QPen(
            self.color, self.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
        p.setPen(pen)
        p.drawLine(self.start, QPointF(*shaft_end))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self.color))
        p.drawPolygon(QPolygonF([QPointF(*pt) for pt in head]))


class RectAnnotation(Annotation):
    """Rectangle outline."""

    def __init__(self, pos: QPointF, color: QColor, width: float) -> None:
        super().__init__(color, width)
        self.p0 = QPointF(pos)
        self.p1 = QPointF(pos)

    def update_drag(self, pos: QPointF) -> None:
        self.p1 = QPointF(pos)

    def is_valid(self) -> bool:
        return abs(self.p1.x() - self.p0.x()) >= 2 and abs(self.p1.y() - self.p0.y()) >= 2

    def paint(self, p: QPainter) -> None:
        pen = QPen(self.color, self.width, Qt.PenStyle.SolidLine)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(self.p0, self.p1).normalized())


class PenAnnotation(Annotation):
    """Freehand stroke, smoothed with quadratic segments."""

    def __init__(self, pos: QPointF, color: QColor, width: float) -> None:
        super().__init__(color, width)
        self.points: list[QPointF] = [QPointF(pos)]

    def update_drag(self, pos: QPointF) -> None:
        last = self.points[-1]
        if math.hypot(pos.x() - last.x(), pos.y() - last.y()) >= 1.0:
            self.points.append(QPointF(pos))

    def _make_pen(self) -> QPen:
        return QPen(
            self.color, self.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )

    def _path(self) -> QPainterPath:
        pts = self.points
        path = QPainterPath(pts[0])
        for i in range(1, len(pts) - 1):
            mid = QPointF((pts[i].x() + pts[i + 1].x()) / 2, (pts[i].y() + pts[i + 1].y()) / 2)
            path.quadTo(pts[i], mid)
        path.lineTo(pts[-1])
        return path

    def paint(self, p: QPainter) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(self._make_pen())
        if len(self.points) == 1:
            p.drawPoint(self.points[0])
        else:
            p.drawPath(self._path())


class MarkerAnnotation(PenAnnotation):
    """Wide, semi-transparent highlighter stroke.

    The whole stroke is one path, so self-overlap does not darken the colour.
    """

    ALPHA = 110

    def __init__(self, pos: QPointF, color: QColor, width: float) -> None:
        tint = QColor(color)
        tint.setAlpha(self.ALPHA)
        super().__init__(pos, tint, max(10.0, width * 4.0))

    def _make_pen(self) -> QPen:
        return QPen(
            self.color, self.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap,
            Qt.PenJoinStyle.RoundJoin,
        )


class TextAnnotation(Annotation):
    """Single-line text anchored at its top-left corner."""

    def __init__(self, pos: QPointF, color: QColor, width: float, text: str = "") -> None:
        super().__init__(color, width)
        self.pos = QPointF(pos)
        self.text = text

    def is_valid(self) -> bool:
        return bool(self.text.strip())

    def paint(self, p: QPainter) -> None:
        font = text_font(self.width)
        p.setFont(font)
        p.setPen(QPen(self.color))
        ascent = QFontMetricsF(font).ascent()
        p.drawText(QPointF(self.pos.x(), self.pos.y() + ascent), self.text)


class RedactAnnotation(RectAnnotation):
    """Opaque box that hides sensitive data (passwords, personal data, tokens).

    The box is solid, not blurred, so the hidden content cannot be recovered.
    """

    def __init__(self, pos: QPointF, color: QColor | None = None, width: float = 0) -> None:
        fill = QColor(color) if color is not None else QColor(REDACT_COLOR)
        fill.setAlpha(255)
        super().__init__(pos, fill, width)

    def is_valid(self) -> bool:
        return abs(self.p1.x() - self.p0.x()) >= 3 and abs(self.p1.y() - self.p0.y()) >= 3

    def paint(self, p: QPainter) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(self.color))
        p.drawRect(QRectF(self.p0, self.p1).normalized())


def badge_radius(width: float) -> float:
    """Radius of a step badge for the given thickness setting."""
    return max(11.0, 8.0 + width * 1.6)


class StepBadgeAnnotation(Annotation):
    """Numbered circle marking the order of actions (1, 2, 3...)."""

    def __init__(self, pos: QPointF, color: QColor, width: float, number: int = 1) -> None:
        super().__init__(color, width)
        self.center = QPointF(pos)
        self.number = number

    def update_drag(self, pos: QPointF) -> None:
        self.center = QPointF(pos)  # dragging positions the badge

    def paint(self, p: QPainter) -> None:
        r = badge_radius(self.width)
        p.setPen(QPen(QColor("white"), max(1.5, r / 7)))
        p.setBrush(QBrush(self.color))
        p.drawEllipse(self.center, r, r)
        font = QFont()
        font.setBold(True)
        font.setPixelSize(max(9, int(r * (1.05 if self.number < 10 else 0.85))))
        p.setFont(font)
        light = (0.299 * self.color.red() + 0.587 * self.color.green() + 0.114 * self.color.blue()) > 170
        p.setPen(QColor("black") if light else QColor("white"))
        box = QRectF(self.center.x() - r, self.center.y() - r, 2 * r, 2 * r)
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, str(self.number))


def create_annotation(kind: ToolKind, pos: QPointF, color: QColor, width: float) -> Annotation:
    """Factory used by the overlay when a drag starts.

    ``TEXT`` is created by the overlay's inline editor instead. ``STEP`` badges
    start at 1; the overlay renumbers them.
    """
    if kind is ToolKind.REDACT:
        return RedactAnnotation(pos)
    if kind is ToolKind.STEP:
        return StepBadgeAnnotation(pos, color, width)
    classes = {
        ToolKind.ARROW: ArrowAnnotation,
        ToolKind.RECT: RectAnnotation,
        ToolKind.PEN: PenAnnotation,
        ToolKind.MARKER: MarkerAnnotation,
    }
    return classes[kind](pos, color, width)


def paint_annotations(p: QPainter, items: list[Annotation]) -> None:
    """Paint a list of annotations with antialiasing, isolating painter state."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    for item in items:
        p.save()
        item.paint(p)
        p.restore()
    p.restore()


class History:
    """Undo/redo stack. Undo removes only the most recent annotation."""

    def __init__(self) -> None:
        self.items: list[Annotation] = []
        self._redo: list[Annotation] = []

    def add(self, item: Annotation) -> None:
        self.items.append(item)
        self._redo.clear()

    def undo(self) -> bool:
        if not self.items:
            return False
        self._redo.append(self.items.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self.items.append(self._redo.pop())
        return True

    def clear(self) -> None:
        self.items.clear()
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self.items)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
