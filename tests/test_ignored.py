"""The right-click menu and the Ignored List: what's hidden, the Watchlist tag, and Ask AI."""
import os, sqlite3, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
settings.save({"ua_contact": "test-contact", "alert_threshold": 1})
import ai, aitools, api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# A database from before this feature keeps its Watchlist and gains the table
conn = sqlite3.connect(db.DB_NAME)
conn.execute("CREATE TABLE watchlist (item_id INTEGER PRIMARY KEY, item_name TEXT NOT NULL, added_ts INTEGER NOT NULL)")
conn.execute("INSERT INTO watchlist VALUES (5, 'Item 005', 1000)")
conn.commit()
conn.close()
db.initialize_db()
check(db.ignored_ids() == set() and [w[0] for w in db.watchlist_items()] == [5], "an older database upgrades and keeps its Watchlist")

# The two lists don't affect each other
db.watchlist_set_targets(5, 900, None)
db.ignore_items([(5, "Item 005"), (6, "Item 006")])
check(db.ignored_ids() == {5, 6} and [w[0] for w in db.watchlist_items()] == [5], "ignoring leaves the Watchlist alone")
check(db.watchlist_targets()[5]["target_below"] == 900, "and its target prices")
db.ignore_items([(6, "Item 006")])
check(len(db.ignored_items()) == 2, "ignoring twice keeps one entry")
db.watchlist_add(6, "Item 006")
check(db.ignored_ids() == {5, 6} and len(db.watchlist_items()) == 2, "adding to the Watchlist leaves the Ignored List alone")
db.watchlist_remove(6)
check(db.ignored_ids() == {5, 6}, "so does removing from the Watchlist")
db.unignore_items([5])
check([w[0] for w in db.watchlist_items()] == [5], "and removing from the Ignored List leaves the Watchlist alone")
db.watchlist_remove(5)

# Item 6 is still ignored when the app starts: it must load that
NOW = time.time()
COUNT = 120
MAPPING = [{"id": i, "name": f"Item {i:03}", "limit": 1000, "members": True} for i in range(1, COUNT + 1)]
LATEST = {str(i): {"high": 1100 + i, "low": 1000, "highTime": NOW - 20, "lowTime": NOW - 30} for i in range(1, COUNT + 1)}
HOUR = {k: {"highPriceVolume": 5000, "lowPriceVolume": 5000, "avgHighPrice": 1100, "avgLowPrice": 1000} for k in LATEST}
# The last 5 minutes back up every margin, so none of them is Suspect
FIVE = {k: {**HOUR[k], "avgHighPrice": v["high"], "avgLowPrice": v["low"]} for k, v in LATEST.items()}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_bulk_average = lambda step="1h": FIVE if step == "5m" else HOUR
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []

menus = []
tk.Menu.tk_popup = lambda self, x, y, entry="": menus.append(self)

root = tk.Tk()
app = gui.OSRSTrackerApp(root)
check(app._ignored == {6}, "the list is loaded at startup")


def settle(cond=lambda: False, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond():
            root.update()
            return True
        time.sleep(0.005)
    return False


def names(tree, id_map):
    return [tree.set(i, "item") for i in tree.get_children() if i in id_map]


def iid_of(tree, id_map, item_id):
    return next(iid for iid, i in id_map.items() if i == item_id)


def flip_names():
    return names(app.flip_tree, app.flip_id_map)


def alert_names():
    return names(app.alerts_tree, app.alert_id_map)


def volatile_names():
    return names(app.volatile_tree, app.volatile_id_map)


def ignored_names():
    return names(app.ignored_tree, app.ignored_id_map)


def watch_names():
    return names(app.watch_tree, app.watch_id_map)


def right_click(tree, iid):
    """A real right-click on a row. tk_popup is replaced, so it can't block."""
    menus.clear()
    tree.see(iid)
    root.update()
    x, y, w, h = tree.bbox(iid, tree["columns"][0])
    tree.event_generate("<Button-3>", x=x + 5, y=y + h // 2)
    root.update()
    return menus[-1] if menus else None


def labels(menu):
    return [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)
            if menu.type(i) == "command"]


def choose(menu, label):
    index = next(i for i in range(menu.index("end") + 1)
                 if menu.type(i) == "command" and menu.entrycget(i, "label") == label)
    menu.invoke(index)
    root.update()


def refresh_flip():
    app._refresh_market()
    settle(lambda: str(app.refresh_btn["state"]) == "normal", 5)


def refresh_volatile():
    app._refresh_volatile()
    settle(lambda: str(app.refresh_btn["state"]) == "normal", 5)


def tick():
    app._refresh_market()
    settle(lambda: not app._monitor_busy, 5)
    root.update()


def scenario():
    try:
        settle(lambda: bool(app.item_data), 10)
        tabs = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        check(tabs[3] == "Ignored List", f"the tab sits after Watchlist: {tabs}")

        # FlipRec: an ignored item is gone, and stays gone after a refresh
        app.notebook.select(app.fliprec_tab)
        settle(lambda: bool(flip_names()), 5)
        check("Item 006" not in flip_names() and len(flip_names()) == COUNT - 1, "FlipRec leaves out what was ignored before launch")
        check(any(r["item_id"] == 6 for r in app.flip_results), "it's hidden, not dropped, so it can come back without a fetch")

        # The right-click menu
        tree, id_map = app.flip_tree, app.flip_id_map
        menu = right_click(tree, iid_of(tree, id_map, 10))
        check(labels(menu) == ["Add to Watchlist", "Ignore item", "Open in Search"], f"menu: {labels(menu)}")
        check([id_map[i] for i in tree.selection()] == [10], "right-clicking selects the row")
        first, second = iid_of(tree, id_map, 11), iid_of(tree, id_map, 12)
        tree.selection_set(first, second)
        menu = right_click(tree, second)
        check(len(tree.selection()) == 2, "right-clicking inside a selection keeps it")
        check(labels(menu) == ["Add 2 items to Watchlist", "Ignore 2 items", "Open in Search"], f"several rows: {labels(menu)}")
        menu = right_click(tree, iid_of(tree, id_map, 13))
        check(len(tree.selection()) == 1, "right-clicking outside a selection replaces it")
        menus.clear()
        tree.yview_moveto(0)
        root.update()
        row_top = tree.bbox(tree.get_children()[0], "item")[1]
        for y in range(1, row_top):
            tree.event_generate("<Button-3>", x=20, y=y)
        root.update()
        check(row_top > 5 and not menus, f"no menu anywhere on a column header ({row_top} pixels tall)")

        # Add to Watchlist
        tree.selection_set(first, second)
        menu = right_click(tree, second)
        choose(menu, "Add 2 items to Watchlist")
        check(sorted(watch_names()) == ["Item 011", "Item 012"], f"added to the Watchlist: {watch_names()}")
        menu = right_click(tree, second)
        check(labels(menu)[0] == "Remove 2 items from Watchlist", f"offers removal once they're on it: {labels(menu)}")
        tree.selection_set(second, iid_of(tree, id_map, 13))
        menu = right_click(tree, second)
        check(labels(menu)[0] == "Add 2 items to Watchlist", "a mix offers adding")

        # Ignoring keeps your sort on FlipRec and doesn't re-fetch
        app._sort_fliprec("name", True)
        fetches = []
        api.get_all_latest_prices = lambda: fetches.append(1) or LATEST
        order = flip_names()
        check(order[0] == "Item 120", "sorted by name, newest last")
        tree = app.flip_tree
        menu = right_click(tree, iid_of(tree, id_map, 120))
        choose(menu, "Ignore item")
        check("Item 120" not in flip_names() and app.flip_sort == ("name", True) and flip_names()[0] == "Item 119", "ignoring keeps the sort")
        check(not fetches, "ignoring doesn't fetch prices")
        check(app._ignored == {6, 120}, "the app's list is up to date")
        api.get_all_latest_prices = lambda: LATEST
        refresh_flip()
        check("Item 120" not in flip_names(), "still gone after Refresh")

        # Hidden items don't count in the banner
        for row in app.flip_results:
            if row["item_id"] == 120:
                row["active"] = False
        app._render_fliprec_table()
        check("item(s) hidden" not in app.flip_banner.get(), f"an ignored item isn't reported as inactive: {app.flip_banner.get()!r}")
        refresh_flip()

        # Alerts: never for an ignored item, and one already live disappears
        tick()
        check(len(alert_names()) == COUNT - 2 and "Item 120" not in alert_names(), "ignored items don't alert")
        check(app.notebook.tab(app.alerts_tab, "text") == f"Alerts ({COUNT - 2})", "the tab count leaves them out")
        tree, id_map = app.alerts_tree, app.alert_id_map
        app.notebook.select(app.alerts_tab)
        root.update()
        menu = right_click(tree, iid_of(tree, id_map, 50))
        choose(menu, "Ignore item")
        check("Item 050" not in alert_names(), "a live alert disappears straight away")
        check(not [a for a in db.get_active_alerts() if a["item_id"] == 50], "so does what Ask AI reads")
        tick()
        check("Item 050" not in alert_names(), "and it doesn't come back on the next check")
        flashes = []
        real_flash, gui.flash_window = gui.flash_window, lambda _root: flashes.append(1)
        app._unignore([50])
        gui.flash_window = real_flash
        check("Item 050" in alert_names(), "taken off the list, it can alert again straight away")
        check(not flashes, "that returns quietly within the hour")
        check("Item 050" in flip_names(), "and it's back on FlipRec, without a fetch")

        # Volatile: the top 100 stays full
        app.notebook.select(app.volatile_tab)
        settle(lambda: bool(volatile_names()), 5)
        shown = volatile_names()
        check(len(shown) == 100 and shown[0] == "Item 119" and "Item 120" not in shown, f"a full 100 without ignored items: {len(shown)}, {shown[:1]}")
        check(shown[-1] == "Item 020", f"the next movers fill the gap: {shown[-1]}")
        tree, id_map = app.volatile_tree, app.volatile_id_map
        app._sort_volatile("name", False)
        check(volatile_names()[0] == "Item 020", "sorted by name")
        menu = right_click(tree, iid_of(tree, id_map, 30))
        choose(menu, "Ignore item")
        now = volatile_names()
        check(len(now) == 100 and "Item 030" not in now and now[0] == "Item 019" and app.volatile_sort == ("name", False), f"ignoring keeps 100 and the sort: {now[:1]}")
        check(app.volatile_status.get() == "100 movers in 1h window", app.volatile_status.get())
        tree.selection_set(iid_of(tree, id_map, 40))
        app._unignore([30])
        check("Item 030" in volatile_names() and "Item 019" not in volatile_names(), "taken off the list, it returns to the top 100")
        check([id_map[i] for i in tree.selection()] == [40], "your selected row stays selected")
        refresh_volatile()
        check("Item 120" not in volatile_names() and app.volatile_sort == ("name", False), "Refresh still leaves it out and keeps the sort")

        # Open in Search
        tree, id_map = app.flip_tree, app.flip_id_map
        app.notebook.select(app.fliprec_tab)
        root.update()
        menu = right_click(tree, iid_of(tree, id_map, 70))
        choose(menu, "Open in Search")
        settle(lambda: app._search_item and app._search_item["id"] == 70, 3)
        check(app._search_item["id"] == 70 and app.search_var.get() == "Item 070", "Open in Search opens the item")
        check(app.notebook.select() == str(app.search_tab), "and shows the Search tab")
        settle()

        # Watchlist: an ignored item stays, tagged, with its target prices working
        db.watchlist_add(70, "Item 070")
        db.watchlist_set_targets(70, 1100, None)
        app._render_watchlist()
        tree, id_map = app.watch_tree, app.watch_id_map
        app.notebook.select(app.watchlist_tab)
        root.update()
        menu = right_click(tree, iid_of(tree, id_map, 70))
        choose(menu, "Ignore item")
        tagged = gui.IGNORED_TAG + "Item 070"
        check(tagged in watch_names() and 70 in app._ignored, f"it stays on the Watchlist, tagged: {watch_names()}")
        check(tagged.startswith("Ignored"), "the tag says it's ignored")
        check("Item 070" not in flip_names() and "Item 070" not in alert_names(), "and it's hidden from FlipRec and Alerts")
        check(db.watchlist_targets()[70]["target_below"] == 1100, "its target price is kept")
        tick()
        check(app.notebook.tab(app.watchlist_tab, "text") == "Watchlist (1)", f"and still works: {app.notebook.tab(app.watchlist_tab, 'text')}")
        db.watchlist_set_targets(70, None, None)
        app._render_watchlist()
        menu = right_click(tree, iid_of(tree, id_map, 70))
        check(labels(menu)[:2] == ["Remove from Watchlist", "Remove from Ignored List"], f"its menu: {labels(menu)}")

        # The tag never ends up in a saved name
        tree.selection_set(iid_of(tree, id_map, 70), iid_of(tree, id_map, 11))
        menu = right_click(tree, iid_of(tree, id_map, 70))
        check(labels(menu)[1] == "Ignore 2 items", f"a mix offers ignoring: {labels(menu)}")
        choose(menu, "Ignore 2 items")
        check({i[1] for i in db.ignored_items()} >= {"Item 070", "Item 011"} and 11 in app._ignored, "saved names don't include the tag")
        app._sort_watchlist("name", False)
        check(watch_names()[0] == gui.IGNORED_TAG + "Item 011", f"sorting by name ignores the tag: {watch_names()}")
        style = ttk.Style()
        body = tkfont.Font(root=root, font=style.lookup("Treeview", "font"))
        room = tree.column("item", "minwidth") - 8
        check(body.measure(gui.IGNORED_TAG + "Ardougne teleport (tablet)") <= room, "a long name fits next to the tag")

        menu = right_click(tree, iid_of(tree, id_map, 12))
        check(labels(menu)[:2] == ["Remove from Watchlist", "Ignore item"], f"on the Watchlist it offers removal: {labels(menu)}")
        choose(menu, "Remove from Watchlist")
        check("Item 012" not in watch_names() and 12 not in app._ignored, "removing from the Watchlist doesn't ignore it")

        # Search still finds an ignored item, and its button only affects the Watchlist
        app._lookup_item(next(i for i in MAPPING if i["id"] == 70))
        settle(lambda: app._search_item and app._search_item["id"] == 70, 3)
        settle(lambda: getattr(app, "watch_btn", None) is not None and app.watch_btn.winfo_exists(), 3)
        settle()
        check(app._search_item["id"] == 70 and app.watch_btn.cget("text").startswith("★"), f"Search finds it: {app.watch_btn.cget('text')}")
        app.watch_btn.invoke()
        root.update()
        check(70 in app._ignored and tagged not in watch_names() and app.watch_btn.cget("text").startswith("☆"), "the Search button removes it from the Watchlist only")
        app.watch_btn.invoke()
        root.update()
        check(70 in app._ignored and tagged in watch_names(), "and adds it back, still tagged")

        # Search has its own button for the Ignored List
        check(app.ignore_btn.cget("text") == "Remove from Ignored List", f"Search says it's ignored: {app.ignore_btn.cget('text')}")
        app.ignore_btn.invoke()
        root.update()
        check(70 not in app._ignored and "Item 070" in watch_names() and "Item 070" in flip_names(), "the Search button takes it off the list")
        check(app.ignore_btn.cget("text") == "Ignore item", "and then offers to ignore it")
        app.ignore_btn.invoke()
        root.update()
        check(70 in app._ignored and tagged in watch_names() and "Item 070" not in flip_names(), "and ignores it from Search")
        check("Item 070" in ignored_names(), "it's on the Ignored List tab")

        # Taken off the list, the tag goes and it's back on FlipRec
        app._unignore([70])
        check("Item 070" in watch_names() and tagged not in watch_names(), "the tag goes when it's no longer ignored")
        check("Item 070" in flip_names(), "and it's back on FlipRec")

        # The Ignored List tab
        app.notebook.select(app.ignored_tab)
        root.update()
        check(set(ignored_names()) == {"Item 006", "Item 011", "Item 120"}, f"the tab lists them: {ignored_names()}")
        conn = sqlite3.connect(db.DB_NAME)
        for item_id, added in ((6, 100), (120, 200), (11, 300)):
            conn.execute("UPDATE ignored_items SET added_ts = ? WHERE item_id = ?", (added, item_id))
        conn.commit()
        conn.close()
        app._render_ignored()
        check(ignored_names() == ["Item 011", "Item 120", "Item 006"], f"newest first: {ignored_names()}")
        app._sort_ignored("name", False)
        check(ignored_names() == ["Item 006", "Item 011", "Item 120"], "sorts by name")
        app._sort_ignored("name", False)
        check(ignored_names() == ["Item 120", "Item 011", "Item 006"], "and reverses")
        tree, id_map = app.ignored_tree, app.ignored_id_map
        menu = right_click(tree, iid_of(tree, id_map, 6))
        check(labels(menu) == ["Add to Watchlist", "Remove from Ignored List", "Open in Search"], f"its menu: {labels(menu)}")
        choose(menu, "Add to Watchlist")
        check(6 in app._ignored and gui.IGNORED_TAG + "Item 006" in watch_names() and "Item 006" in ignored_names(), "adding to the Watchlist keeps it on this list too")
        menu = right_click(tree, iid_of(tree, id_map, 11))
        check(labels(menu)[0] == "Remove from Watchlist", f"it knows what's on the Watchlist: {labels(menu)}")
        choose(menu, "Remove from Ignored List")
        check(11 not in app._ignored and "Item 011" in watch_names() and "Item 011" in flip_names(), "removing from this list keeps it on the Watchlist")
        tree.selection_set(iid_of(tree, id_map, 120), iid_of(tree, id_map, 6))
        app._remove_selected_ignored()
        root.update()
        check(app._ignored == set() and "Item 120" in flip_names() and "Item 006" in watch_names(), "the Remove from list button works")

        # The empty message fits and has no menu
        menus.clear()
        kid = tree.get_children()[0]
        check(kid not in id_map, "an empty list shows a message")
        x, y, w, h = tree.bbox(kid, "item")
        tree.event_generate("<Button-3>", x=x + 5, y=y + h // 2)
        root.update()
        check(not menus, "no menu on that message")
        style = ttk.Style()
        body = tkfont.Font(root=root, font=style.lookup("Treeview", "font"))
        message = tree.set(kid, "item")
        check(body.measure(message) + 8 <= tree.column("item", "width"), f"the message fits: {body.measure(message)} of {tree.column('item', 'width')}")

        # Every tab can be reached at the smallest window
        root.geometry("900x560")
        settle()
        # Not squeezed, even with counts on Alerts and Watchlist: ttk clips the
        # labels if they need more than the width (1 pixel of border each side).
        tab_style = ttk.Style()
        tab_font = tkfont.Font(root=root, font=tab_style.lookup("TNotebook.Tab", "font"))
        pad = tab_style.lookup("TNotebook.Tab", "padding")
        pad = int(str(pad).split()[0]) if not isinstance(pad, (tuple, list)) else int(str(pad[0]))
        worst = ["FlipRec", "Alerts (999)", "Watchlist (99)", "Ignored List", "Volatile", "Search", "Ask AI", "News", "Help"]
        needed = sum(tab_font.measure(label) + 2 * pad + 2 for label in worst) + 4
        check(needed <= app.notebook.winfo_width(), f"tab labels aren't clipped at the smallest window: {needed} of {app.notebook.winfo_width()}")
        last = 0
        for x in range(0, app.notebook.winfo_width(), 5):
            try:
                last = max(last, app.notebook.index(f"@{x},10"))
            except tk.TclError:
                pass
        check(last == len(app.notebook.tabs()) - 1, f"all tabs fit at the smallest window: {last}")
        total = sum(tree.column(c, "minwidth") for c in tree["columns"])
        check(total <= tree.winfo_width() or not tree.winfo_ismapped(), f"the Ignored List table fits: {total} of {tree.winfo_width()}")
        root.geometry("1380x860")
        settle()

        # Ask AI leaves them out
        db.ignore_items([(120, "Item 120")])
        app._lists_changed()
        tools = aitools.MarketTools(lambda: app.item_data, app._volume)
        flips = tools.run("get_flips", {"limit": 50, "sort_by": "net"})
        check(flips["matching_items"] == COUNT - 1 and all(f["name"] != "Item 120" for f in flips["flips"]), f"get_flips leaves it out: {flips['matching_items']}")
        check(tools.run("get_flips", {"name_contains": "Item 120"})["flips"] == [], "even when asked to filter by its name")
        movers = tools.run("get_movers", {"limit": 50})
        check(movers["movers"][0]["name"] == "Item 119", f"get_movers leaves it out: {movers['movers'][0]['name']}")
        tick()
        state = tools.run("get_my_tracker", {})
        check(all(a["name"] != "Item 120" for a in state["active_alerts"]), "get_my_tracker's alerts leave it out")
        check("Item 120" not in str(state), "and doesn't mention it when it's not on the Watchlist")
        db.watchlist_add(120, "Item 120")
        watched = {w["name"]: w for w in tools.run("get_my_tracker", {})["watchlist"]}
        check(watched["Item 120"]["on_ignored_list"] is True, f"on the Watchlist it's flagged: {sorted(watched)}")
        check(watched["Item 011"]["on_ignored_list"] is False, "other Watchlist items aren't")
        item = tools.run("get_item", {"name": "Item 120"})
        check(item.get("on_ignored_list") is True, f"asked by name, it says it's ignored: {item.get('on_ignored_list')}")
        check(tools.run("get_item", {"name": "Item 119"}).get("on_ignored_list") is False, "other items don't")
        check("Ignored List" in ai.SYSTEM_PROMPT and "on_ignored_list" in ai.SYSTEM_PROMPT, "the AI is told what that means")

        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        check("Ignored List" in helptext and "Right-click" in helptext and "Ignored tag" in helptext, "Help covers it")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL IGNORED LIST CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
