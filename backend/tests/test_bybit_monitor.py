#!/usr/bin/env python3
"""Тесты bybit_monitor (без сети)."""
from __future__ import annotations

from prd_agent.analysis.bybit_monitor import (
    BybitMonitorAgent,
    format_position_line,
    summarize_klines,
)
from prd_agent.exchange.bybit_read_adapter import resolve_read_exchange_cfg


def test_summarize_klines_uptrend():
    klines = [
        {"close": 100, "high": 101, "low": 99, "volume": 10},
        {"close": 101, "high": 102, "low": 100, "volume": 12},
        {"close": 102, "high": 103, "low": 101, "volume": 11},
        {"close": 103, "high": 104, "low": 102, "volume": 13},
        {"close": 104, "high": 105, "low": 103, "volume": 14},
        {"close": 105, "high": 106, "low": 104, "volume": 15},
        {"close": 106, "high": 107, "low": 105, "volume": 16},
        {"close": 107, "high": 108, "low": 106, "volume": 17},
        {"close": 108, "high": 109, "low": 107, "volume": 18},
        {"close": 110, "high": 111, "low": 109, "volume": 30},
    ]
    summary = summarize_klines(klines, label="BTCUSDT")
    assert summary["trend"] == "вверх"
    assert summary["change_pct"] > 0
    assert summary["volume_trend"] == "растёт"


def test_format_position_line():
    row = {
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": 0.01,
        "avgPrice": 60000,
        "markPrice": 60500,
        "unrealisedPnl": 5.0,
        "stopLoss": 59000,
        "takeProfit": 62000,
        "liqPrice": 55000,
        "leverage": 10,
    }
    line = format_position_line(row)
    assert "BTCUSDT" in line
    assert "uPnL=+5.00" in line
    assert "dist_liq=" in line


def test_resolve_read_exchange_cfg():
    cfg = {
        "bybit": {
            "api_key": "trade",
            "api_secret": "trade_secret",
            "read_api_key": "read",
            "read_api_secret": "read_secret",
        },
        "_root": "/tmp",
    }
    read_cfg = resolve_read_exchange_cfg(cfg)
    assert read_cfg is not None
    assert read_cfg["bybit"]["api_key"] == "read"
    assert read_cfg["bybit"]["api_secret"] == "read_secret"


def test_bybit_monitor_disabled_message():
    agent = BybitMonitorAgent({"bybit_monitor": {"enabled": False}})
    assert agent.enabled is False
