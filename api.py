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
from version import USER_AGENT

API_BASE = "https://prices.runescape.wiki/api/v1/osrs"
HEADERS = {"User-Agent": USER_AGENT}  # defined in version.py
MAPPING_FILE = paths.data_file("mapping.json")
MAPPING_MAX_AGE = 604_800  # 7 days


def get_mapping() -> list:
    """
    Return the full item mapping, refreshing the local cache if it is
    missing, unreadable, or older than MAPPING_MAX_AGE seconds.
    """
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

    r = requests.get(f"{API_BASE}/mapping", headers=HEADERS, timeout=15)
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
    try:
        r = requests.get(f"{API_BASE}/latest?id={item_id}", headers=HEADERS, timeout=10)
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
    try:
        r = requests.get(
            f"{API_BASE}/timeseries?timestep={timestep}&id={item_id}",
            headers=HEADERS, timeout=15
        )
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception:
        return []


def get_all_latest_prices() -> dict:
    """Bulk fetch — returns {item_id_str: {high, low, ...}} for every item."""
    try:
        r = requests.get(f"{API_BASE}/latest", headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception:
        return {}


def get_bulk_average(timestep: str = "1h") -> dict:
    """
    Bulk averaged prices + volumes for ALL items in the most recent window.
    timestep: '5m' | '1h' | '6h' | '24h'
    Returns {item_id_str: {avgHighPrice, avgLowPrice, highPriceVolume, lowPriceVolume}}
    """
    valid = {"5m", "1h", "6h", "24h"}
    if timestep not in valid:
        raise ValueError(f"timestep must be one of {sorted(valid)}")
    try:
        r = requests.get(f"{API_BASE}/{timestep}", headers=HEADERS, timeout=25)
        r.raise_for_status()
        return r.json().get("data", {})
    except Exception:
        return {}
