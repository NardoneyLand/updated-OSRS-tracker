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
STEP_SECONDS = api.STEP_SECONDS
# The windows shown to the user.
WINDOWS = (("1h", HOUR), ("12h", 12 * HOUR), ("24h", 24 * HOUR))
# About 2 hours of five-minute periods, so they can always reach back to the
# end of the newest published hour.
KEEP = {"5m": 26, "1h": 25}
UPDATE_EVERY = 60          # seconds between looking for new periods
BACKFILL_PAUSE = 1.0       # seconds between requests, to go easy on the Wiki

# How long ago a window may have ended and still count as current. The margin
# check uses the 5-minute view, so old averages there would mark good margins
# as Suspect: a healthy newest period ended at most about 8 minutes ago.
MAX_END_AGE = {FIVE_MIN: 480, HOUR: 1200}
MAX_END_AGE_LONG = 1800
# A period still empty this long after it ended is never coming.
PUBLISH_GRACE = {"5m": 1800, "1h": 3 * HOUR}
# Missing periods a window can absorb. Those numbers read a little low, so the
# app marks them with * rather than leaving the column blank for up to a day.
MAX_GAPS = {HOUR: 1, 6 * HOUR: 1, 12 * HOUR: 2, 24 * HOUR: 2}

# Before the rolling views are ready: how often to ask the Wiki directly, and
# how long to keep using its last good answer if a later request fails. Old
# 5-minute averages are never reused (see MAX_END_AGE).
FALLBACK_EVERY = {"5m": 120, "1h": 300, "6h": 300, "24h": 300}
FALLBACK_KEEP = {"5m": 0, "1h": 2 * HOUR, "6h": 2 * HOUR, "24h": 2 * HOUR}


def _trim(data: dict) -> dict:
    """Keep only what's needed from a Wiki bulk response, compactly."""
    return {sys.intern(item_id): (e.get("highPriceVolume") or 0,
                                  e.get("lowPriceVolume") or 0,
                                  e.get("avgHighPrice"), e.get("avgLowPrice"))
            for item_id, e in data.items()}


def _parse_saved(text: str) -> dict:
    """A saved period, back in trimmed form. Raises if it's damaged."""
    period = {}
    for item_id, entry in json.loads(text).items():
        if len(entry) != 4 or not all(v is None or isinstance(v, (int, float))
                                      for v in entry):
            raise ValueError("bad entry")
        period[sys.intern(item_id)] = tuple(entry)
    return period


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
    swapped in whole. on_change is called from the background thread, so it
    must not touch the window directly.
    """

    def __init__(self, on_change: Optional[Callable[[], None]] = None):
        self._on_change = on_change
        self._buckets = {"5m": {}, "1h": {}}   # step -> {start: trimmed data}
        self._views = {}                       # seconds -> (view, end, partial)
        self._fallbacks = {}                   # step -> (data, asked_at, got_at)

    # ── Reading ───────────────────────────────────────────────────────────────

    def view(self, seconds: int) -> Optional[dict]:
        """A rolling window, or None if it isn't complete and recent."""
        found = self._current(seconds)
        return found[0] if found else None

    def is_partial(self, seconds: int) -> bool:
        """True if a ready window is missing a period, so it reads a bit low."""
        found = self._current(seconds)
        return bool(found and found[2])

    def _current(self, seconds: int) -> Optional[tuple]:
        """(view, end, partial) if ready. Reads _views only once, because the
        background thread can swap in new ones at any moment."""
        found = self._views.get(seconds)
        if not found:
            return None
        limit = MAX_END_AGE.get(seconds, MAX_END_AGE_LONG)
        return found if time.time() - found[1] <= limit else None

    def averages(self, step: str) -> dict:
        """
        A /5m, /1h, /6h or /24h response for anything that needs one: the
        rolling window if it's ready, otherwise the Wiki's newest full period,
        asked for no more often than FALLBACK_EVERY.
        """
        data = self.view(STEP_SECONDS[step])
        if data is not None:
            return data
        now = time.time()
        cached, asked_at, got_at = self._fallbacks.get(step, ({}, 0, 0))
        keep = max(FALLBACK_EVERY[step], FALLBACK_KEEP[step])
        usable = cached and now - got_at <= keep
        if not usable or now - asked_at >= FALLBACK_EVERY[step]:
            fresh = api.get_bulk_average(step)
            if fresh:
                cached, got_at = fresh, now
            self._fallbacks[step] = (cached, now, got_at)
        return cached if now - got_at <= keep else {}

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
        loaded = False
        while True:
            try:
                if not loaded:
                    self._load()
                    loaded = True
                self.update()
            except Exception:
                applog.log.warning("Volume history update failed", exc_info=True)
            time.sleep(UPDATE_EVERY)

    def _load(self) -> None:
        """Pick up what was saved last time."""
        for step, start, text in db.load_volume_buckets():
            try:
                if step in self._buckets:
                    self._buckets[step][start] = _parse_saved(text)
            except (ValueError, TypeError, AttributeError):
                applog.log.warning(f"Discarding a damaged {step} volume period")
                db.delete_volume_bucket(step, start)
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
        """
        Fetch every period we're missing. A newest period that isn't published
        yet comes back empty and is tried again next time; one that's still
        empty long after it ended is saved as a known gap and left alone.
        """
        self._prune()
        missing = self._wanted()
        changed = False
        try:
            for number, (step, start) in enumerate(missing):
                if number:
                    time.sleep(BACKFILL_PAUSE)
                data = api.get_bulk_bucket(step, start)
                ended = start + STEP_SECONDS[step]
                if data or time.time() - ended >= PUBLISH_GRACE[step]:
                    trimmed = _trim(data)
                    db.save_volume_bucket(step, start, json.dumps(
                        trimmed, separators=(",", ":")))
                    self._buckets[step][start] = trimmed
                    changed = True
                # The views can only change once a step's periods are all in.
                last_of_step = (number + 1 == len(missing)
                                or missing[number + 1][0] != step)
                if changed and last_of_step:
                    self._rebuild()
                    changed = False
                self._notify()
        finally:
            if changed:             # a request failed partway through
                self._rebuild()
                self._notify()

    def _prune(self) -> None:
        now = int(time.time())
        for step, count in KEEP.items():
            size = STEP_SECONDS[step]
            cutoff = now - now % size - count * size
            stale = [s for s in self._buckets[step] if s < cutoff]
            for start in stale:
                del self._buckets[step][start]
            if stale:
                db.delete_volume_buckets_before(step, cutoff)

    def _rebuild(self) -> None:
        fives, hours = self._buckets["5m"], self._buckets["1h"]
        five_run = _newest_run(fives, FIVE_MIN)
        hour_run = _newest_run(hours, HOUR)
        views: Dict[int, tuple] = {}

        def add(seconds: int, periods: List[dict], end: int) -> None:
            gaps = sum(1 for period in periods if not period)
            if gaps <= MAX_GAPS[seconds]:
                views[seconds] = (_merge(periods), end, gaps > 0)

        if five_run:
            newest_end = five_run[0] + FIVE_MIN
            views[FIVE_MIN] = (_merge([fives[five_run[0]]]), newest_end, False)
            if len(five_run) >= 12:
                add(HOUR, [fives[s] for s in five_run[:12]], newest_end)
        if hour_run:
            # Five-minute periods since the newest full hour, if they join up.
            hour_end = hour_run[0] + HOUR
            tail = [s for s in five_run if s >= hour_end]
            if tail and tail[-1] != hour_end:
                tail = []
            end = tail[0] + FIVE_MIN if tail else hour_end
            for seconds in (6 * HOUR, 12 * HOUR, 24 * HOUR):
                need = round((seconds - len(tail) * FIVE_MIN) / HOUR)
                if len(hour_run) >= need:
                    add(seconds, [fives[s] for s in tail]
                        + [hours[s] for s in hour_run[:need]], end)
        self._views = views

    def _notify(self) -> None:
        if self._on_change:
            self._on_change()
