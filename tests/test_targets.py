"""Watchlist target prices: when they're met, what shows, and when the app flashes."""
import os, sqlite3, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
from tkinter import ttk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
settings.save({"ua_contact": "test-contact"})
import aitools, analyzer, api, gui, scraper, volume

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# What meets a target
NOW = 1_800_000_000
fresh = {"high": 21_000, "low": 19_000, "highTime": NOW - 30, "lowTime": NOW - 30}
hits = analyzer.target_hits
check(hits(fresh, 19_000, None, NOW) == (True, False), "Buy exactly at the target meets it")
check(hits(fresh, 18_999, None, NOW) == (False, False), "Buy above the target doesn't")
check(hits(fresh, None, 21_000, NOW) == (False, True), "Sell exactly at the target meets it")
check(hits(fresh, None, 21_001, NOW) == (False, False), "Sell below the target doesn't")
check(hits(fresh, 19_500, 20_500, NOW) == (True, True), "both sides can be met")
check(hits(fresh, None, None, NOW) == (False, False), "no targets, nothing met")
check(hits(None, 19_000, 21_000, NOW) == (False, False), "no prices, nothing met")
check(hits({"high": 21_000, "highTime": NOW}, 19_000, 21_000, NOW) == (False, True), "a side with no price can't meet its target")
old = {"high": 21_000, "low": 19_000, "highTime": NOW - 30, "lowTime": NOW - analyzer.FLIP_MAX_AGE - 1}
check(hits(old, 19_000, 21_000, NOW) == (False, True), "a side that stopped trading can't meet its target")

# What can be typed
parse = gui.parse_targets
check(parse("19,000", "") == (19_000, None), "plain price, blank other side")
check(parse("19k", "1.5m") == (19_000, 1_500_000), "short forms")
check(parse("", "  ") == (None, None), "both blank")
check(parse("0", "") == (None, None), "zero is no target")
for below, above, why in (("abc", "", "text"), ("inf", "", "infinity"), ("nan", "", "nan"), ("-5", "", "negative"),
                          ("3b", "", "more than an item can cost"), ("1e30", "", "absurdly big"),
                          ("20k", "20k", "equal targets"), ("21k", "20k", "crossed targets")):
    try:
        parse(below, above)
        check(False, f"refuses {why}")
    except ValueError as exc:
        check(bool(str(exc)), f"refuses {why}: {exc}")

# An existing watchlist keeps working after the update
conn = sqlite3.connect(db.DB_NAME)
conn.executescript("CREATE TABLE watchlist (item_id INTEGER PRIMARY KEY, item_name TEXT NOT NULL, added_ts INTEGER NOT NULL);"
                   "INSERT INTO watchlist VALUES (2, 'Old item', 1700000000);")
conn.commit()
conn.close()
db.initialize_db()
db.initialize_db()
check(db.watchlist_items() == [(2, "Old item", 1700000000)], "old watchlist survives the update, and running it twice is safe")
check(db.watchlist_targets() == {}, "old items start with no targets")
db.watchlist_remove(2)

MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True},
           {"id": 2, "name": "Lobster", "limit": 6000, "members": False}]
prices = {"high": 21_000, "low": 20_000}


def latest():
    t = time.time()
    return {"1": {**prices, "highTime": t - 20, "lowTime": t - 30},
            "2": {"high": 300, "low": 200, "highTime": t - 20, "lowTime": t - 30}}


BULK = {"1": {"highPriceVolume": 500, "lowPriceVolume": 500, "avgHighPrice": 21_000, "avgLowPrice": 20_000},
        "2": {"highPriceVolume": 500, "lowPriceVolume": 500, "avgHighPrice": 300, "avgLowPrice": 200}}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = latest
api.get_bulk_average = lambda step="1h": BULK
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []
volume.BACKFILL_PAUSE = 0
flashes = []
gui.flash_window = lambda root: flashes.append(time.time())

root = tk.Tk()
app = gui.OSRSTrackerApp(root)


def settle(cond, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond():
            root.update()
            return True
        time.sleep(0.005)
    return False


def check_market():
    """One real market check, as the timer runs it."""
    app._monitor_tick()
    settle(lambda: not app._monitor_busy)


def rows():
    tree = app.watch_tree
    cols = tree["columns"]
    return {tree.item(i, "values")[0]: dict(zip(cols, tree.item(i, "values"))) for i in tree.get_children()}


def tags(name):
    tree = app.watch_tree
    return next(tree.item(i, "tags") for i in tree.get_children() if tree.item(i, "values")[0] == name)


def tab():
    return app.notebook.tab(app.watchlist_tab, "text")


def select(name):
    tree = app.watch_tree
    tree.selection_set([i for i in tree.get_children() if tree.item(i, "values")[0] == name])
    root.update()


def entries(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Entry):
            found.append(child)
        found += entries(child)
    return found


def button(widget, text):
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button) and child.cget("text") == text:
            return child
        hit = button(child, text)
        if hit:
            return hit


def open_dialog():
    select("Abyssal whip")
    app.watch_target_btn.invoke()
    root.update()
    return next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))


def set_in_dialog(below, above):
    """Type into the real dialog and press Save. Returns the dialog."""
    dlg = open_dialog()
    below_box, above_box = entries(dlg)
    below_box.delete(0, "end")
    below_box.insert(0, below)
    above_box.delete(0, "end")
    above_box.insert(0, above)
    button(dlg, "Save").invoke()
    root.update()
    return dlg


def scenario():
    try:
        settle(lambda: app.item_data)
        db.watchlist_add(1, "Abyssal whip")
        db.watchlist_add(2, "Lobster")
        check_market()
        app.notebook.select(app.watchlist_tab)
        settle(lambda: False, 0.1)
        flashes.clear()

        check(tab() == "Watchlist", "no count with no targets")
        root.geometry("1250x860")
        settle(lambda: False, 0.3)
        tree = app.watch_tree
        total = sum(tree.column(c, "width") for c in tree["columns"])
        check(total <= tree.winfo_width(), f"every column fits at the default width: {total} vs {tree.winfo_width()}")
        check(rows()["Abyssal whip"]["target_below"] == "" and rows()["Abyssal whip"]["target_above"] == "", "target cells start blank")

        # The button needs a row, and the empty message isn't one
        app.watch_tree.selection_set([])
        root.update()
        check(str(app.watch_target_btn["state"]) == "disabled", "Set target is off with nothing selected")
        select("Abyssal whip")
        check(str(app.watch_target_btn["state"]) == "normal", "Set target is on with a row selected")

        # A bad entry stays in the dialog and saves nothing
        dlg = set_in_dialog("abc", "")
        check(dlg.winfo_exists() and db.watchlist_targets() == {}, "a bad price saves nothing and keeps the dialog open")
        button(dlg, "Cancel").invoke()
        root.update()
        check(not dlg.winfo_exists() and db.watchlist_targets() == {}, "Cancel saves nothing")

        # Set a target the market hasn't reached
        dlg = set_in_dialog("19k", "22,000")
        check(not dlg.winfo_exists(), "Save closes the dialog")
        saved = db.watchlist_targets()[1]
        check(saved["target_below"] == 19_000 and saved["target_above"] == 22_000, f"targets saved: {saved}")
        row = rows()["Abyssal whip"]
        check("19.0K" == row["target_below"] and "22.0K" == row["target_above"], f"targets show in the Prices format: {row}")
        check(tab() == "Watchlist" and "target" not in tags("Abyssal whip"), "not met yet: no count, no gold row")
        check_market()
        check(not flashes, "not met yet: no flash")

        # The price falls to the target
        prices["low"] = 19_000
        check_market()
        row = rows()["Abyssal whip"]
        check(row["target_below"] == "✓ 19.0K" and row["target_above"] == "22.0K", f"the met side gets a tick: {row}")
        check("target" in tags("Abyssal whip"), "the row turns gold")
        check(tab() == "Watchlist (1)", f"the tab counts it: {tab()}")
        check(len(flashes) == 1, f"the taskbar flashes once: {len(flashes)}")
        check_market()
        check(len(flashes) == 1 and tab() == "Watchlist (1)", "staying met doesn't flash again")

        # Bounces straight back: the mark clears, no second flash
        prices["low"] = 20_000
        check_market()
        check(tab() == "Watchlist" and rows()["Abyssal whip"]["target_below"] == "19.0K", "moving away clears the tick and count")
        prices["low"] = 19_000
        check_market()
        check(tab() == "Watchlist (1)" and len(flashes) == 1, "coming back within the hour shows, without another flash")

        # Comes back after an hour away: that's news
        prices["low"] = 20_000
        check_market()
        conn = db.get_connection()
        conn.execute("UPDATE watchlist SET below_seen = ?", (int(time.time()) - gui.ALERT_COOLDOWN - 1,))
        conn.commit()
        conn.close()
        prices["low"] = 18_000
        check_market()
        check(len(flashes) == 2, f"coming back after an hour flashes again: {len(flashes)}")

        # A new target that's already met doesn't flash, but shows
        flashes.clear()
        dlg = set_in_dialog("25k", "")
        check(tab() == "Watchlist (1)" and rows()["Abyssal whip"]["target_below"].startswith("✓"), "a target already met shows right away")
        check_market()
        check(not flashes, "and doesn't flash for something you just set")

        # Prices follow the FlipRec setting, and sorting works
        app.flip_format_var.set(gui.PRICE_FORMATS[1])
        app._on_price_format_changed()
        check(rows()["Abyssal whip"]["target_below"] == "✓ 25,000", "targets follow the Prices setting")
        app._sort_watchlist("target_below", True)
        check(app.watch_tree.item(app.watch_tree.get_children()[0], "values")[0] == "Abyssal whip", "the column sorts")

        # The AI can read them, and only targets that are set
        tracker = aitools.MarketTools(lambda: MAPPING).run("get_my_tracker", {})
        by_name = {e["name"]: e for e in tracker["watchlist"]}
        whip = by_name["Abyssal whip"]
        check(whip.get("target_buy_at_or_below") == 25_000 and whip.get("target_met") is True, f"Ask AI sees the target: {whip.get('target_buy_at_or_below')}")
        check("target_met" not in by_name["Lobster"], "Ask AI sees no target where none is set")

        # Old prices can't keep a target met
        market = app._market
        app._market = (market[0], market[1], time.time() - app._alert_stale_after() - 1, market[3])
        app._render_watchlist()
        check(tab() == "Watchlist", "prices too old to trust don't count")
        flashes.clear()
        app._notify_targets()
        check(not flashes, "and don't flash")
        app._market = market
        app._render_watchlist()

        # Set again reopens with the saved values, and Clear removes them
        dlg = open_dialog()
        check(entries(dlg)[0].get() == "25,000" and entries(dlg)[1].get() == "", "the dialog shows the saved targets")
        button(dlg, "Clear targets").invoke()
        root.update()
        check(db.watchlist_targets() == {} and tab() == "Watchlist", "Clear targets removes them")

        # Removing an item takes its target with it
        dlg = set_in_dialog("25k", "")
        db.watchlist_remove(1)
        app._render_watchlist()
        check(tab() == "Watchlist", "removing the item clears the count")
        db.watchlist_add(1, "Abyssal whip")
        check(db.watchlist_targets() == {}, "adding it back starts with no target")

        # An item with no price yet can still show its target
        db.watchlist_add(9, "Unpriced")
        db.watchlist_set_targets(9, 5, None)
        app._render_watchlist()
        row = rows()["Unpriced"]
        check(row["buy"] == "N/A" and row["target_below"] == "5", f"unpriced item keeps its columns in line: {row}")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL TARGET CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
