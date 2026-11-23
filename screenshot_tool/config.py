"""Persistent user configuration stored as a small JSON file."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

APP_DIR_NAME = "screenshot_tool"
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
REPORT_FORMATS = ("html", "pdf", "md")


def default_config_path() -> Path:
    """Return the platform-appropriate location of ``config.json``."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_DIR_NAME / "config.json"


@dataclass
class Config:
    """User settings. Unknown or invalid values in the file fall back to defaults."""

    hotkey: str = "ctrl+shift+s"
    default_color: str = "#ff3b30"
    thickness: int = 4
    save_folder: str = ""
    evidence_folder: str = ""
    tester: str = ""
    report_formats: list[str] = field(default_factory=lambda: list(REPORT_FORMATS))
    stamp_images: bool = True
    ask_step_details: bool = True
    active_session: str = ""
    path: Path = field(default_factory=default_config_path, repr=False, compare=False)

    _KEYS = ("hotkey", "default_color", "thickness", "save_folder", "evidence_folder", "tester",
             "report_formats", "stamp_images", "ask_step_details", "active_session")

    def to_dict(self) -> dict[str, Any]:
        """Serialisable view of the persisted fields."""
        return {k: getattr(self, k) for k in self._KEYS}

    def save(self) -> bool:
        """Write the config to disk. Returns False (and logs) on failure."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
            return True
        except OSError as exc:
            log.warning("Could not write config %s: %s", self.path, exc)
            return False

    def resolved_save_folder(self) -> Path:
        """Folder to offer in the save dialog (falls back to Pictures, then home)."""
        if self.save_folder:
            folder = Path(self.save_folder).expanduser()
            if folder.is_dir():
                return folder
        pictures = Path.home() / "Pictures"
        return pictures if pictures.is_dir() else Path.home()

    def evidence_root(self) -> Path:
        """Folder that holds evidence sessions (default ``<Documents>/QA-Evidence``)."""
        if self.evidence_folder:
            return Path(self.evidence_folder).expanduser()
        from .evidence import default_evidence_root  # local import keeps config lightweight

        return default_evidence_root()


def _coerce(data: dict[str, Any]) -> dict[str, Any]:
    """Keep only valid values from raw JSON data."""
    out: dict[str, Any] = {}
    hotkey = data.get("hotkey")
    if isinstance(hotkey, str) and hotkey.strip():
        out["hotkey"] = hotkey.strip().lower()
    elif "hotkey" in data:
        log.warning("Ignoring invalid 'hotkey' in config")

    color = data.get("default_color")
    if isinstance(color, str) and _COLOR_RE.match(color):
        out["default_color"] = color.lower()
    elif "default_color" in data:
        log.warning("Ignoring invalid 'default_color' (expected '#rrggbb')")

    thickness = data.get("thickness")
    if isinstance(thickness, int) and not isinstance(thickness, bool) and 1 <= thickness <= 50:
        out["thickness"] = thickness
    elif "thickness" in data:
        log.warning("Ignoring invalid 'thickness' (expected integer 1-50)")

    for key in ("save_folder", "evidence_folder", "tester", "active_session"):
        value = data.get(key)
        if isinstance(value, str):
            out[key] = value
        elif key in data:
            log.warning("Ignoring invalid '%s'", key)

    for key in ("stamp_images", "ask_step_details"):
        value = data.get(key)
        if isinstance(value, bool):
            out[key] = value
        elif key in data:
            log.warning("Ignoring invalid '%s' (expected true or false)", key)

    formats = data.get("report_formats")
    if isinstance(formats, list) and formats and all(
        isinstance(f, str) and f.lower() in REPORT_FORMATS for f in formats
    ):
        out["report_formats"] = list(dict.fromkeys(f.lower() for f in formats))
    elif "report_formats" in data:
        log.warning("Ignoring invalid 'report_formats' (expected a list of %s)", ", ".join(REPORT_FORMATS))
    return out


def load_config(path: Path | str | None = None) -> Config:
    """Load the config, creating a default file if none exists."""
    cfg_path = Path(path) if path else default_config_path()
    cfg = Config(path=cfg_path)
    if not cfg_path.exists():
        cfg.save()
        return cfg
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("top-level JSON value must be an object")
    except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
        log.warning("Could not read config %s (%s); using defaults", cfg_path, exc)
        return cfg
    for key, value in _coerce(raw).items():
        setattr(cfg, key, value)
    return cfg
