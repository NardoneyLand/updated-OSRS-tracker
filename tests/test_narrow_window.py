"""A narrow or short window must not squeeze buttons, or put columns or notes out of reach."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
from tkinter import ttk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
settings.save({"ua_contact": "test-contact", "capital": "500m", "alert_threshold": 1})
import api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


NOW = time.time()
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True},
           {"id": 2, "name": "Voidwaker", "limit": 8, "members": True}]
LATEST = {"1": {"high": 2_000_000, "low": 1_900_000, "highTime": NOW - 20, "lowTime": NOW - 30},
          "2": {"high": 34_148_500, "low": 33_125_702, "highTime": NOW - 20, "lowTime": NOW - 30}}
HOUR = {key: {"highPriceVolume": 500, "lowPriceVolume": 500, "avgHighPrice": v["high"], "avgLowPrice": v["low"]}
        for key, v in LATEST.items()}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_bulk_average = lambda step="1h": HOUR
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


def resize(size):
    root.geometry(size)
    settle()


def right_edge(widget, inside):
    return widget.winfo_rootx() + widget.winfo_width() - inside.winfo_rootx()


def buttons(parent):
    return [w for w in parent.winfo_children() if w.winfo_class() == "TButton"]


def scrollbar_of(tab):
    return next(w for w in tab.winfo_children() if w.winfo_class() == "TScrollbar")


def notes_of(tab):
    return [w for w in tab.winfo_children() if w.winfo_class() == "TLabel" and str(w.cget("justify")) == "center"]


def check_table(name, tab, tree, needs_scroll_at_900):
    """At the current size, with this tab on show."""
    full = {c: tree.column(c, "minwidth") for c in tree["columns"]}
    short = [c for c in full if tree.column(c, "width") < full[c]]
    check(not short, f"{name}: no column is squeezed: {short}")
    note = notes_of(tab)[0]
    check(note.winfo_ismapped() and note.winfo_reqwidth() <= tab.winfo_width(),
          f"{name}: the note wraps inside the tab: {note.winfo_reqwidth()} of {tab.winfo_width()}")
    check(note.winfo_rooty() + note.winfo_height() <= tab.winfo_rooty() + tab.winfo_height(),
          f"{name}: and isn't cut off at the bottom")
    check(tree.winfo_height() > 60, f"{name}: the table still has room: {tree.winfo_height()}px")
    bar = scrollbar_of(tab)
    if needs_scroll_at_900:
        check(bar.winfo_ismapped(), f"{name}: there's a scrollbar for the rest of the columns")
        tree.xview_moveto(1)
        settle()
        first, last = tree.xview()
        check(last >= 1 and first > 0, f"{name}: which reaches the last column: {first:.2f}-{last:.2f}")
        tree.xview_moveto(0)
        settle()
    else:
        check(not bar.winfo_ismapped(), f"{name}: no scrollbar when the columns fit")


def scenario():
    try:
        settle(lambda: app.item_data, 8)
        db.watchlist_add(1, "Abyssal whip")
        app._monitor_tick()
        settle(lambda: not app._monitor_busy, 8)
        app.notebook.select(app.fliprec_tab)
        settle(lambda: app.flip_results, 8)

        tabs = (("FlipRec", app.fliprec_tab, app.flip_tree),
                ("Alerts", app.alerts_tab, app.alerts_tree),
                ("Watchlist", app.watchlist_tab, app.watch_tree),
                ("Volatile", app.volatile_tab, app.volatile_tree))
        wide_values = {}

        # Full size: looks as it always has
        resize("1250x860")
        for name, tab, tree in tabs:
            app.notebook.select(tab)
            settle()
            check(not scrollbar_of(tab).winfo_ismapped(), f"{name} at 1250: no scrollbar")
            wide_values[name] = tree.column(tree["columns"][0], "width")
        app.notebook.select(app.fliprec_tab)
        settle()
        flip_actions = app.flip_refresh_btn.master
        check(flip_actions.grid_info()["row"] == 0, "FlipRec at 1250: Refresh stays on the one row")
        y = app.flip_refresh_btn.winfo_rooty()
        app.flip_status.set("Fetching…")
        settle()
        app.flip_status.set("Capital must look like 5m, 250k or 1,000,000.")
        settle()
        check(app.flip_refresh_btn.winfo_rooty() == y, "FlipRec at 1250: a changing status doesn't make it jump")
        app.flip_status.set("")

        # Smallest window
        resize("900x560")
        for name, tab, tree in tabs:
            app.notebook.select(tab)
            settle()
            check_table(name, tab, tree, name != "Volatile")

        app.notebook.select(app.fliprec_tab)
        settle()
        refresh = app.flip_refresh_btn
        bar = flip_actions.master
        check(flip_actions.grid_info()["row"] == 1, "FlipRec at 900: Refresh moves to its own row")
        check(refresh.winfo_width() >= refresh.winfo_reqwidth(), f"FlipRec at 900: Refresh is whole: {refresh.winfo_width()}")
        check(right_edge(refresh, bar) <= bar.winfo_width(), "FlipRec at 900: and on screen")
        app.flip_status.set("Prices fetched at 12:00:00")
        settle()
        label = next(w for w in flip_actions.winfo_children() if w.winfo_class() == "TLabel")
        check(label.winfo_ismapped() and label.winfo_width() >= label.winfo_reqwidth(),
              f"FlipRec at 900: the status shows beside it: {label.winfo_width()}")
        check(right_edge(bar.winfo_children()[0], bar) <= bar.winfo_width(), "FlipRec at 900: the filters all fit")

        # A long status wraps instead of being cut off
        long_status = "Error: " + "the server said no " * 12
        for size in ("900x560", "1120x560", "1250x860"):
            resize(size)
            app.flip_status.set(long_status)
            settle()
            check(right_edge(label, bar) <= bar.winfo_width() and label.winfo_height() > 25,
                  f"FlipRec at {size}: a long status wraps inside the bar: {right_edge(label, bar)} of {bar.winfo_width()}")
            check(label.winfo_width() >= label.winfo_reqwidth(), f"FlipRec at {size}: and shows in full")
            check(right_edge(refresh, bar) <= bar.winfo_width(), f"FlipRec at {size}: without moving Refresh off screen")
        app.flip_status.set("Prices fetched at 12:00:00")
        resize("900x560")

        app.notebook.select(app.alerts_tab)
        settle()
        alert_actions = app.alerts_check_btn.master
        check(alert_actions.grid_info()["row"] == 1, "Alerts at 900: the buttons move to their own row")
        for button in buttons(alert_actions):
            text = button.cget("text")
            check(button.winfo_width() >= button.winfo_reqwidth(), f"Alerts at 900: {text} is whole")
            check(right_edge(button, alert_actions.master) <= alert_actions.master.winfo_width(),
                  f"Alerts at 900: {text} is on screen")

        # News fits with no scrollbar, and titles get any extra room
        app.notebook.select(app.news_tab)
        settle()
        tree = app.news_tree
        check(not scrollbar_of(app.news_tab).winfo_ismapped() and tree.xview() == (0.0, 1.0),
              f"News at 900: every column fits: {tree.xview()}")
        resize("1250x860")
        app.notebook.select(app.news_tab)
        settle()
        check(tree.column("title", "width") > 700 and tree.column("date", "width") == 140,
              f"News at 1250: titles take the spare room: {tree.column('title', 'width')}")
        resize("900x560")

        # A refresh doesn't undo where you've scrolled to
        app.notebook.select(app.alerts_tab)
        settle()
        app.alerts_tree.xview_moveto(1)
        settle()
        first = app.alerts_tree.xview()[0]
        app._render_alerts()
        settle()
        check(app.alerts_tree.xview()[0] == first, "a redraw keeps the sideways scroll")
        app.alerts_tree.xview_moveto(0)

        # And back to full size, with everything undone
        resize("1250x860")
        for name, tab, tree in tabs:
            app.notebook.select(tab)
            settle()
            check(not scrollbar_of(tab).winfo_ismapped(), f"{name} back at 1250: the scrollbar has gone")
            check(tree.column(tree["columns"][0], "width") == wide_values[name],
                  f"{name} back at 1250: columns return to their size")
        app.notebook.select(app.fliprec_tab)
        settle()
        check(flip_actions.grid_info()["row"] == 0, "FlipRec back at 1250: Refresh is back on the one row")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL NARROW WINDOW CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
