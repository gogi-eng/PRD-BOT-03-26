# -*- coding: utf-8 -*-
"""Комиссия legacy/ExitEngine: exit.fee_rate → positions.fee_breakeven.taker_rate."""
from __future__ import annotations

from core.config import BotConfig
from exchange.bybit_fees import (
    DEFAULT_TAKER_RATE,
    resolve_taker_fee_rate_from_config,
    resolve_taker_fee_rate_from_mapping,
)


def test_fee_rate_from_fee_breakeven_when_exit_missing():
    cfg = {
        "positions": {"fee_breakeven": {"taker_rate": 0.00055}},
    }
    assert resolve_taker_fee_rate_from_mapping(cfg) == 0.00055


def test_fee_rate_exit_overrides_fee_breakeven():
    cfg = {
        "exit": {"fee_rate": 0.0006},
        "positions": {"fee_breakeven": {"taker_rate": 0.00055}},
    }
    assert resolve_taker_fee_rate_from_mapping(cfg) == 0.0006


def test_fee_rate_default_when_empty():
    assert resolve_taker_fee_rate_from_mapping({}) == DEFAULT_TAKER_RATE


def test_fee_rate_bot_config_nested():
    cfg = BotConfig(
        {
            "positions": {
                "fee_breakeven": {"taker_rate": 0.00055},
            }
        }
    )
    assert resolve_taker_fee_rate_from_config(cfg) == 0.00055
