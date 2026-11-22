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
    path: Path = field(default_factory=default_config_path, repr=False, compare=False)

    _KEYS = ("hotkey", "default_color", "thickness", "save_folder")

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

    folder = data.get("save_folder")
    if isinstance(folder, str):
        out["save_folder"] = folder
    elif "save_folder" in data:
        log.warning("Ignoring invalid 'save_folder'")
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
