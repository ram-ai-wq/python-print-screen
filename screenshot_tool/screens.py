"""Headless screen capture (no overlay): monitors, regions and, on Windows, windows.

Used by the MCP server and the CLI. Coordinates are physical pixels of the
virtual desktop (the same space ``mss`` uses).
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import asdict, dataclass

import mss
import mss.exception
from PyQt6.QtGui import QImage


class CaptureTargetError(RuntimeError):
    """The requested monitor/window/region cannot be captured; the message is user-facing."""


@dataclass
class Monitor:
    index: int  # 1-based, as in mss
    x: int
    y: int
    width: int
    height: int
    primary: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WindowInfo:
    title: str
    process: str
    x: int
    y: int
    width: int
    height: int
    minimized: bool
    handle: int = 0

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("handle")
        return data


# ---------------------------------------------------------------- monitors
def _grab(region: dict[str, int]) -> QImage:
    try:
        with mss.mss() as sct:
            shot = sct.grab(region)
    except mss.exception.ScreenShotError as exc:
        raise CaptureTargetError(
            f"Screen capture failed: {exc}. A desktop session is required "
            "(on Linux an X11 session; Wayland is not supported)."
        ) from exc
    except Exception as exc:  # backend specific (no display, permissions...)
        raise CaptureTargetError(f"Screen capture failed: {exc}") from exc
    return QImage(shot.bgra, shot.width, shot.height, shot.width * 4, QImage.Format.Format_RGB32).copy()


def _all_monitors() -> list[dict[str, int]]:
    try:
        with mss.mss() as sct:
            return [dict(m) for m in sct.monitors]
    except Exception as exc:  # noqa: BLE001 - no display, permissions...
        raise CaptureTargetError(f"Cannot access the screen: {exc}") from exc


def list_monitors() -> list[Monitor]:
    """Physical monitors (index 1..n). The desktop's origin monitor is the primary one."""
    mons = _all_monitors()
    return [
        Monitor(i, m["left"], m["top"], m["width"], m["height"], primary=(m["left"], m["top"]) == (0, 0))
        for i, m in enumerate(mons[1:], 1)
    ]


def capture_monitor(index: int | None = None) -> tuple[QImage, dict[str, int]]:
    """Capture monitor ``index`` (1-based) or, if ``None``/0, all monitors as one image."""
    mons = _all_monitors()
    if index is None or index == 0:
        region = mons[0]
    elif 1 <= index < len(mons):
        region = mons[index]
    else:
        raise CaptureTargetError(f"Monitor {index} does not exist; available: 1-{len(mons) - 1}.")
    region = {k: region[k] for k in ("left", "top", "width", "height")}
    return _grab(region), region


def capture_region(x: int, y: int, width: int, height: int) -> tuple[QImage, dict[str, int]]:
    """Capture a rectangle of the virtual desktop (clipped to the screens)."""
    if width < 1 or height < 1:
        raise CaptureTargetError("The region needs a positive width and height.")
    desk = _all_monitors()[0]
    left, top = max(x, desk["left"]), max(y, desk["top"])
    right = min(x + width, desk["left"] + desk["width"])
    bottom = min(y + height, desk["top"] + desk["height"])
    if right <= left or bottom <= top:
        raise CaptureTargetError(
            f"The region is outside the screen area ({desk['left']},{desk['top']} "
            f"{desk['width']}x{desk['height']})."
        )
    region = {"left": left, "top": top, "width": right - left, "height": bottom - top}
    return _grab(region), region


# ----------------------------------------------------------------- windows
if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi")
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.IsHungAppWindow.argtypes = [wintypes.HWND]
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    _GWL_EXSTYLE = -20
    _WS_EX_TOOLWINDOW = 0x00000080
    _DWMWA_EXTENDED_FRAME_BOUNDS = 9
    _DWMWA_CLOAKED = 14
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _SW_RESTORE = 9


def _ensure_dpi_awareness() -> None:
    """Make window coordinates physical pixels (Qt and mss usually did this already)."""
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
        except (AttributeError, OSError):
            pass


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Visible frame bounds (excludes the invisible resize border Windows adds)."""
    rect = wintypes.RECT()
    if _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)):
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def list_windows() -> list[WindowInfo]:
    """Visible top-level windows, front-most first (Windows only; empty elsewhere)."""
    if sys.platform != "win32":
        return []
    _ensure_dpi_awareness()
    found: list[WindowInfo] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return True
        cloaked = wintypes.DWORD()
        _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:  # hidden UWP/background windows
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        minimized = bool(_user32.IsIconic(hwnd))
        x, y, w, h = _window_rect(hwnd)
        if not minimized and (w < 40 or h < 40):
            return True
        found.append(WindowInfo(buf.value, _process_name(hwnd), x, y, w, h, minimized, int(hwnd)))
        return True

    _user32.EnumWindows(_EnumProc(visit), 0)
    return found


def find_window(query: str) -> WindowInfo:
    """Front-most window whose title or process name contains ``query`` (case-insensitive).

    Raises:
        CaptureTargetError: unsupported platform or no match.
    """
    if sys.platform != "win32":
        raise CaptureTargetError("Window capture is only supported on Windows; capture a monitor or region instead.")
    needle = query.strip().lower()
    if not needle:
        raise CaptureTargetError("Give part of the window title or process name.")
    windows = list_windows()
    for win in windows:
        if needle in win.title.lower() or needle in win.process.lower():
            return win
    titles = ", ".join(f"'{w.title}'" for w in windows[:12])
    raise CaptureTargetError(f"No window matches '{query}'. Open windows: {titles or 'none'}.")


def activate_window(win: WindowInfo) -> bool:
    """Restore and bring ``win`` to the front; returns True if it is now the foreground window.

    Never blocks: a window whose application is not responding is refused, and the
    restore request is posted asynchronously.

    Raises:
        CaptureTargetError: the window's application is not responding.
    """
    if sys.platform != "win32":
        return False
    if _user32.IsHungAppWindow(win.handle):
        raise CaptureTargetError(f"The window '{win.title}' ({win.process}) is not responding.")
    if _user32.IsIconic(win.handle):
        _user32.ShowWindowAsync(win.handle, _SW_RESTORE)
    _user32.SetForegroundWindow(win.handle)
    return int(_user32.GetForegroundWindow() or 0) == win.handle


def is_foreground(win: WindowInfo) -> bool:
    return sys.platform == "win32" and int(_user32.GetForegroundWindow() or 0) == win.handle


def is_minimized(win: WindowInfo) -> bool:
    return sys.platform == "win32" and bool(_user32.IsIconic(win.handle))


def capture_window(win: WindowInfo) -> tuple[QImage, dict[str, int]]:
    """Capture the on-screen area of ``win`` (call :func:`activate_window` first)."""
    if sys.platform != "win32":
        raise CaptureTargetError("Window capture is only supported on Windows.")
    if _user32.IsIconic(win.handle):
        raise CaptureTargetError(f"The window '{win.title}' is minimized.")
    x, y, w, h = _window_rect(win.handle)
    return capture_region(x, y, w, h)
