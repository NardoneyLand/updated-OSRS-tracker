"""The Buy / Hold / Sell signal reads fresh rolling hours, and falls back for thin items."""
import os, sys, tempfile
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import settings, db
settings.SETTINGS_FILE = os.path.join(tempfile.mkdtemp(), "settings.json")
db.DB_NAME = os.path.join(tempfile.mkdtemp(), "t.db")
db.initialize_db()
import aitools, analyzer, api, useragent
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


# Rolling hours: every 5 minutes for 31 hours
steady = [five(m) for m in range(31 * 60, 0, -5)]
hours = analyzer.rolling_hours(steady, NOW)
check(len(hours) == 30, "30 hours, however long the history is")
check(hours[0]["timestamp"] < hours[-1]["timestamp"], "oldest first, like the 1-hour history")
check(hours[-1]["highPriceVolume"] == 120 and hours[-1]["lowPriceVolume"] == 60, f"each hour adds 12 periods: {hours[-1]}")
check(hours[-1]["avgHighPrice"] == 1000 and hours[-1]["avgLowPrice"] == 900, "prices carried through")
check(hours[-1]["timestamp"] + 3600 == END, "the newest hour ends at the latest 5-minute mark, not an hour boundary")
check(set(hours[0]) == {"timestamp", "avgHighPrice", "avgLowPrice", "highPriceVolume", "lowPriceVolume"}, "same fields as the Wiki's hourly history")

mixed = analyzer.rolling_hours([five(10, high=100, high_vol=1, low=None, low_vol=0), five(20, high=200, high_vol=3, low=None, low_vol=0)], NOW)
check(mixed[-1]["avgHighPrice"] == 175, "prices are weighted by volume")
check(mixed[-1]["avgLowPrice"] is None and mixed[-1]["lowPriceVolume"] == 0, "a side with no trades has no price")
check(mixed[0]["highPriceVolume"] == 0 and mixed[0]["avgHighPrice"] is None, "an hour with no trades is zero volume")
check(analyzer.rolling_hours([five(-5), five(31 * 60)], NOW)[-1]["highPriceVolume"] == 0, "rows outside the window are ignored")
check(analyzer.rolling_hours([{"timestamp": None}], NOW)[-1]["highPriceVolume"] == 0, "a bad row is ignored")

# A rise shows up now, even though the hourly history wouldn't have it yet
rising = [five(m, high=1000 + (1800 - m), low=900 + (1800 - m)) for m in range(1800, 0, -5)]
stale_rows = [(1000, 900, NOW - 3600 * h) for h in range(2, 32)]
stale_points = [{"timestamp": NOW - 3600 * h, "avgHighPrice": 1000, "avgLowPrice": 900, "highPriceVolume": 100, "lowPriceVolume": 100} for h in range(31, 1, -1)]
trend, prediction = analyzer.price_signal(rising, stale_rows, stale_points, now=NOW)
check(trend["direction"] == "rising", f"uses the fresh data: {trend['direction']} {trend['pct_change']}")
old_trend = analyzer.calculate_trend(stale_rows)
check(old_trend["direction"] == "stable", "where the old data would have said stable")
check(prediction["suggestion"] in ("BUY", "HOLD", "HOLD/SELL", "SELL") and trend["data_points"] <= 30, "scored on the same scale")

# Live price counts as the newest point
trend, _ = analyzer.price_signal(steady, stale_rows, stale_points, live=(2000, 1800), now=NOW)
check(trend["direction"] == "rising" and trend["data_points"] == 30, f"the live price leads the trend: {trend['pct_change']}")
trend, _ = analyzer.price_signal(steady, stale_rows, stale_points, live=(None, 1800), now=NOW)
check(trend["direction"] == "stable", "a live price with one side is left out")

# Volume: last rolling hour against the average
busy = [five(m, high_vol=100 if m <= 60 else 10, low_vol=100 if m <= 60 else 10) for m in range(1800, 0, -5)]
_, prediction = analyzer.price_signal(busy, stale_rows, stale_points, now=NOW)
check(prediction["vol_label"] == "high" and prediction["recent_vol"] == 2400, f"recent volume is the last rolling hour: {prediction['recent_vol']}")

# Thin items and failures use the old path
thin = [five(m) for m in (60, 300, 600, 900, 1200)]   # 5 priced hours
trend, prediction = analyzer.price_signal(thin, stale_rows, stale_points, now=NOW)
check(trend["data_points"] == 30 and prediction["avg_volume"] == 200, "too few priced hours: falls back")
trend, prediction = analyzer.price_signal([], stale_rows, stale_points, now=NOW)
check(trend["data_points"] == 30 and prediction["avg_volume"] == 200, "no 5-minute history: falls back")
six = [five(m) for m in (60, 300, 600, 900, 1200, 1500)]
trend, prediction = analyzer.price_signal(six, stale_rows, stale_points, now=NOW)
check(trend["data_points"] == 6, "enough priced hours: doesn't")
trend, prediction = analyzer.price_signal([], [], [], now=NOW)
check(trend["direction"] == "unknown" and any("Not enough" in r for r in prediction["reasons"]), "no data at all says so")

# Ask AI uses it too
calls = []


def fake_series(item_id, timestep="1h"):
    calls.append(timestep)
    return [] if timestep == "5m" else stale_points


api.get_timeseries = fake_series
api.get_all_latest_prices = lambda: {"1": {"high": 1000, "low": 900, "highTime": NOW, "lowTime": NOW}}
api.get_bulk_average = lambda step="1h": {"1": {"highPriceVolume": 5, "lowPriceVolume": 5, "avgHighPrice": 1000, "avgLowPrice": 900}}
item = aitools.MarketTools(lambda: [{"id": 1, "name": "Thing", "limit": 10}]).run("get_item", {"name": "Thing"})
check("error" not in item and sorted(calls) == ["1h", "5m"], f"Ask AI reads the 5-minute history: {calls}")
check(item["signal"]["suggestion"] and item["history"]["data_points"] == 30, "and still reports the long history")

print("FAILURES" if failures else "ALL SIGNAL CHECKS PASSED", failures or "")
sys.exit(1 if failures else 0)
