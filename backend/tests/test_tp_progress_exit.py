"""Тесты выхода по прогрессу к take profit."""
from __future__ import annotations

from prd_agent.positions.tp_progress_exit import (
    TpProgressExitConfig,
    breakeven_stop_price,
    cycle_breakeven_threshold,
    evaluate_tp_progress_exit,
    progress_to_take_profit_pct,
    tighten_stop,
)


def test_progress_to_tp_long_halfway():
    p = progress_to_take_profit_pct("Buy", 100.0, 110.0, 120.0)
    assert p is not None
    assert abs(p - 50.0) < 0.01


def test_progress_to_tp_short():
    p = progress_to_take_profit_pct("Sell", 100.0, 92.0, 84.0)
    assert p is not None
    assert abs(p - 50.0) < 0.01


def test_breakeven_sl_long():
    sl = breakeven_stop_price("Buy", 1000.0, 0.05)
    assert sl > 1000.0


def test_tighten_long_only_moves_up():
    assert tighten_stop("Buy", 990.0, 1001.0, 1010.0) == 1001.0
    assert tighten_stop("Buy", 1005.0, 1001.0, 1010.0) is None


def test_evaluate_be_at_30_percent():
    cfg = TpProgressExitConfig(
        enabled=True,
        breakeven_at_progress_pct=30.0,
        sr_trail_at_progress_pct=80.0,
        sr_trail_enabled=False,
    )
    res = evaluate_tp_progress_exit(
        cfg=cfg,
        side="Buy",
        entry=100.0,
        price=106.0,
        take_profit=120.0,
        current_sl=95.0,
        klines=[],
        atr=1.0,
    )
    assert res.progress_pct is not None
    assert res.progress_pct >= 30.0
    assert res.suggested_sl is not None
    assert res.suggested_sl >= 100.0
    assert res.phase == "breakeven"


def test_cycle_be_threshold():
    assert cycle_breakeven_threshold(20.0) == 30.0
    assert cycle_breakeven_threshold(40.0) == 20.0
