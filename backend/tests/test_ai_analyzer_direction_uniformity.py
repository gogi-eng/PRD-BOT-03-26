#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from analysis.ai_analyzer import AITradeAnalyzer


def _build_analyzer() -> AITradeAnalyzer:
    analyzer = AITradeAnalyzer()
    analyzer.require_direction_match = True
    analyzer.uniformity_guard_enabled = True
    analyzer.uniformity_window = 4
    analyzer.uniformity_conf_spread_max = 2
    analyzer._recent_ai.clear()
    return analyzer


def test_direction_mismatch_detected_buy_vs_sell():
    analyzer = _build_analyzer()
    assert analyzer._direction_mismatch("BUY", "SELL") is True


def test_direction_mismatch_not_detected_when_aligned():
    analyzer = _build_analyzer()
    assert analyzer._direction_mismatch("SELL", "SELL") is False


def test_direction_mismatch_ignores_wait_or_neutral():
    analyzer = _build_analyzer()
    assert analyzer._direction_mismatch("BUY", "WAIT") is False
    assert analyzer._direction_mismatch("NEUTRAL", "SELL") is False


def test_uniform_bias_detected_same_side_and_near_confidence():
    analyzer = _build_analyzer()
    analyzer._record_ai_output("SELL", 85)
    analyzer._record_ai_output("SELL", 84)
    analyzer._record_ai_output("SELL", 86)
    analyzer._record_ai_output("SELL", 85)
    assert analyzer._uniform_bias_detected() is True


def test_uniform_bias_not_detected_when_confidence_spread_wide():
    analyzer = _build_analyzer()
    analyzer._record_ai_output("SELL", 70)
    analyzer._record_ai_output("SELL", 80)
    analyzer._record_ai_output("SELL", 90)
    analyzer._record_ai_output("SELL", 85)
    assert analyzer._uniform_bias_detected() is False


def test_uniform_bias_not_detected_when_mixed_directions():
    analyzer = _build_analyzer()
    analyzer._record_ai_output("SELL", 85)
    analyzer._record_ai_output("BUY", 85)
    analyzer._record_ai_output("SELL", 85)
    analyzer._record_ai_output("BUY", 85)
    assert analyzer._uniform_bias_detected() is False
