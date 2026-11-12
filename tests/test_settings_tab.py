"""The Settings tab, FlipRec without a Verdict column, and searching Help."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
# A FlipRec order saved while it still had a Verdict column.
settings.save({"ua_contact": "test-contact", "alert_threshold": 1, "flip_show": "All verdicts",
               "column_order": {"fliprec": ["item", "verdict", "sell", "buy"]}})
import api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


now = time.time()
MAPPING = [{"id": 2, "name": "Lobster", "limit": 6000, "members": False},
           {"id": 3, "name": "Dead item", "limit": 100, "members": False}]
LATEST = {"2": {"high": 300, "low": 260, "highTime": now - 20, "lowTime": now - 30},
          # One side hasn't traded for two hours: inactive
          "3": {"high": 5000, "low": 4000, "highTime": now - 7200, "lowTime": now - 30}}
HOUR = {"2": {"highPriceVolume": 5000, "lowPriceVolume": 4000, "avgHighPrice": 290, "avgLowPrice": 250},
        "3": {"highPriceVolume": 0, "lowPriceVolume": 40, "avgHighPrice": None, "avgLowPrice": 4000}}
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


def widgets(w):
    return [w] + [d for c in w.winfo_children() for d in widgets(c)]


def rows():
    tree = app.flip_tree
    return {tree.set(i, "item"): tree.set(i, "signal") for i in tree.get_children()}


def scenario():
    try:
        settle(lambda: bool(app.item_data), 10)
        settle(lambda: app._market is not None and not app._monitor_busy, 5)
        settle()

        # FlipRec has no Verdict column, even in a saved order; the Watchlist keeps its own
        flip = app.flip_tree
        check("verdict" not in flip["columns"], f"FlipRec has no Verdict column: {flip['columns']}")
        shown = list(flip["displaycolumns"])
        check("verdict" not in shown and shown[:3] == ["item", "sell", "buy"], f"a saved order with it still works: {shown}")
        check("verdict" in app.watch_tree["columns"], "the Watchlist keeps its Verdict column")

        # The checkboxes live on Settings now
        tabs = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        check(tabs[-2:] == ["Settings", "Help"], f"Settings sits before Help: {tabs}")
        on_fliprec = [w.cget("text") for w in widgets(app.fliprec_tab) if w.winfo_class() == "TCheckbutton"]
        check(on_fliprec == [], f"FlipRec has no checkboxes left: {on_fliprec}")
        boxes = {w.cget("text"): w for w in widgets(app.settings_tab) if w.winfo_class() == "TCheckbutton"}
        want = ["Hide inactive"] + ([gui.FLASH_LABEL] if gui.FLASH_LABEL else [])
        check(sorted(boxes) == sorted(want), f"Settings has them: {sorted(boxes)}")
        check(app.flip_hide_inactive_var.get() is True, "Hide inactive is on by default")

        app._render_fliprec_table()
        check("Lobster" in rows() and "Dead item" not in rows(), f"so FlipRec hides the inactive item: {rows()}")
        check("Hide inactive on the Settings tab" in app.flip_banner.get(), f"and says where to change that: {app.flip_banner.get()!r}")
        boxes["Hide inactive"].invoke()
        settle()
        check(settings.load()["flip_hide_inactive"] is False, "unticking it on Settings is saved")
        check(rows().get("Dead item", "").startswith("⚠ Inactive"), f"FlipRec shows it, and its Note says Inactive: {rows()}")
        boxes["Hide inactive"].invoke()
        settle()
        check("Dead item" not in rows() and settings.load()["flip_hide_inactive"] is True, "ticking it hides it again")
        if gui.FLASH_LABEL:
            boxes[gui.FLASH_LABEL].invoke()
            check(settings.load()["alert_flash"] is False and not app.alert_flash_var.get(), "the flash checkbox is saved")
            boxes[gui.FLASH_LABEL].invoke()
            check(settings.load()["alert_flash"] is True, "and turned back on")

        # Searching Help
        app.notebook.select(app.help_tab)
        root.geometry("1380x860")
        settle(seconds=0.5)
        text = app.help_text
        whole = text.get("1.0", "end").lower()

        app.help_find_var.set("flash")
        settle()
        n = whole.count("flash")
        check(n > 1 and len(app._help_matches) == n, f"every match is found: {len(app._help_matches)} of {n}")
        check(len(text.tag_ranges("match")) == 2 * n, "and highlighted")
        check(app.help_find_status.get() == f"1 of {n}", f"it goes to the first: {app.help_find_status.get()!r}")
        check(text.get(*text.tag_ranges("match_now")).lower() == "flash", "the current one is marked")
        app._help_step(1)
        check(app.help_find_status.get() == f"2 of {n}", f"Next goes on: {app.help_find_status.get()!r}")
        settle()
        start = app._help_matches[1][0]
        check(text.bbox(start) is not None, "and scrolls to it")
        app._help_step(-1)
        app._help_step(-1)
        check(app.help_find_status.get() == f"{n} of {n}", f"Previous wraps round: {app.help_find_status.get()!r}")
        entry = app.help_find_entry
        check(entry.bind("<Return>") and entry.bind("<Shift-Return>") and entry.bind("<Escape>"), "Enter, Shift+Enter and Escape are bound")

        app.help_find_var.set("SETTINGS TAB")
        settle()
        check(len(app._help_matches) == whole.count("settings tab") > 0, f"it ignores case and finds phrases: {len(app._help_matches)}")
        app.help_find_var.set("zzqxv")
        settle()
        check(app.help_find_status.get() == "No matches" and not text.tag_ranges("match"), "no match says so")
        app.help_find_var.set("")
        settle()
        check(app.help_find_status.get() == "" and not text.tag_ranges("match") and not text.tag_ranges("match_now"), "clearing it removes the highlights")
        check(text.cget("state") == "disabled", "Help stays read-only")

        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        check("Search help" in helptext and "Settings · Help" in helptext, "Help mentions searching it and the Settings tab")
        check("FlipRec has no Verdict column" in helptext and "beside the threshold" not in helptext and "checkbox on FlipRec" not in helptext,
              "and no longer puts the checkboxes on FlipRec")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL SETTINGS TAB CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
