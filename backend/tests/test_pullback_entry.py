"""Откат перед входом: порог в %, вход после отката."""
from __future__ import annotations

from prd_agent.risk.pullback_entry import check_pullback_entry
from prd_agent.signals.types import UnifiedSignal


def _klines(closes: list[float]) -> list[dict]:
    return [{"close": c} for c in closes]


def _cfg() -> dict:
    return {
        "pullback_entry": {
            "enabled": True,
            "momentum_bars": 5,
            "min_momentum_pct": 0.35,
            "min_retrace_pct": 0.12,
            "require_counter_bars": 1,
        },
        "pump_dump_trade": {"enabled": False},
    }


def test_weak_momentum_allows_buy():
    """Как SOL Δ=0.01 — импульс < 0.35%, не блокируем."""
    sig = UnifiedSignal(symbol="SOLUSDT", side="Buy", confidence=0.9, source="own_multi_agent")
    closes = [100.0 + i * 0.001 for i in range(10)]
    ok, reason = check_pullback_entry(sig, _klines(closes), _cfg())
    assert ok is True
    assert reason == ""


def test_strong_impulse_without_pullback_blocks():
    sig = UnifiedSignal(symbol="BTCUSDT", side="Buy", confidence=0.9, source="own_multi_agent")
    closes = [100.0 + i * 0.5 for i in range(10)]
    ok, reason = check_pullback_entry(sig, _klines(closes), _cfg())
    assert ok is False
    assert "нужен откат" in reason


def test_retrace_after_impulse_allows_buy():
    sig = UnifiedSignal(symbol="BTCUSDT", side="Buy", confidence=0.9, source="own_multi_agent")
    closes = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5, 102.0, 101.5]
    ok, reason = check_pullback_entry(sig, _klines(closes), _cfg())
    assert ok is True
    assert "откат" in reason


def test_counter_trend_bars_allow_sell():
    sig = UnifiedSignal(symbol="ALLOUSDT", side="Sell", confidence=0.9, source="own_multi_agent")
    closes = [1.0, 0.99, 0.98, 0.97, 0.96, 0.95, 0.955, 0.96]
    ok, reason = check_pullback_entry(sig, _klines(closes), _cfg())
    assert ok is True
