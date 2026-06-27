#!/usr/bin/env python3
"""Тесты метрик импульса (volume z-score, ATR spike, ускорение цены)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from telegram_agent.scanner_impulse_metrics import (
    ImpulseMetricsConfig,
    analyze_impulse_metrics,
    compute_atr_pct,
    compute_price_acceleration,
    compute_volume_stats,
    impulse_metrics_pass_filters,
)


def _k(o: float, c: float, v: float = 100.0) -> dict:
    return {
        "open": o,
        "close": c,
        "high": max(o, c) * 1.002,
        "low": min(o, c) * 0.998,
        "volume": v,
    }


def test_volume_zscore_detects_spike():
    base = [_k(100.0, 100.1, 60.0 + i * 3.0) for i in range(12)]
    impulse = _k(100.0, 103.4, 400.0)
    klines = base + [impulse]
    ratio, zscore, reliable = compute_volume_stats(klines, impulse=impulse, lookback=12)
    assert ratio > 3.0
    assert reliable is True
    assert zscore >= 2.0


def test_atr_pct_is_not_candle_move():
    base = [_k(100.0, 100.05, 80.0) for _ in range(14)]
    impulse = _k(100.0, 103.4, 220.0)
    klines = base + [impulse]
    atr_pct = compute_atr_pct(klines[:-1], period=14)
    move_pct = abs((103.4 - 100.0) / 100.0 * 100.0)
    assert atr_pct < move_pct


def test_analyze_impulse_metrics_flags_volatility_spike():
    base = [_k(100.0, 100.05, 80.0) for _ in range(14)]
    impulse = _k(100.0, 104.0, 300.0)
    impulse["high"] = 104.5
    impulse["low"] = 99.8
    klines = base + [impulse]
    cfg = ImpulseMetricsConfig(
        volume_zscore_min=2.0,
        min_volume_ratio=1.25,
        atr_spike_ratio_min=1.5,
    )
    metrics = analyze_impulse_metrics(klines, impulse, cfg)
    assert metrics.volume_spike is True
    assert metrics.volatility_spike is True
    assert metrics.atr_pct > 0
    assert metrics.atr_spike_ratio >= 1.5
    assert impulse_metrics_pass_filters(metrics, cfg) is True


def test_impulse_metrics_reject_without_volume_spike():
    base = [_k(100.0, 100.05, 100.0) for _ in range(12)]
    impulse = _k(100.0, 104.0, 105.0)
    klines = base + [impulse]
    cfg = ImpulseMetricsConfig(volume_zscore_min=2.0, min_volume_ratio=1.25)
    metrics = analyze_impulse_metrics(klines, impulse, cfg)
    assert metrics.volume_spike is False
    assert impulse_metrics_pass_filters(metrics, cfg) is False


def test_price_acceleration_positive_on_impulse():
    base = [_k(100.0, 100.02, 80.0) for _ in range(6)]
    impulse = _k(100.0, 103.0, 220.0)
    klines = base + [impulse]
    accel = compute_price_acceleration(klines, bars=3)
    assert accel > 0.1
