"""Tests for engine.execution_ai.ExecutionAI."""
from __future__ import annotations

import numpy as np

from engine.execution_ai import ExecutionAI


def _flat_klines(n: int = 60, close: float = 100.0) -> list:
    return [{"open": close, "close": close, "high": close + 0.01, "low": close - 0.01} for _ in range(n)]


def _book(bid_sz: float = 100.0, ask_sz: float = 100.0) -> dict:
    return {"bids": [[100.0, bid_sz]], "asks": [[100.05, ask_sz]]}


def test_disabled_pass_through():
    ai = ExecutionAI(enabled=False)
    v = ai.evaluate("BUY", _flat_klines(), _book(), 0.6)
    assert v.allow_entry is True
    assert v.signal_boost == 1.0


def test_high_volatility_blocks():
    ai = ExecutionAI(enabled=True, volatility_limit=0.001, skip_on_wait_pullback=False)
    closes = np.linspace(100, 110, 80)
    kl = [{"open": float(c), "close": float(c)} for c in closes]
    v = ai.evaluate("BUY", kl, _book(), 0.8)
    assert v.allow_entry is False
    assert "high_volatility" in v.skip_reason


def test_wide_spread_blocks():
    ai = ExecutionAI(enabled=True, spread_max_pct=0.01, skip_on_wait_pullback=False)
    ob = {"bids": [[100.0, 50.0]], "asks": [[101.0, 50.0]]}
    v = ai.evaluate("BUY", _flat_klines(), ob, 0.8)
    assert v.allow_entry is False
    assert "wide_spread" in v.skip_reason


def test_wait_pullback_blocks_long_when_momentum_up():
    ai = ExecutionAI(enabled=True, pullback_momentum_bars=3, skip_on_wait_pullback=True)
    kl = _flat_klines(60, 100.0)
    for i in range(-4, 0):
        kl[i] = {"open": 100.0 + i, "close": 101.0 + i, "high": 102.0, "low": 99.0}
    v = ai.evaluate("BUY", kl, _book(), 0.8)
    assert v.allow_entry is False
    assert "wait_pullback" in v.skip_reason


def test_microstructure_boost_long_bid_heavy():
    ai = ExecutionAI(
        enabled=True,
        skip_on_wait_pullback=False,
        imbalance_high_ratio=1.05,
        imbalance_boost_high=1.1,
    )
    v = ai.evaluate("BUY", _flat_klines(), _book(bid_sz=200.0, ask_sz=50.0), 0.5)
    assert v.allow_entry is True
    assert abs(v.signal_boost - 1.1) < 1e-6
    assert abs(v.effective_confidence - 0.55) < 1e-6


def test_min_confidence_after_boost():
    ai = ExecutionAI(
        enabled=True,
        skip_on_wait_pullback=False,
        min_confidence_after_boost=0.6,
        imbalance_boost_low=0.9,
        imbalance_low_ratio=10.0,
    )
    v = ai.evaluate("BUY", _flat_klines(), _book(bid_sz=10.0, ask_sz=100.0), 0.7)
    assert v.allow_entry is False
    assert "low_conf_after_boost" in v.skip_reason


def test_scale_fractions_normalize():
    ai = ExecutionAI(scaled_entry=True, scale_fractions=[1, 1, 1])
    assert np.allclose(ai.scale_entry_fractions(), [1 / 3, 1 / 3, 1 / 3])
