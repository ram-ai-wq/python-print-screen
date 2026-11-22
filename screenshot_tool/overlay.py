"""Fullscreen overlay: frozen snapshot, region selection, annotation and output."""

from __future__ import annotations

from enum import Enum, auto
from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFontMetrics,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QRegion,
)
from PyQt6.QtWidgets import QFileDialog, QLineEdit, QMessageBox, QWidget

from .capture import Snapshot, logical_to_pixel_rect, normalize_rect
from .config import Config
from .storage import StorageError, compose_image, copy_to_clipboard, default_filename, save_image
from .toolbar import ActionBar, Toast, ToolBar
from .tools import (
    Annotation,
    History,
    TextAnnotation,
    ToolKind,
    create_annotation,
    paint_annotations,
    text_font,
)

ACCENT = QColor("#0a84ff")
DIM = QColor(0, 0, 0, 120)
HANDLE_DRAW = 8   # drawn handle size (px)
HANDLE_HIT = 8    # hit radius around handle centre (px)
GAP = 8           # space between selection and toolbars


class Handle(Enum):
    """The eight resize handles."""

    TL = auto()
    T = auto()
    TR = auto()
    R = auto()
    BR = auto()
    B = auto()
    BL = auto()
    L = auto()


_CURSORS = {
    Handle.TL: Qt.CursorShape.SizeFDiagCursor, Handle.BR: Qt.CursorShape.SizeFDiagCursor,
    Handle.TR: Qt.CursorShape.SizeBDiagCursor, Handle.BL: Qt.CursorShape.SizeBDiagCursor,
    Handle.T: Qt.CursorShape.SizeVerCursor, Handle.B: Qt.CursorShape.SizeVerCursor,
    Handle.L: Qt.CursorShape.SizeHorCursor, Handle.R: Qt.CursorShape.SizeHorCursor,
}


def resize_rect(rect: QRect, handle: Handle, pos: QPoint) -> QRect:
    """Return ``rect`` with the edges belonging to ``handle`` moved to ``pos``.

    Dragging an edge past the opposite one flips the rectangle (it stays valid).
    """
    left, top = rect.x(), rect.y()
    right, bottom = rect.x() + rect.width(), rect.y() + rect.height()
    if handle in (Handle.TL, Handle.L, Handle.BL):
        left = pos.x()
    if handle in (Handle.TR, Handle.R, Handle.BR):
        right = pos.x()
    if handle in (Handle.TL, Handle.T, Handle.TR):
        top = pos.y()
    if handle in (Handle.BL, Handle.B, Handle.BR):
        bottom = pos.y()
    return QRect(min(left, right), min(top, bottom), abs(right - left), abs(bottom - top))


def move_rect(rect: QRect, dx: int, dy: int, bounds: QRect) -> QRect:
    """Translate ``rect`` by (dx, dy) while keeping it fully inside ``bounds``."""
    x = max(bounds.x(), min(rect.x() + dx, bounds.x() + bounds.width() - rect.width()))
    y = max(bounds.y(), min(rect.y() + dy, bounds.y() + bounds.height() - rect.height()))
    return QRect(x, y, rect.width(), rect.height())


class Mode(Enum):
    """Current mouse interaction."""

    IDLE = auto()
    SELECTING = auto()
    MOVING = auto()
    RESIZING = auto()
    DRAWING = auto()


class TextInput(QLineEdit):
    """Inline editor for text annotations: Enter commits, Esc cancels."""

    committed = pyqtSignal()
    cancelled = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt API)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.committed.emit()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if event.reason() not in (
            Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason
        ):
            self.committed.emit()


class OverlayWindow(QWidget):
    """Frameless window covering the virtual desktop showing the frozen snapshot."""

    closed = pyqtSignal()

    def __init__(self, snapshot: Snapshot, config: Config) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._snap = snapshot
        self._config = config
        self._color = QColor(config.default_color)
        self._width = config.thickness

        # Pre-render bright and dimmed pixmaps once; painting is then just blits.
        ratio = snapshot.image.width() / max(1, snapshot.rect.width())
        dimmed = snapshot.image.copy()
        painter = QPainter(dimmed)
        painter.fillRect(dimmed.rect(), DIM)
        painter.end()
        self._bright = QPixmap.fromImage(snapshot.image)
        self._dim = QPixmap.fromImage(dimmed)
        self._bright.setDevicePixelRatio(ratio)
        self._dim.setDevicePixelRatio(ratio)

        self._sel: QRect | None = None
        self._confirmed = False
        self._mode = Mode.IDLE
        self._origin = QPoint()
        self._orig_sel = QRect()
        self._handle: Handle | None = None
        self._tool: ToolKind | None = None
        self._history = History()
        self._current: Annotation | None = None
        self._text_edit: TextInput | None = None
        self._text_ctx: tuple[QColor, int] = (self._color, self._width)
        self._closing = False

        self._tools = ToolBar(self, self._color, self._width)
        self._actions = ActionBar(self)
        self._toast = Toast(self)
        self._tools.hide()
        self._actions.hide()
        self._tools.toolChanged.connect(self._on_tool_changed)
        self._tools.colorChanged.connect(self._on_color_changed)
        self._tools.thicknessChanged.connect(self._on_width_changed)
        self._tools.undoRequested.connect(self.undo)
        self._tools.redoRequested.connect(self.redo)
        self._actions.copyRequested.connect(self.copy)
        self._actions.saveRequested.connect(self.save)
        self._actions.closeRequested.connect(self.close)

        self.setGeometry(snapshot.rect)

    # ------------------------------------------------------------------ public API
    def show_overlay(self) -> None:
        """Show on top and grab keyboard focus."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def selection(self) -> QRect | None:
        """Current selection in logical (widget) coordinates."""
        return QRect(self._sel) if self._sel else None

    def undo(self) -> None:
        """Remove the most recent annotation."""
        self._finish_text(commit=True)
        if self._history.undo():
            self._after_history_change()

    def redo(self) -> None:
        """Re-apply the most recently undone annotation."""
        self._finish_text(commit=True)
        if self._history.redo():
            self._after_history_change()

    def render_result(self) -> QImage:
        """Compose the selected region with annotations baked in."""
        self._finish_text(commit=True)
        if self._sel is None or not self._confirmed:
            raise StorageError("Select an area first.")
        sx, sy = self._scale()
        return compose_image(self._snap.image, self._sel, list(self._history.items), sx, sy)

    def copy(self) -> None:
        """Copy the result to the clipboard, confirm with a toast, then close."""
        if not self._confirmed:
            return
        try:
            copy_to_clipboard(self.render_result())
        except StorageError as exc:
            self._warn(str(exc))
            return
        self._toast_and_close("Copied to clipboard")

    def save(self) -> None:
        """Ask for a file name, save, confirm with a toast, then close."""
        if not self._confirmed:
            return
        try:
            image = self.render_result()
        except StorageError as exc:
            self._warn(str(exc))
            return
        start = str(self._config.resolved_save_folder() / default_filename())
        path, chosen = QFileDialog.getSaveFileName(
            self, "Save screenshot", start, "PNG image (*.png);;JPEG image (*.jpg *.jpeg)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".jpg" if "JPEG" in chosen else ".png"
        try:
            saved = save_image(image, path)
        except StorageError as exc:
            self._warn(str(exc))
            return
        self._config.save_folder = str(saved.parent)
        self._config.save()
        self._toast_and_close(f"Saved {saved.name}")

    # ------------------------------------------------------------------ geometry
    def _scale(self) -> tuple[float, float]:
        img = self._snap.image
        return img.width() / max(1, self.width()), img.height() / max(1, self.height())

    def _pixel_rect(self, rect: QRect) -> QRect:
        sx, sy = self._scale()
        img = self._snap.image
        return logical_to_pixel_rect(rect, sx, sy, img.width(), img.height())

    def _clamp(self, pos: QPoint) -> QPoint:
        return QPoint(max(0, min(pos.x(), self.width())), max(0, min(pos.y(), self.height())))

    def _clamp_to_sel(self, pos: QPoint) -> QPointF:
        assert self._sel is not None
        s = self._sel
        return QPointF(
            max(s.x(), min(pos.x(), s.x() + s.width())),
            max(s.y(), min(pos.y(), s.y() + s.height())),
        )

    def _handle_points(self) -> dict[Handle, QPoint]:
        assert self._sel is not None
        s = self._sel
        l, t = s.x(), s.y()
        r, b = l + s.width(), t + s.height()
        cx, cy = l + s.width() // 2, t + s.height() // 2
        return {
            Handle.TL: QPoint(l, t), Handle.TR: QPoint(r, t),
            Handle.BR: QPoint(r, b), Handle.BL: QPoint(l, b),
            Handle.T: QPoint(cx, t), Handle.R: QPoint(r, cy),
            Handle.B: QPoint(cx, b), Handle.L: QPoint(l, cy),
        }

    def _hit_handle(self, pos: QPoint) -> Handle | None:
        if self._sel is None or not self._confirmed:
            return None
        for handle, pt in self._handle_points().items():
            if abs(pt.x() - pos.x()) <= HANDLE_HIT and abs(pt.y() - pos.y()) <= HANDLE_HIT:
                return handle
        return None

    def _set_selection(self, new: QRect | None) -> None:
        """Replace the selection and repaint only the affected area."""
        dirty = QRect()
        for r in (self._sel, new):
            if r is not None:
                dirty = dirty.united(r)
        self._sel = new
        self.update(dirty.adjusted(-60, -60, 60, 60) if not dirty.isNull() else self.rect())

    # ------------------------------------------------------------------ painting
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        sel = self._sel
        if sel is None or sel.isEmpty():
            p.drawPixmap(0, 0, self._dim)
            return
        p.setClipRegion(QRegion(self.rect()).subtracted(QRegion(sel)))
        p.drawPixmap(0, 0, self._dim)
        p.setClipRect(sel)
        p.drawPixmap(0, 0, self._bright)
        items = list(self._history.items)
        if self._current is not None:
            items.append(self._current)
        paint_annotations(p, items)
        p.setClipping(False)

        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.setPen(QPen(ACCENT, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(sel.adjusted(0, 0, -1, -1))
        if self._confirmed:
            p.setBrush(QColor("white"))
            for pt in self._handle_points().values():
                h = HANDLE_DRAW
                p.drawRect(pt.x() - h // 2, pt.y() - h // 2, h, h)
        self._paint_size_label(p, sel)

    def _paint_size_label(self, p: QPainter, sel: QRect) -> None:
        pr = self._pixel_rect(sel)
        text = f"{pr.width()}x{pr.height()}"
        fm = QFontMetrics(p.font())
        w, h = fm.horizontalAdvance(text) + 14, fm.height() + 8
        x = max(0, min(sel.x(), self.width() - w))
        y = sel.y() - h - 4
        if y < 0:
            y = sel.y() + 4
        box = QRectF(x, y, w, h)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 190))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor("white"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    # ------------------------------------------------------------------ toolbars
    def _layout_toolbars(self) -> None:
        if self._sel is None:
            return
        sel, bounds = self._sel, self.rect()
        tw, th = self._tools.width(), self._tools.height()
        aw, ah = self._actions.width(), self._actions.height()

        x = sel.x() + sel.width() + GAP  # prefer the right side
        if x + tw > bounds.width():
            x = sel.x() - GAP - tw
        if x < 0:  # no room on either side: tuck inside the right edge
            x = max(0, min(sel.x() + sel.width() - tw - GAP, bounds.width() - tw))
        y = max(0, min(sel.y(), bounds.height() - th))
        tools_rect = QRect(x, y, tw, th)

        ax = max(0, min(sel.x() + sel.width() - aw, bounds.width() - aw))
        ay = sel.y() + sel.height() + GAP  # prefer below
        if ay + ah > bounds.height():
            ay = sel.y() - GAP - ah
        if ay < 0:  # no room above or below: tuck inside the bottom
            ay = max(0, sel.y() + sel.height() - ah - GAP)
        actions_rect = QRect(ax, ay, aw, ah)
        if actions_rect.intersects(tools_rect):
            actions_rect.moveRight(tools_rect.x() - GAP - 1)
            if actions_rect.x() < 0:
                actions_rect.moveLeft(0)

        self._tools.move(tools_rect.topLeft())
        self._actions.move(actions_rect.topLeft())
        for w in (self._tools, self._actions):
            w.show()
            w.raise_()

    def _hide_toolbars(self) -> None:
        self._tools.hide()
        self._actions.hide()

    def _after_history_change(self) -> None:
        self._tools.set_history_state(self._history.can_undo, self._history.can_redo)
        if self._sel is not None:
            self.update(self._sel.adjusted(-2, -2, 2, 2))

    # ------------------------------------------------------------------ tool state
    def _on_tool_changed(self, tool: ToolKind | None) -> None:
        self._finish_text(commit=True)
        self._tool = tool
        self.setFocus()
        self._update_cursor(self.mapFromGlobal(QCursor.pos()))

    def _on_color_changed(self, color: QColor) -> None:
        self._color = QColor(color)
        self.setFocus()

    def _on_width_changed(self, width: int) -> None:
        self._width = width
        self.setFocus()

    def _update_cursor(self, pos: QPoint) -> None:
        shape = Qt.CursorShape.CrossCursor
        if self._sel is not None and self._confirmed:
            handle = self._hit_handle(pos)
            if handle is not None:
                shape = _CURSORS[handle]
            elif self._sel.contains(pos) and self._tool is None:
                shape = Qt.CursorShape.SizeAllCursor
            elif self._sel.contains(pos) and self._tool is ToolKind.TEXT:
                shape = Qt.CursorShape.IBeamCursor
        self.setCursor(shape)

    # ------------------------------------------------------------------ text tool
    def _begin_text(self, pos: QPoint) -> None:
        color, width = QColor(self._color), self._width
        editor = TextInput(self)
        editor.setFont(text_font(width))
        height = QFontMetrics(editor.font()).height() + 4
        editor.setStyleSheet(
            f"QLineEdit {{ background: rgba(0,0,0,70); color: {color.name()};"
            f" border: 1px dashed {color.name()}; padding: 0px 2px; }}"
        )
        editor.setFixedHeight(height)
        editor.setGeometry(pos.x(), pos.y(), 140, height)
        editor.textChanged.connect(self._fit_editor)
        editor.committed.connect(lambda: self._finish_text(commit=True))
        editor.cancelled.connect(lambda: self._finish_text(commit=False))
        self._text_edit = editor
        self._text_ctx = (color, width)
        editor.show()
        editor.setFocus()

    def _fit_editor(self) -> None:
        ed, sel = self._text_edit, self._sel
        if ed is None or sel is None:
            return
        need = QFontMetrics(ed.font()).horizontalAdvance(ed.text()) + 24
        room = sel.x() + sel.width() - ed.x()
        ed.setFixedWidth(max(40, min(max(140, need), room)))

    def _finish_text(self, commit: bool) -> None:
        """Close the inline editor, optionally turning its text into an annotation."""
        editor = self._text_edit
        if editor is None:
            return
        self._text_edit = None  # makes re-entrant calls (focus-out) harmless
        text = editor.text()
        anchor = QPointF(editor.x() + 4, editor.y() + 2)
        editor.hide()
        editor.deleteLater()
        if commit and text.strip():
            color, width = self._text_ctx
            self._history.add(TextAnnotation(anchor, color, width, text))
            self._after_history_change()
        self.setFocus()

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._finish_text(commit=True)
        pos = self._clamp(event.position().toPoint())

        if self._sel is not None and self._confirmed:
            handle = self._hit_handle(pos)
            if handle is not None:
                self._mode, self._handle = Mode.RESIZING, handle
                self._orig_sel = QRect(self._sel)
                return
            if self._sel.contains(pos):
                if self._tool is ToolKind.TEXT:
                    self._begin_text(pos)
                elif self._tool is not None:
                    self._current = create_annotation(
                        self._tool, self._clamp_to_sel(pos), self._color, self._width
                    )
                    self._mode = Mode.DRAWING
                    self.update(self._sel)
                else:
                    self._mode = Mode.MOVING
                    self._origin, self._orig_sel = pos, QRect(self._sel)
                return
            if self._tool is not None:
                return  # clicking outside while a tool is armed does nothing

        # Start a brand-new selection (discards the old one and its annotations).
        self._history.clear()
        self._after_history_change()
        self._hide_toolbars()
        self._confirmed = False
        self._mode = Mode.SELECTING
        self._origin = pos
        self._set_selection(QRect(pos, pos))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = self._clamp(event.position().toPoint())
        mode = self._mode
        if mode is Mode.SELECTING:
            self._set_selection(normalize_rect(self._origin, pos))
        elif mode is Mode.RESIZING and self._handle is not None:
            self._set_selection(
                resize_rect(self._orig_sel, self._handle, pos).intersected(self.rect())
            )
            self._layout_toolbars()
        elif mode is Mode.MOVING:
            delta = pos - self._origin
            self._set_selection(move_rect(self._orig_sel, delta.x(), delta.y(), self.rect()))
            self._layout_toolbars()
        elif mode is Mode.DRAWING and self._current is not None and self._sel is not None:
            self._current.update_drag(self._clamp_to_sel(pos))
            self.update(self._sel)
        else:
            self._update_cursor(pos)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        mode, self._mode = self._mode, Mode.IDLE
        if mode is Mode.SELECTING:
            sel = self._sel
            if sel is None or sel.width() < 3 or sel.height() < 3:
                self._set_selection(None)  # a plain click selects nothing
            else:
                self._confirmed = True
                self._layout_toolbars()
                self.update(sel.adjusted(-60, -60, 60, 60))
        elif mode is Mode.DRAWING:
            item, self._current = self._current, None
            if item is not None and item.is_valid():
                self._history.add(item)
            self._after_history_change()
        elif mode in (Mode.MOVING, Mode.RESIZING):
            self._layout_toolbars()
            if self._sel is not None:
                self.update(self._sel.adjusted(-60, -60, 60, 60))
        self._update_cursor(event.position().toPoint())

    # ------------------------------------------------------------------ keyboard
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key, mods = event.key(), event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        if key == Qt.Key.Key_Escape:
            self.close()
        elif ctrl and key == Qt.Key.Key_Z and not shift:
            self.undo()
        elif ctrl and (key == Qt.Key.Key_Y or (key == Qt.Key.Key_Z and shift)):
            self.redo()
        elif ctrl and key == Qt.Key.Key_C:
            self.copy()
        elif ctrl and key == Qt.Key.Key_S:
            self.save()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------ lifecycle
    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "Screenshot", message)

    def _toast_and_close(self, message: str) -> None:
        sel = self._sel or self.rect()
        self._toast.show_message(message, sel.center())
        if not self._closing:
            self._closing = True
            QTimer.singleShot(900, self.close)

    def closeEvent(self, event: QEvent) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
