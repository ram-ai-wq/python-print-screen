"""Toolbar widgets: tool/colour/thickness panel, action bar and toast.

All widgets are children of the overlay and never take keyboard focus, so the
overlay keeps receiving shortcuts such as Ctrl+Z.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from .tools import ToolKind

BUTTON = 32
ICON = 22
PRESET_COLORS = ["#ff3b30", "#ff9500", "#ffcc00", "#34c759", "#0a84ff", "#af52de", "#000000", "#ffffff"]
THICKNESSES = [2, 4, 6, 10]

PANEL_STYLE = """
QFrame#panel {
    background: rgba(32, 32, 34, 238);
    border: 1px solid rgba(255, 255, 255, 50);
    border-radius: 6px;
}
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 4px; }
QToolButton:hover { background: rgba(255, 255, 255, 45); }
QToolButton:checked { background: rgba(10, 132, 255, 210); }
QToolButton:disabled { opacity: 0.4; }
QToolButton#primary { background: rgba(10, 132, 255, 230); }
QToolButton#primary:hover { background: rgba(64, 156, 255, 255); }
"""


def make_icon(name: str, size: int = ICON) -> QIcon:
    """Draw a small white glyph icon (no image assets needed)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    white = QColor("#ffffff")
    p.setPen(QPen(white, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    s = float(size)
    if name == "arrow":
        tip = QPointF(s - 3, 3)
        p.drawLine(QPointF(4, s - 4), QPointF(s - 7, 7))
        p.setBrush(white)
        p.drawPolygon(QPolygonF([tip, QPointF(tip.x() - 10, tip.y() + 2), QPointF(tip.x() - 2, tip.y() + 10)]))
    elif name == "rect":
        p.drawRect(QRectF(4, 5, s - 8, s - 10))
    elif name == "pen":
        path = QPainterPath(QPointF(3, 15))
        path.cubicTo(7, 2, 11, 20, 19, 6)
        p.drawPath(path)
    elif name == "marker":
        p.setPen(QPen(QColor(255, 225, 0, 190), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        p.drawLine(QPointF(4, s - 7), QPointF(s - 4, s - 13))
    elif name == "text":
        font = QFont()
        font.setPixelSize(17)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "T")
    elif name == "redact":  # "hide": an eye with a slash through it
        eye = QPainterPath(QPointF(2.5, s / 2))
        eye.quadTo(QPointF(s / 2, 3), QPointF(s - 2.5, s / 2))
        eye.quadTo(QPointF(s / 2, s - 3), QPointF(2.5, s / 2))
        p.drawPath(eye)
        p.setBrush(white)
        p.drawEllipse(QPointF(s / 2, s / 2), 2.6, 2.6)
        p.setPen(QPen(white, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(4, s - 3.5), QPointF(s - 4, 3.5))
    elif name == "step":
        p.drawEllipse(QRectF(2.5, 2.5, s - 5, s - 5))
        font = QFont()
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignmentFlag.AlignCenter, "1")
    elif name == "check":
        p.setPen(QPen(white, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath(QPointF(4.5, 11.5))
        path.lineTo(9, 16)
        path.lineTo(17.5, 6)
        p.drawPath(path)
    elif name in ("undo", "redo"):
        if name == "redo":
            p.translate(s, 0)
            p.scale(-1, 1)
        p.drawArc(QRectF(5, 6, s - 10, s - 10), 0, 180 * 16)
        p.setBrush(white)
        p.drawPolygon(QPolygonF([QPointF(5, 18), QPointF(1, 11), QPointF(9, 11)]))
    elif name == "copy":
        p.drawRect(QRectF(8, 3, 10, 12))
        p.drawRect(QRectF(4, 7, 10, 12))
    elif name == "save":
        p.drawRect(QRectF(4, 4, s - 8, s - 8))
        p.drawRect(QRectF(8, 4, 6, 5))
        p.drawLine(QPointF(7, 14), QPointF(s - 7, 14))
    elif name == "close":
        p.drawLine(QPointF(5, 5), QPointF(s - 5, s - 5))
        p.drawLine(QPointF(s - 5, 5), QPointF(5, s - 5))
    p.end()
    return QIcon(pm)


def _dot_icon(diameter: int, size: int = ICON) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#ffffff"))
    d = min(diameter + 2, size - 2)
    p.drawEllipse(QRectF((size - d) / 2, (size - d) / 2, d, d))
    p.end()
    return QIcon(pm)


def _button(tip: str, icon: QIcon | None = None, checkable: bool = False) -> QToolButton:
    btn = QToolButton()
    btn.setToolTip(tip)
    btn.setFixedSize(BUTTON, BUTTON)
    btn.setIconSize(QSize(ICON, ICON))
    btn.setCheckable(checkable)
    btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    btn.setCursor(Qt.CursorShape.ArrowCursor)
    if icon is not None:
        btn.setIcon(icon)
    return btn


def _swatch_style(color: str) -> str:
    return (
        f"QToolButton {{ background: {color}; border: 2px solid #777; border-radius: 4px; }}"
        "QToolButton:checked { border: 2px solid #ffffff; }"
        "QToolButton:hover { border: 2px solid #cccccc; }"
    )


def _separator() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet("background: rgba(255,255,255,50); border: none;")
    return line


class ToolBar(QFrame):
    """Vertical panel: tools, colours, thickness, undo/redo."""

    toolChanged = pyqtSignal(object)  # ToolKind or None
    colorChanged = pyqtSignal(QColor)
    thicknessChanged = pyqtSignal(int)
    undoRequested = pyqtSignal()
    redoRequested = pyqtSignal()

    def __init__(self, parent: QWidget, color: QColor, thickness: int) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(PANEL_STYLE)
        self._color = QColor(color)

        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)

        # --- tools -------------------------------------------------------------
        self._tool_buttons: dict[ToolKind, QToolButton] = {}
        specs = [
            (ToolKind.ARROW, "Arrow"), (ToolKind.RECT, "Rectangle"),
            (ToolKind.PEN, "Pen"), (ToolKind.MARKER, "Highlighter"),
            (ToolKind.TEXT, "Text"), (ToolKind.STEP, "Step number (1, 2, 3…)"),
            (ToolKind.REDACT, "Redact – hide passwords or personal data"),
        ]
        for i, (kind, tip) in enumerate(specs):
            btn = _button(tip, make_icon(kind.value), checkable=True)
            btn.clicked.connect(lambda checked, k=kind: self._on_tool(k, checked))
            self._tool_buttons[kind] = btn
            grid.addWidget(btn, i // 2, i % 2)
        row = (len(specs) + 1) // 2

        grid.addWidget(_separator(), row, 0, 1, 2)
        row += 1

        # --- colours -----------------------------------------------------------
        self._swatches: dict[str, QToolButton] = {}
        self._color_group = QButtonGroup(self)
        for i, hex_ in enumerate(PRESET_COLORS):
            btn = _button(hex_.upper(), checkable=True)
            btn.setStyleSheet(_swatch_style(hex_))
            btn.clicked.connect(lambda _c, h=hex_: self._apply_color(QColor(h)))
            self._color_group.addButton(btn)
            self._swatches[hex_] = btn
            grid.addWidget(btn, row + i // 2, i % 2)
        row += (len(PRESET_COLORS) + 1) // 2
        self._custom = _button("Custom colour…", checkable=True)
        self._custom.setText("…")
        self._custom.setStyleSheet(_swatch_style("#444444") + "QToolButton { color: white; }")
        self._custom.clicked.connect(self._pick_custom)
        self._color_group.addButton(self._custom)
        grid.addWidget(self._custom, row, 0, 1, 2)
        row += 1

        grid.addWidget(_separator(), row, 0, 1, 2)
        row += 1

        # --- thickness ---------------------------------------------------------
        self._width_group = QButtonGroup(self)
        for i, w in enumerate(THICKNESSES):
            btn = _button(f"Thickness {w}", _dot_icon(w), checkable=True)
            btn.clicked.connect(lambda _c, v=w: self.thicknessChanged.emit(v))
            self._width_group.addButton(btn)
            btn.setChecked(w == min(THICKNESSES, key=lambda t: abs(t - thickness)))
            grid.addWidget(btn, row + i // 2, i % 2)
        row += (len(THICKNESSES) + 1) // 2

        grid.addWidget(_separator(), row, 0, 1, 2)
        row += 1

        # --- undo / redo -------------------------------------------------------
        self._undo = _button("Undo (Ctrl+Z)", make_icon("undo"))
        self._redo = _button("Redo (Ctrl+Y)", make_icon("redo"))
        self._undo.clicked.connect(self.undoRequested)
        self._redo.clicked.connect(self.redoRequested)
        grid.addWidget(self._undo, row, 0)
        grid.addWidget(self._redo, row, 1)
        self.set_history_state(False, False)

        self._sync_color_buttons()
        self.adjustSize()

    # --- public ----------------------------------------------------------------
    def set_history_state(self, can_undo: bool, can_redo: bool) -> None:
        """Enable/disable the undo and redo buttons."""
        self._undo.setEnabled(can_undo)
        self._redo.setEnabled(can_redo)

    # --- internals -------------------------------------------------------------
    def _on_tool(self, kind: ToolKind, checked: bool) -> None:
        for k, btn in self._tool_buttons.items():
            btn.setChecked(checked and k == kind)
        self.toolChanged.emit(kind if checked else None)

    def _apply_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self._sync_color_buttons()
        self.colorChanged.emit(self._color)

    def _sync_color_buttons(self) -> None:
        match = self._swatches.get(self._color.name().lower())
        if match is not None:
            match.setChecked(True)
        else:
            self._custom.setStyleSheet(
                _swatch_style(self._color.name()) + "QToolButton { color: white; }"
            )
            self._custom.setChecked(True)

    def _pick_custom(self) -> None:
        chosen = QColorDialog.getColor(self._color, self.window(), "Pick a colour")
        if chosen.isValid():
            self._apply_color(chosen)
        else:
            self._sync_color_buttons()  # restore previous selection


class ActionBar(QFrame):
    """Horizontal panel: [primary action], copy, save, close.

    The optional primary button (e.g. "Add evidence step") is highlighted and is
    what Enter triggers in the overlay.
    """

    primaryRequested = pyqtSignal()
    copyRequested = pyqtSignal()
    saveRequested = pyqtSignal()
    closeRequested = pyqtSignal()

    def __init__(self, parent: QWidget, primary_tip: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(PANEL_STYLE)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.primary: QToolButton | None = None
        if primary_tip:
            self.primary = _button(primary_tip, make_icon("check"))
            self.primary.setObjectName("primary")
            self.primary.clicked.connect(self.primaryRequested)
            lay.addWidget(self.primary)
        for name, tip, sig in (
            ("copy", "Copy to clipboard (Ctrl+C)", self.copyRequested),
            ("save", "Save as… (Ctrl+S)", self.saveRequested),
            ("close", "Close (Esc)", self.closeRequested),
        ):
            btn = _button(tip, make_icon(name))
            btn.clicked.connect(sig)
            lay.addWidget(btn)
        self.adjustSize()


class Toast(QLabel):
    """Short-lived confirmation message."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            "background: rgba(20,20,20,230); color: white; border-radius: 6px;"
            "padding: 8px 14px; font-size: 13px;"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, text: str, center: QPoint, ms: int = 1600) -> None:
        """Show ``text`` centred on ``center`` (clamped inside the parent)."""
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        x, y = center.x() - self.width() // 2, center.y() - self.height() // 2
        if parent is not None:
            x = max(0, min(x, parent.width() - self.width()))
            y = max(0, min(y, parent.height() - self.height()))
        self.move(x, y)
        self.show()
        self.raise_()
        self._timer.start(ms)
