#!/usr/bin/env python3
"""
Iteration 28: Risk Cooldown Early Exit Feature Tests

Tests for preventing overblocking from micro early_exit losses while keeping
protection for real losses. Features tested:
- RiskGuard.record_trade supports reason-aware cooldown/consecutive handling
- early_exit small losses do not trigger cooldown/consecutive
- real losses (e.g., liquidation_stop) still trigger cooldown/consecutive
- main.py passes reason into risk_guard.record_trade for full/partial close
- config has new risk keys: min_loss_usdt_for_cooldown/min_loss_usdt_for_consecutive/ignore_*_reasons
- regression tests for previous iterations still pass
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import pytest
import yaml

from engine.risk_manager import RiskGuard


# ============================================================================
# Core Feature Tests: Reason-aware cooldown/consecutive handling
# ============================================================================

class TestEarlyExitSmallLossDoesNotTriggerCooldown:
    """Verify early_exit with tiny negative pnl does not block subsequent cycles."""

    def test_early_exit_small_loss_no_cooldown(self):
        """Early exit with small loss should not trigger cooldown."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Record a tiny loss with early_exit reason
        guard.record_trade(-0.01, symbol="BTCUSDT", reason="early_exit")

        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is True, f"Should allow trading after early_exit small loss, got: {reason}"
        assert guard.last_loss_time is None, "last_loss_time should not be set for early_exit"

    def test_early_exit_small_loss_no_consecutive_increment(self):
        """Early exit with small loss should not increment consecutive losses."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-0.01, symbol="BTCUSDT", reason="early_exit")

        assert guard._consecutive_losses == 0, "Consecutive losses should remain 0 for early_exit"
        assert guard.day_stats.consecutive_losses == 0

    def test_multiple_early_exit_losses_no_blocking(self):
        """Multiple early_exit losses should not accumulate to block trading."""
        guard = RiskGuard(
            max_consecutive_losses=3,
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Record 5 early_exit losses
        for i in range(5):
            guard.record_trade(-0.05, symbol="ETHUSDT", reason="early_exit")

        allowed, reason = guard.can_trade("ETHUSDT")
        assert allowed is True, f"Should allow trading after multiple early_exit losses, got: {reason}"
        assert guard._consecutive_losses == 0, "Consecutive losses should remain 0"

    def test_early_exit_case_insensitive(self):
        """Reason matching should be case-insensitive."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Test with different cases
        guard.record_trade(-0.01, symbol="BTCUSDT", reason="EARLY_EXIT")
        assert guard._consecutive_losses == 0

        guard.record_trade(-0.01, symbol="BTCUSDT", reason="Early_Exit")
        assert guard._consecutive_losses == 0

    def test_early_exit_with_whitespace(self):
        """Reason matching should handle whitespace."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-0.01, symbol="BTCUSDT", reason="  early_exit  ")
        assert guard._consecutive_losses == 0


class TestRealLossTriggersCooldownAndConsecutive:
    """Verify real losses (e.g., liquidation_stop) still trigger cooldown/consecutive."""

    def test_liquidation_stop_triggers_cooldown(self):
        """Liquidation stop loss should trigger cooldown."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-1.2, symbol="BTCUSDT", reason="liquidation_stop")

        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is False, "Should block trading after liquidation_stop"
        assert "Cooldown" in reason

    def test_liquidation_stop_increments_consecutive(self):
        """Liquidation stop loss should increment consecutive losses."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-1.2, symbol="BTCUSDT", reason="liquidation_stop")

        assert guard._consecutive_losses == 1

    def test_hard_sl_triggers_cooldown(self):
        """Hard SL loss should trigger cooldown."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-0.8, symbol="SOLUSDT", reason="hard_sl")

        allowed, reason = guard.can_trade("SOLUSDT")
        assert allowed is False, "Should block trading after hard_sl"
        assert "Cooldown" in reason
        assert guard._consecutive_losses == 1

    def test_trailing_exit_loss_triggers_cooldown(self):
        """Trailing exit loss should trigger cooldown if above threshold."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-0.5, symbol="LINKUSDT", reason="trailing_exit")

        allowed, reason = guard.can_trade("LINKUSDT")
        assert allowed is False
        assert guard._consecutive_losses == 1

    def test_consecutive_real_losses_trigger_auto_stop(self):
        """Multiple real losses should trigger auto-stop."""
        guard = RiskGuard(
            max_consecutive_losses=3,
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Record 3 real losses
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="hard_sl")
        guard.record_trade(-1.0, symbol="ETHUSDT", reason="hard_sl")
        guard.record_trade(-1.0, symbol="SOLUSDT", reason="hard_sl")

        assert guard._consecutive_losses == 3
        allowed, reason = guard.can_trade()
        assert allowed is False
        assert "consecutive" in reason.lower() or "auto-stop" in reason.lower()


class TestMinLossThresholds:
    """Test minimum loss thresholds for cooldown and consecutive."""

    def test_loss_below_cooldown_threshold_no_cooldown(self):
        """Loss below min_loss_usdt_for_cooldown should not trigger cooldown."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=[],
            ignore_consecutive_loss_reasons=[],
        )
        # Loss of $0.10 is below $0.25 threshold
        guard.record_trade(-0.10, symbol="BTCUSDT", reason="some_reason")

        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is True, f"Should allow trading for loss below threshold, got: {reason}"

    def test_loss_above_cooldown_threshold_triggers_cooldown(self):
        """Loss above min_loss_usdt_for_cooldown should trigger cooldown."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=[],
            ignore_consecutive_loss_reasons=[],
        )
        # Loss of $0.30 is above $0.25 threshold
        guard.record_trade(-0.30, symbol="BTCUSDT", reason="some_reason")

        allowed, reason = guard.can_trade("BTCUSDT")
        assert allowed is False, "Should block trading for loss above threshold"
        assert "Cooldown" in reason

    def test_loss_below_consecutive_threshold_no_increment(self):
        """Loss below min_loss_usdt_for_consecutive should not increment consecutive."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=[],
            ignore_consecutive_loss_reasons=[],
        )
        # Loss of $0.30 is above cooldown but below consecutive threshold
        guard.record_trade(-0.30, symbol="BTCUSDT", reason="some_reason")

        assert guard._consecutive_losses == 0, "Should not increment consecutive for loss below threshold"

    def test_loss_above_consecutive_threshold_increments(self):
        """Loss above min_loss_usdt_for_consecutive should increment consecutive."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=[],
            ignore_consecutive_loss_reasons=[],
        )
        # Loss of $0.60 is above $0.50 threshold
        guard.record_trade(-0.60, symbol="BTCUSDT", reason="some_reason")

        assert guard._consecutive_losses == 1


class TestMixedScenarios:
    """Test mixed scenarios with early_exit and real losses."""

    def test_early_exit_followed_by_real_loss(self):
        """Early exit followed by real loss should only count real loss."""
        guard = RiskGuard(
            max_consecutive_losses=3,
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Early exit - should not count
        guard.record_trade(-0.05, symbol="BTCUSDT", reason="early_exit")
        assert guard._consecutive_losses == 0

        # Real loss - should count
        guard.record_trade(-1.0, symbol="ETHUSDT", reason="hard_sl")
        assert guard._consecutive_losses == 1

    def test_real_loss_followed_by_early_exit(self):
        """Real loss followed by early exit should keep consecutive at 1."""
        guard = RiskGuard(
            max_consecutive_losses=3,
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Real loss - should count
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="hard_sl")
        assert guard._consecutive_losses == 1

        # Early exit - should not increment
        guard.record_trade(-0.05, symbol="ETHUSDT", reason="early_exit")
        assert guard._consecutive_losses == 1  # Still 1, not 2

    def test_win_resets_consecutive_after_real_loss(self):
        """Win should reset consecutive losses after real loss."""
        guard = RiskGuard(
            max_consecutive_losses=3,
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Real loss
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="hard_sl")
        assert guard._consecutive_losses == 1

        # Win
        guard.record_trade(0.5, symbol="ETHUSDT", reason="tp_hit")
        assert guard._consecutive_losses == 0

    def test_early_exit_does_not_reset_consecutive(self):
        """Early exit (even with tiny profit) should not affect consecutive count."""
        guard = RiskGuard(
            max_consecutive_losses=3,
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.25,
            min_loss_usdt_for_consecutive=0.5,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # Real loss
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="hard_sl")
        assert guard._consecutive_losses == 1

        # Early exit with tiny loss - should not increment
        guard.record_trade(-0.01, symbol="ETHUSDT", reason="early_exit")
        assert guard._consecutive_losses == 1  # Still 1


# ============================================================================
# Config Tests
# ============================================================================

class TestConfigHasNewRiskKeys:
    """Verify config.yaml has the new risk keys."""

    @pytest.fixture
    def config(self):
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        with open(config_path, "r") as f:
            return yaml.safe_load(f)

    def test_config_has_min_loss_usdt_for_cooldown(self, config):
        """Config should have min_loss_usdt_for_cooldown key."""
        assert "risk" in config
        assert "min_loss_usdt_for_cooldown" in config["risk"]
        assert isinstance(config["risk"]["min_loss_usdt_for_cooldown"], (int, float))

    def test_config_has_min_loss_usdt_for_consecutive(self, config):
        """Config should have min_loss_usdt_for_consecutive key."""
        assert "min_loss_usdt_for_consecutive" in config["risk"]
        assert isinstance(config["risk"]["min_loss_usdt_for_consecutive"], (int, float))

    def test_config_has_ignore_loss_cooldown_reasons(self, config):
        """Config should have ignore_loss_cooldown_reasons key."""
        assert "ignore_loss_cooldown_reasons" in config["risk"]
        assert isinstance(config["risk"]["ignore_loss_cooldown_reasons"], list)
        assert "early_exit" in config["risk"]["ignore_loss_cooldown_reasons"]

    def test_config_has_ignore_consecutive_loss_reasons(self, config):
        """Config should have ignore_consecutive_loss_reasons key."""
        assert "ignore_consecutive_loss_reasons" in config["risk"]
        assert isinstance(config["risk"]["ignore_consecutive_loss_reasons"], list)
        assert "early_exit" in config["risk"]["ignore_consecutive_loss_reasons"]


# ============================================================================
# RiskGuard Init Tests
# ============================================================================

class TestRiskGuardInitialization:
    """Test RiskGuard initialization with new parameters."""

    def test_default_min_loss_thresholds(self):
        """Default min loss thresholds should be 0."""
        guard = RiskGuard()
        assert guard.min_loss_usdt_for_cooldown == 0.0
        assert guard.min_loss_usdt_for_consecutive == 0.0

    def test_default_ignore_reasons_empty(self):
        """Default ignore reasons should be empty sets."""
        guard = RiskGuard()
        assert guard.ignore_loss_cooldown_reasons == set()
        assert guard.ignore_consecutive_loss_reasons == set()

    def test_custom_min_loss_thresholds(self):
        """Custom min loss thresholds should be set correctly."""
        guard = RiskGuard(
            min_loss_usdt_for_cooldown=0.5,
            min_loss_usdt_for_consecutive=1.0,
        )
        assert guard.min_loss_usdt_for_cooldown == 0.5
        assert guard.min_loss_usdt_for_consecutive == 1.0

    def test_custom_ignore_reasons(self):
        """Custom ignore reasons should be normalized to lowercase set."""
        guard = RiskGuard(
            ignore_loss_cooldown_reasons=["early_exit", "PARTIAL_TP"],
            ignore_consecutive_loss_reasons=["Early_Exit"],
        )
        assert "early_exit" in guard.ignore_loss_cooldown_reasons
        assert "partial_tp" in guard.ignore_loss_cooldown_reasons
        assert "early_exit" in guard.ignore_consecutive_loss_reasons

    def test_negative_thresholds_clamped_to_zero(self):
        """Negative thresholds should be clamped to 0."""
        guard = RiskGuard(
            min_loss_usdt_for_cooldown=-1.0,
            min_loss_usdt_for_consecutive=-2.0,
        )
        assert guard.min_loss_usdt_for_cooldown == 0.0
        assert guard.min_loss_usdt_for_consecutive == 0.0


# ============================================================================
# Integration Tests: main.py passes reason to record_trade
# ============================================================================

class TestMainPyPassesReasonToRecordTrade:
    """Verify main.py passes reason into risk_guard.record_trade."""

    def test_finalize_full_close_signature_has_reason(self):
        """_finalize_full_close should accept reason parameter."""
        import inspect
        from bot.main import TradingBot

        # Check method signature
        sig = inspect.signature(TradingBot._finalize_full_close)
        params = list(sig.parameters.keys())
        assert "reason" in params, "_finalize_full_close should have reason parameter"

    def test_finalize_partial_close_signature_has_reason(self):
        """_finalize_partial_close should accept reason parameter."""
        import inspect
        from bot.main import TradingBot

        sig = inspect.signature(TradingBot._finalize_partial_close)
        params = list(sig.parameters.keys())
        assert "reason" in params, "_finalize_partial_close should have reason parameter"


# ============================================================================
# Regression Tests: Previous iterations
# ============================================================================

class TestRegressionIteration27:
    """Regression tests for iteration 27 features."""

    def test_risk_guard_has_record_trade_method(self):
        """RiskGuard should have record_trade method."""
        guard = RiskGuard()
        assert hasattr(guard, "record_trade")
        assert callable(guard.record_trade)

    def test_risk_guard_has_can_trade_method(self):
        """RiskGuard should have can_trade method."""
        guard = RiskGuard()
        assert hasattr(guard, "can_trade")
        assert callable(guard.can_trade)

    def test_risk_guard_tracks_consecutive_losses(self):
        """RiskGuard should track consecutive losses."""
        guard = RiskGuard(
            min_loss_usdt_for_consecutive=0.0,
            ignore_consecutive_loss_reasons=[],
        )
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="hard_sl")
        assert guard._consecutive_losses == 1

    def test_risk_guard_resets_consecutive_on_win(self):
        """RiskGuard should reset consecutive losses on win."""
        guard = RiskGuard(
            min_loss_usdt_for_consecutive=0.0,
            ignore_consecutive_loss_reasons=[],
        )
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="hard_sl")
        guard.record_trade(0.5, symbol="ETHUSDT", reason="tp_hit")
        assert guard._consecutive_losses == 0


class TestRegressionIteration26:
    """Regression tests for iteration 26 features (adaptive regime presets)."""

    def test_config_has_adaptive_regime_presets(self):
        """Config should have adaptive_regime_presets section."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        assert "adaptive_regime_presets" in config


class TestRegressionIteration25:
    """Regression tests for iteration 25 features (strict HTF, volatility floor)."""

    def test_config_has_strict_htf_mode(self):
        """Config should have strict_htf_mode in entry section."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        assert "entry" in config
        assert "strict_htf_mode" in config["entry"]

    def test_config_has_volatility_floor(self):
        """Config should have volatility_floor settings in entry section."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        assert "volatility_floor_enabled" in config["entry"]
        assert "volatility_floor_atr_pct" in config["entry"]


# ============================================================================
# Edge Cases
# ============================================================================

class TestEdgeCases:
    """Test edge cases for the feature."""

    def test_empty_reason_string(self):
        """Empty reason string should not match ignore list."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.0,
            min_loss_usdt_for_consecutive=0.0,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="")

        assert guard._consecutive_losses == 1
        allowed, _ = guard.can_trade("BTCUSDT")
        assert allowed is False

    def test_none_reason(self):
        """None reason should not match ignore list."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.0,
            min_loss_usdt_for_consecutive=0.0,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        guard.record_trade(-1.0, symbol="BTCUSDT", reason=None)

        assert guard._consecutive_losses == 1

    def test_zero_loss_treated_as_loss(self):
        """Zero loss (breakeven) is treated as a loss in the system."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.0,
            min_loss_usdt_for_consecutive=0.0,
            ignore_loss_cooldown_reasons=[],
            ignore_consecutive_loss_reasons=[],
        )
        guard.record_trade(0.0, symbol="BTCUSDT", reason="breakeven")

        # Zero is not positive, so it's counted as a loss (breakeven = not a win)
        assert guard._consecutive_losses == 1
        assert guard.day_stats.losses == 1

    def test_positive_pnl_not_affected_by_reason(self):
        """Positive PnL should always reset consecutive regardless of reason."""
        guard = RiskGuard(
            cooldown_after_loss_sec=600,
            min_loss_usdt_for_cooldown=0.0,
            min_loss_usdt_for_consecutive=0.0,
            ignore_loss_cooldown_reasons=["early_exit"],
            ignore_consecutive_loss_reasons=["early_exit"],
        )
        # First a real loss
        guard.record_trade(-1.0, symbol="BTCUSDT", reason="hard_sl")
        assert guard._consecutive_losses == 1

        # Then a win with early_exit reason (unusual but possible)
        guard.record_trade(0.1, symbol="ETHUSDT", reason="early_exit")
        assert guard._consecutive_losses == 0  # Reset because positive


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
