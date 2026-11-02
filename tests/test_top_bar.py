"""The bar above the tabs, one refresh for every tab, and the shared columns."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
from tkinter import ttk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
settings.save({"ua_contact": "test-contact", "alert_threshold": 1, "flip_show": "All verdicts"})
import threading
import analyzer, api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


NOW = time.time()
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True},
           {"id": 2, "name": "Lobster", "limit": 6000, "members": False},
           {"id": 3, "name": "Coal", "limit": 13000, "members": False}]


def prices(whip_low=1_900_000):
    now = time.time()
    return {"1": {"high": 2_000_000, "low": whip_low, "highTime": now - 20, "lowTime": now - 30},
            "2": {"high": 300, "low": 260, "highTime": now - 20, "lowTime": now - 30},
            "3": {"high": 200, "low": 150, "highTime": now - 20, "lowTime": now - 30}}


STATE = {"latest": prices(), "fetches": 0, "fail": False}
# 1h: 300 a side, so Vol 1h is the slower side. Each window's average differs.
HOUR = {"1": {"highPriceVolume": 600, "lowPriceVolume": 400, "avgHighPrice": 1_900_000, "avgLowPrice": 1_800_000},
        "2": {"highPriceVolume": 5000, "lowPriceVolume": 4000, "avgHighPrice": 290, "avgLowPrice": 250},
        "3": {"highPriceVolume": 5000, "lowPriceVolume": 4000, "avgHighPrice": 190, "avgLowPrice": 145}}
DAY = {k: {**v, "avgHighPrice": v["avgHighPrice"] // 2, "avgLowPrice": v["avgLowPrice"] // 2} for k, v in HOUR.items()}


def latest():
    STATE["fetches"] += 1
    if STATE["fail"]:
        raise RuntimeError("the Wiki is down")
    return STATE["latest"]


def bulk(step="1h"):
    return DAY if step == "24h" else HOUR


api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = latest
api.get_latest_price = lambda item_id: STATE["latest"][str(item_id)]
api.get_bulk_average = bulk
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []

root = tk.Tk()
app = gui.OSRSTrackerApp(root)


def settle(cond=lambda: False, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond():
            root.update()
            return True
        time.sleep(0.005)
    return False


def refresh():
    app._refresh_market()
    settle(lambda: not app._monitor_busy, 5)
    root.update()


def rows(tree, id_map):
    columns = list(tree["columns"])
    return {id_map[i]: dict(zip(columns, tree.item(i, "values"))) for i in tree.get_children() if i in id_map}


def flips():
    return rows(app.flip_tree, app.flip_id_map)


def watched():
    return rows(app.watch_tree, app.watch_id_map)


def movers():
    return rows(app.volatile_tree, app.volatile_id_map)


def order(tree):
    return [tree.set(i, "item") for i in tree.get_children()]


def buttons_in(widget):
    found = []
    for child in widget.winfo_children():
        if child.winfo_class() == "TButton":
            found.append(child.cget("text"))
        found += buttons_in(child)
    return found


def search_stat(label):
    return app._search_stats[label].cget("text")


def scenario():
    try:
        settle(lambda: bool(app.item_data), 10)
        settle(lambda: not app._monitor_busy, 5)
        settle(lambda: bool(flips()), 5)

        # One bar, outside the tabs
        for widget, name in ((app.refresh_btn, "Refresh now"),):
            check(str(widget.winfo_toplevel()) == str(root) and not str(widget).startswith(str(app.notebook)), f"{name} is outside the tabs")
        check(app.refresh_btn.cget("text") == "Refresh now", "the button says what it does")
        inside = buttons_in(app.notebook)
        check(not [b for b in inside if b in ("Refresh", "Check now", "Refresh now")], f"no tab has its own refresh button: {inside}")
        check("Refresh from sources" in inside and "Refresh chart" in inside, "News and the chart keep theirs")
        for var, name in ((app.flip_capital_var, "Capital"), (app.flip_members_var, "Items"), (app.flip_format_var, "Prices"), (app.alert_interval_var, "Refresh")):
            check(var is not None, f"{name} still exists")
        # The widgets using those variables are outside the notebook
        def uses(widget, var):
            found = []
            for child in widget.winfo_children():
                try:
                    if str(child.cget("textvariable")) == str(var):
                        found.append(child)
                except tk.TclError:
                    pass
                found += uses(child, var)
            return found
        for var, name in ((app.flip_capital_var, "Capital"), (app.flip_members_var, "Items"), (app.flip_format_var, "Prices"), (app.alert_interval_var, "Refresh")):
            everywhere = [w for w in uses(root, var) if w.winfo_class() in ("TEntry", "TCombobox")]
            check(len(everywhere) == 1 and not str(everywhere[0]).startswith(str(app.notebook)), f"one {name} control, above the tabs")

        # Every table starts the same
        shared = ["item", "buy", "sell", "qty", "roi", "cost", "profit", "volume", "age"]
        headers = None
        for tree, name in ((app.flip_tree, "FlipRec"), (app.watch_tree, "Watchlist"), (app.volatile_tree, "Volatile")):
            columns = list(tree["columns"])
            check(columns[:9] == shared, f"{name} starts with the shared columns: {columns[:9]}")
            names = [tree.heading(c, "text").rstrip(" ▲▼") for c in shared]
            headers = headers or names
            check(names == headers, f"{name} uses the same headers: {names}")
        check("net" not in app.watch_tree["columns"], "Net / item is only on FlipRec")
        check({"net", "tax", "limit", "verdict", "signal"} <= set(app.flip_tree["columns"]), "FlipRec keeps its own columns")
        check({"target_below", "target_above", "vol_12h", "vol_24h", "verdict", "added"} <= set(app.watch_tree["columns"]), "Watchlist keeps its own columns")
        check({"pct", "avg", "window_volume"} <= set(app.volatile_tree["columns"]), "Volatile has its own columns")
        check("current" not in app.volatile_tree["columns"], "Buy and Sell replace Current")

        # One fetch updates every tab, with the same numbers
        db.watchlist_add(1, "Abyssal whip")
        app._lookup_item(MAPPING[0])
        settle(lambda: app._search_shown is not None, 3)
        before = STATE["fetches"]
        STATE["latest"] = prices(whip_low=1_850_000)
        refresh()
        check(STATE["fetches"] == before + 1, f"one price request per refresh: {STATE['fetches'] - before}")
        settle(lambda: bool(movers()), 3)
        tables = {"FlipRec": flips()[1], "Watchlist": watched()[1], "Volatile": movers()[1]}
        for name, row in tables.items():
            for column in ("buy", "sell"):
                check(row[column] == tables["FlipRec"][column], f"{name} {column} matches FlipRec: {row[column]}")
            check(row["buy"] == gui.fmt_gp(1_850_000), f"{name} has the new price: {row['buy']}")
        for name, row in tables.items():
            for column in ("qty", "cost", "profit", "roi", "volume"):
                check(row[column] == tables["FlipRec"][column], f"{name} {column} matches FlipRec: {row[column]} vs {tables['FlipRec'][column]}")
        check(watched()[1]["qty"] != "" and watched()[1]["cost"] != "" and watched()[1]["profit"] != "", "the Watchlist now shows Qty, Upfront and Est. profit")

        # Search: the same values and names, without a new search
        check(search_stat("Buy") == "1,850,000 gp" and search_stat("Sell") == "2,000,000 gp", f"Search updates with the market: {search_stat('Buy')}")
        labels = list(app._search_stats)
        check(labels[:8] == ["Buy", "Sell", "Qty", "ROI", "Upfront", "Est. profit", "Vol 1h", "Last trade"], f"Search uses the shared order: {labels[:8]}")
        check("High buy" not in labels and "Low sell" not in labels, "the old names are gone")
        exact = lambda column: tables["FlipRec"][column]
        check(search_stat("Qty") == "70" and search_stat("Vol 1h").startswith("400"), f"Search Qty and Vol 1h: {search_stat('Qty')}, {search_stat('Vol 1h')}")
        check(search_stat("Upfront") == f"{1_850_000 * 70:,}", f"Search Upfront: {search_stat('Upfront')}")
        check(search_stat("ROI") == exact("roi"), f"Search ROI matches: {search_stat('ROI')}")
        widget = app._search_stats["Buy"]
        STATE["latest"] = prices(whip_low=1_800_000)
        refresh()
        check(app._search_stats["Buy"] is widget and search_stat("Buy") == "1,800,000 gp", "it updates the same widgets rather than rebuilding them")
        app.search_format_var.set(gui.PRICE_FORMATS[0])
        app._on_search_format_changed()
        root.update()
        check(search_stat("Buy") == "1.80M gp", f"Search keeps its own Prices setting: {search_stat('Buy')}")
        check(flips()[1]["buy"] == gui.fmt_gp(1_800_000), "and the tables aren't affected")
        refresh()
        check(search_stat("Buy") == "1.80M gp", "and it survives a refresh")
        app.search_format_var.set(gui.PRICE_FORMATS[1])
        app._on_search_format_changed()
        root.update()

        # Your sort and place stay when prices update
        app._sort_fliprec("name", False)
        check(order(app.flip_tree)[0] == "Abyssal whip", "FlipRec sorted by name")
        kid = next(i for i, item in app.flip_id_map.items() if item == 3)
        app.flip_tree.selection_set(kid)
        app._sort_volatile("name", True)
        vol_first = order(app.volatile_tree)[0]
        check(vol_first == "Lobster", f"Volatile sorted by name: {vol_first}")
        kid = next(i for i, item in app.volatile_id_map.items() if item == 2)
        app.volatile_tree.selection_set(kid)
        refresh()
        check(app.flip_sort == ("name", False) and order(app.flip_tree)[0] == "Abyssal whip", "FlipRec keeps its sort through a refresh")
        check("▲" in app.flip_tree.heading("item", "text"), "and its arrow")
        check([app.flip_id_map[i] for i in app.flip_tree.selection()] == [3], "and your selected row")
        check(app.volatile_sort == ("name", True) and order(app.volatile_tree)[0] == "Lobster", "Volatile keeps its sort through a refresh")
        check("▼" in app.volatile_tree.heading("item", "text"), "and its arrow")
        check([app.volatile_id_map[i] for i in app.volatile_tree.selection()] == [2], "and your selected row")
        app.flip_show_var.set("All verdicts")
        app._apply_flip_filters()
        check(app.flip_sort == ("name", False), "changing a setting keeps the sort too")
        app.flip_capital_var.set("")
        app._apply_flip_filters()
        check(app.flip_sort == ("name", False), "so does clicking out of Capital")

        # The timer really does update FlipRec and Volatile
        gui.ALERT_INTERVALS["1 min"], real = 1, gui.ALERT_INTERVALS["1 min"]
        STATE["latest"] = prices(whip_low=1_700_000)
        app._refresh_market()
        settle(lambda: flips()[1]["buy"] == gui.fmt_gp(1_700_000), 3)
        STATE["latest"] = prices(whip_low=1_600_000)
        settle(lambda: flips()[1]["buy"] == gui.fmt_gp(1_600_000), 4)
        check(flips()[1]["buy"] == gui.fmt_gp(1_600_000), "FlipRec updates by itself on the timer")
        settle(lambda: movers()[1]["buy"] == gui.fmt_gp(1_600_000), 2)
        check(movers()[1]["buy"] == gui.fmt_gp(1_600_000), "so does Volatile")
        gui.ALERT_INTERVALS["1 min"] = real
        refresh()

        # Refresh now during a fetch isn't lost
        STATE["latest"] = prices(whip_low=1_500_000)
        app._monitor_busy = True
        app._refresh_market()
        check(app._refresh_again, "a refresh during a fetch waits")
        app._monitor_busy = False
        app._refresh_again = False
        refresh()

        # Settings apply everywhere
        app.flip_capital_var.set("10m")
        app._apply_flip_filters()
        settle()
        check(movers()[1]["qty"] == "6" and watched()[1]["qty"] == "6" and flips()[1]["qty"] == "6", f"Capital sets Qty on every table: {movers()[1]['qty']}")
        check(search_stat("Qty") == "6", f"and on Search: {search_stat('Qty')}")
        app.flip_capital_var.set("")
        app._apply_flip_filters()
        settle()

        app.flip_format_var.set(gui.PRICE_FORMATS[1])
        app._on_price_format_changed()
        check(movers()[1]["buy"] == "1,500,000" and movers()[1]["avg"] == "1,850,000", f"Volatile follows Prices: {movers()[1]['buy']}, {movers()[1]['avg']}")
        check(movers()[1]["window_volume"] == "1,000", f"and Window volume adds both sides: {movers()[1]['window_volume']}")
        check(movers()[1]["volume"].startswith("400"), f"while Vol 1h is the slower side: {movers()[1]['volume']}")
        app.flip_format_var.set(gui.PRICE_FORMATS[0])
        app._on_price_format_changed()

        app.flip_members_var.set("F2P only")
        app._apply_flip_filters()
        check(set(movers()) == {2, 3} and set(flips()) == {2, 3}, f"Items applies to Volatile as well as FlipRec: {set(movers())}")
        check(1 in watched(), "but not to the Watchlist")
        check(search_stat("Buy").startswith("1,500,000"), "or an open Search result")
        app.flip_members_var.set("All items")
        app._apply_flip_filters()
        check(1 in movers(), "and it comes back")
        check(settings.load().get("flip_membership") == "All items", "Items is saved")

        # Volatile's Window: no new price fetch, and it survives a refresh
        before = STATE["fetches"]
        was = movers()[1]["pct"]
        app.volatile_window_var.set("24h")
        app._refresh_volatile()
        settle(lambda: movers()[1]["pct"] != was, 3)
        check(movers()[1]["pct"] != was and STATE["fetches"] == before, f"a new Window uses the same prices: {was} then {movers()[1]['pct']}")
        check("24h" in app.volatile_status.get(), app.volatile_status.get())
        pct = movers()[1]["pct"]
        refresh()
        check(movers()[1]["pct"] == pct and "24h" in app.volatile_status.get(), "a refresh stays on your Window")
        app.volatile_window_var.set("1h")
        app._refresh_volatile()
        settle(lambda: movers()[1]["pct"] != pct, 3)

        # A failed fetch says so once, and keeps the prices
        good = flips()[1]["buy"]
        STATE["fail"] = True
        refresh()
        status = app.market_status.get()
        check("Check failed" in status and "Prices shown are from" in status, f"a failed fetch says so: {status}")
        check(flips()[1]["buy"] == good and movers()[1]["buy"] == good, "the tables keep their last prices")
        STATE["fail"] = False
        refresh()
        check(app.market_status.get().startswith("Prices updated"), app.market_status.get())

        # A bad Capital stays on show until it's fixed, and blocks nothing else
        app.flip_capital_var.set("lots")
        app._apply_flip_filters()
        check(app.market_status.get() == gui.CAPITAL_ERROR, "a bad Capital says so")
        STATE["latest"] = prices(whip_low=1_400_000)
        refresh()
        check(app.market_status.get() == gui.CAPITAL_ERROR, "and a refresh doesn't hide that")
        check(movers()[1]["buy"] == gui.fmt_gp(1_400_000) and watched()[1]["buy"] == gui.fmt_gp(1_400_000), "while other tabs still update")
        app.flip_capital_var.set("")
        app._apply_flip_filters()
        check(app.market_status.get().startswith("Prices updated"), f"fixing it brings the status back: {app.market_status.get()}")
        check(flips()[1]["buy"] == gui.fmt_gp(1_400_000), "and FlipRec catches up")

        # Rate and narrow window
        before = app.alert_interval_var.get()
        app.alert_interval_var.set("15 min")
        app._on_alert_interval_changed()
        settle(lambda: not app._monitor_busy, 3)
        check("next check in 15 min" in app.market_status.get() and settings.load()["alert_interval"] == "15 min", "the rate is saved and shown")
        app.alert_interval_var.set(before)
        app._on_alert_interval_changed()
        settle(lambda: not app._monitor_busy, 3)

        actions = app.refresh_btn.master
        root.geometry("1380x860")
        settle()
        check(actions.grid_info()["row"] == 0, "the bar is one row at full size")
        root.geometry("900x560")
        settle()
        check(actions.grid_info()["row"] == 1, "and two rows when narrow")
        check(app.refresh_btn.winfo_ismapped() and app.refresh_btn.winfo_rootx() + app.refresh_btn.winfo_width() <= root.winfo_rootx() + root.winfo_width(), "Refresh now stays on screen")
        root.geometry("1380x860")
        settle()

        # Search's own history: refreshed with the market, only while you can see it
        series_calls = []
        FACTS = {"pct": 1.0, "notes": [], "fail": False, "gate": None}

        def series(item_id, timestep="1h"):
            series_calls.append((item_id, timestep))
            if FACTS["fail"]:
                raise RuntimeError("no history")
            return []

        def market(_five, _rows, _hourly, live=None):
            gate = FACTS["gate"]
            if gate and live and live[0] == 2_000_000:
                gate.wait(5)
            direction = "up" if FACTS["pct"] > 0 else "down"
            return ({"direction": direction, "pct_change": FACTS["pct"], "hours": 30},
                    {"volume": "high", "recent_volume": 500, "usual_volume": 100,
                     "momentum": "rising" if FACTS["pct"] > 0 else "falling", "notes": list(FACTS["notes"])})

        api.get_timeseries = series
        gui.analyzer.recent_market = market
        app._volume.recent_prices = lambda item_id: []   # history ready, no note about it
        app.notebook.select(app.search_tab)
        settle()
        app._lookup_item(MAPPING[0])
        settle(lambda: app._search_shown is not None and not app._search_facts_busy, 3)
        settle()
        move = app._search_stats["Price move"]
        check(move.cget("text").endswith("over 30 hours"), f"Search starts with the lookup's facts: {move.cget('text')}")
        frame, notes = app._search_notes
        check(not frame.winfo_manager(), "Worth knowing hides with nothing to say")

        series_calls.clear()
        FACTS.update(pct=-4.0, notes=["Trading is quiet right now"])
        refresh()
        settle(lambda: not app._search_facts_busy, 3)
        root.update()
        check(len(series_calls) == 2, f"a refresh downloads the item's history twice: {series_calls}")
        check(app._search_stats["Price move"] is move and "-4.0%" in move.cget("text"), f"Price move updates in place: {move.cget('text')}")
        check(search_stat("Momentum") == "Falling lately", f"Momentum updates: {search_stat('Momentum')}")
        check(search_stat("Trading").startswith("High  (500 both sides"), f"Trading updates: {search_stat('Trading')}")
        check(frame.winfo_manager() and "quiet" in notes.cget("text") and app._search_notes[1] is notes, "Worth knowing appears, reusing its widgets")
        FACTS.update(notes=[])
        refresh()
        settle(lambda: not app._search_facts_busy, 3)
        root.update()
        check(not frame.winfo_manager(), "and goes away again")

        # A failed download keeps what's showing and doesn't touch the status
        FACTS.update(fail=True, pct=9.0)
        was = move.cget("text")
        refresh()
        settle(lambda: not app._search_facts_busy, 3)
        root.update()
        check(move.cget("text") == was and app.market_status.get().startswith("Prices updated"), "a failed history download keeps the old facts")
        check(not app._search_facts_busy, "and doesn't get stuck")
        FACTS.update(fail=False, pct=1.0)

        # Not on Search: no downloads, then one when you come back
        app.notebook.select(app.fliprec_tab)
        settle()
        series_calls.clear()
        refresh()
        settle(lambda: not app._search_facts_busy, 2)
        check(series_calls == [], f"no history downloads while Search isn't showing: {series_calls}")
        app._search_facts_at = 0
        app.notebook.select(app.search_tab)
        settle(lambda: len(series_calls) == 2 and not app._search_facts_busy, 3)
        check(len(series_calls) == 2, f"coming back catches up: {series_calls}")
        series_calls.clear()
        app.notebook.select(app.fliprec_tab)
        settle()
        app.notebook.select(app.search_tab)
        settle()
        check(series_calls == [], "but not if it just did")

        # While one download runs, another isn't started. And if you search for
        # something else meanwhile, the late answer is dropped.
        settle(lambda: not app._search_facts_busy, 3)
        gate = threading.Event()
        FACTS.update(gate=gate, pct=7.0)
        series_calls.clear()
        app._refresh_search_facts()
        settle(lambda: len(series_calls) == 2, 3)
        app._refresh_search_facts()
        settle()
        check(len(series_calls) == 2, f"one download at a time: {series_calls}")
        FACTS["pct"] = 3.0
        app._lookup_item(MAPPING[1])
        settle(lambda: app._search_shown is not None and app._search_shown["item"]["id"] == 2, 3)
        FACTS["pct"] = 7.0
        gate.set()
        settle(lambda: not app._search_facts_busy, 3)
        settle()
        check("+3.0%" in app._search_stats["Price move"].cget("text"), f"a late answer can't replace the newer search: {app._search_stats['Price move'].cget('text')}")
        FACTS["gate"] = None
        app.notebook.select(app.fliprec_tab)
        settle()

        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        for phrase in ("above the tabs", "Refresh now", "same columns"):
            check(phrase in helptext, f"Help covers: {phrase}")
        for stale in ("High buy", "Check now", "on the Alerts tab"):
            check(stale not in helptext, f"Help no longer says: {stale}")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL TOP BAR CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
