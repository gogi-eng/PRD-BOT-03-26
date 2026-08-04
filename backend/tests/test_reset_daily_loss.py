"""Сброс дневного убытка: флаг не даёт reconcile снова заблокировать trade_ok."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from prd_agent.risk.guard import GuardStatus, RiskGuard, StopKind


def test_reset_daily_loss_clears_pnl_and_block():
    g = RiskGuard(
        {
            "timezone_offset": 3,
            "risk": {"max_daily_loss_usdt": 30, "daily_loss_blocks_until_next_day": True},
        }
    )
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
    assert g._manual_daily_loss_reset_active()


def test_manual_reset_blocks_reconcile_until_next_trading_day(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    g = RiskGuard(
        {
            "timezone_offset": 3,
            "risk": {"max_daily_loss_usdt": 10, "daily_loss_blocks_until_next_day": True},
        },
        initial_balance=100.0,
    )
    g.day_stats.net_pnl_usdt = -25.0
    g.day_stats.net_pnl_pct = -25.0
    g.status = GuardStatus.STOPPED
    g.stop_kind = StopKind.DAILY_LOSS

    g.reset_daily_loss_counter()
    assert g.day_stats.net_pnl_usdt == 0.0
    flag = Path("data") / "risk_daily_loss_manual_reset.json"
    assert flag.is_file()
    assert g._manual_daily_loss_reset_active()

    # Bybit снова отдаёт крупный минус — reconcile не должен перетереть сброс
    rows = [
        {
            "updatedTime": str(int(__import__("time").time() * 1000)),
            "closedPnl": "-40",
        }
    ]
    g.reconcile_from_closed_rows(rows, balance=100.0)
    assert g.day_stats.net_pnl_usdt == 0.0
    ok, reason = g.can_trade()
    assert ok, reason

    # На новый торговый день флаг истекает — reconcile снова работает
    tomorrow = g._trading_day() + timedelta(days=1)
    g._manual_daily_loss_reset_day = tomorrow - timedelta(days=1)
    # подменяем «сегодня» на завтра
    monkeypatch.setattr(g, "_trading_day", lambda: tomorrow)
    assert not g._manual_daily_loss_reset_active()
    g.reconcile_from_closed_rows(rows, balance=100.0)
    assert g.day_stats.net_pnl_usdt == -40.0


def test_order_ok_log_format_includes_qty():
    """Формат Order OK: 7 плейсхолдеров и qty не пропущен (регресс TypeError)."""
    fmt = "Order OK %s %s qty=%.6f lev=%dx (req %dx) conf=%.0f%% id=%s"
    msg = fmt % ("BTCUSDT", "Buy", 0.01, 10, 12, 85.0, "oid-1")
    assert "qty=0.010000" in msg
    assert "lev=10x" in msg
