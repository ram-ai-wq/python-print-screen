import hashlib
import io
import json

import pytest
from PIL import Image

from conftest import solid_image
from screenshot_tool.evidence import (
    MANIFEST_NAME,
    EvidenceError,
    EvidenceSession,
    list_sessions,
    normalize_status,
    slugify,
)


def new_session(tmp_path, **kw):
    kw.setdefault("tester", "qa")
    return EvidenceSession.create(tmp_path, kw.pop("title", "Login flow"), **kw)


@pytest.mark.parametrize("raw,expected", [
    ("passed", "passed"), ("PASS", "passed"), ("ok", "passed"), ("Failed", "failed"), ("error", "failed"),
    ("blocked", "blocked"), ("n/a", "skipped"), ("not run", "skipped"), ("", "info"), (None, "info"),
])
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


def test_normalize_status_rejects_unknown():
    with pytest.raises(EvidenceError, match="Unknown status"):
        normalize_status("great")


def test_slugify_is_filename_safe():
    assert slugify("Ação inválida: ../../etc/passwd!") == "acao-invalida-etc-passwd"
    assert slugify("???") == "step"
    assert len(slugify("x" * 200)) == 40


def test_create_writes_manifest_and_folder_name(tmp_path, qapp):
    s = new_session(tmp_path, test_case_id="TC-101", environment="Staging")
    assert s.path.parent == tmp_path and s.path.name.endswith("_tc-101_login-flow")
    data = json.loads((s.path / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert data["schema"] == "screenshot-tool/evidence-session"
    assert data["test_case_id"] == "TC-101" and data["environment"] == "Staging" and data["steps"] == []
    assert s.label == "TC-101 · Login flow"


def test_create_requires_title_or_test_case(tmp_path):
    with pytest.raises(EvidenceError):
        EvidenceSession.create(tmp_path, "  ")
    assert EvidenceSession.create(tmp_path, "", test_case_id="TC-9").title == "TC-9"


def test_two_sessions_same_second_get_distinct_folders(tmp_path, qapp):
    a, b = new_session(tmp_path), new_session(tmp_path)
    assert a.path != b.path and a.path.is_dir() and b.path.is_dir()


def test_add_steps_from_every_source(tmp_path, png_file, qapp):
    s = new_session(tmp_path, stamp_images=False)
    pil = Image.new("RGB", (50, 40), "blue")
    buf = io.BytesIO()
    pil.save(buf, "JPEG")
    steps = [
        s.add_step(png_file, title="From path", status="passed"),
        s.add_step(png_file.read_bytes(), title="From bytes"),
        s.add_step(solid_image(30, 20), title="From QImage"),
        s.add_step(pil, title="From Pillow"),
        s.add_step(buf.getvalue(), title="From JPEG bytes"),
        s.add_step(None, title="Text only", notes="POST /api 201"),
    ]
    assert [st.number for st in steps] == [1, 2, 3, 4, 5, 6]
    assert steps[0].image == "001_from-path.png" and (steps[0].width, steps[0].height) == (320, 200)
    assert steps[3].width == 50 and steps[5].image is None
    for st in steps[:5]:
        data = (s.path / st.image).read_bytes()
        assert hashlib.sha256(data).hexdigest() == st.sha256
    reopened = EvidenceSession.open(s.path)
    assert [st.title for st in reopened.steps] == [st.title for st in steps]
    assert reopened.steps[0].status == "passed" and reopened.steps[1].status == "info"


def test_add_step_errors_are_friendly(tmp_path, qapp):
    s = new_session(tmp_path)
    with pytest.raises(EvidenceError, match="not found"):
        s.add_step(tmp_path / "missing.png", title="x")
    with pytest.raises(EvidenceError, match="Could not read"):
        s.add_step(b"definitely not an image", title="x")
    with pytest.raises(EvidenceError, match="need an image"):
        s.add_step(None, title="x", annotations=[{"type": "rect", "x": 1, "y": 1, "width": 5, "height": 5}])
    assert s.steps == []


def test_stamp_footer_and_annotations(tmp_path, png_file, qapp):
    s = new_session(tmp_path, test_case_id="TC-1")
    step = s.add_step(png_file, title="Stamped", url="https://example.test/login",
                      annotations=[{"type": "redact", "x": 10, "y": 10, "width": 60, "height": 30}])
    assert step.width == 320 and step.height > 200  # footer appended below, width unchanged
    with Image.open(s.path / step.image) as im:
        assert im.getpixel((30, 20))[:3] == (17, 17, 17)        # redaction is solid
        assert im.getpixel((300, 150))[:3] == (255, 255, 255)   # screenshot untouched
        assert im.getpixel((2, step.height - 2))[:3] == (28, 28, 30)  # footer bar
    unstamped = s.add_step(png_file, title="No stamp", stamp=False)
    assert unstamped.height == 200


def test_update_retitles_file_and_validates(tmp_path, png_file, qapp):
    s = new_session(tmp_path, stamp_images=False)
    s.add_step(png_file, title="Old title")
    step = s.update_step(1, title="New title", status="fail", actual="Boom", url=None)
    assert step.status == "failed" and step.actual == "Boom"
    assert step.image == "001_new-title.png" and (s.path / step.image).is_file()
    assert not (s.path / "001_old-title.png").exists()
    with pytest.raises(EvidenceError, match="does not exist"):
        s.update_step(5, title="x")
    with pytest.raises(EvidenceError, match="Cannot change"):
        s.update_step(1, sha256="forged")


def test_delete_and_move_renumber_files(tmp_path, png_file, qapp):
    s = new_session(tmp_path, stamp_images=False)
    for title in ("one", "two", "three"):
        s.add_step(png_file, title=title)
    removed = s.delete_step(1)
    assert removed.title == "one" and not (s.path / "001_one.png").exists()
    assert [(st.number, st.image) for st in s.steps] == [(1, "001_two.png"), (2, "002_three.png")]
    s.move_step(2, 1)  # swap: names must not collide
    assert [(st.number, st.title, st.image) for st in s.steps] == [(1, "three", "001_three.png"),
                                                                    (2, "two", "002_two.png")]
    assert sorted(p.name for p in s.path.glob("*.png")) == ["001_three.png", "002_two.png"]
    assert EvidenceSession.open(s.path).steps[0].title == "three"


def test_finish_computes_result(tmp_path, qapp):
    s = new_session(tmp_path)
    assert s.computed_result() == "info"
    s.add_step(None, title="a", status="passed")
    assert s.finish() == "passed" and s.is_finished
    s.add_step(None, title="b", status="blocked")
    assert s.computed_result() == "blocked"
    s.add_step(None, title="c", status="failed")
    assert s.finish(summary="Bug found") == "failed"
    assert s.finish("passed") == "passed"  # explicit verdict wins
    assert EvidenceSession.open(s.path).summary == "Bug found"
    assert s.counts() == {"passed": 1, "failed": 1, "blocked": 1, "skipped": 0, "info": 0, "total": 3}


def test_changes_from_another_process_are_not_lost(tmp_path, qapp):
    a = new_session(tmp_path)
    b = EvidenceSession.open(a.path)  # e.g. the tray app and the MCP server
    a.add_step(None, title="from A")
    b.add_step(None, title="from B")  # b re-reads the manifest first
    assert [st.title for st in EvidenceSession.open(a.path).steps] == ["from A", "from B"]


def test_open_by_id_and_errors(tmp_path, qapp):
    s = new_session(tmp_path)
    assert EvidenceSession.open(s.id, root=tmp_path).path == s.path
    assert EvidenceSession.open(s.path / MANIFEST_NAME).id == s.id
    with pytest.raises(EvidenceError, match="not found"):
        EvidenceSession.open("nope", root=tmp_path)
    with pytest.raises(EvidenceError, match="not found"):
        EvidenceSession.open("../escape", root=tmp_path)
    bogus = tmp_path / "bogus"
    bogus.mkdir()
    (bogus / MANIFEST_NAME).write_text('{"hello": 1}')
    with pytest.raises(EvidenceError, match="not an evidence session"):
        EvidenceSession.open(bogus)


def test_list_sessions_newest_first_and_skips_broken(tmp_path, qapp):
    first = new_session(tmp_path, title="First")
    second = new_session(tmp_path, title="Second")
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / MANIFEST_NAME).write_text("{not json")
    ids = [s.id for s in list_sessions(tmp_path)]
    assert set(ids) == {first.id, second.id}
    assert list_sessions(tmp_path / "missing") == []
    assert len(list_sessions(tmp_path, limit=1)) == 1


def test_to_dict_has_absolute_paths(tmp_path, png_file, qapp):
    s = new_session(tmp_path, stamp_images=False)
    s.add_step(png_file, title="Shot", status="passed")
    data = s.to_dict()
    assert data["status"] == "in_progress" and data["counts"] == {"passed": 1}
    assert data["steps"][0]["image"] == str(s.path / "001_shot.png")
    assert "expected" not in data["steps"][0]  # empty fields are omitted


# ------------------------------------------------------------ review regressions
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402


def test_steps_have_stable_ids_and_can_be_addressed_by_id(tmp_path, qapp):
    s = new_session(tmp_path)
    a, b, c = (s.add_step(None, title=t) for t in "ABC")
    assert len({a.id, b.id, c.id}) == 3
    stale_view = EvidenceSession.open(s.path)   # e.g. the tray window showing A, B, C
    s.delete_step(1)                             # meanwhile the agent deletes "A" by number
    removed = stale_view.delete_step(b.id)       # the tester deletes "B" from the stale list
    assert removed.title == "B"
    assert [st.title for st in EvidenceSession.open(s.path).steps] == ["C"]
    with pytest.raises(EvidenceError, match="no longer exists"):
        stale_view.update_step(b.id, notes="x")
    assert EvidenceSession.open(s.path).steps[0].id == c.id  # ids survive renumbering and reloads


ADDER = """
import sys
from PyQt6.QtGui import QGuiApplication, QImage, QColor
app = QGuiApplication(["adder", "-platform", "offscreen"])
from screenshot_tool.evidence import EvidenceSession
session = EvidenceSession.open(sys.argv[1])
for i in range(6):
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(QColor(i * 40, 90, 200))
    session.add_step(img, title=f"{sys.argv[2]}-{i}")
"""


def test_concurrent_processes_do_not_lose_steps(tmp_path, qapp):
    s = new_session(tmp_path)
    script = tmp_path / "adder.py"
    script.write_text(ADDER)
    env = {**__import__("os").environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}
    procs = [subprocess.Popen([sys.executable, str(script), str(s.path), name], env=env) for name in ("tray", "agent")]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    steps = EvidenceSession.open(s.path).steps
    assert len(steps) == 12 and [st.number for st in steps] == list(range(1, 13))
    assert len({st.image for st in steps}) == 12 and all((s.path / st.image).is_file() for st in steps)
    assert sorted(p.name for p in s.path.glob("*.png")) == sorted(st.image for st in steps)  # no orphans


def test_manifest_cannot_point_outside_the_session(tmp_path, qapp):
    s = new_session(tmp_path)
    s.add_step(None, title="ok")
    outside = tmp_path / "thesis.docx"
    outside.write_text("precious")
    manifest = s.path / MANIFEST_NAME
    for bad in (str(outside), "../thesis.docx", "..\thesis.docx", "sub/001.png"):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["steps"][0]["image"] = bad
        manifest.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(EvidenceError, match="invalid image file name"):
            EvidenceSession.open(s.path)
    assert outside.read_text() == "precious"


def test_malformed_manifest_values_are_sanitised(tmp_path, qapp):
    s = new_session(tmp_path)
    manifest = s.path / MANIFEST_NAME
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["steps"] = [
        {"title": "no number", "sha256": "<img src=x onerror=alert(1)>", "width": "1920", "status": "weird"},
        {"number": "2", "title": 42, "height": -5},
        "not a step",
    ]
    data["stamp_images"] = "no"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    opened = EvidenceSession.open(s.path)
    first, second = opened.steps
    assert (first.number, first.sha256, first.width, first.status) == (1, None, None, "info")
    assert (second.number, second.title, second.height) == (2, "42", None)
    assert [x.id for x in list_sessions(tmp_path)] == [s.id]  # listing still works


def test_list_sessions_ignores_non_positive_limits(tmp_path, qapp):
    for title in ("a", "b"):
        new_session(tmp_path, title=title)
    assert len(list_sessions(tmp_path, limit=-1)) == 2 and len(list_sessions(tmp_path, limit=0)) == 2
