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
