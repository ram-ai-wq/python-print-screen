"""Global hotkey via pynput, delivered to the Qt thread through a signal."""

from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

_MODIFIERS = {
    "ctrl": "<ctrl>", "control": "<ctrl>",
    "shift": "<shift>",
    "alt": "<alt>", "option": "<alt>",
    "cmd": "<cmd>", "command": "<cmd>", "win": "<cmd>", "super": "<cmd>", "meta": "<cmd>",
}


class HotkeyError(RuntimeError):
    """Raised when the global hotkey cannot be registered."""


def to_pynput_hotkey(text: str) -> str:
    """Convert ``"ctrl+shift+s"`` to pynput syntax ``"<ctrl>+<shift>+s"``.

    Single characters stay as-is; named keys (``f9``, ``print_screen``) get ``<>``.
    """
    parts = [t.strip().lower() for t in text.split("+") if t.strip()]
    if not parts:
        raise ValueError("hotkey is empty")
    out: list[str] = []
    for part in parts:
        if part in _MODIFIERS:
            out.append(_MODIFIERS[part])
        elif len(part) == 1:
            out.append(part)
        else:
            out.append(f"<{part}>")
    return "+".join(out)


class HotkeyListener(QObject):
    """Background hotkey listener. ``activated`` is emitted on the Qt thread."""

    activated = pyqtSignal()

    def __init__(self, hotkey: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hotkey = hotkey
        self._listener = None

    def start(self) -> None:
        """Register the hotkey.

        Raises:
            HotkeyError: invalid combination or no input backend available.
        """
        if sys.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE") == "wayland":
            log.warning("Wayland session: global hotkeys are unlikely to work.")
        try:
            combo = to_pynput_hotkey(self._hotkey)
        except ValueError as exc:
            raise HotkeyError(f"Invalid hotkey '{self._hotkey}': {exc}") from exc
        try:
            from pynput import keyboard  # imported lazily: needs a display on Linux

            self._listener = keyboard.GlobalHotKeys({combo: self.activated.emit})
            self._listener.start()
        except Exception as exc:
            raise HotkeyError(
                f"Could not register the global hotkey '{self._hotkey}': {exc}\n\n"
                "On macOS grant Accessibility/Input Monitoring permission; on Linux an "
                "X11 session is required (Wayland blocks global hotkeys)."
            ) from exc

    def stop(self) -> None:
        """Unregister the hotkey."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
