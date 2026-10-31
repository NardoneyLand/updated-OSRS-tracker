# build.ps1 — build the OSRS Tracker Windows .exe using the project-local venv.
#
# Usage:
#     .\build.ps1                # full clean build
#     .\build.ps1 -NoClean       # incremental (skip wiping build/ and dist/)
#
# Output:
#     dist\OSRS Tracker.exe      # single self-contained executable
#
# If you have an icon source (icon.png or icon.jpg) in the project root,
# this script will use Pillow to convert it to icon.ico the first time —
# Pillow is installed only into .venv, so the bundled .exe stays slim.

param(
    [switch]$NoClean
)

# Native tools (pip, PyInstaller) log progress to stderr, which 'Stop' would treat
# as fatal on PowerShell 5.1; exit codes are checked explicitly instead.
$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot

$venv = '.venv'

# --- Locate Python (same logic as launch.ps1) ----------------------------
. (Join-Path $PSScriptRoot 'find-python.ps1')
$py = Find-Python

if (-not $py) {
    Write-Host 'Python 3.10+ not found. Install from https://www.python.org/downloads/' -ForegroundColor Red
    exit 1
}
$pythonExe = $py.Exe
$pythonArgs = $py.Args

function Assert-Ok([string]$what) {
    if ($LASTEXITCODE -ne 0) {
        Write-Host "!! $what failed (exit code $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
}

# --- Set up venv ----------------------------------------------------------
if (-not (Test-Path $venv)) {
    Write-Host "==> Creating virtualenv at $venv"
    & $pythonExe @pythonArgs -m venv $venv
    Assert-Ok 'Creating the virtualenv'
}

$vpy = Join-Path $venv 'Scripts\python.exe'

Write-Host "==> Using $vpy"
& $vpy --version

Write-Host '==> Installing/updating dependencies...'
& $vpy -m pip install --quiet --upgrade pip
Assert-Ok 'pip upgrade'
& $vpy -m pip install --quiet -r requirements.txt
Assert-Ok 'Installing requirements'

# --- Convert icon source to icon.ico if needed ---------------------------
# Regenerate icon.ico whenever it is missing or older than the source image.
$iconSrc = $null
foreach ($candidate in 'icon.png','icon.jpg','icon.jpeg','icon.PNG','icon.JPG','icon.JPEG') {
    if (Test-Path $candidate) {
        $iconSrc = $candidate
        break
    }
}

if ($iconSrc -and ((-not (Test-Path 'icon.ico')) -or
        ((Get-Item $iconSrc).LastWriteTime -gt (Get-Item 'icon.ico').LastWriteTime))) {
    Write-Host "==> Converting $iconSrc to icon.ico..."
    & $vpy -m pip install --quiet Pillow
    Assert-Ok 'Installing Pillow'
    # Pad to a square canvas first so non-square sources aren't distorted.
    $convertCode = @"
from PIL import Image
img = Image.open(r'$iconSrc').convert('RGBA')
side = max(img.size)
canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
canvas.save('icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
print('Wrote icon.ico')
"@
    & $vpy -c $convertCode
    Assert-Ok 'Converting the icon'
} elseif (Test-Path 'icon.ico') {
    Write-Host '==> Using existing icon.ico.'
} else {
    Write-Host '==> No icon source found — building without an icon.'
}

# --- Clean previous build (unless -NoClean) ------------------------------
if (-not $NoClean) {
    Write-Host '==> Cleaning previous build artifacts...'
    if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
    if (Test-Path 'dist')  { Remove-Item -Recurse -Force 'dist' }
}

# --- Run PyInstaller -----------------------------------------------------
Write-Host '==> Running PyInstaller...'
& $vpy -m PyInstaller --clean OSRS_Tracker.spec
Assert-Ok 'PyInstaller'

# --- Report --------------------------------------------------------------
$exePath = 'dist\OSRS Tracker.exe'
if (Test-Path $exePath) {
    $sizeMB = [math]::Round((Get-Item $exePath).Length / 1MB, 1)
    Write-Host ''
    Write-Host '==> Build complete!' -ForegroundColor Green
    Write-Host "    Output: $exePath ($sizeMB MB)"
    Write-Host '    Test with:  .\dist\"OSRS Tracker.exe"'
} else {
    Write-Host '!! Build did not produce the expected .exe — check pyinstaller logs above.' -ForegroundColor Red
    exit 1
}
