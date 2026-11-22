"""Screen capture (mss) and logical <-> pixel coordinate helpers."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

import mss
import mss.exception
from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QGuiApplication, QImage


class CaptureError(RuntimeError):
    """Raised when the screen cannot be captured; the message is user-facing."""


@dataclass
class Snapshot:
    """A frozen picture of the whole virtual desktop.

    Attributes:
        image: Capture in *physical* pixels.
        rect: Virtual desktop rectangle in Qt *logical* coordinates. The overlay
            window is placed exactly on this rectangle.
    """

    image: QImage
    rect: QRect


def normalize_rect(p1: QPoint, p2: QPoint) -> QRect:
    """Rectangle spanned by two arbitrary corner points (any drag direction)."""
    x, y = min(p1.x(), p2.x()), min(p1.y(), p2.y())
    return QRect(x, y, abs(p1.x() - p2.x()), abs(p1.y() - p2.y()))


def _round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def logical_to_pixel_rect(rect: QRect, sx: float, sy: float, max_w: int, max_h: int) -> QRect:
    """Map a logical rectangle to physical pixels.

    Both edges are rounded independently and clamped to ``[0, max]`` so adjacent
    selections tile without gaps and the result never leaves the image.
    """
    x0 = _round_half_up(rect.x() * sx)
    y0 = _round_half_up(rect.y() * sy)
    x1 = _round_half_up((rect.x() + rect.width()) * sx)
    y1 = _round_half_up((rect.y() + rect.height()) * sy)
    x0, x1 = (max(0, min(v, max_w)) for v in (x0, x1))
    y0, y1 = (max(0, min(v, max_h)) for v in (y0, y1))
    return QRect(x0, y0, x1 - x0, y1 - y0)


def _mac_screen_access_granted() -> bool:
    """Check (and request) the macOS Screen Recording permission."""
    try:
        import ctypes
        import ctypes.util

        lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
        preflight = lib.CGPreflightScreenCaptureAccess
        preflight.restype = ctypes.c_bool
        if preflight():
            return True
        request = lib.CGRequestScreenCaptureAccess
        request.restype = ctypes.c_bool
        request()  # shows the system prompt the first time
        return False
    except (OSError, AttributeError, TypeError):
        return True  # cannot tell; try the capture anyway


def _virtual_logical_rect() -> QRect:
    rect = QRect()
    for screen in QGuiApplication.screens():
        rect = rect.united(screen.geometry())
    return rect


def grab_snapshot() -> Snapshot:
    """Capture every monitor into one image.

    Raises:
        CaptureError: permission denied, no display, or the backend failed.
    """
    if sys.platform == "darwin" and not _mac_screen_access_granted():
        raise CaptureError(
            "Screen recording permission is required.\n\n"
            "Open System Settings > Privacy & Security > Screen Recording, enable "
            "this app (or your terminal), then restart it."
        )
    rect = _virtual_logical_rect()
    if rect.isEmpty():
        raise CaptureError("No screen was detected.")
    try:
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])  # monitors[0] = bounding box of all screens
    except mss.exception.ScreenShotError as exc:
        raise CaptureError(
            f"Screen capture failed: {exc}\n\n"
            "On Linux this tool needs an X11 session (Wayland is not supported)."
        ) from exc
    except Exception as exc:  # backend specific (e.g. no X server)
        raise CaptureError(f"Screen capture failed: {exc}") from exc

    # .copy() detaches the QImage from mss' buffer.
    image = QImage(
        shot.bgra, shot.width, shot.height, shot.width * 4, QImage.Format.Format_RGB32
    ).copy()
    return Snapshot(image=image, rect=rect)
