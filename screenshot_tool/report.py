"""Evidence reports: self-contained HTML, Markdown and PDF.

Image integrity is checked while writing: each image's SHA-256 is recomputed and
compared with the hash recorded at capture time, and mismatches are flagged.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import time
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QMarginsF, Qt, QUrl
from PyQt6.QtGui import QImage, QPageLayout, QPageSize, QPdfWriter, QTextDocument

from . import __version__
from .evidence import EvidenceError, EvidenceSession, EvidenceStep, write_text_atomic, format_timestamp
from .qt_runtime import ensure_gui_app
from .render import ImageError, load_image

FORMATS = ("html", "pdf", "md")
_FORMAT_ALIASES = {"markdown": "md", "htm": "html"}

STATUS_LABELS = {
    "passed": "Passed", "failed": "Failed", "blocked": "Blocked", "skipped": "Skipped",
    "info": "Info", "in_progress": "In progress",
}
RESULT_LABELS = {**STATUS_LABELS, "info": "No verdict"}
STATUS_COLORS = {
    "passed": "#1a7f37", "failed": "#cf222e", "blocked": "#bc4c00", "skipped": "#6e7781",
    "info": "#0969da", "in_progress": "#6e7781",
}
STATUS_EMOJI = {"passed": "✅", "failed": "❌", "blocked": "⛔", "skipped": "⏭️", "info": "ℹ️", "in_progress": "⏳"}


def normalize_formats(formats: Sequence[str] | str | None) -> list[str]:
    """``"html, PDF"`` / ``["markdown"]`` -> ``["html", "pdf"]`` / ``["md"]``.

    Raises:
        EvidenceError: unknown format.
    """
    if formats is None:
        return list(FORMATS)
    items = formats.split(",") if isinstance(formats, str) else list(formats)
    out: list[str] = []
    for item in items:
        key = str(item).strip().lower().lstrip(".")
        key = _FORMAT_ALIASES.get(key, key)
        if not key:
            continue
        if key not in FORMATS:
            raise EvidenceError(f"Unknown report format {item!r}; use {', '.join(FORMATS)}.")
        if key not in out:
            out.append(key)
    if not out:
        raise EvidenceError(f"No report format given; use {', '.join(FORMATS)}.")
    return out


def export_reports(session: EvidenceSession, formats: Sequence[str] | str | None = None,
                   *, embed_images: bool = True) -> dict[str, Path]:
    """Write the requested reports into the session folder; returns ``{format: path}``."""
    writers = {"html": lambda: write_html(session, embed_images=embed_images),
               "md": lambda: write_markdown(session),
               "pdf": lambda: write_pdf(session)}
    return {fmt: writers[fmt]() for fmt in normalize_formats(formats)}


# ------------------------------------------------------------------ shared
def _session_status(session: EvidenceSession) -> str:
    return session.result if session.is_finished and session.result else "in_progress"


def _duration(session: EvidenceSession) -> str:
    seconds = session.duration_seconds()
    if seconds is None:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {secs:02d}s"


def _counts_text(session: EvidenceSession) -> str:
    counts = session.counts()
    parts = [f"{counts[s]} {STATUS_LABELS[s].lower()}" for s in ("passed", "failed", "blocked", "skipped", "info")
             if counts[s]]
    total = counts["total"]
    head = f"{total} step{'s' if total != 1 else ''}"
    return f"{head} ({', '.join(parts)})" if parts else head


def _meta_rows(session: EvidenceSession) -> list[tuple[str, str]]:
    rows = [
        ("Test case", session.test_case_id),
        ("Tester", session.tester),
        ("Environment", session.environment),
        ("Build / version", session.build),
        ("Started", format_timestamp(session.created_at)),
        ("Finished", format_timestamp(session.finished_at) if session.finished_at else "In progress"),
        ("Duration", _duration(session)),
        ("Steps", _counts_text(session)),
    ]
    return [(k, v) for k, v in rows if v]


def _integrity(session: EvidenceSession, step: EvidenceStep) -> tuple[bytes | None, str]:
    """Return ``(file bytes, state)`` where state is ``ok``, ``modified`` or ``missing``."""
    path = session.image_path(step)
    if path is None or not path.is_file():
        return None, "missing"
    data = path.read_bytes()
    if step.sha256 and hashlib.sha256(data).hexdigest() != step.sha256:
        return data, "modified"
    return data, "ok"


def _report_title(session: EvidenceSession) -> str:
    return f"Test evidence — {session.label}"


def _write_report(target: Path, text: str) -> Path:
    try:
        write_text_atomic(target, text)
    except OSError as exc:
        raise EvidenceError(f"Could not write {target} (is it open in another program?): {exc}") from exc
    return target


# -------------------------------------------------------------------- HTML
_CSS = """
:root { color-scheme: light; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0; --soft:#f6f8fa; }
* { box-sizing: border-box; }
body { margin:0; background:#eef1f4; color:var(--fg);
  font:15px/1.5 "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif; }
main { max-width:1100px; margin:24px auto; padding:0 16px; }
.card { background:#fff; border:1px solid var(--line); border-radius:10px; padding:20px 24px; margin-bottom:16px; }
.report-header { display:flex; gap:16px; justify-content:space-between; align-items:flex-start; }
.eyebrow { margin:0; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; font-size:12px; font-weight:600; }
h1 { margin:4px 0 0; font-size:26px; line-height:1.25; }
h2 { margin:0 0 12px; font-size:18px; }
h3 { margin:0; font-size:17px; }
h4 { margin:0 0 4px; font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
.badge { display:inline-block; padding:3px 10px; border-radius:999px; color:#fff; font-size:12px;
  font-weight:700; letter-spacing:.04em; text-transform:uppercase; white-space:nowrap; }
.badge-lg { font-size:15px; padding:6px 16px; }
dl.meta { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:12px 24px; margin:0; }
dl.meta dt { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
dl.meta dd { margin:2px 0 0; font-weight:600; overflow-wrap:anywhere; }
.text { white-space:pre-wrap; overflow-wrap:anywhere; margin:0; }
table.overview { width:100%; border-collapse:collapse; font-size:14px; }
table.overview th, table.overview td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
table.overview th { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.06em; }
table.overview a { color:inherit; }
.step-head { display:flex; gap:12px; justify-content:space-between; align-items:flex-start; }
.num { display:inline-flex; align-items:center; justify-content:center; min-width:28px; height:28px; margin-right:8px;
  border-radius:50%; background:var(--fg); color:#fff; font-size:14px; }
.when { color:var(--muted); font-size:13px; margin:6px 0 0; overflow-wrap:anywhere; }
.when a { color:#0969da; }
.er { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; margin-top:14px; }
.er > div { background:var(--soft); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
.notes { margin-top:12px; }
.textblock { margin-top:16px; }
figure { margin:16px 0 0; }
figure img { display:block; max-width:100%; max-height:80vh; height:auto; margin:0 auto; border:1px solid var(--line);
  border-radius:6px; cursor:zoom-in; background:#fff; }
figure img.zoomed { max-height:none; cursor:zoom-out; }
figcaption { color:var(--muted); font-size:12px; margin-top:6px; overflow-wrap:anywhere; }
.warn { color:#9a6700; font-weight:600; }
footer { color:var(--muted); font-size:12px; text-align:center; padding:8px 0 24px; overflow-wrap:anywhere; }
@media print {
  body { background:#fff; }
  main { margin:0; max-width:none; }
  .card { border:none; padding:0 0 12px; }
  .step { break-inside:avoid; page-break-inside:avoid; }
  figure img { max-height:none; max-width:100%; cursor:default; }
}
"""

_JS = """
document.querySelectorAll('figure img').forEach(function (img) {
  img.addEventListener('click', function () { img.classList.toggle('zoomed'); });
});
"""


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _badge(status: str, labels: dict[str, str] = STATUS_LABELS, large: bool = False) -> str:
    cls = "badge badge-lg" if large else "badge"
    color = STATUS_COLORS.get(status, "#6e7781")
    return f'<span class="{cls}" style="background:{color}">{_esc(labels.get(status, status))}</span>'


def _link(url: str) -> str:
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{_esc(url)}" rel="noopener noreferrer">{_esc(url)}</a>'
    return _esc(url)


def _html_step(session: EvidenceSession, step: EvidenceStep, embed_images: bool) -> str:
    parts = [f'<article class="card step" id="step-{step.number}">',
             '<div class="step-head">',
             f'<h3><span class="num">{step.number}</span>{_esc(step.title)}</h3>',
             _badge(step.status), "</div>"]
    when = [format_timestamp(step.captured_at)]
    if step.url:
        when.append(_link(step.url))
    parts.append(f'<p class="when">{" · ".join(when)}</p>')
    boxes = [(label, value) for label, value in (("Expected result", step.expected), ("Actual result", step.actual))
             if value]
    if boxes:
        parts.append('<div class="er">')
        for label, value in boxes:
            parts.append(f'<div><h4>{label}</h4><p class="text">{_esc(value)}</p></div>')
        parts.append("</div>")
    if step.notes:
        parts.append(f'<div class="notes"><h4>Notes</h4><p class="text">{_esc(step.notes)}</p></div>')
    if step.image:
        data, state = _integrity(session, step)
        alt = _esc(f"Step {step.number}: {step.title}")
        if data is None:
            parts.append(f'<p class="warn">Image file missing: {_esc(step.image)}</p>')
        else:
            src = f"data:image/png;base64,{base64.b64encode(data).decode()}" if embed_images else _esc(step.image)
            caption = [_esc(step.image)]
            if step.width and step.height:
                caption.append(_esc(f"{step.width}×{step.height}"))
            if step.sha256:
                caption.append(f"SHA-256 {_esc(step.sha256)}")
            if state == "modified":
                caption.append('<span class="warn">⚠ file modified since capture</span>')
            parts.append(f'<figure><img src="{src}" alt="{alt}" loading="lazy">'
                         f'<figcaption>{" · ".join(caption)}</figcaption></figure>')
    parts.append("</article>")
    return "\n".join(parts)


def build_html(session: EvidenceSession, *, embed_images: bool = True) -> str:
    """Return the HTML report as a string."""
    status = _session_status(session)
    meta = "".join(f"<div><dt>{_esc(k)}</dt><dd>{_esc(v)}</dd></div>" for k, v in _meta_rows(session))
    text_blocks = []
    for label, value in (("Description", session.description), ("Summary", session.summary)):
        if value:
            text_blocks.append(f'<div class="textblock"><h4>{label}</h4><p class="text">{_esc(value)}</p></div>')
    rows = "".join(
        f'<tr><td>{s.number}</td><td><a href="#step-{s.number}">{_esc(s.title)}</a></td>'
        f"<td>{_badge(s.status)}</td><td>{_esc(format_timestamp(s.captured_at, with_offset=False))}</td></tr>"
        for s in session.steps
    )
    overview = (
        f'<section class="card"><h2>Steps</h2><table class="overview"><thead><tr><th>#</th><th>Step</th>'
        f"<th>Status</th><th>Captured</th></tr></thead><tbody>{rows}</tbody></table></section>"
        if session.steps else '<section class="card"><h2>Steps</h2><p>No steps were recorded.</p></section>'
    )
    steps = "\n".join(_html_step(session, s, embed_images) for s in session.steps)
    generated = format_timestamp(datetime.now().astimezone().isoformat(timespec="seconds"))
    subtitle = f'<p class="eyebrow" style="margin-top:6px">{_esc(session.test_case_id)}</p>' \
        if session.test_case_id and session.test_case_id != session.title else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="Screenshot Tool {__version__}">
<title>{_esc(_report_title(session))}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
<header class="card report-header">
<div><p class="eyebrow">Test evidence report</p><h1>{_esc(session.title)}</h1>{subtitle}</div>
{_badge(status, RESULT_LABELS, large=True)}
</header>
<section class="card"><dl class="meta">{meta}</dl>{"".join(text_blocks)}</section>
{overview}
{steps}
<footer>Generated {_esc(generated)} by Screenshot Tool {__version__} · Session {_esc(session.id)} ·
SHA-256 hashes identify the original image files.</footer>
</main>
<script>{_JS}</script>
</body>
</html>
"""


def write_html(session: EvidenceSession, path: str | Path | None = None, *, embed_images: bool = True) -> Path:
    """Write ``report.html`` (images embedded by default, so it is a single file)."""
    target = Path(path) if path else session.path / "report.html"
    return _write_report(target, build_html(session, embed_images=embed_images))


# ---------------------------------------------------------------- Markdown
def _md(text: str) -> str:
    """Escape table pipes and keep line breaks inside Markdown cells/paragraphs."""
    return (text or "").replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def build_markdown(session: EvidenceSession) -> str:
    """Return the Markdown report (images referenced by relative file name)."""
    status = _session_status(session)
    lines = [f"# Test evidence: {_md(session.label)}", "",
             f"**Result:** {STATUS_EMOJI[status]} {RESULT_LABELS[status].upper()}", "",
             "| Field | Value |", "|---|---|"]
    lines += [f"| {k} | {_md(v)} |" for k, v in _meta_rows(session)]
    for label, value in (("Description", session.description), ("Summary", session.summary)):
        if value:
            lines += ["", f"## {label}", "", value.strip()]
    lines += ["", "## Steps", ""]
    if not session.steps:
        lines.append("No steps were recorded.")
    else:
        lines += ["| # | Step | Status |", "|---|---|---|"]
        lines += [f"| {s.number} | {_md(s.title)} | {STATUS_EMOJI[s.status]} {STATUS_LABELS[s.status]} |"
                  for s in session.steps]
    for step in session.steps:
        lines += ["", f"### {step.number}. {_md(step.title)} — {STATUS_EMOJI[step.status]} "
                      f"{STATUS_LABELS[step.status]}", ""]
        details = [("Captured", format_timestamp(step.captured_at)), ("URL", step.url),
                   ("Expected", step.expected), ("Actual", step.actual), ("Notes", step.notes)]
        lines += [f"- **{k}:** {_md(v)}" for k, v in details if v]
        if step.image:
            _data, state = _integrity(session, step)
            lines += ["", f"![Step {step.number}: {_md(step.title)}]({step.image})"]
            note = f"`{step.image}`"
            if step.sha256:
                note += f" · SHA-256 `{step.sha256}`"
            if state != "ok":
                note += f" · ⚠ file {state}"
            lines += ["", note]
    lines += ["", "---", f"*Generated {format_timestamp(datetime.now().astimezone().isoformat(timespec='seconds'))} "
                         f"by Screenshot Tool {__version__} · session `{session.id}`*", ""]
    return "\n".join(lines)


def write_markdown(session: EvidenceSession, path: str | Path | None = None) -> Path:
    """Write ``report.md`` next to the step images."""
    target = Path(path) if path else session.path / "report.md"
    return _write_report(target, build_markdown(session))


# --------------------------------------------------------------------- PDF
# QTextDocument lays the PDF out in 96-dpi "pixels"; A4 minus the 2 cm margins Qt
# adds when printing leaves about 642 x 971 of them per page.
PDF_IMAGE_MAX_W = 640
PDF_IMAGE_MAX_H = 760
PDF_EMBED_MAX_W = 1600  # embedded pixels: about 2.5x the printed width keeps PDFs sharp but small


def _pdf_tiles(image: QImage) -> list[QImage]:
    """Fit ``image`` to the page width and split very tall images (full-page screenshots)
    into page-sized slices, so nothing is shrunk into an unreadable strip."""
    if image.width() > PDF_EMBED_MAX_W:
        image = image.scaledToWidth(PDF_EMBED_MAX_W, Qt.TransformationMode.SmoothTransformation)
    scale = min(PDF_IMAGE_MAX_W / image.width(), 1.0)
    slice_h = max(1, int(PDF_IMAGE_MAX_H / scale))
    if image.height() <= slice_h * 1.15:  # a little extra height still fits in one piece
        return [image]
    return [image.copy(0, top, image.width(), min(slice_h, image.height() - top))
            for top in range(0, image.height(), slice_h)]


def _qt_badge(status: str, labels: dict[str, str] = STATUS_LABELS) -> str:
    color = STATUS_COLORS.get(status, "#6e7781")
    return (f'<span style="background-color:{color}; color:#ffffff; font-weight:bold;">'
            f"&nbsp;{_esc(labels.get(status, status).upper())}&nbsp;</span>")


def _qt_text(text: str) -> str:
    return _esc(text).replace("\n", "<br>")


def build_pdf_document(session: EvidenceSession) -> QTextDocument:
    """Lay the report out as a ``QTextDocument`` (Qt's rich-text subset)."""
    ensure_gui_app()
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    status = _session_status(session)
    css_font = 'font-family:"Segoe UI","Helvetica Neue",Arial,sans-serif;'
    out = [f'<html><body style="{css_font} font-size:10pt; color:#1f2328;">',
           '<p style="color:#59636e; font-size:8pt; font-weight:bold;">TEST EVIDENCE REPORT</p>',
           f'<h1 style="font-size:20pt; margin-top:0px;">{_esc(session.title)}</h1>',
           f'<p style="font-size:13pt;">{_qt_badge(status, RESULT_LABELS)}</p>',
           '<table width="100%" cellspacing="0" cellpadding="5">']
    for key, value in _meta_rows(session):
        out.append(f'<tr><td width="28%" bgcolor="#f6f8fa" style="color:#59636e;">{_esc(key)}</td>'
                   f"<td><b>{_esc(value)}</b></td></tr>")
    out.append("</table>")
    for label, value in (("Description", session.description), ("Summary", session.summary)):
        if value:
            out.append(f'<h3 style="font-size:11pt;">{label}</h3><p>{_qt_text(value)}</p>')

    out.append('<h2 style="font-size:14pt;">Steps</h2>')
    if session.steps:
        out.append('<table width="100%" cellspacing="0" cellpadding="5">'
                   '<tr bgcolor="#f6f8fa"><th align="left">#</th><th align="left">Step</th>'
                   '<th align="left">Status</th><th align="left">Captured</th></tr>')
        for s in session.steps:
            out.append(f"<tr><td>{s.number}</td><td>{_esc(s.title)}</td><td>{_qt_badge(s.status)}</td>"
                       f"<td>{_esc(format_timestamp(s.captured_at, with_offset=False))}</td></tr>")
        out.append("</table>")
    else:
        out.append("<p>No steps were recorded.</p>")

    for step in session.steps:
        brk = "page-break-before:always;" if step.image else ""
        out.append(f'<h2 style="font-size:14pt; {brk}">Step {step.number} — {_esc(step.title)}</h2>')
        meta = [_qt_badge(step.status), _esc(format_timestamp(step.captured_at))]
        if step.url:
            meta.append(_esc(step.url))
        out.append(f'<p style="color:#59636e;">{" &nbsp;·&nbsp; ".join(meta)}</p>')
        rows = [(k, v) for k, v in (("Expected", step.expected), ("Actual", step.actual), ("Notes", step.notes)) if v]
        if rows:
            out.append('<table width="100%" cellspacing="0" cellpadding="5">')
            out += [f'<tr><td width="18%" bgcolor="#f6f8fa" style="color:#59636e;">{k}</td>'
                    f"<td>{_qt_text(v)}</td></tr>" for k, v in rows]
            out.append("</table>")
        if step.image:
            data, state = _integrity(session, step)
            try:  # Pillow decodes images beyond Qt's 256 MB image-reader limit
                image = load_image(data) if data else None
            except ImageError:
                image = None
            if image is None:
                out.append(f'<p style="color:#9a6700;"><b>Image file missing or unreadable: '
                           f"{_esc(step.image)}</b></p>")
                continue
            tiles = _pdf_tiles(image)
            for index, tile in enumerate(tiles):
                scale = min(PDF_IMAGE_MAX_W / tile.width(), 1.0)
                w, h = max(1, round(tile.width() * scale)), max(1, round(tile.height() * scale))
                name = f"evidence-step-{step.number}-{index}"
                doc.addResource(QTextDocument.ResourceType.ImageResource.value, QUrl(name), tile)
                if index:
                    out.append(f'<p style="page-break-before:always; color:#59636e;">Step {step.number} '
                               f"(continued, part {index + 1} of {len(tiles)})</p>")
                out.append(f'<p align="center" style="margin-top:8px;"><img src="{name}" width="{w}" '
                           f'height="{h}"></p>')
            caption = [_esc(step.image)]
            if step.sha256:
                caption.append(f"SHA-256 {_esc(step.sha256)}")
            if state == "modified":
                caption.append("<b>WARNING: file modified since capture</b>")
            out.append(f'<p style="color:#59636e; font-size:7pt;">{" · ".join(caption)}</p>')
    generated = format_timestamp(datetime.now().astimezone().isoformat(timespec="seconds"))
    out.append(f'<p style="color:#59636e; font-size:7pt; margin-top:16px;">Generated {_esc(generated)} by '
               f"Screenshot Tool {__version__} · session {_esc(session.id)}</p></body></html>")
    doc.setHtml("".join(out))
    return doc


def write_pdf(session: EvidenceSession, path: str | Path | None = None) -> Path:
    """Write ``report.pdf`` (A4, one page per screenshot, page numbers)."""
    target = Path(path) if path else session.path / "report.pdf"
    doc = build_pdf_document(session)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
    writer = QPdfWriter(str(tmp))
    # Layout units only: images keep their full resolution, text stays vector. At the
    # default 1200 dpi a 1-unit table border would be an invisible hairline.
    writer.setResolution(150)
    writer.setTitle(_report_title(session))
    writer.setCreator(f"Screenshot Tool {__version__}")
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Portrait,
                                     QMarginsF(0, 0, 0, 0)))
    doc.print(writer)
    del writer  # closes the file so it can be moved into place
    try:
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise EvidenceError(f"Could not write {target}.")
        for attempt in range(10):  # a PDF viewer may hold the old report open
            try:
                os.replace(tmp, target)
                break
            except PermissionError:
                if attempt < 9:
                    time.sleep(0.1)
                    continue
                try:  # some viewers allow overwriting the file but not replacing it
                    target.write_bytes(tmp.read_bytes())
                except OSError:
                    raise EvidenceError(f"Could not replace {target}: close it in your PDF viewer and "
                                        "create the report again.") from None
    finally:
        tmp.unlink(missing_ok=True)
    return target
