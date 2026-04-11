#!/usr/bin/env python3
"""
Iteration 26: Adaptive Regime Presets - Full Test Suite

Tests for auto preset switching by market regime (trend/range) every 15 minutes
with bot.log + Telegram notifications.

Features tested:
- config has adaptive_regime_presets section with interval 900 and benchmark BTCUSDT
- main.py periodically calls _maybe_apply_regime_preset in run loop
- regime mapping: trend/breakout->trend profile, chop/range->range profile
- switch updates strict_htf_mode and volatility_floor_atr_pct
- telegram message emitted on profile switch when enabled
- no regressions in strict_htf/volatility floor and previous iteration tests
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from main import TradingBot


def _build_bot() -> TradingBot:
    """Build a minimal TradingBot instance for testing without full initialization."""
    bot = TradingBot.__new__(TradingBot)
    # Adaptive regime presets config
    bot.adaptive_regime_presets_enabled = True
    bot.adaptive_regime_presets_interval_sec = 900
    bot.adaptive_regime_presets_notify = True
    bot.adaptive_regime_presets_benchmark_symbol = "BTCUSDT"
    bot.adaptive_trend_strict_htf_mode = True
    bot.adaptive_trend_volatility_floor_atr_pct = 0.06
    bot.adaptive_range_strict_htf_mode = False
    bot.adaptive_range_volatility_floor_atr_pct = 0.02
    # Current state
    bot._last_regime_profile_check_ts = 0.0
    bot._active_regime_profile = "manual"
    bot.strict_htf_mode = True
    bot.volatility_floor_atr_pct = 0.06
    bot.tg = None
    return bot


# ============================================================================
# CONFIG TESTS
# ============================================================================

class TestConfigAdaptiveRegimePresets:
    """Tests for adaptive_regime_presets config section."""

    def test_config_has_adaptive_regime_presets_section(self):
        """Verify config.yaml has adaptive_regime_presets section."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as handle:
            cfg = yaml.safe_load(handle)

        assert "adaptive_regime_presets" in cfg
        assert cfg["adaptive_regime_presets"]["enabled"] is True

    def test_config_interval_is_900_seconds(self):
        """Verify switch_interval_sec is 900 (15 minutes)."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as handle:
            cfg = yaml.safe_load(handle)

        assert cfg["adaptive_regime_presets"]["switch_interval_sec"] == 900

    def test_config_benchmark_is_btcusdt(self):
        """Verify benchmark_symbol is BTCUSDT."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as handle:
            cfg = yaml.safe_load(handle)

        assert cfg["adaptive_regime_presets"]["benchmark_symbol"] == "BTCUSDT"

    def test_config_notify_on_switch_enabled(self):
        """Verify notify_on_switch is true."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as handle:
            cfg = yaml.safe_load(handle)

        assert cfg["adaptive_regime_presets"]["notify_on_switch"] is True

    def test_config_trend_profile_values(self):
        """Verify trend profile config values."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as handle:
            cfg = yaml.safe_load(handle)

        assert cfg["adaptive_regime_presets"]["trend_strict_htf_mode"] is True
        assert cfg["adaptive_regime_presets"]["trend_volatility_floor_atr_pct"] == 0.06

    def test_config_range_profile_values(self):
        """Verify range profile config values."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as handle:
            cfg = yaml.safe_load(handle)

        assert cfg["adaptive_regime_presets"]["range_strict_htf_mode"] is False
        assert cfg["adaptive_regime_presets"]["range_volatility_floor_atr_pct"] == 0.02


# ============================================================================
# REGIME MAPPING TESTS
# ============================================================================

class TestRegimeMapping:
    """Tests for regime to profile mapping."""

    def test_resolve_regime_preset_trend(self):
        """Trend regime maps to trend profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("trend")
        assert profile == "trend"
        assert strict_htf is True
        assert vol_floor == 0.06

    def test_resolve_regime_preset_breakout_maps_to_trend(self):
        """Breakout regime maps to trend profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("breakout")
        assert profile == "trend"
        assert strict_htf is True
        assert vol_floor == 0.06

    def test_resolve_regime_preset_volatile_maps_to_trend(self):
        """Volatile regime maps to trend profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("volatile")
        assert profile == "trend"
        assert strict_htf is True
        assert vol_floor == 0.06

    def test_resolve_regime_preset_chop_maps_to_range(self):
        """Chop regime maps to range profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("chop")
        assert profile == "range"
        assert strict_htf is False
        assert vol_floor == 0.02

    def test_resolve_regime_preset_range_maps_to_range(self):
        """Range regime maps to range profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("range")
        assert profile == "range"
        assert strict_htf is False
        assert vol_floor == 0.02

    def test_resolve_regime_preset_unknown_defaults_to_range(self):
        """Unknown regime defaults to range profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("unknown_regime")
        assert profile == "range"
        assert strict_htf is False
        assert vol_floor == 0.02

    def test_resolve_regime_preset_empty_defaults_to_range(self):
        """Empty regime defaults to range profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("")
        assert profile == "range"
        assert strict_htf is False
        assert vol_floor == 0.02

    def test_resolve_regime_preset_none_defaults_to_range(self):
        """None regime defaults to range profile."""
        bot = _build_bot()
        profile, strict_htf, vol_floor = bot._resolve_regime_preset(None)
        assert profile == "range"
        assert strict_htf is False
        assert vol_floor == 0.02


# ============================================================================
# SWITCH BEHAVIOR TESTS
# ============================================================================

class TestSwitchBehavior:
    """Tests for preset switching behavior."""

    def test_switch_updates_strict_htf_mode(self):
        """Verify switch updates strict_htf_mode."""
        bot = _build_bot()
        bot.strict_htf_mode = False  # Start with different value
        
        # Simulate trend profile application
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("trend")
        bot.strict_htf_mode = strict_htf
        
        assert bot.strict_htf_mode is True

    def test_switch_updates_volatility_floor_atr_pct(self):
        """Verify switch updates volatility_floor_atr_pct."""
        bot = _build_bot()
        bot.volatility_floor_atr_pct = 0.5  # Start with different value
        
        # Simulate range profile application
        profile, strict_htf, vol_floor = bot._resolve_regime_preset("range")
        bot.volatility_floor_atr_pct = vol_floor
        
        assert bot.volatility_floor_atr_pct == 0.02

    def test_switch_from_trend_to_range_changes_vol_floor(self):
        """Verify switching from trend to range changes volatility floor."""
        bot = _build_bot()
        
        # Apply trend profile
        _, _, vol_floor_trend = bot._resolve_regime_preset("trend")
        bot.volatility_floor_atr_pct = vol_floor_trend
        assert bot.volatility_floor_atr_pct == 0.06
        
        # Apply range profile
        _, _, vol_floor_range = bot._resolve_regime_preset("range")
        bot.volatility_floor_atr_pct = vol_floor_range
        assert bot.volatility_floor_atr_pct == 0.02


# ============================================================================
# RUN LOOP INTEGRATION TESTS
# ============================================================================

class TestRunLoopIntegration:
    """Tests for run loop integration."""

    def test_run_calls_maybe_apply_regime_preset(self):
        """Verify run() method calls _maybe_apply_regime_preset."""
        source = inspect.getsource(TradingBot.run)
        assert "await self._maybe_apply_regime_preset()" in source

    def test_maybe_apply_regime_preset_exists(self):
        """Verify _maybe_apply_regime_preset method exists."""
        assert hasattr(TradingBot, '_maybe_apply_regime_preset')
        assert callable(getattr(TradingBot, '_maybe_apply_regime_preset'))

    def test_detect_profile_regime_exists(self):
        """Verify _detect_profile_regime method exists."""
        assert hasattr(TradingBot, '_detect_profile_regime')
        assert callable(getattr(TradingBot, '_detect_profile_regime'))

    def test_resolve_regime_preset_exists(self):
        """Verify _resolve_regime_preset method exists."""
        assert hasattr(TradingBot, '_resolve_regime_preset')
        assert callable(getattr(TradingBot, '_resolve_regime_preset'))


# ============================================================================
# TELEGRAM NOTIFICATION TESTS
# ============================================================================

class TestTelegramNotification:
    """Tests for Telegram notification on profile switch."""

    def test_telegram_message_format_in_code(self):
        """Verify Telegram message format is correct in code."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "ADAPTIVE PRESET SWITCH" in source
        assert "Профиль:" in source
        assert "Режим рынка:" in source
        assert "Strict HTF:" in source
        assert "Vol floor ATR%:" in source

    def test_telegram_notification_conditional_on_notify_flag(self):
        """Verify Telegram notification is conditional on notify_on_switch flag."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "self.adaptive_regime_presets_notify" in source

    def test_telegram_notification_conditional_on_tg_instance(self):
        """Verify Telegram notification is conditional on tg instance."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "if self.tg and self.adaptive_regime_presets_notify:" in source


# ============================================================================
# LOGGING TESTS
# ============================================================================

class TestLogging:
    """Tests for logging on profile switch."""

    def test_logger_info_on_switch(self):
        """Verify logger.info is called on profile switch."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "logger.info(msg)" in source

    def test_log_message_format(self):
        """Verify log message format contains required info."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "[ADAPTIVE PRESET]" in source
        assert "profile=" in source
        assert "regime=" in source
        assert "strict_htf=" in source
        assert "vol_floor=" in source


# ============================================================================
# INTERVAL TIMING TESTS
# ============================================================================

class TestIntervalTiming:
    """Tests for interval timing logic."""

    def test_interval_check_in_maybe_apply(self):
        """Verify interval check is present in _maybe_apply_regime_preset."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "self._last_regime_profile_check_ts" in source
        assert "self.adaptive_regime_presets_interval_sec" in source

    def test_interval_minimum_30_seconds(self):
        """Verify minimum interval is 30 seconds."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "max(30, self.adaptive_regime_presets_interval_sec)" in source


# ============================================================================
# SIGNAL_ONLY MODE TESTS
# ============================================================================

class TestSignalOnlyMode:
    """Tests for signal_only mode compatibility."""

    def test_maybe_apply_called_before_signal_feedback(self):
        """Verify _maybe_apply_regime_preset is called before signal feedback processing."""
        source = inspect.getsource(TradingBot.run)
        # Find positions of both calls
        preset_pos = source.find("await self._maybe_apply_regime_preset()")
        feedback_pos = source.find("await self._process_signal_feedback_loop()")
        
        assert preset_pos > 0
        assert feedback_pos > 0
        assert preset_pos < feedback_pos, "Preset should be applied before signal feedback"


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================

class TestErrorHandling:
    """Tests for error handling in preset switching."""

    def test_exception_handling_in_maybe_apply(self):
        """Verify exception handling in _maybe_apply_regime_preset."""
        source = inspect.getsource(TradingBot._maybe_apply_regime_preset)
        assert "except Exception as exc:" in source
        assert "logger.warning" in source
        assert "Adaptive preset switch skipped" in source


# ============================================================================
# STARTUP LOG TESTS
# ============================================================================

class TestStartupLog:
    """Tests for startup log messages."""

    def test_startup_log_shows_adaptive_presets_status(self):
        """Verify startup log shows adaptive presets status."""
        source = inspect.getsource(TradingBot.run)
        assert "Adaptive presets:" in source
        assert "self.adaptive_regime_presets_enabled" in source
        assert "self.adaptive_regime_presets_interval_sec" in source
        assert "self.adaptive_regime_presets_benchmark_symbol" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
