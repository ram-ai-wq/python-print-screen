"""Record QA evidence from a Playwright (Python) test - no AI involved.

Runs a small test case against the bundled demo site (or your own URL) and writes
an evidence session with an HTML/PDF/Markdown report::

    pip install playwright
    python examples/playwright_evidence.py --channel msedge      # use the installed Edge
    python examples/playwright_evidence.py --channel chrome --headed
    python -m playwright install chromium && python examples/playwright_evidence.py

The same three calls work in any test framework: ``EvidenceSession.create``,
``session.add_step(page.screenshot(), ...)`` and ``session.finish()`` / ``session.export()``.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # run from a source checkout without installing

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from screenshot_tool.evidence import EvidenceSession  # noqa: E402


def box(locator, pad: float = 4) -> dict:
    """Element bounding box (CSS px = screenshot px at scale 1) as a 'rect' annotation."""
    b = locator.bounding_box()
    return {"x": b["x"] - pad, "y": b["y"] - pad, "width": b["width"] + 2 * pad, "height": b["height"] + 2 * pad}


def serve_demo_site() -> tuple[socketserver.TCPServer, str]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args) -> None:  # keep the console clean
            pass

    handler = functools.partial(Quiet, directory=str(ROOT / "examples" / "demo-site"))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/index.html"


def run(url: str, evidence_dir: Path, channel: str | None, headed: bool) -> EvidenceSession:
    session = EvidenceSession.create(
        evidence_dir, "Login with valid credentials", test_case_id="TC-101",
        environment=f"{channel or 'chromium'} {'headed' if headed else 'headless'} · 1280x720",
        description="A registered customer can sign in; a wrong password shows an error.",
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel, headless=not headed)
        page = browser.new_page(viewport={"width": 1280, "height": 720})
        try:
            page.goto(url)
            expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
            session.add_step(page.screenshot(), title="Open the sign-in page", status="passed",
                             expected="Sign-in form is displayed", actual="Form displayed", url=page.url)

            page.fill("#email", "ana@example.com")
            page.fill("#password", "wrong-password")
            page.click("button[type=submit]")
            error = page.get_by_role("alert")
            expect(error).to_contain_text("Invalid email or password")
            session.add_step(
                page.screenshot(), title="Wrong password shows an error", status="passed",
                expected="Message 'Invalid email or password'", actual=error.inner_text(), url=page.url,
                annotations=[{"type": "rect", **box(error), "label": "Error message"},
                             {"type": "redact", **box(page.locator("#password"), pad=0)}],
            )

            page.fill("#password", "correct-horse")
            page.click("button[type=submit]")
            expect(page.get_by_text("Welcome back")).to_be_visible()
            session.add_step(page.screenshot(), title="Valid credentials open the account page", status="passed",
                             expected="Welcome page with the customer name", actual="'Welcome back, Ana!'",
                             url=page.url)
        except Exception as exc:  # record the failure as evidence, then re-raise
            session.add_step(page.screenshot(), title="Unexpected failure", status="failed", actual=str(exc)[:500],
                             url=page.url)
            raise
        finally:
            browser.close()
            session.finish(summary="Automated run with Playwright.")
            print(session.export(["html", "pdf", "md"]))
    return session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="page to test (default: the bundled demo site)")
    parser.add_argument("--channel", help="browser channel: msedge, chrome... (default: Playwright's chromium)")
    parser.add_argument("--headed", action="store_true", help="show the browser")
    parser.add_argument("--evidence-dir", default="evidence", type=Path, help="where sessions are written")
    args = parser.parse_args()
    httpd, url = (None, args.url) if args.url else serve_demo_site()
    try:
        session = run(url, args.evidence_dir, args.channel, args.headed)
        print(f"Result: {session.result} — open {session.path / 'report.html'}")
    finally:
        if httpd is not None:
            httpd.shutdown()


if __name__ == "__main__":
    main()
