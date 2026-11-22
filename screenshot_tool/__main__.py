"""Entry point: ``python -m screenshot_tool``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from screenshot_tool import __version__
from screenshot_tool.capture import CaptureError, grab_snapshot
from screenshot_tool.config import Config, load_config
from screenshot_tool.hotkey import HotkeyError, HotkeyListener
from screenshot_tool.overlay import OverlayWindow


def _app_icon() -> QIcon:
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
    """Owns the hotkey, tray icon and the (single) active overlay."""

    def __init__(self, app: QApplication, config: Config, once: bool) -> None:
        super().__init__(app)
        self._app = app
        self._config = config
        self._once = once
        self._overlay: OverlayWindow | None = None
        self._tray: QSystemTrayIcon | None = None
        self._hotkey = HotkeyListener(config.hotkey, self)
        self._hotkey.activated.connect(self.capture)

    def start(self, capture_now: bool) -> bool:
        """Set up hotkey/tray. Returns False if the app has no way to be used."""
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

    def _setup_tray(self) -> bool:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        tray = QSystemTrayIcon(_app_icon(), self)
        menu = QMenu()
        grab = QAction(f"Capture ({self._config.hotkey})", menu)
        grab.triggered.connect(lambda: QTimer.singleShot(250, self.capture))  # let menu close
        quit_ = QAction("Quit", menu)
        quit_.triggered.connect(self._app.quit)
        menu.addAction(grab)
        menu.addSeparator()
        menu.addAction(quit_)
        tray.setContextMenu(menu)
        tray.setToolTip("Screenshot tool")
        tray.show()
        self._tray, self._menu = tray, menu  # keep references alive
        return True

    @pyqtSlot()
    def capture(self) -> None:
        """Freeze the screen and open the overlay (ignored if one is already open)."""
        if self._overlay is not None:
            return
        try:
            snapshot = grab_snapshot()
        except CaptureError as exc:
            self._error("Screenshot", str(exc))
            if self._once:
                self._app.exit(1)
            return
        self._overlay = OverlayWindow(snapshot, self._config)
        self._overlay.closed.connect(self._on_closed)
        self._overlay.show_overlay()

    def _on_closed(self) -> None:
        self._overlay = None
        if self._once:
            self._app.quit()

    def _error(self, title: str, message: str) -> None:
        logging.getLogger(__name__).error("%s: %s", title, message)
        QMessageBox.critical(None, title, message)

    def shutdown(self) -> None:
        """Stop background threads."""
        self._hotkey.stop()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="screenshot_tool", description="Screenshot + annotate tool")
    parser.add_argument("--once", action="store_true", help="capture once and exit (no hotkey/tray)")
    parser.add_argument("--background", action="store_true",
                        help="start in the tray without capturing right away")
    parser.add_argument("--config", help="path to a config.json (default: per-user location)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the application."""
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Screenshot Tool")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(_app_icon())

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    wake = QTimer()  # lets Python run its signal handlers while Qt's loop is busy
    wake.start(500)
    wake.timeout.connect(lambda: None)

    controller = ScreenshotApp(app, config, once=args.once)
    if not controller.start(capture_now=not args.background):
        print("Neither the hotkey nor a system tray is available; exiting.", file=sys.stderr)
        return 1
    try:
        return app.exec()
    finally:
        controller.shutdown()


if __name__ == "__main__":
    sys.exit(main())
