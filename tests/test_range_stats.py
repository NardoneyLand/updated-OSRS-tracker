"""Range stats beside the Search chart, and in Ask AI: each side's prices, units traded, where the live price sits, buy pressure, change and margin."""
import os, sqlite3, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
settings.save({"ua_contact": "test-contact", "search_price_format": "Exact (1,240)"})
import ai, aitools, analyzer, api, gui, scraper

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# ── The numbers ──────────────────────────────────────────────────────────────
rows = [  # (high, low, ts, high volume, low volume)
    (1100, 1000, 0, 10, 30),
    (1300, 1300, 300, 0, 5),      # nobody instant-bought: 1300 is the low copied over
    (1200, 1050, 600, 30, 10),
    (1150, 1100, 900, None, None),  # a live snapshot
]
s = analyzer.range_stats(rows, 300)
check(s["sell_high"] == 1200 and s["sell_low"] == 1100, f"a side with no trades is left out of its prices: {s['sell_high']}, {s['sell_low']}")
check(s["buy_high"] == 1300 and s["buy_low"] == 1000, f"buy side: {s['buy_high']}, {s['buy_low']}")
check(s["sell_avg"] == 1175, f"average weighted by units traded: {s['sell_avg']}")
check(s["buy_avg"] == round((1000 * 30 + 1300 * 5 + 1050 * 10) / 45), f"buy average: {s['buy_avg']}")
check((s["instant_buys"], s["instant_sells"]) == (40, 45), f"units traded each side: {s['instant_buys']}, {s['instant_sells']}")
check(s["gp_traded"] == 1100 * 10 + 1000 * 30 + 1300 * 5 + 1200 * 30 + 1050 * 10, f"gp traded: {s['gp_traded']}")
check(abs(s["per_hour"] - 85 / (900 / 3600)) < 0.01, f"pace counts only points with volumes: {s['per_hour']}")
check(s["first_mid"] == 1050 and s["last_mid"] == 1125 and abs(s["change_pct"] - 75 / 1050 * 100) < 0.01, f"change from first to last mid: {s['change_pct']}")
# Margins after tax: 1100-22-1000=78, 1200-24-1050=126, 1150-23-1100=27 (the 1300 point has one side only)
check(s["margin"] == 78 and s["margin_positive"] == 100 and s["margin_periods"] == 3, f"median margin after tax: {s['margin']}, {s['margin_positive']}%")
check(analyzer.range_stats(rows, 300, exempt=True)["margin"] == 100, "tax-free items pay no tax in the margin")
check(analyzer.range_stats(rows[:2], 300)["margin"] is None, "too few periods: no margin")
none = analyzer.range_stats([(1100, 1000, 0, None, None)], 300)
check(none["instant_buys"] is None and none["per_hour"] is None, "no volumes known: no traded numbers")
check(analyzer.range_stats([], 300) is None and analyzer.range_stats(None, 300) is None, "no rows: nothing")
check(abs(s["buy_pressure"] - 40 / 85 * 100) < 0.01, f"buy pressure is the share of instant buys: {s['buy_pressure']}")
check(none["buy_pressure"] is None, "no volumes: no buy pressure")
check(analyzer.range_stats([(10, 9, 0, 0, 0)], 300)["buy_pressure"] is None, "nothing traded: no buy pressure")

where = analyzer.range_position(s, 1320, 1150)
check(abs(where["buy_above_low_pct"] - 15) < 0.01 and abs(where["sell_above_low_pct"] - 20) < 0.01, f"how far above each side's low: {where}")
check(abs(where["buy_below_high_pct"] - (1300 - 1150) / 1150 * 100) < 0.01 and where["sell_below_high_pct"] < 0, f"and below its high, negative past it: {where}")
check(analyzer.range_position(None, 1320, 1150)["buy_above_low_pct"] is None and analyzer.range_position(s, None, None)["sell_above_low_pct"] is None, "unknown without a range or a live price")

check([analyzer.range_includes_live(w) for _l, w, _s in analyzer.CHART_RANGES] == [True] * 4 + [False] * 3,
      "live snapshots show on 1H to 1W only")
check([r[0] for r in analyzer.CHART_RANGES] == ["1H", "12H", "1D", "1W", "1M", "1Y", "All"] and gui.HISTORY_RANGES is analyzer.CHART_RANGES, "the chart and Ask AI share one list of ranges")

# ── Stored volumes ───────────────────────────────────────────────────────────
# A database from before volumes were kept gets the columns, and its points get
# their volumes on the next download.
old = os.path.join(tempfile.mkdtemp(), "old.db")
conn = sqlite3.connect(old)
conn.execute("CREATE TABLE price_history (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, "
             "item_name TEXT NOT NULL, high_price INTEGER, low_price INTEGER, timestamp INTEGER NOT NULL, source_step TEXT)")
conn.execute("INSERT INTO price_history (item_id, item_name, high_price, low_price, timestamp, source_step) VALUES (7, 'X', 10, 9, 3600, '1h')")
conn.execute("INSERT INTO price_history (item_id, item_name, high_price, low_price, timestamp, source_step) VALUES (7, 'X', 11, 9, 7200, NULL)")
conn.commit()
conn.close()
real_db = db.DB_NAME
db.DB_NAME = old
db.initialize_db()
cols = [r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(price_history)")]
check("high_volume" in cols and "low_volume" in cols, f"the columns are added: {cols}")
pts = [{"timestamp": t, "avgHighPrice": 10, "avgLowPrice": 9, "highPriceVolume": 5, "lowPriceVolume": 6} for t in (3600, 7200, 10800)]
changed = db.backfill_price_history(7, "X", pts, "1h")
got = db.get_price_range(7, 0, "1h", include_live=True)
check(changed == 3 and got == [(10, 9, 3600, 5, 6), (11, 9, 7200, 5, 6), (10, 9, 10800, 5, 6)], f"old points get volumes, new ones are added: {changed} {got}")
check(db.backfill_price_history(7, "X", pts, "1h") == 0, "the same download again changes nothing")
db.DB_NAME = real_db

# ── The Search panel ─────────────────────────────────────────────────────────
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}]
now = int(time.time())


def series(step):
    size = api.STEP_SECONDS[step]
    end = now - now % size - size
    # Rising 1,000 gp a point, 100 units each side, so every number is easy to check.
    return [{"timestamp": end - i * size, "avgHighPrice": 2_100_000 + (300 - i) * 1000,
             "avgLowPrice": 2_000_000 + (300 - i) * 1000, "highPriceVolume": 100, "lowPriceVolume": 100}
            for i in range(299, -1, -1)]


api.get_mapping = lambda: MAPPING
api.get_latest_price = lambda i: {"high": 2_400_000, "low": 2_300_000, "highTime": now - 20, "lowTime": now - 30}
api.get_all_latest_prices = lambda: {"1": api.get_latest_price(1)}
api.get_bulk_average = lambda step="1h": {"1": {"avgHighPrice": 2_390_000, "avgLowPrice": 2_290_000, "highPriceVolume": 50, "lowPriceVolume": 50}}
api.get_bulk_bucket = lambda step, start: {}
api.get_timeseries = lambda item_id, timestep="1h": series(timestep)
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


def points(step, window):
    """How many of the fake points a range covers."""
    since = int(time.time()) - window
    return sum(p["timestamp"] >= since for p in series(step))


def cell(key):
    return app._range_stats[key].cget("text")


def right_edge(widget, panel):
    return widget.winfo_rootx() + widget.winfo_width() - panel.winfo_rootx()


def scenario():
    try:
        settle(lambda: bool(app.item_data), 10)
        panel = app._range_stats["panel"]
        check(not panel.winfo_ismapped(), "nothing searched: no range stats")
        app.notebook.select(app.search_tab)
        root.geometry("1380x860")
        app._lookup_item(MAPPING[0])
        settle(lambda: cell(("sell", "high")) not in ("", "-"), 8)
        settle()
        check(panel.winfo_ismapped(), "a search shows them")
        # 1W uses hourly points: the last 168 hours, the newest at 2,400,000 / 2,300,000
        check(cell("title") == "Last week", f"titled by the range: {cell('title')!r}")
        n = points("1h", 7 * 86400)
        lowest = f"{2_000_000 + (301 - n) * 1000:,}"
        check(cell(("sell", "high")) == "2,400,000" and cell(("buy", "low")) == lowest, f"highest and lowest: {cell(('sell', 'high'))} {cell(('buy', 'low'))}, expected {lowest}")
        check(cell(("sell", "traded")) == cell(("buy", "traded")) == f"{n * 100:,}", f"units traded: {cell(('sell', 'traded'))}, {n} points")
        check(cell("Typical margin").endswith("(100% positive)"), f"margin: {cell('Typical margin')}")
        check(cell("pace label") == "Per day" and cell("Pace") == "4,800 traded", f"pace: {cell('pace label')} {cell('Pace')}")
        check(cell("Change").startswith("Up ") and "(+" in cell("Change"), f"change: {cell('Change')}")
        low_buy = 2_000_000 + (301 - n) * 1000
        want_now = f"{(2_300_000 - low_buy) / low_buy * 100:+.1f}%"
        check(cell(("buy", "now")) == want_now, f"how far the live Buy is above the week's low: {cell(('buy', 'now'))}, expected {want_now}")
        check(cell("Buy pressure") == "50% instant buys", f"buy pressure: {cell('Buy pressure')}")
        # A market check moves the live price without redrawing the chart
        app._search_shown["price_data"] = {**app._search_shown["price_data"], "low": low_buy}
        app._show_search_prices()
        check(cell(("buy", "now")) == "+0.0%", f"Now vs low follows the market: {cell(('buy', 'now'))}")
        check(cell("GP traded").replace(",", "").isdigit(), f"gp traded: {cell('GP traded')}")

        # Beside the chart: it takes no height from it, and nothing is cut off
        canvas = app.history_canvas
        frame = canvas.master
        def fits():
            return (panel.winfo_height() >= panel.winfo_reqheight()
                    and panel.winfo_rooty() + panel.winfo_reqheight() <= frame.winfo_rooty() + frame.winfo_height()
                    and right_edge(panel, frame) <= frame.winfo_width())

        def facts_beside():
            return app._range_stats["Change"].master.grid_info()["column"] == 1

        print("chart", canvas.winfo_width(), "x", canvas.winfo_height(), "stats", panel.winfo_reqwidth(), "x", panel.winfo_reqheight())
        check(fits(), f"at 1380x860 every row fits beside the chart: {panel.winfo_height()} of {panel.winfo_reqheight()}px")
        check(facts_beside(), "a short chart puts the facts beside the table")
        check(canvas.winfo_width() >= 600, f"and the chart keeps most of the width: {canvas.winfo_width()}px")
        root.geometry("1100x860")
        settle(seconds=0.6)
        check(fits() and canvas.winfo_width() >= 300, f"at 1100 wide too: {canvas.winfo_width()}px")
        root.geometry("1380x1200")
        settle(seconds=0.6)
        check(fits() and not facts_beside(), f"a tall chart stacks them, which leaves it wider: {canvas.winfo_width()}px")
        root.geometry("1380x860")
        settle(seconds=0.6)
        check(fits() and facts_beside(), "and going back puts them beside again")

        app._set_history_range("1H")
        want = f"{points('5m', 3600) * 100:,}"
        settle(lambda: cell("title") == "Last hour" and cell(("sell", "traded")) == want, 5)
        check(cell("title") == "Last hour", f"1H changes the title: {cell('title')!r}")
        check(cell(("sell", "traded")) == want, f"and the numbers, from 5-minute points: {cell(('sell', 'traded'))}, expected {want}")
        check(cell("pace label") == "Per hour" and cell("Pace") == "2,400 traded", f"short ranges give the pace an hour: {cell('pace label')} {cell('Pace')}")

        app._set_history_range("1M")
        settle(lambda: cell("title") == "Last month", 5)
        full = f"{points('6h', 30 * 86400) * 100:,}"
        settle(lambda: cell(("sell", "traded")) == full, 5)
        check(cell(("sell", "traded")) == full, f"1M from 6-hour points: {cell(('sell', 'traded'))}, expected {full}")
        t0, t1 = app._chart_geom["view"]
        app._set_chart_view(t1 - (t1 - t0) / 4, t1)
        settle()
        check(cell("title") == "In view", f"zoomed: {cell('title')!r}")
        check(cell(("sell", "traded")) != full, f"covers only what's in view: {cell(('sell', 'traded'))} vs {full}")
        app._reset_chart_zoom()
        settle()
        check(cell("title") == "Last month" and cell(("sell", "traded")) == full, "Reset zoom brings the whole range back")

        app.search_format_var.set(gui.PRICE_FORMATS[0])
        app._on_search_format_changed()
        settle()
        check(cell(("sell", "high")) == "2.40M", f"Short prices: {cell(('sell', 'high'))}")
        check(cell("GP traded").endswith("B"), f"short gp traded reads in billions: {cell('GP traded')}")

        # Ask AI looks up the same numbers
        tools = aitools.MarketTools(lambda: MAPPING)
        got = tools.run("get_price_range", {"name": "whip", "range": "1M"})
        m = points("6h", 30 * 86400)
        check(got.get("name") == "Abyssal whip" and got.get("range") == "1M" and got.get("data_points") == m, f"Ask AI finds the item and range: {got.get('name')} {got.get('range')} {got.get('data_points')}")
        check(got.get("units_traded", {}).get("total") == m * 200 and got["units_traded"]["instant_buys"] == m * 100, f"with units traded: {got.get('units_traded')}")
        check(got.get("buy_pressure_pct") == 50.0 and got.get("minutes_per_point") == 360, f"buy pressure and point size: {got.get('buy_pressure_pct')} {got.get('minutes_per_point')}")
        check(got.get("sell_price", {}).get("highest") == 2_400_000 and got["buy_price"]["lowest"] == 2_000_000 + (301 - m) * 1000, f"each side's prices: {got.get('sell_price')} {got.get('buy_price')}")
        check(got.get("live", {}).get("buy") == 2_300_000 and got["live"]["buy_above_low_pct"] > 0, f"and where the live price sits: {got.get('live')}")
        check(tools.run("get_price_range", {"name": "whip", "range": "forever"})["range"] == "1W", "an unknown range means a week")
        check("error" in tools.run("get_price_range", {"name": "zzzzqqq"}), "an unknown item is an error")
        check("error" in tools.run("get_price_range", {}), "a missing name is an error")
        api.get_timeseries = lambda item_id, timestep="1h": []
        again = tools.run("get_price_range", {"name": "whip", "range": "1M"})
        check(again.get("data_points") == m, f"a failed download still answers from what's stored: {again.get('data_points')}")
        lonely = aitools.MarketTools(lambda: MAPPING + [{"id": 99, "name": "Unseen thing", "limit": 5, "members": True}])
        empty = lonely.run("get_price_range", {"name": "Unseen thing"})
        check(empty["data_points"] == 0 and "note" in empty, f"no history says so: {empty}")
        api.get_timeseries = lambda item_id, timestep="1h": series(timestep)
        # The log line holds what the AI read, and leaves out a side with no prices
        logged = []
        real_info = aitools.applog.log.info
        aitools.applog.log.info = logged.append
        try:
            aitools._log_lookup("get_price_range", {"name": "whip"}, dict(
                got, sell_price={"highest": None, "lowest": None, "average": None},
                live={"buy": 2_300_000, "sell": None, "prices_checked_at": "12:00"}))
        finally:
            aitools.applog.log.info = real_info
        line = logged[0] if logged else ""
        check("None" not in line and "buy " in line and "sell " not in line.split("live")[0].replace("buy ", ""),
              f"a side with no prices is left out of the log: {line!r}")
        check(all(w in line for w in ("margin ", "change % ", "buy pressure % ", "live buy 2300000", "prices checked 12:00")),
              f"and it has the margin, change, pressure and live quote: {line!r}")

        # A live snapshot counts on short ranges, as it does on the chart
        db.record_price(1, "Abyssal whip", 9_999_999, 2_300_000)
        day = tools.run("get_price_range", {"name": "whip", "range": "1D"})
        check(day.get("sell_price", {}).get("highest") == 9_999_999, f"Ask AI sees the live snapshot the chart shows: {day.get('sell_price')}")
        month = tools.run("get_price_range", {"name": "whip", "range": "1M"})
        check(month["sell_price"]["highest"] == 2_400_000, f"but not on long ranges: {month['sell_price']}")
        check(any(t["name"] == "get_price_range" for t in aitools.TOOLS) and "get_price_range" in aitools.STATUS_TEXT, "it's offered to the AI")
        check("get_price_range" in ai.SYSTEM_PROMPT, "and the AI is told what it's for")

        # While a new item loads there's nothing to describe yet
        app._history_placeholder("Loading")
        check(cell(("sell", "high")) == "-" and cell("Change") == "-" and cell("Buy pressure") == "-" and cell(("buy", "now")) == "-" and cell("title") == "Last month", "dashes while loading")

        helptext = " ".join(body for _title, body in gui.HELP_SECTIONS)
        check(all(w in helptext for w in ("Typical margin", "Per day", "Now vs low", "Buy pressure")), "Help explains them")
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL RANGE STATS CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
