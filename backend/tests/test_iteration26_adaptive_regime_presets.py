#!/usr/bin/env python3
from __future__ import annotations

import inspect
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from main import TradingBot


def _build_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.adaptive_trend_strict_htf_mode = True
    bot.adaptive_trend_volatility_floor_atr_pct = 0.06
    bot.adaptive_range_strict_htf_mode = False
    bot.adaptive_range_volatility_floor_atr_pct = 0.02
    bot.adaptive_trend_require_4h_trend = True
    bot.adaptive_range_require_4h_trend = False
    bot.entry_require_4h_trend = True
    bot.entry_engine = type("DummyEntryEngine", (), {"require_4h_trend": True})()
    bot._active_regime_profile = "manual"
    bot.strict_htf_mode = True
    bot.volatility_floor_atr_pct = 0.06
    bot.adaptive_regime_presets_enabled = True
    bot.adaptive_regime_presets_interval_sec = 1
    bot._last_regime_profile_check_ts = 0.0
    bot.adaptive_regime_presets_notify = False
    bot.tg = None
    return bot


def test_resolve_regime_preset_trend():
    bot = _build_bot()
    profile, strict_htf, vol_floor = bot._resolve_regime_preset("trend")
    assert profile == "trend"
    assert strict_htf is True
    assert vol_floor == 0.06


def test_resolve_regime_preset_breakout_maps_to_trend():
    bot = _build_bot()
    profile, strict_htf, vol_floor = bot._resolve_regime_preset("breakout")
    assert profile == "trend"
    assert strict_htf is True
    assert vol_floor == 0.06


def test_resolve_regime_preset_range():
    bot = _build_bot()
    profile, strict_htf, vol_floor = bot._resolve_regime_preset("chop")
    assert profile == "range"
    assert strict_htf is False
    assert vol_floor == 0.02


def test_maybe_apply_regime_preset_toggles_require_4h_trend_by_profile():
    import asyncio
    from unittest.mock import AsyncMock

    bot = _build_bot()
    bot._detect_profile_regime = AsyncMock(return_value="chop")
    assert bot.entry_require_4h_trend is True
    asyncio.run(bot._maybe_apply_regime_preset())
    assert bot.entry_require_4h_trend is False
    assert bot.entry_engine.require_4h_trend is False

    bot._detect_profile_regime = AsyncMock(return_value="trend")
    bot._last_regime_profile_check_ts = 0.0
    asyncio.run(bot._maybe_apply_regime_preset())
    assert bot.entry_require_4h_trend is True
    assert bot.entry_engine.require_4h_trend is True


def test_run_calls_maybe_apply_regime_preset():
    source = inspect.getsource(TradingBot.run)
    assert "await self._maybe_apply_regime_preset()" in source


def test_config_has_adaptive_regime_presets_section():
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as handle:
        cfg = yaml.safe_load(handle)

    assert cfg["adaptive_regime_presets"]["enabled"] is True
    assert cfg["adaptive_regime_presets"]["switch_interval_sec"] == 900
    assert cfg["adaptive_regime_presets"]["benchmark_symbol"] == "BTCUSDT"
    assert cfg["adaptive_regime_presets"]["trend_volatility_floor_atr_pct"] == 0.06
    assert cfg["adaptive_regime_presets"]["range_volatility_floor_atr_pct"] == 0.02
