"""
aitools.py — the app data an AI assistant can look up while answering.

Every tool here is read-only, and none of them can place trades or change
settings. Results are plain dicts, so ai.py can hand them to either provider.
Arguments come from the model, so each tool checks them instead of trusting
the schema.
"""

import json
import threading
import time
from typing import Callable, Optional

import analyzer
import api
import applog
import db
import search as search_module
import settings
import volume

MARKET_MAX_AGE = 60        # seconds; the Wiki asks apps not to over-fetch
HISTORY_STEP = "1h"
MOVER_WINDOWS = ("5m", "1h", "6h", "24h")
MOVER_POOL = 500           # biggest moves to filter down from
MEMBERSHIPS = ("all", "members", "f2p")
VERDICTS = ("Profitable", "Marginal", "Unsteady", "Suspect", "Inactive")
ITEM_MATCH_MIN_SCORE = 0.35   # "whip" should still find "Abyssal whip"
SETTING_MEMBERSHIP = {"Members only": "members", "F2P only": "f2p"}
SORT_KEYS = ("profit", "roi", "net", "volume")
RANGES = [label for label, _window, _step in analyzer.CHART_RANGES]
DEFAULT_RANGE = "1W"

TOOLS = [
    {
        "name": "get_flips",
        "description": (
            "Search the Grand Exchange for profitable flips, using the same "
            "maths and verdicts as the FlipRec tab. Defaults to the user's own "
            "capital and membership filter. Like the user's alerts, it leaves "
            "out Suspect flips and flips with a side idle over 5 minutes, and "
            "left_out says how many. Results are best first unless sort_by is "
            "given."),
        "schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "Rows to return, 1-50. Default 20."},
                "capital": {"type": "string",
                            "description": "Budget such as 10m, 500k or 1.5b. "
                                           "Defaults to the user's capital."},
                "membership": {"type": "string", "enum": list(MEMBERSHIPS)},
                "verdict": {"type": "string", "enum": list(VERDICTS),
                            "description": "Suspect and Inactive items are "
                                           "left out unless this asks for them."},
                "include_slow": {"type": "boolean",
                                 "description": "Also return flips with a side "
                                                "idle over 5 minutes. Only when "
                                                "the user asks for them."},
                "name_contains": {"type": "string",
                                  "description": "Only item names containing "
                                                 "this text."},
                "max_buy_price": {"type": "integer",
                                  "description": "Highest buy price per item, "
                                                 "in gp."},
                "min_volume": {"type": "integer",
                               "description": "Lowest 1h volume on the slower "
                                              "side."},
                "sort_by": {"type": "string", "enum": list(SORT_KEYS)},
            },
        },
    },
    {
        "name": "get_item",
        "description": (
            "Full details for one item: live prices, flip stats, buy limit, "
            "units traded in the last 1, 12 and 24 hours (null means that "
            "data hasn't loaded yet, not zero trades), price changes over 24 "
            "hours and 7 days, and facts about its recent trading. Accepts "
            "an approximate name."),
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "string",
                                    "description": "Item name."}},
            "required": ["name"],
        },
    },
    {
        "name": "get_price_range",
        "description": (
            "One item's prices and trading over a time range, the same numbers "
            "shown beside the Search chart: the highest, lowest and average buy "
            "and sell prices, units traded each side and in total, the pace per "
            "hour or day, gp traded, buy pressure (the % of units that were "
            "instant buys), the change over the range, the typical margin after "
            "tax, and how far the live prices are above the range's lows and "
            "below its highs. Use it for questions such as how much of an item "
            "traded last week or whether it's near its monthly low. These "
            "describe the past and aren't a forecast. Accepts an approximate "
            "name."),
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Item name."},
                "range": {"type": "string", "enum": RANGES,
                          "description": "1H, 12H, 1D, 1W (default), 1M, 1Y, "
                                         "or All (about a year)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_movers",
        "description": (
            "The biggest price moves across the GE compared with the average "
            "over a time window, as on the Volatile tab. Low-volume items "
            "are left out because one trade can swing their price."),
        "schema": {
            "type": "object",
            "properties": {
                "window": {"type": "string", "enum": list(MOVER_WINDOWS),
                           "description": "Default 1h."},
                "limit": {"type": "integer",
                          "description": "Rows to return, 1-50. Default 20."},
                "direction": {"type": "string", "enum": ["up", "down"],
                              "description": "Only rising or falling items."},
            },
        },
    },
    {
        "name": "get_my_tracker",
        "description": (
            "The user's own setup: capital, membership filter, the items on "
            "their Watchlist with live flip stats and any target prices, and "
            "their active profit alerts."),
        "schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_alert_history",
        "description": (
            "How often items have triggered a profit alert over the last 7 days, "
            "most first. An item alerts at most once an hour. Shows whether each "
            "one is alerting now and whether it's on the user's Watchlist. Use it "
            "to spot items that keep producing good flips."),
        "schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "Items to return, 1-50. Default 20."},
            },
        },
    },
    {
        "name": "get_news",
        "description": "Recent official OSRS news headlines saved by the app.",
        "schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "Headlines to return, 1-20. "
                                         "Default 10."},
            },
        },
    },
]

STATUS_TEXT = {
    "get_flips": "Checking flips…",
    "get_item": "Looking up an item…",
    "get_price_range": "Reading an item's price history…",
    "get_movers": "Checking price moves…",
    "get_my_tracker": "Reading your watchlist and alerts…",
    "get_alert_history": "Checking your alert history…",
    "get_news": "Reading the news…",
}


class ToolError(Exception):
    """A problem to report back to the model rather than the user."""


def _bounded_int(args: dict, key: str, default: int, low: int, high: int) -> int:
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(int(value), high))


def _choice(args: dict, key: str, allowed, default=None):
    value = args.get(key)
    return value if value in allowed else default


def _user_capital() -> int:
    try:
        return analyzer.parse_gp_amount(settings.load().get("capital", ""))
    except ValueError:
        return 0


def _user_membership() -> str:
    return SETTING_MEMBERSHIP.get(settings.load().get("flip_membership"), "all")


def _clock(timestamp: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def _margin_warning(held: Optional[int], typical: Optional[int],
                    periods: Optional[int], unknown: bool) -> Optional[str]:
    if unknown:
        return ("The app's recent price history isn't ready, so it couldn't "
                "check how steady this margin has been.")
    if held is not None and held < analyzer.FLIP_MIN_MARGIN_HELD:
        return (f"The margin was positive in only {held}% of {periods} recent "
                f"5-minute periods, and is usually {typical} gp.")
    return None


def _flip_warning(row: dict) -> Optional[str]:
    """Written here, not left to the model, so every model gets the same one."""
    warnings = []
    if row["verdict"] == "Suspect":
        warnings.append("This margin may not be real.")
    else:
        margin = _margin_warning(row["margin_held"], row["typical_margin"],
                                 row["margin_periods"], row["margin_unknown"])
        if margin:
            warnings.append(margin)
    if row["slow"]:
        warnings.append("A side last traded over 5 minutes ago, so the margin "
                        "may have moved.")
    return " ".join(warnings) or None


def _flip_summary(row: dict) -> dict:
    return {
        "name": row["name"], "buy": row["buy"], "sell": row["sell"],
        "tax": row["tax"], "margin_after_tax": row["net"], "roi_pct": row["roi"],
        "buy_limit": row["limit"], "suggested_qty": row["qty"],
        "total_cost": row["cost"], "est_profit": row["profit"],
        "hourly_volume_slower_side": row["volume"],
        "last_trade_age_seconds": row["age"], "verdict": row["verdict"],
        "price_vs_1h_avg": row["reversion"], "pct_vs_1h_avg": row["reversion_pct"],
        "margin_at_5m_avg_prices": row["net_5m"],
        "margin_backed_by_5m_avg": row["backed_5m"],
        "margin_held_pct": row["margin_held"],
        "typical_margin": row["typical_margin"],
        "margin_periods_checked": row["margin_periods"],
        "warning": _flip_warning(row),
    }


def _log_row(entry: dict) -> str:
    """One thing a lookup returned, for the log."""
    parts = [f"  {entry.get('name')}"]
    for label, key, unit in (("buy", "buy", ""), ("sell", "sell", ""),
                             ("margin", "margin_after_tax", ""),
                             ("age", "last_trade_age_seconds", "s"),
                             ("", "verdict", ""), ("held", "margin_held_pct", "%"),
                             ("mid", "current_mid", ""), ("avg", "window_avg_mid", ""),
                             ("move", "pct_change", "%")):
        if entry.get(key) is not None:
            parts.append(f"{label} {entry[key]}{unit}".strip())
    return " ".join(parts)


def _log_lookup(name: str, args, result: dict) -> None:
    """
    Save a lookup and the numbers it returned, so a surprising answer can be
    checked against what the AI actually saw. The question, the answer and the
    API key never come through here.
    """
    if "error" in result:
        applog.log.info(f"AI {name} {json.dumps(args, default=str)[:300]} -> "
                        f"error: {result['error']}")
        return
    summary, rows = "done", []
    if name == "get_flips":
        out = result["left_out"]
        summary = (f"{result['matching_items']} offered, left out {out['suspect']} "
                   f"suspect, {out['side_idle_over_5_min']} idle")
        rows = result["flips"]
    elif name == "get_item":
        summary = result["name"]
        rows = [result["flip"]] if result["flip"] else []
    elif name == "get_price_range":
        summary = (f"{result['name']} {result['range']}: "
                   f"{result['data_points']} points")
        for side in ("buy", "sell"):
            p = result.get(f"{side}_price") or {}
            if p.get("lowest") is not None:
                summary += (f", {side} {p['lowest']}-{p['highest']} "
                            f"avg {p['average']}")
        if result.get("units_traded"):
            summary += f", traded {result['units_traded']['total']}"
        for key, label in (("typical_margin_after_tax", "margin"),
                           ("margin_positive_pct", "margin positive %"),
                           ("change_pct", "change %"),
                           ("buy_pressure_pct", "buy pressure %")):
            if result.get(key) is not None:
                summary += f", {label} {result[key]}"
        live = result.get("live") or {}
        quotes = [f"{k} {live[k]}" for k in ("buy", "sell")
                  if live.get(k) is not None]
        if quotes:
            summary += (f", live {' '.join(quotes)}, "
                        f"prices checked {live['prices_checked_at']}")
    elif name == "get_movers":
        summary = f"{len(result['movers'])} movers over {result['window']}"
        rows = result["movers"]
    elif name == "get_my_tracker":
        summary = (f"{len(result['watchlist'])} on the Watchlist, "
                   f"{len(result['active_alerts'])} alerts")
        rows = result["watchlist"] + result["active_alerts"]
    elif name == "get_alert_history":
        summary = f"{len(result['items'])} items"
    elif name == "get_news":
        summary = f"{len(result['news'])} headlines"
    if result.get("prices_checked_at"):
        summary += f", prices checked {result['prices_checked_at']}"
    applog.log.info("\n".join(
        [f"AI {name} {json.dumps(args, default=str)[:300]} -> {summary}"]
        + [_log_row(row) for row in rows]))


class MarketTools:
    """Runs tool calls. get_mapping returns the app's loaded item list."""

    def __init__(self, get_mapping: Callable[[], list],
                 history: Optional[volume.VolumeHistory] = None):
        self._get_mapping = get_mapping
        # Without a running history, averages() asks the Wiki directly.
        self._history = history or volume.VolumeHistory()
        self._market = None        # (latest, bulk_1h, fetched_at, bulk_5m)
        self._checked_at = None    # when this question's oldest prices were fetched
        self._lock = threading.Lock()

    def new_question(self) -> None:
        """Each question starts from new prices. Lookups within one question
        share them, so its numbers agree with each other."""
        with self._lock:
            self._market = None
            self._checked_at = None

    def prices_checked_at(self) -> Optional[float]:
        """When this question's prices were fetched. None if it used none."""
        return self._checked_at

    def _note_prices(self, fetched_at: float) -> None:
        self._checked_at = min(self._checked_at or fetched_at, fetched_at)

    def run(self, name: str, args) -> dict:
        """Run one tool and log what it returned. A problem with the log must
        never cost the answer."""
        result = self._run(name, args)
        try:
            _log_lookup(name, args, result)
        except Exception:
            applog.log.warning(f"Couldn't log AI tool {name}", exc_info=True)
        return result

    def _run(self, name: str, args) -> dict:
        """Failures come back as {'error': ...} for the model."""
        handler = {
            "get_flips": self._get_flips,
            "get_item": self._get_item,
            "get_price_range": self._get_price_range,
            "get_movers": self._get_movers,
            "get_my_tracker": self._get_my_tracker,
            "get_alert_history": self._get_alert_history,
            "get_news": self._get_news,
        }.get(name)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}
        if not isinstance(args, dict):
            return {"error": "Tool arguments must be an object."}
        try:
            return handler(args)
        except ToolError as exc:
            return {"error": str(exc)}
        except Exception:
            # One bad lookup shouldn't throw away the whole answer.
            applog.log.warning(f"AI tool {name} failed", exc_info=True)
            return {"error": "That lookup failed. Answer without it, and tell "
                             "the user what is missing."}

    # ── Shared data ───────────────────────────────────────────────────────────

    def _mapping(self) -> list:
        mapping = self._get_mapping()
        if not mapping:
            raise ToolError("The item list is still loading. Try again shortly.")
        return mapping

    def _traded(self, item_id: int) -> dict:
        """Units traded by window: 'instant_buys' at the higher price and
        'instant_sells' at the lower. null = not loaded yet, not zero."""
        traded = {}
        for label, seconds in volume.WINDOWS:
            sides = self._history.sides(item_id, seconds)
            traded[f"last_{label}"] = sides and {
                "instant_buys": sides[0], "instant_sells": sides[1],
                "may_read_low": self._history.is_partial(seconds)}
        return traded

    def _latest_market(self) -> tuple:
        with self._lock:
            if (self._market
                    and time.time() - self._market[2] < MARKET_MAX_AGE):
                self._note_prices(self._market[2])
                return self._market
            latest = api.get_all_latest_prices()
            bulk = self._history.averages("1h")
            if not latest or not bulk:
                raise ToolError("Could not fetch live prices from the OSRS Wiki.")
            bulk_5m = self._history.averages("5m")
            self._market = (latest, bulk, time.time(), bulk_5m)
            self._note_prices(self._market[2])
            return self._market

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _get_flips(self, args: dict) -> dict:
        capital = _user_capital()
        if isinstance(args.get("capital"), str) and args["capital"].strip():
            try:
                capital = analyzer.parse_gp_amount(args["capital"])
            except ValueError:
                raise ToolError("capital must look like 10m, 500k or 1,000,000.")
        membership = _choice(args, "membership", MEMBERSHIPS, _user_membership())

        latest, bulk, fetched_at, bulk_5m = self._latest_market()
        rows = analyzer.find_flips(latest, bulk, self._mapping(),
                                   capital=capital, membership=membership,
                                   bulk_5m=bulk_5m,
                                   recent_prices=self._history.recent_prices)
        ignored = db.ignored_ids()
        rows = [r for r in rows if r["item_id"] not in ignored]

        needle = args.get("name_contains")
        if isinstance(needle, str) and needle.strip():
            needle = needle.strip().lower()
            rows = [r for r in rows if needle in r["name"].lower()]
        max_buy = _bounded_int(args, "max_buy_price", 0, 0, 10**12)
        if max_buy:
            rows = [r for r in rows if r["buy"] <= max_buy]
        min_volume = _bounded_int(args, "min_volume", 0, 0, 10**9)
        if min_volume:
            rows = [r for r in rows if r["volume"] >= min_volume]

        # The same rules as alerts, unless the question asks for the others.
        verdict = _choice(args, "verdict", VERDICTS)
        if verdict:
            rows = [r for r in rows if r["verdict"] == verdict]
        include_slow = args.get("include_slow") is True
        left_out = {"suspect": 0, "side_idle_over_5_min": 0}
        offered = []
        for row in rows:
            reason = analyzer.why_no_alert(row)
            if (reason is None or (reason == "side_idle" and include_slow)
                    or (verdict and reason != "side_idle")):
                offered.append(row)
            elif reason == "suspect":
                left_out["suspect"] += 1
            elif reason == "side_idle":
                left_out["side_idle_over_5_min"] += 1
        rows = offered

        sort_by = _choice(args, "sort_by", SORT_KEYS)
        if sort_by:
            rows = sorted(rows, key=lambda r: r[sort_by], reverse=True)

        limit = _bounded_int(args, "limit", 20, 1, 50)
        return {
            "prices_age_seconds": int(time.time() - fetched_at),
            "prices_checked_at": _clock(fetched_at),
            "capital_used_gp": capital, "membership": membership,
            "matching_items": len(rows), "left_out": left_out,
            "flips": [_flip_summary(r) for r in rows[:limit]],
        }

    def _find_item(self, args: dict) -> list:
        """(score, item) matches for args['name'], best first."""
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolError("name is required.")
        matches = search_module.search_items(
            name, self._mapping(), min_score=ITEM_MATCH_MIN_SCORE, top_n=5)
        if not matches:
            raise ToolError(f"No item found matching {name!r}.")
        return matches

    def _get_item(self, args: dict) -> dict:
        matches = self._find_item(args)
        item = matches[0][1]
        item_id = item["id"]

        latest, bulk, fetched_at, bulk_5m = self._latest_market()
        lt = latest.get(str(item_id))
        row = (analyzer.flip_row(item, lt, bulk.get(str(item_id)),
                                 _user_capital(),
                                 avg_5m=bulk_5m.get(str(item_id)),
                                 recent_prices=self._history.recent_prices)
               if lt else None)

        points = api.get_timeseries(item_id, timestep=HISTORY_STEP)
        # The trend reads rows newest first, like db.get_price_history().
        rows = [(p.get("avgHighPrice"), p.get("avgLowPrice"), p.get("timestamp"))
                for p in reversed(points[-30:])]
        live = (lt.get("high"), lt.get("low")) if lt else None
        trend, facts = analyzer.recent_market(
            api.get_timeseries(item_id, timestep="5m"), rows, points, live=live)

        return {
            "name": item["name"],
            "on_ignored_list": item_id in db.ignored_ids(),
            "members_item": bool(item.get("members")),
            "buy_limit": item.get("limit"),
            "high_alch": item.get("highalch"),
            "examine": item.get("examine"),
            "prices_age_seconds": int(time.time() - fetched_at),
            "prices_checked_at": _clock(fetched_at),
            "flip": _flip_summary(row) if row else None,
            "history": _history_summary(points),
            "units_traded": self._traded(item_id),
            "recent_market": {
                "price_move_pct": trend["pct_change"],
                "over_hours": trend["hours"],
                "direction": trend["direction"],
                "volume_vs_usual": facts["volume"],
                "last_hour_volume": facts["recent_volume"],
                "usual_hourly_volume": facts["usual_volume"],
                "momentum": facts["momentum"],
                "notes": facts["notes"],
            },
            "other_close_matches": [m[1]["name"] for m in matches[1:]],
        }

    def _get_price_range(self, args: dict) -> dict:
        matches = self._find_item(args)
        item = matches[0][1]
        label = _choice(args, "range", RANGES, DEFAULT_RANGE)
        _label, window, step = next(
            r for r in analyzer.CHART_RANGES if r[0] == label)
        size = api.STEP_SECONDS[step]
        since = int(time.time()) - window if window else 0
        # Stored as the chart stores them, then read back the way the chart
        # reads them, so both give the same numbers. If the download fails,
        # what's already stored still answers.
        db.backfill_price_history(item["id"], item["name"],
                                  api.get_timeseries(item["id"], timestep=step),
                                  step)
        rows = db.get_price_range(item["id"], since, step,
                                  include_live=analyzer.range_includes_live(window))
        stats = analyzer.range_stats(
            rows, size, exempt=item["name"] in analyzer.GE_TAX_EXEMPT_NAMES)
        result = {"name": item["name"], "range": label, "data_points": len(rows),
                  "other_close_matches": [m[1]["name"] for m in matches[1:]]}
        if not stats:
            result["note"] = ("No price history came back for this range. The "
                              "item may not have traded, or the lookup failed.")
            return result

        def rounded(value, places=1):
            return None if value is None else round(value, places)

        def clock(ts):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))

        per_hour = stats["per_hour"]
        result.update({
            "minutes_per_point": size // 60,
            "from": clock(rows[0][2]), "to": clock(rows[-1][2] + size),
            "buy_price": {"highest": stats["buy_high"], "lowest": stats["buy_low"],
                          "average": stats["buy_avg"]},
            "sell_price": {"highest": stats["sell_high"],
                           "lowest": stats["sell_low"],
                           "average": stats["sell_avg"]},
            "units_traded": None if per_hour is None else {
                "instant_buys": stats["instant_buys"],
                "instant_sells": stats["instant_sells"],
                "total": stats["instant_buys"] + stats["instant_sells"],
                "per_hour": round(per_hour), "per_day": round(per_hour * 24)},
            "gp_traded": stats["gp_traded"],
            "buy_pressure_pct": rounded(stats["buy_pressure"]),
            "change_pct": rounded(stats["change_pct"], 2),
            "typical_margin_after_tax": stats["margin"],
            "margin_positive_pct": stats["margin_positive"],
            "note": (f"Most points are {size // 60}-minute averages, so single "
                     "trades may have gone past the highest and lowest. On "
                     "ranges up to 1W, prices the app saw live are included "
                     "too. units_traded null means the Wiki sent no volumes."),
        })
        try:
            latest, _bulk, fetched_at, _bulk_5m = self._latest_market()
        except ToolError:
            return result     # the range still answers the question
        lt = latest.get(str(item["id"])) or {}
        high = lt.get("high")
        low = lt.get("low") or high
        where = analyzer.range_position(stats, high, low)
        result["live"] = {"buy": low, "sell": high,
                          "prices_checked_at": _clock(fetched_at),
                          **{k: rounded(v) for k, v in where.items()}}
        return result

    def _get_movers(self, args: dict) -> dict:
        window = _choice(args, "window", MOVER_WINDOWS, "1h")
        latest, _, fetched_at, _ = self._latest_market()
        bulk = self._history.averages(window)
        if not bulk:
            raise ToolError("Could not fetch live prices from the OSRS Wiki.")
        rows = analyzer.find_volatile_items(latest, bulk, self._mapping(),
                                            min_volume=1000, top_n=MOVER_POOL)
        ignored = db.ignored_ids()
        rows = [r for r in rows if r["item_id"] not in ignored]
        direction = args.get("direction")
        if direction == "up":
            rows = [r for r in rows if r["pct_change"] > 0]
        elif direction == "down":
            rows = [r for r in rows if r["pct_change"] < 0]
        limit = _bounded_int(args, "limit", 20, 1, 50)
        return {
            "window": window,
            "prices_checked_at": _clock(fetched_at),
            "movers": [{
                "name": r["name"], "current_mid": r["current"],
                "window_avg_mid": r["avg"], "pct_change": r["pct_change"],
                "volume": r["volume"],
            } for r in rows[:limit]],
        }

    def _get_my_tracker(self, args: dict) -> dict:
        capital = _user_capital()
        watched = db.watchlist_items()
        targets = db.watchlist_targets()
        ignored = db.ignored_ids()
        watchlist = []
        if watched:
            latest, bulk, _, bulk_5m = self._latest_market()
            by_id = {i["id"]: i for i in self._mapping()}
            for item_id, item_name, _added in watched:
                lt = latest.get(str(item_id))
                item = by_id.get(item_id)
                row = (analyzer.flip_row(item, lt, bulk.get(str(item_id)),
                                         capital,
                                         avg_5m=bulk_5m.get(str(item_id)),
                                         recent_prices=self._history.recent_prices)
                       if lt and item else None)
                entry = (_flip_summary(row) if row
                         else {"name": item_name, "verdict": "No price"})
                entry["on_ignored_list"] = item_id in ignored
                target = targets.get(item_id)
                if target:
                    entry["target_buy_at_or_below"] = target["target_below"]
                    entry["target_sell_at_or_above"] = target["target_above"]
                    entry["target_met"] = any(analyzer.target_hits(
                        lt, target["target_below"], target["target_above"],
                        time.time()))
                watchlist.append(entry)
        now = time.time()
        alerts = []
        for a in db.get_active_alerts():
            # These prices are from the app's last market check, not this question.
            with self._lock:
                self._note_prices(a["updated"])
            prices = self._history.recent_prices(a["item_id"])
            held, typical, periods = analyzer.margin_history(
                prices, a["item_name"] in analyzer.GE_TAX_EXEMPT_NAMES)
            alerts.append({
                "name": a["item_name"], "buy": a["buy"], "sell": a["sell"],
                "margin_after_tax": a["net"], "qty": a["qty"],
                "total_cost": a["cost"],
                "est_profit": a["profit"], "roi_pct": a["roi"],
                "alerted_seconds_ago": int(now - a["timestamp"]),
                "times_alerted": a["n"],
                "prices_age_seconds": int(now - a["updated"]),
                "last_trade_age_seconds": (int(now - a["traded_at"])
                                           if a["traded_at"] else None),
                "margin_held_pct": held, "typical_margin": typical,
                "margin_periods_checked": periods,
                "warning": _margin_warning(held, typical, periods, prices is None),
            })
        return {
            "capital_gp": capital,
            "capital_set": capital > 0,
            "membership": _user_membership(),
            "watchlist": watchlist,
            "active_alerts": alerts,
        }

    def _get_alert_history(self, args: dict) -> dict:
        limit = _bounded_int(args, "limit", 20, 1, 50)
        now = time.time()
        alerting = {a["item_id"] for a in db.get_active_alerts()}
        return {
            "days_kept": db.ALERT_RETENTION // 86400,
            "items": [{
                "name": h["item_name"], "times_alerted": h["times"],
                "first_alerted_hours_ago": round((now - h["first_alerted"]) / 3600, 1),
                "last_alerted_hours_ago": round((now - h["last_alerted"]) / 3600, 1),
                "alerting_now": h["item_id"] in alerting,
                "on_watchlist": bool(h["on_watchlist"]),
            } for h in db.alert_history(limit)],
        }

    def _get_news(self, args: dict) -> dict:
        limit = _bounded_int(args, "limit", 10, 1, 20)
        now = time.time()
        return {"news": [{
            "title": title, "source": source, "url": url,
            "age_days": round((now - ts) / 86400, 1),
        } for title, url, source, ts in db.get_recent_news(limit)]}


def _history_summary(points: list) -> dict:
    """Percent changes and range from hourly points (oldest first)."""
    mids = [(p["timestamp"], (p["avgHighPrice"] + p["avgLowPrice"]) / 2)
            for p in points if p.get("avgHighPrice") and p.get("avgLowPrice")]
    if not mids:
        return {"data_points": 0}
    now_ts, current = mids[-1]

    def change_since(seconds: int):
        then = next((mid for ts, mid in mids if ts >= now_ts - seconds), None)
        return round((current - then) / then * 100, 2) if then else None

    prices = [mid for _, mid in mids]
    return {
        "data_points": len(mids),
        "days_covered": round((now_ts - mids[0][0]) / 86400, 1),
        "pct_change_24h": change_since(86400),
        "pct_change_7d": change_since(7 * 86400),
        "low_mid": int(min(prices)), "high_mid": int(max(prices)),
    }
