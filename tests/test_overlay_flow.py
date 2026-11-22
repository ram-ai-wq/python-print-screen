"""End-to-end test of the overlay using simulated mouse input (offscreen)."""

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest

from screenshot_tool.capture import Snapshot
from screenshot_tool.config import Config
from screenshot_tool.overlay import OverlayWindow
from screenshot_tool.tools import ToolKind


def make_overlay(tmp_path):
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    cfg = Config(default_color="#ff0000", thickness=6, path=tmp_path / "c.json")
    ov = OverlayWindow(Snapshot(img, QRect(0, 0, 400, 300)), cfg)
    ov.show()
    return ov


def drag(ov, a, b):
    QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(ov, b)
    QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=b)


def test_select_draw_arrow_undo_and_render(qapp, tmp_path):
    ov = make_overlay(tmp_path)
    drag(ov, QPoint(50, 50), QPoint(250, 150))
    assert ov.selection() == QRect(50, 50, 200, 100)

    ov._tool = ToolKind.ARROW
    drag(ov, QPoint(70, 100), QPoint(200, 100))
    assert len(ov._history.items) == 1

    out = ov.render_result()
    assert (out.width(), out.height()) == (200, 100)       # pixel-accurate size
    assert out.pixelColor(80, 50) == QColor("#ff0000")      # arrow is baked in
    assert out.pixelColor(5, 5) == QColor("white")

    drag(ov, QPoint(70, 120), QPoint(220, 120))
    assert len(ov._history.items) == 2
    ov.undo()
    assert len(ov._history.items) == 1                       # only the last one removed
    ov.redo()
    assert len(ov._history.items) == 2
    ov.close()


def test_move_and_resize_selection(qapp, tmp_path):
    ov = make_overlay(tmp_path)
    drag(ov, QPoint(100, 100), QPoint(200, 180))
    drag(ov, QPoint(150, 140), QPoint(170, 150))            # inside, no tool -> move
    assert ov.selection() == QRect(120, 110, 100, 80)
    drag(ov, QPoint(220, 190), QPoint(260, 230))            # bottom-right handle
    assert ov.selection() == QRect(120, 110, 140, 120)
    ov.close()


def test_click_without_drag_selects_nothing_and_esc_closes(qapp, tmp_path):
    ov = make_overlay(tmp_path)
    QTest.mouseClick(ov, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert ov.selection() is None
    closed = []
    ov.closed.connect(lambda: closed.append(True))
    QTest.keyClick(ov, Qt.Key.Key_Escape)
    assert closed
