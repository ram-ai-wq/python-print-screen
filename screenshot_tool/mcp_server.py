"""MCP (Model Context Protocol) server: lets AI agents record QA evidence.

Works with any MCP client (Claude Code, Claude Desktop, VS Code, Cursor...) and is
designed to sit next to browser automation such as Playwright MCP: the agent drives
the app, then records each verified step here (a Playwright screenshot file, a
screen/window capture, or a capture made by the human tester) and finally writes
an HTML/PDF/Markdown evidence report.

Run (stdio transport)::

    python -m screenshot_tool mcp [--evidence-dir DIR]

Nothing but MCP messages may be written to stdout; logs go to stderr.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import anyio
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__, screens
from .config import Config, load_config
from .evidence import EvidenceError, EvidenceSession, EvidenceStep, list_sessions
from .qt_runtime import ensure_gui_app
from .render import ImageError, load_image, preview_png
from .report import normalize_formats

log = logging.getLogger(__name__)

SERVER_NAME = "qa-evidence"
Status = Literal["passed", "failed", "blocked", "skipped", "info"]
ReportFormat = Literal["html", "pdf", "md"]

INSTRUCTIONS = """\
Records QA test evidence: numbered screenshots with a title, status (passed/failed/blocked/\
skipped/info), expected and actual results, saved in a session folder with an HTML/PDF/Markdown report.

Workflow:
1. start_evidence_session once per test case (title, test_case_id, environment, build).
2. After performing and checking each test step, record it:
   - Web apps driven by Playwright MCP: call browser_take_screenshot (it saves a PNG and prints \
its path), then add_evidence_step with image_path set to that file.
   - Desktop apps or anything on screen: capture_screen (whole desktop, a monitor, a window title \
or a region). Use list_capture_targets to see monitors and windows.
   - When a human must pick or confirm the area: request_manual_capture (opens the capture \
overlay on the tester's screen and waits).
   - Text-only evidence (API response, log line, DB value): add_evidence_step without image_path.
3. finish_evidence_session with the overall result and a summary; tell the user the report paths.

Use status "failed" whenever the actual result differs from the expected result, and describe \
both. Annotations (rect, highlight, arrow, text, step badge, redact) use pixel coordinates of the \
image; use "redact" to hide passwords, tokens or personal data. Fix mistakes with \
update_evidence_step or delete_evidence_step.\
"""


class Shape(BaseModel):
    """A mark drawn on the screenshot. Coordinates are pixels of the image itself."""

    type: Literal["rect", "highlight", "arrow", "text", "step", "redact"] = Field(
        description="rect = outlined box, highlight = translucent box, arrow = line with head, "
        "text = free text, step = numbered circle, redact = solid box hiding sensitive data."
    )
    x: float = Field(description="Left edge of a box or text, start of an arrow, or centre of a step badge.")
    y: float = Field(description="Top edge of a box or text, start of an arrow, or centre of a step badge.")
    width: float | None = Field(None, description="Box width (rect, highlight, redact).")
    height: float | None = Field(None, description="Box height (rect, highlight, redact).")
    to_x: float | None = Field(None, description="Arrow end (tip) x.")
    to_y: float | None = Field(None, description="Arrow end (tip) y.")
    text: str | None = Field(None, description="Text to write (type=text).")
    label: str | None = Field(None, description="Short caption shown next to a rect, highlight or arrow.")
    number: int | None = Field(None, description="Number in a step badge; defaults to 1, 2, 3... in order.")
    color: str | None = Field(None, pattern=r"^#[0-9a-fA-F]{6}$",
                              description="#rrggbb. Defaults: red; yellow for highlight; black for redact.")
    thickness: float | None = Field(None, ge=1, le=50, description="Line width in pixels (default 4).")


class Region(BaseModel):
    """A rectangle of the desktop in physical pixels (see list_capture_targets)."""

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)


# Shared parameter descriptions -------------------------------------------------
# Optional free-text parameters are annotated as plain ``str`` with a ``None`` default
# (= omitted): the SDK JSON-decodes string arguments for any other annotation, which
# would turn text such as '{"error": "invalid_grant"}' into an object and reject it.
TitleArg =Annotated[str, Field(description="What this step shows, e.g. 'Error message after wrong password'.")]
StatusArg = Annotated[Status, Field(description="passed, failed (actual != expected), blocked, skipped or "
                                                "info (context without a verdict).")]
ExpectedArg = Annotated[str, Field(description="Expected result of the step.")]
ActualArg = Annotated[str, Field(description="Actual result observed.")]
NotesArg = Annotated[str, Field(description="Extra details: data used, error text, defect ID, log lines.")]
UrlArg = Annotated[str, Field(description="Page URL or resource the evidence refers to.")]
ShapesArg = Annotated[list[Shape] | None, Field(description="Optional marks to draw on the image before saving.")]
IncludeImageArg = Annotated[bool, Field(description="Also return a downscaled preview of the saved image so you "
                                                    "can verify it (costs tokens).")]
FormatsArg = Annotated[list[ReportFormat], Field(description="Report formats to write: html (single file, "
                                                             "images embedded), pdf, md (Markdown).")]


def _expected_errors(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Report anticipated failures to the model with their message (as ToolError)."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except (EvidenceError, ImageError, screens.CaptureTargetError) as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _gui_command(config_path: str | None) -> list[str]:
    """Command line that starts the capture overlay (GUI) in a separate process."""
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]
    else:
        exe = Path(sys.executable)
        pythonw = exe.with_name("pythonw.exe")
        cmd = [str(pythonw if sys.platform == "win32" and pythonw.is_file() else exe), "-m", "screenshot_tool"]
    if config_path:
        cmd += ["--config", config_path]
    return cmd


async def _client_roots(ctx: Context | None) -> list[Path]:
    """Workspace folders the client exposes via MCP roots (best effort, may be empty)."""
    if ctx is None:
        return []
    try:
        caps = ctx.client_capabilities
        if caps is None or caps.roots is None:
            return []
        with warnings.catch_warnings(), anyio.move_on_after(3):
            warnings.simplefilter("ignore")  # roots are deprecated in newer protocol versions
            result = await ctx.session.list_roots()
            return [Path(url2pathname(unquote(urlparse(str(root.uri)).path)))
                    for root in result.roots if str(root.uri).startswith("file:")]
    except Exception as exc:  # noqa: BLE001 - roots are optional; never fail the tool because of them
        log.debug("Could not list client roots: %s", exc)
    return []


class _State:
    def __init__(self, root: Path, config: Config, workspaces: list[Path] | None = None) -> None:
        self.root = root
        self.config = config
        self.workspaces = workspaces or []
        self.session: EvidenceSession | None = None

    async def resolve_image(self, raw: str, ctx: Context | None) -> Path:
        """Find ``raw``: absolute, or relative to the working directory / workspaces / client roots.

        Playwright MCP saves screenshots relative to its workspace root, which is normally
        the same folder MCP clients start this server in.
        """
        path = Path(raw).expanduser()
        if path.is_absolute():
            if not path.is_file():
                raise EvidenceError(f"Image file not found: {path}")
            return path
        bases = [Path.cwd(), *self.workspaces, *(await _client_roots(ctx))]
        for base in dict.fromkeys(bases):
            candidate = base / path
            if candidate.is_file():
                return candidate
        tried = ", ".join(str(b) for b in dict.fromkeys(bases))
        raise EvidenceError(f"Image file not found: {raw} (looked in: {tried}). Pass an absolute path, or start "
                            "the server with --workspace <folder> where the screenshots are saved.")

    def require(self) -> EvidenceSession:
        if self.session is None:
            raise EvidenceError("No active evidence session. Call start_evidence_session first "
                                "(or open_evidence_session to continue an existing one).")
        return self.session

    def resolve(self, ref: str | None) -> EvidenceSession:
        return EvidenceSession.open(ref, self.root) if ref else self.require()


def _step_result(session: EvidenceSession, step: EvidenceStep, include_image: bool, **extra: Any) -> Any:
    data = {"session_id": session.id, "step": session.step_dict(step), "step_count": len(session.steps)}
    data.update({k: v for k, v in extra.items() if v})
    path = session.image_path(step)
    if include_image and path is not None:
        preview = preview_png(load_image(path))
        return [json.dumps(data, indent=2, ensure_ascii=False), Image(data=preview, format="png")]
    return data


def build_server(root: str | Path, config: Config | None = None, config_path: str | None = None,
                 workspaces: list[str | Path] | None = None) -> MCPServer:
    """Create the MCP server. Sessions are stored under ``root``; relative image paths are
    also looked up in ``workspaces``."""
    config = config or Config()
    state = _State(Path(root).expanduser(), config, [Path(w).expanduser() for w in workspaces or []])
    mcp = MCPServer(
        name=SERVER_NAME,
        title="QA Evidence (Screenshot Tool)",
        description="Record numbered, annotated screenshots as QA test evidence and export HTML/PDF/Markdown reports.",
        instructions=INSTRUCTIONS,
        version=__version__,
    )

    def tool(title: str, *, read_only: bool = False, destructive: bool = False, idempotent: bool = False):
        hints = ToolAnnotations(title=title, read_only_hint=read_only, destructive_hint=destructive,
                                idempotent_hint=idempotent, open_world_hint=False)

        def register(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            mcp.add_tool(_expected_errors(fn), title=title, annotations=hints, structured_output=False)
            return fn

        return register

    # ------------------------------------------------------------- sessions
    @tool("Start evidence session")
    async def start_evidence_session(
        title: Annotated[str, Field(description="Test case or scenario name, e.g. 'Login with valid credentials'.")],
        test_case_id: Annotated[str, Field(description="ID in the test management tool, e.g. 'TC-101' "
                                                       "or a Jira key.")] = "",
        environment: Annotated[str, Field(description="Where it runs, e.g. 'Staging, Chrome 131, Windows 11'.")] = "",
        build: Annotated[str, Field(description="Application version or build number under test.")] = "",
        description: Annotated[str, Field(description="Objective, preconditions or test data.")] = "",
        tester: Annotated[str, Field(description="Who executed the test; defaults to the configured tester "
                                                 "or the OS user.")] = "",
        stamp_images: Annotated[bool | None, Field(description="Add a footer with test case, time, URL and tester "
                                                               "to each screenshot. Omit for the default (on).")] = None,
    ) -> dict:
        """Start a new QA evidence session (one per test case or test run) and make it the active one.

        Call this before recording steps. Returns the session id and the folder where the screenshots
        and reports are written."""
        session = EvidenceSession.create(
            state.root, title, test_case_id=test_case_id, environment=environment, build=build,
            description=description, tester=tester or config.tester or None,
            stamp_images=config.stamp_images if stamp_images is None else stamp_images,
        )
        state.session = session
        return {"session_id": session.id, "folder": str(session.path), "title": session.title,
                "test_case_id": session.test_case_id, "tester": session.tester,
                "next": "Record steps with add_evidence_step, capture_screen or request_manual_capture."}

    @tool("Open evidence session", idempotent=True)
    async def open_evidence_session(
        session: Annotated[str, Field(description="Session id (folder name) from list_evidence_sessions, "
                                                  "or the session folder path.")],
    ) -> dict:
        """Make an existing evidence session the active one, e.g. to add steps to an earlier run."""
        state.session = state.resolve(session)
        return state.session.to_dict(include_steps=False)

    @tool("Get evidence session", read_only=True, idempotent=True)
    async def get_evidence_session(
        session: Annotated[str, Field(description="Session id or folder; omit for the active session.")] = None,
    ) -> dict:
        """Show a session's details and all recorded steps (numbers, titles, statuses, image paths)."""
        return state.resolve(session).to_dict()

    @tool("List evidence sessions", read_only=True, idempotent=True)
    async def list_evidence_sessions(
        limit: Annotated[int, Field(ge=1, le=200, description="Maximum number of sessions, newest first.")] = 20,
    ) -> dict:
        """List recent evidence sessions (newest first) with their status and step counts."""
        sessions = list_sessions(state.root, limit)
        active = state.session.id if state.session else None
        return {"evidence_folder": str(state.root), "active_session": active,
                "sessions": [s.to_dict(include_steps=False) for s in sessions]}

    # ----------------------------------------------------------------- steps
    @tool("Add evidence step")
    async def add_evidence_step(
        ctx: Context,
        title: TitleArg,
        image_path: Annotated[str, Field(description="Image file to record (PNG/JPEG/WebP), e.g. the path "
                                                            "printed by Playwright's browser_take_screenshot "
                                                            "(relative paths are resolved against the workspace). "
                                                            "Omit for a text-only step.")] = None,
        status: StatusArg = "info",
        expected: ExpectedArg = "",
        actual: ActualArg = "",
        notes: NotesArg = "",
        url: UrlArg = "",
        annotations: ShapesArg = None,
        delete_source_file: Annotated[bool, Field(description="Delete image_path after it is copied into the "
                                                              "session, so screenshots do not pile up in the "
                                                              "workspace.")] = False,
        include_image: IncludeImageArg = False,
    ) -> Any:
        """Record a step in the active session from an existing screenshot file or as text only.

        Use after every verified test step, typically right after Playwright's browser_take_screenshot.
        The image is copied into the session folder (with annotations and the evidence footer)."""
        session = state.require()
        source = await state.resolve_image(image_path, ctx) if image_path else None
        shapes = [s.model_dump(exclude_none=True) for s in annotations or []]
        step = session.add_step(source, title=title, status=status, expected=expected, actual=actual,
                                notes=notes, url=url, source="mcp", annotations=shapes)
        if source is not None and delete_source_file:
            try:
                source.unlink()
            except OSError as exc:
                log.warning("Could not delete %s: %s", source, exc)
        return _step_result(session, step, include_image)

    @tool("Capture screen as evidence")
    async def capture_screen(
        title: TitleArg,
        status: StatusArg = "info",
        expected: ExpectedArg = "",
        actual: ActualArg = "",
        notes: NotesArg = "",
        url: UrlArg = "",
        window: Annotated[str, Field(description="Capture only this window: part of its title or process "
                                                        "name, e.g. 'Chrome' or 'notepad.exe' (Windows). It is "
                                                        "brought to the front first.")] = None,
        monitor: Annotated[int | None, Field(ge=0, description="Capture one monitor (1-based, see "
                                                               "list_capture_targets); 0 = all monitors.")] = None,
        region: Annotated[Region | None, Field(description="Capture an exact desktop rectangle in pixels.")] = None,
        annotations: ShapesArg = None,
        delay_seconds: Annotated[float, Field(ge=0, le=10, description="Wait before capturing, e.g. for "
                                                                       "animations or tooltips.")] = 0,
        include_image: IncludeImageArg = False,
    ) -> Any:
        """Take a screenshot of the real screen and record it as a step in the active session.

        Captures the whole desktop unless window, monitor or region is given (use only one). Use it for
        desktop applications or to show browser chrome (address bar, dialogs) that page screenshots miss."""
        session = state.require()
        if sum(target is not None for target in (window, monitor, region)) > 1:
            raise EvidenceError("Use only one of window, monitor or region.")
        if delay_seconds:
            await anyio.sleep(delay_seconds)
        warning = ""
        if window:
            win = screens.find_window(window)
            screens.activate_window(win)
            await anyio.sleep(0.4)  # let it repaint in front
            for _ in range(10):  # a minimized window is restored asynchronously
                if not screens.is_minimized(win):
                    break
                await anyio.sleep(0.2)
            if not screens.is_foreground(win):
                warning = ("The window could not be brought to the front, so other windows may cover it; "
                           "check the image (include_image=true).")
            image, rect = screens.capture_window(win)
            target = f"window '{win.title}' ({win.process})"
        elif region is not None:
            image, rect = screens.capture_region(region.x, region.y, region.width, region.height)
            target = "region"
        else:
            image, rect = screens.capture_monitor(monitor)
            target = f"monitor {monitor}" if monitor else "all monitors"
        shapes = [s.model_dump(exclude_none=True) for s in annotations or []]
        step = session.add_step(image, title=title, status=status, expected=expected, actual=actual,
                                notes=notes, url=url, source="mcp:screen", annotations=shapes)
        return _step_result(session, step, include_image, captured=f"{target} at {rect}", warning=warning)

    @tool("Ask the tester to capture")
    async def request_manual_capture(
        title: TitleArg,
        instructions: Annotated[str, Field(description="What the tester should select or check, shown on "
                                                       "their screen, e.g. 'Select the order total'.")] = "",
        status: StatusArg = "info",
        expected: ExpectedArg = "",
        actual: ActualArg = "",
        notes: NotesArg = "",
        url: UrlArg = "",
        timeout_seconds: Annotated[int, Field(ge=10, le=1800, description="How long to wait for the "
                                                                          "tester.")] = 300,
        include_image: IncludeImageArg = False,
    ) -> Any:
        """Ask the human tester to select (and optionally annotate) part of their screen, then record it.

        Opens the capture overlay on the tester's desktop with your instructions and waits until they press
        Enter (done) or Esc (cancel). Use it when a person must choose or confirm what is shown."""
        session = state.require()
        with tempfile.TemporaryDirectory(prefix="qa-evidence-") as tmp:
            output = Path(tmp) / "capture.png"
            prompt = f"{session.label} — {title}" + (f": {instructions}" if instructions else "")
            cmd = _gui_command(config_path) + ["--output", str(output), "--prompt", prompt]
            env = {k: v for k, v in os.environ.items() if k not in ("QT_QPA_PLATFORM", "QT_QPA_FONTDIR")}
            process = await anyio.open_process(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                with anyio.fail_after(timeout_seconds):
                    code = await process.wait()
            except TimeoutError:
                raise EvidenceError(f"The tester did not finish the capture within {timeout_seconds} s.") from None
            finally:
                # Timed out, cancelled by the client or disconnected: never leave the overlay
                # on the tester's screen once nobody is waiting for it.
                if process.returncode is None:
                    process.kill()
                    with anyio.CancelScope(shield=True):
                        await process.wait()
            if code != 0 or not output.is_file():
                raise EvidenceError("The tester cancelled the capture." if code == 2
                                    else f"The capture overlay failed (exit code {code}).")
            step = session.add_step(output, title=title, status=status, expected=expected, actual=actual,
                                    notes=notes, url=url, source="mcp:manual")
        return _step_result(session, step, include_image)

    @tool("Update evidence step", idempotent=True)
    async def update_evidence_step(
        step: Annotated[int, Field(ge=1, description="Step number.")],
        title: Annotated[str, Field(description="New title.")] = None,
        status: Annotated[Status | None, Field(description="New status.")] = None,
        expected: Annotated[str, Field(description="New expected result.")] = None,
        actual: Annotated[str, Field(description="New actual result.")] = None,
        notes: Annotated[str, Field(description="New notes.")] = None,
        url: Annotated[str, Field(description="New URL.")] = None,
    ) -> dict:
        """Change the text or status of a recorded step in the active session (omitted fields stay as they are)."""
        session = state.require()
        updated = session.update_step(step, title=title, status=status, expected=expected, actual=actual,
                                      notes=notes, url=url)
        return {"session_id": session.id, "step": session.step_dict(updated)}

    @tool("Delete evidence step", destructive=True)
    async def delete_evidence_step(
        step: Annotated[int, Field(ge=1, description="Step number to delete.")],
    ) -> dict:
        """Delete a wrong step (and its image) from the active session; later steps are renumbered."""
        session = state.require()
        removed = session.delete_step(step)
        return {"session_id": session.id, "deleted": removed.title, "step_count": len(session.steps),
                "note": "Later steps were renumbered."}

    # --------------------------------------------------------------- reports
    @tool("Finish evidence session", idempotent=True)
    async def finish_evidence_session(
        result: Annotated[Status | None, Field(description="Overall verdict. Omit to derive it from the steps "
                                                           "(any failed -> failed, any blocked -> blocked, "
                                                           "else passed).")] = None,
        summary: Annotated[str, Field(description="Short conclusion: what was tested, what failed, defects "
                                                  "raised.")] = "",
        formats: FormatsArg = ["html", "pdf", "md"],  # noqa: B006 - read-only default
    ) -> dict:
        """Finish the active session with an overall result and write the evidence reports.

        Call once all steps are recorded; share the returned report paths with the user. The session stays
        active, so you can still add steps and call this again."""
        session = state.require()
        verdict = session.finish(result, summary or None)
        reports = session.export(normalize_formats(formats))
        return {"session_id": session.id, "result": verdict, "counts": session.to_dict(False)["counts"],
                "folder": str(session.path), "reports": {k: str(v) for k, v in reports.items()}}

    @tool("Generate evidence report", idempotent=True)
    async def generate_evidence_report(
        formats: FormatsArg = ["html", "pdf", "md"],  # noqa: B006 - read-only default
        session: Annotated[str, Field(description="Session id or folder; omit for the active "
                                                         "session.")] = None,
    ) -> dict:
        """Write (or refresh) the reports of a session without changing its result."""
        target = state.resolve(session)
        reports = target.export(normalize_formats(formats))
        return {"session_id": target.id, "reports": {k: str(v) for k, v in reports.items()}}

    @tool("List capture targets", read_only=True, idempotent=True)
    async def list_capture_targets() -> dict:
        """List the monitors and (on Windows) open windows that capture_screen can capture."""
        windows = screens.list_windows()
        return {
            "monitors": [m.to_dict() for m in screens.list_monitors()],
            "windows": [w.to_dict() for w in windows[:60]],
            "note": "Pass a monitor index, part of a window title/process, or a region (x, y, width, height) "
                    "to capture_screen. Coordinates are physical desktop pixels.",
        }

    # --------------------------------------------------------------- prompts
    @mcp.prompt(name="test_with_evidence", title="Run a test case and record evidence")
    def test_with_evidence(test_case: str, steps: str, url: str = "", environment: str = "") -> str:
        """Execute a manual test case with browser tools and record QA evidence for every step."""
        target = f" against {url}" if url else ""
        env = f" Environment: {environment}." if environment else ""
        return (
            f"Execute test case '{test_case}'{target} and record QA evidence.{env}\n\n"
            f"Test steps:\n{steps}\n\n"
            "1. Call start_evidence_session (put the test case ID, if any, in test_case_id).\n"
            "2. Perform each step with the browser tools (e.g. Playwright MCP). After each step, take a "
            "screenshot with browser_take_screenshot and record it with add_evidence_step (image_path = the "
            "saved file), stating the expected and actual result and the status. Use annotations to point at "
            "the relevant element and 'redact' to hide secrets.\n"
            "3. If a step fails, record it as failed with details, then continue with the remaining steps "
            "when possible (mark steps that cannot run as blocked).\n"
            "4. Call finish_evidence_session with the overall result and a short summary, then give me the "
            "report paths."
        )

    return mcp


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="screenshot_tool mcp", description="QA evidence MCP server (stdio)")
    add_arguments(parser)
    parser.add_argument("--config", help="path to a config.json (default: per-user location)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging (stderr)")
    return parser.parse_args(argv)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--evidence-dir", help="folder for evidence sessions (default: QA_EVIDENCE_DIR, the "
                                               "config's evidence_folder, or Documents/QA-Evidence)")
    parser.add_argument("--workspace", action="append", default=[], metavar="DIR",
                        help="extra folder for resolving relative screenshot paths, e.g. the Playwright MCP "
                             "workspace (repeatable; also QA_EVIDENCE_WORKSPACE)")


def run(args: argparse.Namespace) -> int:
    """Start the server on stdio (blocks until the client disconnects)."""
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
                        stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    config = load_config(args.config)
    root = Path(args.evidence_dir or os.environ.get("QA_EVIDENCE_DIR") or config.evidence_root()).expanduser()
    workspaces = list(getattr(args, "workspace", None) or [])
    workspaces += [w for w in os.environ.get("QA_EVIDENCE_WORKSPACE", "").split(os.pathsep) if w]
    ensure_gui_app()  # Qt lives on the main thread; every tool is async, so it runs here too
    log.info("QA evidence MCP server %s; sessions in %s", __version__, root)
    build_server(root, config, args.config, workspaces).run("stdio")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: ``screenshot-tool-mcp``."""
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
