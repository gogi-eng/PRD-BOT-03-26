#!/usr/bin/env python3
"""
Iteration 30 Tests: Exchange Closed Cooldown Prevention

Tests verify:
1. exchange_closed reason is ignored for cooldown and consecutive loss tracking
2. Strict > comparison (not >=) for loss thresholds
3. Tiny losses at/below threshold don't trigger cooldown
4. Regression: only exchange_closed is ignored by config
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import pytest
import yaml
from engine.risk_manager import RiskGuard


# ============================================================================
# Test: Config has exchange_closed in ignore lists
# ============================================================================
class TestConfigHasExchangeClosedIgnore:
    """Verify config.yaml has exchange_closed in ignore lists"""

    def test_config_has_exchange_closed_in_ignore_cooldown_reasons(self):
        """exchange_closed should be in ignore_loss_cooldown_reasons"""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        ignore_reasons = config.get('risk', {}).get('ignore_loss_cooldown_reasons', [])
        assert 'exchange_closed' in ignore_reasons, f"exchange_closed not in ignore_loss_cooldown_reasons: {ignore_reasons}"
        print(f"PASS: exchange_closed in ignore_loss_cooldown_reasons: {ignore_reasons}")

    def test_config_has_exchange_closed_in_ignore_consecutive_reasons(self):
        """exchange_closed should be in ignore_consecutive_loss_reasons"""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        ignore_reasons = config.get('risk', {}).get('ignore_consecutive_loss_reasons', [])
        assert 'exchange_closed' in ignore_reasons, f"exchange_closed not in ignore_consecutive_loss_reasons: {ignore_reasons}"
        print(f"PASS: exchange_closed in ignore_consecutive_loss_reasons: {ignore_reasons}")

    def test_config_ignores_exchange_closed_and_early_exit(self):
        """Config should ignore exchange_closed and early_exit for cooldown logic."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        cooldown_reasons = config.get('risk', {}).get('ignore_loss_cooldown_reasons', [])
        consecutive_reasons = config.get('risk', {}).get('ignore_consecutive_loss_reasons', [])
        
        assert set(cooldown_reasons) == {"exchange_closed", "early_exit"}, (
            f"Expected reasons set {{'exchange_closed', 'early_exit'}}, got {cooldown_reasons}"
        )
        assert set(consecutive_reasons) == {"exchange_closed", "early_exit"}, (
            f"Expected reasons set {{'exchange_closed', 'early_exit'}}, got {consecutive_reasons}"
        )
        print("PASS: ignore lists include exchange_closed and early_exit")


# ============================================================================
# Test: exchange_closed does NOT trigger cooldown
# ============================================================================
class TestExchangeClosedNoCooldown:
    """exchange_closed reason should not trigger cooldown regardless of loss size"""

    def test_exchange_closed_tiny_loss_no_cooldown(self):
        """Tiny loss with exchange_closed reason should not trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Tiny loss with exchange_closed
        guard.record_trade(-0.10, symbol="BTCUSDT", reason="exchange_closed")
        
        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is True, f"Should allow trade after exchange_closed tiny loss, got: {reason}"
        assert guard._consecutive_losses == 0, f"Consecutive losses should be 0, got: {guard._consecutive_losses}"
        print("PASS: Tiny loss with exchange_closed does not trigger cooldown")

    def test_exchange_closed_medium_loss_no_cooldown(self):
        """Medium loss (above threshold) with exchange_closed should NOT trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Loss above threshold but with exchange_closed reason
        guard.record_trade(-0.50, symbol="ETHUSDT", reason="exchange_closed")
        
        allowed, reason = guard.can_trade("ETHUSDT")
        assert allowed is True, f"Should allow trade after exchange_closed medium loss, got: {reason}"
        assert guard._consecutive_losses == 0, f"Consecutive losses should be 0, got: {guard._consecutive_losses}"
        print("PASS: Medium loss with exchange_closed does not trigger cooldown")

    def test_exchange_closed_large_loss_no_cooldown(self):
        """Large loss with exchange_closed should NOT trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Large loss with exchange_closed reason
        guard.record_trade(-5.00, symbol="SOLUSDT", reason="exchange_closed")
        
        allowed, reason = guard.can_trade("SOLUSDT")
        assert allowed is True, f"Should allow trade after exchange_closed large loss, got: {reason}"
        assert guard._consecutive_losses == 0, f"Consecutive losses should be 0, got: {guard._consecutive_losses}"
        print("PASS: Large loss with exchange_closed does not trigger cooldown")

    def test_exchange_closed_case_insensitive(self):
        """exchange_closed matching should be case-insensitive"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Test various case variations
        guard.record_trade(-1.00, symbol="BTCUSDT", reason="EXCHANGE_CLOSED")
        allowed1, _ = guard.can_trade("BTCUSDT")
        
        guard.record_trade(-1.00, symbol="ETHUSDT", reason="Exchange_Closed")
        allowed2, _ = guard.can_trade("ETHUSDT")
        
        assert allowed1 is True, "EXCHANGE_CLOSED (uppercase) should be ignored"
        assert allowed2 is True, "Exchange_Closed (mixed case) should be ignored"
        assert guard._consecutive_losses == 0
        print("PASS: exchange_closed matching is case-insensitive")


# ============================================================================
# Test: Strict > comparison for thresholds (not >=)
# ============================================================================
class TestStrictThresholdComparison:
    """Verify strict > comparison is used, not >="""

    def test_loss_exactly_at_cooldown_threshold_no_cooldown(self):
        """Loss exactly at min_loss_usdt_for_cooldown should NOT trigger cooldown (strict >)"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Loss exactly at threshold (0.35)
        guard.record_trade(-0.35, symbol="BTCUSDT", reason="stop_loss")
        
        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is True, f"Loss at threshold should NOT trigger cooldown (strict >), got: {reason}"
        print("PASS: Loss exactly at cooldown threshold does not trigger cooldown")

    def test_loss_exactly_at_consecutive_threshold_no_increment(self):
        """Loss exactly at min_loss_usdt_for_consecutive should NOT increment consecutive (strict >)"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Loss exactly at threshold (0.6)
        guard.record_trade(-0.60, symbol="BTCUSDT", reason="stop_loss")
        
        assert guard._consecutive_losses == 0, f"Loss at threshold should NOT increment consecutive (strict >), got: {guard._consecutive_losses}"
        print("PASS: Loss exactly at consecutive threshold does not increment consecutive")

    def test_loss_just_below_cooldown_threshold_no_cooldown(self):
        """Loss just below threshold should NOT trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Loss just below threshold
        guard.record_trade(-0.34, symbol="BTCUSDT", reason="stop_loss")
        
        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is True, f"Loss below threshold should NOT trigger cooldown, got: {reason}"
        print("PASS: Loss just below cooldown threshold does not trigger cooldown")

    def test_loss_just_above_cooldown_threshold_triggers_cooldown(self):
        """Loss just above threshold SHOULD trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Loss just above threshold
        guard.record_trade(-0.36, symbol="BTCUSDT", reason="stop_loss")
        
        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is False, f"Loss above threshold SHOULD trigger cooldown"
        assert "Cooldown" in reason, f"Reason should mention cooldown, got: {reason}"
        print("PASS: Loss just above cooldown threshold triggers cooldown")

    def test_loss_just_above_consecutive_threshold_increments(self):
        """Loss just above consecutive threshold SHOULD increment consecutive"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Loss just above threshold
        guard.record_trade(-0.61, symbol="BTCUSDT", reason="stop_loss")
        
        assert guard._consecutive_losses == 1, f"Loss above threshold SHOULD increment consecutive, got: {guard._consecutive_losses}"
        print("PASS: Loss just above consecutive threshold increments consecutive")


# ============================================================================
# Test: Tiny losses around threshold don't trigger cooldown
# ============================================================================
class TestTinyLossesNoFalsePositive:
    """Tiny losses should not trigger cooldown false positives"""

    def test_micro_loss_no_cooldown(self):
        """Micro loss (-0.01) should not trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        guard.record_trade(-0.01, symbol="BTCUSDT", reason="stop_loss")
        
        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is True, f"Micro loss should not trigger cooldown, got: {reason}"
        assert guard._consecutive_losses == 0
        print("PASS: Micro loss does not trigger cooldown")

    def test_multiple_tiny_losses_no_consecutive_increment(self):
        """Multiple tiny losses should not increment consecutive counter"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            max_consecutive_losses=5,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Record 10 tiny losses
        for i in range(10):
            guard.record_trade(-0.10, symbol=f"SYM{i}USDT", reason="stop_loss")
        
        assert guard._consecutive_losses == 0, f"Tiny losses should not increment consecutive, got: {guard._consecutive_losses}"
        allowed, _ = guard.can_trade("BTCUSDT")
        assert allowed is True, "Should still be able to trade after tiny losses"
        print("PASS: Multiple tiny losses do not increment consecutive counter")

    def test_tiny_loss_with_exchange_closed_double_protection(self):
        """Tiny loss with exchange_closed has double protection (threshold + reason)"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Tiny loss with exchange_closed - protected by both threshold AND reason
        guard.record_trade(-0.05, symbol="BTCUSDT", reason="exchange_closed")
        
        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is True, f"Double protection should work, got: {reason}"
        assert guard._consecutive_losses == 0
        print("PASS: Tiny loss with exchange_closed has double protection")


# ============================================================================
# Regression: early_exit ignore behavior still works
# ============================================================================
class TestRegressionEarlyExitIgnore:
    """Regression tests for early_exit ignore behavior (from iteration 28)"""

    def test_early_exit_small_loss_no_cooldown(self):
        """early_exit with small loss should not trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        guard.record_trade(-0.01, symbol="LYNUSDT", reason="early_exit")
        
        allowed, reason = guard.can_trade("LYNUSDT")
        assert allowed is True, f"early_exit should not trigger cooldown, got: {reason}"
        assert guard._consecutive_losses == 0
        print("PASS: early_exit small loss does not trigger cooldown")

    def test_early_exit_large_loss_no_cooldown(self):
        """early_exit with large loss should still not trigger cooldown (reason ignored)"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        guard.record_trade(-5.00, symbol="LYNUSDT", reason="early_exit")
        
        allowed, reason = guard.can_trade("LYNUSDT")
        assert allowed is True, f"early_exit should not trigger cooldown even with large loss, got: {reason}"
        assert guard._consecutive_losses == 0
        print("PASS: early_exit large loss does not trigger cooldown")

    def test_real_loss_still_triggers_cooldown(self):
        """Real loss (not ignored reason) should still trigger cooldown"""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        guard.record_trade(-1.20, symbol="LYNUSDT", reason="liquidation_stop")
        
        allowed, reason = guard.can_trade("LYNUSDT")
        assert allowed is False, "Real loss should trigger cooldown"
        assert "Cooldown" in reason
        assert guard._consecutive_losses == 1
        print("PASS: Real loss still triggers cooldown correctly")


# ============================================================================
# Test: Mixed scenarios
# ============================================================================
class TestMixedScenarios:
    """Test mixed scenarios with different reasons and loss sizes"""

    def test_alternating_exchange_closed_and_real_losses(self):
        """Alternating exchange_closed and real losses"""
        guard = RiskGuard(
            cooldown_after_loss_sec=1,  # Short cooldown for testing
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            max_consecutive_losses=5,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # exchange_closed - should not count
        guard.record_trade(-1.00, symbol="BTCUSDT", reason="exchange_closed")
        assert guard._consecutive_losses == 0
        
        # Real loss - should count
        guard.record_trade(-1.00, symbol="ETHUSDT", reason="stop_loss")
        assert guard._consecutive_losses == 1
        
        # exchange_closed - should not count, but doesn't reset consecutive
        guard.record_trade(-1.00, symbol="SOLUSDT", reason="exchange_closed")
        assert guard._consecutive_losses == 1  # Still 1, not reset
        
        # Real loss - should increment
        guard.record_trade(-1.00, symbol="LINKUSDT", reason="stop_loss")
        assert guard._consecutive_losses == 2
        
        print("PASS: Alternating exchange_closed and real losses handled correctly")

    def test_win_resets_consecutive_after_exchange_closed(self):
        """Win should reset consecutive counter even after exchange_closed losses"""
        guard = RiskGuard(
            cooldown_after_loss_sec=1,
            min_loss_usdt_for_cooldown=0.35,
            min_loss_usdt_for_consecutive=0.6,
            max_consecutive_losses=5,
            ignore_loss_cooldown_reasons=["early_exit", "exchange_closed"],
            ignore_consecutive_loss_reasons=["early_exit", "exchange_closed"],
        )
        
        # Real loss
        guard.record_trade(-1.00, symbol="BTCUSDT", reason="stop_loss")
        assert guard._consecutive_losses == 1
        
        # exchange_closed loss (doesn't increment)
        guard.record_trade(-1.00, symbol="ETHUSDT", reason="exchange_closed")
        assert guard._consecutive_losses == 1
        
        # Win - should reset
        guard.record_trade(2.00, symbol="SOLUSDT", reason="take_profit")
        assert guard._consecutive_losses == 0
        
        print("PASS: Win resets consecutive counter correctly")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
