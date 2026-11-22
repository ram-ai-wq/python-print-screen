from datetime import datetime

import pytest
from PIL import Image
from PyQt6.QtCore import QPointF, QRect
from PyQt6.QtGui import QColor, QImage, qRgb

from screenshot_tool.storage import (
    StorageError,
    compose_image,
    default_filename,
    qimage_to_pil,
    save_image,
)
from screenshot_tool.tools import ArrowAnnotation, History, RectAnnotation


def gradient(w: int, h: int) -> QImage:
    """Image where pixel (x, y) == (x % 256, y % 256, 7): lets us verify exact crops."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    for y in range(h):
        for x in range(w):
            img.setPixel(x, y, qRgb(x % 256, y % 256, 7))
    return img


def test_default_filename_format():
    assert default_filename(datetime(2024, 3, 5, 14, 7, 9)) == "screenshot_2024-03-05_14-07-09.png"


def test_crop_is_pixel_accurate(qapp):
    src = gradient(300, 200)
    out = compose_image(src, QRect(40, 30, 120, 60), [], 1.0, 1.0)
    assert (out.width(), out.height()) == (120, 60)
    for (x, y) in [(0, 0), (119, 59), (50, 20)]:
        assert out.pixel(x, y) == src.pixel(40 + x, 30 + y)


def test_crop_with_hidpi_scale(qapp):
    src = gradient(400, 300)  # physical; logical is 200x150 at 2x
    out = compose_image(src, QRect(10, 5, 50, 20), [], 2.0, 2.0)
    assert (out.width(), out.height()) == (100, 40)
    assert out.pixel(0, 0) == src.pixel(20, 10)


def test_arrow_is_baked_into_output(qapp):
    src = QImage(200, 100, QImage.Format.Format_RGB32)
    src.fill(QColor("white"))
    arrow = ArrowAnnotation(QPointF(20, 35), QColor("#ff0000"), 6)
    arrow.update_drag(QPointF(100, 35))
    out = compose_image(src, QRect(10, 10, 100, 50), [arrow], 1.0, 1.0)
    assert out.pixelColor(50, 25) == QColor("#ff0000")   # on the shaft (sel-relative)
    assert out.pixelColor(2, 2) == QColor("white")        # untouched corner


def test_annotations_clipped_to_selection(qapp):
    src = QImage(200, 100, QImage.Format.Format_RGB32)
    src.fill(QColor("white"))
    rect = RectAnnotation(QPointF(0, 0), QColor("#00ff00"), 40)  # huge stroke spills outside
    rect.update_drag(QPointF(150, 80))
    out = compose_image(src, QRect(50, 20, 40, 30), [rect], 1.0, 1.0)
    assert (out.width(), out.height()) == (40, 30)  # output size never changes


def test_save_png_and_jpg_roundtrip(qapp, tmp_path):
    src = gradient(64, 48)
    png = save_image(src, tmp_path / "a.png")
    with Image.open(png) as im:
        assert im.size == (64, 48)
        assert im.convert("RGB").getpixel((10, 5)) == (10, 5, 7)  # lossless
    jpg = save_image(src, tmp_path / "b.jpg")
    with Image.open(jpg) as im:
        assert im.format == "JPEG" and im.size == (64, 48)


def test_save_adds_png_suffix_when_missing(qapp, tmp_path):
    saved = save_image(gradient(8, 8), tmp_path / "noext")
    assert saved.name == "noext.png" and saved.exists()


def test_save_errors_are_friendly(qapp, tmp_path):
    img = gradient(8, 8)
    with pytest.raises(StorageError, match="does not exist"):
        save_image(img, tmp_path / "missing" / "a.png")
    with pytest.raises(StorageError, match="Unsupported"):
        save_image(img, tmp_path / "a.bmp")
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    with pytest.raises(StorageError):
        save_image(img, blocker / "a.png")  # parent is a file, not a folder


def test_qimage_to_pil_size(qapp):
    assert qimage_to_pil(gradient(13, 7)).size == (13, 7)


def test_history_undo_removes_only_last():
    h = History()
    a, b = ArrowAnnotation(QPointF(0, 0), QColor("red"), 3), ArrowAnnotation(QPointF(1, 1), QColor("red"), 3)
    h.add(a)
    h.add(b)
    assert h.undo() and h.items == [a]
    assert h.redo() and h.items == [a, b]
    h.undo()
    h.add(ArrowAnnotation(QPointF(2, 2), QColor("red"), 3))
    assert not h.can_redo  # new action clears redo
