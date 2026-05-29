"""Сброс дневного убытка из Telegram."""
from __future__ import annotations

from prd_agent.risk.guard import GuardStatus, RiskGuard, StopKind


def test_reset_daily_loss_clears_pnl_and_block():
    g = RiskGuard({"risk": {"max_daily_loss_usdt": 30, "daily_loss_blocks_until_next_day": True}})
    g.day_stats.net_pnl_usdt = -34.0
    g.day_stats.net_pnl_pct = -3.4
    g.status = GuardStatus.STOPPED
    g.stop_kind = StopKind.DAILY_LOSS
    g.stop_reason = "Дневной лимит"

    msg = g.reset_daily_loss_counter()
    assert "сброшен" in msg.lower()
    assert g.day_stats.net_pnl_usdt == 0.0
    assert g.status == GuardStatus.ACTIVE
    assert g.stop_kind == StopKind.NONE
    ok, _ = g.can_trade()
    assert ok
