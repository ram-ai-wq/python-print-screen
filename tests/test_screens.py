import sys

import pytest

from screenshot_tool import screens


def test_region_needs_a_positive_size():
    with pytest.raises(screens.CaptureTargetError, match="positive"):
        screens.capture_region(0, 0, 0, 10)


def test_window_capture_is_explained_off_windows(monkeypatch):
    monkeypatch.setattr(screens.sys, "platform", "linux")
    assert screens.list_windows() == []
    with pytest.raises(screens.CaptureTargetError, match="only supported on Windows"):
        screens.find_window("anything")


@pytest.mark.skipif(sys.platform != "win32", reason="window enumeration is Windows-only")
def test_windows_are_listed_and_unknown_titles_are_reported():
    for win in screens.list_windows():
        assert win.title and "handle" not in win.to_dict()
    with pytest.raises(screens.CaptureTargetError, match="No window matches"):
        screens.find_window("zzz-no-such-window-4b1c")
    with pytest.raises(screens.CaptureTargetError, match="title or process"):
        screens.find_window("   ")


@pytest.mark.skipif(sys.platform != "win32", reason="window activation is Windows-only")
def test_hung_windows_are_refused_without_blocking(monkeypatch):
    class FakeUser32:
        def IsHungAppWindow(self, hwnd):  # noqa: N802 - Win32 name
            return 1

    monkeypatch.setattr(screens, "_user32", FakeUser32())
    win = screens.WindowInfo("Frozen app", "frozen.exe", 0, 0, 100, 100, True, 1234)
    with pytest.raises(screens.CaptureTargetError, match="not responding"):
        screens.activate_window(win)
