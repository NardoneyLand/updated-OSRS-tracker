"""Regression tests for the v1.3.0 code review findings and the handle leak."""
import ctypes, json, os, sys, tempfile, threading, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import tkinter as tk
import settings, db
tmp = tempfile.mkdtemp()
settings.SETTINGS_FILE = os.path.join(tmp, "settings.json")
db.DB_NAME = os.path.join(tmp, "t.db")
db.initialize_db()
import aitools, analyzer, api, gui, scraper, useragent, volume
useragent.set_contact("test-contact")
volume.BACKFILL_PAUSE = 0
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


real_time = time.time
CLOCK = [1_793_553_780.0]    # 23 minutes past an hour
time.time = lambda: CLOCK[0]
ENTRY = {"highPriceVolume": 10, "lowPriceVolume": 5, "avgHighPrice": 1000, "avgLowPrice": 900}


def aligned(size):
    now = int(CLOCK[0])
    return now - now % size


def filled(fives=26, hours=25, skip=()):
    """A history holding the given periods, skipping (step, back) pairs."""
    h = volume.VolumeHistory()
    for back in range(1, fives + 1):
        if ("5m", back) not in skip:
            h._buckets["5m"][aligned(300) - back * 300] = volume._trim({"1": ENTRY})
    for back in range(1, hours + 1):
        if ("1h", back) not in skip:
            h._buckets["1h"][aligned(3600) - back * 3600] = volume._trim({"1": ENTRY})
    h._rebuild()
    return h


api_calls = []
api.get_bulk_average = lambda step="1h": (api_calls.append(step), {})[1]

# ── #1 Old 5-minute averages aren't used for the margin check ──
h = filled()
check(h.view(300) is not None, "#1 a fresh 5m period is used")
CLOCK[0] += 400
check(h.view(300) is None, "#1 a 5m period that ended over 8 minutes ago isn't")
check(h.view(3600) is not None, "#1 volumes can still use it for a while")
api_calls.clear()
check(h.averages("5m") == {} and api_calls == ["5m"], "#1 asks the Wiki, and leaves margins unchecked if that fails")
CLOCK[0] -= 400

# ── #2 / #10 One fallback path: throttled, keeps last good data ──
h = volume.VolumeHistory()
answers = {"1h": {"1": ENTRY}, "5m": {"1": ENTRY}}
api.get_bulk_average = lambda step="1h": (api_calls.append(step), answers[step])[1]
api_calls.clear()
check(h.averages("1h") == {"1": ENTRY}, "#2 falls back when the view isn't ready")
for _ in range(3):
    CLOCK[0] += 60
    h.averages("1h")
check(api_calls == ["1h"], f"#2 asks at most every 5 minutes: {api_calls}")
answers["1h"] = {}
CLOCK[0] += 301
api_calls.clear()
check(h.averages("1h") == {"1": ENTRY} and api_calls == ["1h"], "#2 a failed request reuses the last good volumes")
CLOCK[0] += 2 * 3600
check(h.averages("1h") == {}, "#2 but not after 2 hours")
answers["1h"] = {"1": ENTRY}
api_calls.clear()
check(h.averages("1h") == {"1": ENTRY}, "#2 recovers straight away when it has nothing cached")
api_calls.clear()
check(h.averages("5m") == {"1": ENTRY} and h.averages("5m") == {"1": ENTRY} and api_calls == ["5m"], "#2 5m asked at most every 2 minutes")
answers["5m"] = {}
CLOCK[0] += 121
check(h.averages("5m") == {}, "#2 5m data is never reused after a failure")
CLOCK[0] = 1_793_553_780.0

# ── #3 Periods that never arrive ──
requested = []


def bucket_with_gap(step, start):
    requested.append((step, start))
    return {} if (step, start) in GAPS else {"1": ENTRY}


GAPS = {("1h", aligned(3600) - 5 * 3600)}
api.get_bulk_bucket = bucket_with_gap
conn = db.get_connection(); conn.execute("DELETE FROM volume_buckets"); conn.commit(); conn.close()
h = volume.VolumeHistory()
h.update()
gap = aligned(3600) - 5 * 3600
check(h._buckets["1h"][gap] == {}, "#3 a long-empty period is saved as a known gap")
requested.clear()
h.update()
check(requested == [], "#3 and isn't asked for again")
check(h.view(12 * 3600) is not None and h.is_partial(12 * 3600), "#3 12h still shows, marked partial")
check(h.view(24 * 3600) is not None and h.is_partial(24 * 3600), "#3 24h still shows, marked partial")
check(not h.is_partial(3600), "#3 1h is unaffected")
h2 = volume.VolumeHistory()
h2._load()
check(h2.is_partial(24 * 3600), "#3 the gap is remembered after a restart")
# A recent empty period is not a gap yet
GAPS.clear()
GAPS.add(("5m", aligned(300) - 300))
h3 = volume.VolumeHistory()
h3._buckets = {"5m": {}, "1h": dict(h._buckets["1h"])}
h3.update()
check(aligned(300) - 300 not in h3._buckets["5m"], "#3 a just-ended empty period is retried, not saved")
GAPS.clear()
# Tolerance
h = filled(skip=())
for back in (3, 7):
    h._buckets["1h"][aligned(3600) - back * 3600] = {}
h._rebuild()
check(h.is_partial(12 * 3600), "#3 2 missing hours in 12h: shown, marked")
h._buckets["1h"][aligned(3600) - 9 * 3600] = {}
h._rebuild()
check(h.view(12 * 3600) is None, "#3 3 missing hours in 12h: not shown")
h = filled()
h._buckets["5m"][aligned(300) - 2 * 300] = {}
h._rebuild()
check(h.is_partial(3600), "#3 1 missing 5m period in the hour: shown, marked")
h._buckets["5m"][aligned(300) - 4 * 300] = {}
h._rebuild()
check(h.view(3600) is None, "#3 2 missing 5m periods in the hour: not shown")

# ── #5 Windows reach the present, or aren't called current ──
# Hour just ended isn't published: 12:00-13:00 missing at 13:23.
h = filled(skip={("1h", 1)})
check(h.view(24 * 3600) is not None, "#5 an unpublished hour is bridged by 5m data")
end = h._views[24 * 3600][1]
check(CLOCK[0] - end <= 600, f"#5 and the window ends recently ({(CLOCK[0]-end)/60:.0f} min ago)")
tail_periods = (aligned(300) - 300 - (aligned(3600) - 3600)) // 300 + 1
check(h.sides(1, 24 * 3600)[0] == round((86400 - tail_periods * 300) / 3600) * 10 + tail_periods * 10, "#5 and counts each period once")
# 5m data missing altogether: hours alone end too long ago
h = filled(fives=0, skip={("1h", 1)})
check(h.view(24 * 3600) is None, "#5 a window that ended over 30 minutes ago isn't current")
h = filled(fives=0)
check(h.view(24 * 3600) is not None, "#5 one that ended 23 minutes ago is")
print("history fixes ok")
time.time = real_time

# ── #6 Each attempt falls back separately ──
import importlib
importlib.reload(api)
importlib.reload(volume)
useragent.set_contact("test-contact")
urls = []
BOOM = [True]


class Resp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"1": {}}}


def flaky_get(url, **k):
    urls.append(url)
    if "timestamp" in url and BOOM[0]:
        raise OSError("timed out")
    return Resp()


api.SESSION.get = flaky_get
check(api.get_bulk_average("1h") == {"1": {}} and len(urls) == 3 and urls[-1].endswith("/1h"), "#6 failed requests still try the older period and the plain address")
urls.clear()
BOOM[0] = False
api.get_bulk_average("1h")
check(len(urls) == 1 and "timestamp" in urls[0], "#6 and the first success stops there")

# ── #8 Unknown is null, not empty ──
it = aitools.MarketTools(lambda: [])
traded = it._traded(1)
check(traded == {"last_1h": None, "last_12h": None, "last_24h": None}, f"#8 not loaded is null: {traded}")
check("null" in next(t for t in aitools.TOOLS if t["name"] == "get_item")["description"], "#8 and the AI is told what null means")

# ── #9 Download doesn't redo work for every period ──
rebuilds, deletes = [], []
volume.BACKFILL_PAUSE = 0
api.get_bulk_bucket = lambda step, start: {"1": ENTRY}
real_delete = db.delete_volume_buckets_before
db.delete_volume_buckets_before = lambda *a: (deletes.append(a), real_delete(*a))
conn = db.get_connection(); conn.execute("DELETE FROM volume_buckets"); conn.commit(); conn.close()
notes = []
h = volume.VolumeHistory(lambda: notes.append(1))
real_rebuild = h._rebuild
h._rebuild = lambda: (rebuilds.append(1), real_rebuild())
h.update()
check(len(rebuilds) == 2, f"#9 views rebuilt once per step, not per period: {len(rebuilds)}")
check(deletes == [], f"#9 no database deletes when nothing is old: {len(deletes)}")
check(len(notes) == 51, "#9 progress still reported for each period")
db.delete_volume_buckets_before = real_delete
check(h.view(3600) is not None and h.view(24 * 3600) is not None, "#9 and everything is ready at the end")

# A failure partway through still publishes what arrived
calls = [0]


def fails_later(step, start):
    calls[0] += 1
    if calls[0] > 20:
        raise OSError("down")
    return {"1": ENTRY}


api.get_bulk_bucket = fails_later
conn = db.get_connection(); conn.execute("DELETE FROM volume_buckets"); conn.commit(); conn.close()
h = volume.VolumeHistory()
try:
    h.update()
except OSError:
    pass
check(h.view(3600) is not None, "#9 a failure partway still keeps what arrived")

# ── #4 The history thread can't be killed by a bad start ──
attempts = []
stop_at = threading.Event()


class FlakyStart(volume.VolumeHistory):
    def _load(self):
        attempts.append("load")
        if len(attempts) == 1:
            raise RuntimeError("database is locked")
        super()._load()

    def update(self):
        attempts.append("update")
        stop_at.set()
        time.sleep(60)


real_every = volume.UPDATE_EVERY
volume.UPDATE_EVERY = 0.01
f = FlakyStart()
f.start()
check(stop_at.wait(5) and attempts[:3] == ["load", "load", "update"], f"#4 a failed load is retried, and the thread lives: {attempts[:3]}")
volume.UPDATE_EVERY = real_every

# ── Real app: the rest of #3, #4, #7, and the leak ──
conn = db.get_connection(); conn.execute("DELETE FROM volume_buckets"); conn.commit(); conn.close()
settings.save({"ua_contact": "test-contact", "capital": "1b", "alert_threshold": 1})
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}, {"id": 2, "name": "Lobster", "limit": 100, "members": False}]


def lt():
    t = time.time()
    return {"1": {"high": 2_100_000, "low": 1_900_000, "highTime": t - 20, "lowTime": t - 30}, "2": {"high": 200, "low": 180, "highTime": t - 20, "lowTime": t - 30}}


def period(step, start):
    if start + volume.STEP_SECONDS[step] > int(time.time()):
        return {}
    return {"1": {"highPriceVolume": 40, "lowPriceVolume": 30, "avgHighPrice": 2_090_000, "avgLowPrice": 1_910_000},
            "2": {"highPriceVolume": 40, "lowPriceVolume": 30, "avgHighPrice": 199, "avgLowPrice": 181}}


gui.api.get_mapping = lambda: MAPPING
gui.api.get_all_latest_prices = lt
gui.api.get_bulk_bucket = period
gui.api.get_bulk_average = lambda step="1h": {}
gui.api.get_latest_price = lambda i: lt()[str(i)]
gui.api.get_timeseries = lambda i, timestep="1h": []
gui.volume.BACKFILL_PAUSE = 0
scraper.scrape_all = lambda *a, **k: []
# volume was reloaded above, and gui holds the module it imported first.
gui.volume = volume
k32 = ctypes.windll.kernel32
root = tk.Tk()
root.attributes("-topmost", True)
app = gui.OSRSTrackerApp(root)


def settle(cond=lambda: True, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond() and not app._monitor_busy:
            root.update()
            return True
        time.sleep(0.005)
    return False


def handles():
    from ctypes import wintypes as W
    k32.GetCurrentProcess.restype = W.HANDLE
    k32.GetProcessHandleCount.argtypes = [W.HANDLE, ctypes.POINTER(W.DWORD)]
    n = W.DWORD()
    k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(n))
    return n.value


def cell(tree, id_map, name, col):
    for i in tree.get_children():
        if i in id_map and tree.set(i, "item") == name:
            return tree.set(i, col)


def scenario():
    try:
        settle(lambda: app.item_data)
        settle(lambda: 24 * 3600 in app._volume.ready_windows(), 20)
        settle(lambda: app._volume_ready == app._volume.ready_windows())
        app._monitor_tick()   # the first check may have run before the history was ready
        settle()
        check(app.status_var.get() == "2 items loaded.", "#3 header back to normal after the download: " + app.status_var.get())

        # #7 No saved volume isn't zero
        check(app._volume_text(1, None, gui.fmt_gp) == "-", "#7 alert with no saved volume shows -")
        check(app._volume_text(1, 0, gui.fmt_gp).startswith("0"), "#7 a real zero still shows 0")

        # #3 Asterisks and the note
        db.watchlist_add(1, "Abyssal whip")
        app._render_watchlist()
        check(app.watch_note.get() == "", "#3 no note when nothing is missing")
        check(not cell(app.watch_tree, app.watch_id_map, "Abyssal whip", "vol_24h").endswith("*"), "#3 no * when nothing is missing")
        h = app._volume
        gap = sorted(h._buckets["1h"], reverse=True)[3]
        h._buckets["1h"][gap] = {}
        h._rebuild()
        app._render_watchlist()
        check(cell(app.watch_tree, app.watch_id_map, "Abyssal whip", "vol_24h").endswith("*"), "#3 Watchlist 24h gets a *: " + cell(app.watch_tree, app.watch_id_map, "Abyssal whip", "vol_24h"))
        check(cell(app.watch_tree, app.watch_id_map, "Abyssal whip", "vol_12h").endswith("*"), "#3 Watchlist 12h gets a *")
        check(not cell(app.watch_tree, app.watch_id_map, "Abyssal whip", "volume").startswith("360*"), "#3 1h is unaffected")
        check("may read slightly low" in app.watch_note.get(), "#3 Watchlist explains the *")
        app._lookup_item(MAPPING[0])
        settle(lambda: app._search_volume and app._search_volume[0].winfo_exists(), 5)
        settle()
        cells = app._search_volume[2]
        check(cells[0, 24 * 3600].cget("text").endswith("*") and not cells[0, 3600].cget("text").endswith("*"), "#3 Search marks only the affected windows")
        check("may read slightly low" in cells["note"].cget("text"), "#3 Search explains the *")
        # Same widgets on the next update, not new ones
        first = cells[0, 3600]
        children = len(app._search_volume[0].winfo_children())
        for _ in range(20):
            app._render_search_volume()
        check(first.winfo_exists() and len(app._search_volume[0].winfo_children()) == children, "search table updates in place")
        h._buckets["5m"][sorted(h._buckets["5m"], reverse=True)[2]] = {}
        h._rebuild()
        check(app._volume_text(1, 360, gui.fmt_gp).startswith("360*"), "#3 Vol 1h gets a * when its window is partial")
        app._flip_raw = (lt(), h.view(3600), time.time(), h.view(300))
        app._market = app._flip_raw
        app._apply_flip_filters()
        settle()
        check("may read slightly low" in app.flip_banner.get(), "#3 FlipRec explains the *")
        app._monitor_tick()
        settle()
        check("may read slightly low" in app.alerts_status.get(), "#3 Alerts explains the *: " + app.alerts_status.get())

        # The leak: finished work reaches the window without threads calling Tk
        got = []
        tk_thread = threading.get_ident()
        gui.run_async(lambda: threading.get_ident(), lambda result, err: got.append((result, threading.get_ident())))
        settle(lambda: got)
        check(got and got[0][0] != tk_thread and got[0][1] == tk_thread, "background work finishes on the window's thread")
        done = []
        for _ in range(5):
            gui.run_async(lambda: 1, lambda r, e: done.append(1))
        settle(lambda: len(done) >= 5)
        before = handles()
        for _ in range(100):
            done.clear()
            gui.run_async(lambda: 1, lambda r, e: done.append(1))
            settle(lambda: done, 3)
        time.sleep(0.3)
        settle()
        growth = handles() - before
        check(growth <= 3, f"LEAK: 100 background jobs leave no handles behind (+{growth}; was +100)")
        before = handles()
        for _ in range(30):
            app._monitor_tick()
            settle(lambda: not app._monitor_busy, 10)
        time.sleep(0.3)
        settle()
        growth = handles() - before
        check(growth <= 3, f"LEAK: 30 market checks leave no handles behind (+{growth})")

        # A failing callback doesn't stop the queue
        hit = []
        real_report = root.report_callback_exception
        root.report_callback_exception = lambda *a: hit.append("reported")
        gui.ui_call(lambda: 1 / 0)
        gui.ui_call(lambda: hit.append("next"))
        settle(lambda: len(hit) >= 2, 3)
        root.report_callback_exception = real_report
        check(hit == ["reported", "next"], f"a failing callback is reported and the queue carries on: {hit}")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL REVIEW FIXES PASSED", failures or "")
        sys.stdout.flush()
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
