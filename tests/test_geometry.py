import math

from PyQt6.QtCore import QPoint, QRect

from screenshot_tool.capture import logical_to_pixel_rect, normalize_rect
from screenshot_tool.overlay import Handle, move_rect, resize_rect
from screenshot_tool.tools import arrow_geometry


def test_normalize_rect_any_direction():
    expected = QRect(10, 20, 30, 40)
    assert normalize_rect(QPoint(10, 20), QPoint(40, 60)) == expected
    assert normalize_rect(QPoint(40, 60), QPoint(10, 20)) == expected
    assert normalize_rect(QPoint(40, 20), QPoint(10, 60)) == expected


def test_logical_to_pixel_identity_and_hidpi():
    r = QRect(10, 10, 100, 50)
    assert logical_to_pixel_rect(r, 1, 1, 500, 500) == r
    assert logical_to_pixel_rect(r, 2, 2, 500, 500) == QRect(20, 20, 200, 100)


def test_logical_to_pixel_fractional_scale_rounds_edges_and_clamps():
    out = logical_to_pixel_rect(QRect(1, 1, 3, 3), 1.5, 1.5, 100, 100)
    assert out == QRect(2, 2, 4, 4)  # edges 1.5->2 and 6.0
    assert logical_to_pixel_rect(QRect(90, 90, 50, 50), 1, 1, 100, 100) == QRect(90, 90, 10, 10)


def test_resize_rect_handles():
    r = QRect(100, 100, 100, 50)
    assert resize_rect(r, Handle.BR, QPoint(250, 200)) == QRect(100, 100, 150, 100)
    assert resize_rect(r, Handle.TL, QPoint(80, 90)) == QRect(80, 90, 120, 60)
    assert resize_rect(r, Handle.R, QPoint(300, 999)) == QRect(100, 100, 200, 50)  # y ignored
    assert resize_rect(r, Handle.T, QPoint(0, 120)) == QRect(100, 120, 100, 30)


def test_resize_rect_flips_when_crossing():
    r = QRect(100, 100, 100, 50)
    assert resize_rect(r, Handle.L, QPoint(250, 0)) == QRect(200, 100, 50, 50)


def test_move_rect_is_clamped():
    bounds = QRect(0, 0, 500, 400)
    r = QRect(100, 100, 100, 50)
    assert move_rect(r, 10, -20, bounds) == QRect(110, 80, 100, 50)
    assert move_rect(r, -500, -500, bounds) == QRect(0, 0, 100, 50)
    assert move_rect(r, 900, 900, bounds) == QRect(400, 350, 100, 50)


def test_arrow_geometry_basic():
    shaft_end, (tip, left, right) = arrow_geometry((0, 0), (100, 0), 4)
    assert tip == (100, 0)
    assert 0 < shaft_end[0] < 100 and shaft_end[1] == 0
    # head is symmetric about the shaft and sits behind the tip
    assert math.isclose(left[0], right[0]) and math.isclose(left[1], -right[1])
    assert left[0] < 100


def test_arrow_head_scales_with_width():
    def head_size(w):
        _, (tip, left, _r) = arrow_geometry((0, 0), (300, 0), w)
        return tip[0] - left[0]

    assert head_size(2) == 12  # minimum head length
    assert head_size(10) == 40
    assert head_size(10) > head_size(4)


def test_arrow_short_and_zero_length():
    _, head = arrow_geometry((5, 5), (5, 5), 4)
    assert head == []
    shaft_end, (tip, left, _r) = arrow_geometry((0, 0), (6, 0), 10)
    assert tip[0] - left[0] <= 6  # head never longer than the arrow itself
    assert shaft_end[0] >= 0


def test_arrow_geometry_diagonal_direction():
    _, (tip, left, right) = arrow_geometry((0, 0), (50, 50), 4)
    mid = ((left[0] + right[0]) / 2, (left[1] + right[1]) / 2)
    # base centre lies on the line y=x behind the tip
    assert math.isclose(mid[0], mid[1])
    assert mid[0] < tip[0]
