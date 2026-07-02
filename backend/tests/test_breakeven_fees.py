"""Тесты безубытка с учётом комиссий open+close."""
from __future__ import annotations

from prd_agent.positions.breakeven_fees import (
    DEFAULT_BE_FEE_BUFFER_PCT,
    MIN_BE_FEE_BUFFER_PCT,
    breakeven_stop_price,
    clamp_sl_for_profit_lock,
    effective_be_fee_buffer_pct,
    fee_buffer_pct_from_bot_cfg,
    is_stop_in_loss_after_fees,
    net_pnl_pct_at_stop,
    round_trip_fee_buffer_pct,
)


def test_effective_buffer_raises_tiny_config():
    assert effective_be_fee_buffer_pct(0.05) == MIN_BE_FEE_BUFFER_PCT
    assert effective_be_fee_buffer_pct(0.0) == DEFAULT_BE_FEE_BUFFER_PCT


def test_breakeven_long_covers_round_trip_fees():
    buf = round_trip_fee_buffer_pct()
    sl = breakeven_stop_price("Buy", 1000.0, buf)
    assert sl > 1000.0
    assert sl >= 1001.1
    assert sl <= 1002.0


def test_breakeven_short_below_entry():
    sl = breakeven_stop_price("Sell", 500.0, 0.15)
    assert sl < 500.0
    assert abs(sl - 499.25) < 0.01


def test_old_config_005_now_min_buffer():
    sl = breakeven_stop_price("Buy", 1000.0, effective_be_fee_buffer_pct(0.05))
    assert sl >= 1001.2


def test_clamp_short_sr_trail_above_entry():
    """Регрессия: S/R ставил SL выше entry на SHORT → убыток после комиссий."""
    entry = 100.0
    bad_sl = 101.5
    clamped = clamp_sl_for_profit_lock("Sell", entry, bad_sl, 0.15, in_profit=True)
    assert clamped < entry
    assert clamped == breakeven_stop_price("Sell", entry, 0.15)
    assert not is_stop_in_loss_after_fees("Sell", entry, clamped, 0.15)


def test_clamp_long_never_below_be_in_profit():
    entry = 50.0
    bad_sl = 49.9
    clamped = clamp_sl_for_profit_lock("Buy", entry, bad_sl, 0.15, in_profit=True)
    assert clamped > entry
    assert clamped == breakeven_stop_price("Buy", entry, 0.15)


def test_clamp_skipped_when_not_in_profit():
    entry = 100.0
    initial_sl = 102.0
    assert clamp_sl_for_profit_lock("Sell", entry, initial_sl, 0.15, in_profit=False) == initial_sl


def test_fee_buffer_from_bot_config():
    cfg = {
        "positions": {
            "fee_breakeven": {
                "taker_rate": 0.00055,
                "slippage_margin_pct": 0.04,
            }
        }
    }
    buf = fee_buffer_pct_from_bot_cfg(cfg)
    assert buf >= MIN_BE_FEE_BUFFER_PCT


def test_net_pnl_at_be_stop_is_non_negative():
    entry = 1000.0
    buf = effective_be_fee_buffer_pct(0.15)
    sl = breakeven_stop_price("Sell", entry, buf)
    net = net_pnl_pct_at_stop("Sell", entry, sl)
    assert net >= -0.01
