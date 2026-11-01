"""
api.py — Wrapper for the OSRS Wiki Prices API.
Docs: https://prices.runescape.wiki/api/v1/osrs
"""

import json
import os
import time
from typing import Optional

import requests

import paths
import useragent

API_BASE = "https://prices.runescape.wiki/api/v1/osrs"
HEADERS = useragent.HEADERS  # shared; per-install contact, see useragent.py
MAPPING_FILE = paths.data_file("mapping.json")
MAPPING_MAX_AGE = 604_800  # 7 days
STEP_SECONDS = {"5m": 300, "1h": 3600, "6h": 21600, "24h": 86400}
# One shared connection. A new one per request costs memory on every check.
SESSION = requests.Session()


def get_mapping() -> list:
    """
    Return the full item mapping, refreshing the local cache if it is
    missing, unreadable, or older than MAPPING_MAX_AGE seconds.
    """
    useragent.require()
    if os.path.exists(MAPPING_FILE):
        age = time.time() - os.path.getmtime(MAPPING_FILE)
        if age < MAPPING_MAX_AGE:
            try:
                with open(MAPPING_FILE, "r") as fh:
                    data = json.load(fh)
                if data:
                    return data
            except (OSError, ValueError):
                pass  # corrupt/empty cache — fall through and re-download

    r = SESSION.get(f"{API_BASE}/mapping", headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    # Write atomically so an interrupted write can't leave a broken cache.
    tmp = MAPPING_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, MAPPING_FILE)
    return data


def get_latest_price(item_id: int) -> Optional[dict]:
    """Latest high / low for a single item."""
    useragent.require()
    try:
        r = SESSION.get(f"{API_BASE}/latest?id={item_id}", headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()["data"].get(str(item_id))
    except Exception as exc:
        raise RuntimeError(f"Could not fetch price for id {item_id}: {exc}") from exc


def get_timeseries(item_id: int, timestep: str = "1h") -> list:
    """
    Historical price + volume data.
    timestep: '5m' | '1h' | '6h' | '24h'
    Each point: {timestamp, avgHighPrice, avgLowPrice, highPriceVolume, lowPriceVolume}
    """
    useragent.require()
    try:
        r = SESSION.get(
            f"{API_BASE}/timeseries?timestep={timestep}&id={item_id}",
            headers=HEADERS, timeout=15
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception:
        return []


def get_all_latest_prices() -> dict:
    """Bulk fetch — returns {item_id_str: {high, low, ...}} for every item."""
    useragent.require()
    try:
        r = SESSION.get(f"{API_BASE}/latest", headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception:
        return {}


def get_bulk_bucket(timestep: str, start: int) -> dict:
    """
    Bulk averages for ALL items in the period starting at `start`. Empty if
    that period isn't published yet. Raises if the request fails.
    """
    useragent.require()
    r = SESSION.get(f"{API_BASE}/{timestep}?timestamp={int(start)}",
                    headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.json().get("data", {})


def get_bulk_average(timestep: str = "1h") -> dict:
    """
    Bulk averaged prices + volumes for ALL items in the newest full period.
    timestep: '5m' | '1h' | '6h' | '24h'
    Returns {item_id_str: {avgHighPrice, avgLowPrice, highPriceVolume, lowPriceVolume}}

    Asks for that period by time, because the plain /1h, /6h and /24h addresses
    trail a whole period behind (measured: /24h ended 41 hours ago).
    """
    useragent.require()
    if timestep not in STEP_SECONDS:
        raise ValueError(f"timestep must be one of {sorted(STEP_SECONDS)}")
    if timestep != "5m":   # /5m has no such lag
        size = STEP_SECONDS[timestep]
        now = int(time.time())
        current = now - now % size
        for back in (1, 2):
            try:
                data = get_bulk_bucket(timestep, current - back * size)
            except Exception:
                continue       # the next try may still work
            if data:
                return data
    try:
        r = SESSION.get(f"{API_BASE}/{timestep}", headers=HEADERS, timeout=25)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception:
        return {}
