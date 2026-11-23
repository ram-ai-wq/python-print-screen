"""``python -m screenshot_tool evidence ...``: build evidence from scripts and CI.

Any test framework can call these commands (Playwright for JS/TS or Python,
Cypress, Selenium, plain shell scripts). Results are printed as JSON::

    python -m screenshot_tool evidence new --title "Login" --test-case TC-101 --print-path
    python -m screenshot_tool evidence add <SESSION> --image shot.png --title "Login page" --status passed
    python -m screenshot_tool evidence finish <SESSION> --summary "All good" --format html,pdf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

STATUS_HELP = "passed, failed, blocked, skipped or info"


def add_arguments(parser: argparse.ArgumentParser, common: argparse.ArgumentParser | None = None) -> None:
    """Register the ``evidence`` sub-commands on ``parser``."""
    parents = [common] if common else []
    sub = parser.add_subparsers(dest="evidence_command", required=True, metavar="COMMAND")

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text, parents=parents)
        p.add_argument("--evidence-dir", help="folder holding sessions (default: QA_EVIDENCE_DIR, the config's "
                                              "evidence_folder, or Documents/QA-Evidence)")
        return p

    def step_fields(p: argparse.ArgumentParser) -> None:
        p.add_argument("session", help="session folder or ID")
        p.add_argument("--title", required=True, help="what the step shows")
        p.add_argument("--status", default="info", help=STATUS_HELP + " (default: info)")
        p.add_argument("--expected", default="", help="expected result")
        p.add_argument("--actual", default="", help="actual result")
        p.add_argument("--notes", default="", help="extra details")
        p.add_argument("--url", default="", help="page or resource the evidence refers to")
        p.add_argument("--annotations", help="JSON list of shapes, e.g. "
                                             "'[{\"type\":\"rect\",\"x\":10,\"y\":20,\"width\":200,\"height\":40}]'")
        p.add_argument("--no-stamp", action="store_true", help="do not add the evidence footer to the image")

    new = command("new", "create a session and print it")
    new.add_argument("--title", default="", help="test case / scenario name")
    new.add_argument("--test-case", default="", help="test case ID, e.g. TC-101")
    new.add_argument("--tester", help="who runs the test (default: config tester or OS user)")
    new.add_argument("--environment", default="", help="e.g. 'Staging, Chrome 131'")
    new.add_argument("--build", default="", help="application version under test")
    new.add_argument("--description", default="", help="objective, preconditions, test data")
    new.add_argument("--no-stamp", action="store_true", help="do not add evidence footers to screenshots")
    new.add_argument("--print-path", action="store_true", help="print only the session folder")

    add = command("add", "add a step from an image file (or a text-only step)")
    step_fields(add)
    add.add_argument("--image", help="PNG/JPEG/WebP file; omit for a text-only step")

    cap = command("capture", "capture the screen as a step")
    step_fields(cap)
    target = cap.add_mutually_exclusive_group()
    target.add_argument("--monitor", type=int, help="monitor number (1-based); 0 = all monitors (default)")
    target.add_argument("--window", help="part of a window title or process name (Windows)")
    target.add_argument("--region", help="desktop rectangle 'x,y,width,height' in pixels")
    cap.add_argument("--delay", type=float, default=0, help="seconds to wait before capturing")

    fin = command("finish", "set the overall result and write the reports")
    fin.add_argument("session", help="session folder or ID")
    fin.add_argument("--result", help=STATUS_HELP + " (default: derived from the steps)")
    fin.add_argument("--summary", help="conclusion")
    fin.add_argument("--format", default="html,pdf,md", help="comma-separated: html, pdf, md")

    rep = command("report", "write (or refresh) the reports")
    rep.add_argument("session", help="session folder or ID")
    rep.add_argument("--format", default="html,pdf,md", help="comma-separated: html, pdf, md")
    rep.add_argument("--no-embed", action="store_true", help="HTML references the image files instead of "
                                                             "embedding them")

    show = command("show", "print a session and its steps")
    show.add_argument("session", help="session folder or ID")

    lst = command("list", "list sessions, newest first")
    lst.add_argument("--limit", type=_positive_int, default=20, help="maximum number of sessions (default 20)")


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _annotations(text: str | None) -> list[dict[str, Any]] | None:
    from .evidence import EvidenceError

    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise EvidenceError(f"--annotations is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise EvidenceError("--annotations must be a JSON list of shapes.")
    return data


def _region(text: str) -> tuple[int, int, int, int]:
    from .evidence import EvidenceError

    try:
        x, y, w, h = (int(v) for v in text.split(","))
    except ValueError:
        raise EvidenceError("--region must look like 'x,y,width,height', e.g. 0,0,1280,720.") from None
    return x, y, w, h


def run(args: argparse.Namespace) -> int:
    """Execute an ``evidence`` sub-command; returns the process exit code."""
    _utf8_stdout()
    from . import screens
    from .config import load_config
    from .evidence import EvidenceError, EvidenceSession, list_sessions
    from .qt_runtime import ensure_gui_app

    config = load_config(getattr(args, "config", None))
    root = Path(args.evidence_dir or os.environ.get("QA_EVIDENCE_DIR") or config.evidence_root()).expanduser()
    cmd = args.evidence_command
    try:
        ensure_gui_app()  # footers, annotations and PDF need Qt fonts
        if cmd == "new":
            session = EvidenceSession.create(
                root, args.title, test_case_id=args.test_case, tester=args.tester or config.tester or None,
                environment=args.environment, build=args.build, description=args.description,
                stamp_images=config.stamp_images and not args.no_stamp,
            )
            result: Any = str(session.path) if args.print_path else session.to_dict(include_steps=False)
        elif cmd in ("add", "capture"):
            session = EvidenceSession.open(args.session, root)
            if cmd == "add":
                image: Any = args.image
                source = "cli"
            else:
                if args.delay > 0:
                    time.sleep(min(args.delay, 60))
                if args.window:
                    win = screens.find_window(args.window)
                    screens.activate_window(win)
                    time.sleep(0.4)  # let it repaint in front
                    for _ in range(10):  # a minimized window is restored asynchronously
                        if not screens.is_minimized(win):
                            break
                        time.sleep(0.2)
                    image, _rect = screens.capture_window(win)
                elif args.region:
                    image, _rect = screens.capture_region(*_region(args.region))
                else:
                    image, _rect = screens.capture_monitor(args.monitor)
                source = "cli:screen"
            step = session.add_step(
                image, title=args.title, status=args.status, expected=args.expected, actual=args.actual,
                notes=args.notes, url=args.url, source=source, annotations=_annotations(args.annotations),
                stamp=False if args.no_stamp else None,
            )
            result = {"session_id": session.id, "step": session.step_dict(step)}
        elif cmd == "finish":
            session = EvidenceSession.open(args.session, root)
            verdict = session.finish(args.result, args.summary)
            reports = session.export(args.format)
            result = {"session_id": session.id, "result": verdict, "reports": {k: str(v) for k, v in reports.items()}}
        elif cmd == "report":
            session = EvidenceSession.open(args.session, root)
            reports = session.export(args.format, embed_images=not args.no_embed)
            result = {"session_id": session.id, "reports": {k: str(v) for k, v in reports.items()}}
        elif cmd == "show":
            result = EvidenceSession.open(args.session, root).to_dict()
        else:  # list
            result = {"evidence_folder": str(root),
                      "sessions": [s.to_dict(include_steps=False) for s in list_sessions(root, args.limit)]}
    except (EvidenceError, screens.CaptureTargetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: ``screenshot-evidence``."""
    parser = argparse.ArgumentParser(prog="screenshot-evidence", description="QA evidence from scripts and CI")
    parser.add_argument("--config", help="path to a config.json")
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
