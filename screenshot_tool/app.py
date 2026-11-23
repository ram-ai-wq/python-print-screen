"""Tray application: global hotkey, tray menu, capture overlay and evidence sessions."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon

from .capture import CaptureError, grab_snapshot
from .config import Config, load_config
from .evidence import EvidenceError, EvidenceSession, EvidenceStep
from .hotkey import HotkeyError, HotkeyListener
from .overlay import OverlayWindow
from .session_ui import FinishDialog, NewSessionDialog, SessionWindow

log = logging.getLogger(__name__)

EXIT_CANCELLED = 2  # --output mode: the tester closed the overlay without sending


def app_icon() -> QIcon:
    icon_file = Path(__file__).resolve().parent / "assets" / "icon.png"
    if icon_file.is_file():
        return QIcon(str(icon_file))
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#0a84ff"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 12, 12)
    p.setPen(QPen(QColor("white"), 4, Qt.PenStyle.DashLine))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(16, 18, 32, 28)
    p.end()
    return QIcon(pm)


class ScreenshotApp(QObject):
    """Owns the hotkey, tray icon, the (single) overlay and the active evidence session."""

    sessionChanged = pyqtSignal()

    def __init__(self, app: QApplication, config: Config, once: bool = False,
                 output_path: str | None = None, prompt: str = "") -> None:
        super().__init__(app)
        self._app = app
        self._config = config
        self._output_path = output_path
        self._once = once or bool(output_path)
        self._prompt = prompt
        self._overlay: OverlayWindow | None = None
        self._tray: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None
        self._window: SessionWindow | None = None
        self._reshow_window = False
        self._session: EvidenceSession | None = None
        self._default_status = "passed"
        self._exit_code = EXIT_CANCELLED if output_path else 0
        self._hotkey = HotkeyListener(config.hotkey, self)
        self._hotkey.activated.connect(self.capture)
        self.sessionChanged.connect(self._refresh_tray)

    # ------------------------------------------------------------ properties
    @property
    def config(self) -> Config:
        return self._config

    @property
    def session(self) -> EvidenceSession | None:
        return self._session

    @property
    def default_status(self) -> str:
        return self._default_status

    @property
    def hotkey_label(self) -> str:
        return "+".join(part.capitalize() if len(part) > 1 else part.upper()
                        for part in self._config.hotkey.split("+"))

    # ----------------------------------------------------------------- start
    def start(self, capture_now: bool, session_ref: str | None = None) -> bool:
        """Set up hotkey/tray. Returns False if the app has no way to be used."""
        if session_ref:
            self.resume_session(session_ref)  # on failure an error is shown and capture still works
        elif self._config.active_session and not self._output_path:
            self._restore_last_session()
        if not self._once:
            hotkey_ok = True
            try:
                self._hotkey.start()
            except HotkeyError as exc:
                hotkey_ok = False
                self._error("Global hotkey unavailable", str(exc))
            tray_ok = self._setup_tray()
            if not hotkey_ok and not tray_ok and not capture_now:
                return False
        if capture_now:
            QTimer.singleShot(0, self.capture)
        return True

    def _restore_last_session(self) -> None:
        try:
            session = EvidenceSession.open(self._config.active_session)
        except EvidenceError:
            self._set_session(None)
            return
        if session.is_finished:
            self._set_session(None)
        else:
            self._session = session
            self.sessionChanged.emit()

    def _setup_tray(self) -> bool:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        self._tray = QSystemTrayIcon(app_icon(), self)
        self._menu = QMenu()
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._refresh_tray()
        self._tray.show()
        return True

    def _refresh_tray(self) -> None:
        if self._tray is None or self._menu is None:
            return
        menu, session = self._menu, self._session
        menu.clear()
        capture = QAction(f"Capture {'evidence step' if session else 'screenshot'}  ({self.hotkey_label})", menu)
        capture.triggered.connect(lambda: QTimer.singleShot(250, self.capture))  # let the menu close
        menu.addAction(capture)
        menu.addSeparator()
        if session is not None:
            info = QAction(f"● {session.label} — {len(session.steps)} step(s)", menu)
            info.setEnabled(False)
            menu.addAction(info)
            self._add_action(menu, "Evidence session…", self.show_session_window)
            self._add_action(menu, "Finish session && create report…", self.finish_session_dialog)
        else:
            self._add_action(menu, "New evidence session…", self.new_session_dialog)
            self._add_action(menu, "Evidence sessions…", self.show_session_window)
        self._add_action(menu, "Open evidence folder", self.open_evidence_folder)
        menu.addSeparator()
        self._add_action(menu, "Quit", self._app.quit)
        tip = "Screenshot tool"
        if session is not None:
            tip += f"\nEvidence: {session.label} ({len(session.steps)} steps)"
        self._tray.setToolTip(tip)

    @staticmethod
    def _add_action(menu: QMenu, text: str, slot: Callable[[], Any]) -> None:
        action = QAction(text, menu)
        action.triggered.connect(lambda _checked=False: slot())
        menu.addAction(action)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_session_window()

    # --------------------------------------------------------------- capture
    @pyqtSlot()
    def capture(self) -> None:
        """Freeze the screen and open the overlay (ignored if one is already open)."""
        if self._overlay is not None:
            return
        modal = QApplication.activeModalWidget()
        if modal is not None:  # an overlay would be blocked by the open dialog: show the dialog instead
            modal.raise_()
            modal.activateWindow()
            return
        if self._window is not None and self._window.isVisible():
            self._window.hide()  # keep our own window out of the screenshot
            self._reshow_window = True
            QTimer.singleShot(250, self._open_overlay)
            return
        self._open_overlay()

    def capture_from_window(self) -> None:
        self.capture()

    def _open_overlay(self) -> None:
        if self._overlay is not None:
            return
        try:
            snapshot = grab_snapshot()
        except CaptureError as exc:
            self._error("Screenshot", str(exc))
            if self._once:
                self._app.exit(1)
            return
        self._overlay = OverlayWindow(
            snapshot, self._config,
            session=None if self._output_path else self._session,
            output_path=self._output_path, prompt=self._prompt, default_status=self._default_status,
        )
        self._overlay.closed.connect(self._on_closed)
        self._overlay.stepAdded.connect(self._on_step_added)
        self._overlay.outputSaved.connect(self._on_output_saved)
        self._overlay.show_overlay()

    def _on_step_added(self, step: EvidenceStep) -> None:
        self._default_status = step.status  # the next step starts with the same status
        self.sessionChanged.emit()

    def _on_output_saved(self, _path: str) -> None:
        self._exit_code = 0

    def _on_closed(self) -> None:
        self._overlay = None
        if self._once:
            QTimer.singleShot(0, lambda: self._app.exit(self._exit_code))
            return
        if self._reshow_window and self._window is not None:
            self._reshow_window = False
            self._window.show()

    # -------------------------------------------------------------- sessions
    def show_session_window(self) -> None:
        if self._window is None:
            self._window = SessionWindow(self)
            self._window.setWindowIcon(app_icon())
        self._window.refresh()
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def new_session_dialog(self) -> None:
        dialog = NewSessionDialog(self._window if self._window and self._window.isVisible() else None,
                                  self._config)
        dialog.setWindowIcon(app_icon())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            session = EvidenceSession.create(self._config.evidence_root(), **values)
        except EvidenceError as exc:
            self._error("New evidence session", str(exc))
            return
        self._default_status = "passed"
        self._set_session(session)
        self._notify("Evidence session started",
                     f"{session.label}\nPress {self.hotkey_label} to capture each step.")

    def resume_session(self, ref: str | Path) -> bool:
        try:
            session = EvidenceSession.open(ref, self._config.evidence_root())
        except EvidenceError as exc:
            self._error("Evidence session", str(exc))
            return False
        self._set_session(session)
        return True

    def close_session(self) -> None:
        self._set_session(None)

    def _set_session(self, session: EvidenceSession | None) -> None:
        self._session = session
        self._config.active_session = str(session.path) if session else ""
        self._config.save()
        self.sessionChanged.emit()

    def finish_session_dialog(self) -> None:
        session = self._session
        if session is None:
            return
        self.run_safely(session.reload)
        dialog = FinishDialog(self._window if self._window and self._window.isVisible() else None,
                              session, self._config)
        dialog.setWindowIcon(app_icon())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()

        def finish_and_export() -> dict[str, Path]:
            session.finish(values["result"], values["summary"])
            return session.export(values["formats"])

        reports = self._export(finish_and_export)
        if reports is None:
            return
        self._set_session(None)
        if values["open_report"]:
            self.open_path(next(iter(reports.values())))
        self._notify("Evidence report created", f"{session.label}: {session.result}\n{session.path}")

    def generate_report(self) -> None:
        session = self._session
        if session is None:
            return
        reports = self._export(lambda: session.export(self._config.report_formats))
        if reports:
            self.open_path(next(iter(reports.values())))

    def _export(self, job: Callable[[], dict[str, Path]]) -> dict[str, Path] | None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return job()
        except EvidenceError as exc:
            QApplication.restoreOverrideCursor()
            self._error("Evidence report", str(exc))
            return None
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
            self.sessionChanged.emit()

    def run_safely(self, action: Callable[[], Any]) -> Any:
        """Run a session operation, report errors in a dialog, then refresh the UI."""
        try:
            return action()
        except EvidenceError as exc:
            self._error("Evidence session", str(exc))
            return None
        finally:
            self.sessionChanged.emit()

    def open_evidence_folder(self) -> None:
        root = self._config.evidence_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._error("Evidence folder", f"Cannot create {root}: {exc}")
            return
        self.open_path(root)

    @staticmethod
    def open_path(path: str | Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ---------------------------------------------------------------- misc
    def _notify(self, title: str, message: str) -> None:
        if self._tray is not None and QSystemTrayIcon.supportsMessages():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 4000)

    def _error(self, title: str, message: str) -> None:
        log.error("%s: %s", title, message)
        QMessageBox.critical(None, title, message)

    def shutdown(self) -> None:
        """Stop background threads."""
        self._hotkey.stop()


def run_gui(args: argparse.Namespace) -> int:
    """Run the tray app / capture overlay until it quits."""
    config = load_config(args.config)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Screenshot Tool")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()  # lets Python run its signal handlers while Qt's loop is busy
    wake.start(500)
    wake.timeout.connect(lambda: None)

    controller = ScreenshotApp(app, config, once=args.once, output_path=args.output, prompt=args.prompt or "")
    capture_now = bool(args.output) or not args.background
    if not controller.start(capture_now=capture_now, session_ref=args.session):
        print("Neither the hotkey nor a system tray is available; exiting.", file=sys.stderr)
        return 1
    try:
        return app.exec()
    finally:
        controller.shutdown()
