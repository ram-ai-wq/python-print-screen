import json
from pathlib import Path

import pytest

from screenshot_tool import screens
from screenshot_tool.__main__ import main, parse_args
from conftest import solid_image


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out.strip(), out.err


def test_parse_args_gui_and_subcommands():
    args = parse_args(["--once", "--session", "abc", "--config", "c.json"])
    assert args.command is None and args.once and args.session == "abc" and args.config == "c.json"
    # --config before or after the sub-command; the first value is not clobbered
    assert parse_args(["--config", "a.json", "mcp"]).config == "a.json"
    assert parse_args(["mcp", "--config", "b.json", "--evidence-dir", "ev"]).evidence_dir == "ev"
    assert parse_args(["evidence", "list", "--config", "c.json"]).config == "c.json"
    assert parse_args(["--output", "x.png", "--prompt", "Pick it"]).output == "x.png"


def test_evidence_cli_end_to_end(tmp_path, png_file, capsys, qapp):
    base = ["--config", str(tmp_path / "config.json")]
    ev = ["--evidence-dir", str(tmp_path / "ev")]
    code, folder, _ = run(capsys, *base, "evidence", "new", "--title", "Login", "--test-case", "TC-5",
                          "--environment", "CI", *ev, "--print-path")
    assert code == 0 and Path(folder).is_dir()

    code, out, _ = run(capsys, *base, "evidence", "add", folder, "--image", str(png_file), "--title", "Login page",
                       "--status", "pass", "--annotations",
                       '[{"type": "rect", "x": 1, "y": 1, "width": 20, "height": 10}]', *ev)
    step = json.loads(out)["step"]
    assert code == 0 and step["status"] == "passed" and Path(step["image"]).is_file()

    code, out, _ = run(capsys, *base, "evidence", "add", Path(folder).name, "--title", "DB check",
                       "--status", "failed", "--notes", "row missing", *ev)
    assert code == 0 and json.loads(out)["step"]["number"] == 2  # session found by ID

    code, out, _ = run(capsys, *base, "evidence", "finish", folder, "--summary", "Done", "--format", "html,md", *ev)
    result = json.loads(out)
    assert code == 0 and result["result"] == "failed" and set(result["reports"]) == {"html", "md"}

    code, out, _ = run(capsys, *base, "evidence", "list", *ev)
    assert code == 0 and json.loads(out)["sessions"][0]["step_count"] == 2
    code, out, _ = run(capsys, *base, "evidence", "show", folder, *ev)
    assert code == 0 and [s["title"] for s in json.loads(out)["steps"]] == ["Login page", "DB check"]


def test_evidence_cli_errors(tmp_path, capsys, qapp):
    base = ["--config", str(tmp_path / "config.json"), "evidence"]
    ev = ["--evidence-dir", str(tmp_path / "ev")]
    code, _, err = run(capsys, *base, "add", "missing-session", "--title", "x", *ev)
    assert code == 1 and "not found" in err
    code, folder, _ = run(capsys, *base, "new", "--title", "T", *ev, "--print-path")
    code, _, err = run(capsys, *base, "add", folder, "--title", "x", "--annotations", "{bad", *ev)
    assert code == 1 and "not valid JSON" in err
    code, _, err = run(capsys, *base, "add", folder, "--title", "x", "--status", "great", *ev)
    assert code == 1 and "Unknown status" in err
    code, _, err = run(capsys, *base, "capture", folder, "--title", "x", "--region", "1,2", *ev)
    assert code == 1 and "x,y,width,height" in err


def test_evidence_cli_capture(tmp_path, capsys, qapp, monkeypatch):
    monkeypatch.setattr(screens, "capture_region", lambda x, y, w, h: (solid_image(w, h), {}))
    base = ["--config", str(tmp_path / "config.json"), "evidence"]
    ev = ["--evidence-dir", str(tmp_path / "ev")]
    _, folder, _ = run(capsys, *base, "new", "--title", "Desktop", "--no-stamp", *ev, "--print-path")
    code, out, _ = run(capsys, *base, "capture", folder, "--title", "Region", "--region", "0,0,50,40", *ev)
    assert code == 0 and json.loads(out)["step"]["width"] == 50


@pytest.mark.parametrize("argv", [["evidence"], ["evidence", "add"]])
def test_evidence_cli_usage_errors(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


def test_list_limit_must_be_positive(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["evidence", "list", "--limit", "-1"])
    assert exc.value.code == 2 and "must be 1 or more" in capsys.readouterr().err
