#!/usr/bin/env python3
"""Тесты trade_time_profile."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.analysis.trade_time_profile import (
    _max_drawdown_from_pnls,
    analyze_trade_time_profile,
    format_trade_time_profile_md,
)


def test_max_drawdown_sequence():
    assert _max_drawdown_from_pnls([10, -5, -8, 20]) == 13.0


def test_analyze_by_entry_and_close_time(tmp_path: Path):
    trades = tmp_path / "trades"
    trades.mkdir(parents=True)
    # вход 10:00 UTC = 13:00 MSK, закрытие 11:00 UTC = 14:00 MSK
    entered = {
        "event": "entered",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "ts": "2026-07-06T10:00:00+00:00",
    }
    closed_win = {
        "event": "closed",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "pnl": 5.0,
        "ts": "2026-07-06T11:00:00+00:00",
    }
    entered2 = {
        "event": "entered",
        "symbol": "ETHUSDT",
        "side": "Sell",
        "ts": "2026-07-06T14:00:00+00:00",
    }
    closed_loss = {
        "event": "closed",
        "symbol": "ETHUSDT",
        "side": "Sell",
        "pnl": -3.0,
        "ts": "2026-07-06T15:00:00+00:00",
    }
    (trades / "trade_history.jsonl").write_text(
        "\n".join(json.dumps(r) for r in (entered, closed_win, entered2, closed_loss)) + "\n",
        encoding="utf-8",
    )

    report = analyze_trade_time_profile(tmp_path, hours=24 * 365, tz_offset_hours=3)
    assert report.trades_total == 2
    assert report.entry.by_hour[13].trades == 1
    assert report.entry.by_hour[17].trades == 1
    assert report.close.by_hour[14].trades == 1
    assert report.close.by_hour[18].trades == 1
    md = format_trade_time_profile_md(report)
    assert "По времени ВХОДА" in md
    assert "По времени ЗАКРЫТИЯ" in md
