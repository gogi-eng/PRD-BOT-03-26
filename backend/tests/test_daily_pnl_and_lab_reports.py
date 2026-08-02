"""Отчёты Telegram: 📅 По дням и 🧪 Лаборатория."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prd_agent.analysis.trade_analytics import build_daily_pnl_report
from prd_agent.supervisor.skipped_signal_backtest import SkippedSignalBacktester


def _write_journal(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def test_daily_pnl_groups_by_local_day(tmp_path: Path) -> None:
    journal = tmp_path / "data" / "trades" / "trade_history.jsonl"
    # Два разных местных дня UTC+3: ~3ч назад и ~30ч назад
    _write_journal(
        journal,
        [
            {
                "event": "closed",
                "ts": _iso_hours_ago(3),
                "pnl": 2.5,
                "symbol": "BTCUSDT",
                "origin": "bot",
            },
            {
                "event": "closed",
                "ts": _iso_hours_ago(30),
                "pnl": -1.0,
                "symbol": "ETHUSDT",
                "origin": "bot",
            },
        ],
    )
    text = build_daily_pnl_report(journal, days=7, timezone_offset=3)
    assert "📅 PnL по дням" in text
    assert "+2.50" in text
    assert "-1.00" in text
    assert "Итого всё:" in text
    assert "Итого бот:" in text
    assert "Итого ручные:" in text


def test_daily_pnl_splits_bot_and_manual(tmp_path: Path) -> None:
    journal = tmp_path / "data" / "trades" / "trade_history.jsonl"
    _write_journal(
        journal,
        [
            {
                "event": "closed",
                "ts": _iso_hours_ago(2),
                "pnl": 5.0,
                "symbol": "BTCUSDT",
                "origin": "bot",
            },
            {
                "event": "closed",
                "ts": _iso_hours_ago(1),
                "pnl": -2.0,
                "symbol": "ETHUSDT",
                "origin": "manual",
            },
        ],
    )
    text = build_daily_pnl_report(
        journal, days=7, timezone_offset=3, split_origin=True, exclude_manual=False
    )
    assert "бот +5.00" in text
    assert "ручн. -2.00" in text
    assert "Итого бот:" in text
    assert "+5.00 USDT" in text
    assert "Итого ручные:" in text
    assert "-2.00 USDT" in text
    assert "Итого всё:" in text
    assert "+3.00 USDT" in text


def test_daily_pnl_exclude_manual(tmp_path: Path) -> None:
    journal = tmp_path / "data" / "trades" / "trade_history.jsonl"
    _write_journal(
        journal,
        [
            {
                "event": "closed",
                "ts": _iso_hours_ago(2),
                "pnl": 5.0,
                "symbol": "BTCUSDT",
                "origin": "bot",
            },
            {
                "event": "closed",
                "ts": _iso_hours_ago(1),
                "pnl": -2.0,
                "symbol": "ETHUSDT",
                "origin": "manual",
            },
        ],
    )
    text = build_daily_pnl_report(
        journal, days=7, timezone_offset=3, exclude_manual=True, split_origin=False
    )
    assert "без ручных" in text
    assert "+5.00 USDT" in text
    assert "Итого (бот):" in text
    assert "-2.00" not in text


def test_skipped_lab_report_empty(tmp_path: Path) -> None:
    bt = SkippedSignalBacktester(tmp_path / "sup", cfg={"skipped_signal_backtest": {}})
    text = bt.build_telegram_report(168)
    assert "🧪 Лаборатория" in text
    assert "Нет результатов" in text


def test_skipped_lab_report_with_rows(tmp_path: Path) -> None:
    bt = SkippedSignalBacktester(tmp_path / "sup", cfg={"skipped_signal_backtest": {}})
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "ledger_id": "abc1",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "skip_reason": "quality_gate: rr low",
        "outcome": "take_profit",
        "pnl_pct": 1.2,
        "pnl_pct_net": 1.1,
        "pnl_pct_gross": 1.2,
        "fee_pct_round_trip": 0.11,
        "backtested_at": now,
    }
    bt.results_path.parent.mkdir(parents=True, exist_ok=True)
    bt.results_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    bt._done_ids.add("abc1")
    text = bt.build_telegram_report(24, last_run={"tested": 1, "outcomes": {"take_profit": 1}})
    assert "WR если бы вошли" in text
    assert "quality_gate" in text
    assert "Последний прогон" in text
