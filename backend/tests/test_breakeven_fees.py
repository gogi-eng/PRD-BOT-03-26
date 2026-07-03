"""Тесты безубытка с учётом комиссий open+close."""
from __future__ import annotations

from prd_agent.positions.breakeven_fees import (
    DEFAULT_BE_FEE_BUFFER_PCT,
    MIN_BE_FEE_BUFFER_PCT,
    breakeven_stop_price,
    clamp_sl_for_profit_lock,
    effective_be_fee_buffer_pct,
    fee_buffer_pct_from_bot_cfg,
    funding_buffer_pct,
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


def test_fee_multiplier_15_round_trip():
    """Вход ± 1.5× комиссия: 0.055%×2×1.5 = 0.165% от цены (без funding)."""
    cfg = {
        "positions": {
            "fee_breakeven": {
                "taker_rate": 0.00055,
                "fee_multiplier": 1.5,
                "include_funding": False,
            }
        }
    }
    buf = fee_buffer_pct_from_bot_cfg(cfg)
    assert abs(buf - 0.165) < 0.001
    sl = breakeven_stop_price("Sell", 0.08691, buf)
    assert sl < 0.08691
    assert not is_stop_in_loss_after_fees("Sell", 0.08691, sl, buf)


def test_birb_short_mark_retrace_clamped_to_be():
    """Регрессия: откат к entry тянул SL выше входа на SHORT."""
    from prd_agent.positions.scanner_reversal_sl import compute_tightened_sl

    entry = 0.08691
    mark = 0.08680  # ещё в плюсе, но близко к входу
    bot_cfg = {
        "positions": {
            "fee_breakeven": {
                "taker_rate": 0.00055,
                "fee_multiplier": 1.5,
                "include_funding": False,
            }
        }
    }
    rev_cfg = {"tighten_from_mark_pct": 0.35, "min_sl_improve_pct": 0.0}
    new_sl = compute_tightened_sl(
        position_side="Sell",
        entry=entry,
        mark=mark,
        current_sl=0.0,
        invalidation=0.0,
        cfg=rev_cfg,
        bot_cfg=bot_cfg,
    )
    assert new_sl is not None
    be = breakeven_stop_price("Sell", entry, fee_buffer_pct_from_bot_cfg(bot_cfg))
    assert abs(new_sl - be) < 1e-6 or new_sl <= be
    assert new_sl < entry


def test_net_pnl_at_be_stop_is_non_negative():
    entry = 1000.0
    buf = effective_be_fee_buffer_pct(0.15)
    sl = breakeven_stop_price("Sell", entry, buf)
    net = net_pnl_pct_at_stop("Sell", entry, sl)
    assert net >= -0.01


def test_funding_increases_be_buffer_with_hold_time():
    cfg = {
        "positions": {
            "fee_breakeven": {
                "taker_rate": 0.00055,
                "fee_multiplier": 1.5,
                "include_funding": True,
                "funding_rate_assumed": 0.0001,
                "funding_interval_hours": 8,
                "funding_reserve_hours": 24,
            }
        }
    }
    trade_only = fee_buffer_pct_from_bot_cfg(
        {**cfg, "positions": {**cfg["positions"], "fee_breakeven": {**cfg["positions"]["fee_breakeven"], "include_funding": False}}}
    )
    with_funding = fee_buffer_pct_from_bot_cfg(cfg)
    assert with_funding > trade_only
    assert abs(funding_buffer_pct(cfg) - 0.03) < 0.001  # 3×8ч × 0.01%
    long_hold = fee_buffer_pct_from_bot_cfg(cfg, hold_hours=48.0)
    assert long_hold > with_funding
    entry = 100.0
    sl_short = breakeven_stop_price("Sell", entry, long_hold)
    net = net_pnl_pct_at_stop(
        "Sell",
        entry,
        sl_short,
        funding_buffer_pct_val=funding_buffer_pct(cfg, hold_hours=48.0),
    )
    assert net >= -0.01
