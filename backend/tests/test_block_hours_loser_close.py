#!/usr/bin/env python3
"""Тесты block_hours_loser_close."""
from __future__ import annotations

from prd_agent.positions.block_hours_loser_close import (
    PreBlockCloseConfig,
    favorable_trend,
    profit_pct_from_row,
    read_trading_hours_ctl_flags,
    should_close_before_block,
)


def test_close_loser_keep_winner():
    cfg = PreBlockCloseConfig(losers_only=True, fee_buffer_pct=0.12)
    loser = {
        "symbol": "BTCUSDT",
        "side": "Buy",
        "avgPrice": 100.0,
        "markPrice": 99.0,
        "unrealisedPnl": -1.5,
        "size": 1.0,
    }
    winner = {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "avgPrice": 100.0,
        "markPrice": 101.5,
        "unrealisedPnl": 1.2,
        "size": 1.0,
    }
    close_l, _ = should_close_before_block(loser, cfg=cfg, closes=[100, 99.5, 99.0])
    close_w, _ = should_close_before_block(
        winner, cfg=cfg, closes=[100.0, 100.5, 101.0, 101.5]
    )
    assert close_l is True
    assert close_w is False


def test_favorable_trend_long():
    closes = [100.0, 100.5, 101.0, 101.5]
    assert favorable_trend("Buy", closes, 3) is True
    assert favorable_trend("Sell", closes, 3) is False


def test_read_flags_stop_systemd_default_false():
    cfg = {
        "trading": {
            "non_trading_systemd": {
                "enabled": True,
                "pre_block_close": {"enabled": True},
            }
        }
    }
    flags = read_trading_hours_ctl_flags(cfg)
    assert flags["stop_systemd"] is False
    assert flags["pre_block_close"] is True


def test_profit_pct_short():
    row = {"side": "Sell", "avgPrice": 100.0, "markPrice": 98.0, "size": 1.0}
    assert profit_pct_from_row(row) > 0
