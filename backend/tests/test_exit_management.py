#!/usr/bin/env python3
"""Тесты для жёсткого стоп-лосса по убытку."""
from __future__ import annotations

from prd_agent.positions.exit_management import (
    ExitManagementConfig,
    evaluate_exit_actions,
)


def _cfg(hard_max_loss_pct: float = 0.8, close_on_hard_max_loss: bool = True) -> ExitManagementConfig:
    return ExitManagementConfig(
        enabled=True,
        time_stop_enabled=True,
        time_stop_minutes=240.0,
        time_stop_min_atr_progress=0.25,
        close_on_time_stop=True,
        hard_max_loss_pct=hard_max_loss_pct,
        close_on_hard_max_loss=close_on_hard_max_loss,
        early_breakeven_enabled=False,
        early_breakeven_atr_mult=0.45,
        early_breakeven_pct=0.18,
        late_breakeven_enabled=False,
        late_breakeven_retrace_pct=40.0,
        close_on_late_retrace=False,
        late_tighten_distance_factor=0.55,
    )


def test_hard_max_loss_closes_when_threshold_reached():
    action, reason = evaluate_exit_actions(
        cfg=_cfg(hard_max_loss_pct=0.8),
        side="Buy",
        entry=100.0,
        price=99.19,
        atr=1.0,
        opened_at_iso="2099-01-01T12:00:00+00:00",
        peak_profit_pct=0.0,
    )
    assert action == "close_hard_max_loss"
    assert "-0.81% <= -0.8%" in reason


def test_hard_max_loss_does_not_close_when_above_threshold():
    action, reason = evaluate_exit_actions(
        cfg=_cfg(hard_max_loss_pct=0.8),
        side="Buy",
        entry=100.0,
        price=99.5,
        atr=1.0,
        opened_at_iso="2099-01-01T12:00:00+00:00",
        peak_profit_pct=0.0,
    )
    assert action is None
    assert reason == ""


def test_hard_max_loss_disabled_pct_zero():
    action, reason = evaluate_exit_actions(
        cfg=_cfg(hard_max_loss_pct=0.0),
        side="Buy",
        entry=100.0,
        price=95.0,
        atr=1.0,
        opened_at_iso="2099-01-01T12:00:00+00:00",
        peak_profit_pct=0.0,
    )
    assert action is None
    assert reason == ""


def test_close_on_hard_max_loss_false_returns_would():
    action, reason = evaluate_exit_actions(
        cfg=_cfg(hard_max_loss_pct=0.8, close_on_hard_max_loss=False),
        side="Buy",
        entry=100.0,
        price=99.0,
        atr=1.0,
        opened_at_iso="2099-01-01T12:00:00+00:00",
        peak_profit_pct=0.0,
    )
    assert action is None
    assert "hard_max_loss_would" in reason
