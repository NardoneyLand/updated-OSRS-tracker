"""The Upfront column: Buy x Qty on FlipRec and Alerts, sortable, and fitting at full size."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
settings.save({"ua_contact": "test-contact", "capital": "1b", "alert_threshold": 1})
import aitools, analyzer, api, gui, scraper

HEADER_PADDING, CELL_PADDING = 13, 8    # what the theme's cells use up, in pixels

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


NOW = time.time()
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True},
           {"id": 2, "name": "Voidwaker", "limit": 8, "members": True},
           {"id": 3, "name": "Lobster", "limit": 6000, "members": False}]
LATEST = {"1": {"high": 2_000_000, "low": 1_900_000, "highTime": NOW - 20, "lowTime": NOW - 30},
          "2": {"high": 40_000_000, "low": 33_125_702, "highTime": NOW - 20, "lowTime": NOW - 30},
          "3": {"high": 300, "low": 200, "highTime": NOW - 20, "lowTime": NOW - 30}}
HOUR = {key: {"highPriceVolume": 5000, "lowPriceVolume": 5000, "avgHighPrice": v["high"], "avgLowPrice": v["low"]}
        for key, v in LATEST.items()}
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_bulk_average = lambda step="1h": HOUR
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []

# What it's worth
rows = {r["name"]: r for r in analyzer.find_flips(LATEST, HOUR, MAPPING, capital=150_000, now=NOW)}
check(rows["Lobster"]["qty"] == 750 and rows["Lobster"]["cost"] == 150_000, "capital caps it: 750 lobsters at 200 is 150k")
rows = {r["name"]: r for r in analyzer.find_flips(LATEST, HOUR, MAPPING, capital=100_000_000, now=NOW)}
check(all(r["cost"] == r["buy"] * r["qty"] and r["cost"] <= 100_000_000 for r in rows.values()), "it's always Buy x Qty, and never more than your capital")
rows = {r["name"]: r for r in analyzer.find_flips(LATEST, HOUR, MAPPING, capital=0, now=NOW)}
check(rows["Abyssal whip"]["cost"] == 1_900_000 * 70, "with no capital it assumes the 4h limit")

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


def table(tree, id_map):
    columns = list(tree["columns"])
    return [dict(zip(columns, tree.item(i, "values"))) for i in tree.get_children() if i in id_map]


def fits(tree, name):
    total = sum(tree.column(c, "minwidth") for c in tree["columns"])
    check(total <= tree.winfo_width(), f"{name}: every column fits at full size: {total} of {tree.winfo_width()}")
    check(tree.xview() == (0.0, 1.0), f"{name}: with no sideways scrolling")


def scenario():
    try:
        settle(lambda: app.item_data, 8)
        app._refresh_market()
        settle(lambda: not app._monitor_busy, 8)
        check(root.winfo_width() == min(1380, root.winfo_screenwidth() - 80), f"opens wide enough for every column: {root.winfo_width()}")
        root.geometry("1380x860")
        app.notebook.select(app.fliprec_tab)
        settle(lambda: app.flip_results, 8)
        settle()

        # FlipRec
        tree = app.flip_tree
        columns = list(tree["columns"])
        check(columns.index("cost") == columns.index("roi") + 1, "FlipRec: it sits right after ROI")
        check(tree.heading("cost", "text").startswith("Upfront"), "FlipRec: called Upfront")
        fits(tree, "FlipRec")

        app.flip_format_var.set(gui.PRICE_FORMATS[1])
        app._on_price_format_changed()
        settle()
        shown = {row["item"]: row for row in table(tree, app.flip_id_map)}
        whip = next(r for r in app.flip_results if r["name"] == "Abyssal whip")
        check(shown["Abyssal whip"]["cost"] == f"{whip['buy'] * whip['qty']:,}", f"FlipRec: shows Buy x Qty: {shown['Abyssal whip']['cost']}")

        font = tkfont.Font(font=ttk.Style().lookup("Treeview", "font"))
        head = tkfont.Font(font=ttk.Style().lookup("Treeview.Heading", "font"))
        for cid, label, *_ in app.FLIP_COLUMNS:
            room = tree.column(cid, "width") - HEADER_PADDING
            check(head.measure(label) <= room, f"FlipRec: the {label} header fits: {head.measure(label)} of {room}")
        check(head.measure("Upfront ▼") <= tree.column("cost", "width") - HEADER_PADDING, "FlipRec: with room for the sort arrow")
        check(font.measure("133,125,702") + CELL_PADDING <= tree.column("buy", "width"), "FlipRec: a 9-digit Exact price fits")
        wide = max(font.measure(row["cost"]) for row in shown.values())
        check(wide + CELL_PADDING <= tree.column("cost", "width"), f"FlipRec: Exact amounts fit: {wide} of {tree.column('cost', 'width')}")

        heading = tree.heading("cost", "command")
        tree.tk.call(heading)
        settle()
        costs = [r["cost"] for r in app.flip_results]
        check(costs == sorted(costs, reverse=True), f"FlipRec: the header sorts biggest first: {costs}")
        tree.tk.call(heading)
        settle()
        costs = [r["cost"] for r in app.flip_results]
        check(costs == sorted(costs), "FlipRec: and again for smallest first")

        for r in app.flip_results:
            r["_qty"] = r["qty"]
            r["qty"] = 0
        app._render_fliprec_table()
        settle()
        check(all(row["cost"] == "-" for row in table(tree, app.flip_id_map)), "FlipRec: an unknown Qty shows - rather than 0")
        for r in app.flip_results:
            r["qty"] = r.pop("_qty")
        app._render_fliprec_table()

        # Alerts
        app.notebook.select(app.alerts_tab)
        settle()
        tree = app.alerts_tree
        columns = list(tree["columns"])
        check(columns.index("cost") == columns.index("roi") + 1, "Alerts: it sits right after ROI")
        fits(tree, "Alerts")
        alerts = db.get_active_alerts()
        check(alerts and all(a["cost"] == a["buy"] * a["qty"] for a in alerts), "Alerts: Buy x Qty")
        shown = table(tree, app.alert_id_map)
        check(shown and shown[0]["cost"] != "" and "," in shown[0]["cost"], f"Alerts: shown in the chosen format: {shown[0]['cost'] if shown else ''}")
        app._sort_alerts("cost", True)
        settle()
        order = [a["cost"] for a in sorted(db.get_active_alerts(), key=lambda a: a["cost"], reverse=True)]
        check([row["cost"] for row in table(tree, app.alert_id_map)] == [f"{c:,}" for c in order], "Alerts: the header sorts it")

        # Ask AI and Help
        tools = aitools.MarketTools(lambda: app.item_data, app._volume)
        tracker = next(fn for name, fn in vars(aitools.MarketTools).items() if "tracker" in name)
        state = tracker(tools, {}) if tracker.__code__.co_argcount > 1 else tracker(tools)
        check(all("total_cost" in a for a in state["active_alerts"]), "Ask AI: alerts include the total cost")
        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        check("Upfront — Buy × Qty" in helptext, "Help explains it")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL UPFRONT CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
