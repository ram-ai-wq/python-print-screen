import json

import pytest

from screenshot_tool.config import load_config
from screenshot_tool.hotkey import to_pynput_hotkey


def test_config_created_with_defaults(tmp_path):
    path = tmp_path / "sub" / "config.json"
    cfg = load_config(path)
    assert cfg.hotkey == "ctrl+shift+s" and cfg.thickness == 4
    assert json.loads(path.read_text())["default_color"] == "#ff3b30"


def test_config_valid_and_invalid_values(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"hotkey": "Ctrl+Alt+P", "default_color": "red", "thickness": 99,
                                "save_folder": "/x"}))
    cfg = load_config(path)
    assert cfg.hotkey == "ctrl+alt+p"
    assert cfg.default_color == "#ff3b30"  # invalid -> default
    assert cfg.thickness == 4              # out of range -> default
    assert cfg.save_folder == "/x"


def test_config_evidence_settings(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"evidence_folder": str(tmp_path / "ev"), "tester": "ana",
                                "report_formats": ["PDF", "html", "pdf"], "stamp_images": False,
                                "ask_step_details": "yes"}))
    cfg = load_config(path)
    assert cfg.evidence_root() == tmp_path / "ev" and cfg.tester == "ana"
    assert cfg.report_formats == ["pdf", "html"]  # normalised and de-duplicated
    assert cfg.stamp_images is False
    assert cfg.ask_step_details is True  # invalid -> default
    path.write_text(json.dumps({"report_formats": ["docx"]}))
    assert load_config(path).report_formats == ["html", "pdf", "md"]


def test_config_corrupt_file_falls_back(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    assert load_config(path).hotkey == "ctrl+shift+s"
    assert path.read_text() == "{not json"  # user's file is not overwritten


@pytest.mark.parametrize("text,expected", [
    ("ctrl+shift+s", "<ctrl>+<shift>+s"),
    ("Control + Alt + F9", "<ctrl>+<alt>+<f9>"),
    ("win+print_screen", "<cmd>+<print_screen>"),
])
def test_hotkey_conversion(text, expected):
    assert to_pynput_hotkey(text) == expected


def test_hotkey_empty_rejected():
    with pytest.raises(ValueError):
        to_pynput_hotkey(" + ")
