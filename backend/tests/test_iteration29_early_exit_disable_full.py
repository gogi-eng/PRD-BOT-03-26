#!/usr/bin/env python3
"""
Iteration 29 Tests: Early Exit Disable Feature
Bug fix: early_exit_bars=0 must mean feature disabled, not immediate exit.

Tests cover:
1. Early exit disabled when early_exit_bars=0
2. Early exit disabled when early_exit_bars<0 (negative)
3. Early exit enabled when early_exit_bars>0
4. Regression tests for risk cooldown reason-aware logic (iteration 28)
5. Regression tests for signal mode toggle logic (iteration 27)
"""
from __future__ import annotations

import os
import sys
import inspect

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from engine.exit_engine import ExitEngine, ExitReason
from engine.position_manager import Position
from engine.risk_manager import RiskGuard


# ============================================================================
# Helper functions
# ============================================================================

def _pos_long(entry: float = 100.0, bars: int = 0) -> Position:
    """Create a long position for testing."""
    pos = Position(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=entry,
        qty=1.0,
        stop_loss=90.0,
        take_profit=120.0,
    )
    pos.bars_since_entry = bars
    return pos


def _pos_short(entry: float = 100.0, bars: int = 0) -> Position:
    """Create a short position for testing."""
    pos = Position(
        symbol="TESTUSDT",
        side="SELL",
        entry_price=entry,
        qty=1.0,
        stop_loss=110.0,
        take_profit=80.0,
    )
    pos.bars_since_entry = bars
    return pos


# ============================================================================
# Test: Early Exit Disabled when early_exit_bars=0
# ============================================================================

class TestEarlyExitDisabledWhenBarsZero:
    """Verify early_exit_bars=0 disables the early exit feature."""

    def test_early_exit_disabled_long_position_zero_bars_config(self):
        """Long position should NOT trigger early exit when early_exit_bars=0."""
        engine = ExitEngine(early_exit_bars=0, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=999)  # Many bars since entry

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.1,  # Minimal profit
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT

    def test_early_exit_disabled_short_position_zero_bars_config(self):
        """Short position should NOT trigger early exit when early_exit_bars=0."""
        engine = ExitEngine(early_exit_bars=0, early_exit_min_profit_atr=0.35)
        pos = _pos_short(bars=999)

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=99.9,  # Minimal profit
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT

    def test_early_exit_disabled_even_with_zero_profit(self):
        """Even with zero profit, early exit should not trigger when bars=0."""
        engine = ExitEngine(early_exit_bars=0, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=1000)

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.0,  # No profit at all
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT


# ============================================================================
# Test: Early Exit Disabled when early_exit_bars<0 (negative)
# ============================================================================

class TestEarlyExitDisabledWhenBarsNegative:
    """Verify early_exit_bars<0 also disables the early exit feature."""

    def test_early_exit_disabled_negative_bars_config(self):
        """Negative early_exit_bars should disable early exit."""
        engine = ExitEngine(early_exit_bars=-1, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=999)

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.1,
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT

    def test_early_exit_disabled_large_negative_bars_config(self):
        """Large negative early_exit_bars should disable early exit."""
        engine = ExitEngine(early_exit_bars=-100, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=999)

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.1,
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT


# ============================================================================
# Test: Early Exit Enabled when early_exit_bars>0
# ============================================================================

class TestEarlyExitEnabledWhenBarsPositive:
    """Verify early_exit_bars>0 enables the early exit feature."""

    def test_early_exit_triggers_when_bars_positive_and_no_profit(self):
        """Early exit should trigger when bars>0 and position has no profit."""
        engine = ExitEngine(early_exit_bars=12, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=15)  # More than 12 bars

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.1,  # Profit < 0.35 ATR
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is True
        assert reason == ExitReason.EARLY_EXIT
        assert "No movement" in msg

    def test_early_exit_does_not_trigger_when_profit_sufficient(self):
        """Early exit should NOT trigger when profit >= min_profit_atr."""
        engine = ExitEngine(early_exit_bars=12, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=15)

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.5,  # Profit = 0.5 > 0.35 ATR
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT

    def test_early_exit_does_not_trigger_before_bars_threshold(self):
        """Early exit should NOT trigger before bars_since_entry >= early_exit_bars."""
        engine = ExitEngine(early_exit_bars=12, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=10)  # Less than 12 bars

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.1,  # Low profit
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT

    def test_early_exit_respects_allow_early_exit_flag(self):
        """Early exit should NOT trigger when allow_early_exit=False."""
        engine = ExitEngine(early_exit_bars=12, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=15)

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.1,
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=False,  # Disabled
        )

        assert should_close is False
        assert reason != ExitReason.EARLY_EXIT


# ============================================================================
# Test: Config has early_exit_bars=0
# ============================================================================

class TestConfigHasEarlyExitBarsZero:
    """Verify config.yaml has early_exit_bars=0."""

    def test_config_has_early_exit_bars_zero(self):
        """Config should have early_exit_bars=0 to disable the feature."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)

        assert 'exit' in config
        assert 'early_exit_bars' in config['exit']
        assert config['exit']['early_exit_bars'] == 0


# ============================================================================
# Test: ExitEngine code has correct condition
# ============================================================================

class TestExitEngineCodeCorrectness:
    """Verify ExitEngine.check_exit has correct early_exit_bars condition."""

    def test_check_exit_has_early_exit_bars_greater_than_zero_check(self):
        """check_exit should check early_exit_bars > 0 before triggering early exit."""
        source = inspect.getsource(ExitEngine.check_exit)
        # The condition should be: self.early_exit_bars > 0
        assert "self.early_exit_bars > 0" in source


# ============================================================================
# Regression Tests: Risk Cooldown Reason-Aware Logic (Iteration 28)
# ============================================================================

class TestRegressionIteration28RiskCooldown:
    """Regression tests for iteration 28 risk cooldown reason-aware logic."""

    def test_early_exit_small_loss_no_cooldown(self):
        """early_exit small loss should NOT trigger cooldown."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-0.01, symbol="TESTUSDT", reason="early_exit")

        allowed, _reason = guard.can_trade("TESTUSDT")
        assert allowed is True

    def test_early_exit_small_loss_no_consecutive_increment(self):
        """early_exit small loss should NOT increment consecutive losses."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-0.01, symbol="TESTUSDT", reason="early_exit")

        assert guard._consecutive_losses == 0

    def test_real_loss_triggers_cooldown(self):
        """Real loss (e.g., liquidation_stop) should trigger cooldown."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-1.2, symbol="TESTUSDT", reason="liquidation_stop")

        allowed, reason = guard.can_trade("TESTUSDT")
        assert allowed is False
        assert "Cooldown" in reason

    def test_real_loss_increments_consecutive(self):
        """Real loss should increment consecutive losses."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-1.2, symbol="TESTUSDT", reason="liquidation_stop")

        assert guard._consecutive_losses == 1


# ============================================================================
# Regression Tests: Signal Mode Toggle Logic (Iteration 27)
# ============================================================================

class TestRegressionIteration27SignalModeToggle:
    """Regression tests for iteration 27 signal mode toggle logic."""

    def test_trading_bot_has_switch_signal_mode_method(self):
        """TradingBot should have _switch_signal_mode method."""
        from main import TradingBot
        assert hasattr(TradingBot, '_switch_signal_mode')

    def test_telegram_controller_has_mode_switch_actions(self):
        """TelegramController should have mode switch actions."""
        from tg.controller import TelegramController
        source = inspect.getsource(TelegramController.on_button)
        assert "SWITCH_MODE_PROMPT" in source
        assert "SWITCH_MODE_CONFIRM_SIGNAL" in source
        assert "SWITCH_MODE_CONFIRM_LIVE" in source


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge case tests for early exit disable feature."""

    def test_early_exit_bars_exactly_at_threshold(self):
        """Test when bars_since_entry == early_exit_bars (boundary)."""
        engine = ExitEngine(early_exit_bars=12, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=12)  # Exactly at threshold

        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=100.1,  # Low profit
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        # Should trigger because bars_since_entry >= early_exit_bars
        assert should_close is True
        assert reason == ExitReason.EARLY_EXIT

    def test_other_exit_reasons_still_work_when_early_exit_disabled(self):
        """Other exit reasons (SL, TP) should still work when early_exit_bars=0."""
        engine = ExitEngine(early_exit_bars=0, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=999)

        # Test SL hit
        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=89.0,  # Below SL of 90
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is True
        assert reason == ExitReason.HARD_SL

    def test_tp_still_works_when_early_exit_disabled(self):
        """TP should still work when early_exit_bars=0."""
        engine = ExitEngine(early_exit_bars=0, early_exit_min_profit_atr=0.35)
        pos = _pos_long(bars=999)

        # Test TP hit
        should_close, reason, msg = engine.check_exit(
            pos,
            current_price=121.0,  # Above TP of 120
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=True,
        )

        assert should_close is True
        assert reason == ExitReason.TP_CAP


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
