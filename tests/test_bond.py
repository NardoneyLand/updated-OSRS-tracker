"""Old school bonds: kept off FlipRec, Volatile, alerts and Ask AI, with the price in the header."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
settings.save({"ua_contact": "test-contact", "alert_threshold": 1, "flip_show": "All verdicts"})
import aitools, analyzer, api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


BOND = analyzer.BOND_ID
MAPPING = [{"id": BOND, "name": "Old school bond", "limit": 100, "members": False},
           {"id": 2, "name": "Lobster", "limit": 6000, "members": False}]
now = time.time()
# The bond's margin is big and tax-free, so it would top FlipRec and alert.
LATEST = {str(BOND): {"high": 12_450_000, "low": 12_390_000, "highTime": now - 20, "lowTime": now - 30},
          "2": {"high": 300, "low": 260, "highTime": now - 20, "lowTime": now - 30}}
HOUR = {str(BOND): {"highPriceVolume": 5000, "lowPriceVolume": 5000, "avgHighPrice": 12_440_000, "avgLowPrice": 12_380_000},
        "2": {"highPriceVolume": 5000, "lowPriceVolume": 4000, "avgHighPrice": 290, "avgLowPrice": 250}}
# Both moved a lot against this average, so both would be big movers.
MOVED = {k: {**v, "avgHighPrice": v["avgHighPrice"] // 2, "avgLowPrice": v["avgLowPrice"] // 2} for k, v in HOUR.items()}

# The scans everything else uses (FlipRec, alerts, Volatile and Ask AI's tools)
check(analyzer.flip_row(MAPPING[0], LATEST[str(BOND)], HOUR[str(BOND)], now=now)["net"] > 0, "the bond really is a profitable flip")
flips = analyzer.find_flips(LATEST, HOUR, MAPPING, now=now)
check([r["item_id"] for r in flips] == [2], f"find_flips leaves the bond out: {[r['item_id'] for r in flips]}")
movers = analyzer.find_volatile_items(LATEST, MOVED, MAPPING, min_volume=1)
check([r["item_id"] for r in movers] == [2], f"find_volatile_items leaves it out: {[r['item_id'] for r in movers]}")

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


def scenario():
    try:
        settle(lambda: bool(app.item_data), 10)
        settle(lambda: app._market is not None and not app._monitor_busy, 5)
        settle()

        check(BOND not in app.flip_id_map.values() and 2 in app.flip_id_map.values(), "FlipRec doesn't list it")
        check(BOND not in [a["item_id"] for a in db.get_active_alerts()], "it doesn't alert")
        check(db.ignored_items() == [], "it isn't added to the Ignored List")
        tools = aitools.MarketTools(lambda: MAPPING)
        names = [f["name"] for f in tools.run("get_flips", {}).get("flips", [])]
        check(names and "Old school bond" not in names, f"Ask AI isn't offered it as a flip: {names}")

        check(app.bond_var.get() == "Bond  buy 12.39M · sell 12.45M", f"the header shows its price: {app.bond_var.get()!r}")
        app.flip_format_var.set(gui.PRICE_FORMATS[1])
        app._on_price_format_changed()
        check(app.bond_var.get() == "Bond  buy 12,390,000 · sell 12,450,000", f"in the chosen price format: {app.bond_var.get()!r}")

        LATEST[str(BOND)] = {**LATEST[str(BOND)], "high": 12_600_000}
        app._refresh_market()
        settle(lambda: not app._monitor_busy, 5)
        settle()
        check(app.bond_var.get().endswith("sell 12,600,000"), f"and it follows each check: {app.bond_var.get()!r}")

        del LATEST[str(BOND)]
        app._refresh_market()
        settle(lambda: not app._monitor_busy, 5)
        settle()
        check(app.bond_var.get() == "", f"with no price it shows nothing: {app.bond_var.get()!r}")

        # The Watchlist still shows it, like an ignored item
        LATEST[str(BOND)] = {"high": 12_450_000, "low": 12_390_000, "highTime": now - 20, "lowTime": now - 30}
        db.watchlist_add(BOND, "Old school bond")
        app._refresh_market()
        settle(lambda: not app._monitor_busy, 5)
        app._render_watchlist()
        settle()
        check(BOND in app.watch_id_map.values(), "the Watchlist still shows it")
        tag = [app.watch_tree.set(i, "item") for i, v in app.watch_id_map.items() if v == BOND]
        check(tag == ["Old school bond"], f"without an Ignored tag: {tag}")

        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        check("Old school bonds never" in helptext, "Help explains where it went")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL BOND CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
