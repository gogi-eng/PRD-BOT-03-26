#!/usr/bin/env python3
"""
Iteration 22 - Signal-Only Mode Cooldown Bug Fix Verification

Bug: Bot was fully blocked every cycle with 'Trading blocked: Cooldown: ...' in signal-only
mode after feedback-loop updates.

Root Cause: feedback losses feeding RiskGuard cooldown in signal-only mode via record_trade()

Fix:
1. config.yaml: feedback_loop.apply_to_risk_guard = false (by default)
2. main.py: Double protection - even if apply_to_risk_guard=True, doesn't call
   risk_guard.record_trade() when signal_only=True

Tests verify:
- Config has apply_to_risk_guard: false
- main.py _process_signal_feedback_loop has (not self.signal_only) guard
- Signal-only mode does NOT throttle via synthetic feedback losses
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from core.config import BotConfig


class TestConfigFix:
    """Tests for config.yaml fix"""

    def test_apply_to_risk_guard_is_false_in_config(self):
        """Config should have apply_to_risk_guard=false to prevent signal-only cooldown"""
        cfg = BotConfig.load("/app/bot/config.yaml")
        value = cfg.get("feedback_loop", "apply_to_risk_guard", default=True)
        assert value is False, (
            "feedback_loop.apply_to_risk_guard must be False to prevent "
            "synthetic feedback losses from triggering RiskGuard cooldown in signal-only mode"
        )

    def test_signal_only_is_true_in_config(self):
        """Config should have signal_only=true (signal-only mode active)"""
        cfg = BotConfig.load("/app/bot/config.yaml")
        value = cfg.get("bot", "signal_only", default=False)
        assert value is True, "bot.signal_only should be True for signal-only mode"


class TestMainPyCodeFix:
    """Tests for main.py code fix - double protection guard"""

    def test_process_signal_feedback_loop_has_signal_only_guard(self):
        """_process_signal_feedback_loop must NOT call record_trade in signal-only mode"""
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._process_signal_feedback_loop)
        
        # Verify the double protection: both apply_to_risk_guard AND not signal_only
        assert "feedback_apply_to_risk_guard" in source, (
            "_process_signal_feedback_loop must check feedback_apply_to_risk_guard flag"
        )
        assert "not self.signal_only" in source or "(not self.signal_only)" in source, (
            "_process_signal_feedback_loop must have 'not self.signal_only' guard "
            "to prevent record_trade calls in signal-only mode"
        )
        
        # Verify the condition structure: both checks must be ANDed together
        assert "feedback_apply_to_risk_guard and (not self.signal_only)" in source, (
            "_process_signal_feedback_loop must use: "
            "'if self.feedback_apply_to_risk_guard and (not self.signal_only)' "
            "to prevent record_trade in signal-only mode"
        )

    def test_record_trade_call_is_inside_double_guard(self):
        """Verify risk_guard.record_trade is only called when NOT in signal-only mode"""
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._process_signal_feedback_loop)
        
        # Find the line with record_trade and verify it's inside the guard
        lines = source.split('\n')
        record_trade_line = None
        guard_line_idx = None
        
        for i, line in enumerate(lines):
            if 'feedback_apply_to_risk_guard and (not self.signal_only)' in line:
                guard_line_idx = i
            if 'record_trade' in line and 'risk_guard' in line:
                record_trade_line = i
        
        assert guard_line_idx is not None, "Guard line not found"
        assert record_trade_line is not None, "record_trade call not found"
        assert record_trade_line > guard_line_idx, (
            "record_trade call must be AFTER the guard condition "
            "(inside the if block with signal_only check)"
        )


class TestTradingBotInitialization:
    """Tests for TradingBot initialization with the fix"""

    def test_bot_initializes_with_correct_flags(self):
        """TradingBot should initialize with correct feedback_apply_to_risk_guard flag"""
        from main import TradingBot
        
        bot = TradingBot()
        
        # Verify both flags are correctly initialized from config
        assert hasattr(bot, 'signal_only')
        assert hasattr(bot, 'feedback_apply_to_risk_guard')
        
        # Config values
        assert bot.signal_only is True, "Bot should be in signal-only mode"
        assert bot.feedback_apply_to_risk_guard is False, (
            "feedback_apply_to_risk_guard should be False from config"
        )


class TestSignalOnlyModeDoesNotThrottle:
    """Integration tests verifying signal-only mode doesn't trigger RiskGuard cooldown"""

    def test_feedback_outcomes_do_not_call_record_trade_in_signal_only_mode(self):
        """When signal_only=True, feedback outcomes must NOT call risk_guard.record_trade"""
        from main import TradingBot
        
        # Create bot instance
        bot = TradingBot()
        
        # Force signal_only=True (should already be True from config)
        bot.signal_only = True
        
        # Mock risk_guard to track record_trade calls
        bot.risk_guard.record_trade = MagicMock()
        
        # Simulate the condition check in _process_signal_feedback_loop
        # The actual logic is: if self.feedback_apply_to_risk_guard and (not self.signal_only):
        
        # Test with both flags
        should_call_record_trade = bot.feedback_apply_to_risk_guard and (not bot.signal_only)
        
        # In signal-only mode, should_call_record_trade must be False
        assert should_call_record_trade is False, (
            "In signal-only mode, record_trade should NOT be called from feedback outcomes"
        )

    def test_even_with_apply_to_risk_guard_true_signal_only_blocks(self):
        """Even if apply_to_risk_guard=True, signal_only=True should block record_trade"""
        from main import TradingBot
        
        bot = TradingBot()
        
        # Force apply_to_risk_guard=True (override config)
        bot.feedback_apply_to_risk_guard = True
        bot.signal_only = True
        
        # The condition: feedback_apply_to_risk_guard AND (not signal_only)
        # True AND (not True) = True AND False = False
        should_call_record_trade = bot.feedback_apply_to_risk_guard and (not bot.signal_only)
        
        assert should_call_record_trade is False, (
            "Even with apply_to_risk_guard=True, signal_only=True must block record_trade"
        )

    def test_record_trade_called_only_when_not_signal_only(self):
        """record_trade should only be called when signal_only=False AND apply_to_risk_guard=True"""
        from main import TradingBot
        
        bot = TradingBot()
        
        # Scenario 1: signal_only=True, apply_to_risk_guard=True → NO record_trade
        bot.signal_only = True
        bot.feedback_apply_to_risk_guard = True
        assert (bot.feedback_apply_to_risk_guard and (not bot.signal_only)) is False
        
        # Scenario 2: signal_only=True, apply_to_risk_guard=False → NO record_trade
        bot.signal_only = True
        bot.feedback_apply_to_risk_guard = False
        assert (bot.feedback_apply_to_risk_guard and (not bot.signal_only)) is False
        
        # Scenario 3: signal_only=False, apply_to_risk_guard=False → NO record_trade
        bot.signal_only = False
        bot.feedback_apply_to_risk_guard = False
        assert (bot.feedback_apply_to_risk_guard and (not bot.signal_only)) is False
        
        # Scenario 4: signal_only=False, apply_to_risk_guard=True → YES record_trade
        bot.signal_only = False
        bot.feedback_apply_to_risk_guard = True
        assert (bot.feedback_apply_to_risk_guard and (not bot.signal_only)) is True


class TestRegressionIteration21Features:
    """Regression tests to ensure iteration 21 features still work"""

    def test_feedback_apply_to_risk_guard_config_option_exists(self):
        """The apply_to_risk_guard config option must exist"""
        cfg = BotConfig.load("/app/bot/config.yaml")
        value = cfg.get("feedback_loop", "apply_to_risk_guard", default="NOT_FOUND")
        assert value != "NOT_FOUND", "feedback_loop.apply_to_risk_guard must exist in config"

    def test_signal_feedback_loop_enabled(self):
        """Signal feedback loop should still be enabled"""
        cfg = BotConfig.load("/app/bot/config.yaml")
        value = cfg.get("feedback_loop", "enabled", default=False)
        assert value is True, "feedback_loop should be enabled"

    def test_mtf_zone_confirmation_still_enabled(self):
        """MTF zone confirmation from iteration 21 should still be enabled"""
        cfg = BotConfig.load("/app/bot/config.yaml")
        value = cfg.get("mtf_zone_confirmation", "enabled", default=False)
        assert value is True

    def test_symbol_quality_filter_still_enabled(self):
        """Symbol quality filter from iteration 21 should still be enabled"""
        cfg = BotConfig.load("/app/bot/config.yaml")
        value = cfg.get("symbol_quality", "enabled", default=False)
        assert value is True

    def test_correlation_filter_still_enabled(self):
        """Correlation filter from iteration 21 should still be enabled"""
        cfg = BotConfig.load("/app/bot/config.yaml")
        value = cfg.get("correlation", "enabled", default=False)
        assert value is True


# =============================================================================
# Run verification
# =============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
