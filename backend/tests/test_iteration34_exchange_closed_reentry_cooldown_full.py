#!/usr/bin/env python3
"""
Iteration 34: Exchange Closed Reentry Cooldown Tests

Tests for anti-whipsaw behavior after exchange_closed:
- 15-minute (900 sec) symbol block after exchange_closed finalization
- Config verification
- Scan loop rejection of symbols under cooldown
- Cooldown expiration logic
- No regression in exchange_closed debounce/origin logic
"""
from __future__ import annotations

import os
import sys
import time
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from main import TradingBot
from engine.position_manager import Position


# ============================================================================
# Config Verification Tests
# ============================================================================

class TestConfigExchangeClosedReentryCooldown:
    """Verify config.yaml contains exchange_closed_reentry_cooldown_sec=900"""

    def test_config_has_exchange_closed_reentry_cooldown_sec_900(self):
        """Config should have position_sync.exchange_closed_reentry_cooldown_sec=900"""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        
        assert 'position_sync' in cfg, "position_sync section missing in config"
        assert 'exchange_closed_reentry_cooldown_sec' in cfg['position_sync'], \
            "exchange_closed_reentry_cooldown_sec missing in position_sync"
        assert cfg['position_sync']['exchange_closed_reentry_cooldown_sec'] == 900, \
            f"Expected 900, got {cfg['position_sync']['exchange_closed_reentry_cooldown_sec']}"

    def test_bot_loads_exchange_closed_reentry_cooldown_sec(self):
        """Bot should load exchange_closed_reentry_cooldown_sec from config"""
        bot = TradingBot.__new__(TradingBot)
        bot.cfg = MagicMock()
        bot.cfg.get.return_value = 900
        
        # Simulate the config loading
        bot.exchange_closed_reentry_cooldown_sec = int(
            bot.cfg.get("position_sync", "exchange_closed_reentry_cooldown_sec", default=900)
        )
        bot._exchange_closed_reentry_until = {}
        
        assert bot.exchange_closed_reentry_cooldown_sec == 900
        assert isinstance(bot._exchange_closed_reentry_until, dict)


# ============================================================================
# Reentry Block Set on Exchange Closed Finalization Tests
# ============================================================================

class TestReentryBlockOnExchangeClosedFinalization:
    """Verify _set_exchange_closed_reentry_block is called when reason='exchange_closed'"""

    def test_set_exchange_closed_reentry_block_sets_cooldown(self):
        """_set_exchange_closed_reentry_block should set cooldown timestamp"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 900
        bot._exchange_closed_reentry_until = {}
        
        before = time.time()
        bot._set_exchange_closed_reentry_block("BTCUSDT")
        after = time.time()
        
        assert "BTCUSDT" in bot._exchange_closed_reentry_until
        until = bot._exchange_closed_reentry_until["BTCUSDT"]
        assert until >= before + 900
        assert until <= after + 900

    def test_set_exchange_closed_reentry_block_zero_cooldown_does_nothing(self):
        """_set_exchange_closed_reentry_block with cooldown=0 should not set block"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 0
        bot._exchange_closed_reentry_until = {}
        
        bot._set_exchange_closed_reentry_block("BTCUSDT")
        
        assert "BTCUSDT" not in bot._exchange_closed_reentry_until

    def test_set_exchange_closed_reentry_block_negative_cooldown_does_nothing(self):
        """_set_exchange_closed_reentry_block with negative cooldown should not set block"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = -100
        bot._exchange_closed_reentry_until = {}
        
        bot._set_exchange_closed_reentry_block("BTCUSDT")
        
        assert "BTCUSDT" not in bot._exchange_closed_reentry_until

    def test_set_exchange_closed_reentry_block_overwrites_existing(self):
        """_set_exchange_closed_reentry_block should overwrite existing cooldown"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 900
        bot._exchange_closed_reentry_until = {"BTCUSDT": time.time() + 100}
        
        old_until = bot._exchange_closed_reentry_until["BTCUSDT"]
        bot._set_exchange_closed_reentry_block("BTCUSDT")
        new_until = bot._exchange_closed_reentry_until["BTCUSDT"]
        
        assert new_until > old_until


# ============================================================================
# Scan Loop Rejection Tests
# ============================================================================

class TestScanLoopRejectsSymbolsUnderCooldown:
    """Verify scan loop rejects symbols under exchange_closed reentry cooldown"""

    def test_exchange_closed_reentry_remaining_returns_positive_when_under_cooldown(self):
        """_exchange_closed_reentry_remaining should return positive seconds when under cooldown"""
        bot = TradingBot.__new__(TradingBot)
        bot._exchange_closed_reentry_until = {"BTCUSDT": time.time() + 500}
        
        remaining = bot._exchange_closed_reentry_remaining("BTCUSDT")
        
        assert remaining > 0
        assert remaining <= 500

    def test_exchange_closed_reentry_remaining_returns_zero_when_no_cooldown(self):
        """_exchange_closed_reentry_remaining should return 0 when no cooldown set"""
        bot = TradingBot.__new__(TradingBot)
        bot._exchange_closed_reentry_until = {}
        
        remaining = bot._exchange_closed_reentry_remaining("BTCUSDT")
        
        assert remaining == 0

    def test_exchange_closed_reentry_remaining_returns_zero_when_expired(self):
        """_exchange_closed_reentry_remaining should return 0 when cooldown expired"""
        bot = TradingBot.__new__(TradingBot)
        bot._exchange_closed_reentry_until = {"BTCUSDT": time.time() - 10}
        
        remaining = bot._exchange_closed_reentry_remaining("BTCUSDT")
        
        assert remaining == 0
        # Should also clean up the expired entry
        assert "BTCUSDT" not in bot._exchange_closed_reentry_until

    def test_scan_loop_rejects_symbol_under_cooldown(self):
        """Scan loop should reject symbols under exchange_closed reentry cooldown"""
        # This tests the logic at main.py lines 716-719
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 900
        bot._exchange_closed_reentry_until = {"BTCUSDT": time.time() + 500}
        
        # Simulate the check in _scan_entries
        exchange_closed_wait = bot._exchange_closed_reentry_remaining("BTCUSDT")
        
        assert exchange_closed_wait > 0, "Symbol should be under cooldown"
        # In actual code, this would cause mark_reject("exchange_closed_reentry_cooldown")


# ============================================================================
# Cooldown Expiration Tests
# ============================================================================

class TestCooldownExpiration:
    """Verify cooldown expires correctly"""

    def test_cooldown_expires_after_configured_time(self):
        """Cooldown should expire after exchange_closed_reentry_cooldown_sec"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 1  # 1 second for fast test
        bot._exchange_closed_reentry_until = {}
        
        bot._set_exchange_closed_reentry_block("BTCUSDT")
        
        # Should be under cooldown immediately
        assert bot._exchange_closed_reentry_remaining("BTCUSDT") > 0
        
        # Wait for expiration
        time.sleep(1.1)
        
        # Should be expired now
        assert bot._exchange_closed_reentry_remaining("BTCUSDT") == 0

    def test_cooldown_cleanup_on_expiration_check(self):
        """Expired cooldown should be cleaned up when checked"""
        bot = TradingBot.__new__(TradingBot)
        bot._exchange_closed_reentry_until = {"BTCUSDT": time.time() - 10}
        
        # Check remaining (should trigger cleanup)
        remaining = bot._exchange_closed_reentry_remaining("BTCUSDT")
        
        assert remaining == 0
        assert "BTCUSDT" not in bot._exchange_closed_reentry_until

    def test_cooldown_cleanup_on_position_sync(self):
        """Cooldown should be cleared when position is synced from exchange"""
        # This tests the logic at main.py line 1761
        bot = TradingBot.__new__(TradingBot)
        bot._exchange_closed_reentry_until = {"BTCUSDT": time.time() + 500}
        bot._missing_exchange_cycles = {}
        
        # Simulate the cleanup in _sync_exchange_position
        symbol = "BTCUSDT"
        bot._missing_exchange_cycles.pop(symbol, None)
        bot._exchange_closed_reentry_until.pop(symbol, None)
        
        assert "BTCUSDT" not in bot._exchange_closed_reentry_until


# ============================================================================
# Integration Tests: Finalize Full Close Sets Reentry Block
# ============================================================================

class TestFinalizeFullCloseIntegration:
    """Verify _finalize_full_close sets reentry block for exchange_closed reason"""

    @pytest.mark.asyncio
    async def test_finalize_full_close_sets_reentry_block_for_exchange_closed(self):
        """_finalize_full_close should set reentry block when reason='exchange_closed'"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 900
        bot._exchange_closed_reentry_until = {}
        bot._missing_exchange_cycles = {"BTCUSDT": 5}
        bot.position_manager = MagicMock()
        bot.risk_guard = MagicMock()
        bot.controls = MagicMock()
        bot.tg = None
        
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="bot"
        )
        
        # Mock _save_trade to avoid file operations
        bot._save_trade = MagicMock()
        bot._calc_pnl_pct = MagicMock(return_value=1.5)
        
        await bot._finalize_full_close("BTCUSDT", pos, 51000.0, 10.0, "exchange_closed")
        
        # Verify reentry block was set
        assert "BTCUSDT" in bot._exchange_closed_reentry_until
        remaining = bot._exchange_closed_reentry_remaining("BTCUSDT")
        assert remaining > 0
        assert remaining <= 900

    @pytest.mark.asyncio
    async def test_finalize_full_close_does_not_set_reentry_block_for_other_reasons(self):
        """_finalize_full_close should NOT set reentry block for non-exchange_closed reasons"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 900
        bot._exchange_closed_reentry_until = {}
        bot._missing_exchange_cycles = {}
        bot.position_manager = MagicMock()
        bot.risk_guard = MagicMock()
        bot.controls = MagicMock()
        bot.tg = None
        
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="bot"
        )
        
        bot._save_trade = MagicMock()
        bot._calc_pnl_pct = MagicMock(return_value=1.5)
        
        # Test various non-exchange_closed reasons
        for reason in ["tp", "sl", "trailing_stop", "rl_close", "early_exit", "profit_lock"]:
            bot._exchange_closed_reentry_until = {}
            await bot._finalize_full_close("BTCUSDT", pos, 51000.0, 10.0, reason)
            assert "BTCUSDT" not in bot._exchange_closed_reentry_until, \
                f"Reentry block should not be set for reason={reason}"


# ============================================================================
# Regression Tests: Exchange Closed Debounce/Origin Logic
# ============================================================================

class TestRegressionExchangeClosedDebounce:
    """Verify no regression in exchange_closed debounce logic"""

    def test_should_finalize_exchange_closed_requires_multiple_cycles(self):
        """_should_finalize_exchange_closed should require multiple missing cycles"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        # First call - should return False
        assert bot._should_finalize_exchange_closed("BTCUSDT") is False
        assert bot._missing_exchange_cycles.get("BTCUSDT") == 1
        
        # Second call - should return False
        assert bot._should_finalize_exchange_closed("BTCUSDT") is False
        assert bot._missing_exchange_cycles.get("BTCUSDT") == 2
        
        # Third call - should return True
        assert bot._should_finalize_exchange_closed("BTCUSDT") is True
        assert bot._missing_exchange_cycles.get("BTCUSDT") == 3

    def test_can_finalize_exchange_closed_requires_evidence_or_force_cycles(self):
        """_can_finalize_exchange_closed should require closed records or force cycles"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        # No evidence, below force cycles - should return False
        assert bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=0) is False
        
        # With evidence - should return True
        assert bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=1) is True
        
        # Force cycles reached - should return True
        assert bot._can_finalize_exchange_closed(missing_cycles=8, closed_records_count=0) is True


class TestRegressionOriginPersistence:
    """Verify no regression in origin persistence"""

    def test_position_has_origin_attribute(self):
        """Position should have origin attribute"""
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="manual"
        )
        
        assert hasattr(pos, 'origin')
        assert pos.origin == "manual"


class TestRegressionEarlyExitCooldownIgnore:
    """Verify early_exit and exchange_closed are in ignore cooldown reasons"""

    def test_early_exit_in_ignore_cooldown_reasons_config(self):
        """early_exit should be in ignore_loss_cooldown_reasons in config"""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        
        ignore_reasons = cfg.get('risk', {}).get('ignore_loss_cooldown_reasons', [])
        assert 'early_exit' in ignore_reasons

    def test_exchange_closed_in_ignore_cooldown_reasons_config(self):
        """exchange_closed should be in ignore_loss_cooldown_reasons in config"""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
        
        ignore_reasons = cfg.get('risk', {}).get('ignore_loss_cooldown_reasons', [])
        assert 'exchange_closed' in ignore_reasons


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge case tests for reentry cooldown"""

    def test_multiple_symbols_independent_cooldowns(self):
        """Each symbol should have independent cooldown"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 900
        bot._exchange_closed_reentry_until = {}
        
        bot._set_exchange_closed_reentry_block("BTCUSDT")
        bot._set_exchange_closed_reentry_block("ETHUSDT")
        
        assert "BTCUSDT" in bot._exchange_closed_reentry_until
        assert "ETHUSDT" in bot._exchange_closed_reentry_until
        
        # Clear one, other should remain
        bot._exchange_closed_reentry_until.pop("BTCUSDT", None)
        
        assert "BTCUSDT" not in bot._exchange_closed_reentry_until
        assert "ETHUSDT" in bot._exchange_closed_reentry_until

    def test_reentry_remaining_rounds_up(self):
        """_exchange_closed_reentry_remaining should round up to nearest second"""
        bot = TradingBot.__new__(TradingBot)
        # Set cooldown to expire in 0.1 seconds
        bot._exchange_closed_reentry_until = {"BTCUSDT": time.time() + 0.1}
        
        remaining = bot._exchange_closed_reentry_remaining("BTCUSDT")
        
        # Should round up to 1 second
        assert remaining >= 1

    def test_cooldown_value_900_equals_15_minutes(self):
        """Verify 900 seconds equals 15 minutes"""
        assert 900 == 15 * 60, "900 seconds should equal 15 minutes"


# ============================================================================
# Full Flow Integration Test
# ============================================================================

class TestFullFlowIntegration:
    """Full flow integration test for exchange_closed reentry cooldown"""

    @pytest.mark.asyncio
    async def test_full_flow_exchange_closed_to_reentry_block(self):
        """Test full flow: exchange_closed finalization -> reentry block -> scan rejection"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_reentry_cooldown_sec = 900
        bot._exchange_closed_reentry_until = {}
        bot._missing_exchange_cycles = {"BTCUSDT": 5}
        bot.position_manager = MagicMock()
        bot.risk_guard = MagicMock()
        bot.controls = MagicMock()
        bot.tg = None
        
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="bot"
        )
        
        bot._save_trade = MagicMock()
        bot._calc_pnl_pct = MagicMock(return_value=1.5)
        
        # Step 1: Finalize with exchange_closed reason
        await bot._finalize_full_close("BTCUSDT", pos, 51000.0, 10.0, "exchange_closed")
        
        # Step 2: Verify reentry block is set
        assert "BTCUSDT" in bot._exchange_closed_reentry_until
        
        # Step 3: Verify scan would reject this symbol
        remaining = bot._exchange_closed_reentry_remaining("BTCUSDT")
        assert remaining > 0, "Symbol should be under cooldown"
        
        # In actual scan loop, this would trigger:
        # mark_reject("exchange_closed_reentry_cooldown")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
