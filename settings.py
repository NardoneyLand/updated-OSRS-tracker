"""
settings.py — tiny JSON store for user preferences (capital, filters).
"""

import json
import os

import paths

SETTINGS_FILE = paths.data_file("settings.json")


def load() -> dict:
    try:
        with open(SETTINGS_FILE, "r") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(values: dict) -> None:
    data = load()
    data.update(values)
    tmp = SETTINGS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, SETTINGS_FILE)
