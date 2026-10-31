# launch.ps1 — set up a project-local virtualenv and run the GUI on Windows.
#
# Usage (from PowerShell):
#     .\launch.ps1
#
# If PowerShell complains about script execution being disabled, either:
#   * Run once with:  powershell -ExecutionPolicy Bypass -File .\launch.ps1
#   * Or unblock for your user:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# Requires Python 3.10+ with Tkinter. The standard installer from
# python.org bundles Tk — make sure "tcl/tk and IDLE" stays checked.

# Native tools (pip, PyInstaller) log progress to stderr, which 'Stop' would treat
# as fatal on PowerShell 5.1; exit codes are checked explicitly instead.
$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot

$venv = '.venv'

# --- Locate a usable Python interpreter ----------------------------------
# Prefer the Python launcher (`py`) since it's the most reliable way to find
# specific versions on Windows. Fall back to a bare `python` on PATH.

. (Join-Path $PSScriptRoot 'find-python.ps1')
$py = Find-Python

if (-not $py) {
    Write-Host ''
    Write-Host 'Python 3.10+ not found.' -ForegroundColor Red
    Write-Host 'Install Python 3.12 from https://www.python.org/downloads/'
    Write-Host 'During install: check "Add python.exe to PATH" and keep "tcl/tk and IDLE" enabled.'
    exit 1
}
$pythonExe = $py.Exe
$pythonArgs = $py.Args

Write-Host "==> Using Python: $pythonExe $($pythonArgs -join ' ')"
& $pythonExe @pythonArgs --version

# --- Create venv (first run only) -----------------------------------------
if (-not (Test-Path $venv)) {
    Write-Host "==> Creating virtualenv at $venv"
    & $pythonExe @pythonArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host '!! Creating the virtualenv failed.' -ForegroundColor Red
        exit 1
    }
}

$vpy = Join-Path $venv 'Scripts\python.exe'

# --- Verify Tkinter works (in the interpreter that will run the app) ------
Write-Host '==> Verifying Tkinter...'
& $vpy -c "import tkinter; r = tkinter.Tk(); print('Tk:', r.tk.eval('info patchlevel')); r.destroy()"
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host '!! Tkinter is not working in this Python install.' -ForegroundColor Red
    Write-Host '   Reinstall Python from python.org with "tcl/tk and IDLE" enabled.'
    exit 1
}

# --- Install dependencies -------------------------------------------------
Write-Host '==> Installing dependencies into venv...'
& $vpy -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host '!! pip upgrade failed (offline?).' -ForegroundColor Red
    exit 1
}
& $vpy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host '!! Installing requirements failed.' -ForegroundColor Red
    exit 1
}

# --- Launch the GUI -------------------------------------------------------
Write-Host '==> Launching GUI...'
& $vpy gui.py
