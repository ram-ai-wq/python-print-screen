"""Tray controller: session lifecycle, restore after restart, finish flow, --output mode."""

from pathlib import Path

import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QDialog, QMessageBox

from conftest import solid_image
from screenshot_tool import app as app_module
from screenshot_tool import session_ui
from screenshot_tool.app import EXIT_CANCELLED, ScreenshotApp
from screenshot_tool.capture import Snapshot
from screenshot_tool.config import Config, load_config
from screenshot_tool.evidence import EvidenceSession


@pytest.fixture
def config(tmp_path):
    cfg = Config(path=tmp_path / "config.json", evidence_folder=str(tmp_path / "ev"), tester="qa")
    cfg.save()
    return cfg


@pytest.fixture
def no_dialogs(monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: errors.append(a[2] if len(a) > 2 else a))
    monkeypatch.setattr(ScreenshotApp, "open_path", staticmethod(lambda path: errors.append(("opened", str(path)))))
    return errors


def test_new_session_is_started_and_persisted(qapp, config, monkeypatch, no_dialogs):
    monkeypatch.setattr(session_ui.NewSessionDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(session_ui.NewSessionDialog, "values", lambda self: {
        "title": "Checkout", "test_case_id": "TC-9", "tester": "ana", "environment": "Staging", "build": "1.0",
        "description": "", "stamp_images": False})
    ctl = ScreenshotApp(qapp, config)
    changes = []
    ctl.sessionChanged.connect(lambda: changes.append(True))
    ctl.new_session_dialog()
    assert ctl.session is not None and ctl.session.label == "TC-9 · Checkout" and changes
    assert ctl.session.path.parent == Path(config.evidence_folder)
    assert load_config(config.path).active_session == str(ctl.session.path)
    ctl.close_session()
    assert ctl.session is None and load_config(config.path).active_session == ""


def test_unfinished_session_is_restored_after_restart(qapp, config, no_dialogs):
    session = EvidenceSession.create(config.evidence_folder, "Long test", tester="qa")
    config.active_session = str(session.path)
    ctl = ScreenshotApp(qapp, config)
    ctl._restore_last_session()
    assert ctl.session is not None and ctl.session.id == session.id

    session.finish()
    ctl2 = ScreenshotApp(qapp, config)
    ctl2._restore_last_session()
    assert ctl2.session is None and config.active_session == ""  # finished sessions are not resumed


def test_resume_errors_are_reported(qapp, config, no_dialogs):
    ctl = ScreenshotApp(qapp, config)
    assert ctl.resume_session("missing-session") is False
    assert ctl.session is None and any("not found" in str(e) for e in no_dialogs)


def test_finish_dialog_finishes_exports_and_closes(qapp, config, monkeypatch, no_dialogs):
    session = EvidenceSession.create(config.evidence_folder, "Login", tester="qa")
    session.add_step(None, title="Check", status="failed")
    monkeypatch.setattr(session_ui.FinishDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(session_ui.FinishDialog, "values", lambda self: {
        "result": "failed", "summary": "Bug 42", "formats": ["md"], "open_report": True})
    ctl = ScreenshotApp(qapp, config)
    ctl.resume_session(session.path)
    ctl.finish_session_dialog()
    done = EvidenceSession.open(session.path)
    assert done.result == "failed" and done.summary == "Bug 42" and (session.path / "report.md").is_file()
    assert ctl.session is None
    assert ("opened", str(session.path / "report.md")) in no_dialogs


def test_hotkey_label(qapp, config):
    config.hotkey = "ctrl+shift+s"
    assert ScreenshotApp(qapp, config).hotkey_label == "Ctrl+Shift+S"


@pytest.mark.parametrize("send", [True, False])
def test_output_mode_exit_codes(qapp, config, tmp_path, monkeypatch, send):
    target = tmp_path / "agent.png"
    monkeypatch.setattr(app_module, "grab_snapshot", lambda: Snapshot(solid_image(300, 200), QRect(0, 0, 300, 200)))
    ctl = ScreenshotApp(qapp, config, output_path=str(target), prompt="Pick the total")
    assert ctl._exit_code == EXIT_CANCELLED
    ctl._open_overlay()
    overlay = ctl._overlay
    assert overlay is not None and overlay._output_path == str(target)
    if send:
        overlay.select_screen()
        overlay.primary_action()
        assert target.is_file()
    overlay.close()
    assert ctl._overlay is None
    assert ctl._exit_code == (0 if send else EXIT_CANCELLED)


def test_hotkey_does_not_open_overlay_behind_a_dialog(qapp, config, monkeypatch):
    monkeypatch.setattr(app_module, "grab_snapshot", lambda: Snapshot(solid_image(300, 200), QRect(0, 0, 300, 200)))
    ctl = ScreenshotApp(qapp, config)
    dialog = QDialog()
    dialog.setModal(True)
    dialog.show()
    try:
        ctl.capture()
        assert ctl._overlay is None  # the open dialog is brought forward instead
    finally:
        dialog.close()
    ctl.capture()
    assert ctl._overlay is not None
    ctl._overlay.close()


def test_session_window_acts_on_the_step_shown_even_if_stale(qapp, config, monkeypatch, no_dialogs):
    session = EvidenceSession.create(config.evidence_folder, "Stale", tester="qa")
    for title in ("A", "B", "C"):
        session.add_step(None, title=title)
    ctl = ScreenshotApp(qapp, config)
    ctl.resume_session(session.path)
    window = session_ui.SessionWindow(ctl)
    window.refresh()
    window._steps.setCurrentRow(1)                      # the tester selects "B"
    EvidenceSession.open(session.path).delete_step(1)   # meanwhile an agent deletes "A"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._delete_selected()
    assert [s.title for s in EvidenceSession.open(session.path).steps] == ["C"]
