#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from main import TradingBot


def _build_bot(strict_htf: bool = True, vol_enabled: bool = True, vol_floor: float = 0.8) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.strict_htf_mode = strict_htf
    bot.volatility_floor_enabled = vol_enabled
    bot.volatility_floor_atr_pct = vol_floor
    return bot


def test_strict_htf_blocks_sell_in_bull_4h():
    bot = _build_bot(strict_htf=True)
    ok, reason = bot._passes_strict_htf_mode("SELL", 1)
    assert ok is False
    assert reason == "strict_htf_bull_only"


def test_strict_htf_blocks_buy_in_bear_4h():
    bot = _build_bot(strict_htf=True)
    ok, reason = bot._passes_strict_htf_mode("BUY", -1)
    assert ok is False
    assert reason == "strict_htf_bear_only"


def test_strict_htf_allows_aligned_direction():
    bot = _build_bot(strict_htf=True)
    assert bot._passes_strict_htf_mode("BUY", 1)[0] is True
    assert bot._passes_strict_htf_mode("SELL", -1)[0] is True


def test_strict_htf_allows_when_disabled():
    bot = _build_bot(strict_htf=False)
    ok, _ = bot._passes_strict_htf_mode("SELL", 1)
    assert ok is True


def test_volatility_floor_blocks_low_atr_pct():
    bot = _build_bot(vol_enabled=True, vol_floor=0.8)
    ok, reason = bot._passes_volatility_floor(0.55)
    assert ok is False
    assert "volatility_floor" in reason


def test_volatility_floor_allows_high_atr_pct():
    bot = _build_bot(vol_enabled=True, vol_floor=0.8)
    ok, _ = bot._passes_volatility_floor(1.1)
    assert ok is True


def test_volatility_floor_allows_when_disabled():
    bot = _build_bot(vol_enabled=False, vol_floor=0.8)
    ok, _ = bot._passes_volatility_floor(0.2)
    assert ok is True


def test_config_has_strict_htf_and_volatility_floor():
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as handle:
        cfg = yaml.safe_load(handle)

    assert cfg["entry"]["strict_htf_mode"] is True
    assert cfg["entry"]["volatility_floor_enabled"] is True
    assert cfg["entry"]["volatility_floor_atr_pct"] == 0.8
