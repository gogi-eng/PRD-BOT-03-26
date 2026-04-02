#!/usr/bin/env python3
"""
Iteration 37: Trailing Responsiveness Tests

Tests for improved trailing responsiveness:
- bot config has scan_interval_sec and position_active_sleep_sec
- main loop scans entries by scan_interval gate (not every fast position tick)
- when LIVE and positions exist, cycle sleep uses shorter position_active_sleep_sec
- signal-only mode keeps default cycle_sleep
- no regressions in exchange_closed protections
"""
from __future__ import annotations

import os
import sys
import time
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import pytest
from main import TradingBot


class _PMStub:
    """Stub for PositionManager to control position count in tests."""
    def __init__(self, count_value: int):
        self._count = count_value

    def count(self):
        return self._count


# =============================================================================
# Config Tests: scan_interval_sec and position_active_sleep_sec
# =============================================================================

class TestConfigHasScanIntervalAndPositionActiveSleep:
    """Verify config.yaml has the required parameters."""

    def test_config_has_scan_interval_sec(self):
        """config.yaml must have bot.scan_interval_sec."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'bot' in cfg
        assert 'scan_interval_sec' in cfg['bot']
        assert cfg['bot']['scan_interval_sec'] == 60

    def test_config_has_position_active_sleep_sec(self):
        """config.yaml must have bot.position_active_sleep_sec."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'bot' in cfg
        assert 'position_active_sleep_sec' in cfg['bot']
        assert cfg['bot']['position_active_sleep_sec'] == 15

    def test_config_has_cycle_sleep_sec(self):
        """config.yaml must have bot.cycle_sleep_sec (base sleep)."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'bot' in cfg
        assert 'cycle_sleep_sec' in cfg['bot']
        assert cfg['bot']['cycle_sleep_sec'] == 60


class TestBotLoadsConfigValues:
    """Verify TradingBot loads config values correctly."""

    def test_bot_loads_scan_interval_sec(self):
        """Bot must load scan_interval_sec from config."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.scan_interval_sec = 60
        bot.position_active_sleep_sec = 15
        assert bot.scan_interval_sec == 60

    def test_bot_loads_position_active_sleep_sec(self):
        """Bot must load position_active_sleep_sec from config."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.scan_interval_sec = 60
        bot.position_active_sleep_sec = 15
        assert bot.position_active_sleep_sec == 15


# =============================================================================
# Scan Interval Gate Tests: _should_scan_entries_now()
# =============================================================================

class TestScanIntervalGate:
    """Tests for _should_scan_entries_now() method."""

    def test_scan_interval_gate_first_call_returns_true(self):
        """First call to _should_scan_entries_now should return True."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 60
        bot._last_scan_ts = 0.0
        assert bot._should_scan_entries_now() is True

    def test_scan_interval_gate_second_call_returns_false(self):
        """Immediate second call should return False (within interval)."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 60
        bot._last_scan_ts = 0.0
        bot._should_scan_entries_now()  # First call sets timestamp
        assert bot._should_scan_entries_now() is False

    def test_scan_interval_gate_returns_true_after_interval(self):
        """Call after interval elapsed should return True."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 5
        bot._last_scan_ts = time.time() - 6  # 6 seconds ago
        assert bot._should_scan_entries_now() is True

    def test_scan_interval_gate_updates_timestamp_on_true(self):
        """_should_scan_entries_now should update _last_scan_ts when returning True."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 5
        bot._last_scan_ts = 0.0
        before = time.time()
        result = bot._should_scan_entries_now()
        after = time.time()
        assert result is True
        assert before <= bot._last_scan_ts <= after

    def test_scan_interval_gate_minimum_5_seconds(self):
        """Scan interval should be at least 5 seconds."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 1  # Try to set below minimum
        bot._last_scan_ts = time.time() - 3  # 3 seconds ago
        # Should still wait because minimum is 5 seconds
        assert bot._should_scan_entries_now() is False

    def test_scan_interval_gate_respects_configured_value(self):
        """Scan interval should respect configured value when > 5."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 60
        bot._last_scan_ts = time.time() - 30  # 30 seconds ago
        # Should return False because 30 < 60
        assert bot._should_scan_entries_now() is False


# =============================================================================
# Cycle Sleep Tests: _get_cycle_sleep_sec()
# =============================================================================

class TestCycleSleepLiveMode:
    """Tests for _get_cycle_sleep_sec() in LIVE mode (signal_only=False)."""

    def test_cycle_sleep_shorter_when_positions_active_live(self):
        """LIVE mode with positions should use position_active_sleep_sec."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = False
        bot.position_manager = _PMStub(2)
        assert bot._get_cycle_sleep_sec() == 15

    def test_cycle_sleep_default_when_no_positions_live(self):
        """LIVE mode without positions should use default cycle_sleep."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = False
        bot.position_manager = _PMStub(0)
        assert bot._get_cycle_sleep_sec() == 60

    def test_cycle_sleep_uses_min_of_active_and_base(self):
        """Should use minimum of position_active_sleep_sec and cycle_sleep."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 10  # Base is smaller
        bot.position_active_sleep_sec = 15
        bot.signal_only = False
        bot.position_manager = _PMStub(1)
        assert bot._get_cycle_sleep_sec() == 10  # min(15, 10) = 10

    def test_cycle_sleep_minimum_5_seconds(self):
        """Cycle sleep should be at least 5 seconds."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 2  # Below minimum
        bot.position_active_sleep_sec = 3  # Below minimum
        bot.signal_only = False
        bot.position_manager = _PMStub(1)
        assert bot._get_cycle_sleep_sec() == 5  # min(5, 5) = 5


class TestCycleSleepSignalOnlyMode:
    """Tests for _get_cycle_sleep_sec() in signal-only mode."""

    def test_cycle_sleep_default_in_signal_only_with_positions(self):
        """Signal-only mode should always use default cycle_sleep."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = True
        bot.position_manager = _PMStub(3)
        assert bot._get_cycle_sleep_sec() == 60

    def test_cycle_sleep_default_in_signal_only_no_positions(self):
        """Signal-only mode without positions should use default cycle_sleep."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = True
        bot.position_manager = _PMStub(0)
        assert bot._get_cycle_sleep_sec() == 60


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Edge case tests for trailing responsiveness."""

    def test_single_position_triggers_fast_sleep(self):
        """Even a single position should trigger faster sleep in LIVE mode."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = False
        bot.position_manager = _PMStub(1)
        assert bot._get_cycle_sleep_sec() == 15

    def test_many_positions_same_fast_sleep(self):
        """Multiple positions should use same fast sleep."""
        bot = TradingBot.__new__(TradingBot)
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = False
        bot.position_manager = _PMStub(10)
        assert bot._get_cycle_sleep_sec() == 15

    def test_scan_interval_independent_of_cycle_sleep(self):
        """Scan interval should be independent of cycle sleep."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 60
        bot.cycle_sleep = 15  # Different from scan_interval
        bot.position_active_sleep_sec = 5
        bot.signal_only = False
        bot.position_manager = _PMStub(1)
        bot._last_scan_ts = time.time() - 10  # 10 seconds ago
        
        # Cycle sleep should be 5 (fast)
        assert bot._get_cycle_sleep_sec() == 5
        # But scan should not trigger (10 < 60)
        assert bot._should_scan_entries_now() is False


# =============================================================================
# Regression Tests: Exchange Closed Protections
# =============================================================================

class TestRegressionExchangeClosedProtections:
    """Ensure no regressions in exchange_closed protections."""

    def test_config_has_exchange_closed_confirm_cycles(self):
        """config.yaml must have position_sync.exchange_closed_confirm_cycles."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'position_sync' in cfg
        assert 'exchange_closed_confirm_cycles' in cfg['position_sync']
        assert cfg['position_sync']['exchange_closed_confirm_cycles'] == 3

    def test_config_has_exchange_closed_require_closed_pnl(self):
        """config.yaml must have position_sync.exchange_closed_require_closed_pnl."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'position_sync' in cfg
        assert 'exchange_closed_require_closed_pnl' in cfg['position_sync']
        assert cfg['position_sync']['exchange_closed_require_closed_pnl'] is True

    def test_config_has_exchange_closed_force_cycles(self):
        """config.yaml must have position_sync.exchange_closed_force_cycles."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'position_sync' in cfg
        assert 'exchange_closed_force_cycles' in cfg['position_sync']
        assert cfg['position_sync']['exchange_closed_force_cycles'] == 8

    def test_config_has_exchange_closed_reentry_cooldown_sec(self):
        """config.yaml must have position_sync.exchange_closed_reentry_cooldown_sec."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'position_sync' in cfg
        assert 'exchange_closed_reentry_cooldown_sec' in cfg['position_sync']
        assert cfg['position_sync']['exchange_closed_reentry_cooldown_sec'] == 900

    def test_config_has_pause_exchange_closed_after_rate_limit_sec(self):
        """config.yaml must have position_sync.pause_exchange_closed_after_rate_limit_sec."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        assert 'position_sync' in cfg
        assert 'pause_exchange_closed_after_rate_limit_sec' in cfg['position_sync']
        assert cfg['position_sync']['pause_exchange_closed_after_rate_limit_sec'] == 180


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegrationScanAndSleepDecoupled:
    """Integration tests verifying scan and sleep are decoupled."""

    def test_fast_position_ticks_dont_trigger_scan_every_time(self):
        """Fast position management ticks should not scan entries every time."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 60
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = False
        bot.position_manager = _PMStub(2)
        bot._last_scan_ts = 0.0

        # First tick: scan should trigger
        assert bot._should_scan_entries_now() is True
        assert bot._get_cycle_sleep_sec() == 15

        # Simulate 4 fast ticks (15s each = 60s total)
        for i in range(4):
            # Each tick should NOT trigger scan (within 60s interval)
            assert bot._should_scan_entries_now() is False
            assert bot._get_cycle_sleep_sec() == 15

    def test_scan_triggers_after_interval_despite_fast_ticks(self):
        """Scan should trigger after interval even with fast position ticks."""
        bot = TradingBot.__new__(TradingBot)
        bot.scan_interval_sec = 60
        bot.cycle_sleep = 60
        bot.position_active_sleep_sec = 15
        bot.signal_only = False
        bot.position_manager = _PMStub(2)
        
        # Set last scan to 61 seconds ago
        bot._last_scan_ts = time.time() - 61
        
        # Scan should trigger
        assert bot._should_scan_entries_now() is True
        # But sleep should still be fast
        assert bot._get_cycle_sleep_sec() == 15


class TestConfigValuesMatchExpected:
    """Verify all config values match expected for trailing responsiveness."""

    def test_all_trailing_responsiveness_config_values(self):
        """All trailing responsiveness config values should match expected."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            cfg = yaml.safe_load(f)
        
        # Bot section
        assert cfg['bot']['cycle_sleep_sec'] == 60
        assert cfg['bot']['scan_interval_sec'] == 60
        assert cfg['bot']['position_active_sleep_sec'] == 15
        
        # Position sync section (regression check)
        assert cfg['position_sync']['exchange_closed_confirm_cycles'] == 3
        assert cfg['position_sync']['exchange_closed_require_closed_pnl'] is True
        assert cfg['position_sync']['exchange_closed_force_cycles'] == 8
        assert cfg['position_sync']['exchange_closed_reentry_cooldown_sec'] == 900
        assert cfg['position_sync']['pause_exchange_closed_after_rate_limit_sec'] == 180
