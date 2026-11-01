"""Last trade everywhere, the 5-minute margin check, and the 5-minute trade limit."""
import os, re, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import tkinter as tk
from support import ImageGrab
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
settings.save({"ua_contact": "gui-test", "capital": "1b"})
import analyzer, api, scraper, gui, aitools
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


# ── The margin check on its own, using real numbers seen on the live market ──
V = {"id": 1, "name": "Voidwaker", "limit": 8}
now = 1_000_000.0
lt = {"high": 34_148_500, "low": 33_125_702, "highTime": now - 60, "lowTime": now - 100}
h1 = {"avgHighPrice": 34_000_000, "avgLowPrice": 33_300_000, "highPriceVolume": 50, "lowPriceVolume": 50}
good5 = {"avgHighPrice": 34_100_000, "avgLowPrice": 33_150_000, "highPriceVolume": 3, "lowPriceVolume": 3}
fake5 = {"avgHighPrice": 33_818_811, "avgLowPrice": 33_166_180, "highPriceVolume": 4, "lowPriceVolume": 1}

r = analyzer.flip_row(V, lt, h1, now=now)
check(r["verdict"] == "Profitable" and r["backed_5m"] and r["net_5m"] is None, "without 5m data nothing changes")
check(r["traded_at"] == int(now - 100) and r["age"] == 100, "traded_at is the older side")
r = analyzer.flip_row(V, lt, h1, now=now, avg_5m=good5)
check(r["verdict"] == "Profitable" and r["backed_5m"] and r["net_5m"] > 0, f"real margin stays Profitable (5m net {r['net_5m']:,})")
r = analyzer.flip_row(V, lt, h1, now=now, avg_5m=fake5)
check(r["verdict"] == "Suspect" and not r["backed_5m"], f"Voidwaker's fake margin is Suspect (latest {r['net']:,}, 5m {r['net_5m']:,})")

# One side only in the window
r = analyzer.flip_row(V, lt, h1, now=now, avg_5m={"avgHighPrice": 33_500_000, "avgLowPrice": None})
check(r["verdict"] == "Suspect", "one-sided 5m data can still expose a bad sell price")
r = analyzer.flip_row(V, lt, h1, now=now, avg_5m={"avgHighPrice": None, "avgLowPrice": 33_150_000})
check(r["verdict"] == "Profitable", "one-sided 5m data can confirm")
r = analyzer.flip_row(V, lt, h1, now=now, avg_5m={"avgHighPrice": None, "avgLowPrice": None})
check(r["net_5m"] is None and r["backed_5m"], "empty 5m entry counts as no data")

# Exactly at the 50% line, and tax on the 5m sell price
item = {"id": 2, "name": "Thing", "limit": 100}
lt2 = {"high": 1000, "low": 800, "highTime": now - 10, "lowTime": now - 10}   # net 1000-20-800 = 180
h2 = {"avgHighPrice": 1000, "avgLowPrice": 800, "highPriceVolume": 500, "lowPriceVolume": 500}
at = analyzer.flip_row(item, lt2, h2, now=now, avg_5m={"avgHighPrice": 910, "avgLowPrice": 800})  # 910-18-800 = 92
below = analyzer.flip_row(item, lt2, h2, now=now, avg_5m={"avgHighPrice": 900, "avgLowPrice": 810})  # 900-18-810 = 72
check(at["net_5m"] == 92 and at["backed_5m"], "over half the margin passes")
check(below["net_5m"] == 72 and not below["backed_5m"] and below["verdict"] == "Suspect", "under half fails")
exempt = analyzer.flip_row({"id": 3, "name": "Old school bond"}, lt2, h2, now=now, avg_5m={"avgHighPrice": 1000, "avgLowPrice": 800})
check(exempt["net_5m"] == 200, "tax-exempt items pay no tax in the check")

# An item with no margin isn't called Suspect for failing the check
lt3 = {"high": 1000, "low": 990, "highTime": now - 10, "lowTime": now - 10}
r = analyzer.flip_row(item, lt3, h2, now=now, avg_5m={"avgHighPrice": 900, "avgLowPrice": 950})
check(r["verdict"] == "Unprofitable", "unprofitable stays Unprofitable")

# The 5-minute line
check(analyzer.FLIP_SLOW_AGE == 300, "recently traded is 5 minutes")
slow = dict(lt2, highTime=now - 301)
ok = dict(lt2, highTime=now - 299)
check(analyzer.flip_row(item, slow, h2, now=now)["slow"] and not analyzer.flip_row(item, ok, h2, now=now)["slow"], "slow flag flips at 5 minutes")

# Display
check(gui.fmt_last_trade(None) == "-" and gui.fmt_last_trade(now - 45, now) == "45s" and gui.fmt_last_trade(now - 299, now) == "4m", "age text")
check(gui.fmt_last_trade(now - 301, now) == "⏱ 5m" and gui.fmt_last_trade(now + 5, now) == "0s", "old trade flag, clock skew safe")

# ── Real tabs ──
MAPPING = [{"id": 1, "name": "Voidwaker", "limit": 8, "members": True}, {"id": 4, "name": "Good item", "limit": 1000, "members": True},
           {"id": 5, "name": "Slow item", "limit": 1000, "members": True}]


def market(t=None):
    t = t or time.time()
    return {"1": {"high": 34_148_500, "low": 33_125_702, "highTime": t - 60, "lowTime": t - 100},
            "4": {"high": 110_000, "low": 100_000, "highTime": t - 20, "lowTime": t - 30},
            "5": {"high": 110_000, "low": 100_000, "highTime": t - 400, "lowTime": t - 30}}


H1 = {"1": h1, "4": {"avgHighPrice": 109_000, "avgLowPrice": 101_000, "highPriceVolume": 900, "lowPriceVolume": 900},
      "5": {"avgHighPrice": 109_000, "avgLowPrice": 101_000, "highPriceVolume": 900, "lowPriceVolume": 900}}
M5 = {"1": fake5, "4": {"avgHighPrice": 110_000, "avgLowPrice": 100_500, "highPriceVolume": 40, "lowPriceVolume": 30}}
calls = []
STATE = {"latest": market(), "m5": M5}
api.get_mapping = lambda: MAPPING


def fake_latest():
    calls.append("latest")
    return STATE["latest"]


def fake_bulk(step="1h"):
    calls.append(step)
    return STATE["m5"] if step == "5m" else H1


api.get_all_latest_prices = fake_latest
api.get_bulk_average = fake_bulk
api.get_latest_price = lambda i: STATE["latest"][str(i)]
api.get_timeseries = lambda i, timestep="1h": []
scraper.scrape_all = lambda *a, **k: []
api.get_bulk_bucket = lambda step, start: {}   # no rolling history in this test
root = tk.Tk()
root.attributes("-topmost", True)
app = gui.OSRSTrackerApp(root)


def settle(cond=lambda: True, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond() and not app._monitor_busy:
            root.update()
            return True
        time.sleep(0.01)
    return False


def table(tree, id_map):
    cols = tree["columns"]
    return {tree.set(i, "item"): {c: tree.set(i, c) for c in cols} for i in tree.get_children() if i in id_map}


def shot(name):
    root.update()
    time.sleep(0.3)
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height()), all_screens=True).save(os.path.join(HERE, name))


def scenario():
    try:
        settle(lambda: app.item_data)
        settle(lambda: app._market)
        app.notebook.select(app.alerts_tab)
        app._monitor_tick()
        settle()
        t = table(app.alerts_tree, app.alert_id_map)
        check(set(t) == {"Good item"}, f"alerts drop the fake margin and the item not traded in 5 min: {sorted(t)}")
        check(t["Good item"]["age"] in ("30s","31s","32s","33s"), "Alerts: Last trade column: " + t["Good item"]["age"])
        headers = [app.alerts_tree.heading(c, "text").replace(" ▼", "") for c in app.alerts_tree["columns"]]
        check("Last trade" in headers and headers.index("Last trade") == headers.index("Alerted") - 1, f"Alerts headers {headers}")
        shot("shot_trade_alerts.png")

        # Stored, and available to the AI
        a = db.get_active_alerts()[0]
        check(abs(time.time() - 30 - a["traded_at"]) < 5, "last trade time stored")
        tools = aitools.MarketTools(lambda: MAPPING)
        tracker = tools.run("get_my_tracker", {})
        check(abs(tracker["active_alerts"][0]["last_trade_age_seconds"] - 30) < 5, "AI sees the alert's last trade")
        flips = {f["name"]: f for f in tools.run("get_flips", {})["flips"]}
        check(flips["Voidwaker"]["verdict"] == "Suspect" and flips["Voidwaker"]["margin_backed_by_5m_avg"] is False, "AI sees the failed margin check")
        check(flips["Good item"]["margin_at_5m_avg_prices"] > 0, "AI sees the 5m margin")

        # Age keeps counting between checks, without new data
        conn = db.get_connection(); conn.execute("UPDATE profit_alerts SET traded_at = traded_at - 600"); conn.commit(); conn.close()
        app._render_alerts()
        check(table(app.alerts_tree, app.alert_id_map)["Good item"]["age"].startswith("⏱ 10m"), "old trade shows the ⏱ flag")
        conn = db.get_connection(); conn.execute("UPDATE profit_alerts SET traded_at = traded_at + 600"); conn.commit(); conn.close()
        app._render_alerts()
        ages = app._trade_ages[app.alerts_tree]
        iid = next(iter(ages))
        ages[iid] -= 300
        selected_before = app.alerts_tree.selection()
        app._tick_trade_ages()
        check(app.alerts_tree.set(iid, "age").startswith("⏱ 5m"), "ticker counts the age up without a redraw")
        check(app.alerts_tree.selection() == selected_before, "ticker leaves the selection alone")

        # Sort by last trade: newest first on first click
        app._sort_alerts("traded_at", True)
        check("▼" in app.alerts_tree.heading("age", "text"), "Last trade column sorts")
        app._sort_alerts("timestamp", True)

        # Failed margin check goes away if the margin firms up, and vice versa
        STATE["m5"] = {}
        app._volume._fallbacks.clear()
        app._monitor_tick()
        settle()
        check(set(table(app.alerts_tree, app.alert_id_map)) == {"Good item", "Voidwaker"}, "no 5m data: margin isn't marked down")
        STATE["m5"] = M5
        app._volume._fallbacks.clear()
        app._monitor_tick()
        settle()
        t = table(app.alerts_tree, app.alert_id_map)
        check(set(t) == {"Good item"} and "1 expired" in app.alerts_status.get(), "margin losing 5m support expires its alert: " + app.alerts_status.get())

        # FlipRec shows why
        app._refresh_fliprec()
        settle(lambda: app.flip_refresh_btn.instate(["!disabled"]))
        app._apply_flip_filters()
        settle()
        f = table(app.flip_tree, app.flip_id_map)
        check(f["Voidwaker"]["verdict"] == "Suspect" and f["Voidwaker"]["signal"].startswith("5m margin only"), "FlipRec explains: " + f["Voidwaker"]["signal"])
        check(f["Slow item"]["age"].startswith("⏱ 6m"), "FlipRec flags trades over 5 min: " + f["Slow item"]["age"])
        check("not traded in the last 5 min" in app.flip_banner.get(), "FlipRec banner says 5 min")
        app.notebook.select(app.fliprec_tab)
        shot("shot_trade_fliprec.png")

        # Watchlist
        db.watchlist_add(4, "Good item")
        db.watchlist_add(5, "Slow item")
        app._render_watchlist()
        w = table(app.watch_tree, app.watch_id_map)
        check(w["Good item"]["age"] in ("30s","31s","32s","33s","34s") and w["Slow item"]["age"].startswith("⏱ 6m"), f"Watchlist Last trade: {w['Good item']['age']}, {w['Slow item']['age']}")
        check(w["Good item"]["verdict"] == "Profitable", "Watchlist uses the margin check")

        # Search
        app._lookup_item(MAPPING[1])
        settle(lambda: app._search_trade and app._search_trade[0].winfo_exists(), 5)
        label = app._search_trade[0]
        check(re.fullmatch(r"3[0-5]s ago  [(]high 2[0-5]s, low 3[0-5]s[)]", label.cget("text")) is not None, "Search: " + label.cget("text"))
        check(label.cget("fg") == gui.COLOR_TEXT, "fresh trade isn't highlighted")
        app._search_trade = (label, app._search_trade[1] - 400, app._search_trade[2])
        app._tick_trade_ages()
        check(label.cget("text").startswith("⏱ 7m ago") and label.cget("fg") == gui.COLOR_YELLOW, "old trade counts up and highlights: " + label.cget("text"))
        app.notebook.select(app.search_tab)
        shot("shot_trade_search.png")
        app._search_trade = (label, None, 5)
        app._show_search_trade()
        check(label.cget("text") == "Unknown", "missing trade time doesn't crash")
        app._render_message("gone")
        root.update()
        app._search_trade = (label, 1, 2)
        app._tick_trade_ages()   # label destroyed: must not crash
        check(True, "ticker survives a cleared Search panel")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL TRADE CHECKS PASSED", failures or "")
        root.destroy()


root.after(300, scenario)
root.mainloop()
sys.exit(1 if failures else 0)
