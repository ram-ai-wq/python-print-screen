# Screenshot Tool

A lightweight, cross-platform screenshot tool written in Python + PyQt6.
Press a global hotkey, drag to select an area of the screen, annotate it, then copy it to the clipboard or save it as an image.

## Features

- Selects regions across all monitors, including mixed-DPI setups
- Annotation tools: arrow, rectangle, pen, highlighter and text
- Copies to the clipboard or saves as PNG / JPG
- Global hotkey (default `Ctrl+Shift+S`) and a system tray icon
- Simple JSON configuration

## Download (Windows)

You don't need Python to use the app on Windows:

1. Download `ScreenshotTool.exe` from the [Releases](../../releases) page.
2. Double-click it. A capture opens immediately and the app stays in the system tray.
3. Press `Ctrl+Shift+S` whenever you want to take another screenshot.

To start it automatically when you log in, press `Win+R`, type `shell:startup`, and put a shortcut to the exe in that folder.

## Requirements (running from source)

- Python 3.10+
- Windows, macOS, or Linux with an **X11** session (Wayland blocks global hotkeys and screen capture)

## Installation

```bash
git clone <repository-url>
cd python-print-screen
python -m venv .venv
```

Activate the virtual environment:

| OS | Command |
|----|---------|
| Windows (PowerShell) | `.\.venv\Scripts\Activate.ps1` |
| macOS / Linux | `source .venv/bin/activate` |

Then install the dependencies:

```bash
pip install -r requirements.txt
```

## Usage

Run the app **from the project root** (not from inside `screenshot_tool/`):

```bash
python -m screenshot_tool
```

This takes a capture right away and keeps running in the tray. Press the hotkey whenever you want another one.

| Option | Description |
|--------|-------------|
| `--background` | Start in the tray without capturing right away |
| `--once` | Capture once, then exit (no hotkey or tray) |
| `--config PATH` | Use a specific `config.json` |
| `-v`, `--verbose` | Turn on debug logging |
| `--version` | Print the version |

Inside the capture overlay:

1. Drag to select an area.
2. Pick an annotation tool, color and thickness from the toolbar.
3. Copy the result to the clipboard, or save it to a file.

## Configuration

The app creates a `config.json` file the first time it runs:

| OS | Location |
|----|----------|
| Windows | `%APPDATA%\screenshot_tool\config.json` |
| macOS | `~/Library/Application Support/screenshot_tool/config.json` |
| Linux | `$XDG_CONFIG_HOME/screenshot_tool/config.json` (default `~/.config/...`) |

```json
{
  "hotkey": "ctrl+shift+s",
  "default_color": "#ff3b30",
  "thickness": 4,
  "save_folder": ""
}
```

- `hotkey`: modifiers (`ctrl`, `shift`, `alt`, `cmd`/`win`) and one key joined with `+`, for example `ctrl+alt+p` or `print_screen`
- `default_color`: a `#rrggbb` color
- `thickness`: an integer from 1 to 50
- `save_folder`: the default folder for the save dialog. If left empty, it uses `~/Pictures`

Any invalid value is ignored and falls back to its default.

## Building a standalone Windows executable

From the project root in PowerShell:

```powershell
.\build_exe.ps1
```

If script execution is blocked, run `powershell -ExecutionPolicy Bypass -File .\build_exe.ps1` instead.

The script installs the dependencies and [PyInstaller](https://pyinstaller.org) into the active environment (or `.venv` if it exists). It then writes a single-file, windowless executable to `dist\ScreenshotTool.exe`. Python is not needed on the machine that runs the exe.

> Some antivirus products flag unsigned PyInstaller executables. To avoid that when distributing the exe, code-sign it.

## Platform notes

- **macOS:** grant *Screen Recording* and *Accessibility / Input Monitoring* permissions to the app (or to your terminal) in System Settings › Privacy & Security, then restart it.
- **Linux:** needs an X11 session. On Wayland, the global hotkey and screen capture will not work.

## Development

```bash
pip install -r requirements.txt
python -m pytest
```

Project layout:

```text
screenshot_tool/
├── __main__.py   # entry point, tray icon, app controller
├── capture.py    # screen capture (mss) and coordinate helpers
├── config.py     # JSON configuration
├── hotkey.py     # global hotkey (pynput)
├── overlay.py    # full-screen selection / annotation window
├── toolbar.py    # annotation toolbar
├── tools.py      # annotation shapes and painting
└── storage.py    # image composition, clipboard and file output
tests/            # pytest suite
build_exe.ps1     # Windows executable build script
```

## Troubleshooting

**`ModuleNotFoundError: No module named 'screenshot_tool'`**: you ran `python __main__.py` from inside the package. Run `python -m screenshot_tool` from the project root instead.

**The hotkey does nothing**: another app may already be using that key combination. Choose a different `hotkey` in `config.json`.

## Contributing

Issues and pull requests are welcome. Please run `python -m pytest` before submitting.

## License

<!-- TODO: choose a license (e.g. MIT) and add a LICENSE file -->
