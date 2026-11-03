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
           {"id": 2, "name": "Voidwaker", "limit": 8, "members": True, "highalch": 1_200_000, "lowalch": 800_000}]
LATEST = {"1": {"high": 2_000_000, "low": 1_900_000, "highTime": NOW - 20, "lowTime": NOW - 30},
          "2": {"high": 34_148_500, "low": 33_125_702, "highTime": NOW - 20, "lowTime": NOW - 30}}
HOUR = {key: {"highPriceVolume": 500, "lowPriceVolume": 500, "avgHighPrice": v["high"], "avgLowPrice": v["low"]}
        for key, v in LATEST.items()}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_latest_price = lambda item_id: LATEST[str(item_id)]
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
        app._refresh_market()
        settle(lambda: not app._monitor_busy, 8)
        app.notebook.select(app.fliprec_tab)
        settle(lambda: app.flip_results, 8)

        tabs = (("FlipRec", app.fliprec_tab, app.flip_tree),
                ("Watchlist", app.watchlist_tab, app.watch_tree),
                ("Volatile", app.volatile_tab, app.volatile_tree))
        wide_values = {}

        # Full size: looks as it always has
        resize("1380x860")
        for name, tab, tree in tabs:
            app.notebook.select(tab)
            settle()
            # The Watchlist has the most columns, so it scrolls even at full size.
            check(scrollbar_of(tab).winfo_ismapped() == (name == "Watchlist"), f"{name} at 1380: a scrollbar only if it needs one")
            wide_values[name] = tree.column(tree["columns"][0], "width")
        app.notebook.select(app.fliprec_tab)
        settle()
        flip_actions = app.refresh_btn.master
        check(flip_actions.grid_info()["row"] == 0, "FlipRec at 1380: Refresh stays on the one row")
        y = app.refresh_btn.winfo_rooty()
        app.market_status.set("Fetching…")
        settle()
        app.market_status.set("Capital must look like 5m, 250k or 1,000,000.")
        settle()
        check(app.refresh_btn.winfo_rooty() == y, "FlipRec at 1380: a changing status doesn't make it jump")
        app.market_status.set("")

        # Smallest window
        resize("900x560")
        for name, tab, tree in tabs:
            app.notebook.select(tab)
            settle()
            check_table(name, tab, tree, True)

        app.notebook.select(app.fliprec_tab)
        settle()
        refresh = app.refresh_btn
        bar = flip_actions.master
        check(flip_actions.grid_info()["row"] == 1, "FlipRec at 900: Refresh moves to its own row")
        check(refresh.winfo_width() >= refresh.winfo_reqwidth(), f"FlipRec at 900: Refresh is whole: {refresh.winfo_width()}")
        check(right_edge(refresh, bar) <= bar.winfo_width(), "FlipRec at 900: and on screen")
        app.market_status.set("Prices fetched at 12:00:00")
        settle()
        label = next(w for w in flip_actions.winfo_children() if w.winfo_class() == "TLabel")
        check(label.winfo_ismapped() and label.winfo_width() >= label.winfo_reqwidth(),
              f"FlipRec at 900: the status shows beside it: {label.winfo_width()}")
        check(right_edge(bar.winfo_children()[0], bar) <= bar.winfo_width(), "FlipRec at 900: the filters all fit")
        limits_bar = app.fliprec_tab.winfo_children()[0]
        combos = [w for group in limits_bar.winfo_children() for w in group.winfo_children() if w.winfo_class() == "TCombobox"]
        check(len(combos) == 3 and all(right_edge(w, limits_bar) <= limits_bar.winfo_width() for w in combos), f"FlipRec at 900: Show, Traded within and Min Vol 1h all fit: {[right_edge(w, limits_bar) for w in combos]} of {limits_bar.winfo_width()}")

        # Search: the longest Back button still leaves the search bar usable
        size_before = root.geometry().split("+")[0]
        resize("900x680")
        app.notebook.select(app.ignored_tab)
        settle()
        app._open_in_search(1)
        settle()
        back = app.search_back_btn
        check(back.winfo_ismapped() and app.search_entry.winfo_width() >= 150
              and all(right_edge(w, back.master) <= back.master.winfo_width() and w.winfo_width() >= min(w.winfo_reqwidth(), 150)
                      for w in back.master.winfo_children() if w.winfo_ismapped()),
              f"Search at 900: Back fits the bar, search box {app.search_entry.winfo_width()}px")
        app.notebook.select(app.fliprec_tab)
        settle()
        resize(size_before)

        # Search: Alch moves under the Volume table rather than being cut off
        app.notebook.select(app.search_tab)
        app._lookup_item(MAPPING[1])
        settle(lambda: "High alch" in app._search_stats, 3)
        settle()
        panel = app.result_frame
        alch = app._search_stats["High alch"]
        check(alch.cget("text") == "1,200,000 gp" and right_edge(alch, panel) <= panel.winfo_width(),
              f"Search at 900: the widest alch value fits: {right_edge(alch, panel)} of {panel.winfo_width()}")
        real_sides, real_partial = app._volume.sides, app._volume.is_partial
        app._volume.sides = lambda item_id, seconds: (123_456, 123_456)
        app._volume.is_partial = lambda seconds: True
        app._render_search_volume()    # as when volume history finishes loading
        settle()
        check(alch.master.grid_info()["row"] == 1 and right_edge(alch, panel) <= panel.winfo_width(),
              f"Search at 900: wide volumes move it to its own row: row {alch.master.grid_info()['row']}, {right_edge(alch, panel)} of {panel.winfo_width()}")
        app._volume.sides, app._volume.is_partial = real_sides, real_partial
        app._render_search_volume()
        app._lookup_item(MAPPING[0])
        settle(lambda: app._search_shown and app._search_shown["item"]["id"] == 1, 3)
        settle()
        alch = app._search_stats["High alch"]
        check(alch.cget("text") == "-" and alch.master.grid_info()["row"] == 0, f"Search at 900: otherwise it sits beside Volume: {alch.master.grid_info()['row']}")
        app.notebook.select(app.fliprec_tab)
        settle()

        # A long status wraps instead of being cut off
        long_status = "Error: " + "the server said no " * 12
        for size in ("900x560", "1120x560", "1380x860"):
            resize(size)
            app.market_status.set(long_status)
            settle()
            check(right_edge(label, bar) <= bar.winfo_width() and label.winfo_height() > 25,
                  f"FlipRec at {size}: a long status wraps inside the bar: {right_edge(label, bar)} of {bar.winfo_width()}")
            # Linux can wrap a pixel or two past the wrap length, within the label's padding.
            check(label.winfo_width() >= label.winfo_reqwidth() - 2, f"FlipRec at {size}: and shows in full")
            check(right_edge(refresh, bar) <= bar.winfo_width(), f"FlipRec at {size}: without moving Refresh off screen")
        app.market_status.set("Prices fetched at 12:00:00")
        resize("900x560")

        alert_actions = app.refresh_btn.master
        check(alert_actions.grid_info()["row"] == 1, "at 900: the buttons move to their own row")
        for button in buttons(alert_actions):
            text = button.cget("text")
            check(button.winfo_width() >= button.winfo_reqwidth(), f"at 900: {text} is whole")
            check(right_edge(button, alert_actions.master) <= alert_actions.master.winfo_width(),
                  f"at 900: {text} is on screen")

        # News fits with no scrollbar, and titles get any extra room
        app.notebook.select(app.news_tab)
        settle()
        tree = app.news_tree
        check(not scrollbar_of(app.news_tab).winfo_ismapped() and tree.xview() == (0.0, 1.0),
              f"News at 900: every column fits: {tree.xview()}")
        resize("1380x860")
        app.notebook.select(app.news_tab)
        settle()
        check(tree.column("title", "width") > 700 and tree.column("date", "width") == 140,
              f"News at 1380: titles take the spare room: {tree.column('title', 'width')}")
        resize("900x560")

        # A refresh doesn't undo where you've scrolled to
        app.notebook.select(app.fliprec_tab)
        settle()
        app.flip_tree.xview_moveto(1)
        settle()
        first = app.flip_tree.xview()[0]
        app._render_fliprec_table()
        settle()
        check(app.flip_tree.xview()[0] == first, "a redraw keeps the sideways scroll")
        app.flip_tree.xview_moveto(0)

        # And back to full size, with everything undone
        resize("1380x860")
        for name, tab, tree in tabs:
            app.notebook.select(tab)
            settle()
            check(scrollbar_of(tab).winfo_ismapped() == (name == "Watchlist"), f"{name} back at 1380: the scrollbar has gone if it's not needed")
            check(tree.column(tree["columns"][0], "width") == wide_values[name],
                  f"{name} back at 1380: columns return to their size")
        app.notebook.select(app.fliprec_tab)
        settle()
        check(flip_actions.grid_info()["row"] == 0, "FlipRec back at 1380: Refresh is back on the one row")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL NARROW WINDOW CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
