"""
version.py — single source of truth for the app version and User-Agent.

The release workflow overwrites APP_VERSION from the git tag (v1.2.0 -> 1.2.0)
before building, so release builds always report the tagged version.
"""

APP_VERSION = "1.0.0"

REPO_URL = "https://github.com/NardoneyLand/updated-OSRS-tracker"

# The OSRS Wiki asks every app to send a descriptive User-Agent with a way to
# contact the maintainer. The Issues page is that contact, so no personal
# details ship in the build. If you fork this project, point it at your repo.
USER_AGENT = f"OSRS-Price-Tracker/{APP_VERSION} (+{REPO_URL}/issues)"
