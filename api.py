"""
api.py — Wrapper for the OSRS Wiki Prices API.
Docs: https://prices.runescape.wiki/api/v1/osrs
"""

import json
import os
import time
from typing import Optional

import requests

API_BASE = "https://prices.runescape.wiki/api/v1/osrs"
HEADERS = {
    "User-Agent": "OSRS Price Tracker - personal project, @nardoneyland on discord"
}
MAPPING_FILE = "mapping.json"
MAPPING_MAX_AGE = 604_800  # 7 days


def get_mapping() -> list:
    """
    Return the full item mapping, refreshing the local cache if it is
    missing or older than MAPPING_MAX_AGE seconds.
    """
    needs_refresh = True

    if os.path.exists(MAPPING_FILE):
        age = time.time() - os.path.getmtime(MAPPING_FILE)
        if age < MAPPING_MAX_AGE:
            needs_refresh = False

    if needs_refresh:
        r = requests.get(f"{API_BASE}/mapping", headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        with open(MAPPING_FILE, "w") as fh:
            json.dump(data, fh)
        return data
    else:
        with open(MAPPING_FILE, "r") as fh:
            return json.load(fh)


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
