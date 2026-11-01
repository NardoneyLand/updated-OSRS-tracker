"""
aitools.py — the app data an AI assistant can look up while answering.

Every tool here is read-only, and none of them can place trades or change
settings. Results are plain dicts, so ai.py can hand them to either provider.
Arguments come from the model, so each tool checks them instead of trusting
the schema.
"""

import threading
import time
from typing import Callable

import analyzer
import api
import applog
import db
import search as search_module
import settings

MARKET_MAX_AGE = 60        # seconds; the Wiki asks apps not to over-fetch
HISTORY_STEP = "1h"
MOVER_WINDOWS = ("5m", "1h", "6h", "24h")
MOVER_POOL = 500           # biggest moves to filter down from
MEMBERSHIPS = ("all", "members", "f2p")
VERDICTS = ("Profitable", "Marginal", "Suspect", "Inactive")
ITEM_MATCH_MIN_SCORE = 0.35   # "whip" should still find "Abyssal whip"
SETTING_MEMBERSHIP = {"Members only": "members", "F2P only": "f2p"}
SORT_KEYS = ("profit", "roi", "net", "volume")

TOOLS = [
    {
        "name": "get_flips",
        "description": (
            "Search the live Grand Exchange for profitable flips, using the "
            "same maths and verdicts as the FlipRec tab. Defaults to the "
            "user's own capital and membership filter. Results are best "
            "first unless sort_by is given."),
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
                            "description": "Inactive items are left out "
                                           "unless this asks for them."},
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
            "price changes over 24 hours and 7 days, and the app's Buy / "
            "Hold / Sell signal. Accepts an approximate name."),
        "schema": {
            "type": "object",
            "properties": {"name": {"type": "string",
                                    "description": "Item name."}},
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
            "their Watchlist with live flip stats, and their active profit "
            "alerts."),
        "schema": {"type": "object", "properties": {}},
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
    "get_movers": "Checking price moves…",
    "get_my_tracker": "Reading your watchlist and alerts…",
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


def _flip_summary(row: dict) -> dict:
    return {
        "name": row["name"], "buy": row["buy"], "sell": row["sell"],
        "tax": row["tax"], "margin_after_tax": row["net"], "roi_pct": row["roi"],
        "buy_limit": row["limit"], "suggested_qty": row["qty"],
        "total_cost": row["cost"], "est_profit": row["profit"],
        "hourly_volume_slower_side": row["volume"],
        "last_trade_age_seconds": row["age"], "verdict": row["verdict"],
        "signal": row["reversion"], "pct_vs_1h_avg": row["reversion_pct"],
        "margin_at_5m_avg_prices": row["net_5m"],
        "margin_backed_by_5m_avg": row["backed_5m"],
    }


class MarketTools:
    """Runs tool calls. get_mapping returns the app's loaded item list."""

    def __init__(self, get_mapping: Callable[[], list]):
        self._get_mapping = get_mapping
        self._market = None        # (latest, bulk_1h, fetched_at, bulk_5m)
        self._lock = threading.Lock()

    def run(self, name: str, args) -> dict:
        """Run one tool. Failures come back as {'error': ...} for the model."""
        handler = {
            "get_flips": self._get_flips,
            "get_item": self._get_item,
            "get_movers": self._get_movers,
            "get_my_tracker": self._get_my_tracker,
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

    def _latest_market(self) -> tuple:
        with self._lock:
            if (self._market
                    and time.time() - self._market[2] < MARKET_MAX_AGE):
                return self._market
            latest = api.get_all_latest_prices()
            bulk = api.get_bulk_average("1h")
            if not latest or not bulk:
                raise ToolError("Could not fetch live prices from the OSRS Wiki.")
            bulk_5m = api.get_bulk_average("5m")
            self._market = (latest, bulk, time.time(), bulk_5m)
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
                                   bulk_5m=bulk_5m)

        verdict = _choice(args, "verdict", VERDICTS)
        if verdict:
            rows = [r for r in rows if r["verdict"] == verdict]
        else:
            rows = [r for r in rows if r["active"]]
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
        sort_by = _choice(args, "sort_by", SORT_KEYS)
        if sort_by:
            rows = sorted(rows, key=lambda r: r[sort_by], reverse=True)

        limit = _bounded_int(args, "limit", 20, 1, 50)
        return {
            "prices_age_seconds": int(time.time() - fetched_at),
            "capital_used_gp": capital, "membership": membership,
            "matching_items": len(rows),
            "flips": [_flip_summary(r) for r in rows[:limit]],
        }

    def _get_item(self, args: dict) -> dict:
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolError("name is required.")
        matches = search_module.search_items(
            name, self._mapping(), min_score=ITEM_MATCH_MIN_SCORE, top_n=5)
        if not matches:
            raise ToolError(f"No item found matching {name!r}.")
        item = matches[0][1]
        item_id = item["id"]

        latest, bulk, fetched_at, bulk_5m = self._latest_market()
        lt = latest.get(str(item_id))
        row = (analyzer.flip_row(item, lt, bulk.get(str(item_id)),
                                 _user_capital(),
                                 avg_5m=bulk_5m.get(str(item_id)))
               if lt else None)

        points = api.get_timeseries(item_id, timestep=HISTORY_STEP)
        # The trend reads rows newest first, like db.get_price_history().
        rows = [(p.get("avgHighPrice"), p.get("avgLowPrice"), p.get("timestamp"))
                for p in reversed(points[-30:])]
        trend = analyzer.calculate_trend(rows)
        prediction = analyzer.get_prediction(trend, points)

        return {
            "name": item["name"],
            "members_item": bool(item.get("members")),
            "buy_limit": item.get("limit"),
            "high_alch": item.get("highalch"),
            "examine": item.get("examine"),
            "prices_age_seconds": int(time.time() - fetched_at),
            "flip": _flip_summary(row) if row else None,
            "history": _history_summary(points),
            "signal": {
                "suggestion": prediction["suggestion"],
                "confidence": prediction["confidence"],
                "reasons": prediction["reasons"],
                "volume": prediction["vol_label"],
                "momentum": prediction["momentum"],
            },
            "other_close_matches": [m[1]["name"] for m in matches[1:]],
        }

    def _get_movers(self, args: dict) -> dict:
        window = _choice(args, "window", MOVER_WINDOWS, "1h")
        latest = api.get_all_latest_prices()
        bulk = api.get_bulk_average(window)
        if not latest or not bulk:
            raise ToolError("Could not fetch live prices from the OSRS Wiki.")
        rows = analyzer.find_volatile_items(latest, bulk, self._mapping(),
                                            min_volume=1000, top_n=MOVER_POOL)
        direction = args.get("direction")
        if direction == "up":
            rows = [r for r in rows if r["pct_change"] > 0]
        elif direction == "down":
            rows = [r for r in rows if r["pct_change"] < 0]
        limit = _bounded_int(args, "limit", 20, 1, 50)
        return {
            "window": window,
            "movers": [{
                "name": r["name"], "current_mid": r["current"],
                "window_avg_mid": r["avg"], "pct_change": r["pct_change"],
                "volume": r["volume"],
            } for r in rows[:limit]],
        }

    def _get_my_tracker(self, args: dict) -> dict:
        capital = _user_capital()
        watched = db.watchlist_items()
        watchlist = []
        if watched:
            latest, bulk, _, bulk_5m = self._latest_market()
            by_id = {i["id"]: i for i in self._mapping()}
            for item_id, item_name, _added in watched:
                lt = latest.get(str(item_id))
                item = by_id.get(item_id)
                row = (analyzer.flip_row(item, lt, bulk.get(str(item_id)),
                                         capital,
                                         avg_5m=bulk_5m.get(str(item_id)))
                       if lt and item else None)
                watchlist.append(_flip_summary(row) if row
                                 else {"name": item_name, "verdict": "No price"})
        now = time.time()
        return {
            "capital_gp": capital,
            "capital_set": capital > 0,
            "membership": _user_membership(),
            "watchlist": watchlist,
            "active_alerts": [{
                "name": a["item_name"], "buy": a["buy"], "sell": a["sell"],
                "margin_after_tax": a["net"], "qty": a["qty"],
                "est_profit": a["profit"], "roi_pct": a["roi"],
                "alerted_seconds_ago": int(now - a["timestamp"]),
                "prices_age_seconds": int(now - a["updated"]),
                "last_trade_age_seconds": (int(now - a["traded_at"])
                                           if a["traded_at"] else None),
            } for a in db.get_active_alerts()],
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
