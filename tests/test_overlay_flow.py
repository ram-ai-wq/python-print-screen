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


# --------------------------------------------------------------- evidence modes
from PyQt6.QtWidgets import QDialog  # noqa: E402

from screenshot_tool.evidence import EvidenceSession  # noqa: E402
from screenshot_tool.tools import StepBadgeAnnotation  # noqa: E402


def make_evidence_overlay(tmp_path, ask_details=False, **kw):
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    cfg = Config(path=tmp_path / "c.json", ask_step_details=ask_details)
    ov = OverlayWindow(Snapshot(img, QRect(0, 0, 400, 300)), cfg, **kw)
    ov.show()
    return ov


def test_enter_adds_step_to_session(qapp, tmp_path):
    session = EvidenceSession.create(tmp_path / "ev", "Login", tester="qa", stamp_images=False)
    ov = make_evidence_overlay(tmp_path, session=session, default_status="failed")
    added = []
    ov.stepAdded.connect(added.append)
    drag(ov, QPoint(20, 20), QPoint(120, 80))
    ov._tool = ToolKind.STEP
    drag(ov, QPoint(40, 40), QPoint(40, 40))
    drag(ov, QPoint(80, 40), QPoint(80, 40))
    assert [a.number for a in ov._history.items if isinstance(a, StepBadgeAnnotation)] == [1, 2]
    QTest.keyClick(ov, Qt.Key.Key_Return)
    assert len(added) == 1 and added[0].status == "failed" and added[0].source == "manual"
    step = EvidenceSession.open(session.path).steps[0]
    assert (step.width, step.height) == (100, 60) and step.title == "Step 1"
    ov.close()


def test_step_dialog_values_are_used(qapp, tmp_path, monkeypatch):
    from screenshot_tool import session_ui

    session = EvidenceSession.create(tmp_path / "ev", "Login", tester="qa", stamp_images=False)
    monkeypatch.setattr(session_ui.StepDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(session_ui.StepDialog, "values",
                        lambda self: {"title": "Error shown", "status": "passed", "expected": "Error",
                                      "actual": "Error", "notes": ""})
    ov = make_evidence_overlay(tmp_path, ask_details=True, session=session)
    drag(ov, QPoint(20, 20), QPoint(120, 80))
    ov.add_to_evidence()
    assert EvidenceSession.open(session.path).steps[0].title == "Error shown"
    ov.close()


def test_cancelled_step_dialog_keeps_overlay_open(qapp, tmp_path, monkeypatch):
    from screenshot_tool import session_ui

    session = EvidenceSession.create(tmp_path / "ev", "Login", tester="qa")
    monkeypatch.setattr(session_ui.StepDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    ov = make_evidence_overlay(tmp_path, ask_details=True, session=session)
    drag(ov, QPoint(20, 20), QPoint(120, 80))
    ov.add_to_evidence()
    assert EvidenceSession.open(session.path).steps == [] and not ov._closing
    ov.close()


def test_output_mode_writes_file(qapp, tmp_path):
    target = tmp_path / "agent.png"
    ov = make_evidence_overlay(tmp_path, output_path=str(target), prompt="Select the total")
    saved = []
    ov.outputSaved.connect(saved.append)
    assert "Select the total" in ov._hint.text() and ov._hint.isVisible()
    QTest.keyClick(ov, Qt.Key.Key_Return)  # nothing selected yet: ignored
    assert not target.exists()
    drag(ov, QPoint(10, 10), QPoint(60, 50))
    QTest.keyClick(ov, Qt.Key.Key_Enter)
    assert saved == [str(target)] and QImage(str(target)).size().width() == 50
    ov.close()


def test_ctrl_a_selects_a_whole_screen(qapp, tmp_path):
    ov = make_evidence_overlay(tmp_path)
    QTest.keyClick(ov, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    sel = ov.selection()
    assert sel is not None and not sel.isEmpty() and ov._confirmed
    assert ov.rect().contains(sel)
    ov.close()
