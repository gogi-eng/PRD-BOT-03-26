#!/usr/bin/env python3
"""RiskGuard: безубыток (pnl=0) не увеличивает серию убытков."""
from __future__ import annotations

from prd_agent.risk.guard import RiskGuard


def _guard() -> RiskGuard:
    return RiskGuard(
        {"risk": {"max_consecutive_losses": 4, "max_daily_loss_usdt": 100}, "trading": {}},
        initial_balance=100.0,
    )


def test_zero_pnl_does_not_increment_consecutive():
    g = _guard()
    g.record_trade(-1.0)
    assert g._consecutive_losses == 1
    g.record_trade(0.0)
    assert g._consecutive_losses == 1
    assert g.day_stats.losses == 1
    assert g.day_stats.wins == 0
    assert g.day_stats.trades == 2


def test_zero_pnl_does_not_reset_streak():
    g = _guard()
    g.record_trade(-0.5)
    g.record_trade(-0.5)
    assert g._consecutive_losses == 2
    g.record_trade(0.0)
    assert g._consecutive_losses == 2


def test_win_still_resets_after_flat():
    g = _guard()
    g.record_trade(-1.0)
    g.record_trade(0.0)
    g.record_trade(0.5)
    assert g._consecutive_losses == 0


def test_loss_after_flat_continues_streak():
    g = _guard()
    g.record_trade(-1.0)
    g.record_trade(0.0)
    g.record_trade(-0.2)
    assert g._consecutive_losses == 2
