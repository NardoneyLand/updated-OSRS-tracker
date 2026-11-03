#!/usr/bin/env bash
# launch.sh — set up a project-local virtualenv and run the GUI, with
# Homebrew Python 3.12 on macOS or the system's Python on Linux.
set -e
cd "$(dirname "$0")"

VENV=.venv

# Says what to install on Linux, for the main families of distro.
linux_install_help() {
    echo "  Ubuntu, Debian:  sudo apt install python3 python3-tk python3-venv"
    echo "  Fedora:          sudo dnf install python3 python3-tkinter"
    echo "  Arch:            sudo pacman -S python tk"
}

if [ "$(uname)" = "Darwin" ]; then
    PY=/opt/homebrew/opt/python@3.12/bin/python3.12
    if [ ! -x "$PY" ]; then
        echo "Homebrew Python 3.12 not found at $PY"
        echo "Install with:  brew install python@3.12 python-tk@3.12"
        exit 1
    fi
else
    PY=python3
    if ! command -v "$PY" >/dev/null; then
        echo "Python 3 not found. Install it with:"
        linux_install_help
        exit 1
    fi
    if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        echo "The app needs Python 3.10 or newer; this is $("$PY" --version)."
        exit 1
    fi
    if ! "$PY" -c 'import tkinter' 2>/dev/null; then
        echo "Python's Tk support (tkinter) isn't installed. Install it with:"
        linux_install_help
        exit 1
    fi
    if [ ! -d "$VENV" ] && ! "$PY" -c 'import ensurepip' 2>/dev/null; then
        echo "Python can't create a virtualenv yet. Install its venv support with:"
        linux_install_help
        exit 1
    fi
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
