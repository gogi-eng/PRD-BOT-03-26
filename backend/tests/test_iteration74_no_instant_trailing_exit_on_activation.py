#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from engine.exit_engine import ExitEngine, ExitReason
from engine.position_manager import Position


def test_long_trailing_activation_does_not_instantly_self_close():
    """
    Regression for case where breakeven+fee could be above market on activation,
    causing immediate trailing_exit in the same cycle.
    """
    engine = ExitEngine(
        trailing_activation_atr=0.8,
        trailing_distance_atr=1.2,
        min_profit_before_trail_pct=0.0,
        trailing_min_distance_from_price_pct=0.0,
        fee_rate=0.001,  # reproduces fee buffer scale from runtime logs
    )
    pos = Position(
        symbol="XAUTUSDT",
        side="BUY",
        entry_price=4697.8,
        qty=0.1,
        stop_loss=4625.6,
        take_profit=0.0,
    )
    engine.initialize_position(pos, atr_value=11.7405)  # activation ~= 4707.1924

    current_price = 4708.2
    engine.update_trailing(pos, current_price=current_price, last_swing_low=0.0, last_swing_high=0.0)

    assert pos.trailing_active is True
    assert pos.trailing_stop > 0.0
    assert pos.trailing_stop < current_price

    should_exit, reason, _ = engine.check_exit(pos, current_price=current_price, atr_value=11.7405)
    assert should_exit is False
    assert reason is None

