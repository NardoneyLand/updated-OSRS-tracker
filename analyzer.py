"""
analyzer.py — Trend analysis, market facts, and flip/profit maths.
"""

from typing import List, Optional, Tuple


# ── Trend ─────────────────────────────────────────────────────────────────────

def calculate_trend(history: List[Tuple]) -> dict:
    """
    Analyze stored price history rows (high, low, timestamp) — newest first.
    Returns a trend summary dict.
    """
    if len(history) < 2:
        return {
            "direction": "unknown", "strength": 0,
            "avg_high": 0, "avg_low": 0, "pct_change": 0,
            "data_points": len(history), "hours": 0,
        }

    valid = [(h, l, ts) for h, l, ts in history if h and l]
    if not valid:
        return {
            "direction": "unknown", "strength": 0,
            "avg_high": 0, "avg_low": 0, "pct_change": 0,
            "data_points": 0, "hours": 0,
        }

    mids = [(h + l) / 2 for h, l, _ in valid]
    newest, oldest = mids[0], mids[-1]
    pct = ((newest - oldest) / oldest) * 100 if oldest else 0

    if pct > 3:
        direction = "rising"
    elif pct < -3:
        direction = "falling"
    else:
        direction = "stable"

    avg_high = sum(h for h, _, _ in valid) / len(valid)
    avg_low  = sum(l for _, l, _ in valid) / len(valid)
    stamps = [ts for _, _, ts in valid if ts]

    return {
        "direction":   direction,
        "strength":    abs(pct),
        "avg_high":    int(avg_high),
        "avg_low":     int(avg_low),
        "pct_change":  round(pct, 2),
        "data_points": len(valid),
        "hours": round((max(stamps) - min(stamps)) / 3600) if stamps else 0,
    }


# ── Recent market facts ───────────────────────────────────────────────────────

MARKET_HOURS = 30             # how far back to look
MARKET_MIN_PRICED_HOURS = 6   # fewer than this is too thin to use


def read_market(trend: dict, points: list) -> dict:
    """
    Plain facts about an item's recent trading, from a trend summary and its
    hourly points (oldest first).

    This deliberately gives no Buy / Sell verdict. An earlier score that treated
    rising prices as a reason to buy did worse than picking at random: replayed
    over 100 days, BUY items lagged and SELL items beat the market, because
    prices tend to partly reverse. See tests/backtest.py before adding one back.
    """
    pct = trend.get("pct_change", 0.0)
    direction = trend.get("direction", "unknown")
    volumes = [(p.get("highPriceVolume") or 0) + (p.get("lowPriceVolume") or 0)
               for p in points]
    prices = [(p["avgHighPrice"] + p["avgLowPrice"]) / 2 for p in points
              if p.get("avgHighPrice") and p.get("avgLowPrice")]

    avg_volume = sum(volumes) / len(volumes) if volumes else 0
    recent_volume = volumes[-1] if volumes else 0
    volume = "unknown"
    if avg_volume > 0:
        ratio = recent_volume / avg_volume
        volume = "high" if ratio >= 1.3 else "low" if ratio <= 0.7 else "normal"

    # Prices in the later half of the period against the earlier half.
    momentum = "flat"
    if len(prices) >= 4:
        half = len(prices) // 2
        early = sum(prices[:half]) / half
        late = sum(prices[half:]) / (len(prices) - half)
        change = (late - early) / early * 100 if early else 0
        momentum = "rising" if change > 2 else "falling" if change < -2 else "flat"

    notes = []
    if abs(pct) > 25:
        notes.append(f"⚠ Extreme price swing ({pct:+.1f}%). This could be "
                     "manipulation, so be careful.")
    if direction == "unknown":
        notes.append("Not enough price history to judge the move.")
    elif direction != "stable":
        notes.append("Big moves often partly reverse, but usually by less than "
                     "the 2% GE tax.")
    if volume == "low":
        notes.append("Trading is quiet right now, so this move means less.")

    return {"volume": volume, "recent_volume": int(recent_volume),
            "usual_volume": int(avg_volume), "momentum": momentum, "notes": notes}


def rolling_hours(points_5m: list, now: Optional[float] = None) -> List[dict]:
    """
    Turn an item's 5-minute history into hours counted back from now, oldest
    first, in the shape of its 1-hour history. The Wiki's own hourly history
    stops over an hour back, while this reaches the last few minutes. An hour
    with no trades has no 5-minute rows, so it comes out as zero volume.
    """
    import time as _time
    now = int(now if now is not None else _time.time())
    end = now - now % 300
    start = end - MARKET_HOURS * 3600
    hours = [[0, 0, 0, 0] for _ in range(MARKET_HOURS)]
    for point in points_5m:
        stamp = point.get("timestamp") or 0
        if not start <= stamp < end:
            continue
        hour = hours[(stamp - start) // 3600]
        high_vol, low_vol = (point.get("highPriceVolume") or 0,
                             point.get("lowPriceVolume") or 0)
        high, low = point.get("avgHighPrice"), point.get("avgLowPrice")
        if high_vol and high:
            hour[0] += high_vol
            hour[2] += high * high_vol
        if low_vol and low:
            hour[1] += low_vol
            hour[3] += low * low_vol
    return [{
        "timestamp": start + index * 3600,
        "highPriceVolume": t[0], "lowPriceVolume": t[1],
        "avgHighPrice": round(t[2] / t[0]) if t[0] else None,
        "avgLowPrice": round(t[3] / t[1]) if t[1] else None,
    } for index, t in enumerate(hours)]


def recent_market(points_5m: list, fallback_rows: List[Tuple],
                  fallback_points: list, live: Optional[Tuple[int, int]] = None,
                  now: Optional[float] = None) -> Tuple[dict, dict]:
    """
    (trend, facts) for an item, from rolling hours where it trades enough. For a
    thinly traded item, or if the 5-minute history is unavailable, uses the
    fallback: price rows newest first, and 1-hour points oldest first.
    """
    import time as _time
    now = now if now is not None else _time.time()
    hours = rolling_hours(points_5m, now)
    rows = [(p["avgHighPrice"], p["avgLowPrice"], p["timestamp"])
            for p in reversed(hours)
            if p["avgHighPrice"] and p["avgLowPrice"]]
    if len(rows) < MARKET_MIN_PRICED_HOURS:
        trend = calculate_trend(fallback_rows)
        return trend, read_market(trend, fallback_points)
    if live and live[0] and live[1]:
        rows = [(live[0], live[1], now)] + rows
    trend = calculate_trend(rows[:MARKET_HOURS])
    return trend, read_market(trend, hours)


# ── Volatility scanner ────────────────────────────────────────────────────────

def find_volatile_items(
    latest: dict,
    bulk_avg: dict,
    mapping: list,
    min_volume: int = 1000,
    top_n: int = 100,
) -> list:
    """
    Identify items with the largest mid-price movement.

    Compares the current /latest mid-price to the windowed average mid-price
    from a bulk endpoint (/5m, /1h, /6h, /24h). Filters items whose total
    volume in the window is below `min_volume` to suppress noise from
    illiquid items where a single trade can swing the price.

    Returns a list of dicts (sorted by abs(pct_change) desc):
        {item_id, name, current, avg, pct_change, volume}
    """
    name_by_id = {item["id"]: item["name"] for item in mapping}
    results = []

    for item_id_str, current in latest.items():
        try:
            item_id = int(item_id_str)
        except (TypeError, ValueError):
            continue

        avg_data = bulk_avg.get(item_id_str)
        if not avg_data:
            continue

        vol = ((avg_data.get("highPriceVolume") or 0)
               + (avg_data.get("lowPriceVolume") or 0))
        if vol < min_volume:
            continue

        cur_high = current.get("high")
        cur_low = current.get("low") or cur_high
        if not cur_high:
            continue
        cur_mid = (cur_high + cur_low) / 2

        avg_high = avg_data.get("avgHighPrice")
        avg_low = avg_data.get("avgLowPrice") or avg_high
        if not avg_high:
            continue
        avg_mid = (avg_high + avg_low) / 2
        if avg_mid == 0:
            continue

        pct = (cur_mid - avg_mid) / avg_mid * 100

        results.append({
            "item_id":    item_id,
            "name":       name_by_id.get(item_id, f"Item #{item_id}"),
            "current":    int(cur_mid),
            "avg":        int(avg_mid),
            "pct_change": round(pct, 2),
            "volume":     int(vol),
        })

    results.sort(key=lambda r: abs(r["pct_change"]), reverse=True)
    return results[:top_n]


# ── Flip recommendations (FlipRec) ────────────────────────────────────────────

GE_TAX_RATE = 0.02            # 2% of the sell price, per item
GE_TAX_CAP = 5_000_000        # max tax per item
# The rate and the exempt list have changed before — verify against the OSRS
# Wiki "Grand Exchange tax" page. Exempt items pay no tax at any price.
GE_TAX_EXEMPT_NAMES = {
    "Old school bond",
    "Chisel", "Gardening trowel", "Glassblowing pipe", "Hammer", "Needle",
    "Pestle and mortar", "Rake", "Saw", "Secateurs", "Shears", "Spade",
    "Watering can",
}

FLIP_MAX_AGE = 3600           # a side idle longer than this => "Inactive"
FLIP_SLOW_AGE = 300           # idle longer than this => flagged "slow": no
                              # alerts, and a warning on the other tabs
FLIP_MIN_ROI = 1.0            # % net ROI needed to count as "Profitable"
FLIP_MAX_ROI = 40.0           # above this the margin is probably not real
FLIP_MIN_5M_SUPPORT = 0.5     # the 5m averages must back up this share of the
                              # margin. In practice real margins sit near 100%
                              # and one-off trades at 0% or below.
FLIP_MIN_SIDE_VOLUME = 10     # min items traded per side in the last hour
VOLUME_PACE_UP = 3.0          # the last hour vs an average hour over the day
VOLUME_PACE_DOWN = 0.5
REVERSION_THRESHOLD = 3.0     # % away from the 1h average to flag
REVERSION_EXTREME = 25.0      # beyond this, treat as possible manipulation

# Best verdict first when sorting (higher = better).
VERDICT_RANK = {"Profitable": 3, "Marginal": 2, "Suspect": 1, "Inactive": 0,
                "Unprofitable": -1}


def ge_tax(sell_price: int, exempt: bool = False) -> int:
    """GE tax paid by the seller: floor(2%), so under 50 gp it is 0."""
    if exempt or sell_price <= 0:
        return 0
    return min(int(sell_price * GE_TAX_RATE), GE_TAX_CAP)


def parse_gp_amount(text: str) -> int:
    """Parse '10m', '2.5b', '500k', '1,000,000' into gp. Raises ValueError."""
    t = (text or "").strip().lower().replace(",", "").replace("_", "")
    if t.endswith("gp"):
        t = t[:-2].strip()
    if not t:
        return 0
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(t[-1])
    if mult:
        t = t[:-1]
    value = float(t) * (mult or 1)
    if value < 0:
        raise ValueError("negative amount")
    return int(value)


def reversion_signal(current_mid: float, avg_mid: float) -> Tuple[str, float]:
    """Compare the current mid price to the window average."""
    if not avg_mid:
        return "", 0.0
    pct = (current_mid - avg_mid) / avg_mid * 100
    if abs(pct) > REVERSION_EXTREME:
        return "Extreme move - caution", pct
    if pct <= -REVERSION_THRESHOLD:
        return "Dip - may rebound", pct
    if pct >= REVERSION_THRESHOLD:
        return "Spike - may fall", pct
    return "", pct


def volume_pace(volume_1h: int, volume_24h: Optional[int]) -> str:
    """
    '▲' or '▼' when the last hour's trading is far from the day's usual pace,
    otherwise ''. Compares the slower side of each. Very quiet items are left
    out, since a couple of trades there would swing it wildly.
    """
    if not volume_24h:
        return ""
    usual = volume_24h / 24
    if max(volume_1h, usual) < FLIP_MIN_SIDE_VOLUME:
        return ""
    if volume_1h >= usual * VOLUME_PACE_UP:
        return "▲"
    if volume_1h < usual * VOLUME_PACE_DOWN:
        return "▼"
    return ""


def margin_at_5m_prices(avg_5m: Optional[dict], buy: int, sell: int, net: int,
                        exempt: bool) -> Tuple[Optional[int], bool]:
    """
    What the margin would be at the last 5 minutes' average prices, and whether
    that backs up the margin from the latest trades. A side with no trades in
    the window keeps its latest price, so the other side is still checked.
    No 5m data at all proves nothing either way, so it counts as backed up.
    """
    avg_5m = avg_5m or {}
    avg_sell, avg_buy = avg_5m.get("avgHighPrice"), avg_5m.get("avgLowPrice")
    if not avg_sell and not avg_buy:
        return None, True
    sell_5m = avg_sell or sell
    net_5m = int(sell_5m - ge_tax(sell_5m, exempt) - (avg_buy or buy))
    return net_5m, net <= 0 or net_5m >= net * FLIP_MIN_5M_SUPPORT


def flip_row(item: dict, lt: dict, avg: dict, capital: int = 0,
             now: Optional[float] = None,
             avg_5m: Optional[dict] = None) -> Optional[dict]:
    """
    Flip stats for one item, profitable or not (used by FlipRec, Alerts and
    the Watchlist). `lt` is the item's /latest entry, `avg` its /1h entry,
    `avg_5m` its /5m entry if known. Returns None if either side has no price.

    Buys at the latest instant-sell price (`low`) and sells at the latest
    instant-buy price (`high`), minus the GE tax: the same numbers an in-game
    margin check gives. The averages are used only for volume and as a sanity
    check: one odd trade shows up as a huge ROI, an extreme move vs the 1h
    average or a margin the 5m averages don't back up, and is marked 'Suspect'.

    Keys: item_id, name, buy, sell, tax, net, roi, limit, qty, cost, profit,
    volume (min per-side 1h volume), age (seconds since the older side last
    traded), traded_at (when that was), slow (active, but a side idle >
    FLIP_SLOW_AGE), active, affordable, verdict, reversion, reversion_pct,
    net_5m (the margin at 5m average prices, None without 5m data), backed_5m.
    """
    import time as _time
    now = now if now is not None else _time.time()
    avg = avg or {}
    item_id = item.get("id")
    # Latest trades, not 1h averages: averages lag a moving market and
    # overstate margins (e.g. a buy price that is no longer available).
    sell, buy = (lt or {}).get("high"), (lt or {}).get("low")
    if not sell or not buy:
        return None

    exempt = item.get("name") in GE_TAX_EXEMPT_NAMES
    tax = ge_tax(sell, exempt)
    net = sell - tax - buy
    roi = net / buy * 100

    hv = avg.get("highPriceVolume") or 0
    lv = avg.get("lowPriceVolume") or 0
    volume = min(hv, lv)

    high_age = now - (lt.get("highTime") or 0)
    low_age = now - (lt.get("lowTime") or 0)
    active = (high_age <= FLIP_MAX_AGE and low_age <= FLIP_MAX_AGE
              and hv > 0 and lv > 0)

    limit = item.get("limit") or None
    affordable = True
    if capital > 0:
        can_buy = capital // buy
        affordable = can_buy >= 1
        qty = min(limit, can_buy) if limit else can_buy
    else:
        qty = limit or 0
    if active and qty:
        # Realistic fill: you can't buy more than the market trades.
        # Roughly 4 hours of the slower side's current hourly volume.
        qty = min(qty, max(1, volume * 4))
    profit = max(net, 0) * qty

    a_hi, a_lo = avg.get("avgHighPrice"), avg.get("avgLowPrice")
    if a_hi and a_lo:
        reversion, rev_pct = reversion_signal((sell + buy) / 2,
                                              (a_hi + a_lo) / 2)
    else:
        reversion, rev_pct = "", 0.0

    net_5m, backed_5m = margin_at_5m_prices(avg_5m, buy, sell, net, exempt)

    if not active:
        verdict = "Inactive"
    elif net <= 0:
        verdict = "Unprofitable"
    elif (roi > FLIP_MAX_ROI or reversion.startswith("Extreme")
          or not backed_5m):
        verdict = "Suspect"   # too good to be true / possible manipulation
    elif roi >= FLIP_MIN_ROI and volume >= FLIP_MIN_SIDE_VOLUME:
        verdict = "Profitable"
    else:
        verdict = "Marginal"

    age = max(high_age, low_age)
    return {
        "item_id": item_id, "name": item.get("name", f"Item #{item_id}"),
        "buy": buy, "sell": sell, "tax": tax, "net": net,
        "roi": round(roi, 2), "limit": limit, "qty": qty,
        "cost": buy * qty, "profit": profit, "volume": volume,
        "age": int(age), "traded_at": int(now - age), "active": active,
        "slow": active and age > FLIP_SLOW_AGE,
        "affordable": affordable, "verdict": verdict,
        "reversion": reversion, "reversion_pct": round(rev_pct, 1),
        "net_5m": net_5m, "backed_5m": backed_5m,
    }


def find_flips(
    latest: dict,
    bulk_1h: dict,
    mapping: list,
    capital: int = 0,
    membership: str = "all",      # 'all' | 'members' | 'f2p'
    now: Optional[float] = None,
    top_n: int = 0,                # 0 = return everything
    bulk_5m: Optional[dict] = None,
) -> list:
    """
    All items with a positive after-tax margin that you can afford at least
    one of, as flip_row() dicts, best first. Items whose buy or sell side
    has not traded within FLIP_MAX_AGE are kept but flagged active=False /
    verdict 'Inactive' (their margin is likely phantom).
    """
    bulk_5m = bulk_5m or {}
    results = []
    for item in mapping:
        if membership == "members" and not item.get("members"):
            continue
        if membership == "f2p" and item.get("members"):
            continue
        item_id = str(item.get("id"))
        lt = latest.get(item_id)
        if not lt:
            continue
        row = flip_row(item, lt, bulk_1h.get(item_id), capital, now,
                       bulk_5m.get(item_id))
        if row and row["net"] > 0 and row["affordable"]:
            results.append(row)

    # Best first: trustworthy verdicts, then realistic total profit when we
    # know the quantity (else margin per item).
    results.sort(key=lambda r: (VERDICT_RANK[r["verdict"]],
                                r["profit"] or r["net"]),
                 reverse=True)
    return results[:top_n] if top_n else results
