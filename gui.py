"""
gui.py — OSRS Price Tracker — Tkinter desktop interface.

Run from source:
    ./launch.sh

Build a Mac .app bundle:
    ./build.sh
"""

import bisect
import ctypes
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import ttk, messagebox, scrolledtext

import ai
import aitools
import api
import analyzer
import applog
import db
import scraper
import search as search_module
import settings
import updater
import useragent
import volume
from version import APP_VERSION


# ── Constants ─────────────────────────────────────────────────────────────────
# Profit alerts: checked in the background while the app is open.
# How often to re-check the market. The OSRS Wiki updates about every 60s, so
# checking faster than 1 minute would just re-fetch the same prices.
ALERT_INTERVALS = {"1 min": 60, "5 min": 300, "15 min": 900, "30 min": 1800}
ALERT_DEFAULT_INTERVAL = "1 min"
ALERT_MIN_STALE = 300                   # see _alert_stale_after
TRADE_AGE_TICK_MS = 30 * 1000           # keep "Last trade" ages counting up
ALERT_COOLDOWN = 3600                   # re-alert an item after 1 hour
MAX_GP = 2_147_483_647                  # the most gp a stack can hold
ALERT_DEFAULT_THRESHOLD = 1_000_000     # est. profit, adjustable in 100K steps
ALERT_THRESHOLD_STEPS = [f"{v:,}" for v in range(100_000, 100_000_001, 100_000)]
TIMESERIES_STEP = "1h"

# History tab time ranges: (label, window in seconds or None for all time,
# API timestep used for that window). Coarser steps reach further back:
# 5m ≈ 1 day, 1h ≈ 15 days, 6h ≈ 90 days, 24h ≈ 1 year.
HISTORY_RANGES = [
    ("1H",   3600,        "5m"),
    ("12H",  12 * 3600,   "5m"),
    ("1D",   86400,       "5m"),
    ("1W",   7 * 86400,   "1h"),
    ("1M",   30 * 86400,  "6h"),
    ("1Y",   365 * 86400, "24h"),
    ("All",  None,        "24h"),
]
HISTORY_DEFAULT_RANGE = "1W"
PRICE_FORMATS = ["Short (1.2K)", "Exact (1,240)"]
STEP_SECONDS = api.STEP_SECONDS
UI_QUEUE_POLL_MS = 50             # how often the window picks up finished work
CHART_MIN_POINTS = 4              # zooming stops at about this many points
FLIP_MAX_ROWS = 300
AI_MODEL_LIST_MARGIN = 40         # px for the list's scrollbar and padding
AI_INTRO = ("Ask about flips, prices, your Watchlist or the news. The AI looks "
            "up live data from this app before it answers.")
AI_STARTERS = [
    ("Best flips for my budget",
     "What are the best flips for my capital right now, and why?"),
    ("What's moving now?",
     "Which items are making the biggest moves right now, and are any of "
     "them worth acting on?"),
    ("Review my watchlist",
     "Review my watchlist and alerts. Which items should I act on or skip?"),
]

# Fantasy / OSRS-inspired palette (avoiding Jagex copyrighted colors directly)
COLOR_BG       = "#1a120b"   # near-black brown
COLOR_PANEL    = "#2d1f12"   # warm dark brown
COLOR_PANEL_HI = "#3b2818"   # raised panel
COLOR_BORDER   = "#5e3f1f"   # weathered wood border
COLOR_ACCENT   = "#f0b32c"   # gold
COLOR_ACCENT_HI = "#ffd87a"  # bright gold
COLOR_TARGET_ROW = "#5a4012"  # a met target on the Watchlist
COLOR_TEXT     = "#f4ead5"   # parchment cream
COLOR_DIM      = "#9a8866"   # faded tan
COLOR_GREEN    = "#7fbf3f"   # warmer green
COLOR_RED      = "#d44a2c"   # warm red
COLOR_YELLOW   = "#f0b32c"   # gold (same as accent)
COLOR_BLUE     = "#5b9aa0"   # muted teal
COLOR_INPUT    = "#1f1610"   # dark input fill

FONT_HEADER = ("Georgia", 18, "bold")
FONT_TITLE  = ("Georgia", 16, "bold")
FONT_LABEL  = ("Helvetica", 12)
FONT_VALUE  = ("Helvetica", 12, "bold")
FONT_BODY   = ("Helvetica", 11)
FONT_SMALL  = ("Helvetica", 9)


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt_gp(gp) -> str:
    if gp is None:
        return "N/A"
    try:
        gp = int(gp)
    except (TypeError, ValueError):
        return "N/A"
    sign, gp = ("-" if gp < 0 else ""), abs(gp)
    if gp >= 1_000_000:
        return f"{sign}{gp / 1_000_000:.2f}M"
    if gp >= 1_000:
        return f"{sign}{gp / 1_000:.1f}K"
    return f"{sign}{gp:,}"


def fmt_gp_exact(gp) -> str:
    """Whole-number gp with thousands separators, e.g. 1,240."""
    if gp is None:
        return "N/A"
    try:
        return f"{int(gp):,}"
    except (TypeError, ValueError):
        return "N/A"


def parse_targets(below_text: str, above_text: str) -> tuple:
    """Typed target prices as (below, above), None where blank. Raises
    ValueError with a message to show the user."""
    targets = []
    for text in (below_text, above_text):
        try:
            value = analyzer.parse_gp_amount(text)
        except ValueError:
            raise ValueError("Enter prices like 19,000, 19k or 1.5m.")
        if value > MAX_GP:
            raise ValueError(f"The most an item can cost is {MAX_GP:,} gp.")
        targets.append(value or None)
    below, above = targets
    if below and above and below >= above:
        raise ValueError("The Buy target must be lower than the Sell target.")
    return below, above


def fmt_target(target, hit, fmt) -> str:
    """A target price for the table, ticked once the market has reached it."""
    if not target:
        return ""
    return f"✓ {fmt(target)}" if hit else fmt(target)


def fmt_age(seconds) -> str:
    """Compact age: 45s, 12m, 3h."""
    s = int(seconds or 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def fmt_last_trade(traded_at, now=None) -> str:
    """How long ago a trade was, flagged if it's too old to trust the margin."""
    if not traded_at:
        return "-"
    age = (now or time.time()) - traded_at
    return ("⏱ " if age > analyzer.FLIP_SLOW_AGE else "") + fmt_age(max(age, 0))


def fmt_price_move(trend: dict) -> str:
    """'Down 5.2% over 30 hours'. Plain text: a move isn't good or bad."""
    pct = trend["pct_change"]
    if trend["direction"] == "unknown":
        return "Not enough history"
    move = {"rising": f"Up {abs(pct):.1f}%", "falling": f"Down {abs(pct):.1f}%"
            }.get(trend["direction"], f"Steady ({pct:+.1f}%)")
    return f"{move} over {trend['hours']} hours" if trend["hours"] else move


def fmt_volume_pace(facts: dict) -> str:
    if facts["volume"] == "unknown":
        return "Unknown"
    return (f"{facts['volume'].capitalize()} for this item  "
            f"({fmt_gp(facts['recent_volume'])} last hour, "
            f"usually {fmt_gp(facts['usual_volume'])})")


# ── Taskbar flash ─────────────────────────────────────────────────────────────

def flash_window(root: tk.Tk):
    """Flash the taskbar button until the window is focused (Windows only)."""
    if sys.platform != "win32":
        return
    try:
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint), ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint)]
        FLASHW_ALL, FLASHW_TIMERNOFG = 0x3, 0xC
        hwnd = int(root.wm_frame(), 16)
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd,
                          FLASHW_ALL | FLASHW_TIMERNOFG, 0, 0)
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass  # cosmetic only


# ── Threading helper ──────────────────────────────────────────────────────────

# Background threads must never call Tk, even root.after: each thread that does
# leaves a Windows handle and about 17 KB behind for good, which adds up with a
# new thread on every market check. They queue the call for the window instead.
_ui_calls = queue.SimpleQueue()


def ui_call(fn, *args) -> None:
    """Run fn(*args) on the window's thread. Safe from any thread."""
    _ui_calls.put((fn, args))


def run_ui_queue(root: tk.Tk) -> None:
    # Rescheduled first, so a callback that opens a dialog can't stall the rest.
    root.after(UI_QUEUE_POLL_MS, run_ui_queue, root)
    while True:
        try:
            fn, args = _ui_calls.get_nowait()
        except queue.Empty:
            return
        try:
            fn(*args)
        except Exception:
            root.report_callback_exception(*sys.exc_info())


def run_async(target, on_done):
    def worker():
        try:
            result = target()
            err = None
        except Exception as e:
            applog.log.warning(f"Background task {target.__name__} failed",
                               exc_info=True)
            result, err = None, e
        ui_call(on_done, result, err)
    threading.Thread(target=worker, daemon=True).start()


# ── Help text ─────────────────────────────────────────────────────────────────

HELP_SECTIONS = [
    ("Getting started",
     "OSRS Price Tracker uses live Grand Exchange data from the OSRS Wiki to "
     "find items worth flipping, alert you to big opportunities, and track "
     "items you care about.\n\n"
     "    1.  On first launch, enter a contact (see \"Your contact\" below).\n"
     "    2.  Open FlipRec and type your available gp into Capital "
     "(e.g. 10m, 500k, 1.5b). Profit estimates and alerts use it.\n"
     "    3.  Look for green Profitable rows. Click Refresh right before "
     "you place offers, because margins move quickly.\n"
     "    4.  On Alerts, choose the profit that's worth your time "
     "(1M by default). The app checks for you every minute, or as often "
     "as you choose.\n"
     "    5.  Add items you want to follow to your Watchlist from the "
     "Search tab.\n\n"
     "The tabs, left to right: FlipRec · Alerts · Watchlist · Volatile · "
     "Search · Ask AI · News · Help."),

    ("FlipRec — flip recommendations",
     "FlipRec lists every item you could buy and immediately resell at a "
     "profit after the GE tax, best first.\n\n"
     "How a flip is priced: you buy at the latest instant-sell price "
     "(Buy) and sell at the latest instant-buy price (Sell), the same "
     "numbers an in-game margin check gives you.\n\n"
     "Controls:\n"
     "    •  Capital — the gp you have available. Leave blank to ignore it.\n"
     "    •  Items — All items, Members only or F2P only.\n"
     "    •  Hide inactive — hides items whose margin isn't real (see "
     "Inactive below).\n"
     "    •  Prices — Short (1.2K) or Exact (1,240). The same setting is "
     "on the Alerts tab, and Watchlist follows it too.\n"
     "    •  Show — only one verdict, e.g. just Profitable.\n"
     "    •  Refresh — fetch fresh prices. The tab loads automatically "
     "the first time you open it.\n\n"
     "Columns:\n"
     "    •  GE tax — tax on one sale (see \"The GE tax\").\n"
     "    •  Net / item — Sell − tax − Buy.\n"
     "    •  ROI — Net / item as a % of the Buy price.\n"
     "    •  4h limit — the GE buy limit per 4 hours (\"?\" if unknown).\n"
     "    •  Qty — how many you can realistically flip: the smallest of "
     "what your capital buys, the 4h limit, and about 4 hours of market "
     "volume.\n"
     "    •  Est. profit — Net / item × Qty.\n"
     "    •  Vol 1h — units traded in the last hour on the slower side, "
     "which is what limits how fast a flip fills. ▲ means at least 3x its "
     "usual hour over the last day; ▼ means under half.\n"
     "    •  Last trade — how long ago the older side last traded. "
     "⏱ means over 5 minutes, so the margin may have moved.\n"
     "    •  Signal — the current price vs its 1-hour average: "
     "\"Dip - may rebound\" (3%+ below), \"Spike - may fall\" (3%+ "
     "above) or \"Extreme move - caution\" (25%+ away).\n\n"
     "Click a column header to sort; click again to reverse. Double-click "
     "a row to open the item in Search."),

    ("Verdicts",
     "    •  Profitable — positive after-tax margin, ROI of 1% or more, "
     "and at least 10 traded per hour on each side.\n"
     "    •  Marginal — still profitable after tax, but a thin ROI or low "
     "volume.\n"
     "    •  Suspect — looks too good to be true: ROI over 40%, the "
     "price is 25%+ away from its 1-hour average, or the margin at the last "
     "5 minutes' average prices is under half the margin shown. Often one "
     "odd trade or manipulation.\n"
     "    •  Inactive — one side hasn't traded in the last hour, so the "
     "margin is probably not real.\n"
     "    •  Unprofitable — (Watchlist only) the tax eats the whole "
     "margin.\n\n"
     "Sorting the Verdict column goes Profitable → Marginal → Suspect → "
     "Inactive, best profit first within each."),

    ("The GE tax",
     "The seller pays 2% of the sell price, rounded down, per item:\n\n"
     "    •  Items selling below 50 gp pay no tax (2% is under 1 coin).\n"
     "    •  The tax is capped at 5M gp per item.\n"
     "    •  A few items are exempt, such as Old school bonds and some "
     "basic tools.\n\n"
     "Rounding down means an item listed at an exact multiple of 50 "
     "(e.g. 100) earns the seller the same as one coin less (99), so "
     "undercutting those prices by 1 gp costs you nothing."),

    ("Alerts — big profit opportunities",
     "While the app is open it checks the market on a timer and logs an "
     "alert when a flip's Est. profit (calculated as in FlipRec, using your "
     "capital and item filter) reaches your threshold.\n\n"
     "Alerts stay live. Every check updates their prices, and an alert "
     "disappears by itself once the flip stops qualifying. If it qualifies "
     "again it comes back.\n\n"
     "    •  Refresh — how often to check: every 1, 5, 15 or 30 minutes (1 "
     "by default). Watchlist prices update on the same timer.\n"
     "    •  Threshold — the picker next to \"Alert when est. profit ≥\". "
     "1,000,000 by default, in 100,000 steps (arrows or mouse wheel). "
     "Changing it updates the list straight away: raising it hides "
     "smaller alerts, lowering it shows them again and adds any new "
     "ones.\n"
     "    •  Only actively traded items count: both sides must have "
     "traded in the last 5 minutes. Suspect items are ignored, including "
     "margins the last 5 minutes' average prices don't back up.\n"
     "    •  Last trade — how long ago the older side last traded. It keeps "
     "counting up between checks.\n"
     "    •  Each item alerts at most once an hour. \"Times\" counts how "
     "often it has alerted. An alert that comes back within the hour "
     "returns quietly, without counting or flashing.\n"
     "    •  The tab shows how many alerts are live, e.g. Alerts (3). New "
     "ones flash the taskbar on Windows.\n"
     "    •  Dismiss selected hides an item for up to an hour, until it "
     "can alert again; Dismiss all does the same for everything.\n"
     "    •  If checks keep failing, alerts disappear once their prices "
     "are too old to trust: after 5 minutes, or two check periods if that "
     "is longer.\n"
     "    •  Check now runs a check immediately.\n"
     "    •  Prices — Short (1.2K) or Exact (1,240); the same setting as "
     "on FlipRec.\n"
     "    •  Click a column header to sort (newest first to start); click "
     "again to reverse. Double-click a row to open it in Search.\n\n"
     "Nothing is checked while the app is closed. Without a Capital set "
     "on FlipRec, quantities are limited only by buy limits and volume, "
     "so you'll see more (and larger) alerts."),

    ("Watchlist — items you follow",
     "Your own hand-picked list. To add an item, search it and click "
     "☆ Add to Watchlist on its result; click ★ On Watchlist to remove it "
     "again. You can also select rows on the Watchlist tab and click "
     "Remove selected.\n\n"
     "Each item shows its live Buy, Sell, Net / item, ROI, last trade and "
     "verdict, using the same maths as FlipRec, plus units traded in the "
     "last 1, 12 and 24 hours (the slower side). It updates with "
     "market check (see Refresh on the Alerts tab), or when you click "
     "Refresh. Double-click "
     "a row to open the item in Search.\n\n"
     "Target prices: select a row and click Set target to be told when the "
     "item reaches a price. Buy ≤ watches for the Buy price falling to your "
     "target, and Sell ≥ for the Sell price rising to it. Type 19,000, 19k "
     "or 1.5m.\n"
     "    •  A met target gets a ✓ and a gold row, the tab shows how many "
     "items have one, e.g. Watchlist (1), and the taskbar flashes if the app "
     "isn't in front.\n"
     "    •  It's checked on the same timer as alerts, only while the app is "
     "open, against the latest trade. One odd trade can meet a target, so check "
     "the price before acting.\n"
     "    •  A target stays set until you change it. If the price stays there, "
     "or comes back within the hour, it won't flash again.\n\n"
     "Click a column header to sort (oldest added first to start); click "
     "again to reverse. Verdict sorts by rank, as on FlipRec."),

    ("Volatile — big movers",
     "Scans every item on the Grand Exchange for large price moves. Pick "
     "a window (5m / 1h / 6h / 24h) to compare the current price with "
     "that window's average. You'll see the top 100 movers by % change, "
     "green for up and red for down.\n\n"
     "Items with under 1,000 units traded in the window are left out to "
     "cut noise from rarely traded items. Click a header to sort; "
     "double-click a row to open it in Search."),

    ("Search — look up any item and see its chart",
     "Start typing an item name and pick from the dropdown — 'Bandos' "
     "shows all Bandos gear, 'Bandos chest' narrows it down. Click a "
     "row, or press Enter for the top match; Escape closes the "
     "dropdown.\n\n"
     "The result panel shows:\n"
     "    •  High buy / Low sell — the latest instant-buy and "
     "instant-sell prices.\n"
     "    •  Spread — the gap between them (before tax).\n"
     "    •  Prices — the dropdown by the search box shows these and the "
     "chart's prices as Exact (1,240) or Short (1.2K). It's separate from the "
     "setting on FlipRec and Alerts.\n"
     "    •  Last trade — how long ago the older side last traded, and each "
     "side separately. ⏱ means over 5 minutes, so the prices may have "
     "moved.\n"
     "    •  Price move — how far the price has moved over the last 30 hours "
     "or so, counted back from now.\n"
     "    •  Volume — units traded in the last 1, 12 and 24 hours, split "
     "into instant buys and instant sells.\n"
     "    •  Momentum — whether prices in the later half of that time are "
     "higher or lower than in the earlier half.\n\n"
     "Click ☆ Add to Watchlist to follow the item on the Watchlist tab.\n\n"
     "Below the result is the item's price chart (high, low and mid). "
     "Choose a range: 1H, 12H, 1D, 1W, 1M, 1Y or All. The history comes "
     "from the OSRS Wiki, so the chart is filled in even the first time "
     "you search an item. Short ranges use 5-minute data, 1W hourly, 1M "
     "6-hourly, and 1Y / All daily; All goes back about a year. Refresh "
     "chart re-downloads it.\n\n"
     "Zoom in: scroll the mouse wheel over the chart (it zooms around the "
     "pointer), then drag left or right to move along. The price axis "
     "rescales to what's in view. Double-click the chart or press Reset "
     "zoom to see the whole range again."),

    ("Why there's no Buy or Sell signal",
     "Search shows facts rather than a verdict. The app used to score items "
     "as BUY, HOLD or SELL, treating a rising price as a reason to buy. "
     "Replayed over 100 days of real prices, that was backwards: BUY items "
     "did worse than average and SELL items did better.\n\n"
     "Prices often partly reverse after a big move, but usually by less than "
     "the 2% GE tax, so simply doing the opposite doesn't reliably make money "
     "either. Use the margins on FlipRec for flipping decisions."),

    ("Ask AI — suggestions from your own AI account",
     "Ask questions in plain English, such as \"What should I flip with "
     "20m?\" or \"Is now a good time to buy a whip?\" The AI looks up "
     "live prices, flips, big movers, item history, your Watchlist, your "
     "alerts and the news, then answers using those numbers.\n\n"
     "Setting it up:\n"
     "    1.  Choose a Provider: Anthropic, OpenAI, OpenRouter, or "
     "OpenAI-compatible for any other service with an OpenAI-compatible API "
     "(such as Groq, or a local server such as Ollama).\n"
     "    2.  Click API key… and paste a key from that service. For "
     "OpenAI-compatible, also enter the service's API URL. A Claude or "
     "ChatGPT subscription doesn't include API use, so you may need to add "
     "credit to your API account.\n"
     "    3.  Choose a Model, or type the ID of another one. The model must "
     "support tools (also called function calling), because that's how it "
     "reads the app's data. Faster models usually cost less. Changing the "
     "model starts a new conversation.\n\n"
     "What it costs: the service bills your account for each question. "
     "The token count shown after each answer, when the service reports one, "
     "is what that question used. "
     "Longer conversations cost more per question, so click New chat when "
     "you change topic.\n\n"
     "Your key is kept in your computer's credential store (Windows "
     "Credential Manager or macOS Keychain), not in your settings file, "
     "and is only sent to the service you chose. Remove it any time from "
     "the API key… window.\n\n"
     "What gets shared: your question, plus whatever the AI looks up: "
     "market data, your capital and item filter, Watchlist, alerts and news "
     "headlines. Your contact is not shared. Services like OpenRouter pass "
     "it on to whichever company runs the model you pick.\n\n"
     "The AI can read this data but can't change anything in the app or "
     "place trades. It can make mistakes, so check the numbers in game "
     "before you trade."),

    ("News",
     "The latest official OSRS news loads automatically when the app "
     "starts. Refresh from sources fetches it again; Reload cache "
     "re-reads what's already saved. Double-click an article to open it "
     "in your browser."),

    ("How fresh is the data?",
     "The OSRS Wiki updates prices from the game about every 60 "
     "seconds. Volumes are true rolling windows, built from the Wiki's "
     "5-minute and hourly data. The first launch downloads a day of it in "
     "the background (about a minute), and until then the 12h and 24h "
     "numbers and the ▲▼ arrows stay blank. A * on a volume means the Wiki "
     "is missing part of that period, so it may read slightly low. FlipRec fetches when you open it or click Refresh; Alerts "
     "and Watchlist update on the timer you choose on Alerts (every minute "
     "by default); Search is live each time. "
     "Last trade (FlipRec, Alerts, Watchlist, Search) shows exactly how "
     "old each margin "
     "is — always Refresh before you trade."),

    ("Your contact",
     "The OSRS Wiki provides the price data for free and asks every app "
     "to identify itself with a way to reach whoever is running it. The "
     "contact you entered on first launch is sent with every request "
     "as:\n\n"
     "    OSRS-Price-Tracker/<version> (contact: <your contact>)\n\n"
     "It goes to the OSRS Wiki (prices) and runescape.com (news). Use "
     "Change contact… at the top of this tab to update it."),

    ("Your settings and data",
     "Your contact, Capital, filters, price format and alert threshold "
     "are saved automatically, along with your watchlist, alerts and "
     "price history. They're stored on your computer in:\n\n"
     "    •  Windows: %APPDATA%\\OSRS Tracker\n"
     "    •  macOS: ~/Library/Application Support/OSRS Tracker"),

    ("Updates, logs and your data folder",
     "The top of this tab shows your version and three tools:\n\n"
     "    •  Check for updates — looks for a newer release on the project's "
     "GitHub page. On Windows it downloads the installer, closes the app, "
     "updates it and reopens it. On macOS it downloads and opens the disk "
     "image: quit the app and drag the new version into Applications. "
     "Only the app name and version are sent to GitHub.\n"
     "    •  Open data folder — your settings, watchlist, alerts, price "
     "history and log file.\n"
     "    •  Log file (osrs_tracker.log) — if something goes wrong, the "
     "details are saved here. Attach it when reporting a problem on the "
     "project's GitHub Issues page."),

    ("Disclaimer",
     "Estimates only — offers can take time to fill and margins can "
     "close before they do. This is a personal-use tool. It is not "
     "affiliated with or endorsed by Jagex. Old School RuneScape and "
     "related marks are property of Jagex Ltd."),
]


# ── Main Application ──────────────────────────────────────────────────────────

class OSRSTrackerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.item_data = []
        self.filtered_items = []  # current dropdown contents

        self.ready = False
        self._market = None            # (latest, bulk_1h, fetched_at, bulk_5m)
        self._monitor_started = False
        self._monitor_busy = False
        self._monitor_job = None       # the pending timer for the next check
        self._trade_ages = {}          # tree -> {row id: traded_at}
        self._volume = volume.VolumeHistory(
            lambda: ui_call(self._on_volume_changed))
        self._volume_ready = frozenset()
        self._volume_loaded = False    # the first download has finished
        self._search_volume = None     # (frame, item_id, cells) showing the table
        self._search_trade = None      # (label, high time, low time)
        self._search_item = None       # item shown in the Search panel
        self._search_shown = None      # what the panel was last drawn from
        self._lookup_seq = 0           # latest Search lookup (see _lookup_item)
        self._error_shown = False
        # Errors inside button clicks / timers: log them and tell the user
        # once, instead of failing silently in the windowed app.
        self.root.report_callback_exception = self._on_tk_error

        self._configure_root()
        self._build_ui()

        db.initialize_db()
        db.expire_stale_alerts(self._alert_stale_after())
        # A contact is required before any network request (see useragent.py).
        if not self._ensure_contact():
            self.root.destroy()
            return
        self.ready = True
        run_ui_queue(self.root)
        self.root.after(TRADE_AGE_TICK_MS, self._tick_trade_ages)
        self._volume.start()
        self._load_mapping_async()
        self._refresh_news_from_sources()   # news is ready before you open it

    # ── Contact / User-Agent setup ────────────────────────────────────────────

    def _ensure_contact(self) -> bool:
        """Apply the saved contact, or run first-time setup. False = quit."""
        saved = settings.load().get("ua_contact", "")
        if useragent.is_valid_contact(saved):
            useragent.set_contact(saved)
            self._update_contact_label()
            return True
        self.root.withdraw()
        ok = self._contact_dialog(first_run=True)
        if ok:
            self.root.deiconify()
        return ok

    def _contact_dialog(self, first_run: bool) -> bool:
        """Modal dialog to enter/change the contact. True if saved."""
        dlg = tk.Toplevel(self.root)
        dlg.title("OSRS Price Tracker — Setup" if first_run
                  else "Change contact")
        dlg.configure(bg=COLOR_BG)
        dlg.resizable(False, False)
        if not first_run:
            dlg.transient(self.root)   # (root is hidden on first run)

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=24, pady=20)

        ttk.Label(body, text="Set your contact" if first_run
                  else "Change your contact",
                  style="Header.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(body, wraplength=480, justify="left", text=(
            "The OSRS Wiki provides this app's price data for free and asks "
            "every app to identify itself with a way to contact whoever is "
            "running it. Enter a contact the Wiki team could reach you on, "
            "such as your Discord name or an email address.")
                  ).pack(anchor="w")
        tk.Label(body, bg=COLOR_BG, fg=COLOR_YELLOW, font=FONT_BODY,
                 wraplength=480, justify="left", text=(
                     "Privacy: this is sent with every request to the OSRS "
                     "Wiki (prices) and runescape.com (news). Don't enter "
                     "anything you want kept private.")
                 ).pack(anchor="w", pady=(10, 12))

        current = settings.load().get("ua_contact", "")
        var = tk.StringVar(value=current)
        entry = ttk.Entry(body, textvariable=var, width=48)
        entry.pack(anchor="w", fill="x")
        entry.focus_set()

        preview = tk.StringVar()
        ttk.Label(body, textvariable=preview, style="Dim.TLabel",
                  wraplength=480, justify="left"
                  ).pack(anchor="w", pady=(8, 0))
        error = tk.StringVar()
        tk.Label(body, textvariable=error, bg=COLOR_BG, fg=COLOR_RED,
                 font=FONT_BODY).pack(anchor="w", pady=(4, 0))

        def update_preview(*_):
            cleaned = useragent.clean_contact(var.get())
            preview.set(f"Sent as:  {useragent.build(var.get())}"
                        if cleaned else "Sent as:  (enter a contact)")
            error.set("")
        var.trace_add("write", update_preview)
        update_preview()

        result = {"ok": False}

        def save(_e=None):
            if not useragent.is_valid_contact(var.get()):
                error.set(f"Enter at least {useragent.CONTACT_MIN_LEN} "
                          "letters, numbers or symbols (no emoji).")
                return
            cleaned = useragent.clean_contact(var.get())
            useragent.set_contact(cleaned)
            settings.save({"ua_contact": cleaned})
            self._update_contact_label()
            result["ok"] = True
            dlg.destroy()

        def cancel(_e=None):
            dlg.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(anchor="e", pady=(14, 0))
        ttk.Button(buttons, text="Quit" if first_run else "Cancel",
                   command=cancel).pack(side="right")
        ttk.Button(buttons, text="Save and continue" if first_run else "Save",
                   command=save).pack(side="right", padx=(0, 8))
        dlg.bind("<Return>", save)
        dlg.bind("<Escape>", cancel)
        dlg.protocol("WM_DELETE_WINDOW", cancel)

        dlg.update_idletasks()
        x = (dlg.winfo_screenwidth() - dlg.winfo_reqwidth()) // 2
        y = (dlg.winfo_screenheight() - dlg.winfo_reqheight()) // 3
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()
        dlg.lift()
        self.root.wait_window(dlg)
        return result["ok"]

    def _update_contact_label(self):
        if hasattr(self, "contact_label_var"):
            self.contact_label_var.set(
                f"Your contact:  {settings.load().get('ua_contact', '(not set)')}")

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _configure_root(self):
        self.root.title("OSRS Price Tracker")
        # Big enough for the Search panel + chart, but never off-screen
        # (120px vertical margin leaves room for taskbars / DPI scaling).
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{min(1250, sw - 80)}x{min(860, sh - 120)}")
        # A fixed floor, so small screens can still shrink or tile the window.
        self.root.minsize(min(900, sw - 80), min(560, sh - 120))
        self.root.configure(bg=COLOR_BG)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Frames
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Panel.TFrame", background=COLOR_PANEL,
                        borderwidth=1, relief="solid",
                        bordercolor=COLOR_BORDER)
        # Sections inside a panel: same background, no extra border.
        style.configure("Inner.TFrame", background=COLOR_PANEL, borderwidth=0)
        style.configure("Sub.TFrame", background=COLOR_PANEL_HI)

        # Labels
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                        font=FONT_BODY)
        style.configure("Panel.TLabel", background=COLOR_PANEL,
                        foreground=COLOR_TEXT, font=FONT_BODY)
        style.configure("Dim.TLabel", background=COLOR_BG,
                        foreground=COLOR_DIM, font=FONT_BODY)
        style.configure("Header.TLabel", background=COLOR_BG,
                        foreground=COLOR_ACCENT, font=FONT_HEADER)
        style.configure("Item.TLabel", background=COLOR_PANEL,
                        foreground=COLOR_ACCENT, font=FONT_TITLE)
        style.configure("Stat.TLabel", background=COLOR_PANEL,
                        foreground=COLOR_TEXT, font=FONT_LABEL)
        style.configure("StatVal.TLabel", background=COLOR_PANEL,
                        foreground=COLOR_TEXT, font=FONT_VALUE)
        style.configure("PanelHeader.TLabel", background=COLOR_PANEL,
                        foreground=COLOR_ACCENT,
                        font=("Georgia", 12, "bold"))

        # Buttons
        style.configure("TButton",
                        background=COLOR_PANEL_HI,
                        foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER,
                        focuscolor=COLOR_ACCENT,
                        padding=(12, 6),
                        font=("Helvetica", 11, "bold"))
        style.map("TButton",
                  background=[("active", COLOR_BORDER),
                              ("pressed", COLOR_PANEL)],
                  foreground=[("disabled", COLOR_DIM),
                              ("active", COLOR_ACCENT_HI)])

        # Entry / Combobox
        style.configure("TEntry",
                        fieldbackground=COLOR_INPUT,
                        foreground=COLOR_TEXT,
                        insertcolor=COLOR_ACCENT,
                        bordercolor=COLOR_BORDER,
                        lightcolor=COLOR_BORDER,
                        darkcolor=COLOR_BORDER,
                        padding=6,
                        font=FONT_BODY)
        style.configure("TCombobox",
                        fieldbackground=COLOR_INPUT,
                        background=COLOR_PANEL_HI,
                        foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER,
                        arrowcolor=COLOR_ACCENT)
        # Read-only comboboxes otherwise render pale text on a light field.
        style.map("TCombobox",
                  fieldbackground=[("readonly", COLOR_INPUT)],
                  foreground=[("readonly", COLOR_TEXT)],
                  selectbackground=[("readonly", COLOR_INPUT)],
                  selectforeground=[("readonly", COLOR_TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", COLOR_INPUT)
        self.root.option_add("*TCombobox*Listbox.foreground", COLOR_TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground",
                             COLOR_BORDER)
        self.root.option_add("*TCombobox*Listbox.selectForeground",
                             COLOR_ACCENT_HI)

        style.configure("TSpinbox",
                        fieldbackground=COLOR_INPUT,
                        background=COLOR_PANEL_HI,
                        foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER,
                        arrowcolor=COLOR_ACCENT)
        style.map("TSpinbox",
                  fieldbackground=[("readonly", COLOR_INPUT)],
                  foreground=[("readonly", COLOR_TEXT)])

        # Time-range buttons (Search tab chart)
        style.configure("Range.TButton", padding=(10, 4))
        style.configure("RangeSel.TButton", padding=(10, 4),
                        background=COLOR_ACCENT, foreground=COLOR_BG)
        style.map("RangeSel.TButton",
                  background=[("active", COLOR_ACCENT_HI)],
                  foreground=[("active", COLOR_BG)])

        # Notebook
        style.configure("TNotebook", background=COLOR_BG, borderwidth=0,
                        tabmargins=(2, 4, 2, 0))
        style.configure("TNotebook.Tab",
                        background=COLOR_PANEL,
                        foreground=COLOR_DIM,
                        padding=(18, 8),
                        font=("Georgia", 11, "bold"),
                        bordercolor=COLOR_BORDER)
        style.map("TNotebook.Tab",
                  background=[("selected", COLOR_PANEL_HI)],
                  foreground=[("selected", COLOR_ACCENT)],
                  expand=[("selected", (1, 1, 1, 0))])

        # Treeview
        style.configure("Treeview",
                        background=COLOR_PANEL,
                        fieldbackground=COLOR_PANEL,
                        foreground=COLOR_TEXT,
                        rowheight=28,
                        font=FONT_BODY,
                        bordercolor=COLOR_BORDER)
        style.configure("Treeview.Heading",
                        background=COLOR_PANEL_HI,
                        foreground=COLOR_ACCENT,
                        font=("Georgia", 10, "bold"),
                        bordercolor=COLOR_BORDER,
                        padding=6)
        style.map("Treeview",
                  background=[("selected", COLOR_BORDER)],
                  foreground=[("selected", COLOR_ACCENT_HI)])

        # Scrollbar
        style.configure("Vertical.TScrollbar",
                        background=COLOR_PANEL_HI,
                        troughcolor=COLOR_PANEL,
                        bordercolor=COLOR_BORDER,
                        arrowcolor=COLOR_ACCENT)

    def _build_ui(self):
        # Header bar
        header = ttk.Frame(self.root)
        header.pack(side="top", fill="x", padx=20, pady=(16, 8))

        ttk.Label(header, text="⚔  OSRS Price Tracker  ⚔",
                  style="Header.TLabel").pack(side="left")
        self.status_var = tk.StringVar(value="Loading item mapping…")
        ttk.Label(header, textvariable=self.status_var,
                  style="Dim.TLabel").pack(side="right")

        # Subtle divider
        divider = tk.Frame(self.root, bg=COLOR_BORDER, height=1)
        divider.pack(side="top", fill="x", padx=20)

        # Notebook
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=20, pady=(8, 20))

        # Price format (Short 1.2K / Exact 1,240): one setting, with a
        # dropdown on FlipRec and Alerts, also used by Watchlist.
        self.flip_format_var = tk.StringVar(
            value=settings.load().get("flip_price_format", PRICE_FORMATS[0]))
        # Search has its own, so its full prices don't depend on how compact
        # the tables are set to be.
        self.search_format_var = tk.StringVar(
            value=settings.load().get("search_price_format", PRICE_FORMATS[1]))

        self.search_tab = self._build_search_tab()
        self.ai_tab = self._build_ai_tab()
        self.alerts_tab = self._build_alerts_tab()
        self.watchlist_tab = self._build_watchlist_tab()
        self.volatile_tab = self._build_volatile_tab()
        self.fliprec_tab = self._build_fliprec_tab()
        self.news_tab = self._build_news_tab()
        self.help_tab = self._build_help_tab()

        self.notebook.add(self.fliprec_tab, text="FlipRec")
        self.notebook.add(self.alerts_tab, text="Alerts")
        self.notebook.add(self.watchlist_tab, text="Watchlist")
        self.notebook.add(self.volatile_tab, text="Volatile")
        self.notebook.add(self.search_tab, text="Search")
        self.notebook.add(self.ai_tab, text="Ask AI")
        self.notebook.add(self.news_tab, text="News")
        self.notebook.add(self.help_tab, text="Help")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ── Mapping load ──────────────────────────────────────────────────────────

    def _load_mapping_async(self):
        def done(data, err):
            if err:
                self.status_var.set(f"Failed to load items: {err}")
                messagebox.showerror("Mapping load failed", str(err))
                return
            self.item_data = data
            self.status_var.set(f"{len(data):,} items loaded.")
            self.search_entry.config(state="normal")
            # Tabs that need the mapping may have been opened while it loaded
            # (FlipRec is the first tab), so load whichever one is showing.
            self._on_tab_changed(None)
            if not self._monitor_started:
                self._monitor_started = True
                self._monitor_tick()

        run_async(api.get_mapping, done)

    # ── Search tab (with autocomplete) ────────────────────────────────────────

    def _build_search_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        # Search bar
        bar = ttk.Frame(frame)
        bar.pack(side="top", fill="x", padx=20, pady=(20, 4))

        ttk.Label(bar, text="Search:", style="TLabel").pack(side="left",
                                                            padx=(0, 8))

        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var,
                                      state="disabled", width=50,
                                      font=FONT_BODY)
        self.search_entry.pack(side="left", fill="x", expand=True,
                               padx=(0, 16))
        self.search_entry.bind("<KeyRelease>", self._on_search_keyrelease)
        self.search_entry.bind("<Down>", self._focus_dropdown)
        self.search_entry.bind("<Return>", self._on_search_enter)
        self.search_entry.bind("<Escape>", lambda _e: self._hide_dropdown())
        self._price_format_picker(bar, self.search_format_var,
                                  self._on_search_format_changed, trailing=0)

        # Dropdown listbox (always present, packed/unpacked as needed)
        self.dropdown_frame = ttk.Frame(frame)

        self.dropdown_listbox = tk.Listbox(
            self.dropdown_frame, height=10,
            background=COLOR_INPUT, foreground=COLOR_TEXT,
            selectbackground=COLOR_BORDER, selectforeground=COLOR_ACCENT_HI,
            highlightthickness=1, highlightcolor=COLOR_BORDER,
            highlightbackground=COLOR_BORDER,
            borderwidth=0, font=FONT_BODY,
            activestyle="none",
        )
        scroll = ttk.Scrollbar(self.dropdown_frame, orient="vertical",
                                command=self.dropdown_listbox.yview)
        self.dropdown_listbox.configure(yscrollcommand=scroll.set)
        self.dropdown_listbox.pack(side="left", fill="x", expand=True)
        scroll.pack(side="right", fill="y")

        self.dropdown_listbox.bind("<Double-Button-1>",
                                    lambda _e: self._pick_from_dropdown())
        self.dropdown_listbox.bind("<Return>",
                                    lambda _e: self._pick_from_dropdown())
        self.dropdown_listbox.bind("<Escape>",
                                    lambda _e: self._hide_dropdown())
        self.dropdown_listbox.bind("<Up>", self._dropdown_up)

        # Result panel
        self.result_frame = ttk.Frame(frame, style="Panel.TFrame")
        self.result_frame.pack(side="top", fill="x", padx=20, pady=(10, 6))

        self.placeholder_label = ttk.Label(
            self.result_frame,
            text=("\nStart typing to search — see live prices, a "
                  "buy/hold/sell signal and the item's price chart.\n"),
            style="Panel.TLabel", justify="center",
        )
        self.placeholder_label.pack(expand=True, pady=10)

        # Price chart (formerly the History tab)
        self.history_item = None          # (item_id, name) being charted
        self.history_range = HISTORY_DEFAULT_RANGE
        self.history_range_btns = {}
        self._history_fetched = set()     # (item_id, step) pulled this session
        self._chart_rows = None           # rows for the item/range on screen
        self._chart_font = tkfont.Font(font=FONT_SMALL)
        self._chart_view = None           # (t0, t1) when zoomed, else None
        self._chart_geom = None           # plot layout, for mouse handlers
        self._chart_drag = None           # (start x, view) while panning

        range_bar = ttk.Frame(frame)
        range_bar.pack(side="top", fill="x", padx=20, pady=(0, 6))
        ttk.Label(range_bar, text="Chart range:", style="TLabel").pack(
            side="left", padx=(0, 8))
        for label, _secs, _step in HISTORY_RANGES:
            btn = ttk.Button(range_bar, text=label, style="Range.TButton", width=4,
                             command=lambda l=label: self._set_history_range(l))
            btn.pack(side="left", padx=(0, 4))
            self.history_range_btns[label] = btn
        self._update_range_buttons()
        ttk.Button(range_bar, text="Refresh chart",
                   command=self._refresh_history_chart
                   ).pack(side="left", padx=(12, 0))
        ttk.Button(range_bar, text="Reset zoom",
                   command=self._reset_chart_zoom
                   ).pack(side="left", padx=(6, 0))

        chart_frame = ttk.Frame(frame, style="Panel.TFrame")
        chart_frame.pack(side="top", fill="both", expand=True,
                         padx=20, pady=(0, 20))
        self.history_canvas = tk.Canvas(
            chart_frame, bg=COLOR_PANEL, highlightthickness=0, height=240)
        self.history_canvas.pack(fill="both", expand=True, padx=10, pady=10)
        # Every resize redraws, including the first time the canvas is shown,
        # so the placeholder is centred at the real size.
        self.history_canvas.bind("<Configure>",
                                 lambda _e: self._draw_history_chart())
        self.history_canvas.bind("<MouseWheel>", self._on_chart_wheel)
        self.history_canvas.bind("<ButtonPress-1>", self._on_chart_press)
        self.history_canvas.bind("<B1-Motion>", self._on_chart_drag)
        self.history_canvas.bind("<ButtonRelease-1>", self._on_chart_release)
        self.history_canvas.bind("<Double-Button-1>",
                                 lambda _e: self._reset_chart_zoom())

        return frame

    def _on_search_keyrelease(self, event):
        # Ignore navigation keys handled separately
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return

        query = self.search_var.get().strip()
        if not query:
            self._hide_dropdown()
            return

        if not self.item_data:
            return

        matches = search_module.search_items(
            query, self.item_data, min_score=0.40, top_n=200
        )

        # Also include substring matches that didn't beat the score floor,
        # so partial typing like "Band" still surfaces "Bandos" items.
        if not matches:
            ql = query.lower()
            substr = [(1.0, it) for it in self.item_data
                      if ql in it["name"].lower()][:200]
            matches = substr

        self.filtered_items = matches
        if not matches:
            self._show_dropdown_with(["No matches"])
            return

        labels = [f"{item['name']}" for _score, item in matches[:200]]
        self._show_dropdown_with(labels)

    def _show_dropdown_with(self, labels):
        self.dropdown_listbox.delete(0, "end")
        for lbl in labels:
            self.dropdown_listbox.insert("end", lbl)
        if not self.dropdown_frame.winfo_ismapped():
            # Float the dropdown over the page under the search box, so it
            # doesn't push the result panel down and squeeze the chart.
            self.dropdown_frame.place(in_=self.search_entry, relx=0, rely=1,
                                      relwidth=1, y=2)
            self.dropdown_frame.lift()
        # auto-select top
        if self.dropdown_listbox.size() > 0:
            self.dropdown_listbox.selection_clear(0, "end")
            self.dropdown_listbox.selection_set(0)

    def _hide_dropdown(self):
        if self.dropdown_frame.winfo_ismapped():
            self.dropdown_frame.place_forget()
        self.filtered_items = []

    def _focus_dropdown(self, _event):
        if self.dropdown_listbox.size() > 0:
            self.dropdown_listbox.focus_set()
            self.dropdown_listbox.selection_clear(0, "end")
            self.dropdown_listbox.selection_set(0)
            self.dropdown_listbox.activate(0)
        return "break"

    def _dropdown_up(self, _event):
        sel = self.dropdown_listbox.curselection()
        if sel and sel[0] == 0:
            self.search_entry.focus_set()
            return "break"

    def _on_search_enter(self, _event):
        # Enter from the entry picks the top dropdown item
        if self.filtered_items:
            self._lookup_item(self.filtered_items[0][1])
            self._hide_dropdown()

    def _pick_from_dropdown(self):
        sel = self.dropdown_listbox.curselection()
        if not sel or not self.filtered_items:
            return
        idx = sel[0]
        if idx >= len(self.filtered_items):
            return
        _score, item = self.filtered_items[idx]
        self._hide_dropdown()
        self.search_var.set(item["name"])
        self._lookup_item(item)

    def _lookup_item(self, item: dict):
        self._render_message(f"Fetching {item['name']}…")
        item_id = item["id"]
        item_name = item["name"]

        # Each lookup gets a number; a slower, older lookup finishing late
        # must not overwrite the panel for the item now being shown.
        self._lookup_seq += 1
        seq = self._lookup_seq

        # The chart follows the latest search. This lookup downloads the
        # hourly history itself, so mark it fetched (no duplicate request)
        # and draw once that data has arrived.
        self.history_item = (item_id, item_name)
        self._chart_view = None
        self._history_fetched.add((item_id, TIMESERIES_STEP))
        self._history_placeholder(f"Loading {item_name} chart…")

        def fetch():
            price_data = api.get_latest_price(item_id)
            if not price_data or not price_data.get("high"):
                return None

            high = price_data["high"]
            low = price_data.get("low") or high
            db.record_price(item_id, item_name, high, low)

            # Pull historical points from the API so a first-time search
            # already has history (deduped by timestamp).
            ts_data = api.get_timeseries(item_id, timestep=TIMESERIES_STEP)
            db.backfill_price_history(item_id, item_name, ts_data,
                                    TIMESERIES_STEP)

            trend, facts = analyzer.recent_market(
                api.get_timeseries(item_id, timestep="5m"),
                db.get_price_history(item_id, limit=30), ts_data,
                live=(high, low))

            return {
                "item": item, "price_data": price_data,
                "trend": trend, "facts": facts,
            }

        def done(payload, err):
            if seq != self._lookup_seq:
                return              # a newer search has replaced this one
            # Draw the chart whatever happened: stored history may exist
            # even if the live price fetch failed.
            self._redraw_chart_if_current(item_id)
            if err:
                self._render_message(f"Error: {err}")
                return
            if payload is None:
                self._render_message(f"No live price data for {item_name}.")
                return
            self._render_result(**payload)

        run_async(fetch, done)

    def _render_message(self, text: str):
        self._search_shown = None
        for child in self.result_frame.winfo_children():
            child.destroy()
        ttk.Label(self.result_frame, text=text,
                  style="Panel.TLabel", justify="center"
                  ).pack(expand=True, pady=20)

    def _render_result(self, item, price_data, trend, facts):
        self._search_shown = {"item": item, "price_data": price_data,
                              "trend": trend, "facts": facts}
        for child in self.result_frame.winfo_children():
            child.destroy()

        name = item["name"]
        high = price_data.get("high") or 0
        low = price_data.get("low") or high
        spread = high - low
        spread_pct = (spread / low * 100) if low else 0
        fmt = self._search_price_fmt()

        # Header
        header = ttk.Frame(self.result_frame, style="Inner.TFrame")
        header.pack(fill="x", padx=16, pady=(12, 6))
        ttk.Label(header, text=name, style="Item.TLabel").pack(side="left")

        self._search_item = item
        self.watch_btn = ttk.Button(header, command=self._toggle_watch)
        self.watch_btn.pack(side="right")
        self._update_watch_btn()

        # Stats grid
        body = ttk.Frame(self.result_frame, style="Inner.TFrame")
        body.pack(fill="x", padx=16, pady=(0, 12))
        grid = ttk.Frame(body, style="Inner.TFrame")
        grid.pack(side="left", anchor="n")

        rows = [
            ("High buy", f"{fmt(high)} gp", COLOR_GREEN),
            ("Low sell", f"{fmt(low)} gp", COLOR_RED),
            ("Spread",   f"{fmt(spread)} gp  ({spread_pct:.1f}%)", COLOR_TEXT),
            ("Last trade", "", COLOR_TEXT),
            ("Price move", fmt_price_move(trend), COLOR_TEXT),
            ("Volume", fmt_volume_pace(facts), COLOR_TEXT),
            ("Momentum", {"rising": "Rising lately", "falling": "Falling lately"
                          }.get(facts["momentum"], "Flat"), COLOR_TEXT),
        ]

        for i, (label, value, color) in enumerate(rows):
            ttk.Label(grid, text=label, style="Stat.TLabel"
                      ).grid(row=i, column=0, sticky="w", padx=(0, 24), pady=2)
            value_label = tk.Label(grid, text=value, bg=COLOR_PANEL, fg=color,
                                   font=FONT_VALUE)
            value_label.grid(row=i, column=1, sticky="w", pady=2)
            if label == "Last trade":
                self._search_trade = (value_label, price_data.get("highTime"),
                                      price_data.get("lowTime"))
                self._show_search_trade()

        right = ttk.Frame(body, style="Inner.TFrame")
        right.pack(side="left", anchor="n", fill="x", expand=True, padx=(40, 0))

        volume_frame = ttk.Frame(right, style="Inner.TFrame")
        volume_frame.pack(anchor="w", pady=(0, 10))
        self._search_volume = (volume_frame, item["id"], {})
        self._render_search_volume()

        # Notes
        if facts["notes"]:
            reasons_frame = ttk.Frame(right, style="Inner.TFrame")
            reasons_frame.pack(anchor="w", fill="x")
            ttk.Label(reasons_frame, text="Worth knowing:",
                      style="PanelHeader.TLabel"
                      ).pack(anchor="w")
            for r in facts["notes"]:
                ttk.Label(reasons_frame, text=f"  •  {r}",
                          style="Panel.TLabel",
                          wraplength=330, justify="left"
                          ).pack(anchor="w")

    def _render_search_volume(self):
        """The units-traded table on the Search panel. Widgets are reused:
        Tk never frees a widget's name, so rebuilding it forever would leak."""
        if not self._search_volume or not self._search_volume[0].winfo_exists():
            return
        frame, item_id, cells = self._search_volume
        if not cells:
            ttk.Label(frame, text="Volume", style="PanelHeader.TLabel"
                      ).grid(row=0, column=0, sticky="w", padx=(0, 18))
            for col, (label, _s) in enumerate(volume.WINDOWS, start=1):
                ttk.Label(frame, text=label, style="PanelHeader.TLabel"
                          ).grid(row=0, column=col, sticky="e", padx=(16, 0))
            for side, label in enumerate(("Instant buys", "Instant sells")):
                ttk.Label(frame, text=label, style="Stat.TLabel"
                          ).grid(row=side + 1, column=0, sticky="w",
                                 padx=(0, 18), pady=1)
                for col, (_l, seconds) in enumerate(volume.WINDOWS, start=1):
                    cells[side, seconds] = tk.Label(frame, bg=COLOR_PANEL,
                                                    font=FONT_VALUE)
                    cells[side, seconds].grid(row=side + 1, column=col,
                                              sticky="e", padx=(16, 0), pady=1)
            cells["note"] = ttk.Label(frame, style="Panel.TLabel", wraplength=420,
                                      justify="left", foreground=COLOR_DIM)
            cells["note"].grid(row=3, column=0, columnspan=4, sticky="w")
        for key, cell in cells.items():
            if key != "note":
                side, seconds = key
                sides = self._volume.sides(item_id, seconds)
                cell.config(
                    text=(self._window_text(sides[side], seconds, fmt_gp)
                          if sides else "-"),
                    fg=COLOR_TEXT if sides else COLOR_DIM)
        cells["note"].config(text=self._volume_note(
            [seconds for _l, seconds in volume.WINDOWS]))

    def _window_text(self, value, seconds: int, fmt) -> str:
        """A window's number: * if the Wiki is missing part of that window."""
        if value is None:
            return "-"
        return fmt(value) + ("*" if self._volume.is_partial(seconds) else "")

    def _volume_note(self, windows) -> str:
        """The explanation for any * being shown, otherwise nothing."""
        if any(self._volume.is_partial(seconds) for seconds in windows):
            return ("* Some Wiki data is missing for this period, so this may "
                    "read slightly low.")
        return ""

    def _on_volume_changed(self):
        """A new period of volume history arrived."""
        ready = self._volume.ready_windows()
        if self.item_data and not self._volume_loaded:
            if 24 * 3600 in ready:
                self._volume_loaded = True
                self.status_var.set(f"{len(self.item_data):,} items loaded.")
            else:
                have, wanted = self._volume.progress()
                self.status_var.set(f"Loading volume history… {have}/{wanted}")
        self._render_search_volume()
        if ready != self._volume_ready:
            # New columns can fill in. Otherwise the tables update on the
            # next market check, so they don't redraw every 5 minutes as well.
            self._volume_ready = ready
            self._render_alerts()
            self._render_watchlist()
            if self.flip_results:
                self._render_fliprec_table()

    def _volume_text(self, item_id, volume_1h, fmt) -> str:
        """The Vol 1h cell: the amount, with an arrow if it's unusual. No saved
        amount (an alert from before this column existed) isn't zero."""
        if volume_1h is None:
            return "-"
        pace = analyzer.volume_pace(
            volume_1h, self._volume.slower_side(item_id, 24 * 3600))
        return f"{self._window_text(volume_1h, 3600, fmt)} {pace}".rstrip()

    def _hour_and_5m_averages(self) -> tuple:
        """Averages for pricing flips (see VolumeHistory.averages)."""
        return self._volume.averages("1h"), self._volume.averages("5m")

    def _show_search_trade(self):
        label, high_time, low_time = self._search_trade
        if not high_time or not low_time:
            label.config(text="Unknown", fg=COLOR_DIM)
            return
        older = fmt_last_trade(min(high_time, low_time))
        late = older.startswith("⏱")
        label.config(
            text=(f"{older} ago  (high {fmt_age(time.time() - high_time)}, "
                  f"low {fmt_age(time.time() - low_time)})"),
            fg=COLOR_YELLOW if late else COLOR_TEXT)

    def _tick_trade_ages(self):
        """Prices only change on a check, but the trades keep getting older."""
        now = time.time()
        for tree, ages in self._trade_ages.items():
            for iid, traded_at in ages.items():
                tree.set(iid, "age", fmt_last_trade(traded_at, now))
        if self._search_trade and self._search_trade[0].winfo_exists():
            self._show_search_trade()
        self.root.after(TRADE_AGE_TICK_MS, self._tick_trade_ages)

    # ── Alerts tab ────────────────────────────────────────────────────────────

    ALERT_COLUMNS = [
        # (tree id, header, sort key, width, anchor, descending on 1st click)
        ("item",   "Item",        "item_name", 230, "w", False),
        ("buy",    "Buy (gp)",    "buy",       95,  "e", True),
        ("sell",   "Sell (gp)",   "sell",      95,  "e", True),
        ("net",    "Net / item",  "net",       90,  "e", True),
        ("qty",    "Qty",         "qty",       75,  "e", True),
        ("profit", "Est. profit", "profit",    115, "e", True),
        ("roi",    "ROI",         "roi",       65,  "e", True),
        ("volume", "Vol 1h",      "volume",    80,  "e", True),
        ("age",    "Last trade",  "traded_at", 90,  "e", True),
        ("when",   "Alerted",     "timestamp", 110, "w", True),
        ("hits",   "Times",       "n",         60,  "e", True),
    ]

    def _build_alerts_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 6))
        ttk.Label(bar, text="Alert when est. profit ≥", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        saved = settings.load().get("alert_threshold", ALERT_DEFAULT_THRESHOLD)
        self.alert_threshold_var = tk.StringVar(value=f"{int(saved):,}")
        spin = ttk.Spinbox(bar, textvariable=self.alert_threshold_var,
                           values=ALERT_THRESHOLD_STEPS, state="readonly",
                           width=13, wrap=False)
        spin.pack(side="left", padx=(0, 4))
        # Mouse wheel steps the value too (ttk doesn't do this by default).
        spin.bind("<MouseWheel>", lambda e: (spin.event_generate(
            "<<Increment>>" if e.delta > 0 else "<<Decrement>>"), "break")[1])
        # React to every change (arrows, keyboard, wheel), debounced so
        # holding an arrow down doesn't re-query on every step.
        self._threshold_job = None
        self.alert_threshold_var.trace_add(
            "write", lambda *_: self._schedule_threshold_change())
        ttk.Label(bar, text="gp", style="TLabel").pack(side="left",
                                                       padx=(0, 16))
        ttk.Label(bar, text="Refresh:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        interval = settings.load().get("alert_interval")
        self.alert_interval_var = tk.StringVar(
            value=interval if interval in ALERT_INTERVALS
            else ALERT_DEFAULT_INTERVAL)
        interval_combo = ttk.Combobox(
            bar, textvariable=self.alert_interval_var, state="readonly",
            width=7, values=list(ALERT_INTERVALS))
        interval_combo.pack(side="left", padx=(0, 16))
        interval_combo.bind("<<ComboboxSelected>>",
                            lambda _e: self._on_alert_interval_changed())

        self._price_format_picker(bar, self.flip_format_var,
                                  self._on_price_format_changed)

        self.alerts_check_btn = ttk.Button(
            bar, text="Check now", command=self._monitor_tick)
        self.alerts_check_btn.pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Dismiss selected",
                   command=self._dismiss_selected_alerts
                   ).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Dismiss all",
                   command=self._dismiss_all_alerts).pack(side="left")

        # Own line, so the status isn't pushed off the edge of the bar.
        self.alerts_status = tk.StringVar(value="Waiting for first check…")
        ttk.Label(frame, textvariable=self.alerts_status, style="Dim.TLabel"
                  ).pack(anchor="w", padx=20, pady=(0, 6))

        cols = tuple(c[0] for c in self.ALERT_COLUMNS)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        for cid, label, key, width, anchor, desc in self.ALERT_COLUMNS:
            tree.heading(cid, text=label,
                         command=lambda k=key, d=desc: self._sort_alerts(k, d))
            tree.column(cid, width=width, anchor=anchor)
        tree.tag_configure("alert", foreground=COLOR_GREEN)
        tree.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        tree.bind("<Double-Button-1>",
                  lambda _e: self._open_selected_in_search(
                      self.alerts_tree, self.alert_id_map))
        self.alerts_tree = tree
        self.alert_id_map = {}
        # Kept across refreshes; newest first until a header is clicked.
        self.alert_sort = ("timestamp", True)
        self._update_alert_arrows()

        ttk.Label(frame,
                  text=("Prices update with every check while the app is "
                        "open, and an alert disappears once the flip stops "
                        "qualifying. Uses your FlipRec capital and item "
                        "filter. Only items where both sides traded in the "
                        "last 5 minutes; Suspect items are ignored. Each "
                        "item alerts at most once an hour. Double-click a "
                        "row to open it in Search."),
                  style="Dim.TLabel", wraplength=1000, justify="center"
                  ).pack(side="bottom", before=tree, pady=(0, 10))
        return frame

    def _alert_threshold(self) -> int:
        try:
            return int(self.alert_threshold_var.get().replace(",", ""))
        except ValueError:
            return ALERT_DEFAULT_THRESHOLD

    def _schedule_threshold_change(self):
        if self._threshold_job:
            self.root.after_cancel(self._threshold_job)
        self._threshold_job = self.root.after(
            250, self._on_alert_threshold_changed)

    def _on_alert_threshold_changed(self):
        self._threshold_job = None
        settings.save({"alert_threshold": self._alert_threshold()})
        # Re-check the last market snapshot right away with the new threshold.
        if self._evaluate_alerts()[0]:
            flash_window(self.root)
        self._render_alerts()

    def _alert_interval(self) -> int:
        """Seconds between market checks."""
        return ALERT_INTERVALS[self.alert_interval_var.get()]

    def _alert_stale_after(self) -> int:
        """Seconds without a successful check before alerts can't be trusted.
        Allows a missed check or a short Wiki outage without losing them."""
        return max(ALERT_MIN_STALE, 2 * self._alert_interval())

    def _on_alert_interval_changed(self):
        settings.save({"alert_interval": self.alert_interval_var.get()})
        if self._monitor_started:
            self._monitor_tick()       # check now, then on the new timer

    def _monitor_tick(self):
        """Fetch the market, update profit alerts, refresh Alerts + Watchlist."""
        # Any check (timer, button or new rate) restarts the countdown.
        if self._monitor_job:
            self.root.after_cancel(self._monitor_job)
        self._monitor_job = self.root.after(self._alert_interval() * 1000,
                                            self._monitor_tick)
        if self._monitor_busy or not self.item_data:
            return
        self._monitor_busy = True
        self.alerts_check_btn.config(state="disabled")
        self.alerts_status.set("Checking the market…")

        def task():
            return (api.get_all_latest_prices(), *self._hour_and_5m_averages())

        def done(payload, err):
            self._monitor_busy = False
            self.alerts_check_btn.config(state="normal")
            next_check = f"next check in {self.alert_interval_var.get()}"
            if err or not payload or not payload[0] or not payload[1]:
                db.expire_stale_alerts(self._alert_stale_after())
                self._render_alerts()
                problem = err or "no data"
                shown = ""
                if self._market:
                    stamp = time.strftime("%H:%M:%S",
                                          time.localtime(self._market[2]))
                    shown = f" Alerts show prices from {stamp}."
                self.alerts_status.set(
                    f"Check failed: {problem}.{shown} · {next_check}")
                return
            self._market = (payload[0], payload[1], time.time(), payload[2])
            db.purge_old_alerts()
            new, expired = self._evaluate_alerts()
            self._render_alerts()
            self._render_watchlist()
            self._notify_targets()
            stamp = time.strftime("%H:%M:%S", time.localtime(self._market[2]))
            self.alerts_status.set(
                f"Prices updated {stamp} · {new} new · {expired} expired · "
                f"{next_check}   {self._volume_note([3600])}".rstrip())
            if new:
                flash_window(self.root)

        run_async(task, done)

    def _evaluate_alerts(self) -> tuple:
        """Match the alerts to the last market snapshot. Returns (new, expired)."""
        if not self._market or not self.item_data:
            return 0, 0
        latest, bulk, fetched_at, bulk_5m = self._market
        if not latest or not bulk:
            # Without volumes every item looks inactive: that isn't a reason
            # to expire every alert.
            return 0, 0
        if time.time() - fetched_at > self._alert_stale_after():
            # Too old to trust, as after an outage or the PC sleeping. A changed
            # filter mustn't bring alerts back on these prices.
            return 0, 0
        rows = analyzer.find_flips(
            latest, bulk, self.item_data,
            capital=self._flip_capital() or 0,
            membership=self._flip_membership(), now=fetched_at,
            bulk_5m=bulk_5m)
        threshold = self._alert_threshold()
        return db.sync_profit_alerts(
            [r for r in rows
             if r["active"] and not r["slow"] and r["verdict"] != "Suspect"
             and r["profit"] >= threshold],
            ALERT_COOLDOWN, fetched_at)

    def _tree_view_state(self, tree, id_map) -> tuple:
        """What to put back after a redraw, so a timed refresh doesn't lose
        the rows you selected or your scroll position."""
        return ({id_map[iid] for iid in tree.selection() if iid in id_map},
                tree.yview()[0])

    def _restore_tree_view(self, tree, id_map, state):
        selected, top = state
        tree.selection_set([iid for iid, item_id in id_map.items()
                            if item_id in selected])
        tree.yview_moveto(top)

    def _render_alerts(self):
        tree = self.alerts_tree
        view = self._tree_view_state(tree, self.alert_id_map)
        for r in tree.get_children():
            tree.delete(r)
        self.alert_id_map.clear()
        ages = self._trade_ages[tree] = {}
        # Only show alerts that still meet the current threshold; raising it
        # hides smaller ones (they come back if you lower it again).
        threshold = self._alert_threshold()
        rows = [a for a in db.get_active_alerts() if a["profit"] >= threshold]

        n = len(rows)
        self.notebook.tab(self.alerts_tab,
                          text=f"Alerts ({n})" if n else "Alerts")
        if not rows:
            tree.insert("", "end", values=(
                f"No flips at or above {fmt_gp(threshold)} gp "
                f"est. profit right now.",
                *[""] * (len(self.ALERT_COLUMNS) - 1)))
            return
        key, desc = self.alert_sort
        if key == "item_name":
            rows.sort(key=lambda a: a["item_name"].lower(), reverse=desc)
        else:
            rows.sort(key=lambda a: a[key] or 0, reverse=desc)
        fmt = self._price_fmt()
        for a in rows:
            iid = tree.insert("", "end", tags=("alert",), values=(
                a["item_name"], fmt(a["buy"]), fmt(a["sell"]), fmt(a["net"]),
                fmt(a["qty"]), fmt(a["profit"]), f"{a['roi']:.1f}%",
                self._volume_text(a["item_id"], a["volume"], fmt),
                fmt_last_trade(a["traded_at"]),
                time.strftime("%m/%d %H:%M", time.localtime(a["timestamp"])),
                a["n"],
            ))
            self.alert_id_map[iid] = a["item_id"]
            if a["traded_at"]:
                ages[iid] = a["traded_at"]
        self._restore_tree_view(tree, self.alert_id_map, view)

    def _sort_alerts(self, key: str, default_desc: bool):
        """Click a header to sort by it; click again to reverse."""
        cur_key, cur_desc = self.alert_sort
        desc = (not cur_desc) if cur_key == key else default_desc
        self.alert_sort = (key, desc)
        self._update_alert_arrows()
        self._render_alerts()

    def _update_alert_arrows(self):
        key, desc = self.alert_sort
        arrow = " ▼" if desc else " ▲"
        for cid, label, k, *_ in self.ALERT_COLUMNS:
            self.alerts_tree.heading(
                cid, text=label + (arrow if k == key else ""))

    def _dismiss_selected_alerts(self):
        for iid in self.alerts_tree.selection():
            item_id = self.alert_id_map.get(iid)
            if item_id:
                db.dismiss_alerts(item_id)
        self._render_alerts()

    def _dismiss_all_alerts(self):
        db.dismiss_alerts()
        self._render_alerts()

    # ── Watchlist tab ─────────────────────────────────────────────────────────

    WATCH_COLUMNS = [
        # (tree id, header, sort key, width, anchor, descending on 1st click)
        ("item",    "Item",        "name",    171, "w",      False),
        ("buy",     "Buy (gp)",    "buy",     88,  "e",      True),
        ("sell",    "Sell (gp)",   "sell",    88,  "e",      True),
        ("target_below", "Buy ≤",  "target_below", 100, "e", True),
        ("target_above", "Sell ≥", "target_above", 100, "e", True),
        ("net",     "Net / item",  "net",     88,  "e",      True),
        ("roi",     "ROI",         "roi",     55,  "e",      True),
        ("volume",  "Vol 1h",      "volume",  72,  "e",      True),
        ("vol_12h", "12h",         "vol_12h", 72,  "e",      True),
        ("vol_24h", "24h",         "vol_24h", 72,  "e",      True),
        ("age",     "Last trade",  "age",     88,  "e",      False),
        ("verdict", "Verdict",     "verdict", 85,  "center", True),
        ("added",   "Added",       "added",   78,  "w",      True),
    ]

    def _build_watchlist_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Button(bar, text="Refresh",
                   command=self._monitor_tick
                   ).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Remove selected",
                   command=self._remove_selected_watch
                   ).pack(side="left", padx=(0, 8))
        self.watch_target_btn = ttk.Button(bar, text="Set target…",
                                           state="disabled",
                                           command=self._target_dialog)
        self.watch_target_btn.pack(side="left")
        ttk.Label(bar, text="Add items with the ☆ button on a Search result.",
                  style="Dim.TLabel").pack(side="left", padx=12)
        self.watch_note = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.watch_note, style="Dim.TLabel"
                  ).pack(side="left")

        cols = tuple(c[0] for c in self.WATCH_COLUMNS)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        for cid, label, key, width, anchor, desc in self.WATCH_COLUMNS:
            tree.heading(cid, text=label,
                         command=lambda k=key, d=desc: self._sort_watchlist(k, d))
            tree.column(cid, width=width, anchor=anchor)
        tree.tag_configure("good", foreground=COLOR_GREEN)
        tree.tag_configure("warn", foreground=COLOR_YELLOW)
        tree.tag_configure("bad", foreground=COLOR_RED)
        tree.tag_configure("dim", foreground=COLOR_DIM)
        tree.tag_configure("target", foreground=COLOR_ACCENT_HI,
                           background=COLOR_TARGET_ROW)
        tree.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        tree.bind("<Double-Button-1>",
                  lambda _e: self._open_selected_in_search(
                      self.watch_tree, self.watch_id_map))
        tree.bind("<<TreeviewSelect>>", self._on_watch_selected)
        self.watch_tree = tree
        self.watch_id_map = {}
        # Kept across refreshes; oldest added first until a header is clicked.
        self.watch_sort = ("added", False)
        self._update_watch_arrows()

        ttk.Label(frame,
                  text=("Prices refresh with every market check (see Refresh "
                        "on the Alerts tab) or when you click Refresh here. "
                        "Margins use the "
                        "same maths as FlipRec. Select a row and click Set "
                        "target to be alerted at a price. Double-click a row "
                        "to open it in Search."),
                  style="Dim.TLabel", wraplength=1000, justify="center"
                  ).pack(pady=(0, 10))
        return frame

    def _render_watchlist(self):
        tree = self.watch_tree
        view = self._tree_view_state(tree, self.watch_id_map)
        for r in tree.get_children():
            tree.delete(r)
        self.watch_id_map.clear()
        ages = self._trade_ages[tree] = {}
        self.watch_note.set(self._volume_note(
            [seconds for _l, seconds in volume.WINDOWS]))
        items = db.watchlist_items()
        targets = db.watchlist_targets()
        hits = self._target_hits(targets)
        met = sum(any(h) for h in hits.values())
        self.notebook.tab(self.watchlist_tab,
                          text=f"Watchlist ({met})" if met else "Watchlist")
        if not items:
            tree.insert("", "end", values=(
                "Your watchlist is empty. Search an item and click "
                "☆ Add to Watchlist.", *[""] * (len(self.WATCH_COLUMNS) - 1)))
            return

        fmt = self._price_fmt()
        by_id = {it.get("id"): it for it in self.item_data}
        latest, bulk, fetched_at, bulk_5m = self._market or ({}, {}, None, {})
        capital = self._flip_capital() or 0
        entries = []
        for item_id, name, added in items:
            row = None
            lt = latest.get(str(item_id))
            if lt and item_id in by_id:
                row = analyzer.flip_row(by_id[item_id], lt,
                                        bulk.get(str(item_id)), capital,
                                        now=fetched_at,
                                        avg_5m=bulk_5m.get(str(item_id)))
                if row:        # None if a side has no price yet
                    row["vol_12h"] = self._volume.slower_side(item_id, 12 * 3600)
                    row["vol_24h"] = self._volume.slower_side(item_id, 24 * 3600)
                    row["target_below"] = targets.get(item_id, {}).get("target_below")
                    row["target_above"] = targets.get(item_id, {}).get("target_above")
            entries.append((item_id, name, added, row))

        for item_id, name, added, row in self._sorted_watch_entries(entries):
            added_s = time.strftime("%m/%d %H:%M", time.localtime(added))
            target = targets.get(item_id, {})
            hit_below, hit_above = hits.get(item_id, (False, False))
            below = fmt_target(target.get("target_below"), hit_below, fmt)
            above = fmt_target(target.get("target_above"), hit_above, fmt)
            if row is None:
                values = (name, "…" if not self._market else "N/A", "",
                          below, above,
                          *[""] * (len(self.WATCH_COLUMNS) - 6), added_s)
                tag = "dim"
            else:
                values = (
                    name, fmt(row["buy"]), fmt(row["sell"]), below, above,
                    fmt(row["net"]), f"{row['roi']:.1f}%",
                    self._volume_text(item_id, row["volume"], fmt),
                    self._window_text(row["vol_12h"], 12 * 3600, fmt),
                    self._window_text(row["vol_24h"], 24 * 3600, fmt),
                    fmt_last_trade(row["traded_at"]),
                    "⚠ Inactive" if not row["active"] else row["verdict"],
                    added_s,
                )
                tag = {"Profitable": "good", "Marginal": "warn",
                       "Suspect": "warn", "Unprofitable": "bad"}.get(
                           row["verdict"], "dim")
            if hit_below or hit_above:
                tag = "target"
            iid = tree.insert("", "end", values=values, tags=(tag,))
            self.watch_id_map[iid] = item_id
            if row:
                ages[iid] = row["traded_at"]
        self._restore_tree_view(tree, self.watch_id_map, view)

    def _sorted_watch_entries(self, entries: list) -> list:
        """Sort (item_id, name, added, row) by the chosen column. Items with
        no price data yet stay at the bottom whichever way you sort."""
        key, desc = self.watch_sort
        if key == "name":
            return sorted(entries, key=lambda e: e[1].lower(), reverse=desc)
        if key == "added":
            return sorted(entries, key=lambda e: e[2], reverse=desc)
        priced = [e for e in entries if e[3]]
        missing = [e for e in entries if not e[3]]
        if key == "verdict":
            # Rank, as on FlipRec; best Net / item first within each.
            priced.sort(key=lambda e: -(e[3]["net"] or 0))
            priced.sort(key=lambda e: analyzer.VERDICT_RANK[e[3]["verdict"]],
                        reverse=desc)
        else:
            priced.sort(key=lambda e: e[3].get(key) or 0, reverse=desc)
        return priced + missing

    def _sort_watchlist(self, key: str, default_desc: bool):
        """Click a header to sort by it; click again to reverse."""
        cur_key, cur_desc = self.watch_sort
        desc = (not cur_desc) if cur_key == key else default_desc
        self.watch_sort = (key, desc)
        self._update_watch_arrows()
        self._render_watchlist()

    def _update_watch_arrows(self):
        key, desc = self.watch_sort
        arrow = " ▼" if desc else " ▲"
        for cid, label, k, *_ in self.WATCH_COLUMNS:
            self.watch_tree.heading(
                cid, text=label + (arrow if k == key else ""))

    def _target_hits(self, targets: dict) -> dict:
        """{item_id: (below met, above met)} from the last market check."""
        if not targets or not self._market:
            return {}
        latest, _bulk, fetched_at, _bulk_5m = self._market
        if time.time() - fetched_at > self._alert_stale_after():
            return {}                  # too old to trust, as with alerts
        return {item_id: analyzer.target_hits(
                    latest.get(str(item_id)), t["target_below"],
                    t["target_above"], fetched_at)
                for item_id, t in targets.items()}

    def _notify_targets(self, quiet: bool = False):
        """Flash when a target is newly met. One that stays met, or comes back
        within the hour after a price bounces around it, doesn't flash again."""
        targets = db.watchlist_targets()
        now = time.time()
        seen, fresh = [], False
        for item_id, hit in self._target_hits(targets).items():
            for side, met in zip(("below", "above"), hit):
                if met:
                    seen.append((item_id, side))
                    last = targets[item_id][f"{side}_seen"] or 0
                    fresh = fresh or now - last >= ALERT_COOLDOWN
        db.watchlist_mark_seen(seen, now)
        if fresh and not quiet:
            flash_window(self.root)

    def _on_watch_selected(self, _event=None):
        chosen = any(iid in self.watch_id_map
                     for iid in self.watch_tree.selection())
        self.watch_target_btn.config(state="normal" if chosen else "disabled")

    def _target_dialog(self):
        """Dialog to set the selected Watchlist item's target prices."""
        item_id = next((self.watch_id_map[iid]
                        for iid in self.watch_tree.selection()
                        if iid in self.watch_id_map), None)
        if item_id is None:
            return
        name = next(n for i, n, _a in db.watchlist_items() if i == item_id)
        target = db.watchlist_targets().get(item_id, {})

        dlg = tk.Toplevel(self.root)
        dlg.title("Target prices")
        dlg.configure(bg=COLOR_BG)
        dlg.resizable(False, False)
        dlg.transient(self.root)

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=24, pady=20)
        ttk.Label(body, text=name, style="Header.TLabel"
                  ).pack(anchor="w", pady=(0, 10))
        ttk.Label(body, wraplength=400, justify="left", text=(
            "You get the tab count and taskbar flash when the latest trade "
            "reaches a target. Leave a box blank for no target.")
                  ).pack(anchor="w", pady=(0, 12))

        latest = (self._market or ({},))[0].get(str(item_id)) or {}
        grid = ttk.Frame(body)
        grid.pack(anchor="w")
        fields = []
        for i, (label, key, price) in enumerate((
                ("Alert when Buy is at or below:", "target_below", latest.get("low")),
                ("Alert when Sell is at or above:", "target_above", latest.get("high")))):
            ttk.Label(grid, text=label, style="TLabel"
                      ).grid(row=i, column=0, sticky="w", padx=(0, 10), pady=4)
            var = tk.StringVar(value=fmt_gp_exact(target[key])
                               if target.get(key) else "")
            ttk.Entry(grid, textvariable=var, width=16
                      ).grid(row=i, column=1, pady=4)
            ttk.Label(grid, style="Dim.TLabel",
                      text=f"now {fmt_gp_exact(price)}" if price else ""
                      ).grid(row=i, column=2, sticky="w", padx=(10, 0))
            fields.append(var)
        grid.winfo_children()[1].focus_set()

        error = tk.StringVar()
        tk.Label(body, textvariable=error, bg=COLOR_BG, fg=COLOR_RED,
                 font=FONT_BODY, wraplength=400, justify="left"
                 ).pack(anchor="w", pady=(6, 0))

        def close(_e=None):
            dlg.destroy()

        def apply(below, above):
            db.watchlist_set_targets(item_id, below, above)
            self._notify_targets(quiet=True)  # already met: nothing to flash for
            self._render_watchlist()
            close()

        def save(_e=None):
            try:
                below, above = parse_targets(fields[0].get(), fields[1].get())
            except ValueError as exc:
                error.set(str(exc))
                return
            apply(below, above)

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=close).pack(side="right")
        ttk.Button(buttons, text="Save", command=save
                   ).pack(side="right", padx=(0, 8))
        if target:
            ttk.Button(buttons, text="Clear targets",
                       command=lambda: apply(None, None)).pack(side="left")
        dlg.bind("<Return>", save)
        dlg.bind("<Escape>", close)

        dlg.update_idletasks()
        x = (dlg.winfo_screenwidth() - dlg.winfo_reqwidth()) // 2
        y = (dlg.winfo_screenheight() - dlg.winfo_reqheight()) // 3
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()
        dlg.lift()

    def _remove_selected_watch(self):
        for iid in self.watch_tree.selection():
            item_id = self.watch_id_map.get(iid)
            if item_id:
                db.watchlist_remove(item_id)
        self._render_watchlist()
        self._update_watch_btn()

    def _toggle_watch(self):
        item = self._search_item
        if not item:
            return
        if db.watchlist_contains(item["id"]):
            db.watchlist_remove(item["id"])
        else:
            db.watchlist_add(item["id"], item["name"])
        self._update_watch_btn()
        self._render_watchlist()

    def _update_watch_btn(self):
        btn = getattr(self, "watch_btn", None)
        if not self._search_item or not btn or not btn.winfo_exists():
            return
        on = db.watchlist_contains(self._search_item["id"])
        btn.config(text="★ On Watchlist (remove)" if on
                   else "☆ Add to Watchlist")

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _price_fmt(self):
        """Price formatter chosen in the Prices dropdown."""
        return (fmt_gp_exact if self.flip_format_var.get().startswith("Exact")
                else fmt_gp)

    def _search_price_fmt(self):
        """Price formatter chosen in the Prices dropdown on Search."""
        return (fmt_gp_exact if self.search_format_var.get().startswith("Exact")
                else fmt_gp)

    def _price_format_picker(self, bar, var, on_change, trailing=16):
        """'Prices:' dropdown. FlipRec and Alerts share one setting, so
        changing either updates both; Search has its own."""
        ttk.Label(bar, text="Prices:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        combo = ttk.Combobox(bar, textvariable=var,
                             state="readonly", width=14, values=PRICE_FORMATS)
        combo.pack(side="left", padx=(0, trailing))
        combo.bind("<<ComboboxSelected>>", lambda _e: on_change())

    def _on_price_format_changed(self):
        settings.save({"flip_price_format": self.flip_format_var.get()})
        # Only the display changes, so redraw without re-sorting.
        if self.flip_results:
            self._render_fliprec_table()
        self._render_alerts()
        self._render_watchlist()

    def _on_search_format_changed(self):
        settings.save({"search_price_format": self.search_format_var.get()})
        # Only the display changes, so redraw without fetching anything.
        if self._search_shown:
            self._render_result(**self._search_shown)
        self._draw_history_chart()

    def _open_selected_in_search(self, tree, id_map):
        sel = tree.selection()
        item_id = id_map.get(sel[0]) if sel else None
        item = next((it for it in self.item_data
                     if it.get("id") == item_id), None) if item_id else None
        if not item:
            return
        self.notebook.select(self.search_tab)
        self.search_var.set(item["name"])
        self._hide_dropdown()
        self._lookup_item(item)

    # ── Volatile tab ──────────────────────────────────────────────────────────

    def _build_volatile_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 10))

        ttk.Label(bar, text="Window:", style="TLabel"
                  ).pack(side="left", padx=(0, 8))
        self.volatile_window_var = tk.StringVar(value="1h")
        window_combo = ttk.Combobox(
            bar, textvariable=self.volatile_window_var,
            values=["5m", "1h", "6h", "24h"],
            state="readonly", width=6,
        )
        window_combo.pack(side="left", padx=(0, 16))
        window_combo.bind("<<ComboboxSelected>>",
                           lambda _e: self._refresh_volatile())

        self.volatile_refresh_btn = ttk.Button(
            bar, text="Refresh", command=self._refresh_volatile,
        )
        self.volatile_refresh_btn.pack(side="left")

        self.volatile_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.volatile_status,
                  style="Dim.TLabel").pack(side="left", padx=12)

        # Column metadata: (tree column id, base header text, data key, default desc)
        # Used by _sort_volatile and _update_sort_arrows.
        self._volatile_columns = [
            ("item",    "Item",            "name",       False),
            ("current", "Current (gp)",    "current",    True),
            ("avg",     "Window avg (gp)", "avg",        True),
            ("pct",     "Change",          "pct_change", True),
            ("volume",  "Volume",          "volume",     True),
        ]
        self.volatile_sort = (None, None)  # (data_key, descending)
        self.volatile_results = []

        cols = tuple(c[0] for c in self._volatile_columns)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        for cid, label, key, default_desc in self._volatile_columns:
            tree.heading(
                cid, text=label,
                command=lambda k=key, d=default_desc: self._sort_volatile(k, d),
            )
        tree.column("item",    width=280, anchor="w")
        tree.column("current", width=130, anchor="e")
        tree.column("avg",     width=140, anchor="e")
        tree.column("pct",     width=100, anchor="e")
        tree.column("volume",  width=120, anchor="e")

        tree.tag_configure("up", foreground=COLOR_GREEN)
        tree.tag_configure("down", foreground=COLOR_RED)

        tree.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        tree.bind("<Double-Button-1>", self._on_volatile_click)
        self.volatile_tree = tree
        self.volatile_id_map = {}  # tree row id -> item id

        ttk.Label(frame,
                  text=("Double-click a row to open its full price panel "
                        "in the Search tab. Items with under 1,000 traded "
                        "in the window are filtered out."),
                  style="Dim.TLabel"
                  ).pack(pady=(0, 10))
        return frame

    def _refresh_volatile(self):
        if not self.item_data:
            self.volatile_status.set("Item mapping not loaded yet.")
            return

        self.volatile_refresh_btn.config(state="disabled")
        self.volatile_status.set("Fetching…")
        timestep = self.volatile_window_var.get()

        def task():
            latest = api.get_all_latest_prices()
            bulk = self._volume.averages(timestep)
            return analyzer.find_volatile_items(
                latest, bulk, self.item_data,
                min_volume=1000, top_n=100,
            )

        def done(results, err):
            self.volatile_refresh_btn.config(state="normal")
            if err:
                self.volatile_status.set(f"Error: {err}")
                return
            # Reset to "most volatile by abs movement" on every fresh fetch.
            self.volatile_results = results
            self.volatile_sort = (None, None)
            self._update_sort_arrows()
            self._render_volatile_table()
            self.volatile_status.set(f"{len(results)} movers in {timestep} window")

        run_async(task, done)

    def _sort_volatile(self, key: str, default_desc: bool):
        """Click handler for a column header — sort results by `key`."""
        current_key, current_desc = self.volatile_sort
        if current_key == key:
            new_desc = not current_desc
        else:
            new_desc = default_desc
        self.volatile_sort = (key, new_desc)

        # Case-insensitive sort for strings, numeric for everything else.
        if key == "name":
            self.volatile_results.sort(
                key=lambda r: r["name"].lower(),
                reverse=new_desc,
            )
        else:
            self.volatile_results.sort(
                key=lambda r: r.get(key, 0),
                reverse=new_desc,
            )

        self._update_sort_arrows()
        self._render_volatile_table()

    def _update_sort_arrows(self):
        """Refresh the heading text on each column to show the active sort arrow."""
        active_key, desc = self.volatile_sort
        arrow = " ▼" if desc else " ▲"
        for cid, label, key, _ in self._volatile_columns:
            text = label + (arrow if key == active_key else "")
            self.volatile_tree.heading(cid, text=text)

    def _render_volatile_table(self):
        results = self.volatile_results
        for r in self.volatile_tree.get_children():
            self.volatile_tree.delete(r)
        self.volatile_id_map.clear()

        if not results:
            self.volatile_tree.insert(
                "", "end",
                values=("No volatile items found in this window.",
                        "", "", "", ""),
            )
            return

        for r in results:
            tag = "up" if r["pct_change"] > 0 else "down"
            iid = self.volatile_tree.insert(
                "", "end",
                values=(
                    r["name"],
                    fmt_gp(r["current"]),
                    fmt_gp(r["avg"]),
                    f"{r['pct_change']:+.1f}%",
                    fmt_gp(r["volume"]),
                ),
                tags=(tag,),
            )
            self.volatile_id_map[iid] = r["item_id"]

    def _on_volatile_click(self, _event):
        sel = self.volatile_tree.selection()
        if not sel:
            return
        item_id = self.volatile_id_map.get(sel[0])
        if not item_id:
            return
        item = next((it for it in self.item_data
                     if it.get("id") == item_id), None)
        if not item:
            return
        # Jump to Search tab with this item loaded
        self.notebook.select(self.search_tab)
        self.search_var.set(item["name"])
        self._hide_dropdown()
        self._lookup_item(item)

    # ── FlipRec tab ───────────────────────────────────────────────────────────

    FLIP_COLUMNS = [
        # (tree id, header, data key, width, anchor, default desc)
        ("item",    "Item",        "name",      184, "w",      False),
        ("buy",     "Buy (gp)",    "buy",       74,  "e",      False),
        ("sell",    "Sell (gp)",   "sell",      74,  "e",      True),
        ("tax",     "GE tax",      "tax",       60,  "e",      True),
        ("net",     "Net / item",  "net",       85,  "e",      True),
        ("roi",     "ROI",         "roi",       50,  "e",      True),
        ("limit",   "4h limit",    "limit",     72,  "e",      True),
        ("qty",     "Qty",         "qty",       55,  "e",      True),
        ("profit",  "Est. profit", "profit",    88,  "e",      True),
        ("volume",  "Vol 1h",      "volume",    75,  "e",      True),
        ("age",     "Last trade",  "age",       88,  "e",      False),
        ("verdict", "Verdict",     "verdict",   80,  "center", True),
        ("signal",  "Signal",      "reversion", 165, "w",      False),
    ]

    def _build_fliprec_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        prefs = settings.load()

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 6))

        ttk.Label(bar, text="Capital:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        self.flip_capital_var = tk.StringVar(value=prefs.get("capital", ""))
        cap_entry = ttk.Entry(bar, textvariable=self.flip_capital_var, width=12)
        cap_entry.pack(side="left", padx=(0, 16))
        cap_entry.bind("<Return>", lambda _e: self._apply_flip_filters())
        cap_entry.bind("<FocusOut>", lambda _e: self._apply_flip_filters())

        ttk.Label(bar, text="Items:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        self.flip_members_var = tk.StringVar(
            value=prefs.get("flip_membership", "All items"))
        mem_combo = ttk.Combobox(
            bar, textvariable=self.flip_members_var, state="readonly",
            width=14, values=["All items", "Members only", "F2P only"])
        mem_combo.pack(side="left", padx=(0, 16))
        mem_combo.bind("<<ComboboxSelected>>",
                       lambda _e: self._apply_flip_filters())

        self.flip_hide_inactive_var = tk.BooleanVar(
            value=prefs.get("flip_hide_inactive", True))
        ttk.Checkbutton(bar, text="Hide inactive",
                        variable=self.flip_hide_inactive_var,
                        command=self._apply_flip_filters
                        ).pack(side="left", padx=(0, 16))

        self._price_format_picker(bar, self.flip_format_var,
                                  self._on_price_format_changed)

        ttk.Label(bar, text="Show:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        self.flip_verdict_var = tk.StringVar(
            value=prefs.get("flip_verdict", "All verdicts"))
        verdict_combo = ttk.Combobox(
            bar, textvariable=self.flip_verdict_var, state="readonly",
            width=12, values=["All verdicts", "Profitable", "Marginal",
                              "Suspect", "Inactive"])
        verdict_combo.pack(side="left", padx=(0, 16))
        verdict_combo.bind("<<ComboboxSelected>>",
                           lambda _e: self._apply_flip_filters())

        self.flip_refresh_btn = ttk.Button(
            bar, text="Refresh", command=self._refresh_fliprec)
        self.flip_refresh_btn.pack(side="left")

        self.flip_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.flip_status, style="Dim.TLabel"
                  ).pack(side="left", padx=12)

        # Banner for items that are not actively trading
        self.flip_banner = tk.StringVar(value="")
        tk.Label(frame, textvariable=self.flip_banner, bg=COLOR_BG,
                 fg=COLOR_YELLOW, font=FONT_BODY, anchor="w", justify="left"
                 ).pack(fill="x", padx=22, pady=(0, 6))

        cols = tuple(c[0] for c in self.FLIP_COLUMNS)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        for cid, label, key, width, anchor, desc in self.FLIP_COLUMNS:
            tree.heading(cid, text=label,
                         command=lambda k=key, d=desc: self._sort_fliprec(k, d))
            tree.column(cid, width=width, anchor=anchor)
        tree.tag_configure("good", foreground=COLOR_GREEN)
        tree.tag_configure("warn", foreground=COLOR_YELLOW)
        tree.tag_configure("dim", foreground=COLOR_DIM)
        tree.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        tree.bind("<Double-Button-1>", self._on_fliprec_click)
        self.flip_tree = tree
        self.flip_id_map = {}
        self.flip_results = []        # computed for the current filters
        self.flip_sort = (None, None)
        self._flip_raw = None         # (latest, bulk_1h, fetched_at, bulk_5m)

        ttk.Label(frame,
                  text=("Buy at the instant-sell price, sell at the "
                        "instant-buy price (latest trades, like an in-game "
                        "margin check), minus the 2% GE tax. Last trade = "
                        "age of the older side (⏱ = over 5 min). Qty is "
                        "capped by your capital, the "
                        "4h limit, and ~4h of market volume. Estimates "
                        "only: offers can take time to fill and margins "
                        "close quickly. Inactive = a side has not traded in "
                        "the last hour. Double-click a row to open it in "
                        "Search."),
                  style="Dim.TLabel", wraplength=1000, justify="center"
                  ).pack(side="bottom", before=tree, pady=(0, 10))
        return frame

    def _flip_membership(self) -> str:
        return {"Members only": "members", "F2P only": "f2p"}.get(
            self.flip_members_var.get(), "all")

    def _flip_capital(self):
        """Parsed capital in gp, or None if the field is not a valid amount."""
        try:
            return analyzer.parse_gp_amount(self.flip_capital_var.get())
        except ValueError:
            return None

    def _refresh_fliprec(self):
        if not self.item_data:
            self.flip_status.set("Item mapping not loaded yet.")
            return
        self.flip_refresh_btn.config(state="disabled")
        self.flip_status.set("Fetching…")

        def task():
            return (api.get_all_latest_prices(), *self._hour_and_5m_averages())

        def done(payload, err):
            self.flip_refresh_btn.config(state="normal")
            if err or not payload or not payload[0] or not payload[1]:
                # Without volumes every item would look Inactive.
                self.flip_status.set(
                    f"Error: {err}" if err else "Could not fetch prices.")
                return
            self._flip_raw = (payload[0], payload[1], time.time(), payload[2])
            self._market = self._flip_raw
            self._apply_flip_filters()

        run_async(task, done)

    def _apply_flip_filters(self):
        """Recompute results from the last fetch using the current inputs."""
        capital = self._flip_capital()
        if capital is None:
            self.flip_status.set("Capital must look like 5m, 250k or 1,000,000.")
            return
        settings.save({
            "capital": self.flip_capital_var.get().strip(),
            "flip_membership": self.flip_members_var.get(),
            "flip_hide_inactive": bool(self.flip_hide_inactive_var.get()),
            "flip_price_format": self.flip_format_var.get(),
            "flip_verdict": self.flip_verdict_var.get(),
        })
        if not self._flip_raw:
            return
        latest, bulk, fetched_at, bulk_5m = self._flip_raw
        self.flip_results = analyzer.find_flips(
            latest, bulk, self.item_data, capital=capital,
            membership=self._flip_membership(), bulk_5m=bulk_5m,
        )
        self.flip_sort = (None, None)
        self._update_flip_arrows()
        self._render_fliprec_table()
        stamp = time.strftime("%H:%M:%S", time.localtime(fetched_at))
        self.flip_status.set(f"Prices fetched at {stamp}")
        # Format/capital/filter also apply to the Alerts and Watchlist tabs.
        if self._evaluate_alerts()[0]:
            flash_window(self.root)
        self._render_alerts()
        self._render_watchlist()
        self._notify_targets()

    def _sort_fliprec(self, key: str, default_desc: bool):
        cur_key, cur_desc = self.flip_sort
        desc = (not cur_desc) if cur_key == key else default_desc
        self.flip_sort = (key, desc)
        if key == "verdict":
            # Rank, not alphabetical: ▼ = Profitable, Marginal, Suspect,
            # Inactive; ▲ reverses it. Ties keep the best profit first.
            self.flip_results.sort(key=lambda r: -(r["profit"] or r["net"]))
            self.flip_results.sort(
                key=lambda r: analyzer.VERDICT_RANK[r["verdict"]],
                reverse=desc)
        elif key in ("name", "reversion"):
            self.flip_results.sort(key=lambda r: str(r[key]).lower(),
                                   reverse=desc)
        else:
            self.flip_results.sort(key=lambda r: r.get(key) or 0, reverse=desc)
        self._update_flip_arrows()
        self._render_fliprec_table()

    def _update_flip_arrows(self):
        key, desc = self.flip_sort
        arrow = " ▼" if desc else " ▲"
        for cid, label, k, *_ in self.FLIP_COLUMNS:
            self.flip_tree.heading(cid, text=label + (arrow if k == key else ""))

    def _render_fliprec_table(self):
        tree = self.flip_tree
        view = self._tree_view_state(tree, self.flip_id_map)
        for r in tree.get_children():
            tree.delete(r)
        self.flip_id_map.clear()
        ages = self._trade_ages[tree] = {}

        show = self.flip_verdict_var.get()
        # Choosing "Inactive" in the Show filter overrides Hide inactive.
        hide = self.flip_hide_inactive_var.get() and show != "Inactive"
        inactive = [r for r in self.flip_results if not r["active"]]
        rows = [r for r in self.flip_results
                if (r["active"] or not hide)
                and (show == "All verdicts" or r["verdict"] == show)
                ][:FLIP_MAX_ROWS]

        banner = []
        if inactive and hide:
            banner.append(
                f"⚠ {len(inactive)} item(s) hidden: no trade on one side in "
                f"the last hour, so their margin is likely not real. Untick "
                f"'Hide inactive' to see them.")
        elif inactive:
            banner.append(
                f"⚠ {len(inactive)} item(s) are inactive (no trade on one "
                f"side in the last hour); their margins are likely not real.")
        slow = sum(1 for r in rows if r["slow"])
        if slow:
            banner.append(
                f"⏱ {slow} listed item(s) have a side not traded in the last "
                f"5 min; the margin may have moved.")
        if self._volume_note([3600]):
            banner.append(self._volume_note([3600]))
        self.flip_banner.set("\n".join(banner))

        if not rows:
            tree.insert("", "end", values=(
                "No profitable flips found with these filters.",
                *[""] * (len(self.FLIP_COLUMNS) - 1)))
            return

        fmt = self._price_fmt()
        for r in rows:
            tag = {"Profitable": "good", "Marginal": "warn", "Suspect": "warn"}.get(
                r["verdict"], "dim")
            verdict = "⚠ Inactive" if not r["active"] else r["verdict"]
            sig = r["reversion"]
            if sig:
                sig = f"{sig} ({r['reversion_pct']:+.1f}%)"
            if not r["backed_5m"]:
                sig = f"5m margin only {fmt(r['net_5m'])}"
            iid = tree.insert("", "end", tags=(tag,), values=(
                r["name"], fmt(r["buy"]), fmt(r["sell"]),
                fmt(r["tax"]), fmt(r["net"]), f"{r['roi']:.1f}%",
                fmt(r["limit"]) if r["limit"] else "?",
                fmt(r["qty"]) if r["qty"] else "-",
                fmt(r["profit"]) if r["qty"] else "-",
                self._volume_text(r["item_id"], r["volume"], fmt),
                fmt_last_trade(r["traded_at"]),
                verdict, sig,
            ))
            self.flip_id_map[iid] = r["item_id"]
            ages[iid] = r["traded_at"]
        self._restore_tree_view(tree, self.flip_id_map, view)

    def _on_fliprec_click(self, _event):
        sel = self.flip_tree.selection()
        item_id = self.flip_id_map.get(sel[0]) if sel else None
        item = next((it for it in self.item_data
                     if it.get("id") == item_id), None) if item_id else None
        if not item:
            return
        self.notebook.select(self.search_tab)
        self.search_var.set(item["name"])
        self._hide_dropdown()
        self._lookup_item(item)

    # ── News tab ──────────────────────────────────────────────────────────────

    def _build_news_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 10))
        self.news_refresh_btn = ttk.Button(
            bar, text="Refresh from sources",
            command=self._refresh_news_from_sources,
        )
        self.news_refresh_btn.pack(side="left")
        ttk.Button(bar, text="Reload cache",
                   command=self._reload_news_cache).pack(side="left", padx=8)
        self.news_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.news_status,
                  style="Dim.TLabel").pack(side="left", padx=12)

        cols = ("title", "source", "date")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        tree.heading("title",  text="Title")
        tree.heading("source", text="Source")
        tree.heading("date",   text="Date")
        tree.column("title",  width=620, anchor="w")
        tree.column("source", width=200, anchor="w")
        tree.column("date",   width=140, anchor="w")

        tree.bind("<Double-Button-1>", self._on_news_open)
        tree.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.news_tree = tree
        self.news_urls = {}

        ttk.Label(frame,
                  text="Double-click a row to open the article in your browser.",
                  style="Dim.TLabel").pack(pady=(0, 10))

        return frame

    def _reload_news_cache(self):
        for r in self.news_tree.get_children():
            self.news_tree.delete(r)
        self.news_urls.clear()

        rows = db.get_recent_news(50)
        if not rows:
            self.news_tree.insert(
                "", "end",
                values=("No news yet. Loading from sources…", "", "")
            )
            return
        for title, url, source, ts in rows:
            iid = self.news_tree.insert(
                "", "end",
                values=(
                    title, source,
                    time.strftime("%b %d %H:%M", time.localtime(ts)),
                ),
            )
            self.news_urls[iid] = url

    def _refresh_news_from_sources(self):
        self.news_refresh_btn.config(state="disabled")
        self.news_status.set("Scraping sources…")

        def task():
            articles = scraper.scrape_all()
            for a in articles:
                db.cache_news(a["title"], a["url"], a["content"], a["source"])
            return len(articles)

        def done(count, err):
            self.news_refresh_btn.config(state="normal")
            if err:
                self.news_status.set(f"Error: {err}")
                return
            self.news_status.set(f"Cached {count} articles.")
            self._reload_news_cache()

        run_async(task, done)

    def _on_news_open(self, _event):
        sel = self.news_tree.selection()
        if not sel:
            return
        url = self.news_urls.get(sel[0])
        if url:
            webbrowser.open(url)

    # ── Price chart (Search tab) ──────────────────────────────────────────────

    def _chart_size(self):
        """Canvas (width, height), or None if it isn't on screen yet.

        Tk reports 1x1 for a widget that hasn't been displayed; the
        <Configure> binding redraws once it has a real size.
        """
        c = self.history_canvas
        w, h = c.winfo_width(), c.winfo_height()
        return None if w <= 1 or h <= 1 else (w, h)

    def _history_placeholder(self, text: str):
        # Nothing to zoom while a message is shown.
        self._chart_rows = None
        self._chart_geom = None
        size = self._chart_size()
        if not size:
            return
        c = self.history_canvas
        c.delete("all")
        c.create_text(size[0] / 2, size[1] / 2, text=text,
                      fill=COLOR_DIM, font=FONT_BODY)

    def _update_range_buttons(self):
        for label, btn in self.history_range_btns.items():
            btn.configure(style="RangeSel.TButton"
                          if label == self.history_range else "Range.TButton")

    def _set_history_range(self, label: str):
        self.history_range = label
        self._chart_view = None
        self._update_range_buttons()
        self._draw_history_chart()

    def _refresh_history_chart(self):
        """Re-pull API data for the selected item, then redraw."""
        item_id = self.history_item[0] if self.history_item else None
        self._history_fetched = {k for k in self._history_fetched
                                 if k[0] != item_id}
        self._draw_history_chart()

    def _ensure_history_data(self, item_id: int, item_name: str, step: str):
        """Fetch + store API history for this item/step once per session."""
        key = (item_id, step)
        if key in self._history_fetched:
            return
        self._history_fetched.add(key)

        def fetch():
            points = api.get_timeseries(item_id, timestep=step)
            return db.backfill_price_history(item_id, item_name, points, step)

        def done(added, err):
            if err or not added:
                return
            self._redraw_chart_if_current(item_id)

        run_async(fetch, done)

    def _redraw_chart_if_current(self, item_id: int):
        """Redraw only if the chart is still showing this item."""
        if self.history_item and self.history_item[0] == item_id:
            self._draw_history_chart()

    def _draw_history_chart(self):
        if not self.history_item:
            self._history_placeholder(
                "Search for an item to see its price chart.")
            return
        item_id, name = self.history_item

        _label, window, step = next(
            r for r in HISTORY_RANGES if r[0] == self.history_range)
        self._ensure_history_data(item_id, name, step)

        since = int(time.time()) - window if window else 0
        # Live snapshots only make sense on short ranges; on long ranges
        # they would just clutter the right edge.
        history = db.get_price_range(
            item_id, since, step,
            include_live=bool(window and window <= 7 * 86400))

        if not history:
            self._history_placeholder(
                "No price data for this range (it may still be loading).")
            return
        self._chart_rows = history
        self._render_chart()

    def _render_chart(self):
        """Draw the stored rows, zoomed to self._chart_view if set.

        Zooming and panning call this directly, so they redraw without
        another database query.
        """
        history = self._chart_rows
        if not history or not self.history_item:
            return
        name = self.history_item[1]
        range_label, _window, step = next(
            r for r in HISTORY_RANGES if r[0] == self.history_range)

        # Check the size before clearing, so a squeezed canvas keeps its
        # last drawing instead of going blank.
        size = self._chart_size()
        if not size or size[0] < 50 or size[1] < 50:
            return
        W, H = size

        times = [ts for _h, _l, ts in history]
        full_min, full_max = times[0], times[-1]
        t_min, t_max = self._chart_view or (full_min, full_max)

        # Points inside the view, plus one either side so the lines run
        # to the edges of the plot (the overhang is covered up below).
        first = bisect.bisect_left(times, t_min)
        last = bisect.bisect_right(times, t_max)
        visible = history[first:last]
        drawn = history[max(0, first - 1):last + 1]

        def mid(h, l):
            return (h + l) / 2 if (h and l) else (h or l or 0)

        # Fit the price axis to what's in view, so zooming in on a flat
        # stretch shows its detail.
        all_vals = [v for h, l, _ts in (visible or drawn)
                    for v in (h, l, mid(h, l)) if v]
        if not all_vals:
            self._history_placeholder("No usable price values.")
            return
        y_min, y_max = min(all_vals), max(all_vals)
        if y_max == y_min:
            pad = max(1, y_max * 0.05)
            y_min, y_max = y_min - pad, y_max + pad
        grid_vals = [y_min + (y_max - y_min) * (i / 4) for i in range(5)]

        # Y labels in the chosen format, or exact gp when zoomed in so far that
        # the short labels would repeat (e.g. "1.1K" twice).
        fmt = self._search_price_fmt()
        y_labels = [fmt(round(v)) for v in grid_vals]
        if len(set(y_labels)) < len(y_labels):
            y_labels = [fmt_gp_exact(round(v)) for v in grid_vals]

        # Full prices can be wider than the usual margin.
        widest = max(self._chart_font.measure(t) for t in y_labels)
        ML, MR, MT, MB = max(70, widest + 16), 30, 40, 60
        plot_w = W - ML - MR
        plot_h = H - MT - MB
        if plot_w < 10 or plot_h < 10:
            return

        c = self.history_canvas
        c.delete("all")
        self._chart_geom = {
            "left": ML, "width": plot_w, "view": (t_min, t_max),
            "full": (full_min, full_max),
            "min_span": CHART_MIN_POINTS * STEP_SECONDS.get(step, 3600),
        }

        def x_pos(t):
            # Position by timestamp so mixed-resolution data stays to scale.
            if t_max == t_min:
                return ML + plot_w / 2
            return ML + ((t - t_min) / (t_max - t_min)) * plot_w

        def y_pos(v):
            return MT + plot_h - ((v - y_min) / (y_max - y_min)) * plot_h

        # Y gridlines
        for v in grid_vals:
            y = y_pos(v)
            c.create_line(ML, y, ML + plot_w, y,
                          fill="#3a2818", dash=(2, 4))

        # Plot lines
        def plot_series(values, color, marker=False):
            pts = []
            for (_h, _l, ts), v in zip(drawn, values):
                if not v:
                    continue
                x, y = x_pos(ts), y_pos(v)
                pts.extend([x, y])
                if marker:
                    r = 2
                    c.create_oval(x - r, y - r, x + r, y + r,
                                  fill=color, outline=color)
            if len(pts) >= 4:
                c.create_line(pts, fill=color, width=2, smooth=False)

        plot_series([h for h, _l, _ts in drawn], COLOR_GREEN, marker=True)
        plot_series([l for _h, l, _ts in drawn], COLOR_RED, marker=True)
        plot_series([mid(h, l) for h, l, _ts in drawn], COLOR_BLUE)

        # Cover anything drawn outside the plot area (the lines out to the
        # off-screen neighbours, or above/below the fitted price range).
        for box in ((0, 0, ML, H), (ML + plot_w, 0, W, H),
                    (0, 0, W, MT), (0, MT + plot_h, W, H)):
            c.create_rectangle(*box, fill=COLOR_PANEL, outline="")

        # Title
        zoomed = "  ·  zoomed" if self._chart_view else ""
        c.create_text(W / 2, MT / 2,
                      text=f"{name}  —  {range_label}  "
                           f"({len(visible)} points){zoomed}",
                      fill=COLOR_ACCENT, font=("Georgia", 12, "bold"))

        # Axes
        c.create_line(ML, MT, ML, MT + plot_h, fill=COLOR_DIM)
        c.create_line(ML, MT + plot_h, ML + plot_w, MT + plot_h, fill=COLOR_DIM)

        # Y labels
        for v, text in zip(grid_vals, y_labels):
            c.create_text(ML - 8, y_pos(v), text=text,
                          fill=COLOR_DIM, anchor="e", font=FONT_SMALL)

        # X tick labels
        span = t_max - t_min
        if span <= 2 * 86400:
            tick_fmt = "%H:%M" if span <= 86400 else "%m/%d %H:%M"
        elif span <= 90 * 86400:
            tick_fmt = "%m/%d"
        else:
            tick_fmt = "%b %Y" if span > 400 * 86400 else "%m/%d/%y"
        ticks = 6
        for i in range(ticks):
            t = t_min + span * i / (ticks - 1)
            x = ML + plot_w * i / (ticks - 1)
            label = time.strftime(tick_fmt, time.localtime(t))
            c.create_line(x, MT + plot_h, x, MT + plot_h + 4, fill=COLOR_DIM)
            c.create_text(x, MT + plot_h + 18, text=label,
                          fill=COLOR_DIM, font=FONT_SMALL)

        # Legend
        legend_x = ML + 8
        legend_y = MT + 8
        for label, color in [("High", COLOR_GREEN),
                             ("Low",  COLOR_RED),
                             ("Mid",  COLOR_BLUE)]:
            c.create_line(legend_x, legend_y, legend_x + 18, legend_y,
                          fill=color, width=2)
            c.create_text(legend_x + 24, legend_y, text=label,
                          fill=COLOR_TEXT, anchor="w", font=FONT_SMALL)
            legend_x += 64

        c.create_text(W - MR, H - 8, anchor="se", fill=COLOR_DIM,
                      font=FONT_SMALL,
                      text="Scroll to zoom  ·  drag to pan  ·  "
                           "double-click to reset")

    # ── Chart zoom and pan ────────────────────────────────────────────────────

    def _set_chart_view(self, t0: float, t1: float):
        """Zoom to [t0, t1], kept inside the data; the full span un-zooms."""
        full_min, full_max = self._chart_geom["full"]
        span = t1 - t0
        if span >= full_max - full_min:
            self._chart_view = None
        else:
            t0 = min(max(t0, full_min), full_max - span)
            self._chart_view = (t0, t0 + span)
        self._render_chart()

    def _reset_chart_zoom(self):
        if self._chart_view is None:
            return
        self._chart_view = None
        self._render_chart()

    def _on_chart_wheel(self, event):
        g = self._chart_geom
        if not g or not event.delta:
            return
        full_min, full_max = g["full"]
        full_span = full_max - full_min
        if full_span <= 0:
            return
        # Windows sends ±120 per wheel notch; macOS sends small values,
        # many per trackpad swipe, so each one zooms only a little.
        if sys.platform == "darwin":
            factor = 0.97 ** event.delta
        else:
            factor = 0.8 ** (event.delta / 120)
        t0, t1 = g["view"]
        span = (t1 - t0) * factor
        span = max(span, min(g["min_span"], full_span))
        # Keep the time under the cursor where it is.
        frac = min(1.0, max(0.0, (event.x - g["left"]) / g["width"]))
        anchor = t0 + (t1 - t0) * frac
        start = anchor - span * frac
        self._set_chart_view(start, start + span)

    def _on_chart_press(self, event):
        if self._chart_geom and self._chart_view:
            self._chart_drag = (event.x, self._chart_view)
            self.history_canvas.configure(cursor="fleur")

    def _on_chart_drag(self, event):
        if not self._chart_drag or not self._chart_geom:
            return
        x0, (t0, t1) = self._chart_drag
        shift = -(event.x - x0) / self._chart_geom["width"] * (t1 - t0)
        self._set_chart_view(t0 + shift, t1 + shift)

    def _on_chart_release(self, _event):
        self._chart_drag = None
        self.history_canvas.configure(cursor="")

    # ── Ask AI tab ────────────────────────────────────────────────────────────

    def _build_ai_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)
        prefs = settings.load()
        self._ai_tools = aitools.MarketTools(lambda: self.item_data,
                                             self._volume)
        self._ai_chat = None           # the current ai.Chat
        self._ai_cancel = None         # set while a question is running
        self._ai_seq = 0               # latest question (see _ai_send)
        self._ai_live_models = {}      # (provider, url) -> model IDs

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 10))

        ttk.Label(bar, text="Provider:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        provider = prefs.get("ai_provider")
        self.ai_provider_var = tk.StringVar(
            value=provider if provider in ai.PROVIDERS else ai.PROVIDERS[0])
        provider_combo = ttk.Combobox(
            bar, textvariable=self.ai_provider_var, state="readonly",
            width=17, values=ai.PROVIDERS)
        provider_combo.pack(side="left", padx=(0, 16))
        provider_combo.bind("<<ComboboxSelected>>",
                            lambda _e: self._on_ai_provider_changed())

        ttk.Label(bar, text="Model:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        self.ai_model_var = tk.StringVar()
        self.ai_model_combo = ttk.Combobox(
            bar, textvariable=self.ai_model_var, width=30,
            style="AIModel.TCombobox",
            postcommand=self._fit_ai_model_list)
        for event in ("<<ComboboxSelected>>", "<FocusOut>", "<Return>"):
            self.ai_model_combo.bind(event,
                                     lambda _e: self._save_ai_model())

        # Packed from the right first, so the model box can take the space
        # left over: some model IDs are long.
        ttk.Button(bar, text="New chat", command=self._ai_new_chat
                   ).pack(side="right")
        self.ai_key_status = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.ai_key_status, style="Dim.TLabel"
                  ).pack(side="right", padx=12)
        ttk.Button(bar, text="API key…",
                   command=lambda: self._ai_key_dialog(
                       self.ai_provider_var.get())).pack(side="right")
        self.ai_model_combo.pack(side="left", fill="x", expand=True,
                                 padx=(0, 16))

        # Packed bottom-up so the input stays visible in a short window.
        ttk.Label(frame,
                  text=("Your question and the data the AI looks up go to "
                        "the service you chose, which bills your account. "
                        "Answers can be wrong. See Help."),
                  style="Dim.TLabel", wraplength=1000, justify="center"
                  ).pack(side="bottom", pady=(0, 10))

        input_row = ttk.Frame(frame)
        input_row.pack(side="bottom", fill="x", padx=20, pady=(0, 8))
        self.ai_send_btn = ttk.Button(input_row, text="Send",
                                      command=self._ai_send)
        self.ai_send_btn.pack(side="right", padx=(8, 0))
        self.ai_stop_btn = ttk.Button(input_row, text="Stop", state="disabled",
                                      command=self._ai_stop)
        self.ai_stop_btn.pack(side="right", padx=(8, 0))
        self.ai_input = tk.Text(
            input_row, height=3, wrap="word",
            background=COLOR_INPUT, foreground=COLOR_TEXT,
            insertbackground=COLOR_ACCENT,
            highlightthickness=1, highlightbackground=COLOR_BORDER,
            highlightcolor=COLOR_ACCENT, borderwidth=0,
            padx=8, pady=6, font=FONT_BODY)
        self.ai_input.pack(side="left", fill="x", expand=True)
        self.ai_input.bind("<Return>", self._on_ai_input_return)

        starters = ttk.Frame(frame)
        starters.pack(side="bottom", fill="x", padx=20, pady=(0, 8))
        self.ai_status = tk.StringVar(value="")
        ttk.Label(starters, textvariable=self.ai_status, style="Dim.TLabel"
                  ).pack(side="right")
        for label, question in AI_STARTERS:
            ttk.Button(starters, text=label, style="Range.TButton",
                       command=lambda q=question: self._ai_send(q)
                       ).pack(side="left", padx=(0, 6))

        text = scrolledtext.ScrolledText(
            frame, wrap="word", state="disabled",
            background=COLOR_PANEL, foreground=COLOR_TEXT,
            insertbackground=COLOR_ACCENT,
            highlightthickness=0, borderwidth=0,
            padx=20, pady=16, font=FONT_BODY)
        text.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        text.tag_configure("speaker", foreground=COLOR_ACCENT,
                           font=("Georgia", 12, "bold"), spacing1=10,
                           spacing3=4)
        text.tag_configure("body", foreground=COLOR_TEXT, spacing3=4)
        text.tag_configure("note", foreground=COLOR_DIM, spacing3=4)
        text.tag_configure("error", foreground=COLOR_RED, spacing3=4)
        self.ai_text = text

        self._show_ai_models()
        self._ai_write(AI_INTRO + "\n", "note")
        return frame

    def _ai_model_for(self, provider: str) -> str:
        """The model last used with this provider, or its default."""
        saved = settings.load().get(f"ai_model_{provider.lower()}")
        return saved or next(iter(ai.MODELS[provider]), "")

    def _show_ai_models(self):
        provider = self.ai_provider_var.get()
        self._set_ai_model_choices(provider)
        self.ai_model_var.set(self._ai_model_for(provider))

    def _set_ai_model_choices(self, provider: str):
        """The suggested models first, then the service's full list."""
        suggested = ai.MODELS[provider]
        live = self._ai_live_models.get((provider, ai.base_url(provider)), [])
        self.ai_model_combo.configure(
            values=suggested + [m for m in live if m not in suggested])

    def _fit_ai_model_list(self):
        """Runs just before the list opens: widen it past the model box if
        the longest model ID wouldn't fit."""
        combo = self.ai_model_combo
        font = tkfont.nametofont("TkTextFont")
        models = combo.tk.splitlist(combo.cget("values"))
        longest = max((font.measure(m) for m in models), default=0)
        extra = longest + AI_MODEL_LIST_MARGIN - combo.winfo_width()
        ttk.Style().configure("AIModel.TCombobox",
                              postoffset=(0, 0, max(0, extra), 0))

    def _refresh_ai_models(self, force: bool = False):
        """Fetch the selected service's model list, once per session."""
        provider = self.ai_provider_var.get()
        url = ai.base_url(provider)
        if not ai.has_model_list(provider) or not ai.is_set_up(provider):
            return
        cache_key = (provider, url)
        if cache_key in self._ai_live_models and not force:
            return
        api_key = ai.get_key(provider)

        def done(models, err):
            if err:
                return     # logged; the suggestions and typing an ID still work
            self._ai_live_models[cache_key] = models
            if self.ai_provider_var.get() == provider:
                self._set_ai_model_choices(provider)

        run_async(lambda: ai.list_models(api_key, url), done)

    def _on_ai_provider_changed(self):
        settings.save({"ai_provider": self.ai_provider_var.get()})
        self._show_ai_models()
        self._update_ai_key_status()
        self._refresh_ai_models()

    def _save_ai_model(self):
        model = self.ai_model_var.get().strip()
        if model:
            provider = self.ai_provider_var.get().lower()
            settings.save({f"ai_model_{provider}": model})

    def _update_ai_key_status(self):
        provider = self.ai_provider_var.get()
        name = ai.service_name(provider, ai.base_url(provider))
        self.ai_key_status.set(f"{name} key saved" if ai.is_set_up(provider)
                               else f"No {provider} key yet")

    def _on_ai_input_return(self, event):
        """Enter sends; Shift+Enter starts a new line."""
        if event.state & 0x0001:
            return None
        self._ai_send()
        return "break"

    def _ai_write(self, text: str, tag: str = "body"):
        # Follow the reply only if the reader hasn't scrolled up.
        at_end = self.ai_text.yview()[1] >= 0.999
        self.ai_text.configure(state="normal")
        self.ai_text.insert("end", text, tag)
        self.ai_text.configure(state="disabled")
        if at_end:
            self.ai_text.see("end")

    def _set_ai_busy(self, busy: bool):
        self.ai_send_btn.config(state="disabled" if busy else "normal")
        self.ai_stop_btn.config(state="normal" if busy else "disabled")

    def _ai_send(self, question: str = None):
        if self._ai_cancel:
            return                     # still answering; Stop is available
        typed = question is None
        if typed:
            question = self.ai_input.get("1.0", "end").strip()
        provider = self.ai_provider_var.get()
        model = self.ai_model_var.get().strip()
        if not question:
            return
        if not model:
            self.ai_status.set("Choose a model first.")
            return

        if not ai.is_set_up(provider) and not self._ai_key_dialog(provider):
            return
        api_key = ai.get_key(provider)
        url = ai.base_url(provider)
        if typed:
            self.ai_input.delete("1.0", "end")
        self._save_ai_model()

        chat = self._ai_chat
        if not chat or (chat.provider, chat.model, chat.url) != (provider, model, url):
            if chat and chat.history:
                self._ai_write(f"\nNow using {model}. This is a new "
                               "conversation, so it can't see the earlier "
                               "questions.\n", "note")
            chat = self._ai_chat = ai.Chat(provider, model, self._ai_tools, url)

        self._ai_write("You\n", "speaker")
        self._ai_write(question + "\n", "body")
        self._ai_write(f"{model}\n", "speaker")

        # Each question gets a number; once it is stopped or replaced, any
        # output still on its way from the old thread is ignored.
        self._ai_seq += 1
        seq = self._ai_seq
        cancel = self._ai_cancel = threading.Event()
        self._set_ai_busy(True)

        def if_current(action, value):
            if seq == self._ai_seq:
                action(value)

        def on_text(chunk):
            ui_call(if_current, self._ai_write, chunk)

        def on_status(text):
            ui_call(if_current, self.ai_status.set, text)

        def ask():
            return chat.send(question, api_key, on_text, on_status, cancel)

        def done(result, err):
            if seq != self._ai_seq:
                return
            self._ai_cancel = None
            self._set_ai_busy(False)
            self._ai_write("\n")
            if err:
                self.ai_status.set("")
                self._ai_write(
                    f"{err}\n" if isinstance(err, ai.AIError) else
                    "Something went wrong while asking the AI. The details "
                    "were saved to the log file.\n", "error")
                return
            if result["notice"]:
                self._ai_write(result["notice"] + "\n", "note")
            self.ai_status.set(f"Used {result['tokens']:,} tokens."
                               if result["tokens"] else "Done.")

        run_async(ask, done)

    def _ai_stop(self):
        if self._ai_cancel:
            self._ai_cancel.set()
            self._ai_cancel = None
            self._ai_seq += 1
            self._set_ai_busy(False)
            self.ai_status.set("")
            self._ai_write("\n(Stopped. The AI won't remember this "
                           "question.)\n", "note")

    def _ai_new_chat(self):
        self._ai_stop()
        self._ai_chat = None
        self.ai_status.set("")
        self.ai_text.configure(state="normal")
        self.ai_text.delete("1.0", "end")
        self.ai_text.configure(state="disabled")
        self._ai_write(AI_INTRO + "\n", "note")

    def _ai_key_dialog(self, provider: str) -> bool:
        """Modal dialog to add or remove an API key. True if a key was saved."""
        dlg = tk.Toplevel(self.root)
        dlg.title(f"{provider} API key")
        dlg.configure(bg=COLOR_BG)
        dlg.resizable(False, False)
        dlg.transient(self.root)

        body = ttk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=24, pady=20)

        ttk.Label(body, text=f"{provider} API key", style="Header.TLabel"
                  ).pack(anchor="w", pady=(0, 10))
        custom = provider == ai.COMPATIBLE
        ttk.Label(body, wraplength=480, justify="left", text=(
            "Use any service with an OpenAI-compatible API, such as Groq, or "
            "a local server such as Ollama. Any charges come from that "
            "service." if custom else
            f"Ask AI uses your own {provider} account, and {provider} bills "
            "you for each question. A Claude or ChatGPT subscription doesn't "
            "include API use.")).pack(anchor="w")
        if provider in ai.KEY_PAGES:
            ttk.Button(body, text=f"Get a key from {provider}",
                       command=lambda: webbrowser.open(ai.KEY_PAGES[provider])
                       ).pack(anchor="w", pady=(10, 0))

        url_var = tk.StringVar(value=ai.base_url(provider) or "")
        if custom:
            ttk.Label(body, style="TLabel", text="API URL:"
                      ).pack(anchor="w", pady=(12, 4))
            ttk.Entry(body, textvariable=url_var, width=56
                      ).pack(anchor="w", fill="x")
            ttk.Label(body, style="Dim.TLabel", wraplength=480, justify="left",
                      text=("For example https://api.groq.com/openai/v1 or "
                            "http://localhost:11434/v1")
                      ).pack(anchor="w", pady=(4, 0))

        has_key = bool(ai.get_key(provider))
        key_prompt = "Paste your key:"
        if has_key:
            key_prompt = ("A key is saved. To change the URL or the key, "
                          "enter the key again:" if custom else
                          "A key is saved. Paste a new one to replace it:")
        ttk.Label(body, style="TLabel", text=key_prompt
                  ).pack(anchor="w", pady=(12, 4))
        var = tk.StringVar()
        entry = ttk.Entry(body, textvariable=var, width=56, show="•")
        entry.pack(anchor="w", fill="x")
        entry.focus_set()

        ttk.Label(body, style="Dim.TLabel", wraplength=480, justify="left",
                  text=("Kept in your computer's credential store and only "
                        f"sent to {'this URL' if custom else provider}.")
                  ).pack(anchor="w", pady=(8, 0))
        error = tk.StringVar()
        tk.Label(body, textvariable=error, bg=COLOR_BG, fg=COLOR_RED,
                 font=FONT_BODY, wraplength=480, justify="left"
                 ).pack(anchor="w", pady=(4, 0))

        result = {"ok": False, "open": True}

        def close(_e=None):
            result["open"] = False
            dlg.destroy()

        def save(_e=None):
            key = var.get().strip()
            url = ai.base_url(provider)
            if custom:
                try:
                    url = ai.clean_base_url(url_var.get())
                except ai.AIError as exc:
                    error.set(str(exc))
                    return
            if not ai.looks_like_key(key):
                error.set("That doesn't look like an API key. Copy the whole "
                          "key, with no spaces. A local server with no keys "
                          "accepts any text of 8 or more characters.")
                return
            save_btn.config(state="disabled")
            error.set("Checking the key…")

            def done(warning, err):
                if not result["open"]:
                    return             # the dialog was cancelled meanwhile
                save_btn.config(state="normal")
                if err:
                    error.set(str(err) if isinstance(err, ai.AIError) else
                              f"Could not check the key: {type(err).__name__}")
                    return
                note = warning or f"{ai.service_name(provider, url)} key saved."
                if custom:
                    settings.save({"ai_base_url": url})
                if not ai.save_key(provider, key):
                    note = ("No credential store is available, so the key "
                            "will only be kept until you close the app.")
                self.ai_status.set(note)
                self._update_ai_key_status()
                self._refresh_ai_models(force=True)
                result["ok"] = True
                close()

            run_async(lambda: ai.verify_key(provider, key, url), done)

        def remove():
            ai.delete_key(provider)
            self.ai_status.set(f"{provider} key removed.")
            self._update_ai_key_status()
            close()

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=close).pack(side="right")
        save_btn = ttk.Button(buttons, text="Save", command=save)
        save_btn.pack(side="right", padx=(0, 8))
        if has_key:
            ttk.Button(buttons, text="Remove key", command=remove
                       ).pack(side="left")
        dlg.bind("<Return>", save)
        dlg.bind("<Escape>", close)
        dlg.protocol("WM_DELETE_WINDOW", close)

        dlg.update_idletasks()
        x = (dlg.winfo_screenwidth() - dlg.winfo_reqwidth()) // 2
        y = (dlg.winfo_screenheight() - dlg.winfo_reqheight()) // 3
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()
        dlg.lift()
        self.root.wait_window(dlg)
        return result["ok"]

    # ── Help tab ──────────────────────────────────────────────────────────────

    def _build_help_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 0))
        self.contact_label_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.contact_label_var,
                  style="TLabel").pack(side="left")
        ttk.Button(bar, text="Change contact…",
                   command=lambda: self._contact_dialog(first_run=False)
                   ).pack(side="left", padx=12)
        self._update_contact_label()

        about = ttk.Frame(frame)
        about.pack(fill="x", padx=20, pady=(10, 0))
        ttk.Label(about, text=f"Version {APP_VERSION}", style="TLabel"
                  ).pack(side="left")
        self.update_btn = ttk.Button(about, text="Check for updates",
                                     command=self._check_for_updates)
        self.update_btn.pack(side="left", padx=12)
        ttk.Button(about, text="Open data folder",
                   command=self._open_data_folder).pack(side="left")
        self.update_status = tk.StringVar(value="")
        ttk.Label(about, textvariable=self.update_status, style="Dim.TLabel"
                  ).pack(side="left", padx=12)

        text = scrolledtext.ScrolledText(
            frame, wrap="word",
            background=COLOR_PANEL, foreground=COLOR_TEXT,
            insertbackground=COLOR_ACCENT,
            highlightthickness=0, borderwidth=0,
            padx=24, pady=20,
            font=FONT_BODY,
        )
        text.pack(fill="both", expand=True, padx=20, pady=20)

        text.tag_configure("h1", foreground=COLOR_ACCENT,
                           font=("Georgia", 16, "bold"),
                           spacing1=14, spacing3=8)
        text.tag_configure("body", foreground=COLOR_TEXT,
                           font=FONT_BODY, spacing3=6, lmargin1=6, lmargin2=6)

        for heading, body in HELP_SECTIONS:
            text.insert("end", heading + "\n", "h1")
            text.insert("end", body + "\n\n", "body")

        text.configure(state="disabled")
        return frame

    # ── About: updates, data folder, error reporting ──────────────────────────

    def _check_for_updates(self):
        self.update_btn.config(state="disabled")
        self.update_status.set("Checking for updates…")

        def done(rel, err):
            self.update_btn.config(state="normal")
            if err:
                self.update_status.set(f"Update check failed: {err}")
                return
            if rel is None:
                self.update_status.set("No releases have been published yet.")
                return
            if not updater.is_newer(rel["version"]):
                self.update_status.set(
                    f"You're on the latest version ({APP_VERSION}).")
                return
            self.update_status.set(f"Version {rel['version']} is available.")
            self._offer_update(rel)

        run_async(updater.latest_release, done)

    def _offer_update(self, rel: dict):
        newer = f"Version {rel['version']} is available (you have {APP_VERSION})."
        if not updater.can_self_install() or not rel["asset"]:
            # Running from source, or no download for this system.
            if messagebox.askyesno("Update available",
                                   f"{newer}\n\nOpen the download page?"):
                webbrowser.open(rel["page"])
            return
        if sys.platform == "win32":
            question = (f"{newer}\n\nDownload and install it now? The app "
                        "will close, update itself and reopen.")
        else:
            question = (f"{newer}\n\nDownload it now? The disk image will "
                        "open; quit OSRS Tracker and drag the new version to "
                        "Applications to replace it.")
        if not messagebox.askyesno("Update available", question):
            return

        self.update_btn.config(state="disabled")

        def progress(done_bytes, total):
            pct = f" {done_bytes * 100 // total}%" if total else ""
            ui_call(lambda: self.update_status.set(
                f"Downloading {rel['asset']['name']}…{pct}"))

        def finished(path, err):
            self.update_btn.config(state="normal")
            if err:
                self.update_status.set(f"Update failed: {err}")
                return
            applog.log.info(f"Installing update {rel['tag']} from {path}")
            try:
                updater.launch_installer(path)
            except Exception as exc:
                applog.log.exception("Could not start the installer")
                self.update_status.set(f"Couldn't start the installer: {exc}")
                return
            if sys.platform == "win32":
                self.root.destroy()   # let the installer replace the app
            else:
                self.update_status.set("Update downloaded — drag it into "
                                       "Applications, then reopen the app.")

        run_async(lambda: updater.download(rel["asset"], progress),
                  finished)

    def _open_data_folder(self):
        folder = os.path.dirname(applog.LOG_FILE)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showinfo("Data folder", f"Your data is in:\n{folder}\n\n"
                                f"(Couldn't open it automatically: {exc})")

    def _on_tk_error(self, exc_type, exc, tb):
        applog.log.error("Error in the user interface",
                         exc_info=(exc_type, exc, tb))
        if not self._error_shown:
            self._error_shown = True
            messagebox.showerror(
                "Something went wrong",
                f"{exc_type.__name__}: {exc}\n\nThe details were saved to the "
                f"log file:\n{applog.LOG_FILE}\n\nYou can keep using the app. "
                "If it keeps happening, please report it on the project's "
                "GitHub Issues page and attach the log.")

    # ── Tab change handler ────────────────────────────────────────────────────

    def _on_tab_changed(self, _event):
        tab = self.notebook.nametowidget(self.notebook.select())
        if tab is self.search_tab:
            if self.item_data:
                self.search_entry.focus_set()
        elif tab is self.alerts_tab:
            self._render_alerts()
        elif tab is self.watchlist_tab:
            self._render_watchlist()
        elif tab is self.volatile_tab:
            # Auto-refresh on first visit; subsequent visits keep the table
            # so the user doesn't pay the API cost on every tab switch.
            if not self.volatile_tree.get_children():
                self._refresh_volatile()
        elif tab is self.fliprec_tab:
            # Same: fetch on first visit; the Refresh button re-fetches.
            if self._flip_raw is None:
                self._refresh_fliprec()
        elif tab is self.ai_tab:
            self._update_ai_key_status()
            self._refresh_ai_models()
            self.ai_input.focus_set()
        elif tab is self.news_tab:
            self._reload_news_cache()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    applog.setup()
    root = tk.Tk()
    app = OSRSTrackerApp(root)
    if app.ready:              # False if the user quit first-time setup
        root.mainloop()


if __name__ == "__main__":
    main()
