"""Тесты безубытка с учётом комиссий."""
from __future__ import annotations

from prd_agent.positions.breakeven_fees import (
    DEFAULT_BE_FEE_BUFFER_PCT,
    MIN_BE_FEE_BUFFER_PCT,
    breakeven_stop_price,
    effective_be_fee_buffer_pct,
    round_trip_fee_buffer_pct,
)


def test_effective_buffer_raises_tiny_config():
    assert effective_be_fee_buffer_pct(0.05) == MIN_BE_FEE_BUFFER_PCT
    assert effective_be_fee_buffer_pct(0.0) == DEFAULT_BE_FEE_BUFFER_PCT


def test_breakeven_long_covers_round_trip_fees():
    buf = round_trip_fee_buffer_pct()
    sl = breakeven_stop_price("Buy", 1000.0, buf)
    assert sl > 1000.0
    # 0.11% fees + 0.04 slippage margin
    assert sl >= 1001.1
    assert sl <= 1002.0


def test_breakeven_short_below_entry():
    sl = breakeven_stop_price("Sell", 500.0, 0.15)
    assert sl < 500.0
    assert abs(sl - 499.25) < 0.01


def test_old_config_005_now_min_buffer():
    """Регрессия: be_fee_buffer_pct: 0.05 в yaml больше не даёт убыток на BE."""
    sl = breakeven_stop_price("Buy", 1000.0, effective_be_fee_buffer_pct(0.05))
    assert sl >= 1001.2
