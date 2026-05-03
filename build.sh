#!/usr/bin/env bash
# build.sh — build the OSRS Tracker macOS .app bundle using the
# project-local virtualenv (.venv).
#
# Usage:
#   ./build.sh                 # full clean build
#   ./build.sh --no-clean      # incremental
#
# Output: dist/OSRS Tracker.app
set -euo pipefail
cd "$(dirname "$0")"

BASE_PY=/opt/homebrew/opt/python@3.12/bin/python3.12
VENV=.venv
VPY="$VENV/bin/python"

if [ ! -d "$VENV" ]; then
    echo "==> Creating virtualenv at $VENV"
    "$BASE_PY" -m venv "$VENV"
fi

echo "==> Using $VPY"
"$VPY" --version

echo "==> Installing/updating dependencies..."
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r requirements.txt

echo "==> Generating icon (if icon.png present)..."
./make_icns.sh

if [[ "${1:-}" != "--no-clean" ]]; then
    echo "==> Cleaning previous build artifacts..."
    rm -rf build dist
fi

echo "==> Running PyInstaller..."
"$VPY" -m PyInstaller --clean OSRS_Tracker.spec

if [[ -d "dist/OSRS Tracker.app" ]]; then
    SIZE=$(du -sh "dist/OSRS Tracker.app" | cut -f1)
    echo ""
    echo "==> Build complete!"
    echo "    Bundle: dist/OSRS Tracker.app  ($SIZE)"
    echo "    Open with: open 'dist/OSRS Tracker.app'"
elif [[ -d "dist/OSRS Tracker" ]]; then
    echo ""
    echo "==> Build complete (non-macOS): dist/OSRS Tracker/"
else
    echo "!! Build did not produce an expected output. Check pyinstaller logs above." >&2
    exit 1
fi
