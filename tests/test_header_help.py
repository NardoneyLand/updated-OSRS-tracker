"""Hover text on column headers: where it shows, when it goes, and that dragging and sorting still work."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
settings.save({"ua_contact": "test-contact", "flip_show": "All verdicts"})
import api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


now = time.time()
MAPPING = [{"id": i, "name": f"Item {i}", "limit": 1000} for i in range(1, 6)]
LATEST = {str(i): {"high": 2000, "low": 1800, "highTime": now, "lowTime": now} for i in range(1, 6)}
BULK = {str(i): {"avgHighPrice": 2000, "avgLowPrice": 1800, "highPriceVolume": 900, "lowPriceVolume": 900} for i in range(1, 6)}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_bulk_average = lambda step="1h": BULK
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []
gui.HEADER_TIP_DELAY = 40
root = tk.Tk()
app = gui.OSRSTrackerApp(root)


def settle(seconds=0.15):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.005)


def header_x(tree, column):
    order = app._display_order(tree)
    widths = {c: tree.column(c, "width") for c in order}
    offset = tree.xview()[0] * sum(widths.values())
    left = sum(widths[c] for c in order[:order.index(column)])
    return int(left + widths[column] / 2 - offset)


def over_tree(tree):
    """The mouse is really on another desktop's screen, so say it's on the table."""
    tree.winfo_containing = lambda x, y: tree
    tree.winfo_pointerxy = lambda: (root.winfo_rootx() + 100, root.winfo_rooty() + 100)


def hover(tree, column, y=12):
    tree.event_generate("<Motion>", x=header_x(tree, column), y=y)
    settle()


def tip_text():
    """What's showing, or None."""
    if not app._header_tip or app._header_tip[0].state() == "withdrawn":
        return None
    return app._header_tip[1].cget("text")


def leave(tree):
    tree.event_generate("<Leave>")
    settle()


def scenario():
    try:
        settle(0.8)
        flip, watch, ignored, volatile, news = app.flip_tree, app.watch_tree, app.ignored_tree, app.volatile_tree, app.news_tree

        # Every column on every table has text
        tables = {"FlipRec": (app.FLIP_COLUMNS, {}), "Watchlist": (app.WATCH_COLUMNS, gui.WATCH_HEADER_HELP),
                  "Ignored List": (app.IGNORED_COLUMNS, gui.IGNORED_HEADER_HELP),
                  "Volatile": (app.VOLATILE_COLUMNS, gui.VOLATILE_HEADER_HELP)}
        for name, (columns, tab_help) in tables.items():
            missing = [c[0] for c in columns if c[0] not in {**gui.HEADER_HELP, **tab_help}]
            check(not missing, f"{name} explains every column: {missing}")
        check(set(news["columns"]) <= set(gui.NEWS_HEADER_HELP), "News too")

        # Showing, after a pause
        app.notebook.select(app.fliprec_tab)
        settle()
        over_tree(flip)
        check(app._header_tip is None, "no window until it's needed")
        flip.event_generate("<Motion>", x=header_x(flip, "qty"), y=12)
        root.update()
        check(tip_text() is None, "nothing straight away")
        settle()
        check(tip_text() == gui.HEADER_HELP["qty"], f"it shows after a pause: {tip_text()}")
        tip = app._header_tip[0]
        check(tip.winfo_rootx() >= root.winfo_rootx(), "inside the app's window on the left")
        check(tip.winfo_rootx() + tip.winfo_width() <= root.winfo_rootx() + root.winfo_width() + 1, "and on the right")

        # Moving
        hover(flip, "cost")
        check(tip_text() == gui.HEADER_HELP["cost"], "moving to another header changes it")
        hover(flip, "cost")
        check(tip_text() == gui.HEADER_HELP["cost"], "moving along the same header leaves it")
        flip.event_generate("<Motion>", x=header_x(flip, "cost"), y=60)
        settle()
        check(tip_text() is None, "over the rows it goes away")
        hover(flip, "cost")
        check(tip_text() == gui.HEADER_HELP["cost"], "and comes back on the header")
        leave(flip)
        check(tip_text() is None and app._header_tip_target is None, "leaving the table hides it")
        window = app._header_tip[0]
        hover(flip, "roi")
        check(app._header_tip[0] is window, "one window is reused")
        leave(flip)

        # A quick pass over a header shows nothing
        flip.event_generate("<Motion>", x=header_x(flip, "roi"), y=12)
        root.update()
        leave(flip)
        check(tip_text() is None and app._header_tip_timer is None, "a quick pass shows nothing")

        # A separator isn't a header
        left = sum(flip.column(c, "width") for c in app._display_order(flip)[:app._display_order(flip).index("qty") + 1])
        edge = next(x for x in range(left - 6, left + 6) if flip.identify_region(x, 12) == "separator")
        hover(flip, "roi")
        flip.event_generate("<Motion>", x=edge, y=12)
        settle()
        check(tip_text() is None, "the edge between columns shows nothing")

        # Clicking sorts, hides it, and it stays hidden on that column
        hover(flip, "profit")
        check(tip_text() == gui.HEADER_HELP["profit"], "shown before the click")
        before = app.flip_sort
        x = header_x(flip, "profit")
        flip.event_generate("<ButtonPress-1>", x=x, y=12)
        root.update()
        check(tip_text() is None, "pressing hides it")
        flip.event_generate("<ButtonRelease-1>", x=x, y=12)
        settle()
        check(app.flip_sort != before and app.flip_sort[0] == "profit", f"the click still sorts: {app.flip_sort}")
        flip.event_generate("<Motion>", x=x, y=12)
        settle()
        check(tip_text() is None, "it stays away until you move on")
        hover(flip, "qty")
        check(tip_text() == gui.HEADER_HELP["qty"], "then the next column shows")

        # Dragging still moves a column, with no text
        leave(flip)
        order = app._display_order(flip)
        x = header_x(flip, "qty")
        flip.event_generate("<ButtonPress-1>", x=x, y=12)
        root.update()
        target = header_x(flip, "profit")
        for step in (x + 20, target):
            flip.event_generate("<Motion>", x=step, y=12, state=0x100)
            root.update()
        settle()
        check(tip_text() is None, "no text during a drag")
        flip.event_generate("<ButtonRelease-1>", x=target, y=12)
        settle()
        check(app._display_order(flip) != order, "dragging still moves the column")

        # It follows the column after a move
        hover(flip, "qty")
        check(tip_text() == gui.HEADER_HELP["qty"], "the text follows a moved column")
        leave(flip)
        app._reset_columns(flip)
        settle()

        # Right-click still opens the header menu, and hides the text
        hover(flip, "roi")
        menus = []
        real_popup, app._popup = app._popup, lambda menu, event: menus.append(menu)
        flip.event_generate("<Button-3>", x=header_x(flip, "roi"), y=12)
        settle()
        app._popup = real_popup
        check(menus and tip_text() is None, "right-click opens the menu and hides the text")
        leave(flip)

        # Mouse over a menu or another window
        flip.winfo_containing = lambda x, y: root
        hover(flip, "tax")
        check(tip_text() is None, "not if something else is under the mouse")
        leave(flip)

        def unnamed(x, y):
            raise KeyError("popdown")
        flip.winfo_containing = unnamed
        hover(flip, "tax")
        check(tip_text() is None, "or a widget Tk doesn't know")
        leave(flip)
        over_tree(flip)

        # Changing tab
        hover(flip, "tax")
        app.notebook.select(app.watchlist_tab)
        settle()
        check(tip_text() is None, "changing tab hides it")

        # Each tab's own wording
        for tab, tree, column, expected in (
                (app.watchlist_tab, watch, "added", "When you added this item to your Watchlist."),
                (app.watchlist_tab, watch, "verdict", gui.WATCH_HEADER_HELP["verdict"]),
                (app.watchlist_tab, watch, "buy", gui.HEADER_HELP["buy"]),
                (app.ignored_tab, ignored, "added", "When you added this item to your Ignored List."),
                (app.volatile_tab, volatile, "window_volume", gui.VOLATILE_HEADER_HELP["window_volume"]),
                (app.news_tab, news, "date", gui.NEWS_HEADER_HELP["date"])):
            app.notebook.select(tab)
            settle()
            over_tree(tree)
            tree.xview_moveto(0)
            settle(0.05)
            if header_x(tree, column) >= tree.winfo_width():   # past the right edge
                tree.xview_moveto(1)
                settle(0.05)
            hover(tree, column)
            check(tip_text() == expected, f"{column} on its tab: {tip_text()}")
            leave(tree)
        check("Unprofitable" not in gui.HEADER_HELP["verdict"] and "Unprofitable" in gui.WATCH_HEADER_HELP["verdict"], "only the Watchlist mentions Unprofitable")
        check("rest the mouse on its header" in " ".join(b for _h, b in gui.HELP_SECTIONS), "Help mentions it")
    except Exception:
        traceback.print_exc()
        failures.append("scenario crashed")
    finally:
        root.destroy()


root.after(50, scenario)
root.mainloop()
print("\nALL PASSED" if not failures else f"\nFAILURES: {failures}")
sys.exit(1 if failures else 0)
