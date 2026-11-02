"""
settings.py — tiny JSON store for user preferences (capital, filters).
"""

import json
import os
import time

import paths

SETTINGS_FILE = paths.data_file("settings.json")
READ_ATTEMPTS = 3             # antivirus and backup tools only hold files briefly
LAUNCH_READ_ATTEMPTS = 21     # starting up can wait about 2 seconds for them
READ_RETRY_SECONDS = 0.1


def _read(attempts: int = READ_ATTEMPTS) -> dict:
    """What's in the settings file, or nothing if there isn't one yet. Raises
    OSError if it's there but can't be read, or ValueError if it's damaged."""
    problem = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(READ_RETRY_SECONDS)
        try:
            with open(SETTINGS_FILE, "r") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            problem = exc
            continue
        if isinstance(data, dict):
            return data
        problem = ValueError("not a settings file")
    raise problem


def load() -> dict:
    try:
        return _read()
    except (OSError, ValueError):
        return {}


def _set_aside_damaged() -> None:
    """A damaged file can never be read, so keep a copy and start fresh."""
    os.replace(SETTINGS_FILE, SETTINGS_FILE + ".bad")


def check_at_launch() -> bool:
    """Run before anything reads the settings. True if the file was damaged and
    has been set aside. Raises OSError if it can't be read, so the app never
    starts from blanks that it could then save over the real settings."""
    try:
        _read(LAUNCH_READ_ATTEMPTS)
    except ValueError:
        _set_aside_damaged()
        return True
    return False


def save(values: dict) -> None:
    """Add values to the settings file. Writing back less than the whole file
    would lose the rest, so this raises OSError, leaving the file as it was, if
    it can't be read or written right now."""
    try:
        data = _read()
    except ValueError:
        _set_aside_damaged()
        data = {}
    data.update(values)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, SETTINGS_FILE)
