"""Alert lifecycle: the database rules, then FlipRec's Alerts view with a controllable market."""
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

# Nothing can dismiss an alert now, but one dismissed before the update still counts.
conn = db.get_connection(); conn.execute("UPDATE profit_alerts SET dismissed = 1 WHERE item_id = 1"); conn.commit(); conn.close()
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

# ── The real app ──
# flip_verdict is what v1.4 saved: it must not decide what FlipRec opens on, and
# column_order's "alerts" must be left alone, so going back to v1.4 still works.
settings.save({"ua_contact": "gui-test", "capital": "100m", "flip_verdict": "All verdicts",
               "column_order": {"alerts": ["item", "age"]}})
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
    return [app.flip_tree.item(i, "values") for i in app.flip_tree.get_children() if i in app.flip_id_map]


def times():
    return {a["item_name"]: a["n"] for a in db.get_active_alerts()}


def col(name):
    """Where a column is, so adding one doesn't move what these checks read."""
    return next(i for i, c in enumerate(gui.OSRSTrackerApp.FLIP_COLUMNS) if c[0] == name)


def steady(table):
    """Rows without Last trade, whose age ticks up by itself every second."""
    age = col("age")
    return [row[:age] + row[age + 1:] for row in table]


def check_now():
    app._refresh_market()
    settle()


def scenario():
    try:
        settle(lambda: app.item_data)
        tabs = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        check(tabs[0].startswith("FlipRec") and not any(t.startswith("Alerts") for t in tabs), f"one tab for flips and alerts: {tabs}")
        check(app.notebook.select() == str(app.fliprec_tab), "the app opens on it")
        check(app.flip_show_var.get() == "Alerts", "showing Alerts, whatever v1.4 saved")
        check(not hasattr(app, "alerts_tree"), "the Alerts table has gone")
        def texts(widget):
            found = []
            for child in widget.winfo_children():
                if child.winfo_class() in ("TButton", "TLabel"):
                    found.append(child.cget("text"))
                found += texts(child)
            return found
        words = texts(app.fliprec_tab)
        check("Alert when est. profit ≥" in words, "the threshold is on FlipRec")
        check(not any("Dismiss" in str(w) for w in texts(root)), "the Dismiss buttons have gone")
        columns = list(app.flip_tree["columns"])
        check("when" not in columns and "hits" not in columns, f"Alerted and Times are off the screen: {columns}")
        check(app.alert_interval_var.get() == "1 min", "default refresh is 1 minute")
        check(list(gui.ALERT_INTERVALS.values()) == [60, 300, 900, 1800], "rates are 1, 5, 15 and 30 minutes")
        filters = app.refresh_btn.master.master.winfo_children()[0]
        combo = next(w for w in filters.winfo_children() if isinstance(w, ttk.Combobox) and w.cget("textvariable") == str(app.alert_interval_var))
        check(tuple(combo.cget("values")) == ("1 min", "5 min", "15 min", "30 min") and str(combo.cget("state")) == "readonly", "dropdown choices")

        # A big flip fires, and its numbers follow the market
        MARKET["latest"] = {"1": trade(2_100_000, 1_900_000), "2": trade(200, 198)}
        check_now()
        r = rows()
        check(len(r) == 1 and r[0][0] == "Abyssal whip", "flip fires an alert")
        check(app.notebook.tab(app.fliprec_tab, "text") == "FlipRec (1)", "tab count")
        check("1 new" in app.market_status.get() and "next check in 1 min" in app.market_status.get(), "status: " + app.market_status.get())
        profit_before = r[0][col("profit")]

        # Timer scheduled once at the chosen rate
        jobs = root.tk.call("after", "info")
        check(len([j for j in root.tk.splitlist(jobs) if j == app._monitor_job]) == 1, "next check is scheduled")

        # User selects it; a refresh with new prices keeps the selection
        tree = app.flip_tree
        tree.selection_set(tree.get_children()[0])
        MARKET["latest"]["1"] = trade(2_300_000, 1_900_000)
        check_now()
        r = rows()
        check(r[0][col("profit")] != profit_before, f"price change shows up without a new alert ({profit_before} -> {r[0][col("profit")]})")
        check("0 new" in app.market_status.get() and times()["Abyssal whip"] == 1, "refresh isn't a new alert")
        check(len(tree.selection()) == 1 and app.flip_id_map[tree.selection()[0]] == 1, "selection survives the refresh")
        grab_shot("shot_alerts_live.png")

        # Margin closes: alert goes away by itself
        MARKET["latest"]["1"] = trade(1_950_000, 1_940_000)
        check_now()
        check(rows() == [] and "1 expired" in app.market_status.get(), "alert expires when the margin closes: " + app.market_status.get())
        check(app.notebook.tab(app.fliprec_tab, "text") == "FlipRec", "tab count clears")
        check("No flips at or above 1.00M gp" in app.flip_banner.get(), f"and it says why it's empty: {app.flip_banner.get()}")

        # The other views still have everything, and the count doesn't change.
        # This margin is real, but the flip makes under 1M.
        MARKET["latest"]["1"] = trade(1_950_000, 1_900_000)
        check_now()
        check(rows() == [], "a smaller flip doesn't alert")
        app.flip_show_var.set("All verdicts")
        app._apply_flip_filters()
        settle()
        check(any(x[0] == "Abyssal whip" for x in rows()), f"All verdicts shows flips that don't alert: {[x[0] for x in rows()]}")
        check(app.notebook.tab(app.fliprec_tab, "text") == "FlipRec", "without counting them")
        check(settings.load()["flip_show"] == "All verdicts", "Show is saved")
        check(settings.load()["flip_verdict"] == "All verdicts", "and v1.4's setting isn't touched")
        app.flip_show_var.set("Alerts")
        app._apply_flip_filters()
        settle()
        check(rows() == [], "back to Alerts")

        # Margin returns: back quietly, no flash
        flashes = []
        real_flash = gui.flash_window
        gui.flash_window = lambda r: flashes.append(1)
        MARKET["latest"]["1"] = trade(2_300_000, 1_900_000)
        check_now()
        check(len(rows()) == 1 and times()["Abyssal whip"] == 1 and not flashes, "returning flip comes back quietly")

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
        before = {x[0] for x in rows()}
        counts = times()
        app.alert_threshold_var.set("100,000,000")
        app._on_alert_threshold_changed()
        check(rows() == [], "raising the threshold hides alerts")
        app.alert_threshold_var.set("1,000,000")
        app._on_alert_threshold_changed()
        check({x[0] for x in rows()} == before and times() == counts, "lowering brings them back unchanged")

        # The tab count follows the threshold, whatever Show is set to
        check(app.notebook.tab(app.fliprec_tab, "text") == f"FlipRec ({len(before)})", app.notebook.tab(app.fliprec_tab, "text"))
        app.flip_show_var.set("Profitable")
        app._apply_flip_filters()
        settle()
        app.alert_threshold_var.set("100,000,000")
        app._on_alert_threshold_changed()
        check(app.notebook.tab(app.fliprec_tab, "text") == "FlipRec", "the count follows the threshold in any view")
        app.alert_threshold_var.set("1,000,000")
        app._on_alert_threshold_changed()
        app.flip_show_var.set("Alerts")
        app._apply_flip_filters()
        settle()

        # Your sort stays, in either view
        app._sort_fliprec("name", False)
        check([x[0] for x in rows()] == sorted(x[0] for x in rows()), "alerts sort like every other row")
        check_now()
        check(app.flip_sort == ("name", False), "and stay sorted through a check")
        app.flip_sort = (None, None)
        app._update_flip_arrows()
        check_now()

        # Capital change applies straight away
        app.flip_capital_var.set("1m")
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
        check(steady(rows()) == steady(good) and "Check failed" in app.market_status.get() and "Prices shown are from" in app.market_status.get(), "failed check keeps alerts and says so: " + app.market_status.get())
        # ...and neither must volumes failing with nothing to fall back on
        MARKET["latest"] = keep_latest
        keep_bulk = MARKET["bulk"]
        MARKET["bulk"] = {}
        app._volume._fallbacks.clear()
        check_now()
        check(steady(rows()) == steady(good) and "Check failed" in app.market_status.get(), "no volumes: alerts aren't all expired")
        # A FlipRec refresh that got no volumes can't expire them either
        app._market = (keep_latest, {}, time.time(), {})
        check(app._evaluate_alerts() == (0, 0) and steady(rows()) == steady(good), "snapshot with no volumes is ignored")
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

        # Ask AI: how often items alert, for suggesting the Watchlist
        import aitools
        tools = aitools.MarketTools(lambda: app.item_data, app._volume)
        conn = db.get_connection()
        for hours in (3, 9, 30):
            conn.execute("INSERT INTO profit_alerts (buy, sell, net, qty, profit, roi, item_id, item_name, timestamp, expired) "
                         "VALUES (1, 2, 1, 1, 1, 1, 2, 'Lobster', ?, 1)", (int(time.time()) - hours * 3600,))
        conn.commit(); conn.close()
        history = tools.run("get_alert_history", {})
        items = {h["name"]: h for h in history["items"]}
        check(history["days_kept"] == 7, "it says how far back it goes")
        check(history["items"][0]["name"] == "Lobster" and items["Lobster"]["times_alerted"] == 3, f"most alerted first: {history['items'][:1]}")
        check(items["Lobster"]["alerting_now"] is False and items["Lobster"]["last_alerted_hours_ago"] > 2, "past alerts count, and aren't called live")
        check(items["Abyssal whip"]["alerting_now"] is True, "live ones are marked")
        check(items["Lobster"]["on_watchlist"] is False, "not on the Watchlist yet")
        db.watchlist_add(2, "Lobster")
        check(tools.run("get_alert_history", {})["items"][0]["on_watchlist"] is True, "says when it's on the Watchlist")
        db.watchlist_remove(2)
        db.ignore_items([(2, "Lobster")])
        check(all(h["name"] != "Lobster" for h in tools.run("get_alert_history", {})["items"]), "leaves out ignored items")
        db.unignore_items([2])
        check(len(tools.run("get_alert_history", {"limit": 1})["items"]) == 1, "honors its limit")
        check("error" not in tools.run("get_alert_history", {"limit": "lots"}), "a bad limit isn't an error")
        tracker = tools.run("get_my_tracker", {})
        check(all(a["times_alerted"] >= 1 for a in tracker["active_alerts"]), "live alerts include Times")
        import ai
        check("get_alert_history" in ai.SYSTEM_PROMPT, "the AI is told what it's for")
        conn = db.get_connection(); conn.execute("DELETE FROM profit_alerts WHERE item_id = 2"); conn.commit(); conn.close()
        check(settings.load()["column_order"].get("alerts") == ["item", "age"], "v1.4's saved Alerts column order is left alone")

        # Changing the rate: saved, checks now, restarts the timer once
        old_job = app._monitor_job
        calls = []
        real_fetch = api.get_all_latest_prices
        api.get_all_latest_prices = lambda: (calls.append(1), real_fetch())[1]
        app.alert_interval_var.set("15 min")
        app._on_alert_interval_changed()
        settle()
        check(calls == [1], "changing the rate checks right away")
        check(settings.load()["alert_interval"] == "15 min" and "next check in 15 min" in app.market_status.get(), "rate saved and shown")
        check(app._monitor_job != old_job and str(old_job) not in root.tk.splitlist(root.tk.call("after", "info")), "old timer cancelled")
        check(str(app._monitor_job) in root.tk.splitlist(root.tk.call("after", "info")), "new timer running")
        check(app._alert_stale_after() == 1800, "stale limit follows the rate")
        app.alert_interval_var.set("1 min")
        check(app._alert_stale_after() == 300, "stale limit minimum")

        # Real timer really fires, at the set rate
        gui.ALERT_INTERVALS["1 min"] = 1
        calls.clear()
        app._refresh_market()
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

        # A bad saved setting falls back
        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        for stale in ("Dismiss", "On Alerts", "\"Times\"", "Alerts (3)", "FlipRec · Alerts"):
            check(stale not in helptext, f"Help no longer says: {stale}")
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
