"""
gui.py — OSRS Price Tracker — Tkinter desktop interface.

Run from source:
    ./launch.sh

Build a Mac .app bundle:
    ./build.sh
"""

import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox, scrolledtext

import api
import analyzer
import db
import scraper
import search as search_module
import settings


# ── Constants ─────────────────────────────────────────────────────────────────
ALERT_THRESHOLD = 0.10
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
    if gp >= 1_000_000:
        return f"{gp / 1_000_000:.2f}M"
    if gp >= 1_000:
        return f"{gp / 1_000:.1f}K"
    return f"{gp:,}"


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


# ── Threading helper ──────────────────────────────────────────────────────────

def run_async(root: tk.Tk, target, on_done):
    def worker():
        try:
            result = target()
            err = None
        except Exception as e:
            result, err = None, e
        root.after(0, lambda: on_done(result, err))
    threading.Thread(target=worker, daemon=True).start()


# ── Help text ─────────────────────────────────────────────────────────────────

HELP_SECTIONS = [
    ("Welcome",
     "OSRS Price Tracker pulls live prices from the Old School RuneScape "
     "Wiki Prices API and helps you spot trades worth making. Every search "
     "fetches fresh data — there is no client-side cache on prices."),

    ("Searching for items",
     "Start typing in the search box. A dropdown appears showing every "
     "matching item. Keep typing to narrow it down — 'Bandos' shows all "
     "Bandos gear, 'Bandos chest' narrows to just the chestplate.\n\n"
     "    •  Click a row to select an item.\n"
     "    •  Press Enter to accept the top match.\n"
     "    •  Press Escape to clear the dropdown."),

    ("Price panel",
     "Once an item is selected you'll see:\n\n"
     "    •  High buy / Low sell — the current Grand Exchange offers.\n"
     "    •  Spread — how much margin exists between buy and sell.\n"
     "    •  Trend — direction over the last few snapshots stored locally.\n"
     "    •  Signal — BUY, HOLD, or SELL based on price + volume + momentum.\n"
     "    •  Volume — how active trading is right now vs. recent average.\n"
     "    •  Momentum — whether the price move is accelerating or stalling."),

    ("BUY / HOLD / SELL — what they mean",
     "The signal is a heuristic, not financial advice. It scores from −3 to +3:\n\n"
     "    +1 / −1   for price direction (rising / falling)\n"
     "    +1 / −1   for volume confirming or contradicting that direction\n"
     "    +1 / −1   for momentum (whether the move is speeding up or slowing)\n\n"
     "Score ≥ 2 → BUY (high), 1 → BUY (moderate), 0 → HOLD, "
     "−1 → HOLD/SELL, ≤ −2 → SELL.\n\n"
     "Extreme moves over ±25% trigger a manipulation guard and clamp the "
     "signal toward neutral."),

    ("Alerts",
     "Whenever you look up an item, the app compares the live mid-price "
     "to the last snapshot stored locally. If the price moved by 10% or "
     "more in either direction, it's logged as an alert. The Alerts tab "
     "shows the last 50."),

    ("Volatile",
     "The Volatile tab proactively scans every item on the Grand Exchange "
     "for significant price movement — no need to look anything up first. "
     "Pick a window (5m / 1h / 6h / 24h), hit Refresh, and you'll see the "
     "top 100 movers ranked by absolute % change.\n\n"
     "Items with under 1,000 units traded in the window are filtered out "
     "to suppress noise from illiquid junk. Double-click any row to open "
     "its full price panel in the Search tab."),

    ("News",
     "The News tab caches articles from the official OSRS news RSS feed. "
     "Click 'Refresh from sources' to pull the latest, then double-click "
     "any row to open the article in your browser."),

    ("Price history chart",
     "The History tab shows a chart of every snapshot the app has stored "
     "for an item, with high / low / mid lines. The more often you look "
     "up an item, the richer the chart becomes."),

    ("Data freshness",
     "Prices are pulled live every time you run a search — the OSRS Wiki "
     "API itself updates from in-game data roughly every 60 seconds, so "
     "that's the upper bound on freshness."),

    ("Disclaimer",
     "This is a personal-use tool. It is not affiliated with or endorsed "
     "by Jagex. Old School RuneScape and related marks are property of "
     "Jagex Ltd."),
]


# ── Main Application ──────────────────────────────────────────────────────────

class OSRSTrackerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.item_data = []
        self.filtered_items = []  # current dropdown contents

        self._configure_root()
        self._build_ui()

        db.initialize_db()
        self._load_mapping_async()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _configure_root(self):
        self.root.title("OSRS Price Tracker")
        self.root.geometry("1100x720")
        self.root.configure(bg=COLOR_BG)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Frames
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Panel.TFrame", background=COLOR_PANEL,
                        borderwidth=1, relief="solid")
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

        # Time-range buttons (History tab)
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
        self.volatile_tab = self._build_volatile_tab()
        self.fliprec_tab = self._build_fliprec_tab()
        self.news_tab = self._build_news_tab()
        self.history_tab = self._build_history_tab()
        self.help_tab = self._build_help_tab()

        self.notebook.add(self.search_tab, text="Search")
        self.notebook.add(self.alerts_tab, text="Alerts")
        self.notebook.add(self.volatile_tab, text="Volatile")
        self.notebook.add(self.fliprec_tab, text="FlipRec")
        self.notebook.add(self.news_tab, text="News")
        self.notebook.add(self.history_tab, text="History")
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
            self.search_entry.focus_set()

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
        self.result_frame.pack(side="top", fill="both", expand=True,
                               padx=20, pady=10)

        self.placeholder_label = ttk.Label(
            self.result_frame,
            text=("\nStart typing to search "
                  "— see live prices, trends, and a buy/hold/sell signal.\n"),
            style="Panel.TLabel", justify="center",
        )
        self.placeholder_label.pack(expand=True)

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
            self.dropdown_frame.pack(side="top", fill="x",
                                      padx=20, pady=(0, 8),
                                      before=self.result_frame)
        # auto-select top
        if self.dropdown_listbox.size() > 0:
            self.dropdown_listbox.selection_clear(0, "end")
            self.dropdown_listbox.selection_set(0)

    def _hide_dropdown(self):
        if self.dropdown_frame.winfo_ismapped():
            self.dropdown_frame.pack_forget()
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

        def fetch():
            price_data = api.get_latest_price(item_id)
            if not price_data or not price_data.get("high"):
                return None

            high = price_data["high"]
            low = price_data.get("low") or high
            last = db.get_last_recorded_price(item_id)
            alert = analyzer.check_price_alert(
                item_id, item_name, high, low, last,
                threshold=ALERT_THRESHOLD,
            )
            if alert:
                db.record_alert(**alert)
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
                "alert": alert,
            }

        def done(payload, err):
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

    def _render_result(self, item, price_data, trend, prediction, alert):
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
        header = ttk.Frame(self.result_frame, style="Panel.TFrame")
        header.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Label(header, text=name, style="Item.TLabel").pack(side="left")

        ttk.Button(header, text="View history →",
                   command=lambda: self._open_history(item["id"], name)
                   ).pack(side="right")

        # Alert banner
        if alert:
            ap = alert["pct_change"]
            ac = COLOR_GREEN if alert["alert_type"] == "spike" else COLOR_RED
            banner = tk.Frame(self.result_frame, bg=ac)
            banner.pack(fill="x", padx=20, pady=4)
            tk.Label(
                banner,
                text=(f"⚠  PRICE ALERT — moved {ap:+.1f}%  "
                      f"({fmt_gp(alert['old_price'])} → "
                      f"{fmt_gp(alert['new_price'])} gp)"),
                bg=ac, fg=COLOR_BG,
                font=("Georgia", 12, "bold"),
            ).pack(pady=6)

        # Stats grid
        grid = ttk.Frame(self.result_frame, style="Panel.TFrame")
        grid.pack(fill="x", padx=20, pady=10)

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
            reasons_frame = ttk.Frame(self.result_frame, style="Panel.TFrame")
            reasons_frame.pack(fill="x", padx=20, pady=(8, 20))
            ttk.Label(reasons_frame, text="Analysis:",
                      style="PanelHeader.TLabel"
                      ).pack(anchor="w")
            for r in prediction["reasons"]:
                ttk.Label(reasons_frame, text=f"  •  {r}",
                          style="Panel.TLabel",
                          wraplength=900, justify="left"
                          ).pack(anchor="w")

    # ── Alerts tab ────────────────────────────────────────────────────────────

    def _build_alerts_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Button(bar, text="Refresh",
                   command=self._refresh_alerts).pack(side="left")
        ttk.Label(bar, text="Recent ≥10% price moves",
                  style="Dim.TLabel").pack(side="left", padx=12)

        cols = ("item", "type", "old", "new", "pct", "time")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=20)
        tree.heading("item", text="Item")
        tree.heading("type", text="Type")
        tree.heading("old",  text="Old (gp)")
        tree.heading("new",  text="New (gp)")
        tree.heading("pct",  text="Change")
        tree.heading("time", text="When")
        tree.column("item", width=260, anchor="w")
        tree.column("type", width=80,  anchor="center")
        tree.column("old",  width=120, anchor="e")
        tree.column("new",  width=120, anchor="e")
        tree.column("pct",  width=100, anchor="e")
        tree.column("time", width=160, anchor="w")

        tree.tag_configure("spike", foreground=COLOR_GREEN)
        tree.tag_configure("drop",  foreground=COLOR_RED)

        tree.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.alerts_tree = tree
        return frame

    def _refresh_alerts(self):
        for r in self.alerts_tree.get_children():
            self.alerts_tree.delete(r)
        rows = db.get_recent_alerts(50)
        if not rows:
            self.alerts_tree.insert("", "end",
                                    values=("No alerts yet.", "", "", "", "", ""))
            return
        for item_name, atype, old, new, pct, ts in rows:
            self.alerts_tree.insert(
                "", "end",
                values=(
                    item_name,
                    atype.upper(),
                    fmt_gp(old),
                    fmt_gp(new),
                    f"{pct:+.1f}%",
                    time.strftime("%m/%d %H:%M", time.localtime(ts)),
                ),
                tags=(atype,),
            )

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
                 fg=COLOR_YELLOW, font=FONT_BODY, anchor="w"
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
                        "age of the older side. Qty is capped by your capital, the "
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

        if inactive and hide:
            self.flip_banner.set(
                f"⚠ {len(inactive)} item(s) hidden: no trade on one side in "
                f"the last hour, so their margin is likely not real. Untick "
                f"'Hide inactive' to see them.")
        elif inactive:
            self.flip_banner.set(
                f"⚠ {len(inactive)} item(s) below are inactive (no trade on "
                f"one side in the last hour); their margins are likely not real.")
        else:
            self.flip_banner.set("")

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
                fmt(r["volume"]), fmt_age(r["age"]), verdict, sig,
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
                values=("No news cached. Click 'Refresh from sources'.", "", "")
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

    # ── History tab ───────────────────────────────────────────────────────────

    def _build_history_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", padx=20, pady=(20, 10))
        ttk.Label(bar, text="Item:", style="TLabel").pack(side="left",
                                                          padx=(0, 8))

        self.history_item_var = tk.StringVar()
        self.history_combo = ttk.Combobox(
            bar, textvariable=self.history_item_var,
            state="readonly", width=40,
        )
        self.history_combo.pack(side="left", padx=(0, 8))
        self.history_combo.bind("<<ComboboxSelected>>",
                                 lambda _e: self._draw_history_chart())

        ttk.Button(bar, text="Refresh list",
                   command=self._reload_history_items
                   ).pack(side="left", padx=(0, 8))
        ttk.Button(bar, text="Refresh chart",
                   command=self._refresh_history_chart).pack(side="left")

        # Time-range selector
        self.history_range = HISTORY_DEFAULT_RANGE
        self.history_range_btns = {}
        self._history_fetched = set()   # (item_id, step) pulled this session
        range_bar = ttk.Frame(frame)
        range_bar.pack(fill="x", padx=20, pady=(0, 10))
        ttk.Label(range_bar, text="Range:", style="TLabel").pack(
            side="left", padx=(0, 8))
        for label, _secs, _step in HISTORY_RANGES:
            btn = ttk.Button(range_bar, text=label, style="Range.TButton",
                             command=lambda l=label: self._set_history_range(l))
            btn.pack(side="left", padx=(0, 4))
            self.history_range_btns[label] = btn
        self._update_range_buttons()

        chart_frame = ttk.Frame(frame, style="Panel.TFrame")
        chart_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))

        self.history_canvas = tk.Canvas(
            chart_frame, bg=COLOR_PANEL, highlightthickness=0,
        )
        self.history_canvas.pack(fill="both", expand=True, padx=10, pady=10)
        self.history_canvas.bind("<Configure>",
                                  lambda _e: self._draw_history_chart())

        self.history_id_map = {}
        self._history_placeholder("Select an item to see its price history.")
        return frame

    def _history_placeholder(self, text: str):
        c = self.history_canvas
        c.delete("all")
        w = c.winfo_width() or 800
        h = c.winfo_height() or 400
        c.create_text(w / 2, h / 2, text=text,
                      fill=COLOR_DIM, font=FONT_BODY)

    def _reload_history_items(self):
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT DISTINCT item_id, item_name "
            "FROM price_history "
            "ORDER BY item_name COLLATE NOCASE"
        ).fetchall()
        conn.close()

        self.history_id_map = {row[1]: row[0] for row in rows}
        names = list(self.history_id_map.keys())
        self.history_combo["values"] = names
        if names:
            if self.history_item_var.get() not in names:
                self.history_combo.current(0)
            self._draw_history_chart()
        else:
            self.history_item_var.set("")
            self._history_placeholder(
                "No price history yet. Search for an item first."
            )

    def _open_history(self, item_id: int, item_name: str):
        self._reload_history_items()
        if item_name in self.history_id_map:
            self.history_item_var.set(item_name)
            self._draw_history_chart()
        self.notebook.select(self.history_tab)

    def _update_range_buttons(self):
        for label, btn in self.history_range_btns.items():
            btn.configure(style="RangeSel.TButton"
                          if label == self.history_range else "Range.TButton")

    def _set_history_range(self, label: str):
        self.history_range = label
        self._update_range_buttons()
        self._draw_history_chart()

    def _refresh_history_chart(self):
        """Re-pull API data for the selected item, then redraw."""
        name = self.history_item_var.get()
        item_id = self.history_id_map.get(name)
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
            if self.history_id_map.get(self.history_item_var.get()) == item_id:
                self._draw_history_chart()

        run_async(self.root, fetch, done)

    def _draw_history_chart(self):
        name = self.history_item_var.get()
        if not name or name not in self.history_id_map:
            return
        item_id = self.history_id_map[name]

        range_label, window, step = next(
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

        times = [ts for _h, _l, ts in history]
        highs = [h for h, _l, _ts in history]
        lows  = [l for _h, l, _ts in history]
        mids  = [((h or 0) + (l or 0)) / 2 if (h and l)
                 else (h or l or 0) for h, l, _ts in history]

        c = self.history_canvas
        c.delete("all")

        W = c.winfo_width() or 800
        H = c.winfo_height() or 400
        if W < 50 or H < 50:
            return

        ML, MR, MT, MB = 70, 30, 40, 60
        plot_w = W - ML - MR
        plot_h = H - MT - MB
        if plot_w < 10 or plot_h < 10:
            return

        all_vals = [v for v in (highs + lows + mids) if v]
        if not all_vals:
            self._history_placeholder("No usable price values.")
            return
        y_min, y_max = min(all_vals), max(all_vals)
        if y_max == y_min:
            pad = max(1, y_max * 0.05)
            y_min, y_max = y_min - pad, y_max + pad

        n = len(history)
        t_min, t_max = times[0], times[-1]

        def x_pos(i):
            # Position by timestamp so mixed-resolution data stays to scale.
            if t_max == t_min:
                return ML + plot_w / 2
            return ML + ((times[i] - t_min) / (t_max - t_min)) * plot_w

        def y_pos(v):
            return MT + plot_h - ((v - y_min) / (y_max - y_min)) * plot_h

        # Title
        c.create_text(W / 2, MT / 2,
                      text=f"{name}  —  {range_label}  ({n} points)",
                      fill=COLOR_ACCENT, font=("Georgia", 12, "bold"))

        # Axes
        c.create_line(ML, MT, ML, MT + plot_h, fill=COLOR_DIM)
        c.create_line(ML, MT + plot_h, ML + plot_w, MT + plot_h, fill=COLOR_DIM)

        # Y gridlines + labels
        for i in range(5):
            v = y_min + (y_max - y_min) * (i / 4)
            y = y_pos(v)
            c.create_line(ML, y, ML + plot_w, y,
                          fill="#3a2818", dash=(2, 4))
            c.create_text(ML - 8, y, text=fmt_gp(int(v)),
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

        # Plot lines
        def plot_series(values, color, marker=False):
            pts = []
            for i, v in enumerate(values):
                if not v:
                    continue
                pts.extend([x_pos(i), y_pos(v)])
                if marker:
                    r = 2
                    x, y = x_pos(i), y_pos(v)
                    c.create_oval(x - r, y - r, x + r, y + r,
                                  fill=color, outline=color)
            if len(pts) >= 4:
                c.create_line(pts, fill=color, width=2, smooth=False)

        plot_series(highs, COLOR_GREEN, marker=True)
        plot_series(lows, COLOR_RED, marker=True)
        plot_series(mids, COLOR_BLUE)

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

    # ── Help tab ──────────────────────────────────────────────────────────────

    def _build_help_tab(self) -> ttk.Frame:
        frame = ttk.Frame(self.notebook)

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

    # ── Tab change handler ────────────────────────────────────────────────────

    def _on_tab_changed(self, _event):
        tab = self.notebook.nametowidget(self.notebook.select())
        if tab is self.alerts_tab:
            self._refresh_alerts()
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
        elif tab is self.history_tab:
            self._reload_history_items()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    OSRSTrackerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
