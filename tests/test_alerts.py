"""Alert lifecycle: the database rules, then the real Alerts tab with a controllable market."""
import os, sqlite3, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
from tkinter import ttk
from support import ImageGrab
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


# ── Migration from a v1.1.0 database that still holds an old alert ──
conn = sqlite3.connect(db.DB_NAME)
conn.executescript("""CREATE TABLE profit_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL,
  item_name TEXT NOT NULL, buy INTEGER, sell INTEGER, net INTEGER, qty INTEGER, profit INTEGER, roi REAL,
  timestamp INTEGER NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0);""")
conn.execute("INSERT INTO profit_alerts (item_id,item_name,buy,sell,net,qty,profit,roi,timestamp) VALUES (9,'Old thing',1,2,1,1,5000000,1.0,?)", (int(time.time()) - 3 * 86400,))
conn.commit()
conn.close()
db.initialize_db()
db.initialize_db()   # running it twice must be harmless
check([a["item_name"] for a in db.get_active_alerts()] == ["Old thing"], "old alert survives the migration")
db.expire_stale_alerts(300)
check(db.get_active_alerts() == [], "alert left over from days ago expires")


# ── Lifecycle rules, with a fake clock ──
def row(item=1, profit=2_000_000, buy=100):
    return {"item_id": item, "name": f"Item {item}", "buy": buy, "sell": 120, "net": 18, "qty": 100, "profit": profit, "roi": 18.0}


T = 1_000_000
HOUR = 3600
active = lambda: {a["item_id"]: a for a in db.get_active_alerts()}
conn = db.get_connection(); conn.execute("DELETE FROM profit_alerts"); conn.commit(); conn.close()

check(db.sync_profit_alerts([row()], HOUR, T) == (1, 0), "new alert")
a = active()[1]
check(a["timestamp"] == T and a["updated"] == T and a["n"] == 1, "new alert fields")

check(db.sync_profit_alerts([row(buy=105, profit=3_000_000)], HOUR, T + 60) == (0, 0), "still qualifying: nothing new")
a = active()[1]
check(a["buy"] == 105 and a["profit"] == 3_000_000 and a["updated"] == T + 60, "prices updated in place")
check(a["timestamp"] == T and a["n"] == 1, "Alerted time and Times unchanged by a refresh")

check(db.sync_profit_alerts([], HOUR, T + 120) == (0, 1), "stops qualifying: expires")
check(active() == {}, "expired alert not shown")
check(db.sync_profit_alerts([], HOUR, T + 180) == (0, 0), "expired alert only counted once")

check(db.sync_profit_alerts([row(buy=90)], HOUR, T + 240) == (0, 0), "back within the hour: not new")
a = active()[1]
check(a["buy"] == 90 and a["n"] == 1, "comes back quietly with live prices")

db.sync_profit_alerts([], HOUR, T + 300)
check(db.sync_profit_alerts([row()], HOUR, T + HOUR + 1) == (1, 0), "back after the hour: new alert")
a = active()[1]
check(a["n"] == 2 and a["timestamp"] == T + HOUR + 1, "Times counts the new alert")

db.dismiss_alerts(1)
check(active() == {}, "dismissed")
check(db.sync_profit_alerts([row()], HOUR, T + HOUR + 100) == (0, 0) and active() == {}, "dismissed stays hidden while the flip qualifies, within the hour")
check(db.sync_profit_alerts([], HOUR, T + HOUR + 160) == (0, 0), "dismissed isn't counted as expired")
check(db.sync_profit_alerts([row()], HOUR, T + 2 * HOUR + 2) == (1, 0), "dismissed item alerts again after the hour")

db.sync_profit_alerts([row(1), row(2), row(3)], HOUR, T + 2 * HOUR + 60)
check(set(active()) == {1, 2, 3}, "several alerts")
check(db.sync_profit_alerts([row(2)], HOUR, T + 2 * HOUR + 120) == (0, 2) and set(active()) == {2}, "only those that stopped qualifying expire")
conn = db.get_connection(); conn.execute("DELETE FROM profit_alerts"); conn.commit(); conn.close()

# Lots of qualifying flips stay fast
start = time.time()
db.sync_profit_alerts([row(i) for i in range(1, 1500)], HOUR, T)
first = time.time() - start
start = time.time()
db.sync_profit_alerts([row(i, buy=101) for i in range(1, 1500)], HOUR, T + 60)
second = time.time() - start
print(f"1,499 alerts: created {first*1000:.0f} ms, refreshed {second*1000:.0f} ms")
check(second < 0.5, "refreshing many alerts doesn't stall the app")
conn = db.get_connection(); conn.execute("DELETE FROM profit_alerts"); conn.commit(); conn.close()
print("db ok")

# ── Real Alerts tab ──
settings.save({"ua_contact": "gui-test", "capital": "100m"})
import api, scraper, gui, analyzer
now = time.time()
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}, {"id": 2, "name": "Lobster", "limit": 13000, "members": False},
           {"id": 3, "name": "Bandos tassets", "limit": 8, "members": True}]


def trade(high, low):
    t = time.time()
    return {"high": high, "low": low, "highTime": t - 30, "lowTime": t - 40}


MARKET = {"latest": {}, "bulk": {"1": {"avgHighPrice": 2_000_000, "avgLowPrice": 1_900_000, "highPriceVolume": 500, "lowPriceVolume": 400},
                                 "2": {"avgHighPrice": 200, "avgLowPrice": 180, "highPriceVolume": 50000, "lowPriceVolume": 40000},
                                 "3": {"avgHighPrice": 30_000_000, "avgLowPrice": 28_000_000, "highPriceVolume": 40, "lowPriceVolume": 40}}}
bulk_calls = []
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: MARKET["latest"]


def fake_bulk(step="1h"):
    if step == "5m":
        return {}
    bulk_calls.append(step)
    return MARKET["bulk"]


api.get_bulk_average = fake_bulk
scraper.scrape_all = lambda *a, **k: []
real_after = None
api.get_bulk_bucket = lambda step, start: {}   # no rolling history in this test
root = tk.Tk()
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


def rows():
    return [app.alerts_tree.item(i, "values") for i in app.alerts_tree.get_children() if i in app.alert_id_map]


def check_now():
    app._monitor_tick()
    settle()


def scenario():
    try:
        settle(lambda: app.item_data)
        app.notebook.select(app.alerts_tab)
        settle()
        check(app.alert_interval_var.get() == "1 min", "default refresh is 1 minute")
        check(list(gui.ALERT_INTERVALS.values()) == [60, 300, 900, 1800], "rates are 1, 5, 15 and 30 minutes")
        combo = next(w for w in app.alerts_tab.winfo_children()[0].winfo_children() if isinstance(w, ttk.Combobox) and w.cget("textvariable") == str(app.alert_interval_var))
        check(tuple(combo.cget("values")) == ("1 min", "5 min", "15 min", "30 min") and str(combo.cget("state")) == "readonly", "dropdown choices")

        # A big flip fires, and its numbers follow the market
        MARKET["latest"] = {"1": trade(2_100_000, 1_900_000), "2": trade(200, 198)}
        check_now()
        r = rows()
        check(len(r) == 1 and r[0][0] == "Abyssal whip", "flip fires an alert")
        check("(1)" in app.notebook.tab(app.alerts_tab, "text"), "tab count")
        check("1 new" in app.alerts_status.get() and "next check in 1 min" in app.alerts_status.get(), "status: " + app.alerts_status.get())
        profit_before = r[0][5]

        # Timer scheduled once at the chosen rate
        jobs = root.tk.call("after", "info")
        check(len([j for j in root.tk.splitlist(jobs) if j == app._monitor_job]) == 1, "next check is scheduled")

        # User selects it; a refresh with new prices keeps the selection
        tree = app.alerts_tree
        tree.selection_set(tree.get_children()[0])
        MARKET["latest"]["1"] = trade(2_300_000, 1_900_000)
        check_now()
        r = rows()
        check(r[0][5] != profit_before, f"price change shows up without a new alert ({profit_before} -> {r[0][5]})")
        check("0 new" in app.alerts_status.get() and r[0][10] == "1", "refresh isn't a new alert")
        check(len(tree.selection()) == 1 and app.alert_id_map[tree.selection()[0]] == 1, "selection survives the refresh")
        grab_shot("shot_alerts_live.png")

        # Margin closes: alert goes away by itself
        MARKET["latest"]["1"] = trade(1_950_000, 1_940_000)
        check_now()
        check(rows() == [] and "1 expired" in app.alerts_status.get(), "alert expires when the margin closes: " + app.alerts_status.get())
        check(app.notebook.tab(app.alerts_tab, "text") == "Alerts", "tab count clears")

        # Margin returns: back quietly, no flash
        flashes = []
        real_flash = gui.flash_window
        gui.flash_window = lambda r: flashes.append(1)
        MARKET["latest"]["1"] = trade(2_300_000, 1_900_000)
        check_now()
        check(len(rows()) == 1 and rows()[0][10] == "1" and not flashes, "returning flip comes back quietly")

        # Item stops trading: expires
        t = trade(2_300_000, 1_900_000)
        t["highTime"] = time.time() - 2000
        MARKET["latest"]["1"] = t
        check_now()
        check(rows() == [], "alert expires when a side stops trading")

        # A brand new item does flash
        MARKET["latest"] = {"1": trade(2_300_000, 1_900_000), "3": trade(30_500_000, 28_000_000)}
        check_now()
        check(flashes and any(x[0] == "Bandos tassets" for x in rows()), "new item flashes")
        gui.flash_window = real_flash

        # Threshold: raising hides, lowering brings back with no new alert
        before = {x[0]: x[10] for x in rows()}
        app.alert_threshold_var.set("100,000,000")
        app._on_alert_threshold_changed()
        check(rows() == [], "raising the threshold hides alerts")
        app.alert_threshold_var.set("1,000,000")
        app._on_alert_threshold_changed()
        check({x[0]: x[10] for x in rows()} == before, "lowering brings them back unchanged")

        # Capital change applies straight away
        app.flip_capital_var.set("1m")
        app._flip_raw = app._market
        app._apply_flip_filters()
        settle()
        check(rows() == [], "alerts follow a lower capital right away")
        app.flip_capital_var.set("100m")
        app._apply_flip_filters()
        settle()
        check(len(rows()) == 2, "and come back with more capital")

        # Failures must not wipe live alerts...
        good = rows()
        keep_latest = MARKET["latest"]
        MARKET["latest"] = {}
        check_now()
        check(rows() == good and "Check failed" in app.alerts_status.get() and "Alerts show prices from" in app.alerts_status.get(), "failed check keeps alerts and says so: " + app.alerts_status.get())
        # ...and neither must volumes failing with nothing to fall back on
        MARKET["latest"] = keep_latest
        keep_bulk = MARKET["bulk"]
        MARKET["bulk"] = {}
        app._volume._fallbacks.clear()
        check_now()
        check(rows() == good and "Check failed" in app.alerts_status.get(), "no volumes: alerts aren't all expired")
        # A FlipRec refresh that got no volumes can't expire them either
        app._market = (keep_latest, {}, time.time(), {})
        check(app._evaluate_alerts() == (0, 0) and rows() == good, "snapshot with no volumes is ignored")
        MARKET["bulk"] = keep_bulk
        app._volume._fallbacks.clear()

        # ...but alerts disappear once failures go on too long
        conn = db.get_connection()
        conn.execute("UPDATE profit_alerts SET updated = ?", (int(time.time()) - 301,))
        conn.commit(); conn.close()
        MARKET["latest"] = {}
        check_now()
        check(rows() == [], "alerts disappear when prices are too old to trust")
        MARKET["latest"] = keep_latest
        check_now()
        check(len(rows()) == 2, "and return when checks work again")

        # Dismiss
        tree.selection_set(tree.get_children()[0])
        app._dismiss_selected_alerts()
        check(len(rows()) == 1, "dismiss selected")
        check_now()
        check(len(rows()) == 1, "dismissed alert doesn't come back on the next check")

        # Changing the rate: saved, checks now, restarts the timer once
        old_job = app._monitor_job
        calls = []
        real_fetch = api.get_all_latest_prices
        api.get_all_latest_prices = lambda: (calls.append(1), real_fetch())[1]
        app.alert_interval_var.set("15 min")
        app._on_alert_interval_changed()
        settle()
        check(calls == [1], "changing the rate checks right away")
        check(settings.load()["alert_interval"] == "15 min" and "next check in 15 min" in app.alerts_status.get(), "rate saved and shown")
        check(app._monitor_job != old_job and str(old_job) not in root.tk.splitlist(root.tk.call("after", "info")), "old timer cancelled")
        check(str(app._monitor_job) in root.tk.splitlist(root.tk.call("after", "info")), "new timer running")
        check(app._alert_stale_after() == 1800, "stale limit follows the rate")
        app.alert_interval_var.set("1 min")
        check(app._alert_stale_after() == 300, "stale limit minimum")

        # Real timer really fires, at the set rate
        gui.ALERT_INTERVALS["1 min"] = 1
        calls.clear()
        app._monitor_tick()
        check(settle(lambda: len(calls) >= 3, 6), f"timer keeps firing by itself ({len(calls)} checks)")
        gui.ALERT_INTERVALS["1 min"] = 60

        # Watchlist keeps selection through a timed refresh
        db.watchlist_add(1, "Abyssal whip")
        db.watchlist_add(2, "Lobster")
        app._render_watchlist()
        wt = app.watch_tree
        wt.selection_set(wt.get_children()[1])
        selected = app.watch_id_map[wt.selection()[0]]
        check_now()
        check(wt.selection() and app.watch_id_map[wt.selection()[0]] == selected, "Watchlist selection survives a refresh")

        # Bad saved setting falls back
        check(all("5 minutes" not in body or "last" in body or "5-minute" in body or "after 5" in body for _, body in gui.HELP_SECTIONS), "Help no longer promises a 5-minute check")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL ALERT CHECKS PASSED", failures or "")
        root.destroy()


def grab_shot(name):
    root.update()
    time.sleep(0.3)
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab((x, y, x + root.winfo_width(), y + root.winfo_height()), all_screens=True).save(os.path.join(HERE, name))


root.after(300, scenario)
root.mainloop()
sys.exit(1 if failures else 0)
