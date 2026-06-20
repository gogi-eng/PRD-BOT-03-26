"""Отчёты Telegram: 📅 По дням и 🧪 Лаборатория."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prd_agent.analysis.trade_analytics import build_daily_pnl_report
from prd_agent.supervisor.skipped_signal_backtest import SkippedSignalBacktester


def _write_journal(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_daily_pnl_groups_by_local_day(tmp_path: Path) -> None:
    journal = tmp_path / "data" / "trades" / "trade_history.jsonl"
    # UTC 21:00 19.06 = 00:00 20.06 UTC+3
    _write_journal(
        journal,
        [
            {
                "event": "closed",
                "ts": "2026-06-19T21:30:00+00:00",
                "pnl": 2.5,
                "symbol": "BTCUSDT",
            },
            {
                "event": "closed",
                "ts": "2026-06-19T10:00:00+00:00",
                "pnl": -1.0,
                "symbol": "ETHUSDT",
            },
        ],
    )
    text = build_daily_pnl_report(journal, days=7, timezone_offset=3)
    assert "📅 PnL по дням" in text
    assert "+2.50 USDT" in text
    assert "-1.00 USDT" in text
    assert "Итого:" in text


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
