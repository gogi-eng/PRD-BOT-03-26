#!/usr/bin/env python3
"""Тесты analyze_spike_trailing_72h / spike_trailing_report."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.analysis.spike_trailing_report import (
    analyze_spike_trailing,
    format_spike_trailing_md,
    is_spike_trade_row,
    parse_bot_log,
    read_trade_history_all,
)


def test_is_spike_trade_row_spike_scanner():
    ok, _ = is_spike_trade_row({"source": "SPIKE_SCANNER", "event": "entered"})
    assert ok is True


def test_read_trade_history_includes_archive(tmp_path: Path):
    trades = tmp_path / "trades"
    archive = trades / "archive"
    archive.mkdir(parents=True)
    row = {"event": "closed", "symbol": "SOLUSDT", "source": "SPIKE_SCANNER", "pnl": 1.0}
    (archive / "trade_history_old.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    rows = read_trade_history_all(tmp_path)
    assert len(rows) == 1


def test_analyze_spike_trailing_journal_and_log(tmp_path: Path):
    data = tmp_path / "data"
    ledger = data / "ledger"
    trades = data / "trades"
    ledger.mkdir(parents=True)
    trades.mkdir(parents=True)

    entered = {
        "event": "entered",
        "symbol": "CAPUSDT",
        "side": "Buy",
        "source": "SPIKE_SCANNER",
        "entry": 1.0,
        "ts": "2099-06-01T10:00:00+00:00",
    }
    closed = {
        "event": "closed",
        "symbol": "CAPUSDT",
        "side": "Buy",
        "source": "SPIKE_SCANNER",
        "reason": "exchange_closed",
        "pnl": -0.12,
        "entry": 1.0,
        "exit": 0.995,
        "ts": "2099-06-01T10:25:00+00:00",
    }
    (trades / "trade_history.jsonl").write_text(
        json.dumps(entered) + "\n" + json.dumps(closed) + "\n",
        encoding="utf-8",
    )
    ledger_row = {
        "symbol": "CAPUSDT",
        "source": "SPIKE_SCANNER",
        "status": "executed",
        "reason": "spike_scalp_pump",
        "created_at": "2099-06-01T09:55:00+00:00",
    }
    (ledger / "signal_ledger.jsonl").write_text(json.dumps(ledger_row) + "\n", encoding="utf-8")

    log = tmp_path / "bot.log"
    log.write_text(
        "2099-06-01 10:00:00,123 [INFO] prd_agent.trades: ENTERED CAPUSDT: BUY [SPIKE_SCANNER]\n"
        "2099-06-01 10:25:00,456 [INFO] prd_agent: EXIT CAPUSDT Buy action=close_time_stop "
        "reason=time_stop peak=1.20% age=25m\n",
        encoding="utf-8",
    )

    report = analyze_spike_trailing(tmp_path, data_dir=data, hours=24 * 365)
    assert len(report.spike_pairs) == 1
    assert report.log_spike_entered == 1
    assert report.ledger_spike_total == 1
    md = format_spike_trailing_md(report)
    assert "CAPUSDT" in md
    assert "pump_dump_trailing" in md or "Недостаточно данных" in md


def test_parse_bot_log_spike_signal():
    text = (
        "2026-07-08 12:00:00,100 [INFO] agent: "
        "SPIKE_SCANNER pump_dump_spike CAPUSDT PUMP move=4.50% score=80\n"
    )
    path = Path("_test_bot.log")
    try:
        path.write_text(text, encoding="utf-8")
        parsed = parse_bot_log(path, hours=72)
        assert len(parsed["spike_signals"]) == 1
        assert parsed["spike_signals"][0]["symbol"] == "CAPUSDT"
    finally:
        path.unlink(missing_ok=True)
