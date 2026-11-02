"""Привычки ruflo: журнал reasons[], стоп-краны на дне МСК, TTL 24ч, урок только после закрытия."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prd_agent.analysis.reject_journal import RejectJournal, classify_source, plain_reason
from prd_agent.market.scanner_learning import (
    append_closed_scanner_learning_event,
    decided_window,
    is_verified_closed_lesson,
    record_closed_trade_lesson,
)
from prd_agent.risk.guard import DAY_LOCKED_STOP_KINDS, RiskGuard, StopKind, named_stop_reason
from prd_agent.risk.market_sessions import session_window
from prd_agent.signals.ttl import (
    SIGNAL_TTL_SECONDS,
    filter_live_signals,
    fingerprint_from_row,
    is_synthetic_or_missing_bar,
    oldest_created_at,
    signal_fingerprint,
)
from prd_agent.signals.types import UnifiedSignal
from prd_agent.supervisor.skipped_signal_backtest import SkippedSignalBacktester

ROOT = Path(__file__).resolve().parents[2]
MSK = timezone(timedelta(hours=3))
SESSION_BOUNDARIES_MSK = (
    (1, 0),
    (9, 0),
    (12, 0),
    (19, 0),
    (20, 0),
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
        "trading": {"max_positions": 2},
    }


def _row(symbol: str, pnl: float, *, age_sec: int = 120) -> dict:
    ts = int((datetime.now(timezone.utc) - timedelta(seconds=age_sec)).timestamp() * 1000)
    return {"symbol": symbol, "closedPnl": str(pnl), "updatedTime": str(ts)}


def test_reject_row_has_verdict_and_reasons_array(tmp_path: Path) -> None:
    journal = RejectJournal.in_data_dir(tmp_path)
    row = journal.maybe_record(
        symbol="BTCUSDT",
        reason="supervisor_v4: местный UTC+3 час 17 заблокирован (режим NORMAL)",
        mark_price=95000.0,
        intended_size=0.001,
        side="Buy",
        source="supervisor_v4",
    )
    assert row is not None
    assert row["verdict"] == "BLOCK"
    assert row["reasons"] == ["час 17 заблокирован"]
    parsed = json.loads(journal.path.read_text(encoding="utf-8").splitlines()[0])
    assert parsed["verdict"] == "BLOCK"
    assert isinstance(parsed["reasons"], list)
    assert parsed["reasons"][0] == "час 17 заблокирован"


def test_named_stop_kinds_are_stable() -> None:
    assert StopKind.DAILY_LOSS.value == "daily_loss"
    assert StopKind.SYMBOL_LOSS.value == "symbol_loss"
    assert StopKind.CONCENTRATION.value == "concentration"
    assert named_stop_reason(StopKind.DAILY_LOSS, "Дневной лимит убытка $6.20 (лимит $5)").startswith(
        "[daily_loss]"
    )
    assert {
        StopKind.DAILY_LOSS,
        StopKind.SYMBOL_LOSS,
        StopKind.TRADE_LIMIT,
        StopKind.CONCENTRATION,
    } <= DAY_LOCKED_STOP_KINDS
    assert classify_source("[daily_loss] Дневной лимит убытка $6.20 (лимит $5)") == "risk_guard"
    assert classify_source("[symbol_loss] JCTUSDT: уже 2 убыточных") == "risk_guard"
    assert plain_reason("risk_guard", "[concentration] Макс. позиций: 8") == "потолок концентрации"


def test_session_boundaries_do_not_reset_day_stops(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    day = datetime(2026, 11, 2, tzinfo=MSK).date()
    day_start = datetime(2026, 11, 1, 21, 0, tzinfo=timezone.utc)
    loss_ts = str(int((day_start + timedelta(hours=2)).timestamp() * 1000))
    monkeypatch.setattr(RiskGuard, "_trading_day", lambda self: day)
    monkeypatch.setattr(RiskGuard, "_trading_day_start_utc", lambda self: day_start)
    for hour, minute in SESSION_BOUNDARIES_MSK:
        local = datetime(2026, 11, 2, hour, minute, tzinfo=MSK)
        window = session_window(local.astimezone(timezone.utc), 3)
        monkeypatch.setattr(
            "prd_agent.risk.guard.session_window",
            lambda *_args, **_kwargs: window,
        )
        g = RiskGuard(_cfg(), initial_balance=80.0)
        g.reconcile_from_closed_rows(
            [{"symbol": "PHAUSDT", "closedPnl": "-11", "updatedTime": loss_ts}],
            balance=80.0,
        )
        ok, reason = g.can_trade("SOLUSDT")
        assert not ok, f"session {hour:02d}:{minute:02d} released daily stop: {reason}"
        assert "[daily_loss]" in reason
        assert "01:00/09:00/12:00/19:00/20:00" in reason
        assert g.stop_kind == StopKind.DAILY_LOSS
        ok_fast, reason_fast = g.fast_path_block("SOLUSDT")
        assert not ok_fast
        assert "[daily_loss]" in reason_fast


def test_symbol_loss_and_concentration_named(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    now = datetime.now(timezone.utc)
    g = RiskGuard(_cfg(), initial_balance=80.0)
    g.reconcile_from_closed_rows(
        [_row("JCTUSDT", -2.0, age_sec=40 * 60), _row("JCTUSDT", -2.0, age_sec=50 * 60)],
        balance=80.0,
    )
    ok, reason = g.can_trade("JCTUSDT")
    assert not ok
    assert "[symbol_loss]" in reason
    assert "торговый день" in reason
    g2 = RiskGuard(_cfg(), initial_balance=80.0)
    g2.open_positions_count = 2
    ok_c, reason_c = g2.can_trade("BTCUSDT")
    assert not ok_c
    assert "[concentration]" in reason_c


def test_signal_ttl_drops_stale_synthetic_and_duplicates() -> None:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    fresh = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.9,
        source="telegram",
        entry=100,
        stop_loss=98,
        take_profit=104,
        created_at=(now - timedelta(hours=2)).isoformat(),
    )
    stale = UnifiedSignal(
        symbol="ETHUSDT",
        side="Buy",
        confidence=0.9,
        source="telegram",
        entry=100,
        stop_loss=98,
        take_profit=104,
        created_at=(now - timedelta(hours=25)).isoformat(),
    )
    synthetic = UnifiedSignal(
        symbol="SOLUSDT",
        side="Buy",
        confidence=0.9,
        source="ta_volatility",
        entry=20,
        stop_loss=19,
        take_profit=22,
        raw={"data_source": "synthetic", "missing_bar": True},
        created_at=now.isoformat(),
    )
    copy = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.91,
        source="telegram",
        entry=100,
        stop_loss=98,
        take_profit=104,
        created_at=(now - timedelta(hours=1)).isoformat(),
    )
    assert SIGNAL_TTL_SECONDS == 24 * 3600
    assert is_synthetic_or_missing_bar(synthetic) is True
    kept = filter_live_signals([fresh, stale, synthetic, copy], now=now)
    assert [s.symbol for s in kept] == ["BTCUSDT"]
    assert signal_fingerprint(fresh) == signal_fingerprint(copy)


def test_oldest_created_at_preserved_for_merged_ttl() -> None:
    now = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
    old = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.9,
        source="telegram",
        entry=100,
        stop_loss=98,
        take_profit=106,
        created_at=(now - timedelta(hours=30)).isoformat(),
        raw={"created_at": (now - timedelta(hours=30)).isoformat()},
    )
    live = UnifiedSignal(
        symbol="ETHUSDT",
        side="Buy",
        confidence=0.9,
        source="telegram",
        entry=50,
        stop_loss=49,
        take_profit=53,
        created_at=(now - timedelta(hours=1)).isoformat(),
        raw={"created_at": (now - timedelta(hours=1)).isoformat()},
    )
    assert oldest_created_at([old, live]).startswith("2026-11-01")
    kept = filter_live_signals([old, live], now=now)
    assert [s.symbol for s in kept] == ["ETHUSDT"]
    src = (ROOT / "prd_agent" / "signals" / "router.py").read_text(encoding="utf-8")
    assert "filter_live_signals" in src
    assert "oldest_created_at" in src


def test_skipped_backtest_dedups_fingerprint(tmp_path: Path) -> None:
    bt = SkippedSignalBacktester(tmp_path / "sup", cfg={"skipped_signal_backtest": {}})
    row = {
        "id": "a1",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "source": "telegram",
        "entry": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "status": "skipped",
        "reason": "час 17 заблокирован",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    copy = dict(row)
    copy["id"] = "a2"
    assert fingerprint_from_row(row) == fingerprint_from_row(copy)
    bt._done_fingerprints.add(fingerprint_from_row(row))
    picked = bt._pick_candidates([row, copy])
    assert picked == []


def test_learning_only_from_verified_closed_scanner_trade() -> None:
    assert is_verified_closed_lesson(source="SPIKE_SCANNER", order_id="ord-1") is True
    assert is_verified_closed_lesson(source="telegram_channel") is False
    assert is_verified_closed_lesson(source="SPIKE_SCANNER", kind="hypothetical") is False
    state: dict = {}
    assert (
        append_closed_scanner_learning_event(
            state, source="rejected", symbol="BTCUSDT", pnl=1.0, order_id="x"
        )
        is None
    )
    assert (
        append_closed_scanner_learning_event(
            state,
            source="SPIKE_SCANNER",
            symbol="BTCUSDT",
            pnl=1.2,
            order_id="ord-9",
        )
        is not None
    )
    event, _proposed = record_closed_trade_lesson(
        state,
        source="MARKET_SCANNER",
        symbol="ETHUSDT",
        pnl=-0.4,
        order_id="ord-10",
    )
    assert event is not None
    assert event["kind"] == "closed_trade"
    state["market_scanner_learning_events"].append(
        {
            "id": "hyp-1",
            "kind": "hypothetical",
            "outcome": "win",
            "symbol": "FAKEUSDT",
        }
    )
    decided = decided_window(state["market_scanner_learning_events"], 30)
    assert all(item.get("kind") != "hypothetical" for item in decided)
    assert any(item.get("id") == "closed:ord-9" for item in decided)


def test_no_human_confirm_or_exchange_trailing() -> None:
    text = (ROOT / "prd_agent" / "analysis" / "reject_journal.py").read_text(encoding="utf-8")
    assert "confirm: true" not in text
    assert "trailingStop" not in text
    assert "input(" not in text
    guard_src = (ROOT / "prd_agent" / "risk" / "guard.py").read_text(encoding="utf-8")
    assert "StopKind.DAILY_LOSS" in guard_src
    assert "01:00/09:00/12:00/19:00/20:00" in guard_src
    learn_src = (ROOT / "prd_agent" / "market" / "scanner_learning.py").read_text(encoding="utf-8")
    assert "is_verified_closed_lesson" in learn_src
    core_src = (ROOT / "prd_agent" / "market" / "scanner_core.py").read_text(encoding="utf-8")
    assert "unverified setup" in core_src
    assert "channel_trade_search: false" in (ROOT / "config.yaml").read_text(encoding="utf-8")
