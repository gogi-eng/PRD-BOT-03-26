#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from engine.exit_engine import ExitEngine
from engine.position_manager import Position


def _long_pos(entry: float = 100.0, stop_loss: float = 98.0, bars: int = 0) -> Position:
    pos = Position(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=entry,
        qty=1.0,
        stop_loss=stop_loss,
        take_profit=entry + 8.0,
    )
    pos.bars_since_entry = bars
    return pos


def _short_pos(entry: float = 100.0, stop_loss: float = 102.0, bars: int = 0) -> Position:
    pos = Position(
        symbol="TESTUSDT",
        side="SELL",
        entry_price=entry,
        qty=1.0,
        stop_loss=stop_loss,
        take_profit=entry - 8.0,
    )
    pos.bars_since_entry = bars
    return pos


def test_trailing_long_respects_min_distance_floor():
    engine = ExitEngine(
        trailing_activation_atr=0.8,
        trailing_distance_atr=0.6,  # intentionally tight
        trailing_min_distance_from_price_pct=0.35,
        fee_rate=0.001,
    )
    pos = _long_pos()
    engine.initialize_position(pos, atr_value=1.0)

    # Activate trailing and set best.
    engine.update_trailing(pos, current_price=101.0)
    engine.update_trailing(pos, current_price=103.0)

    assert pos.trailing_stop > 0
    min_gap = 103.0 * 0.0035
    assert (103.0 - pos.trailing_stop) + 1e-9 >= min_gap


def test_trailing_short_respects_min_distance_floor():
    engine = ExitEngine(
        trailing_activation_atr=0.8,
        trailing_distance_atr=0.6,  # intentionally tight
        trailing_min_distance_from_price_pct=0.35,
        fee_rate=0.001,
    )
    pos = _short_pos()
    engine.initialize_position(pos, atr_value=1.0)

    # Activate trailing and set best.
    engine.update_trailing(pos, current_price=99.0)
    engine.update_trailing(pos, current_price=97.0)

    assert pos.trailing_stop > 0
    min_gap = 97.0 * 0.0035
    assert (pos.trailing_stop - 97.0) + 1e-9 >= min_gap

