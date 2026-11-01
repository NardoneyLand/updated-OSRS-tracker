"""Search and Ask AI show facts from fresh rolling hours, with no Buy / Sell verdict."""
import os, sys, tempfile, time, traceback
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import support  # keeps test windows off the screen
import tkinter as tk
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
db.initialize_db()
settings.save({"ua_contact": "test-contact"})
import aitools, ai, analyzer, api, gui, scraper, useragent
useragent.set_contact("test-contact")
failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


NOW = 1_793_553_780           # 23 minutes past an hour
END = NOW - NOW % 300


def five(minutes_ago, high=1000, low=900, high_vol=10, low_vol=5):
    return {"timestamp": END - minutes_ago * 60, "avgHighPrice": high, "avgLowPrice": low,
            "highPriceVolume": high_vol, "lowPriceVolume": low_vol}


# Rolling hours
steady = [five(m) for m in range(31 * 60, 0, -5)]
hours = analyzer.rolling_hours(steady, NOW)
check(len(hours) == 30, "30 hours, however long the history is")
check(hours[0]["timestamp"] < hours[-1]["timestamp"], "oldest first, like the 1-hour history")
check(hours[-1]["highPriceVolume"] == 120 and hours[-1]["lowPriceVolume"] == 60, f"each hour adds 12 periods: {hours[-1]}")
check(hours[-1]["timestamp"] + 3600 == END, "the newest hour ends at the latest 5-minute mark, not an hour boundary")
mixed = analyzer.rolling_hours([five(10, high=100, high_vol=1, low=None, low_vol=0), five(20, high=200, high_vol=3, low=None, low_vol=0)], NOW)
check(mixed[-1]["avgHighPrice"] == 175, "prices are weighted by volume")
check(mixed[-1]["avgLowPrice"] is None and mixed[0]["highPriceVolume"] == 0, "no trades means no price and zero volume")
check(analyzer.rolling_hours([five(-5), five(31 * 60), {"timestamp": None}], NOW)[-1]["highPriceVolume"] == 0, "rows outside the window, or bad ones, are ignored")

# Facts use fresh data
rising = [five(m, high=1000 + (1800 - m), low=900 + (1800 - m)) for m in range(1800, 0, -5)]
stale_rows = [(1000, 900, NOW - 3600 * h) for h in range(2, 32)]
stale_points = [{"timestamp": NOW - 3600 * h, "avgHighPrice": 1000, "avgLowPrice": 900, "highPriceVolume": 100, "lowPriceVolume": 100} for h in range(31, 1, -1)]
trend, facts = analyzer.recent_market(rising, stale_rows, stale_points, now=NOW)
check(trend["direction"] == "rising", f"uses the fresh data: {trend['pct_change']}")
check(analyzer.calculate_trend(stale_rows)["direction"] == "stable", "where the old data would have said stable")
check(28 <= trend["hours"] <= 30, f"knows how many hours it covers: {trend['hours']}")
check(facts["momentum"] == "rising", "momentum compares later prices with earlier ones")
trend, _ = analyzer.recent_market(steady, stale_rows, stale_points, live=(2000, 1800), now=NOW)
check(trend["direction"] == "rising" and trend["data_points"] == 30, "the live price leads the trend")
trend, _ = analyzer.recent_market(steady, stale_rows, stale_points, live=(None, 1800), now=NOW)
check(trend["direction"] == "stable", "a live price with one side is left out")
busy = [five(m, high_vol=100 if m <= 60 else 10, low_vol=100 if m <= 60 else 10) for m in range(1800, 0, -5)]
_, facts = analyzer.recent_market(busy, stale_rows, stale_points, now=NOW)
check(facts["volume"] == "high" and facts["recent_volume"] == 2400, f"recent volume is the last rolling hour: {facts['recent_volume']}")

# Thin items and failures use the fallback
thin = [five(m) for m in (60, 300, 600, 900, 1200)]
trend, facts = analyzer.recent_market(thin, stale_rows, stale_points, now=NOW)
check(trend["data_points"] == 30 and facts["usual_volume"] == 200, "too few priced hours: falls back")
trend, facts = analyzer.recent_market([], stale_rows, stale_points, now=NOW)
check(trend["data_points"] == 30 and facts["usual_volume"] == 200, "no 5-minute history: falls back")
trend, facts = analyzer.recent_market([five(m) for m in (60, 300, 600, 900, 1200, 1500)], stale_rows, stale_points, now=NOW)
check(trend["data_points"] == 6, "enough priced hours: doesn't")
trend, facts = analyzer.recent_market([], [], [], now=NOW)
check(trend["direction"] == "unknown" and any("Not enough" in n for n in facts["notes"]), "no data at all says so")

# Notes
mk = lambda direction, pct: analyzer.read_market({"direction": direction, "pct_change": pct}, [])
check(any("partly reverse" in n for n in mk("falling", -5)["notes"]) and any("partly reverse" in n for n in mk("rising", 5)["notes"]), "big moves get the reversal note")
check(not any("partly reverse" in n for n in mk("stable", 1)["notes"]), "steady prices don't")
check(any("Extreme" in n for n in mk("rising", 30)["notes"]), "extreme swings are flagged")
quiet = [dict(p, highPriceVolume=1, lowPriceVolume=1) if i == 29 else p for i, p in enumerate(stale_points)]
check(any("quiet" in n for n in analyzer.read_market({"direction": "stable", "pct_change": 0}, quiet)["notes"]), "quiet trading is noted")

# Display text
check(gui.fmt_price_move({"direction": "falling", "pct_change": -5.2, "hours": 30}) == "Down 5.2% over 30 hours", "price move: down")
check(gui.fmt_price_move({"direction": "rising", "pct_change": 3.4, "hours": 30}) == "Up 3.4% over 30 hours", "price move: up")
check(gui.fmt_price_move({"direction": "stable", "pct_change": 0.4, "hours": 30}) == "Steady (+0.4%) over 30 hours", "price move: steady")
check(gui.fmt_price_move({"direction": "unknown", "pct_change": 0, "hours": 0}) == "Not enough history", "price move: unknown")
check("High for this item" in gui.fmt_volume_pace({"volume": "high", "recent_volume": 4800, "usual_volume": 2100}), "volume text")
check(gui.fmt_volume_pace({"volume": "unknown", "recent_volume": 0, "usual_volume": 0}) == "Unknown", "volume unknown")

# Nothing still gives the old advice
for name, text in (("Help", " ".join(b for h, b in gui.HELP_SECTIONS if not h.startswith("Why there"))), ("AI prompt", ai.SYSTEM_PROMPT),
                   ("AI tools", str(aitools.TOOLS)), ("analyzer", str([n for n in dir(analyzer)]))):
    check("BUY" not in text and "Buy / Hold" not in text and "get_prediction" not in text, f"no verdict left in {name}")
check("partly reverse" in ai.SYSTEM_PROMPT and "Don't treat a recent move as a signal" in ai.SYSTEM_PROMPT, "the AI is told about reversal")

# Ask AI
calls = []
series_5m = []


def fake_series(item_id, timestep="1h"):
    calls.append(timestep)
    return series_5m if timestep == "5m" else stale_points


api.get_timeseries = fake_series
api.get_all_latest_prices = lambda: {"1": {"high": 1000, "low": 900, "highTime": time.time(), "lowTime": time.time()}}
api.get_bulk_average = lambda step="1h": {"1": {"highPriceVolume": 5, "lowPriceVolume": 5, "avgHighPrice": 1000, "avgLowPrice": 900}}
api.get_bulk_bucket = lambda step, start: {}
item = aitools.MarketTools(lambda: [{"id": 1, "name": "Thing", "limit": 10}]).run("get_item", {"name": "Thing"})
check("error" not in item and sorted(calls) == ["1h", "5m"], f"Ask AI reads the 5-minute history: {calls}")
check("signal" not in item and set(item["recent_market"]) >= {"price_move_pct", "over_hours", "volume_vs_usual", "momentum", "notes"}, "Ask AI gets facts, not a verdict")
check("suggestion" not in str(item) and "BUY" not in str(item), "with no suggestion anywhere")

# The real Search panel
MAPPING = [{"id": 1, "name": "Abyssal whip", "limit": 70, "members": True}]
api.get_mapping = lambda: MAPPING
api.get_latest_price = lambda i: {"high": 2_000_000, "low": 1_900_000, "highTime": time.time() - 20, "lowTime": time.time() - 30}
scraper.scrape_all = lambda *a, **k: []
real_now = int(time.time())
real_end = real_now - real_now % 300
series_5m[:] = [{"timestamp": real_end - m * 60, "avgHighPrice": 2_100_000 - m * 100, "avgLowPrice": 2_000_000 - m * 100, "highPriceVolume": 100000, "lowPriceVolume": 100000} for m in range(1800, 0, -5)]
root = tk.Tk()
app = gui.OSRSTrackerApp(root)


def settle(cond, seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        if cond():
            root.update()
            return True
        time.sleep(0.005)
    return False


def texts(widget):
    found = []
    for child in widget.winfo_children():
        try:
            found.append(str(child.cget("text")))
        except tk.TclError:
            pass
        found += texts(child)
    return found


def scenario():
    try:
        settle(lambda: app.item_data)
        app._lookup_item(MAPPING[0])
        settle(lambda: any("Price move" == t for t in texts(app.result_frame)))
        shown = texts(app.result_frame)
        print("panel:", [t for t in shown if t][:24])
        check("Price move" in shown and "Volume" in shown and "Momentum" in shown, "shows the three facts")
        check("Signal" not in shown and "Trend" not in shown, "no Signal or Trend row")
        check(not any("BUY" in t or "SELL" in t or "HOLD" in t or "confidence" in t for t in shown), "no verdict anywhere on the panel")
        check(any(t.startswith("Up ") and "over" in t and "hours" in t for t in shown), "price move shows")
        check(any("partly reverse" in t for t in shown), "the reversal note shows")
        check(any("Worth knowing" in t for t in shown), "under Worth knowing")

        # Chart buttons and notes stay whole at normal and narrower widths
        def cut_off(w, found):
            for c in w.winfo_children():
                if c.winfo_ismapped() and c.winfo_class() in ("TButton", "TLabel"):
                    right = c.winfo_rootx() + c.winfo_width()
                    if c.winfo_width() < c.winfo_reqwidth() - 1 or right > root.winfo_rootx() + root.winfo_width():
                        found.append(str(c.cget("text"))[:30])
                cut_off(c, found)
            return found

        app.notebook.select(app.search_tab)
        for width in (1250, 1100, 1000):
            root.geometry(f"{width}x860")
            settle(lambda: True, 0.3)
            check(cut_off(app.search_tab, []) == [], f"nothing cut off on Search at {width}px: {cut_off(app.search_tab, [])}")
            chart_buttons = [b for b in texts(app.search_tab) if b in ("Refresh chart", "Reset zoom")]
            check(len(chart_buttons) == 2, f"both chart buttons present at {width}px")
        # Everything fits inside the panel at the normal window size, even with big numbers
        app.notebook.select(app.search_tab)
        root.geometry("1250x860")
        settle(lambda: True, 0.3)
        right_edge = app.result_frame.winfo_rootx() + app.result_frame.winfo_width()
        def widest(w):
            edges = [c.winfo_rootx() + c.winfo_reqwidth() for c in w.winfo_children()]
            return max(edges + [widest(c) for c in w.winfo_children()], default=0)
        check(widest(app.result_frame) <= right_edge, f"nothing runs off the panel ({widest(app.result_frame)} vs {right_edge}): " + str([t for t in texts(app.result_frame) if "last hour" in t]))
    except Exception:
        traceback.print_exc()
        failures.append("crashed")
    finally:
        print("FAILURES" if failures else "ALL FACT CHECKS PASSED", failures or "", flush=True)
        os._exit(1 if failures else 0)


root.after(200, scenario)
root.mainloop()
