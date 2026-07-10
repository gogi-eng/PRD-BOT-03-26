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


def test_analyze_by_hour_and_weekday(tmp_path: Path):
    trades = tmp_path / "trades"
    trades.mkdir(parents=True)
    # Пн 2026-07-06 10:00 UTC = 13:00 MSK (UTC+3), hour=13
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
    h13 = report.by_hour[13]
    h17 = report.by_hour[17]
    assert h13.trades == 1
    assert h13.max_single_profit == 5.0
    assert h17.trades == 1
    assert h17.max_single_loss == -3.0
    assert report.by_weekday[0].trades == 2  # Monday
    md = format_trade_time_profile_md(report)
    assert "13:00" in md
    assert "Пн" in md
