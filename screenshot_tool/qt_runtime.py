"""Create a Qt application on demand for headless use (MCP server, CLI, Python API).

Rendering text into images and writing PDFs needs a ``QGuiApplication``. The GUI
already has one; everything else calls :func:`ensure_gui_app` first.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtGui import QGuiApplication

_app: QGuiApplication | None = None  # keeps the instance we created alive


def _windows_font_dir() -> str:
    return str(Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts")


def ensure_gui_app() -> QGuiApplication:
    """Return the running ``QGuiApplication``, creating a windowless one if needed.

    * Windows uses the native platform plugin (no window is ever shown).
    * macOS and Linux use the ``offscreen`` plugin, so no display is required.

    Raises:
        RuntimeError: called from a worker thread before any Qt application exists
            (Qt must be initialised on the main thread).
    """
    global _app
    existing = QCoreApplication.instance()
    if existing is not None:
        if not isinstance(existing, QGuiApplication):
            raise RuntimeError("A non-GUI QCoreApplication is running; images cannot be rendered.")
        return existing
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Qt must be initialised from the main thread.")

    argv = [sys.argv[0] if sys.argv and sys.argv[0] else "screenshot_tool"]
    forced = os.environ.get("QT_QPA_PLATFORM", "")
    if not forced and sys.platform != "win32":
        argv += ["-platform", "offscreen"]  # via argv: child processes keep a real display
    if sys.platform == "win32" and forced.startswith("offscreen"):
        # Qt's offscreen plugin on Windows finds no fonts unless pointed at them.
        os.environ.setdefault("QT_QPA_FONTDIR", _windows_font_dir())
    _app = QGuiApplication(argv)
    return _app
