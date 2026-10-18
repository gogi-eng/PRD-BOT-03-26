"""Журнал: вход SPIKE пишется так же, как обычный, причина закрытия не теряется."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.analysis.trade_journal import TradeJournal, close_reason_from_context
from prd_agent.positions.position_steward import PositionSteward, TrackedPosition


def _read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_log_entered_writes_same_fields_as_orchestrator(tmp_path: Path) -> None:
    journal = TradeJournal(tmp_path, {})
    journal.log_entered(
        symbol="btcusdt",
        side="Buy",
        source="SPIKE_SCANNER",
        qty=1.5,
        entry=100.0,
        order_id="oid-1",
        stop_loss=99.0,
        take_profit=106.0,
        confidence=80.0,
        leverage=10,
        origin="bot",
        entry_context={"path": "telegram_signal_agent", "notional": 22.5},
    )
    rows = _read_rows(journal.path)
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "entered"
    assert row["symbol"] == "BTCUSDT"
    assert row["side"] == "Buy"
    assert row["source"] == "SPIKE_SCANNER"
    assert row["order_id"] == "oid-1"
    assert row["stop_loss"] == 99.0
    assert row["take_profit"] == 106.0
    assert row["entry_context"]["path"] == "telegram_signal_agent"


def test_exchange_close_keeps_steward_reason(tmp_path: Path) -> None:
    journal = TradeJournal(tmp_path, {})
    journal.record_closed_from_exchange(
        {
            "symbol": "ETHUSDT",
            "closedPnl": -1.2,
            "orderId": "close-1",
            "side": "Buy",
            "avgEntryPrice": 2000,
            "avgExitPrice": 1980,
            "qty": 0.01,
        },
        exit_context={"close_reason": "close_time_stop: 45m"},
        reason="close_time_stop: 45m",
    )
    row = _read_rows(journal.path)[0]
    assert row["event"] == "closed"
    assert row["reason"] == "close_time_stop: 45m"
    assert row["pnl"] == -1.2


def test_exchange_close_without_named_reason_stays_exchange_closed(tmp_path: Path) -> None:
    journal = TradeJournal(tmp_path, {})
    journal.record_closed_from_exchange(
        {
            "symbol": "SOLUSDT",
            "closedPnl": 0.4,
            "orderId": "close-2",
            "side": "Sell",
            "avgEntryPrice": 150,
            "avgExitPrice": 149,
            "qty": 1,
        },
        exit_context={"close_reason": "exchange_closed"},
    )
    row = _read_rows(journal.path)[0]
    assert row["reason"] == "exchange_closed"


def test_close_reason_helper_ignores_blank_and_generic() -> None:
    assert close_reason_from_context(None) == ""
    assert close_reason_from_context({"close_reason": "exchange_closed"}) == ""
    assert close_reason_from_context({"close_reason": "  liquidation near "}) == "liquidation near"


def test_steward_remembers_close_reason_until_journal_reads_it(tmp_path: Path) -> None:
    steward = PositionSteward({"_root": str(tmp_path)})
    pos = TrackedPosition(symbol="btcusdt", side="Buy", entry=100.0, qty=0.01)
    steward._log_note_close(pos, "close_time_stop", "время вышло")
    assert steward.pop_close_reason("BTCUSDT") == "время вышло"
    assert steward.pop_close_reason("BTCUSDT") == ""
