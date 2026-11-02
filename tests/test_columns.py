"""Moving columns by dragging their headers: per tab, saved, and separate from sorting."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
# Saved orders as they might be found: good, damaged by hand, and from an older
# version that didn't have every column.
settings.save({"ua_contact": "test-contact", "alert_threshold": 1, "column_order": {
    "volatile": ["item", "pct", "buy"],
    "alerts": ["age", "item", "bogus", "age", 7, ["x"]],
    "watchlist": "nope",
}})
import api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


NOW = time.time()
MAPPING = [{"id": i, "name": f"Item {i}", "limit": 1000, "members": True} for i in range(1, 6)]
LATEST = {str(i): {"high": 1100 + i, "low": 1000, "highTime": NOW - 20, "lowTime": NOW - 30} for i in range(1, 6)}
HOUR = {k: {"highPriceVolume": 5000, "lowPriceVolume": 5000, "avgHighPrice": v["high"], "avgLowPrice": v["low"]} for k, v in LATEST.items()}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_bulk_average = lambda step="1h": HOUR
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []

menus = []
tk.Menu.tk_popup = lambda self, x, y, entry="": menus.append(self)

# Cleaning a saved order
clean = gui.clean_column_order
check(clean(None, ["item", "a"]) is None and clean("x", ["item", "a"]) is None, "no saved order, or a damaged one, means the default")
check(clean(["b", "item", "zzz", "b", 3], ["item", "a", "b"]) == ["item", "b", "a"], "Item first, no repeats, no unknowns, new columns at the end")

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


def order(tree):
    return app._display_order(tree)


def header_x(tree, column):
    """Where that column's header is on screen."""
    columns = order(tree)
    widths = {c: tree.column(c, "width") for c in columns}
    offset = tree.xview()[0] * sum(widths.values())
    left = sum(widths[c] for c in columns[:columns.index(column)])
    return int(left + widths[column] / 2 - offset)


def press(tree, x, y=12):
    tree.event_generate("<ButtonPress-1>", x=x, y=y)
    root.update()


def move(tree, x, y=12):
    tree.event_generate("<Motion>", x=x, y=y, state=0x100)
    root.update()


def release(tree, x, y=12):
    tree.event_generate("<ButtonRelease-1>", x=x, y=y)
    settle(seconds=0.05)


def drag(tree, column, to_x):
    start = header_x(tree, column)
    press(tree, start)
    move(tree, start + (20 if to_x > start else -20))
    move(tree, to_x)
    release(tree, to_x)


def saved():
    return settings.load().get("column_order", {})


def scenario():
    try:
        settle(lambda: bool(app.item_data), 10)
        settle(lambda: not app._monitor_busy and bool(app.flip_id_map), 5)
        flip, watch, alerts, volatile = app.flip_tree, app.watch_tree, app.alerts_tree, app.volatile_tree

        # What was saved is back
        check(order(volatile)[:3] == ["item", "pct", "buy"] and len(order(volatile)) == len(volatile["columns"]), f"a saved order loads, with missing columns added: {order(volatile)[:4]}")
        check(order(alerts)[:2] == ["item", "age"] and "bogus" not in order(alerts) and len(order(alerts)) == len(alerts["columns"]), f"a damaged order is cleaned up: {order(alerts)[:3]}")
        check(order(watch) == list(watch["columns"]), "something that isn't an order is ignored")
        check(order(flip) == list(flip["columns"]), "no saved order is the default")

        app.notebook.select(app.fliprec_tab)
        settle()
        default = list(flip["columns"])

        # A drag moves the column, and doesn't sort
        was_sort = app.flip_sort
        kid = next(iter(app.flip_id_map))
        values = {c: flip.set(kid, c) for c in default}
        marker_seen = []
        start = header_x(flip, "cost")
        press(flip, start)
        move(flip, start - 20)
        target = header_x(flip, "buy") - 30
        move(flip, target)
        marker = app._column_markers[flip]
        check(marker.winfo_ismapped(), "a line shows where it will land")
        check(str(flip.cget("cursor")) != "", "and the pointer changes")
        marker_x = marker.winfo_x()
        check(abs(marker_x - flip.column("item", "width")) <= 4, f"the line sits in the gap: {marker_x} vs {flip.column('item', 'width')}")
        release(flip, target)
        check(order(flip)[:3] == ["item", "cost", "buy"], f"the column moved: {order(flip)[:4]}")
        check(app.flip_sort == was_sort and not any("▲" in flip.heading(c, "text") or "▼" in flip.heading(c, "text") for c in default), f"a drag doesn't sort: {app.flip_sort}")
        check(not marker.winfo_ismapped() and str(flip.cget("cursor")) == "", "the line and pointer go back")
        check(flip.bbox(kid, "cost")[0] < flip.bbox(kid, "buy")[0], "the table really shows it there")
        check({c: flip.set(kid, c) for c in default} == values, "every value stays in its own column")
        check(saved().get("fliprec") == order(flip), f"the order is saved: {saved().get('fliprec')}")
        check(order(watch) == list(watch["columns"]) and "watchlist" not in app._column_orders, "other tabs aren't affected")

        # A click still sorts, on a moved column too
        x = header_x(flip, "cost")
        press(flip, x)
        release(flip, x)
        check(app.flip_sort == ("cost", True) and order(flip)[1] == "cost", f"a click sorts: {app.flip_sort}")
        press(flip, x)
        move(flip, x + gui.COLUMN_DRAG_START - 2)
        release(flip, x + gui.COLUMN_DRAG_START - 2)
        check(app.flip_sort == ("cost", False) and order(flip)[1] == "cost", f"a small wobble is still a click: {app.flip_sort}")
        app.flip_sort = (None, None)
        app._update_flip_arrows()

        # Two quick clicks on a header sort twice. They don't open Search.
        kid = next(iter(app.flip_id_map))
        flip.selection_set(kid)
        x = header_x(flip, "roi")
        press(flip, x)
        release(flip, x)
        press(flip, x)     # within a double-click's time, so Tk says it's one
        release(flip, x)
        check(app.flip_sort == ("roi", False), f"a double-click on a header sorts each way: {app.flip_sort}")
        check(app.notebook.select() == str(app.fliprec_tab), "and doesn't jump to Search")
        press(flip, x)
        move(flip, x + 40)
        release(flip, x + 40)
        app.flip_sort = (None, None)
        app._update_flip_arrows()

        # A double-click on a row still opens it
        kid = next(iter(app.flip_id_map))
        x, y, w, h = flip.bbox(kid, "item")
        press(flip, x + 5, y + h // 2)
        release(flip, x + 5, y + h // 2)
        press(flip, x + 5, y + h // 2)
        release(flip, x + 5, y + h // 2)
        settle(lambda: app._search_shown is not None, 3)
        check(app.notebook.select() == str(app.search_tab), "a double-click on a row opens it in Search")
        app.notebook.select(app.fliprec_tab)
        settle()

        # Item stays put, and nothing lands in front of it
        before = order(flip)
        x = header_x(flip, "item")
        press(flip, x)
        move(flip, x + 200)
        release(flip, x + 200)
        check(order(flip) == before, "Item can't be dragged")
        drag(flip, "sell", 2)
        check(order(flip)[0] == "item" and order(flip)[1] == "sell", f"nothing lands in front of Item: {order(flip)[:3]}")

        # To the end
        drag(flip, "buy", flip.winfo_width() - 40)
        check(order(flip)[-1] == "buy" or flip.xview()[1] < 1, f"a column can go last: {order(flip)[-2:]}")
        check(order(flip)[-1] == "buy", f"last: {order(flip)[-2:]}")
        flip.xview_moveto(0)
        settle()

        # Dropped where it was: nothing changes
        before, writes = order(flip), saved()
        x = header_x(flip, "qty")
        press(flip, x)
        move(flip, x + 12)
        release(flip, x + 12)
        check(order(flip) == before and app.flip_sort == (None, None), "dropped back in its own place: no move and no sort")

        # A separator still resizes
        qty = header_x(flip, "qty") + flip.column("qty", "width") // 2
        edge = next((x for x in range(qty - 6, qty + 6) if flip.identify_region(x, 12) == "separator"), None)
        check(edge is not None, "found the separator")
        before, width = order(flip), flip.column("qty", "width")
        press(flip, edge)
        move(flip, edge + 30)
        release(flip, edge + 30)
        check(order(flip) == before and flip.column("qty", "width") != width and app.flip_sort == (None, None), f"dragging a separator resizes rather than moves: {width} to {flip.column('qty', 'width')}")

        # Rows still behave
        kid = next(iter(app.flip_id_map))
        x, y, w, h = flip.bbox(kid, "item")
        press(flip, x + 5, y + h // 2)
        move(flip, x + 60, y + h // 2)
        release(flip, x + 60, y + h // 2)
        check(order(flip) == before and kid in flip.selection(), "dragging across rows moves nothing and still selects")

        # New prices keep the order
        app._refresh_market()
        settle(lambda: not app._monitor_busy, 5)
        check(order(flip) == before, "a refresh keeps the order")
        kid = next(iter(app.flip_id_map))
        check(flip.set(kid, "cost") and flip.set(kid, "buy") and flip.bbox(kid, order(flip)[1])[0] < flip.bbox(kid, order(flip)[2])[0], "and the rows follow it")
        app.flip_format_var.set(gui.PRICE_FORMATS[1])
        app._on_price_format_changed()
        check(order(flip) == before, "so does changing Prices")

        # A scrolled table: the right column moves, and it scrolls for you
        db.watchlist_add(1, "Item 1")
        app._lists_changed()
        app.notebook.select(app.watchlist_tab)
        settle()
        check(watch.xview()[1] < 1, "the Watchlist is wider than the window")
        watch.xview_moveto(1)
        settle()
        far = order(watch)[-1]
        check(far == "added", far)
        x = header_x(watch, "verdict")
        check(watch.identify_column(x) == f"#{order(watch).index('verdict') + 1}", "the scrolled header is found")
        drag(watch, "verdict", header_x(watch, "added") + 70)
        check(order(watch)[-1] == "verdict", f"it moves the column you grabbed: {order(watch)[-3:]}")
        watch.xview_moveto(0)
        settle()
        first = watch.xview()[0]
        x = header_x(watch, "vol_12h")
        press(watch, x)
        move(watch, x + 20)
        move(watch, watch.winfo_width() - 5)
        check(watch.xview()[0] > first, f"holding near the side scrolls: {first} to {watch.xview()[0]}")
        move(watch, 5)
        move(watch, 5)
        release(watch, 200)
        watch.xview_moveto(0)
        settle()
        check(saved().get("watchlist") == order(watch), "each tab saves its own")
        check(set(saved()) >= {"fliprec", "watchlist"}, f"both are kept: {sorted(saved())}")

        # The right-click menu still gets the real name
        kid = next(iter(app.watch_id_map))
        x, y, w, h = watch.bbox(kid, "item")
        menus.clear()
        watch.event_generate("<Button-3>", x=x + 5, y=y + h // 2)
        root.update()
        check(menus and menus[-1].entrycget(0, "label") == "Remove from Watchlist", "rows keep their right-click menu")

        # Reset
        menus.clear()
        watch.event_generate("<Button-3>", x=30, y=12)
        root.update()
        menu = menus[-1]
        check(menu.entrycget(0, "label") == "Reset columns" and menu.entrycget(0, "state") == "normal", "right-clicking a header offers Reset columns")
        fliprec_saved = saved().get("fliprec")
        menu.invoke(0)
        root.update()
        check(order(watch) == list(watch["columns"]), "it puts the tab back")
        check("watchlist" not in saved() and saved().get("fliprec") == fliprec_saved, "only that tab")
        menus.clear()
        watch.event_generate("<Button-3>", x=30, y=12)
        root.update()
        check(menus[-1].entrycget(0, "state") == "disabled", "nothing to reset: it's greyed out")

        # Tables that don't move columns
        app.notebook.select(app.ignored_tab)
        settle()
        menus.clear()
        app.ignored_tree.event_generate("<Button-3>", x=30, y=12)
        root.update()
        check(not menus, "the Ignored List has no column menu")
        check(app.ignored_tree not in app._column_tables, "or dragging")

        # Help covers it
        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        check("drag its header" in helptext and "Reset columns" in helptext, "Help covers it")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL COLUMN CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
