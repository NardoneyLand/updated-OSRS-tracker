"""
settings.py — tiny JSON store for user preferences (capital, filters).
"""

import json
import os
import shutil
import time

import paths

SETTINGS_FILE = paths.data_file("settings.json")
ATTEMPTS = 3                  # antivirus and backup tools only hold files briefly
LAUNCH_ATTEMPTS = 21          # starting up can wait about 2 seconds for them
RETRY_SECONDS = 0.1

_last_read = None             # the file as last read or written
_unsaved = {}                 # changes waiting for the file to be writable


def _read(attempts: int = ATTEMPTS) -> dict:
    """What's in the settings file, or nothing if there isn't one yet. Raises
    OSError if it's there but can't be read, or ValueError if it's damaged."""
    problem = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(RETRY_SECONDS)
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
    """The current settings, including any changes still waiting to be saved.
    If the file can't be read right now, this is what it held last time."""
    global _last_read
    try:
        _last_read = _read()
    except (OSError, ValueError):
        pass
    return {**(_last_read or {}), **_unsaved}


def _damaged_copy_path() -> str:
    """Where to keep a damaged file, never replacing an earlier copy."""
    path = SETTINGS_FILE + ".bad"
    copies = 1
    while os.path.exists(path):
        copies += 1
        path = f"{SETTINGS_FILE}.bad{copies}"
    return path


def check_at_launch() -> bool:
    """Run before anything reads the settings. True if the file was damaged and
    has been set aside. Raises OSError if it can't be read, so the app never
    starts from blanks that it could then save over the real settings."""
    global _last_read
    try:
        _last_read = _read(LAUNCH_ATTEMPTS)
    except ValueError:
        os.replace(SETTINGS_FILE, _damaged_copy_path())
        _last_read = {}
        return True
    return False


def hold(values: dict) -> None:
    """Keep changes that couldn't be saved: load() includes them from now on,
    and the next save() that works writes them too."""
    global _unsaved
    _unsaved = {**_unsaved, **values}


def _replace_file(tmp: str) -> None:
    for attempt in range(ATTEMPTS):
        if attempt:
            time.sleep(RETRY_SECONDS)
        try:
            os.replace(tmp, SETTINGS_FILE)
            return
        except OSError as exc:
            problem = exc
    raise problem


def save(values: dict) -> bool:
    """Add values, and any held changes, to the settings file. Writing back less
    than the whole file would lose the rest, so this raises OSError, leaving the
    file as it was, if it can't be read or written right now. True if the file
    was damaged: a copy is kept, and it's rebuilt from the last good read."""
    global _last_read, _unsaved
    damaged = False
    try:
        data = _read()
    except ValueError:
        shutil.copyfile(SETTINGS_FILE, _damaged_copy_path())
        data = dict(_last_read or {})
        damaged = True
    data.update(_unsaved)
    data.update(values)
    tmp = SETTINGS_FILE + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        _replace_file(tmp)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _last_read = data
    _unsaved = {}
    return damaged
