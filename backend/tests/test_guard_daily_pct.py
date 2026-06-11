"""Дневной PnL % считается от баланса на начало UTC-дня, не от устаревшего initial_balance."""
from __future__ import annotations

from prd_agent.risk.guard import RiskGuard


def test_daily_pnl_pct_uses_day_start_balance_not_stale_initial():
    g = RiskGuard({"risk": {"max_daily_loss_pct": 5.0}})
    g.initial_balance = 24.0
    g.update_balance_reference(123.0)
    assert g.day_start_balance == 123.0

    g.reconcile_from_closed_rows([], balance=123.0)
    g.day_stats.net_pnl_usdt = -46.51
    g._recalc_day_pnl_pct(123.0)

    pct = g.day_stats.net_pnl_pct
    assert -40.0 < pct < -36.0, f"expected ~-37.8%, got {pct}"


def test_record_trade_recalcs_pct_from_day_start_balance():
    g = RiskGuard({"risk": {"max_daily_loss_pct": 5.0}})
    g.update_balance_reference(100.0)
    g.record_trade(-5.0)
    assert g.day_stats.net_pnl_pct == -5.0
