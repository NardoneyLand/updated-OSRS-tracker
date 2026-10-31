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
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, scrolledtext

import api
import analyzer
import applog
import db
import scraper
import search as search_module
import settings
import updater
import useragent
from version import APP_VERSION


# ── Constants ─────────────────────────────────────────────────────────────────
# Profit alerts: checked in the background while the app is open.
ALERT_CHECK_MS = 5 * 60 * 1000          # every 5 minutes
ALERT_COOLDOWN = 3600                   # re-alert an item after 1 hour
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
STEP_SECONDS = {"5m": 300, "1h": 3600, "6h": 6 * 3600, "24h": 86400}
CHART_MIN_POINTS = 4              # zooming stops at about this many points
FLIP_MAX_ROWS = 300

# Fantasy / OSRS-inspired palette (avoiding Jagex copyrighted colors directly)
COLOR_BG       = "#1a120b"   # near-black brown
COLOR_PANEL    = "#2d1f12"   # warm dark brown
COLOR_PANEL_HI = "#3b2818"   # raised panel
COLOR_BORDER   = "#5e3f1f"   # weathered wood border
COLOR_ACCENT   = "#f0b32c"   # gold
COLOR_ACCENT_HI = "#ffd87a"  # bright gold
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


def fmt_age(seconds) -> str:
    """Compact age: 45s, 12m, 3h."""
    s = int(seconds or 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def trend_arrow(direction: str) -> tuple:
    return {
        "rising":  ("↑", COLOR_GREEN),
        "falling": ("↓", COLOR_RED),
        "stable":  ("→", COLOR_YELLOW),
        "unknown": ("?", COLOR_DIM),
    }.get(direction, ("?", COLOR_DIM))


def signal_color(sugg: str) -> str:
    if "BUY" in sugg:
        return COLOR_GREEN
    if "SELL" in sugg:
        return COLOR_RED
    return COLOR_YELLOW


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

def run_async(root: tk.Tk, target, on_done):
    def worker():
        try:
            result = target()
            err = None
        except Exception as e:
            applog.log.warning(f"Background task {target.__name__} failed",
                               exc_info=True)
            result, err = None, e
        root.after(0, lambda: on_done(result, err))
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
     "(1M by default). The app checks for you every 5 minutes.\n"
     "    5.  Add items you want to follow to your Watchlist from the "
     "Search tab.\n\n"
     "The tabs, left to right: FlipRec · Alerts · Watchlist · Volatile · "
     "Search · News · Help."),

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
     "    •  Prices — Short (1.2K) or Exact (1,240). Also used by Alerts "
     "and Watchlist.\n"
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
     "    •  Vol/hr — units traded in the last hour on the slower side.\n"
     "    •  Last trade — how long ago the older side last traded. "
     "⏱ means over 15 minutes, so the margin may have moved.\n"
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
     "    •  Suspect — looks too good to be true: ROI over 40%, or the "
     "price is 25%+ away from its 1-hour average. Often one odd trade or "
     "manipulation.\n"
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
     "While the app is open it checks the market every 5 minutes and logs "
     "an alert when a flip's Est. profit (calculated as in FlipRec, using "
     "your capital and item filter) reaches your threshold.\n\n"
     "    •  Threshold — the picker next to \"Alert when est. profit ≥\". "
     "1,000,000 by default, in 100,000 steps (arrows or mouse wheel). "
     "Changing it updates the list straight away: raising it hides "
     "smaller alerts, lowering it shows them again and adds any new "
     "ones.\n"
     "    •  Only actively traded items count: both sides must have "
     "traded in the last 15 minutes. Suspect items are ignored.\n"
     "    •  Each item alerts at most once an hour. \"Times\" counts how "
     "often it has alerted.\n"
     "    •  New alerts show as a count on the tab, e.g. Alerts (3), and "
     "flash the taskbar on Windows.\n"
     "    •  Dismiss selected clears an item; Dismiss all clears "
     "everything. Alerts are kept for 7 days.\n"
     "    •  Check now runs a check immediately.\n\n"
     "Nothing is checked while the app is closed. Without a Capital set "
     "on FlipRec, quantities are limited only by buy limits and volume, "
     "so you'll see more (and larger) alerts."),

    ("Watchlist — items you follow",
     "Your own hand-picked list. To add an item, search it and click "
     "☆ Add to Watchlist on its result; click ★ On Watchlist to remove it "
     "again. You can also select rows on the Watchlist tab and click "
     "Remove selected.\n\n"
     "Each item shows its live Buy, Sell, Net / item, ROI, volume, last "
     "trade and verdict, using the same maths as FlipRec. It updates with "
     "every 5-minute market check, or when you click Refresh. Double-click "
     "a row to open the item in Search."),

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
     "    •  Trend — direction over the item's recent stored prices.\n"
     "    •  Signal — BUY, HOLD or SELL (see below).\n"
     "    •  Volume — trading now vs the recent average.\n"
     "    •  Momentum — whether the move is speeding up or slowing.\n\n"
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

    ("BUY / HOLD / SELL — what they mean",
     "The Search signal is a rule of thumb, not financial advice. It "
     "scores from −3 to +3:\n\n"
     "    +1 / −1   for price direction (rising / falling)\n"
     "    +1 / −1   for volume confirming that direction\n"
     "    +1 / −1   for momentum (speeding up / slowing)\n\n"
     "Score ≥ 2 → BUY (high), 1 → BUY (moderate), 0 → HOLD, "
     "−1 → HOLD/SELL, ≤ −2 → SELL. Moves over ±25% trigger a "
     "manipulation guard that pulls the signal toward neutral."),

    ("News",
     "The latest official OSRS news loads automatically when the app "
     "starts. Refresh from sources fetches it again; Reload cache "
     "re-reads what's already saved. Double-click an article to open it "
     "in your browser."),

    ("How fresh is the data?",
     "The OSRS Wiki updates prices from the game about every 60 "
     "seconds. FlipRec fetches when you open it or click Refresh; Alerts "
     "and Watchlist update every 5 minutes; Search is live each time. "
     "Last trade (FlipRec, Watchlist) shows exactly how old each margin "
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
        self._market = None            # (latest, bulk_1h, fetched_at)
        self._monitor_started = False
        self._monitor_busy = False
        self._search_item = None       # item shown in the Search panel
        self._lookup_seq = 0           # latest Search lookup (see _lookup_item)
        self._error_shown = False
        # Errors inside button clicks / timers: log them and tell the user
        # once, instead of failing silently in the windowed app.
        self.root.report_callback_exception = self._on_tk_error

        self._configure_root()
        self._build_ui()

        db.initialize_db()
        # A contact is required before any network request (see useragent.py).
        if not self._ensure_contact():
            self.root.destroy()
            return
        self.ready = True
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
                  foreground=[("active", COLOR_ACCENT_HI)])

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

        self.search_tab = self._build_search_tab()
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

        run_async(self.root, api.get_mapping, done)

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
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<KeyRelease>", self._on_search_keyrelease)
        self.search_entry.bind("<Down>", self._focus_dropdown)
        self.search_entry.bind("<Return>", self._on_search_enter)
        self.search_entry.bind("<Escape>", lambda _e: self._hide_dropdown())

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
        self._chart_view = None           # (t0, t1) when zoomed, else None
        self._chart_geom = None           # plot layout, for mouse handlers
        self._chart_drag = None           # (start x, view) while panning

        range_bar = ttk.Frame(frame)
        range_bar.pack(side="top", fill="x", padx=20, pady=(0, 6))
        ttk.Label(range_bar, text="Chart range:", style="TLabel").pack(
            side="left", padx=(0, 8))
        for label, _secs, _step in HISTORY_RANGES:
            btn = ttk.Button(range_bar, text=label, style="Range.TButton",
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

            history = db.get_price_history(item_id, limit=30)
            trend = analyzer.calculate_trend(history)
            prediction = analyzer.get_prediction(trend, ts_data)

            return {
                "item": item, "price_data": price_data,
                "trend": trend, "prediction": prediction,
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

        run_async(self.root, fetch, done)

    def _render_message(self, text: str):
        for child in self.result_frame.winfo_children():
            child.destroy()
        ttk.Label(self.result_frame, text=text,
                  style="Panel.TLabel", justify="center"
                  ).pack(expand=True, pady=20)

    def _render_result(self, item, price_data, trend, prediction):
        for child in self.result_frame.winfo_children():
            child.destroy()

        name = item["name"]
        high = price_data.get("high") or 0
        low = price_data.get("low") or high
        spread = high - low
        spread_pct = (spread / low * 100) if low else 0

        arrow, tcolor = trend_arrow(trend["direction"])
        sugg = prediction["suggestion"]
        sc = signal_color(sugg)

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
            ("High buy", f"{fmt_gp(high)} gp", COLOR_GREEN),
            ("Low sell", f"{fmt_gp(low)} gp", COLOR_RED),
            ("Spread",   f"{fmt_gp(spread)} gp  ({spread_pct:.1f}%)", COLOR_TEXT),
            ("Trend",
             f"{arrow}  {trend['direction'].capitalize()}  "
             f"({trend['pct_change']:+.1f}%)  "
             f"over {trend['data_points']} snapshots",
             tcolor),
            ("Signal",
             f"{sugg}  ({prediction['confidence']} confidence)",
             sc),
            ("Volume",
             f"{prediction['vol_label'].title()}  "
             f"(recent {fmt_gp(prediction['recent_vol'])} / "
             f"avg {fmt_gp(prediction['avg_volume'])})",
             COLOR_TEXT),
            ("Momentum", prediction["momentum"].capitalize(), COLOR_TEXT),
        ]

        for i, (label, value, color) in enumerate(rows):
            ttk.Label(grid, text=label, style="Stat.TLabel"
                      ).grid(row=i, column=0, sticky="w", padx=(0, 24), pady=2)
            tk.Label(grid, text=value, bg=COLOR_PANEL, fg=color,
                     font=FONT_VALUE
                     ).grid(row=i, column=1, sticky="w", pady=2)

        # Reasons
        if prediction["reasons"]:
            reasons_frame = ttk.Frame(body, style="Inner.TFrame")
            reasons_frame.pack(side="left", anchor="n", fill="x",
                               expand=True, padx=(40, 0))
            ttk.Label(reasons_frame, text="Analysis:",
                      style="PanelHeader.TLabel"
                      ).pack(anchor="w")
            for r in prediction["reasons"]:
                ttk.Label(reasons_frame, text=f"  •  {r}",
                          style="Panel.TLabel",
                          wraplength=520, justify="left"
                          ).pack(anchor="w")

    # ── Alerts tab ────────────────────────────────────────────────────────────

    ALERT_COLUMNS = [
        # (tree id, header, width, anchor)
        ("item",   "Item",          230, "w"),
        ("buy",    "Buy (gp)",      95,  "e"),
        ("sell",   "Sell (gp)",     95,  "e"),
        ("net",    "Net / item",    90,  "e"),
        ("qty",    "Qty",           75,  "e"),
        ("profit", "Est. profit",   115, "e"),
        ("roi",    "ROI",           65,  "e"),
        ("when",   "Alerted",       110, "w"),
        ("hits",   "Times",         60,  "e"),
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

        self.alerts_check_btn = ttk.Button(
            bar, text="Check now",
            command=lambda: self._monitor_tick(manual=True))
        self.alerts_check_btn.pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Dismiss selected",
                   command=self._dismiss_selected_alerts
                   ).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Dismiss all",
                   command=self._dismiss_all_alerts).pack(side="left")

        self.alerts_status = tk.StringVar(value="Waiting for first check…")
        ttk.Label(bar, textvariable=self.alerts_status, style="Dim.TLabel"
                  ).pack(side="left", padx=12)

        cols = tuple(c[0] for c in self.ALERT_COLUMNS)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        for cid, label, width, anchor in self.ALERT_COLUMNS:
            tree.heading(cid, text=label)
            tree.column(cid, width=width, anchor=anchor)
        tree.tag_configure("alert", foreground=COLOR_GREEN)
        tree.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        tree.bind("<Double-Button-1>",
                  lambda _e: self._open_selected_in_search(
                      self.alerts_tree, self.alert_id_map))
        self.alerts_tree = tree
        self.alert_id_map = {}

        ttk.Label(frame,
                  text=("Checks the market every 5 minutes while the app is "
                        "open. Uses your FlipRec capital and item filter. "
                        "Only items where both sides traded in the last 15 "
                        "minutes; Suspect items are ignored. Each item "
                        "alerts at most once an hour; alerts are kept for 7 "
                        "days. Double-click a row to open it in Search."),
                  style="Dim.TLabel", wraplength=1000, justify="center"
                  ).pack(pady=(0, 10))
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
        if self._evaluate_alerts():
            flash_window(self.root)
        self._render_alerts()

    def _monitor_tick(self, manual: bool = False):
        """Fetch the market, log profit alerts, refresh Alerts + Watchlist."""
        if not manual:
            self.root.after(ALERT_CHECK_MS, self._monitor_tick)
        if self._monitor_busy or not self.item_data:
            return
        self._monitor_busy = True
        self.alerts_check_btn.config(state="disabled")
        self.alerts_status.set("Checking the market…")

        def task():
            return api.get_all_latest_prices(), api.get_bulk_average("1h")

        def done(payload, err):
            self._monitor_busy = False
            self.alerts_check_btn.config(state="normal")
            if err or not payload or not payload[0]:
                self.alerts_status.set(
                    f"Check failed: {err}" if err else "Check failed: no data.")
                return
            self._market = (payload[0], payload[1], time.time())
            db.purge_old_alerts()
            new = self._evaluate_alerts()
            self._render_alerts()
            self._render_watchlist()
            stamp = time.strftime("%H:%M", time.localtime(self._market[2]))
            self.alerts_status.set(
                f"Last checked {stamp} · {new} new · next check in 5 min")
            if new:
                flash_window(self.root)

        run_async(self.root, task, done)

    def _evaluate_alerts(self) -> int:
        """Log alerts from the last market snapshot. Returns how many new."""
        if not self._market or not self.item_data:
            return 0
        latest, bulk, fetched_at = self._market
        rows = analyzer.find_flips(
            latest, bulk, self.item_data,
            capital=self._flip_capital() or 0,
            membership=self._flip_membership(), now=fetched_at)
        threshold = self._alert_threshold()
        now = int(time.time())
        new = 0
        for r in rows:
            if (not r["active"] or r["slow"] or r["verdict"] == "Suspect"
                    or r["profit"] < threshold):
                continue
            last = db.last_alert_time(r["item_id"])
            if last and now - last < ALERT_COOLDOWN:
                continue
            db.record_profit_alert(r, now)
            new += 1
        return new

    def _render_alerts(self):
        tree = self.alerts_tree
        for r in tree.get_children():
            tree.delete(r)
        self.alert_id_map.clear()
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
        fmt = self._price_fmt()
        for a in rows:
            iid = tree.insert("", "end", tags=("alert",), values=(
                a["item_name"], fmt(a["buy"]), fmt(a["sell"]), fmt(a["net"]),
                fmt(a["qty"]), fmt(a["profit"]), f"{a['roi']:.1f}%",
                time.strftime("%m/%d %H:%M", time.localtime(a["timestamp"])),
                a["n"],
            ))
            self.alert_id_map[iid] = a["item_id"]

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
        ("item",    "Item",        230, "w"),
        ("buy",     "Buy (gp)",    95,  "e"),
        ("sell",    "Sell (gp)",   95,  "e"),
        ("net",     "Net / item",  90,  "e"),
        ("roi",     "ROI",         65,  "e"),
        ("volume",  "Vol/hr",      75,  "e"),
        ("age",     "Last trade",  80,  "e"),
        ("verdict", "Verdict",     110, "center"),
        ("added",   "Added",       100, "w"),
    ]

    def _build_watchlist_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Button(bar, text="Refresh",
                   command=lambda: self._monitor_tick(manual=True)
                   ).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Remove selected",
                   command=self._remove_selected_watch).pack(side="left")
        ttk.Label(bar, text="Add items with the ☆ button on a Search result.",
                  style="Dim.TLabel").pack(side="left", padx=12)

        cols = tuple(c[0] for c in self.WATCH_COLUMNS)
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        for cid, label, width, anchor in self.WATCH_COLUMNS:
            tree.heading(cid, text=label)
            tree.column(cid, width=width, anchor=anchor)
        tree.tag_configure("good", foreground=COLOR_GREEN)
        tree.tag_configure("warn", foreground=COLOR_YELLOW)
        tree.tag_configure("bad", foreground=COLOR_RED)
        tree.tag_configure("dim", foreground=COLOR_DIM)
        tree.pack(fill="both", expand=True, padx=20, pady=(0, 10))
        tree.bind("<Double-Button-1>",
                  lambda _e: self._open_selected_in_search(
                      self.watch_tree, self.watch_id_map))
        self.watch_tree = tree
        self.watch_id_map = {}

        ttk.Label(frame,
                  text=("Prices refresh with every market check (every 5 "
                        "minutes) or when you click Refresh. Margins use the "
                        "same maths as FlipRec. Double-click a row to open "
                        "it in Search."),
                  style="Dim.TLabel", wraplength=1000, justify="center"
                  ).pack(pady=(0, 10))
        return frame

    def _render_watchlist(self):
        tree = self.watch_tree
        for r in tree.get_children():
            tree.delete(r)
        self.watch_id_map.clear()
        items = db.watchlist_items()
        if not items:
            tree.insert("", "end", values=(
                "Your watchlist is empty. Search an item and click "
                "☆ Add to Watchlist.", *[""] * (len(self.WATCH_COLUMNS) - 1)))
            return

        fmt = self._price_fmt()
        by_id = {it.get("id"): it for it in self.item_data}
        latest, bulk, fetched_at = self._market or ({}, {}, None)
        capital = self._flip_capital() or 0
        for item_id, name, added in items:
            row = None
            lt = latest.get(str(item_id))
            if lt and item_id in by_id:
                row = analyzer.flip_row(by_id[item_id], lt,
                                        bulk.get(str(item_id)), capital,
                                        now=fetched_at)
            added_s = time.strftime("%m/%d %H:%M", time.localtime(added))
            if row is None:
                values = (name, "…" if not self._market else "N/A",
                          "", "", "", "", "", "", added_s)
                tag = "dim"
            else:
                values = (
                    name, fmt(row["buy"]), fmt(row["sell"]), fmt(row["net"]),
                    f"{row['roi']:.1f}%", fmt(row["volume"]),
                    ("⏱ " if row["slow"] else "") + fmt_age(row["age"]),
                    "⚠ Inactive" if not row["active"] else row["verdict"],
                    added_s,
                )
                tag = {"Profitable": "good", "Marginal": "warn",
                       "Suspect": "warn", "Unprofitable": "bad"}.get(
                           row["verdict"], "dim")
            iid = tree.insert("", "end", values=values, tags=(tag,))
            self.watch_id_map[iid] = item_id

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
        """Price formatter chosen in FlipRec (Short 1.2K / Exact 1,240)."""
        return (fmt_gp_exact if self.flip_format_var.get().startswith("Exact")
                else fmt_gp)

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
            bulk = api.get_bulk_average(timestep)
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

        run_async(self.root, task, done)

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
        ("item",    "Item",        "name",      230, "w",      False),
        ("buy",     "Buy (gp)",    "buy",       95,  "e",      False),
        ("sell",    "Sell (gp)",   "sell",      95,  "e",      True),
        ("tax",     "GE tax (gp)", "tax",       85,  "e",      True),
        ("net",     "Net / item",  "net",       90,  "e",      True),
        ("roi",     "ROI",         "roi",       65,  "e",      True),
        ("limit",   "4h limit",    "limit",     70,  "e",      True),
        ("qty",     "Qty",         "qty",       70,  "e",      True),
        ("profit",  "Est. profit", "profit",    110, "e",      True),
        ("volume",  "Vol/hr",      "volume",    70,  "e",      True),
        ("age",     "Last trade",  "age",       75,  "e",      False),
        ("verdict", "Verdict",     "verdict",   95,  "center", True),
        ("signal",  "Signal",      "reversion", 180, "w",      False),
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

        ttk.Label(bar, text="Prices:", style="TLabel"
                  ).pack(side="left", padx=(0, 6))
        self.flip_format_var = tk.StringVar(
            value=prefs.get("flip_price_format", "Short (1.2K)"))
        fmt_combo = ttk.Combobox(
            bar, textvariable=self.flip_format_var, state="readonly",
            width=14, values=["Short (1.2K)", "Exact (1,240)"])
        fmt_combo.pack(side="left", padx=(0, 16))
        fmt_combo.bind("<<ComboboxSelected>>",
                       lambda _e: self._apply_flip_filters())

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
        self._flip_raw = None         # (latest, bulk_1h, fetched_at)

        ttk.Label(frame,
                  text=("Buy at the instant-sell price, sell at the "
                        "instant-buy price (latest trades, like an in-game "
                        "margin check), minus the 2% GE tax. Last trade = "
                        "age of the older side (⏱ = over 15 min). Qty is "
                        "capped by your capital, the "
                        "4h limit, and ~4h of market volume. Estimates "
                        "only: offers can take time to fill and margins "
                        "close quickly. Inactive = a side has not traded in "
                        "the last hour. Double-click a row to open it in "
                        "Search."),
                  style="Dim.TLabel", wraplength=1000, justify="center"
                  ).pack(pady=(0, 10))
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
            return api.get_all_latest_prices(), api.get_bulk_average("1h")

        def done(payload, err):
            self.flip_refresh_btn.config(state="normal")
            if err or not payload or not payload[0]:
                self.flip_status.set(
                    f"Error: {err}" if err else "Could not fetch prices.")
                return
            self._flip_raw = (payload[0], payload[1], time.time())
            self._market = self._flip_raw
            self._apply_flip_filters()

        run_async(self.root, task, done)

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
        latest, bulk, fetched_at = self._flip_raw
        self.flip_results = analyzer.find_flips(
            latest, bulk, self.item_data, capital=capital,
            membership=self._flip_membership(),
        )
        self.flip_sort = (None, None)
        self._update_flip_arrows()
        self._render_fliprec_table()
        stamp = time.strftime("%H:%M:%S", time.localtime(fetched_at))
        self.flip_status.set(f"Prices fetched at {stamp}")
        # Format/capital/filter also apply to the Alerts and Watchlist tabs.
        self._render_alerts()
        self._render_watchlist()

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
        for r in tree.get_children():
            tree.delete(r)
        self.flip_id_map.clear()

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
                f"15 min; the margin may have moved.")
        self.flip_banner.set("\n".join(banner))

        if not rows:
            tree.insert("", "end", values=(
                "No profitable flips found with these filters.",
                *[""] * (len(self.FLIP_COLUMNS) - 1)))
            return

        fmt = (fmt_gp_exact if self.flip_format_var.get().startswith("Exact")
               else fmt_gp)
        for r in rows:
            tag = {"Profitable": "good", "Marginal": "warn", "Suspect": "warn"}.get(
                r["verdict"], "dim")
            verdict = "⚠ Inactive" if not r["active"] else r["verdict"]
            sig = r["reversion"]
            if sig:
                sig = f"{sig} ({r['reversion_pct']:+.1f}%)"
            iid = tree.insert("", "end", tags=(tag,), values=(
                r["name"], fmt(r["buy"]), fmt(r["sell"]),
                fmt(r["tax"]), fmt(r["net"]), f"{r['roi']:.1f}%",
                fmt(r["limit"]) if r["limit"] else "?",
                fmt(r["qty"]) if r["qty"] else "-",
                fmt(r["profit"]) if r["qty"] else "-",
                fmt(r["volume"]),
                ("⏱ " if r["slow"] else "") + fmt_age(r["age"]),
                verdict, sig,
            ))
            self.flip_id_map[iid] = r["item_id"]

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

        run_async(self.root, task, done)

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

        run_async(self.root, fetch, done)

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

        ML, MR, MT, MB = 70, 30, 40, 60
        plot_w = W - ML - MR
        plot_h = H - MT - MB
        if plot_w < 10 or plot_h < 10:
            return

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
        grid_vals = [y_min + (y_max - y_min) * (i / 4) for i in range(5)]
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

        # Y labels: short form, or exact gp when zoomed in so far that the
        # short labels would repeat (e.g. "1.1K" twice).
        y_labels = [fmt_gp(int(v)) for v in grid_vals]
        if len(set(y_labels)) < len(y_labels):
            y_labels = [fmt_gp_exact(round(v)) for v in grid_vals]
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

        run_async(self.root, updater.latest_release, done)

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
            self.root.after(0, lambda: self.update_status.set(
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

        run_async(self.root, lambda: updater.download(rel["asset"], progress),
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
