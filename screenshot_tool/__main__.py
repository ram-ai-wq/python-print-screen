"""Entry point: ``python -m screenshot_tool``.

    python -m screenshot_tool               # capture now, then keep running in the tray
    python -m screenshot_tool mcp           # MCP server for AI agents (stdio)
    python -m screenshot_tool evidence ...  # evidence sessions from scripts / CI
"""

from __future__ import annotations

import argparse
import logging
import sys

from screenshot_tool import __version__
from screenshot_tool.cli import add_arguments as add_evidence_arguments

EPILOG = """\
commands:
  mcp        run the MCP server so AI agents (Claude, Copilot, Cursor...) can record evidence
  evidence   create sessions, add steps and write reports from scripts and CI
             (see: python -m screenshot_tool evidence --help)
"""


def _common(suppress: bool) -> argparse.ArgumentParser:
    """Options accepted before or after a sub-command (``SUPPRESS`` keeps the first value)."""
    default = argparse.SUPPRESS if suppress else None
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=default, help="path to a config.json (default: per-user location)")
    common.add_argument("-v", "--verbose", action="store_true",
                        default=argparse.SUPPRESS if suppress else False, help="debug logging")
    return common


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="screenshot_tool", description="Screenshot, annotation and QA evidence tool",
        parents=[_common(False)], epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--once", action="store_true", help="capture once and exit (no hotkey/tray)")
    parser.add_argument("--background", action="store_true",
                        help="start in the tray without capturing right away")
    parser.add_argument("--session", metavar="SESSION",
                        help="start with this evidence session active (folder or ID)")
    parser.add_argument("--output", metavar="FILE",
                        help="capture once, save the selection to FILE (PNG) and exit; exit code 2 if cancelled")
    parser.add_argument("--prompt", metavar="TEXT", help="instructions shown at the top of the capture overlay")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    mcp = sub.add_parser("mcp", parents=[_common(True)], help="run the MCP server for AI agents (stdio)",
                         description="QA evidence MCP server (stdio transport).")
    mcp.add_argument("--evidence-dir", help="folder for evidence sessions (default: QA_EVIDENCE_DIR, the config's "
                                            "evidence_folder, or Documents/QA-Evidence)")
    mcp.add_argument("--workspace", action="append", default=[], metavar="DIR",
                     help="extra folder for resolving relative screenshot paths, e.g. the Playwright MCP "
                          "workspace (repeatable; also QA_EVIDENCE_WORKSPACE)")
    evidence = sub.add_parser("evidence", help="evidence sessions from scripts and CI",
                              description="Create evidence sessions, add steps and write reports.")
    add_evidence_arguments(evidence, _common(True))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the application."""
    args = parse_args(argv)
    if args.command == "mcp":
        try:
            from screenshot_tool.mcp_server import run as run_mcp
        except ModuleNotFoundError as exc:
            if (exc.name or "").split(".")[0] not in ("mcp", "mcp_types"):
                raise
            print("The MCP server needs the 'mcp' package: pip install -r requirements.txt", file=sys.stderr)
            return 1
        return run_mcp(args)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.command == "evidence":
        from screenshot_tool.cli import run as run_cli

        return run_cli(args)

    from screenshot_tool.app import run_gui

    return run_gui(args)


if __name__ == "__main__":
    sys.exit(main())
