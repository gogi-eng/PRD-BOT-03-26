#!/usr/bin/env python3
"""Тесты детектора 15m памп/дамп (spike scalp)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from telegram_agent.pump_dump_spike_scan import (
    SpikeScanConfig,
    analyze_spike_setup,
    candle_move_pct,
    spike_invalidation_and_target,
)


def _k(o: float, c: float, v: float = 100.0) -> dict:
    return {
        "open": o,
        "close": c,
        "high": max(o, c) * 1.002,
        "low": min(o, c) * 0.998,
        "volume": v,
    }


def test_candle_move_pct_pump():
    assert abs(candle_move_pct(_k(100.0, 103.5)) - 3.5) < 1e-9


def test_analyze_spike_detects_pump():
    cfg = SpikeScanConfig(enabled=True, min_move_pct=3.0, min_volume_ratio=1.0)
    base = [_k(100.0, 100.1, 80.0) for _ in range(8)]
    impulse = _k(100.0, 103.4, 220.0)
    klines = base + [impulse]
    row = analyze_spike_setup(symbol="SOLUSDT", klines=klines, turnover_24h=12_000_000, cfg=cfg)
    assert row is not None
    assert row["scenario"] == "PUMP"
    assert row["score"] >= 72
    assert row["range_pct"] >= 3.0


def test_analyze_spike_rejects_small_move():
    cfg = SpikeScanConfig(enabled=True, min_move_pct=3.0, min_volume_ratio=1.0)
    klines = [_k(100.0, 101.5, 200.0) for _ in range(10)]
    assert analyze_spike_setup(symbol="BTCUSDT", klines=klines, turnover_24h=50_000_000, cfg=cfg) is None


def test_spike_sl_tp_long():
    candle = _k(100.0, 103.0)
    inv, tgt = spike_invalidation_and_target(
        scenario="PUMP",
        price=103.0,
        candle=candle,
        sl_buffer_pct=0.25,
        min_rr=1.5,
    )
    assert inv < 103.0
    assert tgt > 103.0
    risk = 103.0 - inv
    assert abs((tgt - 103.0) - risk * 1.5) < 1e-6
