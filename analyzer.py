"""
analyzer.py — Trend analysis, flip/profit maths, and buy/hold prediction.
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
            "data_points": len(history),
        }

    valid = [(h, l, ts) for h, l, ts in history if h and l]
    if not valid:
        return {
            "direction": "unknown", "strength": 0,
            "avg_high": 0, "avg_low": 0, "pct_change": 0,
            "data_points": 0,
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

    return {
        "direction":   direction,
        "strength":    abs(pct),
        "avg_high":    int(avg_high),
        "avg_low":     int(avg_low),
        "pct_change":  round(pct, 2),
        "data_points": len(valid),
    }


# ── Alert ─────────────────────────────────────────────────────────────────────

# ── Prediction ────────────────────────────────────────────────────────────────

def get_prediction(trend: dict, timeseries: list) -> dict:
    """
    Generate a BUY / HOLD / SELL suggestion from trend + volume data.

    Scoring rubric  (range −3 … +3):
      +1 / −1  price direction (rising / falling)
      +1 / −1  volume confirms or contradicts direction
      +1 / −1  momentum (recent change accelerating)
      Penalty  extreme moves (possible manipulation)
    """
    direction  = trend.get("direction", "unknown")
    pct_change = trend.get("pct_change", 0.0)

    # ── Volume from timeseries ────────────────────────────────────────────────
    vols: List[int] = []
    prices: List[float] = []

    for pt in timeseries:
        hv = pt.get("highPriceVolume") or 0
        lv = pt.get("lowPriceVolume") or 0
        vols.append(hv + lv)

        hp = pt.get("avgHighPrice") or 0
        lp = pt.get("avgLowPrice") or 0
        if hp and lp:
            prices.append((hp + lp) / 2)

    avg_vol    = sum(vols) / len(vols) if vols else 0
    recent_vol = vols[-1] if vols else 0

    if avg_vol > 0:
        vol_ratio = recent_vol / avg_vol
        if vol_ratio >= 1.3:
            vol_label = "high"
        elif vol_ratio <= 0.7:
            vol_label = "low"
        else:
            vol_label = "normal"
    else:
        vol_label = "unknown"

    # ── Momentum (compare last quarter vs first quarter of timeseries) ────────
    momentum = "flat"
    if len(prices) >= 4:
        half = len(prices) // 2
        early_avg = sum(prices[:half]) / half
        late_avg  = sum(prices[half:]) / (len(prices) - half)
        mom_pct   = ((late_avg - early_avg) / early_avg * 100) if early_avg else 0
        if mom_pct > 2:
            momentum = "accelerating"
        elif mom_pct < -2:
            momentum = "decelerating"

    # ── Score ─────────────────────────────────────────────────────────────────
    score = 0
    reasons: List[str] = []

    # Price direction
    if direction == "rising":
        score += 1
        reasons.append(f"Price is trending upward ({pct_change:+.1f}% vs history)")
    elif direction == "falling":
        score -= 1
        reasons.append(f"Price is trending downward ({pct_change:+.1f}% vs history)")
    else:
        reasons.append("Price is relatively stable in recent history")

    # Volume
    if vol_label == "high":
        if direction == "rising":
            score += 1
            reasons.append("High volume confirms the upward move")
        elif direction == "falling":
            score -= 1
            reasons.append("High volume confirms the downward move")
        else:
            reasons.append("Volume is elevated but price is stable")
    elif vol_label == "low":
        reasons.append("Low trading volume — trend signal is weak")
    elif vol_label == "normal":
        reasons.append("Volume is within normal range")

    # Momentum
    if momentum == "accelerating":
        score += 1
        reasons.append("Momentum is accelerating — move may continue")
    elif momentum == "decelerating":
        score -= 1
        reasons.append("Momentum is slowing — reversal possible")

    # Manipulation guard
    if abs(pct_change) > 25:
        reasons.append(
            f"⚠ Extreme price swing ({pct_change:+.1f}%) — "
            "possible market manipulation, trade with caution"
        )
        score = max(-1, min(score, 1))  # clamp near neutral

    # ── Map score to label ────────────────────────────────────────────────────
    if score >= 2:
        suggestion, confidence = "BUY",       "High"
    elif score == 1:
        suggestion, confidence = "BUY",       "Moderate"
    elif score == 0:
        suggestion, confidence = "HOLD",      "Neutral"
    elif score == -1:
        suggestion, confidence = "HOLD/SELL", "Moderate"
    else:
        suggestion, confidence = "SELL",      "High"

    return {
        "suggestion":  suggestion,
        "confidence":  confidence,
        "score":       score,
        "reasons":     reasons,
        "vol_label":   vol_label,
        "avg_volume":  int(avg_vol),
        "recent_vol":  int(recent_vol),
        "momentum":    momentum,
    }


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
