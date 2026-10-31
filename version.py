"""
version.py — single source of truth for the app version.

The release workflow overwrites APP_VERSION from the git tag (v1.2.0 -> 1.2.0)
before building, so release builds always report the tagged version.
"""

APP_VERSION = "1.0.0"
