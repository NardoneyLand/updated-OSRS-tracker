"""
updater.py — "Check for updates" against the project's GitHub Releases.

Only files attached to a release of REPO are downloaded, over HTTPS:
    Windows: OSRS-Tracker-Setup-<version>.exe  (run silently, then the app
             closes so the installer can replace it and relaunch it)
    macOS:   OSRS-Tracker-<version>.dmg        (opened; drag to Applications)
The request to GitHub sends only the app name and version, not your contact.
"""

import os
import re
import subprocess
import sys
import tempfile
from typing import Callable, Optional

import requests

from version import APP_VERSION

REPO = "NardoneyLand/updated-OSRS-tracker"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
HEADERS = {"User-Agent": f"OSRS-Price-Tracker/{APP_VERSION}",
           "Accept": "application/vnd.github+json"}


class UpdateError(RuntimeError):
    pass


def parse_version(text: str) -> tuple:
    """'v1.2.10' -> (1, 2, 10). Non-numeric parts are ignored."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


def can_self_install() -> bool:
    """Only the packaged app can install updates; source runs can't."""
    return bool(getattr(sys, "frozen", False)) and sys.platform in (
        "win32", "darwin")


def latest_release() -> Optional[dict]:
    """
    {'version', 'tag', 'page', 'asset'} for the newest published release, or
    None if there are no releases yet. 'asset' is this platform's download
    ({'name', 'url', 'size'}) or None.
    """
    try:
        r = requests.get(API_LATEST, headers=HEADERS, timeout=15)
    except requests.RequestException as exc:
        raise UpdateError(f"Couldn't reach GitHub: {exc}") from exc
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise UpdateError(f"GitHub returned HTTP {r.status_code}.")
    data = r.json()
    tag = data.get("tag_name", "")
    return {
        "version": ".".join(str(n) for n in parse_version(tag)),
        "tag": tag,
        "page": data.get("html_url") or RELEASES_PAGE,
        "asset": _pick_asset(data.get("assets") or []),
    }


def _pick_asset(assets: list) -> Optional[dict]:
    if sys.platform == "win32":
        ok = lambda n: n.startswith("OSRS-Tracker-Setup-") and n.endswith(".exe")
    elif sys.platform == "darwin":
        ok = lambda n: n.startswith("OSRS-Tracker-") and n.endswith(".dmg")
    else:
        return None
    for a in assets:
        name, url = a.get("name", ""), a.get("browser_download_url", "")
        # Only accept files attached to this repo's releases.
        if ok(name) and url.startswith(DOWNLOAD_PREFIX):
            return {"name": name, "url": url, "size": a.get("size") or 0}
    return None


def download(asset: dict,
             progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Download the asset to a temp folder; returns the file path."""
    if not asset["url"].startswith(DOWNLOAD_PREFIX):
        raise UpdateError("Refusing to download from an unexpected address.")
    dest_dir = tempfile.mkdtemp(prefix="osrs-tracker-update-")
    dest = os.path.join(dest_dir, os.path.basename(asset["name"]))
    try:
        with requests.get(asset["url"], headers=HEADERS, stream=True,
                          timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or asset["size"] or 0)
            done = 0
            with open(dest, "wb") as fh:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except requests.RequestException as exc:
        raise UpdateError(f"Download failed: {exc}") from exc
    if asset["size"] and os.path.getsize(dest) != asset["size"]:
        raise UpdateError("Download was incomplete — please try again.")
    return dest


def launch_installer(path: str) -> None:
    """
    Windows: run the installer silently; it closes this app if needed,
    installs over it and starts the new version. The caller should exit.
    macOS: open the disk image so the user can drag the app across.
    """
    if sys.platform == "win32":
        subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                          "/CLOSEAPPLICATIONS"], close_fds=True)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        raise UpdateError("Automatic install isn't supported on this system.")
