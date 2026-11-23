"""MCP server tests: in-process client for the tools, plus one real stdio round trip."""

import json
import sys
from pathlib import Path

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from conftest import solid_image  # noqa: E402
from screenshot_tool import mcp_server, screens  # noqa: E402
from screenshot_tool.evidence import EvidenceSession  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def call(root, *calls):
    """Run tool calls against a fresh in-process server; returns the results."""

    async def main():
        out = []
        async with Client(mcp_server.build_server(root)) as client:
            for name, args in calls:
                out.append(await client.call_tool(name, args))
        return out

    return anyio.run(main)


def payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_tools_are_listed_with_hints(tmp_path, qapp):
    async def main():
        async with Client(mcp_server.build_server(tmp_path)) as client:
            return (await client.list_tools()).tools, (await client.list_prompts()).prompts

    tools, prompts = anyio.run(main)
    names = {t.name for t in tools}
    assert {"start_evidence_session", "add_evidence_step", "capture_screen", "request_manual_capture",
            "update_evidence_step", "delete_evidence_step", "finish_evidence_session", "generate_evidence_report",
            "get_evidence_session", "list_evidence_sessions", "open_evidence_session",
            "list_capture_targets"} <= names
    by_name = {t.name: t for t in tools}
    assert by_name["get_evidence_session"].annotations.read_only_hint is True
    assert by_name["delete_evidence_step"].annotations.destructive_hint is True
    assert by_name["add_evidence_step"].description.startswith("Record a step")
    status = by_name["add_evidence_step"].input_schema["properties"]["status"]
    assert status["enum"] == ["passed", "failed", "blocked", "skipped", "info"]
    assert [p.name for p in prompts] == ["test_with_evidence"]


def test_full_agent_flow(tmp_path, png_file, qapp):
    results = call(tmp_path,
                   ("start_evidence_session", {"title": "Login", "test_case_id": "TC-1", "environment": "CI"}),
                   ("add_evidence_step", {"title": "Login page", "image_path": str(png_file), "status": "passed",
                                          "url": "https://app.test/login", "include_image": True,
                                          "annotations": [{"type": "rect", "x": 5, "y": 5, "width": 50,
                                                           "height": 20, "label": "Logo"}]}),
                   ("add_evidence_step", {"title": "API 500", "status": "failed", "notes": "POST /login 500"}),
                   ("update_evidence_step", {"step": 2, "actual": "HTTP 500"}),
                   ("finish_evidence_session", {"summary": "Login broken", "formats": ["html", "md"]}),
                   ("get_evidence_session", {}))
    started = payload(results[0])
    added = results[1]
    assert not added.is_error and [c.type for c in added.content] == ["text", "image"]
    step = json.loads(added.content[0].text)["step"]
    assert step["number"] == 1 and Path(step["image"]).is_file()
    assert payload(results[3])["step"]["actual"] == "HTTP 500"
    finished = payload(results[4])
    assert finished["result"] == "failed" and set(finished["reports"]) == {"html", "md"}
    assert all(Path(p).is_file() for p in finished["reports"].values())
    session = payload(results[5])
    assert session["id"] == started["session_id"] and session["status"] == "failed" and session["step_count"] == 2


def test_errors_reach_the_model(tmp_path, qapp):
    results = call(tmp_path,
                   ("add_evidence_step", {"title": "too early"}),
                   ("start_evidence_session", {"title": "S"}),
                   ("add_evidence_step", {"title": "x", "image_path": str(tmp_path / "missing.png")}),
                   ("add_evidence_step", {"title": "x", "status": "great"}),
                   ("delete_evidence_step", {"step": 3}),
                   ("open_evidence_session", {"session": "does-not-exist"}),
                   ("capture_screen", {"title": "x", "monitor": 1, "window": "Chrome"}))
    texts = [r.content[0].text for r in results]
    assert all(r.is_error for i, r in enumerate(results) if i != 1)
    assert "Call start_evidence_session first" in texts[0]
    assert "Image file not found" in texts[2]
    assert "status" in texts[3]
    assert "Step 3 does not exist" in texts[4]
    assert "not found" in texts[5]
    assert "only one of window, monitor or region" in texts[6]


def test_capture_screen_targets(tmp_path, qapp, monkeypatch):
    region = {"left": 0, "top": 0, "width": 64, "height": 48}
    monkeypatch.setattr(screens, "capture_monitor", lambda index=None: (solid_image(64, 48), region))
    monkeypatch.setattr(screens, "capture_region", lambda x, y, w, h: (solid_image(w, h), region))
    win = screens.WindowInfo("Shop – Edge", "msedge.exe", 0, 0, 80, 60, False, 42)
    monkeypatch.setattr(screens, "find_window", lambda q: win)
    monkeypatch.setattr(screens, "activate_window", lambda w: False)
    monkeypatch.setattr(screens, "is_foreground", lambda w: False)
    monkeypatch.setattr(screens, "capture_window", lambda w: (solid_image(80, 60), region))
    results = call(tmp_path,
                   ("start_evidence_session", {"title": "Desktop", "stamp_images": False}),
                   ("capture_screen", {"title": "All", "status": "info"}),
                   ("capture_screen", {"title": "Region", "region": {"x": 1, "y": 2, "width": 30, "height": 20}}),
                   ("capture_screen", {"title": "Window", "window": "edge"}))
    assert payload(results[1])["step"]["width"] == 64
    assert payload(results[2])["step"]["width"] == 30
    window = payload(results[3])
    assert "msedge.exe" in window["captured"] and "could not be brought to the front" in window["warning"]


FAKE_GUI = """\
import sys
from PIL import Image
args = sys.argv[1:]
if "--cancel" in args:
    sys.exit(2)
out = args[args.index("--output") + 1]
assert "--prompt" in args
Image.new("RGB", (40, 30), "red").save(out)
"""


@pytest.mark.parametrize("cancel", [False, True])
def test_request_manual_capture(tmp_path, qapp, monkeypatch, cancel):
    script = tmp_path / "fake_gui.py"
    script.write_text(FAKE_GUI)
    cmd = [sys.executable, str(script)] + (["--cancel"] if cancel else [])
    monkeypatch.setattr(mcp_server, "_gui_command", lambda config_path: list(cmd))
    results = call(tmp_path,
                   ("start_evidence_session", {"title": "Hybrid", "stamp_images": False}),
                   ("request_manual_capture", {"title": "Tester picks the total", "instructions": "Select it",
                                               "timeout_seconds": 60}))
    if cancel:
        assert results[1].is_error and "cancelled" in results[1].content[0].text
    else:
        step = payload(results[1])["step"]
        assert (step["width"], step["height"]) == (40, 30) and step["source"] == "mcp:manual"


def test_sessions_can_be_listed_and_reopened(tmp_path, qapp):
    existing = EvidenceSession.create(tmp_path, "Earlier run", tester="qa")
    results = call(tmp_path,
                   ("list_evidence_sessions", {}),
                   ("open_evidence_session", {"session": existing.id}),
                   ("add_evidence_step", {"title": "Added later"}),
                   ("generate_evidence_report", {"formats": ["md"]}))
    assert [s["id"] for s in payload(results[0])["sessions"]] == [existing.id]
    assert payload(results[2])["step"]["number"] == 1
    assert Path(payload(results[3])["reports"]["md"]).is_file()


def test_stdio_server_round_trip(tmp_path):
    """Spawn ``python -m screenshot_tool mcp`` exactly like an MCP client does."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "screenshot_tool", "mcp", "--evidence-dir", str(tmp_path / "ev"),
              "--config", str(tmp_path / "config.json")],
        cwd=str(PROJECT_ROOT),
    )

    async def main():
        with anyio.fail_after(60):
            async with Client(params) as client:
                tools = (await client.list_tools()).tools
                started = await client.call_tool("start_evidence_session", {"title": "Stdio"})
                step = await client.call_tool("add_evidence_step", {"title": "Text check", "status": "passed"})
                return client.server_info, tools, started, step

    info, tools, started, step = anyio.run(main)
    assert info.name == "qa-evidence" and len(tools) >= 12
    assert Path(payload(started)["folder"]).is_dir()
    assert payload(step)["step"]["status"] == "passed"


def test_relative_image_paths_resolve_like_playwright(tmp_path, qapp, monkeypatch):
    """Playwright MCP prints paths relative to its workspace, e.g. './login.png'."""
    workspace, extra = tmp_path / "workspace", tmp_path / "extra"
    workspace.mkdir()
    extra.mkdir()
    for folder, name in ((workspace, "login.png"), (extra, "other.png")):
        solid_image(30, 20).save(str(folder / name))
    monkeypatch.chdir(workspace)  # MCP clients start servers in the workspace

    async def main():
        server = mcp_server.build_server(tmp_path / "ev", workspaces=[extra])
        async with Client(server) as client:
            await client.call_tool("start_evidence_session", {"title": "Paths", "stamp_images": False})
            return [await client.call_tool("add_evidence_step", args) for args in (
                {"title": "cwd", "image_path": "./login.png", "delete_source_file": True},
                {"title": "workspace option", "image_path": "other.png"},
                {"title": "missing", "image_path": "nope.png"},
            )]

    from_cwd, from_extra, missing = anyio.run(main)
    assert payload(from_cwd)["step"]["width"] == 30
    assert not (workspace / "login.png").exists()  # delete_source_file moved it into the session
    assert payload(from_extra)["step"]["number"] == 2 and (extra / "other.png").exists()
    assert missing.is_error and "looked in" in missing.content[0].text and str(extra) in missing.content[0].text


def test_update_step_accepts_json_looking_text(tmp_path, qapp):
    """The SDK JSON-decodes string arguments unless they are typed plain ``str``."""
    results = call(tmp_path,
                   ("start_evidence_session", {"title": "API"}),
                   ("add_evidence_step", {"title": "Token call", "notes": '{"a": 1}'}),
                   ("update_evidence_step", {"step": 1, "actual": '{"error":"invalid_grant"}', "notes": '["a","b"]'}))
    step = payload(results[2])["step"]
    assert step["actual"] == '{"error":"invalid_grant"}' and step["notes"] == '["a","b"]'


SLOW_GUI = """\
import sys, time
from pathlib import Path
beat = Path(sys.argv[1])
while True:
    beat.write_text(str(time.time()))
    time.sleep(0.05)
"""


def test_cancelled_manual_capture_closes_the_overlay(tmp_path, qapp, monkeypatch):
    script, beat = tmp_path / "slow_gui.py", tmp_path / "heartbeat.txt"
    script.write_text(SLOW_GUI)
    monkeypatch.setattr(mcp_server, "_gui_command", lambda config_path: [sys.executable, str(script), str(beat)])

    async def main():
        async with Client(mcp_server.build_server(tmp_path / "ev")) as client:
            await client.call_tool("start_evidence_session", {"title": "Hybrid"})
            with anyio.move_on_after(3):  # the client gives up waiting for the tester
                await client.call_tool("request_manual_capture", {"title": "Pick", "timeout_seconds": 600})

    anyio.run(main)
    assert beat.exists()
    last = beat.read_text()
    import time

    time.sleep(0.5)
    assert beat.read_text() == last  # the overlay process was killed, not left on screen
