"""Дневной стоп и два убытка по монете — для обычного входа и охотника за скачками."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from prd_agent.risk.fast_entry_guard import fast_entry_block_reason
from prd_agent.risk.guard import RiskGuard


def _cfg() -> dict:
    return {
        "timezone_offset": 3,
        "risk": {
            "max_daily_loss_usdt": 10,
            "max_daily_loss_pct": 50,
            "max_symbol_losses_per_day": 2,
            "daily_loss_blocks_until_next_day": True,
            "max_consecutive_losses": 4,
        },
        "trading": {"max_positions": 8},
    }


def _row(symbol: str, pnl: float, *, age_sec: int = 120) -> dict:
    ts = int((datetime.now(timezone.utc) - timedelta(seconds=age_sec)).timestamp() * 1000)
    return {"symbol": symbol, "closedPnl": str(pnl), "updatedTime": str(ts)}


def _guard(tmp_path, monkeypatch) -> RiskGuard:
    monkeypatch.chdir(tmp_path)
    return RiskGuard(_cfg(), initial_balance=80.0)


def test_two_losses_block_only_that_symbol(tmp_path, monkeypatch):
    g = _guard(tmp_path, monkeypatch)
    g.reconcile_from_closed_rows(
        [
            _row("JCTUSDT", -1.2),
            _row("JCTUSDT", -0.4, age_sec=300),
            _row("BTCUSDT", 0.5, age_sec=400),
        ],
        balance=80.0,
    )
    ok_j, reason_j = g.can_trade("JCTUSDT")
    ok_b, reason_b = g.can_trade("BTCUSDT")
    assert not ok_j
    assert "JCTUSDT" in reason_j
    assert ok_b, reason_b


def test_one_loss_still_allows_symbol(tmp_path, monkeypatch):
    g = _guard(tmp_path, monkeypatch)
    g.reconcile_from_closed_rows([_row("BRUSDT", -4.0)], balance=80.0)
    ok, reason = g.can_trade("BRUSDT")
    assert ok, reason


def test_win_between_losses_still_counts_two(tmp_path, monkeypatch):
    g = _guard(tmp_path, monkeypatch)
    g.reconcile_from_closed_rows(
        [
            _row("JCTUSDT", -1.0, age_sec=500),
            _row("JCTUSDT", 0.3, age_sec=300),
            _row("JCTUSDT", -0.8, age_sec=100),
        ],
        balance=80.0,
    )
    ok, reason = g.fast_path_block("JCTUSDT")
    assert not ok
    assert "JCTUSDT" in reason


def test_daily_loss_blocks_spike_path_for_any_symbol(tmp_path, monkeypatch):
    g = _guard(tmp_path, monkeypatch)
    g.open_positions_count = 99
    g.reconcile_from_closed_rows([_row("PHAUSDT", -11.0)], balance=80.0)
    ok, reason = g.fast_path_block("SOLUSDT")
    assert not ok
    assert "10" in reason
    assert g.open_positions_count == 99


def test_yesterday_loss_does_not_count(tmp_path, monkeypatch):
    g = _guard(tmp_path, monkeypatch)
    g.reconcile_from_closed_rows(
        [
            _row("JCTUSDT", -3.0, age_sec=60 * 60 * 30),
            _row("JCTUSDT", -3.0, age_sec=60 * 60 * 30 + 10),
        ],
        balance=80.0,
    )
    ok, reason = g.fast_path_block("JCTUSDT")
    assert ok, reason


def test_fast_entry_block_reason_uses_exchange_rows(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class _Bybit:
        async def get_closed_pnl_page(self, **_kwargs):
            return [_row("ERAUSDT", -2.0), _row("ERAUSDT", -1.0, age_sec=200)], ""

        async def get_balance(self):
            return 80.0

    reason = asyncio.run(
        fast_entry_block_reason(
            _Bybit(),
            _cfg(),
            "ERAUSDT",
            store_dir=tmp_path / "risk_checks",
        )
    )
    assert "ERAUSDT" in reason


def test_fast_entry_allows_fresh_symbol(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class _Bybit:
        async def get_closed_pnl_page(self, **_kwargs):
            return [_row("ERAUSDT", -2.0)], ""

        async def get_balance(self):
            return 80.0

    reason = asyncio.run(
        fast_entry_block_reason(
            _Bybit(),
            _cfg(),
            "SOLUSDT",
            store_dir=tmp_path / "risk_checks",
        )
    )
    assert reason == ""
