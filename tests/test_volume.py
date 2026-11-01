"""Rolling windows: exact sums with a fake Wiki, restart behaviour, gaps, and the real tabs."""
import os, sys, tempfile, threading, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
from support import ImageGrab
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
db.initialize_db()
import analyzer, api, volume, gui, aitools, scraper
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


volume.BACKFILL_PAUSE = 0
real_time = time.time
CLOCK = [1_793_553_780.0]
time.time = lambda: CLOCK[0]


def aligned(size):
    now = int(CLOCK[0])
    return now - now % size


# Fake Wiki: every full hour trades 100/50 units of item 1, every 5 minutes 10/5.
# Bucket number is encoded in price so sums can be checked.
requests_made = []
NOT_READY = set()
FAIL = [False]


def fake_bucket(step, start):
    requests_made.append((step, start))
    if FAIL[0]:
        raise OSError("network down")
    size = volume.STEP_SECONDS[step]
    if start + size > int(CLOCK[0]) or (step, start) in NOT_READY:
        return {}
    if step == "5m":
        return {"1": {"highPriceVolume": 10, "lowPriceVolume": 5, "avgHighPrice": 1000, "avgLowPrice": 900},
                "2": {"highPriceVolume": 0, "lowPriceVolume": 7, "avgHighPrice": None, "avgLowPrice": 50}}
    return {"1": {"highPriceVolume": 100, "lowPriceVolume": 50, "avgHighPrice": 2000, "avgLowPrice": 1800}}


api.get_bulk_bucket = fake_bucket
h = volume.VolumeHistory()
check(h.view(3600) is None and h.sides(1, 3600) is None, "nothing ready before any data")
h.update()
print("first run requests:", len(requests_made), "progress", h.progress())
check(sum(1 for s, _ in requests_made if s == "5m") == 26 and sum(1 for s, _ in requests_made if s == "1h") == 25, "first run asks for each period once")
check(h.progress() == (51, 51), "all periods held")
check(h.ready_windows() == {300, 3600, 6*3600, 12*3600, 24*3600}, f"all windows ready: {sorted(h.ready_windows())}")

# 1h = exactly 12 five-minute periods
check(h.sides(1, 3600) == (120, 60), f"1h is 12 x 5m: {h.sides(1, 3600)}")
check(h.sides(1, 300) == (10, 5), "newest 5m view")
# Five-minute periods completed since the newest full hour ended.
tail = int(CLOCK[0]) % 3600 // 300
expected_hours = round((12 * 3600 - tail * 300) / 3600)
check(h.sides(1, 12 * 3600) == (expected_hours * 100 + tail * 10, expected_hours * 50 + tail * 5), f"12h = {expected_hours} hours + {tail} x 5m: {h.sides(1, 12*3600)}")
check(h.sides(1, 24 * 3600) == (round((24*3600 - tail*300)/3600) * 100 + tail * 10, round((24*3600 - tail*300)/3600) * 50 + tail * 5), f"24h: {h.sides(1, 24*3600)}")
check(h.sides(99, 3600) == (0, 0), "item with no trades in a ready window is zero")
check(h.slower_side(1, 3600) == 60, "slower side")

# Same shape as the Wiki, with volume-weighted prices
v = h.view(3600)["1"]
check(set(v) == {"avgHighPrice", "avgLowPrice", "highPriceVolume", "lowPriceVolume"} and v["avgHighPrice"] == 1000 and v["avgLowPrice"] == 900, f"view has the Wiki's shape: {v}")
v2 = h.view(3600)["2"]
check(v2["avgHighPrice"] is None and v2["highPriceVolume"] == 0 and v2["lowPriceVolume"] == 84, "side with no trades has no average")
w = volume._merge([{"1": (10, 1, 100, 50)}, {"1": (30, 3, 200, 90)}])["1"]
check(w["avgHighPrice"] == 175 and w["avgLowPrice"] == 80, f"averages weighted by volume: {w}")

# Works with flip_row
item = {"id": 1, "name": "X", "limit": 100}
lt = {"high": 1100, "low": 900, "highTime": CLOCK[0] - 10, "lowTime": CLOCK[0] - 10}
r = analyzer.flip_row(item, lt, h.view(3600)["1"], now=CLOCK[0], avg_5m=h.view(300)["1"])
check(r["volume"] == 60 and r["active"], "flip_row reads a rolling view")

# Next update with nothing new: no requests
requests_made.clear()
h.update()
check(requests_made == [], "no requests when nothing is due")

# Time moves on 5 minutes: exactly one small request, old period dropped
CLOCK[0] += 300
requests_made.clear()
h.update()
check(requests_made == [("5m", aligned(300) - 300)], f"one request per new 5m period: {requests_made}")
check(len(h._buckets["5m"]) == 26, "old 5m period pruned")

# Restart: loads from disk, fetches only what's missing
h2 = volume.VolumeHistory()
h2._load()
requests_made.clear()
h2.update()
check(requests_made == [], "restart reuses saved periods")
check(h2.sides(1, 12 * 3600) == h.sides(1, 12 * 3600), "same numbers after restart")
conn = db.get_connection()
check(conn.execute("SELECT COUNT(*) FROM volume_buckets").fetchone()[0] == 51, "database keeps only what's needed")
conn.close()

# Long gap: restart after 2 hours away
CLOCK[0] += 2 * 3600
h3 = volume.VolumeHistory()
h3._load()
check(h3.view(3600) is None, "stale 5m data isn't called the last hour")
requests_made.clear()
h3.update()
check(sum(1 for s, _ in requests_made if s == "5m") == 24 and sum(1 for s, _ in requests_made if s == "1h") == 2, f"catches up on just the gap: {len(requests_made)}")
check(h3.view(3600) is not None and h3.view(24 * 3600) is not None, "windows ready again")

# Newest period not published yet: older numbers still work, retried next time
CLOCK[0] += 3600
newest_hour = aligned(3600) - 3600
NOT_READY.add(("1h", newest_hour))
requests_made.clear()
h3.update()
check(newest_hour not in h3._buckets["1h"] and h3.view(24 * 3600) is not None, "hour not published yet: window still works")
NOT_READY.clear()
h3.update()
check(newest_hour in h3._buckets["1h"], "picked up on the next pass")

# A hole in the middle: window says not ready instead of undercounting
hole = volume.VolumeHistory()
now_hour = aligned(3600)
hole._buckets["1h"] = {now_hour - k * 3600: {"1": (1, 1, 10, 10)} for k in range(1, 26) if k != 5}
hole._rebuild()
check(hole.view(6 * 3600) is None and hole.view(24 * 3600) is None, "hole in the data: window not shown rather than undercounted")
hole2 = volume.VolumeHistory()
now5 = aligned(300)
hole2._buckets["5m"] = {now5 - k * 300: {"1": (1, 1, 10, 10)} for k in range(1, 15) if k != 3}
hole2._rebuild()
check(hole2.view(3600) is None and hole2.view(300) is not None, "hole in 5m data: 1h not shown")

# Fails during backfill: keeps what it has, doesn't crash the thread's caller contract
f = volume.VolumeHistory()
FAIL[0] = True
try:
    f.update()
    check(False, "network failure surfaces to the loop")
except OSError:
    check(True, "network failure surfaces to the loop, which logs and retries")
FAIL[0] = False

# Corrupt saved row doesn't break launch
db.save_volume_bucket("5m", 1, "{not json")
c = volume.VolumeHistory()
c._load()
check(True, "corrupt saved data doesn't crash")
conn = db.get_connection(); conn.execute("DELETE FROM volume_buckets WHERE start = 1"); conn.commit(); conn.close()

# Pace arrows
p = analyzer.volume_pace
check(p(100, 2400) == "" and p(300, 2400) == "▲" and p(299, 2400) == "" and p(49, 2400) == "▼" and p(50, 2400) == "", "arrows at 3x and under 0.5x")
check(p(100, None) == "" and p(100, 0) == "", "no arrow without a 24h number")
check(p(5, 24) == "" and p(9, 24) == "" and p(0, 48) == "", "very quiet items get no arrow")
print("history ok")

time.time = real_time

# ── api fix: asks for the newest full period by time ──
real_get = api.SESSION.get
urls = []


class R:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return {"data": self.data}


api.get_bulk_bucket = volume.api.get_bulk_bucket = None
import importlib
importlib.reload(api)
importlib.reload(volume)
volume.BACKFILL_PAUSE = 0
import useragent
useragent.set_contact("gui-test")


def fake_get(url, **k):
    urls.append(url)
    return R({} if "timestamp" in url and "PUBLISHED" not in url and len(urls) == 1 and SKIP[0] else {"1": {}})


SKIP = [False]
api.SESSION.get = fake_get
assert api.get_bulk_average("1h") == {"1": {}}
now = int(time.time())
check(urls == [f"{api.API_BASE}/1h?timestamp={now - now % 3600 - 3600}"], f"1h asks for newest full hour: {urls[0][-30:]}")
urls.clear(); SKIP[0] = True
api.get_bulk_average("24h")
check(len(urls) == 2 and f"timestamp={now - now % 86400 - 2 * 86400}" in urls[1], "24h steps back one if not published")
SKIP[0] = False; urls.clear()
api.get_bulk_average("5m")
check(urls == [f"{api.API_BASE}/5m"], "5m uses the plain address")
api.SESSION.get = real_get

# ── Real tabs ──
now = time.time()
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}, {"id": 2, "name": "Lobster", "limit": 13000, "members": False},
           {"id": 3, "name": "Quiet thing", "limit": 100, "members": True}]


def lt_(h, l):
    t = time.time()
    return {"high": h, "low": l, "highTime": t - 30, "lowTime": t - 40}


LATEST = {"1": lt_(2_100_000, 1_900_000), "2": lt_(200, 180), "3": lt_(1000, 900)}


def bucket(step, start):
    if start + volume.STEP_SECONDS[step] > int(time.time()):
        return {}
    if step == "5m":
        return {"1": {"highPriceVolume": 40, "lowPriceVolume": 30, "avgHighPrice": 2_090_000, "avgLowPrice": 1_910_000},
                "2": {"highPriceVolume": 900, "lowPriceVolume": 800, "avgHighPrice": 199, "avgLowPrice": 181},
                "3": {"highPriceVolume": 1, "lowPriceVolume": 1, "avgHighPrice": 1000, "avgLowPrice": 900}}
    # hourly: whip is 3x+ busier now than usual; lobster much quieter now than usual
    return {"1": {"highPriceVolume": 60, "lowPriceVolume": 50, "avgHighPrice": 2_090_000, "avgLowPrice": 1_910_000},
            "2": {"highPriceVolume": 90000, "lowPriceVolume": 80000, "avgHighPrice": 199, "avgLowPrice": 181},
            "3": {"highPriceVolume": 12, "lowPriceVolume": 12, "avgHighPrice": 1000, "avgLowPrice": 900}}


GATE = threading.Event()   # holds the backfill so the "loading" state can be seen


def gated_bucket(step, start):
    GATE.wait(20)
    return bucket(step, start)


conn = db.get_connection(); conn.execute("DELETE FROM volume_buckets"); conn.commit(); conn.close()
settings.save({"ua_contact": "gui-test", "capital": "1b", "alert_threshold": 100_000})
api.get_mapping = lambda: MAPPING
api.get_all_latest_prices = lambda: LATEST
api.get_latest_price = lambda i: LATEST[str(i)]
api.get_timeseries = lambda i, timestep="1h": []
api.get_bulk_bucket = gated_bucket
fallback_calls = []


def fallback(step="1h"):
    fallback_calls.append(step)
    return {} if step == "5m" else bucket("1h", 0)


api.get_bulk_average = fallback
scraper.scrape_all = lambda *a, **k: []
root = tk.Tk()
app = gui.OSRSTrackerApp(root)
gui.volume.BACKFILL_PAUSE = 0
app._volume.__class__.__module__


def settle(cond=lambda: True, seconds=15):
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


def search_table():
    frame = app._search_volume[0]
    return {(w.grid_info()["row"], w.grid_info()["column"]): w.cget("text") for w in frame.winfo_children()}


def scenario():
    try:
        settle(lambda: app.item_data)
        settle(lambda: app._market)
        # Before history: tables still work on the fallback, windows blank
        check("1h" in fallback_calls, "falls back to the Wiki's newest full hour until history is ready")
        db.watchlist_add(1, "Abyssal whip")
        db.watchlist_add(2, "Lobster")
        app._render_watchlist()
        w = table(app.watch_tree, app.watch_id_map)
        check(w["Abyssal whip"]["vol_12h"] == "-" and w["Abyssal whip"]["vol_24h"] == "-", "Watchlist shows - until 12h and 24h are ready")
        app._lookup_item(MAPPING[0])
        settle(lambda: app._search_volume and app._search_volume[0].winfo_exists(), 5)
        settle()
        t = search_table()
        check(t[(1, 1)] == "-" and t[(0, 0)] == "Volume" and t[(1, 0)] == "Instant buys" and t[(2, 0)] == "Instant sells", "Search table shows - while loading")
        app.notebook.select(app.search_tab)
        shot("shot_vol_loading.png")

        # Let the download run
        GATE.set()
        check(settle(lambda: 24 * 3600 in app._volume.ready_windows(), 15), "history finishes in the background")
        settle(lambda: app._volume_ready == app._volume.ready_windows())
        print("header:", app.status_var.get())
        check(app.status_var.get() == "3 items loaded.", "header back to normal when done: " + app.status_var.get())

        # Search
        t = search_table()
        print("search table:", t)
        check([t[(0, c)] for c in (1, 2, 3)] == ["1h", "12h", "24h"], "Search headings")
        check(t[(1, 1)] == "480" and t[(2, 1)] == "360", "Search 1h buys and sells")
        check(t[(1, 2)] != "-" and t[(1, 3)] != "-", "Search fills in 12h and 24h without another search")
        labels = [w.cget("text") for w in app.result_frame.winfo_children()[1].winfo_children()[0].winfo_children() if isinstance(w, tk.Label) or w.winfo_class() == "TLabel"]
        check("Volume" in labels and "Signal" not in labels and "Price move" in labels, f"Search rows: {labels}")
        shot("shot_vol_search.png")

        # FlipRec
        app._refresh_fliprec()
        settle(lambda: app.flip_refresh_btn.instate(["!disabled"]))
        settle()
        app.notebook.select(app.fliprec_tab)
        f = table(app.flip_tree, app.flip_id_map)
        print("fliprec volumes:", {k: v["volume"] for k, v in f.items()})
        check(f["Abyssal whip"]["volume"] == "360 ▲", "FlipRec arrow up (360 vs usual ~50/hr): " + f["Abyssal whip"]["volume"])
        check(f["Lobster"]["volume"].endswith("▼"), "FlipRec arrow down (9.6K vs usual ~80K/hr): " + f["Lobster"]["volume"])
        check("▲" not in f.get("Quiet thing", {"volume": ""})["volume"] and "▼" not in f.get("Quiet thing", {"volume": ""})["volume"], "item at its usual pace has no arrow")
        check(app.flip_tree.heading("volume", "text") == "Vol 1h", "FlipRec header says Vol 1h")
        shot("shot_vol_fliprec.png")

        # Sort still numeric, not by text with arrows
        app._sort_fliprec("volume", True)
        names = [app.flip_tree.set(i, "item") for i in app.flip_tree.get_children()]
        check(names[0] == "Lobster", f"sorting by volume is numeric: {names}")

        # Alerts
        app.notebook.select(app.alerts_tab)
        app._monitor_tick()
        settle()
        a = table(app.alerts_tree, app.alert_id_map)
        check(a["Abyssal whip"]["volume"] == "360 ▲", "Alerts Vol 1h: " + a["Abyssal whip"]["volume"])
        check(db.get_active_alerts()[0]["volume"] is not None, "volume stored with the alert")
        headers = [app.alerts_tree.heading(c, "text").replace(" ▼", "") for c in app.alerts_tree["columns"]]
        check(headers.index("Vol 1h") == headers.index("Last trade") - 1, "Alerts column placement")
        shot("shot_vol_alerts.png")

        # Watchlist
        app._render_watchlist()
        w = table(app.watch_tree, app.watch_id_map)
        print("watchlist:", {k: (v["volume"], v["vol_12h"], v["vol_24h"]) for k, v in w.items()})
        check(w["Abyssal whip"]["vol_12h"] not in ("-", "") and w["Abyssal whip"]["vol_24h"] not in ("-", ""), "Watchlist fills in 12h and 24h")
        app._sort_watchlist("vol_24h", True)
        check(table(app.watch_tree, app.watch_id_map) and app.watch_tree.set(app.watch_tree.get_children()[0], "item") == "Lobster", "Watchlist sorts by 24h")
        app.notebook.select(app.watchlist_tab)
        shot("shot_vol_watch.png")

        # Monitor no longer calls the Wiki's bulk endpoints once history is ready
        fallback_calls.clear()
        app._monitor_tick()
        settle()
        check(fallback_calls == [], f"checks use the history, not extra bulk requests: {fallback_calls}")

        # Volatile and AI read the rolling data
        tools = aitools.MarketTools(lambda: MAPPING, app._volume)
        fallback_calls.clear()
        it = tools.run("get_item", {"name": "whip"})
        check(it["units_traded"]["last_1h"] == {"instant_buys": 480, "instant_sells": 360, "may_read_low": False}, f"AI sees units traded: {it['units_traded'].get('last_1h')}")
        check(set(it["units_traded"]) == {"last_1h", "last_12h", "last_24h"}, "AI sees all three windows")
        m = tools.run("get_movers", {"window": "6h"})
        check("error" not in m and fallback_calls == [], f"AI movers use rolling data: {fallback_calls}")
        fallback_calls.clear()
        app.volatile_window_var.set("24h")
        app._refresh_volatile()
        settle(lambda: app.volatile_refresh_btn.instate(["!disabled"]))
        check(fallback_calls == [], f"Volatile uses rolling data: {fallback_calls}")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL VOLUME CHECKS PASSED", failures or "")
        root.destroy()


root.after(300, scenario)
root.mainloop()
sys.exit(1 if failures else 0)
