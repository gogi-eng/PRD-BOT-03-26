#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from engine.exit_engine import ExitEngine, ExitReason
from engine.position_manager import Position


def _pos_long(entry: float = 100.0) -> Position:
    return Position(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=entry,
        qty=1.0,
        stop_loss=90.0,
        take_profit=120.0,
    )


def test_early_exit_disabled_when_bars_zero():
    engine = ExitEngine(early_exit_bars=0, early_exit_min_profit_atr=0.35)
    pos = _pos_long()
    pos.bars_since_entry = 999

    should_close, reason, _msg = engine.check_exit(
        pos,
        current_price=100.1,
        atr_value=1.0,
        protective_level=0.0,
        allow_early_exit=True,
    )

    assert should_close is False
    assert reason != ExitReason.EARLY_EXIT
