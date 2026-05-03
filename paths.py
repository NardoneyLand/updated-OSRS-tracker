"""
paths.py — Resolve writable filesystem paths for app data.

When run from source, files live in the project folder for convenience.
When run from a bundled .app, files live in:
    ~/Library/Application Support/OSRS Tracker/

This avoids permission errors when the bundle's working directory is read-only.
"""

import os
import sys


def _is_bundled() -> bool:
    """True when running inside a PyInstaller-built bundle."""
    return getattr(sys, "frozen", False)


def app_data_dir() -> str:
    """
    Return the writable data directory for the app.
    Creates the directory if it doesn't exist.
    """
    if _is_bundled():
        # Mac convention. Falls back to home dir on other platforms.
        if sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        elif sys.platform == "win32":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
        else:
            base = os.path.expanduser("~/.local/share")
        path = os.path.join(base, "OSRS Tracker")
    else:
        # Dev mode: project folder
        path = os.path.dirname(os.path.abspath(__file__))

    os.makedirs(path, exist_ok=True)
    return path


def data_file(filename: str) -> str:
    """Absolute path to a file inside the app data directory."""
    return os.path.join(app_data_dir(), filename)
