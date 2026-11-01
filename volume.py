"""
volume.py — true rolling market history: the last 1, 6, 12 and 24 hours.

The Wiki's bulk endpoints only cover fixed periods, and their defaults trail
well behind (/24h can end nearly two days ago). So this keeps the recent
5-minute and hourly periods for every item and adds them up:

    1 hour      the latest 12 five-minute periods
    6/12/24h    five-minute periods back to the end of the newest full hour,
                then full hours for the rest

The periods are saved in the database, so a restart only fetches what it
missed. The first run fetches them one at a time, in the background. After
that it's about one request every 5 minutes.

Each view has the same shape as a Wiki bulk response, so the rest of the app
can use one anywhere it would use that:
    {item_id_str: {avgHighPrice, avgLowPrice, highPriceVolume, lowPriceVolume}}
"""

import json
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

import api
import applog
import db

FIVE_MIN = 300
HOUR = 3600
STEP_SECONDS = {"5m": FIVE_MIN, "1h": HOUR, "6h": 6 * HOUR, "24h": 24 * HOUR}
# A couple more than the windows need, because the newest period of each
# isn't published straight away.
KEEP = {"5m": 14, "1h": 25}
UPDATE_EVERY = 60          # seconds between looking for new periods
BACKFILL_PAUSE = 1.0       # seconds between requests, to go easy on the Wiki
# Past these ages a window is too old to call "the last hour" and so on.
MAX_AGE_5M = 1200
MAX_AGE_1H = 3 * HOUR


def _trim(data: dict) -> dict:
    """Keep only what's needed from a Wiki bulk response, compactly."""
    return {sys.intern(item_id): (e.get("highPriceVolume") or 0,
                                  e.get("lowPriceVolume") or 0,
                                  e.get("avgHighPrice"), e.get("avgLowPrice"))
            for item_id, e in data.items()}


def _merge(buckets: List[dict]) -> dict:
    """Add periods together: total volume, and average prices by volume."""
    totals = {}
    for bucket in buckets:
        for item_id, (high_vol, low_vol, high, low) in bucket.items():
            t = totals.get(item_id)
            if t is None:
                t = totals[item_id] = [0, 0, 0, 0]
            if high_vol and high:
                t[0] += high_vol
                t[2] += high * high_vol
            if low_vol and low:
                t[1] += low_vol
                t[3] += low * low_vol
    return {item_id: {
        "highPriceVolume": t[0], "lowPriceVolume": t[1],
        "avgHighPrice": round(t[2] / t[0]) if t[0] else None,
        "avgLowPrice": round(t[3] / t[1]) if t[1] else None,
    } for item_id, t in totals.items()}


def _newest_run(starts, step: int) -> List[int]:
    """The newest unbroken run of period start times, newest first."""
    ordered = sorted(starts, reverse=True)
    run = ordered[:1]
    for start in ordered[1:]:
        if start != run[-1] - step:
            break
        run.append(start)
    return run


class VolumeHistory:
    """
    Reading is safe from any thread: the views are built off to the side and
    swapped in whole. on_change is called from the background thread whenever
    a new period arrives.
    """

    def __init__(self, on_change: Optional[Callable[[], None]] = None):
        self._on_change = on_change
        self._buckets = {"5m": {}, "1h": {}}   # step -> {start: trimmed data}
        self._views = {}                       # seconds -> (view, newest start)

    # ── Reading ───────────────────────────────────────────────────────────────

    def view(self, seconds: int) -> Optional[dict]:
        """A rolling window, or None if it isn't complete and recent."""
        found = self._views.get(seconds)
        if not found:
            return None
        data, newest = found
        limit = MAX_AGE_5M if seconds <= HOUR else MAX_AGE_1H
        return data if time.time() - newest <= limit else None

    def sides(self, item_id, seconds: int) -> Optional[Tuple[int, int]]:
        """(instant buys, instant sells) in a window. None = window not ready;
        an item with no trades in a ready window is (0, 0)."""
        data = self.view(seconds)
        if data is None:
            return None
        entry = data.get(str(item_id))
        if not entry:
            return 0, 0
        return entry["highPriceVolume"], entry["lowPriceVolume"]

    def slower_side(self, item_id, seconds: int) -> Optional[int]:
        sides = self.sides(item_id, seconds)
        return min(sides) if sides else None

    def ready_windows(self) -> frozenset:
        return frozenset(s for s in self._views if self.view(s) is not None)

    def progress(self) -> Tuple[int, int]:
        """(periods held, periods wanted), for showing the first download."""
        have = sum(len(b) for b in self._buckets.values())
        return min(have, sum(KEEP.values())), sum(KEEP.values())

    # ── Updating ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True,
                         name="volume-history").start()

    def _run(self) -> None:
        self._load()
        while True:
            try:
                self.update()
            except Exception:
                applog.log.warning("Volume history update failed", exc_info=True)
            time.sleep(UPDATE_EVERY)

    def _load(self) -> None:
        """Pick up what was saved last time."""
        try:
            for step, start, text in db.load_volume_buckets():
                if step in self._buckets:
                    self._buckets[step][start] = {
                        sys.intern(k): tuple(v) for k, v in json.loads(text).items()}
        except Exception:
            applog.log.warning("Could not load volume history", exc_info=True)
        self._prune()
        self._rebuild()
        self._notify()

    def _wanted(self) -> List[Tuple[str, int]]:
        """Periods we should have but don't, newest first within each step."""
        now = int(time.time())
        missing = []
        for step, count in KEEP.items():
            size = STEP_SECONDS[step]
            current = now - now % size    # still in progress, so never ready
            missing += [(step, current - back * size)
                        for back in range(1, count + 1)
                        if current - back * size not in self._buckets[step]]
        return missing

    def update(self) -> None:
        """Fetch every period we're missing. A newest period that isn't
        published yet comes back empty and is simply tried again next time."""
        missing = self._wanted()
        for number, (step, start) in enumerate(missing):
            if number:
                time.sleep(BACKFILL_PAUSE)
            data = api.get_bulk_bucket(step, start)
            if not data:
                continue
            trimmed = _trim(data)
            db.save_volume_bucket(step, start, json.dumps(
                trimmed, separators=(",", ":")))
            self._buckets[step][start] = trimmed
            self._prune()
            self._rebuild()
            self._notify()

    def _prune(self) -> None:
        now = int(time.time())
        for step, count in KEEP.items():
            size = STEP_SECONDS[step]
            cutoff = now - now % size - count * size
            for start in [s for s in self._buckets[step] if s < cutoff]:
                del self._buckets[step][start]
            db.delete_volume_buckets_before(step, cutoff)

    def _rebuild(self) -> None:
        fives, hours = self._buckets["5m"], self._buckets["1h"]
        five_run = _newest_run(fives, FIVE_MIN)
        hour_run = _newest_run(hours, HOUR)
        views: Dict[int, tuple] = {}
        if five_run:
            views[FIVE_MIN] = (_merge([fives[five_run[0]]]), five_run[0])
        if len(five_run) >= 12:
            views[HOUR] = (_merge([fives[s] for s in five_run[:12]]), five_run[0])
        if hour_run:
            # Five-minute periods since the newest full hour, if they join up.
            hour_end = hour_run[0] + HOUR
            tail = [s for s in five_run if s >= hour_end]
            if tail and tail[-1] != hour_end:
                tail = []
            for seconds in (6 * HOUR, 12 * HOUR, 24 * HOUR):
                need = round((seconds - len(tail) * FIVE_MIN) / HOUR)
                if len(hour_run) >= need:
                    views[seconds] = (
                        _merge([fives[s] for s in tail]
                               + [hours[s] for s in hour_run[:need]]),
                        hour_run[0])
        self._views = views

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()
