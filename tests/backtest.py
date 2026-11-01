"""
Replay a Buy / Sell rule over real history. Run this before adding one to the app:

    python tests/backtest.py

Not part of run_all: it downloads about 300 items from the OSRS Wiki, gently, and
takes a couple of minutes the first time (the data is then cached in the temp
folder for a day). It shows no windows.

How a rule is judged:
  - No look-ahead. A rule at hour t sees hours up to t, and is acted on at hour
    t+1's average price, because hour t's price has already gone by then.
  - Excess return = the item's return minus the average of all sampled items over
    the same hours, so a market-wide move doesn't count as skill.
  - Confidence intervals resample whole items, since one item's overlapping hours
    aren't independent.

To be worth showing, a "buy" label needs a clearly positive excess return, and a
raw return that beats the 2% GE tax. The trend-following rule below is the one the
app used to show. It failed both: its BUY items did worse than average, here and
over the 85 days before.
"""

import json, os, random, statistics, sys, tempfile, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import analyzer, api, useragent

CACHE = os.path.join(tempfile.gettempdir(), "osrs_tracker_backtest.json")
WINDOW = 30
HORIZONS = (1, 6, 24)
CAP = 0.5          # one absurd price shouldn't decide an average
ITEMS_BUSIEST, ITEMS_OTHERS = 100, 200


def trend_following(trend: dict, facts: dict) -> str:
    """The old BUY / HOLD / SELL score, kept as the example not to repeat."""
    direction = {"rising": 1, "falling": -1}.get(trend["direction"], 0)
    score = direction
    if facts["volume"] == "high":
        score += direction
    score += {"rising": 1, "falling": -1}.get(facts["momentum"], 0)
    if abs(trend["pct_change"]) > 25:      # its guard against manipulation
        score = max(-1, min(score, 1))
    if score >= 1:
        return "BUY"
    if score == 0:
        return "HOLD"
    return "HOLD/SELL" if score == -1 else "SELL"


RULE = trend_following


def download() -> dict:
    if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 86400:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    useragent.set_contact("github.com/NardoneyLand/updated-OSRS-tracker")
    names = {str(i["id"]) for i in api.get_mapping()}
    ranked = []
    for item_id, entry in api.get_bulk_average("24h").items():
        slower = min(entry.get("highPriceVolume") or 0, entry.get("lowPriceVolume") or 0)
        if item_id in names and slower >= 240:   # about 10 an hour on each side
            ranked.append((slower, item_id))
    ranked.sort(reverse=True)
    rest = ranked[ITEMS_BUSIEST:]
    sample = ranked[:ITEMS_BUSIEST] + random.Random(1234).sample(rest, min(ITEMS_OTHERS, len(rest)))
    series = {}
    for count, (_slower, item_id) in enumerate(sample):
        series[item_id] = api.get_timeseries(int(item_id), "1h")
        time.sleep(0.3)
        if count % 50 == 0:
            print(f"downloaded {count}/{len(sample)}", flush=True)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(series, fh)
    return series


def mid(point):
    high, low = point.get("avgHighPrice"), point.get("avgLowPrice")
    return (high + low) / 2 if high and low else None


def replay(series: dict):
    """Each signal as (item, label, stamp acted on, {horizon: return})."""
    records = []
    for item_id, points in series.items():
        for t in range(WINDOW - 1, len(points) - 1):
            window = points[t - WINDOW + 1: t + 1]
            rows = [(p.get("avgHighPrice"), p.get("avgLowPrice"), p.get("timestamp"))
                    for p in reversed(window)]
            trend = analyzer.calculate_trend(rows)
            entry = mid(points[t + 1])
            if trend["data_points"] < analyzer.MARKET_MIN_PRICED_HOURS or not entry:
                continue
            returns = {h: max(-CAP, min(CAP, mid(points[t + 1 + h]) / entry - 1))
                       for h in HORIZONS
                       if t + 1 + h < len(points) and mid(points[t + 1 + h])}
            if returns:
                label = RULE(trend, analyzer.read_market(trend, window))
                records.append((item_id, label, points[t + 1]["timestamp"], returns))
    return records


def interval(by_item: dict, resamples: int = 500) -> tuple:
    rng = random.Random(7)
    items = list(by_item)
    totals = {i: (sum(by_item[i]), len(by_item[i])) for i in items}
    means = []
    for _ in range(resamples):
        total = count = 0
        for i in rng.choices(items, k=len(items)):
            total += totals[i][0]
            count += totals[i][1]
        means.append(total / count)
    means.sort()
    return means[int(resamples * .025)], means[int(resamples * .975)]


def main() -> None:
    records = replay(download())
    by_time = {h: defaultdict(list) for h in HORIZONS}
    for _item, _label, stamp, returns in records:
        for h, value in returns.items():
            by_time[h][stamp].append(value)
    market = {h: {s: statistics.mean(v) for s, v in by_time[h].items()} for h in HORIZONS}

    excess = {h: defaultdict(lambda: defaultdict(list)) for h in HORIZONS}
    raw = {h: defaultdict(list) for h in HORIZONS}
    for item_id, label, stamp, returns in records:
        for h, value in returns.items():
            excess[h][label][item_id].append(value - market[h][stamp])
            raw[h][label].append(value)

    print(f"{len(records):,} signals. '*' = clearly different from the market average.\n")
    for label in sorted({r[1] for r in records}):
        line = f"{label:10s}"
        for h in HORIZONS:
            by_item = excess[h].get(label)
            if not by_item:
                continue
            values = [v for vs in by_item.values() for v in vs]
            low, high = interval(by_item)
            star = "*" if low > 0 or high < 0 else " "
            line += (f"  next {h:>2}h: excess {statistics.mean(values) * 100:+.2f}%{star}"
                     f" raw {statistics.mean(raw[h][label]) * 100:+.2f}%")
        print(line)
    print("\nA buy label must show a positive * and a raw return above 2% to pay the tax.")


if __name__ == "__main__":
    main()
