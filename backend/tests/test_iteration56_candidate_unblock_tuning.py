#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

BOT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "bot" / "config.yaml"


def test_candidate_unblock_tuning_values_present():
    source = BOT_CONFIG_PATH.read_text(encoding="utf-8")

    assert "min_volatility_pct: 0.06" in source
    assert "entry_threshold: 0.64" in source
    assert "min_orderflow_imbalance: 1.24" in source
    assert "buy_volume_guard_min_ratio: 0.50" in source
    assert "buy_momentum_guard_min_pct: 0.35" in source
    assert "ema_guard_min_diff_pct: 0.30" in source
    assert "impulse_min_body_atr: 0.45" in source
    assert "retest_max_body_ratio: 0.85" in source
    assert "early_exit_bars: 45" in source
    assert "early_exit_min_profit_atr: 0.35" in source
    assert "ema_exit_confirm_bars: 3" in source
    assert "signal_cooldown_sec: 5400" in source

    assert "trend_require_4h_trend: false" in source
    assert "range_require_4h_trend: false" in source
