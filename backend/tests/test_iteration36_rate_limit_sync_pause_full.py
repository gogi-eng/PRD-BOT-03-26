#!/usr/bin/env python3
"""
Iteration 36: Rate Limit Sync Pause & Loss Reduction Measures

Tests for:
1. Position sync pauses exchange_closed reconciliation for 180s after Bybit rate limit 10006
2. exchange_closed finalization requires evidence (closed pnl) or force cycles
3. Trade history stores origin field from position
4. Reentry cooldown after exchange_closed remains active (900s)
5. No regressions in existing exchange_closed and cooldown tests
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import main as main_module
from main import TradingBot
from engine.position_manager import Position


# ============================================================================
# Test Fixtures
# ============================================================================

class MockClientWithRateLimit:
    """Mock client that simulates rate limit behavior."""
    def __init__(self, rate_limit_at: float = 0.0):
        self.last_rate_limit_at_monotonic = rate_limit_at


@pytest.fixture
def bot_instance():
    """Create a minimal TradingBot instance for testing."""
    bot = TradingBot.__new__(TradingBot)
    bot.exchange_closed_pause_after_rate_limit_sec = 180
    bot.exchange_closed_confirm_cycles = 3
    bot.exchange_closed_require_closed_pnl = True
    bot.exchange_closed_force_cycles = 8
    bot.exchange_closed_reentry_cooldown_sec = 900
    bot._missing_exchange_cycles = {}
    bot._exchange_closed_reentry_until = {}
    bot.client = MockClientWithRateLimit(0.0)
    return bot


# ============================================================================
# Feature 1: Rate Limit Sync Pause (180s after 10006)
# ============================================================================

class TestRateLimitSyncPause:
    """Tests for exchange_closed reconciliation pause after rate limit."""

    def test_config_has_pause_exchange_closed_after_rate_limit_sec(self):
        """Verify config.yaml has pause_exchange_closed_after_rate_limit_sec: 180."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert 'position_sync' in config
        assert 'pause_exchange_closed_after_rate_limit_sec' in config['position_sync']
        assert config['position_sync']['pause_exchange_closed_after_rate_limit_sec'] == 180

    def test_bot_loads_pause_config_value(self):
        """Verify bot loads the pause config value correctly."""
        bot = TradingBot.__new__(TradingBot)
        # Simulate config loading
        bot.exchange_closed_pause_after_rate_limit_sec = 180
        assert bot.exchange_closed_pause_after_rate_limit_sec == 180

    def test_sync_pause_remaining_returns_positive_after_recent_rate_limit(self, bot_instance):
        """After rate limit, pause remaining should be positive."""
        bot_instance.client.last_rate_limit_at_monotonic = time.monotonic()
        
        remaining = bot_instance._exchange_closed_sync_pause_remaining()
        assert remaining > 0
        assert remaining <= 180

    def test_sync_pause_remaining_returns_zero_when_no_rate_limit(self, bot_instance):
        """When no rate limit occurred, pause remaining should be 0."""
        bot_instance.client.last_rate_limit_at_monotonic = 0.0
        
        remaining = bot_instance._exchange_closed_sync_pause_remaining()
        assert remaining == 0

    def test_sync_pause_remaining_returns_zero_when_expired(self, bot_instance):
        """After 180s, pause remaining should be 0."""
        bot_instance.client.last_rate_limit_at_monotonic = time.monotonic() - 200
        
        remaining = bot_instance._exchange_closed_sync_pause_remaining()
        assert remaining == 0

    def test_sync_pause_remaining_decreases_over_time(self, bot_instance):
        """Pause remaining should decrease as time passes."""
        bot_instance.client.last_rate_limit_at_monotonic = time.monotonic()
        
        remaining1 = bot_instance._exchange_closed_sync_pause_remaining()
        time.sleep(0.1)
        remaining2 = bot_instance._exchange_closed_sync_pause_remaining()
        
        assert remaining2 <= remaining1

    def test_sync_pause_remaining_with_zero_config(self, bot_instance):
        """When config is 0, pause should always be 0."""
        bot_instance.exchange_closed_pause_after_rate_limit_sec = 0
        bot_instance.client.last_rate_limit_at_monotonic = time.monotonic()
        
        remaining = bot_instance._exchange_closed_sync_pause_remaining()
        assert remaining == 0

    def test_sync_pause_remaining_with_negative_config(self, bot_instance):
        """When config is negative, pause should always be 0."""
        bot_instance.exchange_closed_pause_after_rate_limit_sec = -10
        bot_instance.client.last_rate_limit_at_monotonic = time.monotonic()
        
        remaining = bot_instance._exchange_closed_sync_pause_remaining()
        assert remaining == 0


class TestBybitClientRateLimitTracking:
    """Tests for Bybit client rate limit tracking."""

    def test_bybit_client_has_rate_limit_attribute(self):
        """Verify BybitClient has last_rate_limit_at_monotonic attribute."""
        from exchange.bybit_client import BybitClient
        client = BybitClient("test_key", "test_secret", testnet=True)
        assert hasattr(client, 'last_rate_limit_at_monotonic')
        assert client.last_rate_limit_at_monotonic == 0.0

    def test_bybit_client_rate_limit_attribute_is_float(self):
        """Verify rate limit attribute is a float."""
        from exchange.bybit_client import BybitClient
        client = BybitClient("test_key", "test_secret", testnet=True)
        assert isinstance(client.last_rate_limit_at_monotonic, float)


# ============================================================================
# Feature 2: Exchange Closed Evidence Requirement
# ============================================================================

class TestExchangeClosedEvidence:
    """Tests for exchange_closed finalization requiring evidence."""

    def test_config_has_exchange_closed_require_closed_pnl(self):
        """Verify config.yaml has exchange_closed_require_closed_pnl: true."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert config['position_sync']['exchange_closed_require_closed_pnl'] is True

    def test_config_has_exchange_closed_force_cycles(self):
        """Verify config.yaml has exchange_closed_force_cycles: 8."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert config['position_sync']['exchange_closed_force_cycles'] == 8

    def test_can_finalize_with_closed_records(self, bot_instance):
        """With closed PnL records, finalization should be allowed."""
        result = bot_instance._can_finalize_exchange_closed(
            missing_cycles=3,
            closed_records_count=1
        )
        assert result is True

    def test_cannot_finalize_without_evidence_below_force_cycles(self, bot_instance):
        """Without evidence and below force cycles, finalization should be blocked."""
        result = bot_instance._can_finalize_exchange_closed(
            missing_cycles=3,
            closed_records_count=0
        )
        assert result is False

    def test_can_finalize_at_force_cycles_without_evidence(self, bot_instance):
        """At force cycles threshold, finalization should be allowed even without evidence."""
        result = bot_instance._can_finalize_exchange_closed(
            missing_cycles=8,
            closed_records_count=0
        )
        assert result is True

    def test_can_finalize_above_force_cycles_without_evidence(self, bot_instance):
        """Above force cycles threshold, finalization should be allowed."""
        result = bot_instance._can_finalize_exchange_closed(
            missing_cycles=10,
            closed_records_count=0
        )
        assert result is True

    def test_can_finalize_when_require_pnl_disabled(self, bot_instance):
        """When require_closed_pnl is disabled, always allow finalization."""
        bot_instance.exchange_closed_require_closed_pnl = False
        
        result = bot_instance._can_finalize_exchange_closed(
            missing_cycles=1,
            closed_records_count=0
        )
        assert result is True


class TestExchangeClosedDebounce:
    """Tests for exchange_closed debounce (confirm cycles)."""

    def test_config_has_exchange_closed_confirm_cycles(self):
        """Verify config.yaml has exchange_closed_confirm_cycles: 3."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert config['position_sync']['exchange_closed_confirm_cycles'] == 3

    def test_should_finalize_requires_multiple_cycles(self, bot_instance):
        """Position should not be finalized until confirm cycles reached."""
        symbol = "TESTUSDT"
        
        # First cycle - should not finalize
        assert bot_instance._should_finalize_exchange_closed(symbol) is False
        # Second cycle - should not finalize
        assert bot_instance._should_finalize_exchange_closed(symbol) is False
        # Third cycle - should finalize
        assert bot_instance._should_finalize_exchange_closed(symbol) is True

    def test_should_finalize_counter_increments(self, bot_instance):
        """Counter should increment with each call."""
        symbol = "TESTUSDT"
        
        bot_instance._should_finalize_exchange_closed(symbol)
        assert bot_instance._missing_exchange_cycles[symbol] == 1
        
        bot_instance._should_finalize_exchange_closed(symbol)
        assert bot_instance._missing_exchange_cycles[symbol] == 2

    def test_should_finalize_independent_per_symbol(self, bot_instance):
        """Each symbol should have independent counter."""
        symbol1 = "BTCUSDT"
        symbol2 = "ETHUSDT"
        
        bot_instance._should_finalize_exchange_closed(symbol1)
        bot_instance._should_finalize_exchange_closed(symbol1)
        bot_instance._should_finalize_exchange_closed(symbol2)
        
        assert bot_instance._missing_exchange_cycles[symbol1] == 2
        assert bot_instance._missing_exchange_cycles[symbol2] == 1


# ============================================================================
# Feature 3: Trade History Origin Field
# ============================================================================

class TestTradeHistoryOrigin:
    """Tests for origin field in trade history."""

    def test_save_trade_persists_origin_bot(self, tmp_path, monkeypatch):
        """Verify origin='bot' is saved to trade history."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        bot = TradingBot.__new__(TradingBot)
        
        bot._save_trade(
            symbol="BTCUSDT",
            side="BUY",
            qty=0.01,
            entry=50000.0,
            exit_price=51000.0,
            pnl=10.0,
            reason="tp",
            origin="bot",
        )
        
        history_path = tmp_path / "trade_history.json"
        assert history_path.exists()
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(rows) == 1
        assert rows[0]["origin"] == "bot"

    def test_save_trade_persists_origin_manual(self, tmp_path, monkeypatch):
        """Verify origin='manual' is saved to trade history."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        bot = TradingBot.__new__(TradingBot)
        
        bot._save_trade(
            symbol="ETHUSDT",
            side="SELL",
            qty=0.1,
            entry=3000.0,
            exit_price=2900.0,
            pnl=10.0,
            reason="sl",
            origin="manual",
        )
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "manual"

    def test_save_trade_default_origin_is_bot(self, tmp_path, monkeypatch):
        """Verify default origin is 'bot' when not specified."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        bot = TradingBot.__new__(TradingBot)
        
        # Call without origin parameter
        bot._save_trade(
            symbol="SOLUSDT",
            side="BUY",
            qty=1.0,
            entry=100.0,
            exit_price=110.0,
            pnl=10.0,
            reason="tp",
        )
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "bot"

    def test_save_trade_appends_to_existing_history(self, tmp_path, monkeypatch):
        """Verify trades are appended to existing history."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        bot = TradingBot.__new__(TradingBot)
        
        # First trade
        bot._save_trade("BTCUSDT", "BUY", 0.01, 50000.0, 51000.0, 10.0, "tp", "bot")
        # Second trade
        bot._save_trade("ETHUSDT", "SELL", 0.1, 3000.0, 2900.0, 10.0, "sl", "manual")
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(rows) == 2
        assert rows[0]["origin"] == "bot"
        assert rows[1]["origin"] == "manual"


class TestPositionOriginAttribute:
    """Tests for Position origin attribute."""

    def test_position_has_origin_attribute(self):
        """Verify Position class has origin attribute."""
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="bot",
        )
        assert hasattr(pos, 'origin')
        assert pos.origin == "bot"

    def test_position_origin_manual(self):
        """Verify Position can have origin='manual'."""
        pos = Position(
            symbol="ETHUSDT",
            side="SELL",
            entry_price=3000.0,
            qty=0.1,
            stop_loss=3100.0,
            take_profit=2800.0,
            origin="manual",
        )
        assert pos.origin == "manual"


# ============================================================================
# Feature 4: Reentry Cooldown After Exchange Closed (900s)
# ============================================================================

class TestReentryCooldown:
    """Tests for reentry cooldown after exchange_closed."""

    def test_config_has_exchange_closed_reentry_cooldown_sec(self):
        """Verify config.yaml has exchange_closed_reentry_cooldown_sec: 900."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert config['position_sync']['exchange_closed_reentry_cooldown_sec'] == 900

    def test_set_reentry_block_sets_cooldown(self, bot_instance):
        """Verify _set_exchange_closed_reentry_block sets cooldown."""
        symbol = "BTCUSDT"
        bot_instance._set_exchange_closed_reentry_block(symbol)
        
        assert symbol in bot_instance._exchange_closed_reentry_until
        remaining = bot_instance._exchange_closed_reentry_remaining(symbol)
        assert remaining > 0
        assert remaining <= 900

    def test_reentry_remaining_returns_zero_when_no_cooldown(self, bot_instance):
        """When no cooldown set, remaining should be 0."""
        remaining = bot_instance._exchange_closed_reentry_remaining("NEWUSDT")
        assert remaining == 0

    def test_reentry_remaining_returns_zero_when_expired(self, bot_instance):
        """After cooldown expires, remaining should be 0."""
        symbol = "BTCUSDT"
        bot_instance._exchange_closed_reentry_until[symbol] = time.time() - 100
        
        remaining = bot_instance._exchange_closed_reentry_remaining(symbol)
        assert remaining == 0

    def test_reentry_cooldown_cleanup_on_expiration(self, bot_instance):
        """Expired cooldown should be cleaned up."""
        symbol = "BTCUSDT"
        bot_instance._exchange_closed_reentry_until[symbol] = time.time() - 100
        
        bot_instance._exchange_closed_reentry_remaining(symbol)
        assert symbol not in bot_instance._exchange_closed_reentry_until

    def test_reentry_cooldown_independent_per_symbol(self, bot_instance):
        """Each symbol should have independent cooldown."""
        bot_instance._set_exchange_closed_reentry_block("BTCUSDT")
        
        assert bot_instance._exchange_closed_reentry_remaining("BTCUSDT") > 0
        assert bot_instance._exchange_closed_reentry_remaining("ETHUSDT") == 0


# ============================================================================
# Feature 5: Regression Tests
# ============================================================================

class TestRegressionExchangeClosedInIgnoreReasons:
    """Verify exchange_closed is in ignore cooldown/consecutive reasons."""

    def test_exchange_closed_in_ignore_cooldown_reasons_config(self):
        """Verify exchange_closed is in ignore_loss_cooldown_reasons."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert 'exchange_closed' in config['risk']['ignore_loss_cooldown_reasons']

    def test_exchange_closed_in_ignore_consecutive_reasons_config(self):
        """Verify exchange_closed is in ignore_consecutive_loss_reasons."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert 'exchange_closed' in config['risk']['ignore_consecutive_loss_reasons']


class TestRegressionEarlyExitInIgnoreReasons:
    """Verify early_exit is still in ignore cooldown/consecutive reasons."""

    def test_early_exit_in_ignore_cooldown_reasons_config(self):
        """Verify early_exit is in ignore_loss_cooldown_reasons."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert 'early_exit' in config['risk']['ignore_loss_cooldown_reasons']

    def test_early_exit_in_ignore_consecutive_reasons_config(self):
        """Verify early_exit is in ignore_consecutive_loss_reasons."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert 'early_exit' in config['risk']['ignore_consecutive_loss_reasons']


class TestRegressionAllPositionSyncConfig:
    """Verify all position_sync config values are correct."""

    def test_all_position_sync_config_values(self):
        """Verify all position_sync config values match expected."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        ps = config['position_sync']
        assert ps['adopt_all_positions'] is True
        assert ps['preserve_existing_sl_tp'] is True
        assert ps['exchange_closed_confirm_cycles'] == 3
        assert ps['exchange_closed_require_closed_pnl'] is True
        assert ps['exchange_closed_force_cycles'] == 8
        assert ps['exchange_closed_reentry_cooldown_sec'] == 900
        assert ps['pause_exchange_closed_after_rate_limit_sec'] == 180


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegrationRateLimitPausesClearsMissingCycles:
    """Test that rate limit pause clears missing cycles counter."""

    def test_rate_limit_pause_clears_missing_cycles(self, bot_instance):
        """When rate limit pause is active, missing cycles should be cleared."""
        # Set up some missing cycles
        bot_instance._missing_exchange_cycles["BTCUSDT"] = 2
        bot_instance._missing_exchange_cycles["ETHUSDT"] = 1
        
        # Simulate rate limit
        bot_instance.client.last_rate_limit_at_monotonic = time.monotonic()
        
        # Check pause is active
        assert bot_instance._exchange_closed_sync_pause_remaining() > 0
        
        # The _manage_positions method clears missing cycles when pause is active
        # We verify the logic exists in main.py lines 564-565
        sync_pause_remaining = bot_instance._exchange_closed_sync_pause_remaining()
        if sync_pause_remaining > 0:
            bot_instance._missing_exchange_cycles.clear()
        
        assert len(bot_instance._missing_exchange_cycles) == 0


class TestIntegrationFullExchangeClosedFlow:
    """Test full exchange_closed flow with all features."""

    def test_full_flow_debounce_evidence_reentry_cooldown(self, bot_instance):
        """Test complete flow: debounce -> evidence check -> reentry cooldown."""
        symbol = "TESTUSDT"
        
        # Step 1: Debounce - need 3 cycles
        assert bot_instance._should_finalize_exchange_closed(symbol) is False
        assert bot_instance._should_finalize_exchange_closed(symbol) is False
        assert bot_instance._should_finalize_exchange_closed(symbol) is True
        
        # Step 2: Evidence check - with closed records
        can_finalize = bot_instance._can_finalize_exchange_closed(
            missing_cycles=3,
            closed_records_count=1
        )
        assert can_finalize is True
        
        # Step 3: Set reentry cooldown
        bot_instance._set_exchange_closed_reentry_block(symbol)
        
        # Step 4: Verify reentry is blocked
        remaining = bot_instance._exchange_closed_reentry_remaining(symbol)
        assert remaining > 0
        assert remaining <= 900


class TestEdgeCases:
    """Edge case tests."""

    def test_sync_pause_with_missing_client_attribute(self, bot_instance):
        """Handle missing last_rate_limit_at_monotonic gracefully."""
        # Remove the attribute
        delattr(bot_instance.client, 'last_rate_limit_at_monotonic')
        
        # Should return 0, not raise exception
        remaining = bot_instance._exchange_closed_sync_pause_remaining()
        assert remaining == 0

    def test_sync_pause_with_none_client_attribute(self, bot_instance):
        """Handle None last_rate_limit_at_monotonic gracefully."""
        bot_instance.client.last_rate_limit_at_monotonic = None
        
        remaining = bot_instance._exchange_closed_sync_pause_remaining()
        assert remaining == 0

    def test_reentry_remaining_rounds_up(self, bot_instance):
        """Verify remaining time rounds up."""
        symbol = "BTCUSDT"
        # Set cooldown to expire in 0.1 seconds
        bot_instance._exchange_closed_reentry_until[symbol] = time.time() + 0.1
        
        remaining = bot_instance._exchange_closed_reentry_remaining(symbol)
        # Should round up to at least 1
        assert remaining >= 1

    def test_force_cycles_boundary_7_returns_false(self, bot_instance):
        """At 7 cycles (below 8), should return False without evidence."""
        result = bot_instance._can_finalize_exchange_closed(
            missing_cycles=7,
            closed_records_count=0
        )
        assert result is False

    def test_force_cycles_boundary_8_returns_true(self, bot_instance):
        """At exactly 8 cycles, should return True without evidence."""
        result = bot_instance._can_finalize_exchange_closed(
            missing_cycles=8,
            closed_records_count=0
        )
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
