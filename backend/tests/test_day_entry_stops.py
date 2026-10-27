"""Стоп по убытку сессии и два убытка по монете — обычный вход и охотник."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from prd_agent.risk.fast_entry_guard import fast_entry_block_reason
from prd_agent.risk.guard import RiskGuard
from prd_agent.risk.market_sessions import SessionWindow, session_window


def _pin_open_session(monkeypatch) -> None:
    """Окно шире, чем возраст тестовых сделок, чтобы проверка не зависела от часов."""
    now = datetime.now(timezone.utc)
    window = SessionWindow(
        start_utc=now - timedelta(hours=6),
        end_utc=now + timedelta(hours=6),
        name="тест",
        end_hhmm="23:59",
    )
    monkeypatch.setattr(
        "prd_agent.risk.guard.session_window",
        lambda *_args, **_kwargs: window,
    )


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
    _pin_open_session(monkeypatch)
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
    _pin_open_session(monkeypatch)

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


def test_loss_before_this_session_does_not_block(tmp_path, monkeypatch):
    """Убытки по монете из прошлой сессии не блокируют монету, но дневной лимит убытка держится весь день."""
    monkeypatch.chdir(tmp_path)
    now = datetime.now(timezone.utc)
    window = SessionWindow(
        start_utc=now - timedelta(minutes=10),
        end_utc=now + timedelta(hours=2),
        name="Нью-Йорк",
        end_hhmm="01:00",
    )
    monkeypatch.setattr(
        "prd_agent.risk.guard.session_window",
        lambda *_args, **_kwargs: window,
    )
    g = RiskGuard(_cfg(), initial_balance=80.0)
    # 2 убытка по JCTUSDT были в прошлой сессии (-$4 суммарно < дневного лимита -$10)
    g.reconcile_from_closed_rows(
        [
            _row("JCTUSDT", -2.0, age_sec=40 * 60),
            _row("JCTUSDT", -2.0, age_sec=50 * 60),
        ],
        balance=80.0,
    )
    ok, reason = g.fast_path_block("JCTUSDT")
    assert ok, reason
    ok_any, reason_any = g.can_trade("SOLUSDT")
    assert ok_any, reason_any


def test_daily_loss_persists_across_intraday_session_boundary(tmp_path, monkeypatch):
    """Дневной лимит убытка (-$11 при лимите $10) НЕ сбрасывается при смене внутридневной сессии."""
    monkeypatch.chdir(tmp_path)
    now = datetime.now(timezone.utc)
    window = SessionWindow(
        start_utc=now - timedelta(minutes=10),
        end_utc=now + timedelta(hours=2),
        name="Европа",
        end_hhmm="19:00",
    )
    monkeypatch.setattr(
        "prd_agent.risk.guard.session_window",
        lambda *_args, **_kwargs: window,
    )
    g = RiskGuard(_cfg(), initial_balance=80.0)
    # Убыток -$11 случился 30 минут назад (в предыдущей сессии того же торгового дня)
    g.reconcile_from_closed_rows(
        [
            _row("PHAUSDT", -11.0, age_sec=30 * 60),
        ],
        balance=80.0,
    )
    assert g.day_stats.net_pnl_usdt == -11.0
    ok_fast, reason_fast = g.fast_path_block("SOLUSDT")
    assert not ok_fast
    assert "10" in reason_fast
    ok_trade, reason_trade = g.can_trade("SOLUSDT")
    assert not ok_trade
    assert "10" in reason_trade


def test_day_start_balance_persisted_across_restart(tmp_path, monkeypatch):
    """Рестарт бота посреди дня не перезаписывает day_start_balance просевшим балансом."""
    monkeypatch.chdir(tmp_path)
    _pin_open_session(monkeypatch)
    g1 = RiskGuard(_cfg(), initial_balance=0.0)
    g1.update_balance_reference(100.0)
    assert g1.day_start_balance == 100.0

    # Симулируем рестарт бота после просадки до 82 USDT в тот же торговый день
    g2 = RiskGuard(_cfg(), initial_balance=0.0)
    g2.update_balance_reference(82.0)
    assert g2.day_start_balance == 100.0
    g2.record_trade(-5.0)
    assert g2.day_stats.net_pnl_pct == -5.0


def test_session_windows_match_moscow_table():
    # 20:51 МСК 20.10.2026 = 17:51 UTC → сессия Нью-Йорка до 01:00
    now = datetime(2026, 10, 20, 17, 51, tzinfo=timezone.utc)
    window = session_window(now, 3)
    assert window.name == "Нью-Йорк"
    assert window.end_hhmm == "01:00"
    assert window.start_utc == datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
    assert window.end_utc == datetime(2026, 10, 20, 22, 0, tzinfo=timezone.utc)

    # 14:00 МСК = 11:00 UTC → Европа до 19:00
    europe = session_window(datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc), 3)
    assert europe.name == "Европа"
    assert europe.end_hhmm == "19:00"
    assert europe.start_utc == datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)

    # 00:30 МСК 21.10 = 21:30 UTC 20.10 → всё ещё Нью-Йорк до 01:00
    late = session_window(datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc), 3)
    assert late.name == "Нью-Йорк"
    assert late.start_utc == datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)


def test_fast_entry_allows_fresh_symbol(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _pin_open_session(monkeypatch)

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
