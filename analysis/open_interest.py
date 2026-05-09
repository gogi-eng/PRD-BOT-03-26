#!/usr/bin/env python3
"""Open Interest helpers: changes vs Bybit buckets, divergence, trend strength."""
from __future__ import annotations

import math
from typing import Dict, List, Tuple


def _f_oi_hist(row: Dict, key: str = "openInterest") -> float:
    try:
        return float(row.get(key, 0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def oi_pct_change_recent(hist: List[Dict]) -> Tuple[float, bool]:
    """(oi_now - oi_prev_bucket) / oi_prev_bucket using two newest buckets (Bybit list = newest first)."""
    if not hist or len(hist) < 2:
        return 0.0, False
    now = _f_oi_hist(hist[0])
    prev = _f_oi_hist(hist[1])
    if prev <= 0 or now <= 0:
        return 0.0, False
    return (now - prev) / prev, True


def price_close_change_frac(klines: List[Dict], bars: int = 5) -> float:
    """(close[-1] / close[-bars] - 1)."""
    if not klines or len(klines) < bars + 1:
        return 0.0
    c0 = float(klines[-1].get("close", 0) or 0.0)
    c1 = float(klines[-1 - bars].get("close", 0) or 0.0)
    if c1 <= 0 or c0 <= 0:
        return 0.0
    return (c0 / c1) - 1.0


def divergence_score(price_chg_frac: float, oi_chg: float) -> float:
    """Same sign ⇒ confirmation (positive tanh); opposite sign ⇒ negative (trap / fade)."""
    return math.tanh(price_chg_frac * 60.0 * oi_chg * 25.0)


def trend_strength_score(oi_5m: float, oi_15m: float) -> float:
    """Magnitude-aligned strength in [-1, 1]."""
    x = (oi_5m * 1.85 + oi_15m * 1.0) / 2.85
    return math.tanh(x * 12.0)


def build_open_interest_pack(hist_5m: List[Dict], hist_15m: List[Dict], klines: List[Dict]) -> Dict[str, float]:
    """
    Returns fractional OI deltas (e.g. 0.03 = +3%), derived scores, spike flag [0..1].

    Uses two newest buckets per horizon (approx. Δ between last two buckets of that timeframe).
    """
    oi5, ok5 = oi_pct_change_recent(hist_5m or [])
    oi15, ok15 = oi_pct_change_recent(hist_15m or [])
    available = bool(ok5 or ok15)
    pr5 = price_close_change_frac(klines, bars=5)
    div = divergence_score(pr5, oi5) if ok5 else 0.0
    tstr = trend_strength_score(oi5 if ok5 else 0.0, oi15 if ok15 else 0.0)
    spike = 1.0 if (abs(oi5) >= 0.08 or abs(oi15) >= 0.08) else 0.0
    return {
        "available": float(1.0 if available else 0.0),
        "oi_change_5m": float(oi5),
        "oi_change_15m": float(oi15),
        "oi_trend_strength": float(tstr),
        "oi_price_divergence": float(div),
        "oi_spike": float(spike),
        "price_change_5m": float(pr5),
    }


def empty_oi_pack() -> Dict[str, float]:
    return {
        "available": 0.0,
        "oi_change_5m": 0.0,
        "oi_change_15m": 0.0,
        "oi_trend_strength": 0.0,
        "oi_price_divergence": 0.0,
        "oi_spike": 0.0,
        "price_change_5m": 0.0,
    }
