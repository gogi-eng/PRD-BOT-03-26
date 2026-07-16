#!/usr/bin/env python3
"""Тесты open_position_gate."""
from __future__ import annotations

from prd_agent.positions.open_position_gate import (
    has_open_position_for_symbol,
    open_position_skip_reason,
    symbols_with_open_positions,
)


def test_symbols_with_open_positions():
    rows = [
        {"symbol": "RAVEUSDT", "size": 10},
        {"symbol": "BTCUSDT", "size": 0},
        {"symbol": "ETHUSDT", "size": 0.01},
    ]
    assert symbols_with_open_positions(rows) == {"RAVEUSDT", "ETHUSDT"}
    assert has_open_position_for_symbol(rows, "raveusdt") is True
    assert has_open_position_for_symbol(rows, "BTCUSDT") is False


def test_open_position_skip_reason():
    assert "RAVEUSDT" in open_position_skip_reason("raveusdt")
