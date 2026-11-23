"""Evidence-session dialogs and the session window used by manual testers."""

from __future__ import annotations

from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .config import REPORT_FORMATS, Config
from .evidence import (
    STATUSES,
    EvidenceError,
    EvidenceSession,
    EvidenceStep,
    default_tester,
    format_timestamp,
    list_sessions,
)

if TYPE_CHECKING:  # pragma: no cover
    from .app import ScreenshotApp

STATUS_TEXT = {"passed": "&Passed", "failed": "&Failed", "blocked": "&Blocked", "skipped": "&Skipped", "info": "&Info"}
STATUS_COLORS = {"passed": "#1a7f37", "failed": "#cf222e", "blocked": "#bc4c00", "skipped": "#6e7781",
                 "info": "#0969da"}
FORMAT_LABELS = {"html": "HTML (single file)", "pdf": "PDF", "md": "Markdown"}


def _status_style(color: str) -> str:
    return (
        "QPushButton { padding: 4px 10px; border: 1px solid #8c959f; border-radius: 4px; }"
        f"QPushButton:checked {{ background: {color}; color: white; border-color: {color}; font-weight: bold; }}"
    )


class StatusPicker(QWidget):
    """Row of mutually exclusive status buttons (keyboard: Alt+P/F/B/S/I)."""

    def __init__(self, value: str = "passed", parent: QWidget | None = None, no_verdict_label: bool = False) -> None:
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QPushButton] = {}
        for status in STATUSES:
            text = "&No verdict" if (status == "info" and no_verdict_label) else STATUS_TEXT[status]
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setAutoDefault(False)
            btn.setStyleSheet(_status_style(STATUS_COLORS[status]))
            self._group.addButton(btn)
            self._buttons[status] = btn
            lay.addWidget(btn)
        lay.addStretch(1)
        self.set_value(value)

    def value(self) -> str:
        for status, btn in self._buttons.items():
            if btn.isChecked():
                return status
        return "info"

    def set_value(self, status: str) -> None:
        self._buttons.get(status, self._buttons["info"]).setChecked(True)


def _thumbnail(image: QImage, size: QSize) -> QPixmap:
    return QPixmap.fromImage(image.scaled(size, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))


class StepDialog(QDialog):
    """Title, status and expected/actual results for a new or existing step."""

    def __init__(self, parent: QWidget | None, *, number: int, session_label: str,
                 step: EvidenceStep | None = None, status: str = "passed", preview: QImage | None = None) -> None:
        super().__init__(parent)
        editing = step is not None
        self.setWindowTitle(f"Edit step {number}" if editing else "Add evidence step")
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)

        if preview is not None and not preview.isNull():
            thumb = QLabel()
            thumb.setPixmap(_thumbnail(preview, QSize(480, 200)))
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setStyleSheet("border: 1px solid #d0d7de; background: #f6f8fa; padding: 4px;")
            root.addWidget(thumb)
        heading = QLabel(f"<b>Step {number}</b> · {_html(session_label)}")
        heading.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(heading)

        form = QFormLayout()
        self.title = QLineEdit(step.title if step else "")
        self.title.setPlaceholderText("What does this step show? e.g. Error message after wrong password")
        self.status = StatusPicker(step.status if step else status)
        self.expected = QLineEdit(step.expected if step else "")
        self.expected.setPlaceholderText("Expected result (optional)")
        self.actual = QLineEdit(step.actual if step else "")
        self.actual.setPlaceholderText("Actual result (optional)")
        self.notes = QLineEdit(step.notes if step else "")
        self.notes.setPlaceholderText("Notes: test data, defect ID… (optional)")
        form.addRow("&Title", self.title)
        form.addRow("Status", self.status)
        form.addRow("&Expected", self.expected)
        form.addRow("&Actual", self.actual)
        form.addRow("N&otes", self.notes)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save" if editing else "Add step")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.title.setFocus()

    def values(self) -> dict[str, str]:
        return {"title": self.title.text().strip(), "status": self.status.value(),
                "expected": self.expected.text().strip(), "actual": self.actual.text().strip(),
                "notes": self.notes.text().strip()}


class NewSessionDialog(QDialog):
    """Metadata for a new evidence session."""

    def __init__(self, parent: QWidget | None, config: Config) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("New evidence session")
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        intro = QLabel("Captures you add while the session is open become numbered test steps. "
                       "Finish the session to generate the evidence report.")
        intro.setWordWrap(True)
        root.addWidget(intro)

        last = next(iter(list_sessions(config.evidence_root(), 1)), None)  # prefill env/build
        form = QFormLayout()
        self.test_case = QLineEdit()
        self.test_case.setPlaceholderText("e.g. TC-101 or PROJ-1234")
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. Login with valid credentials")
        self.tester = QLineEdit(config.tester or default_tester())
        self.environment = QLineEdit(last.environment if last else "")
        self.environment.setPlaceholderText("e.g. Staging · Chrome 131 · Windows 11")
        self.build = QLineEdit(last.build if last else "")
        self.build.setPlaceholderText("e.g. 2.4.0-rc1")
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("Objective, preconditions, test data (optional)")
        self.description.setFixedHeight(70)
        self.stamp = QCheckBox("Add a footer with test case, time and tester to every screenshot")
        self.stamp.setChecked(config.stamp_images)
        form.addRow("Test &case ID", self.test_case)
        form.addRow("&Title", self.title)
        form.addRow("Te&ster", self.tester)
        form.addRow("&Environment", self.environment)
        form.addRow("&Build / version", self.build)
        form.addRow("&Description", self.description)
        form.addRow("", self.stamp)
        root.addLayout(form)

        folder_row = QHBoxLayout()
        self.folder = QLabel()
        self.folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        change = QPushButton("Change…")
        change.setAutoDefault(False)
        change.clicked.connect(self._change_folder)
        folder_row.addWidget(QLabel("Saved in:"))
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(change)
        root.addLayout(folder_row)
        self._update_folder()

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start session")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)
        for edit in (self.test_case, self.title):
            edit.textChanged.connect(self._validate)
        self._validate()
        self.test_case.setFocus()

    def _update_folder(self) -> None:
        path = str(self._config.evidence_root())
        self.folder.setText(self.folder.fontMetrics().elidedText(path, Qt.TextElideMode.ElideMiddle, 340))
        self.folder.setToolTip(path)

    def _change_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Evidence folder", str(self._config.evidence_root()))
        if chosen:
            self._config.evidence_folder = chosen
            self._config.save()
            self._update_folder()

    def _validate(self) -> None:
        ok = bool(self.title.text().strip() or self.test_case.text().strip())
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def values(self) -> dict[str, Any]:
        return {"title": self.title.text().strip(), "test_case_id": self.test_case.text().strip(),
                "tester": self.tester.text().strip(), "environment": self.environment.text().strip(),
                "build": self.build.text().strip(), "description": self.description.toPlainText().strip(),
                "stamp_images": self.stamp.isChecked()}


class FinishDialog(QDialog):
    """Overall result, summary and report formats."""

    def __init__(self, parent: QWidget | None, session: EvidenceSession, config: Config) -> None:
        super().__init__(parent)
        self.setWindowTitle("Finish evidence session")
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        counts = session.counts()
        summary_line = ", ".join(f"{counts[s]} {s}" for s in STATUSES if counts[s]) or "no steps yet"
        root.addWidget(QLabel(f"<b>{_html(session.label)}</b><br>{counts['total']} step(s): {summary_line}"))
        form = QFormLayout()
        self.result = StatusPicker(session.result or session.computed_result(), no_verdict_label=True)
        self.summary = QPlainTextEdit(session.summary)
        self.summary.setPlaceholderText("Conclusion: what was tested, what failed, defects raised…")
        self.summary.setFixedHeight(90)
        formats_row = QWidget()
        fl = QHBoxLayout(formats_row)
        fl.setContentsMargins(0, 0, 0, 0)
        self.formats: dict[str, QCheckBox] = {}
        for fmt in REPORT_FORMATS:
            box = QCheckBox(FORMAT_LABELS[fmt])
            box.setChecked(fmt in config.report_formats)
            self.formats[fmt] = box
            fl.addWidget(box)
        fl.addStretch(1)
        self.open_report = QCheckBox("Open the report when it is ready")
        self.open_report.setChecked(True)
        form.addRow("Result", self.result)
        form.addRow("Su&mmary", self.summary)  # Alt+S is taken by "Skipped"
        form.addRow("Reports", formats_row)
        form.addRow("", self.open_report)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Finish && create report")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self) -> None:
        if not self.selected_formats():
            QMessageBox.information(self, "Finish evidence session", "Choose at least one report format.")
            return
        self.accept()

    def selected_formats(self) -> list[str]:
        return [fmt for fmt, box in self.formats.items() if box.isChecked()]

    def values(self) -> dict[str, Any]:
        return {"result": self.result.value(), "summary": self.summary.toPlainText().strip(),
                "formats": self.selected_formats(), "open_report": self.open_report.isChecked()}


class SessionWindow(QWidget):
    """Shows the active session's steps; start, resume, edit and finish sessions."""

    closed = pyqtSignal()

    def __init__(self, controller: ScreenshotApp) -> None:
        super().__init__(None, Qt.WindowType.Window)
        self._ctl = controller
        self.setWindowTitle("Evidence session")
        self.resize(720, 560)
        self._thumbs: dict[tuple[str, str], QIcon] = {}
        root = QVBoxLayout(self)
        self._stack = QStackedWidget()
        root.addWidget(self._stack)
        self._stack.addWidget(self._build_empty_page())
        self._stack.addWidget(self._build_session_page())
        controller.sessionChanged.connect(self.refresh)
        self.refresh()

    # --------------------------------------------------------------- layout
    def _button(self, text: str, slot: Callable[[], Any], tip: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setAutoDefault(False)
        btn.setToolTip(tip)
        btn.clicked.connect(slot)
        return btn

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        title = QLabel("<h2>No evidence session</h2>"
                       "<p>Start a session for the test case you are running. Every capture "
                       f"(<b>{self._ctl.hotkey_label}</b>) is then saved as a numbered step with its status, and "
                       "finishing the session creates an HTML/PDF/Markdown report.</p>")
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(title)
        row = QHBoxLayout()
        new = self._button("New session…", self._ctl.new_session_dialog)
        new.setDefault(True)
        row.addWidget(new)
        row.addWidget(self._button("Resume selected", self._resume_selected))
        row.addWidget(self._button("Open evidence folder", self._ctl.open_evidence_folder))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Recent sessions (double-click to continue):"))
        self._recent = QListWidget()
        self._recent.itemDoubleClicked.connect(lambda item: self._ctl.resume_session(item.data(Qt.ItemDataRole.UserRole)))
        lay.addWidget(self._recent, 1)
        return page

    def _build_session_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self._header = QLabel()
        self._header.setTextFormat(Qt.TextFormat.RichText)
        self._header.setWordWrap(True)
        self._header.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._header.setOpenExternalLinks(True)
        lay.addWidget(self._header)
        top = QHBoxLayout()
        capture = self._button("Capture step", self._ctl.capture_from_window,
                               f"Select an area of the screen ({self._ctl.hotkey_label})")
        capture.setDefault(True)
        top.addWidget(capture)
        top.addWidget(self._button("Text step…", self._add_text_step, "Record a check without a screenshot"))
        top.addStretch(1)
        self._edit = self._button("Edit…", self._edit_selected)
        self._delete = self._button("Delete", self._delete_selected)
        self._up = self._button("Move up", lambda: self._move_selected(-1))
        self._down = self._button("Move down", lambda: self._move_selected(1))
        for btn in (self._edit, self._delete, self._up, self._down):
            top.addWidget(btn)
        lay.addLayout(top)
        self._steps = QListWidget()
        self._steps.setIconSize(QSize(160, 90))
        self._steps.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._steps.setSpacing(2)
        self._steps.itemDoubleClicked.connect(lambda _item: self._edit_selected())
        self._steps.currentRowChanged.connect(lambda _row: self._update_buttons())
        lay.addWidget(self._steps, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self._button("Open folder", lambda: self._ctl.open_path(self._ctl.session.path)
                                      if self._ctl.session else None))
        bottom.addWidget(self._button("Create report", self._ctl.generate_report,
                                      "Write the report without finishing the session"))
        bottom.addStretch(1)
        bottom.addWidget(self._button("Close session", self._ctl.close_session,
                                      "Stop adding captures to this session (it stays on disk)"))
        finish = self._button("Finish…", self._ctl.finish_session_dialog)
        bottom.addWidget(finish)
        lay.addLayout(bottom)
        return page

    # -------------------------------------------------------------- refresh
    def refresh(self) -> None:
        session = self._ctl.session
        if session is None:
            self.setWindowTitle("Evidence session")
            self._stack.setCurrentIndex(0)
            self._recent.clear()
            for s in list_sessions(self._ctl.config.evidence_root(), 30):
                state = (s.result or "done") if s.is_finished else "in progress"
                item = QListWidgetItem(f"{s.label} — {len(s.steps)} step(s), {state} · "
                                       f"{format_timestamp(s.created_at, with_offset=False)}")
                item.setData(Qt.ItemDataRole.UserRole, str(s.path))
                self._recent.addItem(item)
            return
        self._stack.setCurrentIndex(1)
        self.setWindowTitle(f"Evidence session — {session.label}")
        counts = session.counts()
        parts = ", ".join(f"{counts[s]} {s}" for s in STATUSES if counts[s]) or "no steps yet"
        state = f"finished: <b>{session.result}</b>" if session.is_finished else "in progress"
        folder = session.path.as_uri()
        self._header.setText(
            f"<h3 style='margin:0'>{_html(session.label)}</h3>"
            f"<p style='margin:4px 0'>{counts['total']} step(s) ({parts}) · {state}<br>"
            f"<span style='color:#59636e'>Tester: {_html(session.tester) or '—'} · "
            f"Environment: {_html(session.environment) or '—'} · <a href='{folder}'>open folder</a></span></p>"
        )
        current = self._steps.currentRow()
        self._steps.clear()
        for step in session.steps:
            item = QListWidgetItem(f"{step.number}. {step.title}\n{step.status.upper()} · "
                                   f"{format_timestamp(step.captured_at, with_offset=False)}")
            item.setForeground(QColor(STATUS_COLORS.get(step.status, "#1f2328")))
            item.setData(Qt.ItemDataRole.UserRole, step.id)  # numbers can change; ids cannot
            icon = self._icon(session, step)
            if icon is not None:
                item.setIcon(icon)
            self._steps.addItem(item)
        if self._steps.count():
            self._steps.setCurrentRow(min(max(current, 0), self._steps.count() - 1))
        self._update_buttons()

    def _placeholder(self, text: str) -> QIcon:
        key = ("placeholder", text)
        if key not in self._thumbs:
            pm = QPixmap(160, 90)
            pm.fill(QColor("#eef1f4"))
            p = QPainter(pm)
            p.setPen(QColor("#8c959f"))
            p.drawRect(0, 0, 159, 89)
            p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
            p.end()
            self._thumbs[key] = QIcon(pm)
        return self._thumbs[key]

    def _icon(self, session: EvidenceSession, step: EvidenceStep) -> QIcon | None:
        path = session.image_path(step)
        if path is None:
            return self._placeholder("text step")
        if not path.is_file():
            return self._placeholder("image missing")
        key = (str(path), step.sha256 or "")
        if key not in self._thumbs:
            image = QImage(str(path))
            if image.isNull():
                return None
            self._thumbs[key] = QIcon(_thumbnail(image, QSize(160, 90)))
        return self._thumbs[key]

    def _update_buttons(self) -> None:
        row = self._steps.currentRow()
        count = self._steps.count()
        for btn in (self._edit, self._delete):
            btn.setEnabled(row >= 0)
        self._up.setEnabled(row > 0)
        self._down.setEnabled(0 <= row < count - 1)

    # -------------------------------------------------------------- actions
    def _selected_step(self) -> EvidenceStep | None:
        """The selected step, looked up by its stable id in the *current* session file.

        The list may be out of date (an AI agent or another program can change the
        session at any time), so the row number alone must not decide which step to edit.
        """
        session, item = self._ctl.session, self._steps.currentItem()
        if session is None or item is None:
            return None
        try:
            session.reload()
            return session.step(item.data(Qt.ItemDataRole.UserRole))
        except EvidenceError as exc:
            QMessageBox.warning(self, "Evidence session", str(exc))
            self.refresh()
            return None

    def _resume_selected(self) -> None:
        item = self._recent.currentItem()
        if item is not None:
            self._ctl.resume_session(item.data(Qt.ItemDataRole.UserRole))

    def _add_text_step(self) -> None:
        session = self._ctl.session
        if session is None:
            return
        dialog = StepDialog(self, number=len(session.steps) + 1, session_label=session.label,
                            status=self._ctl.default_status)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._ctl.run_safely(lambda: session.add_step(None, source="manual", **dialog.values()))

    def _edit_selected(self) -> None:
        session, step = self._ctl.session, self._selected_step()
        if session is None or step is None:
            return
        path = session.image_path(step)
        preview = QImage(str(path)) if path and path.is_file() else None
        dialog = StepDialog(self, number=step.number, session_label=session.label, step=step, preview=preview)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._ctl.run_safely(lambda: session.update_step(step.id, **dialog.values()))

    def _delete_selected(self) -> None:
        session, step = self._ctl.session, self._selected_step()
        if session is None or step is None:
            return
        answer = QMessageBox.question(self, "Delete step",
                                      f"Delete step {step.number} “{step.title}” and its screenshot?")
        if answer == QMessageBox.StandardButton.Yes:
            self._ctl.run_safely(lambda: session.delete_step(step.id))

    def _move_selected(self, delta: int) -> None:
        session, step = self._ctl.session, self._selected_step()
        if session is None or step is None:
            return
        target = step.number + delta
        if 1 <= target <= len(session.steps):
            self._ctl.run_safely(lambda: session.move_step(step.id, target))
            self._steps.setCurrentRow(target - 1)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._ctl.session is not None:
            self._ctl.run_safely(self._ctl.session.reload)  # pick up steps added by other processes
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


def _html(text: str) -> str:
    return escape(text or "")


def open_path(path: str | Path) -> None:  # pragma: no cover - opens the desktop shell
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
