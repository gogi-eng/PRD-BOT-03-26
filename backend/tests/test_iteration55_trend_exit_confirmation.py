#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[2] / "bot"
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from engine.exit_engine import ExitEngine, ExitReason
from engine.position_manager import Position


def _klines_from_closes(closes: list[float]) -> list[dict]:
    return [{"open": c, "high": c, "low": c, "close": c, "volume": 1000.0} for c in closes]


def test_ema_trend_exit_waits_for_confirm_bars():
    engine = ExitEngine(
        ema_trend_exit_buffer_pct=0.12,
        ema_trend_exit_confirm_bars=2,
        ema_trend_exit_require_slope=True,
    )
    pos = Position(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=100.0,
        qty=1.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    pos.bars_since_entry = 30

    closes = [100.0] * 34 + [99.6]
    should_exit, reason, _details = engine.check_ema_trend_exit(pos, _klines_from_closes(closes), ema_period=20)
    assert should_exit is False
    assert reason is None
    assert "confirmed 2 bars" not in (_details or "")


def test_ema_trend_exit_triggers_on_second_confirmed_breach():
    engine = ExitEngine(
        ema_trend_exit_buffer_pct=0.12,
        ema_trend_exit_confirm_bars=2,
        ema_trend_exit_require_slope=True,
    )
    pos = Position(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=100.0,
        qty=1.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    pos.bars_since_entry = 30

    closes1 = [100.0] * 34 + [99.6]
    closes2 = [100.0] * 33 + [99.6, 99.5]
    engine.check_ema_trend_exit(pos, _klines_from_closes(closes1), ema_period=20)
    should_exit, reason, details = engine.check_ema_trend_exit(pos, _klines_from_closes(closes2), ema_period=20)
    assert should_exit is True
    assert reason == ExitReason.TREND_EXIT
    assert "confirmed 2 bars" in details


def test_ema_trend_exit_resets_counter_when_price_recovers():
    engine = ExitEngine(
        ema_trend_exit_buffer_pct=0.12,
        ema_trend_exit_confirm_bars=2,
        ema_trend_exit_require_slope=True,
    )
    pos = Position(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=100.0,
        qty=1.0,
        stop_loss=95.0,
        take_profit=110.0,
    )
    pos.bars_since_entry = 30

    breach = [100.0] * 34 + [99.6]
    recover = [100.0] * 34 + [100.2]
    engine.check_ema_trend_exit(pos, _klines_from_closes(breach), ema_period=20)
    should_exit, reason, _ = engine.check_ema_trend_exit(pos, _klines_from_closes(recover), ema_period=20)
    assert should_exit is False
    assert reason is None
    assert getattr(pos, "ema_trend_breach_count", 0) == 0
