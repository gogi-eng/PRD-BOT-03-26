#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from engine.exit_engine import ExitEngine, ExitReason
from engine.position_manager import Position


def _klines_from_closes(closes: list[float]) -> list[dict]:
    return [{"open": c, "high": c, "low": c, "close": c, "volume": 1000.0} for c in closes]


def test_trend_exit_blocked_near_entry_even_with_confirmed_ema_breach():
    engine = ExitEngine(
        ema_exit_buffer_pct=0.12,
        ema_exit_confirm_bars=2,
        ema_exit_require_ema_slope=True,
        ema_exit_min_move_from_entry_pct=0.35,
    )
    pos = Position(
        symbol="TESTUSDT",
        side="SELL",
        entry_price=100.0,
        qty=1.0,
        stop_loss=102.0,
        take_profit=95.0,
    )
    pos.bars_since_entry = 30

    closes = [100.0] * 33 + [100.08, 100.12]
    should_exit, reason, details = engine.check_ema_trend_exit(
        pos, _klines_from_closes(closes), ema_period=20
    )
    assert should_exit is False
    assert reason is None
    assert details == ""


def test_trend_exit_allows_when_adverse_move_exceeds_thresholds():
    engine = ExitEngine(
        ema_exit_buffer_pct=0.12,
        ema_exit_confirm_bars=2,
        ema_exit_require_ema_slope=True,
        ema_exit_min_move_from_entry_pct=0.35,
    )
    pos = Position(
        symbol="TESTUSDT",
        side="SELL",
        entry_price=100.0,
        qty=1.0,
        stop_loss=102.0,
        take_profit=95.0,
    )
    pos.bars_since_entry = 30

    closes = [100.0] * 33 + [100.5, 100.8]
    should_exit, reason, details = engine.check_ema_trend_exit(
        pos, _klines_from_closes(closes), ema_period=20
    )
    assert should_exit is True
    assert reason == ExitReason.TREND_EXIT
    assert "SHORT confirmed" in details

