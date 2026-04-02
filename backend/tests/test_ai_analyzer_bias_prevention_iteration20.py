#!/usr/bin/env python3
"""
Tests for AI Analyzer Direction Mismatch + Uniformity Bias Guard (Iteration 20)
Validates that the fixes prevent repeated SELL 85% blind acceptance pattern.

Features tested:
1. Direction mismatch detection (AI decision != proposed signal)
2. Uniformity confidence bias guard (same direction + near-identical confidence)
3. Config values for require_direction_match, uniformity_guard_enabled, uniformity_window, uniformity_conf_spread_max
4. Integration in main.py wiring
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import pytest
import yaml
from unittest.mock import MagicMock

from analysis.ai_analyzer import AITradeAnalyzer


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def analyzer():
    """Build fresh analyzer for each test with bias guards enabled."""
    a = AITradeAnalyzer()
    a.require_direction_match = True
    a.uniformity_guard_enabled = True
    a.uniformity_window = 4  # smaller window for faster tests
    a.uniformity_conf_spread_max = 3
    a._recent_ai.clear()
    return a


@pytest.fixture
def config():
    config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


# =============================================================================
# Test Config Values
# =============================================================================

class TestConfigValues:
    """Verify config.yaml has correct AI bias guard settings."""

    def test_require_direction_match_is_true(self, config):
        """ai.require_direction_match should be True."""
        assert config['ai']['require_direction_match'] is True

    def test_uniformity_guard_enabled_is_true(self, config):
        """ai.uniformity_guard_enabled should be True."""
        assert config['ai']['uniformity_guard_enabled'] is True

    def test_uniformity_window_is_8(self, config):
        """ai.uniformity_window should be 8."""
        assert config['ai']['uniformity_window'] == 8

    def test_uniformity_conf_spread_max_is_3(self, config):
        """ai.uniformity_conf_spread_max should be 3."""
        assert config['ai']['uniformity_conf_spread_max'] == 3

    def test_ai_enabled_is_true(self, config):
        """ai.enabled should be True (AI is kept enabled)."""
        assert config['ai']['enabled'] is True

    def test_ai_min_confidence_is_55(self, config):
        """ai.min_confidence should be 55."""
        assert config['ai']['min_confidence'] == 55

    def test_ai_fail_open_is_false(self, config):
        """ai.fail_open should be False (strict mode)."""
        assert config['ai']['fail_open'] is False


# =============================================================================
# Test Direction Mismatch Detection
# =============================================================================

class TestDirectionMismatch:
    """Validate _direction_mismatch detects when AI contradicts proposed signal."""

    def test_buy_vs_sell_detected(self, analyzer):
        """BUY proposed + SELL AI → mismatch detected."""
        assert analyzer._direction_mismatch("BUY", "SELL") is True

    def test_sell_vs_buy_detected(self, analyzer):
        """SELL proposed + BUY AI → mismatch detected."""
        assert analyzer._direction_mismatch("SELL", "BUY") is True

    def test_buy_aligned_not_detected(self, analyzer):
        """BUY proposed + BUY AI → no mismatch."""
        assert analyzer._direction_mismatch("BUY", "BUY") is False

    def test_sell_aligned_not_detected(self, analyzer):
        """SELL proposed + SELL AI → no mismatch."""
        assert analyzer._direction_mismatch("SELL", "SELL") is False

    def test_wait_ai_decision_not_mismatch(self, analyzer):
        """AI deciding WAIT is not a mismatch."""
        assert analyzer._direction_mismatch("BUY", "WAIT") is False
        assert analyzer._direction_mismatch("SELL", "WAIT") is False

    def test_neutral_proposed_not_mismatch(self, analyzer):
        """NEUTRAL proposed is not a mismatch regardless of AI."""
        assert analyzer._direction_mismatch("NEUTRAL", "SELL") is False
        assert analyzer._direction_mismatch("NEUTRAL", "BUY") is False

    def test_case_insensitive(self, analyzer):
        """Direction mismatch handles case variations."""
        assert analyzer._direction_mismatch("buy", "sell") is True
        assert analyzer._direction_mismatch("Buy", "Sell") is True
        assert analyzer._direction_mismatch("BUY", "sell") is True

    def test_none_values_handled(self, analyzer):
        """None values don't cause crash."""
        assert analyzer._direction_mismatch(None, "SELL") is False
        assert analyzer._direction_mismatch("BUY", None) is False
        assert analyzer._direction_mismatch(None, None) is False


# =============================================================================
# Test Uniform Bias Detection (Anti-Repeated SELL 85% Pattern)
# =============================================================================

class TestUniformBiasDetection:
    """Validate _uniform_bias_detected catches suspicious repeated pattern."""

    def test_repeated_sell_85_detected(self, analyzer):
        """SELL 85%, 85%, 85%, 85% → uniform bias detected (the exact user-reported issue)."""
        for _ in range(4):
            analyzer._record_ai_output("SELL", 85)
        assert analyzer._uniform_bias_detected() is True

    def test_repeated_sell_near_identical_conf_detected(self, analyzer):
        """SELL 84%, 85%, 86%, 85% (spread=2 ≤ 3) → detected."""
        analyzer._record_ai_output("SELL", 84)
        analyzer._record_ai_output("SELL", 85)
        analyzer._record_ai_output("SELL", 86)
        analyzer._record_ai_output("SELL", 85)
        assert analyzer._uniform_bias_detected() is True

    def test_repeated_buy_near_identical_conf_detected(self, analyzer):
        """BUY 70%, 71%, 70%, 72% (spread=2 ≤ 3) → detected."""
        analyzer._record_ai_output("BUY", 70)
        analyzer._record_ai_output("BUY", 71)
        analyzer._record_ai_output("BUY", 70)
        analyzer._record_ai_output("BUY", 72)
        assert analyzer._uniform_bias_detected() is True

    def test_wide_confidence_spread_not_detected(self, analyzer):
        """SELL 70%, 80%, 90%, 85% (spread=20 > 3) → NOT detected."""
        analyzer._record_ai_output("SELL", 70)
        analyzer._record_ai_output("SELL", 80)
        analyzer._record_ai_output("SELL", 90)
        analyzer._record_ai_output("SELL", 85)
        assert analyzer._uniform_bias_detected() is False

    def test_mixed_directions_not_detected(self, analyzer):
        """SELL, BUY, SELL, BUY → NOT detected (directions differ)."""
        analyzer._record_ai_output("SELL", 85)
        analyzer._record_ai_output("BUY", 85)
        analyzer._record_ai_output("SELL", 85)
        analyzer._record_ai_output("BUY", 85)
        assert analyzer._uniform_bias_detected() is False

    def test_not_detected_below_window_size(self, analyzer):
        """Only 3 records < window=4 → NOT detected."""
        analyzer._record_ai_output("SELL", 85)
        analyzer._record_ai_output("SELL", 85)
        analyzer._record_ai_output("SELL", 85)
        assert analyzer._uniform_bias_detected() is False

    def test_only_most_recent_window_checked(self, analyzer):
        """Older diverse data + recent uniform → detected."""
        # Old diverse data
        analyzer._record_ai_output("BUY", 60)
        analyzer._record_ai_output("SELL", 90)
        analyzer._record_ai_output("BUY", 75)
        # Recent uniform (window=4)
        analyzer._record_ai_output("SELL", 85)
        analyzer._record_ai_output("SELL", 84)
        analyzer._record_ai_output("SELL", 86)
        analyzer._record_ai_output("SELL", 85)
        assert analyzer._uniform_bias_detected() is True

    def test_wait_decisions_not_recorded(self, analyzer):
        """WAIT decisions are not recorded in _recent_ai."""
        analyzer._record_ai_output("WAIT", 50)
        assert len(analyzer._recent_ai) == 0

    def test_guard_disabled_returns_false(self, analyzer):
        """When guard is disabled, always returns False."""
        analyzer.uniformity_guard_enabled = False
        for _ in range(4):
            analyzer._record_ai_output("SELL", 85)
        assert analyzer._uniform_bias_detected() is False


# =============================================================================
# Test main.py Wiring
# =============================================================================

class TestMainPyWiring:
    """Verify main.py correctly wires AI config values."""

    def test_main_py_has_require_direction_match_wiring(self):
        """main.py should wire require_direction_match from config."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path, 'r') as f:
            content = f.read()
        assert 'ai_analyzer.require_direction_match' in content
        assert 'ai", "require_direction_match"' in content

    def test_main_py_has_uniformity_guard_enabled_wiring(self):
        """main.py should wire uniformity_guard_enabled from config."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path, 'r') as f:
            content = f.read()
        assert 'ai_analyzer.uniformity_guard_enabled' in content
        assert 'ai", "uniformity_guard_enabled"' in content

    def test_main_py_has_uniformity_window_wiring(self):
        """main.py should wire uniformity_window from config."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path, 'r') as f:
            content = f.read()
        assert 'ai_analyzer.uniformity_window' in content
        assert 'ai", "uniformity_window"' in content

    def test_main_py_has_uniformity_conf_spread_max_wiring(self):
        """main.py should wire uniformity_conf_spread_max from config."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path, 'r') as f:
            content = f.read()
        assert 'ai_analyzer.uniformity_conf_spread_max' in content
        assert 'ai", "uniformity_conf_spread_max"' in content


# =============================================================================
# Test AI Analyzer Methods Integration
# =============================================================================

class TestAnalyzerMethodsExist:
    """Verify AITradeAnalyzer has all required methods and attributes."""

    def test_analyzer_has_require_direction_match_attr(self, analyzer):
        assert hasattr(analyzer, 'require_direction_match')

    def test_analyzer_has_uniformity_guard_enabled_attr(self, analyzer):
        assert hasattr(analyzer, 'uniformity_guard_enabled')

    def test_analyzer_has_uniformity_window_attr(self, analyzer):
        assert hasattr(analyzer, 'uniformity_window')

    def test_analyzer_has_uniformity_conf_spread_max_attr(self, analyzer):
        assert hasattr(analyzer, 'uniformity_conf_spread_max')

    def test_analyzer_has_recent_ai_deque(self, analyzer):
        assert hasattr(analyzer, '_recent_ai')

    def test_analyzer_has_direction_mismatch_method(self, analyzer):
        assert hasattr(analyzer, '_direction_mismatch')
        assert callable(analyzer._direction_mismatch)

    def test_analyzer_has_record_ai_output_method(self, analyzer):
        assert hasattr(analyzer, '_record_ai_output')
        assert callable(analyzer._record_ai_output)

    def test_analyzer_has_uniform_bias_detected_method(self, analyzer):
        assert hasattr(analyzer, '_uniform_bias_detected')
        assert callable(analyzer._uniform_bias_detected)


# =============================================================================
# Test Scenario: Prevents SELL 85% Blind Acceptance
# =============================================================================

class TestPreventsSellBiasScenario:
    """Integration tests for the exact user-reported issue."""

    def test_direction_mismatch_prevents_wrong_direction_entry(self, analyzer):
        """
        Scenario: Strategy proposes BUY but AI says SELL → entry blocked.
        This prevents AI from overriding strategy direction.
        """
        proposed_signal = "BUY"
        ai_decision = "SELL"
        
        assert analyzer.require_direction_match is True
        mismatch = analyzer._direction_mismatch(proposed_signal, ai_decision)
        assert mismatch is True  # Should be blocked

    def test_uniformity_guard_prevents_repeated_pattern(self, analyzer):
        """
        Scenario: AI keeps returning SELL ~85% for every symbol → bias detected.
        After window fills with uniform data, entry blocked.
        """
        # Simulate AI returning SELL 85% for 8 consecutive symbols
        for i in range(8):
            analyzer._record_ai_output("SELL", 85 + (i % 3) - 1)  # 84, 85, 86, 84, 85, 86...
        
        # The last window (4 items) should trigger bias detection
        assert analyzer._uniform_bias_detected() is True

    def test_healthy_ai_variance_allowed(self, analyzer):
        """
        Scenario: AI returns diverse decisions with varying confidence → allowed.
        """
        analyzer._record_ai_output("BUY", 75)
        analyzer._record_ai_output("SELL", 60)
        analyzer._record_ai_output("BUY", 82)
        analyzer._record_ai_output("SELL", 55)
        
        # Diverse directions and wide confidence spread → not detected as bias
        assert analyzer._uniform_bias_detected() is False

    def test_aligned_directions_with_varied_confidence_allowed(self, analyzer):
        """
        Scenario: AI returns same direction but with naturally varying confidence → allowed.
        """
        analyzer._record_ai_output("SELL", 55)
        analyzer._record_ai_output("SELL", 78)
        analyzer._record_ai_output("SELL", 62)
        analyzer._record_ai_output("SELL", 90)
        
        # Same direction but spread = 35 > 3 → not detected as bias
        assert analyzer._uniform_bias_detected() is False


# =============================================================================
# Test Edge Cases
# =============================================================================

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_confidence_exactly_at_spread_boundary(self, analyzer):
        """Confidence spread exactly at max (3) → detected."""
        analyzer._record_ai_output("SELL", 82)
        analyzer._record_ai_output("SELL", 83)
        analyzer._record_ai_output("SELL", 84)
        analyzer._record_ai_output("SELL", 85)  # spread = 85-82 = 3
        assert analyzer._uniform_bias_detected() is True

    def test_confidence_just_over_spread_boundary(self, analyzer):
        """Confidence spread just over max (4 > 3) → NOT detected."""
        analyzer._record_ai_output("SELL", 81)
        analyzer._record_ai_output("SELL", 83)
        analyzer._record_ai_output("SELL", 84)
        analyzer._record_ai_output("SELL", 85)  # spread = 85-81 = 4 > 3
        assert analyzer._uniform_bias_detected() is False

    def test_window_size_1_not_useful(self, analyzer):
        """Window size 1 should not detect bias (always returns False)."""
        analyzer.uniformity_window = 1
        analyzer._record_ai_output("SELL", 85)
        assert analyzer._uniform_bias_detected() is False

    def test_zero_confidence_recorded(self, analyzer):
        """Zero confidence values are properly recorded and checked."""
        analyzer._record_ai_output("SELL", 0)
        analyzer._record_ai_output("SELL", 1)
        analyzer._record_ai_output("SELL", 2)
        analyzer._record_ai_output("SELL", 0)  # spread = 2
        assert analyzer._uniform_bias_detected() is True

    def test_100_confidence_recorded(self, analyzer):
        """100% confidence values are properly recorded."""
        for _ in range(4):
            analyzer._record_ai_output("BUY", 100)
        assert analyzer._uniform_bias_detected() is True


# =============================================================================
# Test Default Values
# =============================================================================

class TestDefaultValues:
    """Verify default values in AITradeAnalyzer.__init__."""

    def test_default_require_direction_match(self):
        """Default require_direction_match should be True."""
        analyzer = AITradeAnalyzer()
        assert analyzer.require_direction_match is True

    def test_default_uniformity_guard_enabled(self):
        """Default uniformity_guard_enabled should be True."""
        analyzer = AITradeAnalyzer()
        assert analyzer.uniformity_guard_enabled is True

    def test_default_uniformity_window(self):
        """Default uniformity_window should be 8."""
        analyzer = AITradeAnalyzer()
        assert analyzer.uniformity_window == 8

    def test_default_uniformity_conf_spread_max(self):
        """Default uniformity_conf_spread_max should be 3."""
        analyzer = AITradeAnalyzer()
        assert analyzer.uniformity_conf_spread_max == 3


# =============================================================================
# Run tests directly
# =============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
