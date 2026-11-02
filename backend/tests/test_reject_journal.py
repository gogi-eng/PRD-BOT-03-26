"""Журнал отказов до ордера: одна jsonl-строка, без «да» человека и без ордера."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from prd_agent.analysis.reject_journal import (
    MIN_NOTIONAL_USDT,
    RejectJournal,
    classify_source,
    plain_reason,
)

ROOT = Path(__file__).resolve().parents[2]


def test_min_notional_is_5_usdt():
    assert MIN_NOTIONAL_USDT == 5.0


def test_classify_listed_blockers():
    assert (
        classify_source(
            "supervisor_v4: местный UTC+3 час 17 заблокирован (режим NORMAL)"
        )
        == "hour"
    )
    assert classify_source("Дневной лимит убытка $6.20 (лимит $5)") == "risk_guard"
    assert (
        classify_source(
            "Номинал ордера 3.00 USDT ниже минимума Bybit 5.00 USDT (retCode=110094)"
        )
        == "notional"
    )
    assert classify_source("стакан против Buy: перекос -1.20 (порог -1.00)") == "orderbook"
    assert (
        classify_source("timesfm: direction mismatch (tfm=down Δ=-0.400% signal=up)")
        == "timesfm"
    )
    assert classify_source("RR ниже 1.6 после подгонки SL/TP", "scanner") == "scanner"
    assert classify_source("supervisor_v4: символ BTCUSDT в чёрном списке") == "supervisor_v4"
    assert classify_source("quality_gate: confidence 0.40 < 0.85") is None
    assert classify_source("local_hour=7<9") == "hour"


def test_plain_reason_uses_simple_words():
    assert (
        plain_reason(
            "hour",
            "supervisor_v4: местный UTC+3 час 17 заблокирован (режим NORMAL)",
        )
        == "час 17 заблокирован"
    )
    assert plain_reason("risk_guard", "Дневной лимит убытка $6.20 (лимит $5)") == "дневной лимит"
    assert (
        plain_reason(
            "notional",
            "Номинал ордера 3.00 USDT ниже минимума Bybit 5.00 USDT (retCode=110094)",
        )
        == "номинал ниже 5 USDT"
    )
    assert (
        plain_reason("orderbook", "стакан против Buy: перекос -1.20 (порог -1.00)")
        == "стакан против"
    )
    assert (
        plain_reason("timesfm", "timesfm: direction mismatch (tfm=down Δ=-0.400% signal=up)")
        == "TimesFM против входа"
    )


def test_one_jsonl_line_per_blocked_entry(tmp_path: Path):
    journal = RejectJournal.in_data_dir(tmp_path)
    rows = [
        journal.maybe_record(
            symbol="BTCUSDT",
            reason="supervisor_v4: местный UTC+3 час 17 заблокирован (режим NORMAL)",
            mark_price=95000.0,
            intended_size=0.001,
            side="Buy",
            source="supervisor_v4",
        ),
        journal.maybe_record(
            symbol="ETHUSDT",
            reason="Дневной лимит убытка $6.20 (лимит $5)",
            mark_price=3200.0,
            intended_size=0.01,
            side="Sell",
            source="risk_guard",
        ),
        journal.maybe_record(
            symbol="AAVEUSDT",
            reason="стакан против Sell: перекос 1.30 (порог 1.00)",
            mark_price=180.0,
            intended_size=0.04,
            side="Sell",
            source="orderbook",
        ),
        journal.maybe_record(
            symbol="SOLUSDT",
            reason="timesfm: direction mismatch (tfm=up Δ=0.200% signal=down)",
            mark_price=150.0,
            intended_size=0.2,
            side="Sell",
            source="timesfm",
        ),
        journal.maybe_record(
            symbol="DOGEUSDT",
            reason="Номинал ордера 3.00 USDT ниже минимума Bybit 5.00 USDT (retCode=110094)",
            mark_price=0.12,
            intended_size=25.0,
            side="Buy",
        ),
        journal.maybe_record(
            symbol="BNBUSDT",
            reason="RR ниже 1.6 после подгонки SL/TP",
            mark_price=700.0,
            intended_size=0.02,
            side="Buy",
            source="scanner",
        ),
        journal.maybe_record(
            symbol="XRPUSDT",
            reason="supervisor_v4: символ XRPUSDT в чёрном списке",
            mark_price=0.5,
            intended_size=10.0,
            side="Buy",
            source="supervisor_v4",
        ),
    ]
    assert all(row is not None for row in rows)
    lines = journal.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 7
    parsed = [json.loads(line) for line in lines]
    assert [row["source"] for row in parsed] == [
        "hour",
        "risk_guard",
        "orderbook",
        "timesfm",
        "notional",
        "scanner",
        "supervisor_v4",
    ]
    hour = parsed[0]
    assert hour["symbol"] == "BTCUSDT"
    assert hour["verdict"] == "BLOCK"
    assert hour["reason"] == "час 17 заблокирован"
    assert hour["reasons"] == ["час 17 заблокирован"]
    assert hour["mark_price"] == 95000.0
    assert hour["intended_size"] == 0.001
    assert hour["side"] == "Buy"
    assert hour["note"] == "Журнал отказов до ордера. Из этой строки ордер не ставится."
    assert parsed[4]["reason"] == "номинал ниже 5 USDT"
    assert parsed[4]["reasons"] == ["номинал ниже 5 USDT"]
    assert parsed[1]["verdict"] == "BLOCK"
    assert "дневной лимит" in parsed[1]["reasons"]
    assert journal.maybe_record(
        symbol="BTCUSDT",
        reason="quality_gate: confidence 0.40 < 0.85",
        mark_price=95000.0,
        intended_size=0.001,
    ) is None
    assert len(journal.path.read_text(encoding="utf-8").splitlines()) == 7


def test_empty_symbol_is_not_written(tmp_path: Path):
    journal = RejectJournal(tmp_path / "reject_before_order.jsonl")
    assert (
        journal.maybe_record(
            symbol="",
            reason="час 17 заблокирован",
            mark_price=1.0,
            source="hour",
        )
        is None
    )
    assert not journal.path.exists()


def test_module_does_not_place_orders_or_wait_for_human():
    text = (ROOT / "prd_agent" / "analysis" / "reject_journal.py").read_text(encoding="utf-8")
    assert "place_order" not in text
    assert "trailingStop" not in text
    assert "activePrice" not in text
    assert "channel_trade_search" not in text
    assert "input(" not in text
    assert "fade" not in text.lower()
    assert "human yes" not in text.lower()


def test_hooks_live_on_orchestrator_and_scanner():
    orch_src = (ROOT / "prd_agent" / "engine" / "orchestrator.py").read_text(encoding="utf-8")
    agent_src = (ROOT / "scripts" / "telegram_signal_agent.py").read_text(encoding="utf-8")
    assert "RejectJournal.in_data_dir" in orch_src
    assert "def _journal_blocked_entry(" in orch_src
    assert 'source="risk_guard"' in orch_src
    assert 'source="supervisor_v4"' in orch_src
    assert 'source="timesfm"' in orch_src
    assert 'source="orderbook"' in orch_src
    assert "intended_qty = float(qty or 0)" in orch_src
    assert "RejectJournal.in_data_dir" in agent_src
    assert "def _maybe_journal_blocked_signal(" in agent_src
    assert "retCode=110094" in agent_src
    assert "channel_trade_search: false" in (ROOT / "config.yaml").read_text(encoding="utf-8")


def test_logger_name_exists():
    assert logging.getLogger("prd_agent.reject_journal").name == "prd_agent.reject_journal"
