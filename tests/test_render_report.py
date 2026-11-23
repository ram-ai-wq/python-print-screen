import base64

import pytest
from PIL import Image
from PyQt6.QtGui import QColor, QImage

from conftest import solid_image
from screenshot_tool.evidence import EvidenceError, EvidenceSession
from screenshot_tool.render import (
    ImageError,
    apply_annotations,
    build_annotations,
    encode_png,
    load_image,
    preview_png,
    stamp_footer,
)
from screenshot_tool.report import build_html, build_markdown, normalize_formats, write_pdf


# ------------------------------------------------------------------ render
def test_annotations_are_painted(qapp):
    img = apply_annotations(solid_image(200, 120), [
        {"type": "rect", "x": 10, "y": 10, "width": 80, "height": 40, "color": "#00ff00", "thickness": 4},
        {"type": "redact", "x": 120, "y": 10, "width": 60, "height": 40},
        {"type": "highlight", "x": 10, "y": 70, "width": 60, "height": 30},
        {"type": "arrow", "x": 100, "y": 110, "to_x": 190, "to_y": 110},
        {"type": "step", "x": 150, "y": 85},
        {"type": "text", "x": 5, "y": 100, "text": "Ação ✓"},
    ])
    assert img.pixelColor(10, 30) == QColor("#00ff00")          # rect outline
    assert img.pixelColor(50, 30) == QColor("white")            # rect is not filled
    assert img.pixelColor(150, 30) == QColor("#111111")         # redaction is opaque
    hl = img.pixelColor(40, 85)
    assert hl != QColor("white") and hl.red() > 200 and hl.blue() < 200  # translucent yellow


def test_step_badges_number_themselves():
    items = build_annotations([{"type": "step", "x": 1, "y": 1}, {"type": "step", "x": 2, "y": 2},
                               {"type": "step", "x": 3, "y": 3, "number": 7}, {"type": "step", "x": 4, "y": 4}])
    assert [i.number for i in items] == [1, 2, 7, 4]


@pytest.mark.parametrize("spec,message", [
    ({"type": "circle", "x": 1, "y": 1}, "Unknown annotation type"),
    ({"type": "rect", "x": 1, "y": 1}, "needs 'width'"),
    ({"type": "rect", "x": 1, "y": 1, "width": 0, "height": 5}, "non-zero"),
    ({"type": "arrow", "x": 1, "y": 1, "to_x": "a", "to_y": 2}, "must be a number"),
    ({"type": "text", "x": 1, "y": 1}, "non-empty 'text'"),
    ({"type": "rect", "x": 1, "y": 1, "width": 5, "height": 5, "color": "red"}, "#rrggbb"),
    ({"type": "step", "x": 1, "y": 1, "number": -1}, "integer 'number'"),
])
def test_invalid_annotations_are_rejected(spec, message):
    with pytest.raises(ImageError, match=message):
        build_annotations([spec])


def test_load_image_errors(tmp_path):
    with pytest.raises(ImageError, match="not found"):
        load_image(tmp_path / "nope.png")
    with pytest.raises(ImageError, match="Could not read"):
        load_image(b"\x89PNG broken")
    with pytest.raises(ImageError, match="Unsupported"):
        load_image(12345)


def test_encode_png_opaque_is_rgb_and_preview_downscales(qapp):
    png = encode_png(solid_image(40, 30))
    with Image.open(__import__("io").BytesIO(png)) as im:
        assert im.mode == "RGB" and im.size == (40, 30)
    small = QImage.fromData(preview_png(solid_image(2000, 1000), max_side=500))
    assert (small.width(), small.height()) == (500, 250)


def test_stamp_footer_keeps_screenshot(qapp):
    src = solid_image(300, 100, "#336699")
    out = stamp_footer(src, "TC-1 · Login", "2026-01-01 10:00:00 +00:00", "https://x.test · qa")
    assert out.width() == 300 and out.height() > 100
    assert out.pixelColor(150, 50) == QColor("#336699")
    assert out.pixelColor(1, out.height() - 1) == QColor("#1c1c1e")


# ------------------------------------------------------------------ reports
@pytest.fixture
def session(tmp_path, png_file, qapp):
    s = EvidenceSession.create(tmp_path, "Checkout <script>", test_case_id="TC-7", tester="qa",
                               environment="Staging", description="Line 1\nLine 2")
    s.add_step(png_file, title="Cart page", status="passed", expected="Cart shown", actual="Cart shown",
               url="https://shop.test/cart")
    s.add_step(None, title="API | check", status="failed", notes="500 | error")
    s.finish(summary="Checkout broken")
    return s


def test_html_report_is_self_contained_and_escaped(session):
    html = build_html(session)
    assert "data:image/png;base64," in html and "001_cart-page.png" in html
    assert "<h1>Checkout &lt;script&gt;</h1>" in html and "Checkout <script>" not in html
    assert ">Failed<" in html and ">Passed<" in html
    assert session.steps[0].sha256 in html
    assert "modified since capture" not in html
    linked = build_html(session, embed_images=False)
    assert 'src="001_cart-page.png"' in linked and "base64" not in linked


def test_reports_flag_modified_or_missing_images(session):
    image = session.path / session.steps[0].image
    image.write_bytes(image.read_bytes() + b"tampered")
    assert "modified since capture" in build_html(session)
    assert "file modified" in build_markdown(session)
    image.unlink()
    assert "Image file missing" in build_html(session)


def test_markdown_report(session):
    md = build_markdown(session)
    assert md.startswith("# Test evidence: TC-7 · Checkout <script>")
    assert "**Result:** ❌ FAILED" in md
    assert "![Step 1: Cart page](001_cart-page.png)" in md
    assert "| 2 | API \\| check | ❌ Failed |" in md  # pipes escaped in tables


def test_pdf_report(session, tmp_path):
    path = write_pdf(session, tmp_path / "out.pdf")
    data = path.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 2000
    pdf = pytest.importorskip("PyQt6.QtPdf")
    doc = pdf.QPdfDocument(None)
    doc.load(str(path))
    assert doc.pageCount() >= 2  # summary page + one page per screenshot


def test_export_writes_requested_formats(session):
    out = session.export(["md", "html"])
    assert set(out) == {"md", "html"} and all(p.is_file() for p in out.values())


def test_normalize_formats():
    assert normalize_formats("HTML, pdf ,markdown") == ["html", "pdf", "md"]
    assert normalize_formats(None) == ["html", "pdf", "md"]
    with pytest.raises(EvidenceError):
        normalize_formats(["docx"])
    with pytest.raises(EvidenceError):
        normalize_formats(" , ")


def test_html_embeds_exact_file_bytes(session):
    data = (session.path / session.steps[0].image).read_bytes()
    assert base64.b64encode(data).decode() in build_html(session)


# ------------------------------------------------------------ review regressions
import sys  # noqa: E402

from PyQt6.QtGui import QImageReader  # noqa: E402

from screenshot_tool.report import build_pdf_document  # noqa: E402


def test_pdf_splits_tall_screenshots_into_pages(tmp_path, qapp):
    s = EvidenceSession.create(tmp_path, "Full page", tester="qa", stamp_images=False)
    s.add_step(solid_image(800, 6000, "#dfe7ef"), title="Whole page")
    path = write_pdf(s, tmp_path / "tall.pdf")
    pdf = pytest.importorskip("PyQt6.QtPdf")
    doc = pdf.QPdfDocument(None)
    doc.load(str(path))
    assert doc.pageCount() >= 5  # summary page + several slices instead of one unreadable strip
    assert "part 2 of" in build_pdf_document(s).toPlainText()


def test_pdf_decodes_images_beyond_qt_reader_limit(tmp_path, qapp):
    s = EvidenceSession.create(tmp_path, "Big", tester="qa", stamp_images=False)
    s.add_step(solid_image(1400, 900), title="Large")
    old = QImageReader.allocationLimit()
    QImageReader.setAllocationLimit(1)  # 1 MB: QImage.fromData would refuse this image
    try:
        text = build_pdf_document(s).toPlainText()
    finally:
        QImageReader.setAllocationLimit(old)
    assert "missing or unreadable" not in text


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows locks files that are open")
def test_pdf_open_in_a_viewer(session, tmp_path):
    import ctypes
    from ctypes import wintypes

    target = session.path / "report.pdf"
    write_pdf(session, target)
    old = target.read_bytes()
    # A viewer that shares the file for writing: the report is updated in place.
    with open(target, "rb"):
        target.write_bytes(b"stale")
        write_pdf(session, target)
    assert target.read_bytes().startswith(b"%PDF")
    # A viewer that locks the file (no write/delete sharing): a clear error, never a stale success.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(target), 0x80000000, 0x1, None, 3, 0x80, None)  # GENERIC_READ, SHARE_READ
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        with pytest.raises(EvidenceError, match="close it"):
            write_pdf(session, target)
    finally:
        kernel32.CloseHandle(handle)
    assert target.read_bytes().startswith(b"%PDF") and old  # the previous report is intact
    assert not list(session.path.glob(".report.pdf.*.tmp"))  # no temp files left behind
