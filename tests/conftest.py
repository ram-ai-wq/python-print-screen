import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # Qt's offscreen plugin finds no fonts on Windows otherwise
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def solid_image(w: int = 320, h: int = 200, color: str = "white") -> QImage:
    """A plain test 'screenshot'."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    return img


@pytest.fixture
def png_file(tmp_path, qapp) -> Path:
    """A 320x200 white PNG on disk (like a Playwright screenshot)."""
    path = tmp_path / "shot.png"
    assert solid_image().save(str(path))
    return path
