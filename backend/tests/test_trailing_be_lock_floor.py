"""Трейлинг: пол SL = комиссии + be_lock (не только fee), иначе откат → минус."""
from __future__ import annotations

from prd_agent.positions.breakeven_fees import breakeven_stop_price
from prd_agent.positions.position_steward import PositionSteward, TrackedPosition
from prd_agent.signals.pump_dump_mode import TrailingProfile


def test_trailing_sl_floor_includes_be_lock(tmp_path):
    cfg = {
        "_root": str(tmp_path),
        "positions": {
            "trailing_enabled": True,
            "trailing_activation_pct": 1.0,
            "trailing_distance_pct": 10.0,  # широкий — без пола ушёл бы ниже entry
            "trailing_distance_atr_mult": 0.0,
            "trailing_min_distance_pct": 0.0,
            "breakeven_after_pct": 0.5,
            "tp_progress_exit": {
                "enabled": True,
                "be_fee_buffer_pct": 0.30,
                "be_lock_extra_pct": 1.0,
            },
            "fee_breakeven": {
                "taker_rate": 0.00055,
                "maker_rate": 0.0002,
                "fee_multiplier": 2.0,
                "include_funding": False,
                "slippage_margin_pct": 0.0,
            },
        },
    }
    steward = PositionSteward(cfg)
    profile = steward._default_profile
    pos = TrackedPosition(
        symbol="BTCUSDT",
        side="Buy",
        entry=100.0,
        qty=1.0,
        stop_loss=99.0,
        take_profit=110.0,
        best_price=102.0,
    )
    # profit ~2% от входа, activation пройден
    sl = steward._calc_trailing_sl(pos, price=102.0, atr=0.0, profile=profile)
    assert sl is not None
    fee = steward._be_fee_buffer_for(pos, profile)
    floor = breakeven_stop_price("Buy", 100.0, fee + 1.0)
    assert sl + 1e-9 >= floor
    # Замок 1% — пол заметно выше чистого entry+fee
    fee_only = breakeven_stop_price("Buy", 100.0, fee)
    assert floor > fee_only + 0.4
