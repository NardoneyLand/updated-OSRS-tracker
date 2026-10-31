"""
applog.py — crash/error log for the app.

The packaged app has no console, so errors would otherwise vanish. Everything
goes to osrs_tracker.log in the app data folder (rotated at 1 MB, 3 backups):

    Windows: %APPDATA%\\OSRS Tracker\\osrs_tracker.log
    macOS:   ~/Library/Application Support/OSRS Tracker/osrs_tracker.log
"""

import logging
import logging.handlers
import platform
import sys
import threading

import paths
from version import APP_VERSION

LOG_FILE = paths.data_file("osrs_tracker.log")
log = logging.getLogger("osrs_tracker")


def setup() -> None:
    """Start logging to the file and catch otherwise-unhandled errors."""
    if log.handlers:
        return  # already set up
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    def on_crash(exc_type, exc, tb):
        log.critical("Unhandled error", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def on_thread_crash(args):
        log.critical(f"Unhandled error in thread {args.thread.name}",
                     exc_info=(args.exc_type, args.exc_value,
                               args.exc_traceback))

    sys.excepthook = on_crash
    threading.excepthook = on_thread_crash

    log.info(f"Starting OSRS Price Tracker {APP_VERSION} on "
             f"{platform.platform()} (Python {platform.python_version()}, "
             f"{'packaged' if getattr(sys, 'frozen', False) else 'source'})")
