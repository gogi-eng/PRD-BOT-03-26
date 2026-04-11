#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from engine.exit_engine import ExitEngine, ExitReason
from engine.position_manager import Position


def _long_pos(entry: float = 100.0, stop_loss: float = 95.0, bars: int = 12) -> Position:
    pos = Position(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=entry,
        qty=1.0,
        stop_loss=stop_loss,
        take_profit=entry + 10.0,
    )
    pos.bars_since_entry = bars
    return pos


def test_trailing_activation_uses_atr_cap_when_sl_is_wide():
    engine = ExitEngine(
        trailing_activation_atr=0.8,
        min_profit_before_trail_pct=0.45,
    )
    pos = _long_pos(entry=100.0, stop_loss=95.0, bars=0)  # 1R = 5.0

    engine.initialize_position(pos, atr_value=1.0)

    # min(1R=5.0, max(0.8 ATR, 0.45% move=0.45)) => 0.8
    assert abs(pos.trailing_activation_price - 100.8) < 1e-9


def test_trailing_activation_keeps_one_r_when_atr_cap_is_higher():
    engine = ExitEngine(
        trailing_activation_atr=1.3,
        min_profit_before_trail_pct=0.45,
    )
    pos = _long_pos(entry=100.0, stop_loss=99.0, bars=0)  # 1R = 1.0

    engine.initialize_position(pos, atr_value=1.0)

    # min(1R=1.0, max(1.3 ATR, 0.45% move=0.45)) => 1.0
    assert abs(pos.trailing_activation_price - 101.0) < 1e-9


def test_early_exit_uses_best_profit_not_only_current_profit():
    engine = ExitEngine(
        early_exit_bars=12,
        early_exit_min_profit_atr=0.35,
        fee_rate=0.0001,  # keep fee floor below ATR floor
    )
    pos = _long_pos(entry=100.0, stop_loss=95.0, bars=12)
    pos.best_price = 100.45  # best profit=0.45 >= 0.35 ATR floor

    should_exit, reason, _ = engine.check_exit(
        pos,
        current_price=100.05,  # current profit only 0.05
        atr_value=1.0,
        allow_early_exit=True,
    )

    assert should_exit is False
    assert reason != ExitReason.EARLY_EXIT


def test_early_exit_still_triggers_when_no_real_progress():
    engine = ExitEngine(
        early_exit_bars=12,
        early_exit_min_profit_atr=0.35,
        fee_rate=0.0001,
    )
    pos = _long_pos(entry=100.0, stop_loss=95.0, bars=12)
    pos.best_price = 100.10

    should_exit, reason, details = engine.check_exit(
        pos,
        current_price=100.05,
        atr_value=1.0,
        allow_early_exit=True,
    )

    assert should_exit is True
    assert reason == ExitReason.EARLY_EXIT
    assert "best" in details


def test_early_exit_is_not_checked_when_trailing_is_active():
    engine = ExitEngine(
        early_exit_bars=12,
        early_exit_min_profit_atr=0.35,
    )
    pos = _long_pos(entry=100.0, stop_loss=95.0, bars=99)
    pos.trailing_active = True
    pos.trailing_stop = 99.0

    should_exit, reason, _ = engine.check_exit(
        pos,
        current_price=100.01,
        atr_value=1.0,
        allow_early_exit=True,
    )

    if should_exit:
        assert reason != ExitReason.EARLY_EXIT
