# Builds a standalone Windows executable: dist\ScreenshotTool.exe
# The exe is the tray app and, with the "mcp" argument, the MCP server for AI agents.
# Usage (from the project root):  .\build_exe.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $python -m pip install --upgrade -r requirements.txt pyinstaller==6.11.1
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# mcp_types loads its per-protocol-version modules dynamically, so collect them all.
& $python -m PyInstaller `
    --noconfirm --clean `
    --onefile `
    --windowed `
    --name ScreenshotTool `
    --paths . `
    --icon screenshot_tool/assets/icon.ico `
    --add-data "screenshot_tool/assets;assets" `
    --hidden-import pynput.keyboard._win32 `
    --hidden-import pynput.mouse._win32 `
    --collect-submodules mcp_types `
    screenshot_tool\__main__.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

Write-Host "`nBuilt: $(Resolve-Path dist\ScreenshotTool.exe)" -ForegroundColor Green
