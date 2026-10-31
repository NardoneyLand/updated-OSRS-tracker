"""
analyzer.py — Trend analysis, alert detection, and buy/hold prediction.
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

def check_price_alert(
    item_id: int,
    item_name: str,
    current_high: int,
    current_low: int,
    last_record: Optional[Tuple],
    threshold: float = 0.10,
) -> Optional[dict]:
    """
    Return an alert dict if mid-price shifted ≥ threshold (default 10%)
    since the last recorded snapshot, otherwise None.
    """
    if not last_record:
        return None

    old_high, old_low, _ = last_record
    if not old_high:
        return None

    mid_old = (old_high + (old_low or old_high)) / 2
    mid_new = (current_high + (current_low or current_high)) / 2

    if mid_old == 0:
        return None

    pct = ((mid_new - mid_old) / mid_old) * 100

    if abs(pct) >= threshold * 100:
        return {
            "item_id":    item_id,
            "item_name":  item_name,
            "alert_type": "spike" if pct > 0 else "drop",
            "old_price":  int(mid_old),
            "new_price":  int(mid_new),
            "pct_change": round(pct, 2),
        }
    return None


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
FLIP_MIN_ROI = 1.0            # % net ROI needed to count as "Profitable"
FLIP_MAX_ROI = 40.0           # above this the margin is probably not real
FLIP_MIN_SIDE_VOLUME = 10     # min items traded per side in the last hour
REVERSION_THRESHOLD = 3.0     # % away from the 1h average to flag
REVERSION_EXTREME = 25.0      # beyond this, treat as possible manipulation


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


def find_flips(
    latest: dict,
    bulk_1h: dict,
    mapping: list,
    capital: int = 0,
    membership: str = "all",      # 'all' | 'members' | 'f2p'
    now: Optional[float] = None,
    top_n: int = 0,                # 0 = return everything
) -> list:
    """
    Find items where buying at the 1h-average instant-sell price (avgLowPrice)
    and selling at the 1h-average instant-buy price (avgHighPrice) leaves a
    profit after the GE tax. Falls back to the latest trades when an item has
    no 1h average. The reversion signal compares the latest mid to the average.

    Each result dict: item_id, name, buy, sell, tax, net, roi, limit, qty,
    cost, profit, volume (min per-side 1h volume), active, verdict,
    reversion, reversion_pct.
    Items whose buy or sell side has not traded within FLIP_MAX_AGE are kept
    but flagged active=False / verdict 'Inactive' (their margin is likely
    phantom).
    """
    import time as _time
    now = now if now is not None else _time.time()
    results = []

    for item in mapping:
        item_id = item.get("id")
        if membership == "members" and not item.get("members"):
            continue
        if membership == "f2p" and item.get("members"):
            continue

        lt = latest.get(str(item_id))
        if not lt:
            continue
        avg = bulk_1h.get(str(item_id)) or {}
        # Prefer the 1h average prices: a single odd trade in /latest can
        # fake a huge margin. Fall back to the latest trade if no average.
        sell = avg.get("avgHighPrice") or lt.get("high")
        buy = avg.get("avgLowPrice") or lt.get("low")
        if not sell or not buy:
            continue

        exempt = item.get("name") in GE_TAX_EXEMPT_NAMES
        tax = ge_tax(sell, exempt)
        net = sell - tax - buy
        if net <= 0:
            continue
        roi = net / buy * 100

        hv = avg.get("highPriceVolume") or 0
        lv = avg.get("lowPriceVolume") or 0
        volume = min(hv, lv)

        high_age = now - (lt.get("highTime") or 0)
        low_age = now - (lt.get("lowTime") or 0)
        active = (high_age <= FLIP_MAX_AGE and low_age <= FLIP_MAX_AGE
                  and hv > 0 and lv > 0)

        limit = item.get("limit") or None
        if capital > 0:
            affordable = capital // buy
            if affordable < 1:
                continue          # can't afford even one
            qty = min(limit, affordable) if limit else affordable
        else:
            qty = limit or 0
        if active and qty:
            # Realistic fill: you can't buy more than the market trades.
            # Roughly 4 hours of the slower side's current hourly volume.
            qty = min(qty, max(1, volume * 4))
        profit = net * qty

        a_hi, a_lo = avg.get("avgHighPrice"), avg.get("avgLowPrice")
        if a_hi and a_lo:
            reversion, rev_pct = reversion_signal(
                (lt.get("high", sell) + lt.get("low", buy)) / 2,
                (a_hi + a_lo) / 2)
        else:
            reversion, rev_pct = "", 0.0

        if not active:
            verdict = "Inactive"
        elif roi > FLIP_MAX_ROI or reversion.startswith("Extreme"):
            verdict = "Suspect"   # too good to be true / possible manipulation
        elif roi >= FLIP_MIN_ROI and volume >= FLIP_MIN_SIDE_VOLUME:
            verdict = "Profitable"
        else:
            verdict = "Marginal"

        results.append({
            "item_id": item_id, "name": item.get("name", f"Item #{item_id}"),
            "buy": buy, "sell": sell, "tax": tax, "net": net,
            "roi": round(roi, 2), "limit": limit, "qty": qty,
            "cost": buy * qty, "profit": profit, "volume": volume,
            "active": active, "verdict": verdict,
            "reversion": reversion, "reversion_pct": round(rev_pct, 1),
        })

    # Best first: trustworthy verdicts, then realistic total profit when we
    # know the quantity (else margin per item).
    rank = {"Profitable": 3, "Marginal": 2, "Suspect": 1, "Inactive": 0}
    results.sort(key=lambda r: (rank[r["verdict"]], r["profit"] or r["net"]),
                 reverse=True)
    return results[:top_n] if top_n else results
