#!/usr/bin/env bash
# launch.sh — set up a project-local virtualenv (Homebrew Python 3.12)
# and run the GUI.
set -e
cd "$(dirname "$0")"

PY=/opt/homebrew/opt/python@3.12/bin/python3.12
VENV=.venv

if [ ! -x "$PY" ]; then
    echo "Homebrew Python 3.12 not found at $PY"
    echo "Install with:  brew install python@3.12 python-tk@3.12"
    exit 1
fi

echo "==> Verifying Tkinter on base Python..."
"$PY" -c 'import tkinter; r = tkinter.Tk(); print("Tk:", r.tk.eval("info patchlevel")); r.destroy()'

if [ ! -d "$VENV" ]; then
    echo "==> Creating virtualenv at $VENV"
    "$PY" -m venv "$VENV"
fi

VPY="$VENV/bin/python"

echo "==> Installing dependencies into venv..."
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r requirements.txt

echo "==> Launching GUI..."
exec "$VPY" gui.py
