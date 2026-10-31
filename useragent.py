"""
useragent.py — the per-install User-Agent sent to the OSRS Wiki and news feed.

The Wiki asks every app to identify itself with a way to contact whoever is
running it. Each user enters their own contact on first launch (stored in
settings.json), so no developer details ship in the build:

    OSRS-Price-Tracker/1.0.0 (contact: their_name)

HEADERS is a single shared dict: api.py and scraper.py use it directly, so
set_contact() updates every request at once.
"""

import re

from version import APP_VERSION

APP_TOKEN = f"OSRS-Price-Tracker/{APP_VERSION}"
CONTACT_MIN_LEN = 3
CONTACT_MAX_LEN = 60

HEADERS = {"User-Agent": f"{APP_TOKEN} (unconfigured)"}
_configured = False


class ContactNotSetError(RuntimeError):
    """Raised if a network request is attempted before setup is complete."""


def clean_contact(text: str) -> str:
    """
    Make user input safe for an HTTP header: printable ASCII only (no line
    breaks, emoji or non-Latin text), no parentheses/backslashes/quotes that
    would break the User-Agent comment, single spaces, max 60 characters.
    """
    t = re.sub(r"\s", " ", text or "")       # line breaks/tabs -> spaces
    t = "".join(ch for ch in t if 32 <= ord(ch) < 127)
    t = re.sub(r'[()\\"]', "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:CONTACT_MAX_LEN].strip()


def is_valid_contact(text: str) -> bool:
    return len(clean_contact(text)) >= CONTACT_MIN_LEN


def build(contact: str) -> str:
    return f"{APP_TOKEN} (contact: {clean_contact(contact)})"


def set_contact(contact: str) -> None:
    global _configured
    if not is_valid_contact(contact):
        raise ValueError("contact must be at least "
                         f"{CONTACT_MIN_LEN} usable characters")
    HEADERS["User-Agent"] = build(contact)
    _configured = True


def require() -> None:
    """Call before any network request."""
    if not _configured:
        raise ContactNotSetError(
            "Set your contact (Help tab) before fetching data.")
