#!/usr/bin/env python3
"""Unit tests for SCALP session strategy."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from strategy.scalp import ScalpSessionStrategy


def _kline(ts_ms: int, o: float, c: float, v: float) -> dict:
    hi = max(o, c) * 1.001
    lo = min(o, c) * 0.999
    return {
        "time": ts_ms,
        "open": o,
        "high": hi,
        "low": lo,
        "close": c,
        "volume": v,
    }


def _build_series_for_hour(
    local_hour: int,
    tz_offset: int = 3,
    bars: int = 30,
    impulse_mult: float = 1.012,
    volume_last: float = 260.0,
) -> list[dict]:
    """Builds a short 5m series ending exactly in target local hour."""
    utc_hour = (local_hour - tz_offset) % 24
    end = datetime(2024, 11, 1, utc_hour, 0, tzinfo=timezone.utc)
    start = end - timedelta(minutes=5 * (bars - 1))

    arr: list[dict] = []
    price = 100.0
    for i in range(bars - 1):
        ts = int((start.timestamp() + i * 300) * 1000)
        arr.append(_kline(ts, price, price * 1.0002, 100.0))
        price *= 1.0002

    ts = int((start.timestamp() + (bars - 1) * 300) * 1000)
    arr.append(_kline(ts, price, price * impulse_mult, volume_last))
    return arr


def test_scalp_generates_buy_in_configured_pump_hour():
    strat = ScalpSessionStrategy(
        config={
            "enabled": True,
            "timezone_offset": 3,
            "pump_hours_local": [3, 4, 5, 7, 11, 14, 18],
            "dump_hours_local": [3, 4, 5, 13, 19, 20, 21],
            "min_impulse_pct": 0.45,
            "min_confirm_move_pct": 0.3,
            "min_volume_ratio": 1.5,
            "confirm_bars": 3,
            "cooldown_bars": 6,
        }
    )
    klines = _build_series_for_hour(local_hour=4, tz_offset=3)
    result = strat.analyze("BTCUSDT", klines)
    assert result is not None
    assert result["signal"] == "BUY"
    assert "SCALP PUMP" in result["reason"]


def test_scalp_generates_sell_in_configured_dump_hour():
    strat = ScalpSessionStrategy(
        config={
            "enabled": True,
            "timezone_offset": 3,
            "pump_hours_local": [3, 4, 5],
            "dump_hours_local": [3, 4, 5, 13],
            "min_impulse_pct": 0.40,
            "min_confirm_move_pct": 0.30,
            "min_volume_ratio": 1.3,
            "confirm_bars": 3,
            "cooldown_bars": 6,
        }
    )
    klines = _build_series_for_hour(local_hour=13, tz_offset=3, impulse_mult=0.988, volume_last=240.0)
    result = strat.analyze("ETHUSDT", klines)
    assert result is not None
    assert result["signal"] == "SELL"
    assert "SCALP DUMP" in result["reason"]


def test_scalp_respects_hour_filter():
    strat = ScalpSessionStrategy(
        config={
            "enabled": True,
            "timezone_offset": 3,
            "pump_hours_local": [3, 4, 5],
            "dump_hours_local": [3, 4, 5],
            "min_impulse_pct": 0.4,
            "min_confirm_move_pct": 0.3,
            "min_volume_ratio": 1.3,
            "confirm_bars": 3,
            "cooldown_bars": 6,
        }
    )
    klines = _build_series_for_hour(local_hour=10, tz_offset=3)
    result = strat.analyze("SOLUSDT", klines)
    assert result is None
