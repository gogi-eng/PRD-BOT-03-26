"""Отчёты Telegram: 📅 По дням и 🧪 Лаборатория."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from prd_agent.analysis.trade_analytics import (
    build_daily_pnl_report,
    build_trades_csv_text,
    compute_daily_pnl_extremes,
    export_trades_csv,
)
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
    assert "Сводка периода" in text
    assert "Лучший день:" in text
    assert "Худший день:" in text
    assert "Серия минусов подряд:" in text


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


def test_daily_extremes_best_worst_and_loss_streak() -> None:
    day_rows = [
        ("20.09.2026", [{"pnl": 3.0, "origin": "bot"}]),
        ("19.09.2026", [{"pnl": -1.0, "origin": "bot"}]),
        ("18.09.2026", [{"pnl": -2.0, "origin": "bot"}]),
        ("17.09.2026", [{"pnl": -0.5, "origin": "bot"}]),
        ("16.09.2026", [{"pnl": 1.0, "origin": "bot"}]),
        ("15.09.2026", [{"pnl": -4.0, "origin": "bot"}]),
    ]
    ext = compute_daily_pnl_extremes(day_rows)
    assert ext is not None
    assert ext["best_day"] == "20.09.2026"
    assert ext["best_pnl"] == 3.0
    assert ext["worst_day"] == "15.09.2026"
    assert ext["worst_pnl"] == -4.0
    # 17–19.09 подряд минус → streak 3; одиночный 15.09 не длиннее
    assert ext["max_loss_streak"] == 3


def test_daily_extremes_streak_breaks_on_gap() -> None:
    day_rows = [
        ("20.09.2026", [{"pnl": -1.0}]),
        ("18.09.2026", [{"pnl": -2.0}]),  # пропуск 19.09 — серия рвётся
    ]
    ext = compute_daily_pnl_extremes(day_rows)
    assert ext is not None
    assert ext["max_loss_streak"] == 1


def test_trades_csv_week_rows_and_header(tmp_path: Path) -> None:
    journal = tmp_path / "data" / "trades" / "trade_history.jsonl"
    _write_journal(
        journal,
        [
            {
                "event": "closed",
                "ts": _iso_hours_ago(5),
                "pnl": 1.25,
                "symbol": "BTCUSDT",
                "side": "Buy",
                "reason": "tp",
                "source": "SPIKE",
                "origin": "bot",
                "entry": 100.0,
                "exit_price": 101.0,
                "qty": 0.01,
                "order_id": "oid1",
            },
            {
                "event": "entered",
                "ts": _iso_hours_ago(6),
                "symbol": "ETHUSDT",
                "origin": "bot",
            },
            {
                "event": "closed",
                "ts": _iso_hours_ago(50),
                "pnl": -0.5,
                "symbol": "ETHUSDT",
                "side": "Sell",
                "reason": "sl",
                "source": "TA",
                "origin": "manual",
            },
        ],
    )
    csv_text, summary = build_trades_csv_text(journal, days=7, timezone_offset=3)
    assert summary["n"] == 2
    assert abs(summary["total_pnl"] - 0.75) < 1e-9
    reader = csv.DictReader(io.StringIO(csv_text))
    assert reader.fieldnames is not None
    assert "symbol" in reader.fieldnames
    assert "local_day" in reader.fieldnames
    assert "pnl" in reader.fieldnames
    rows = list(reader)
    assert len(rows) == 2
    symbols = {r["symbol"] for r in rows}
    assert symbols == {"BTCUSDT", "ETHUSDT"}

    export_dir = tmp_path / "data" / "exports"
    path, caption = export_trades_csv(
        journal, export_dir, days=7, timezone_offset=3
    )
    assert path.exists()
    assert path.suffix == ".csv"
    assert "📥 CSV за 7 дн." in caption
    assert "+0.75" in caption
    assert path.read_text(encoding="utf-8").startswith("ts,local_day,symbol")


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
