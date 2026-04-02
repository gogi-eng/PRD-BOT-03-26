#!/usr/bin/env python3
"""
Tests for LYNUSDT hotfix quality gate tightening (Iteration 19).

User-approved hotfix choices after losing SELL LYNUSDT:
1. Soft countertrend rule: allow only with high confidence (>=0.82) + strong imbalance (>=0.20)
2. no_zone allowed only with elevated confidence (>=0.84)
3. No temporary blacklist (config should NOT have LYNUSDT blacklisted)
4. Tighten quality gate: min_expected_edge=0.68, min_abs_imbalance=0.10

Tests cover:
- Config values match hotfix requirements
- Countertrend soft filter rejects weak countertrend signals (LYNUSDT SELL scenario)
- no_zone filter rejects low-confidence entries without valid zone
- Quality gate processes signals correctly before Telegram pass-through
- No regression in existing quality gate / SL / trained model tests
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from engine.entry_engine import EntrySignal
from main import TradingBot


# ============== HELPER: Build TradingBot with Hotfix Config ================

def _build_hotfix_bot() -> TradingBot:
    """Build TradingBot with LYNUSDT hotfix quality gate settings (from config.yaml)."""
    bot = TradingBot.__new__(TradingBot)
    # Config values from config.yaml quality_gate section (iteration 19 hotfix)
    bot.quality_gate_enabled = True
    bot.quality_gate_min_confidence = 0.64  # from config
    bot.quality_gate_min_expected_edge = 0.68  # HOTFIX: tightened
    bot.quality_gate_min_adx = 14.5  # from config
    bot.quality_gate_min_atr_pct = 0.16  # from config
    bot.quality_gate_min_abs_imbalance = 0.10  # HOTFIX: tightened
    bot.quality_gate_allow_chop = False
    bot.quality_gate_require_htf_trend = False
    # Countertrend soft filter: high confidence + strong imbalance
    bot.quality_gate_countertrend_min_confidence = 0.82  # HOTFIX
    bot.quality_gate_countertrend_min_abs_imbalance = 0.20  # HOTFIX
    # no_zone elevated confidence
    bot.quality_gate_no_zone_min_confidence = 0.84  # HOTFIX
    return bot


def _make_signal(
    side: str = "BUY",
    confidence: float = 0.90,
    rr_ratio: float = 3.0,
    regime: str = "trend",
    adx: float = 30.0,
    atr_pct: float = 0.6,
    htf_trend: str = "up",
    htf_4h_trend: int = 1,
    entry_zone: str = "fvg_bullish",
    normalized_imbalance: float = 0.35,
    trained_model_prob: float = None,
) -> EntrySignal:
    """Create EntrySignal for testing."""
    metadata = {
        "regime": regime,
        "adx": adx,
        "atr_pct": atr_pct,
        "htf_trend": htf_trend,
        "htf_4h_trend": htf_4h_trend,
        "entry_zone": entry_zone,
        "normalized_imbalance": normalized_imbalance,
    }
    if trained_model_prob is not None:
        metadata["trained_model_prob"] = trained_model_prob
    
    return EntrySignal(
        should_enter=True,
        side=side,
        confidence=confidence,
        rr_ratio=rr_ratio,
        metadata=metadata,
    )


# ============== TEST: Config Values Match Hotfix Requirements ==============

class TestConfigHotfixValues:
    """Verify config.yaml has correct hotfix values."""
    
    def test_min_expected_edge_is_0_68(self):
        """min_expected_edge should be 0.68 (tightened from 0.75)."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "min_expected_edge: 0.68" in content
    
    def test_anti_flat_min_abs_imbalance_is_0_10(self):
        """anti_flat_min_abs_imbalance should be 0.10 (tightened from 0.08)."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "anti_flat_min_abs_imbalance: 0.10" in content
    
    def test_countertrend_min_confidence_is_0_82(self):
        """countertrend_min_confidence should be 0.82."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "countertrend_min_confidence: 0.82" in content
    
    def test_countertrend_min_abs_imbalance_is_0_20(self):
        """countertrend_min_abs_imbalance should be 0.20."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "countertrend_min_abs_imbalance: 0.20" in content
    
    def test_no_zone_min_confidence_is_0_84(self):
        """no_zone_min_confidence should be 0.84."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "no_zone_min_confidence: 0.84" in content
    
    def test_lynusdt_not_in_blacklist(self):
        """LYNUSDT should NOT be in blacklist (no temporary blacklist)."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        # Check blacklist_symbols line does not contain LYNUSDT
        assert "LYNUSDT" not in content


# ============== TEST: Soft Countertrend Filter (LYNUSDT SELL Scenario) =====

class TestSoftCountertrendFilter:
    """Tests for countertrend soft filter: only allow with high conf + strong imbalance."""
    
    def test_rejects_sell_against_bullish_4h_with_low_confidence(self):
        """SELL against bullish 4H trend should be rejected if confidence < 0.82."""
        bot = _build_hotfix_bot()
        # LYNUSDT scenario: SELL (short) against bullish 4H trend with insufficient confidence
        signal = _make_signal(
            side="SELL",
            confidence=0.79,  # Below 0.82 threshold
            rr_ratio=3.0,
            htf_4h_trend=1,  # Bullish 4H
            entry_zone="ob_bearish",
            normalized_imbalance=-0.35,  # Strong bearish imbalance
        )
        ok, reason, _ = bot._passes_signal_quality_gate("LYNUSDT", signal)
        assert ok is False
        assert reason == "countertrend_low_confidence"
    
    def test_rejects_sell_against_bullish_4h_with_weak_imbalance(self):
        """SELL against bullish 4H trend should be rejected if |imbalance| < 0.20."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="SELL",
            confidence=0.85,  # High confidence, passes first check
            rr_ratio=3.0,
            htf_4h_trend=1,  # Bullish 4H
            entry_zone="ob_bearish",
            normalized_imbalance=-0.15,  # Weak imbalance (|0.15| < 0.20)
        )
        ok, reason, _ = bot._passes_signal_quality_gate("LYNUSDT", signal)
        assert ok is False
        assert reason == "countertrend_weak_imbalance"
    
    def test_allows_sell_against_bullish_4h_with_high_conf_and_strong_imbalance(self):
        """SELL against bullish 4H trend passes with high conf + strong imbalance."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="SELL",
            confidence=0.85,  # >= 0.82
            rr_ratio=3.0,
            htf_4h_trend=1,  # Bullish 4H (countertrend)
            entry_zone="ob_bearish",
            normalized_imbalance=-0.35,  # |0.35| >= 0.20
        )
        ok, reason, _ = bot._passes_signal_quality_gate("LYNUSDT", signal)
        assert ok is True
        assert reason == "ok"
    
    def test_rejects_buy_against_bearish_4h_with_low_confidence(self):
        """BUY against bearish 4H trend should be rejected if confidence < 0.82."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.78,  # Below 0.82
            rr_ratio=3.0,
            htf_4h_trend=-1,  # Bearish 4H
            entry_zone="fvg_bullish",
            normalized_imbalance=0.40,  # Strong bullish imbalance
        )
        ok, reason, _ = bot._passes_signal_quality_gate("ETHUSDT", signal)
        assert ok is False
        assert reason == "countertrend_low_confidence"
    
    def test_allows_buy_against_bearish_4h_with_high_conf_and_strong_imbalance(self):
        """BUY against bearish 4H trend passes with high conf + strong imbalance."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.88,  # >= 0.82
            rr_ratio=3.0,
            htf_4h_trend=-1,  # Bearish 4H (countertrend)
            entry_zone="fvg_bullish",
            normalized_imbalance=0.30,  # |0.30| >= 0.20
        )
        ok, reason, _ = bot._passes_signal_quality_gate("ETHUSDT", signal)
        assert ok is True
    
    def test_no_countertrend_filter_when_4h_trend_neutral(self):
        """Countertrend filter is skipped when 4H trend is neutral (0)."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="SELL",
            confidence=0.70,  # Below countertrend threshold but OK for neutral
            rr_ratio=3.0,
            htf_4h_trend=0,  # Neutral 4H
            entry_zone="ob_bearish",
            normalized_imbalance=-0.15,  # Would fail countertrend imbalance
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        # Should NOT fail countertrend checks (only standard checks apply)
        assert reason != "countertrend_low_confidence"
        assert reason != "countertrend_weak_imbalance"
    
    def test_no_countertrend_filter_for_trend_aligned_trade(self):
        """Countertrend filter is skipped when trade aligns with 4H trend."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",  # Long
            confidence=0.70,
            rr_ratio=3.0,
            htf_4h_trend=1,  # Bullish 4H - trade is aligned
            entry_zone="fvg_bullish",
            normalized_imbalance=0.12,  # Would fail countertrend imbalance
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        # Should NOT fail countertrend checks (trade is aligned with trend)
        assert reason != "countertrend_low_confidence"
        assert reason != "countertrend_weak_imbalance"


# ============== TEST: no_zone Elevated Confidence Filter ===================

class TestNoZoneElevatedConfidenceFilter:
    """Tests for no_zone entries requiring elevated confidence >= 0.84."""
    
    def test_rejects_no_zone_with_low_confidence(self):
        """no_zone entries should be rejected if confidence < 0.84."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.80,  # Below 0.84
            rr_ratio=3.0,
            entry_zone="no_zone",
            htf_4h_trend=1,  # Aligned with trend
        )
        ok, reason, _ = bot._passes_signal_quality_gate("SOLUSDT", signal)
        assert ok is False
        assert reason == "no_zone_low_confidence"
    
    def test_allows_no_zone_with_high_confidence(self):
        """no_zone entries pass with confidence >= 0.84."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.86,  # >= 0.84
            rr_ratio=3.0,
            entry_zone="no_zone",
            htf_4h_trend=1,
        )
        ok, reason, _ = bot._passes_signal_quality_gate("SOLUSDT", signal)
        assert ok is True
    
    def test_allows_valid_zone_with_lower_confidence(self):
        """Valid zones (fvg, ob) can pass with lower confidence."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.75,  # Would fail no_zone check, but zone is valid
            rr_ratio=3.0,
            entry_zone="fvg_bullish",  # Valid zone
            htf_4h_trend=1,
        )
        ok, reason, _ = bot._passes_signal_quality_gate("LINKUSDT", signal)
        # Should NOT fail no_zone check
        assert reason != "no_zone_low_confidence"
    
    def test_no_zone_boundary_at_0_84(self):
        """Confidence exactly at 0.84 should pass no_zone check."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.84,  # Exactly at threshold
            rr_ratio=3.0,
            entry_zone="no_zone",
            htf_4h_trend=1,
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        # Should pass no_zone check (expected edge might still fail)
        assert reason != "no_zone_low_confidence"


# ============== TEST: Quality Gate Expected Edge Threshold =================

class TestExpectedEdgeThreshold068:
    """Tests for min_expected_edge = 0.68 threshold."""
    
    def test_rejects_expected_edge_below_0_68(self):
        """Expected edge < 0.68 should be rejected."""
        bot = _build_hotfix_bot()
        # confidence=0.72, rr=1.5 -> 0.72 * 2.5 - 1.0 = 0.8 (passes)
        # confidence=0.70, rr=1.2 -> 0.70 * 2.2 - 1.0 = 0.54 (fails)
        signal = _make_signal(
            confidence=0.70,
            rr_ratio=1.2,
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_expected_edge"
        assert meta["quality_expected_edge"] < 0.68
    
    def test_passes_expected_edge_at_0_68(self):
        """Expected edge >= 0.68 should pass this check."""
        bot = _build_hotfix_bot()
        # Need confidence * (rr + 1) - 1 >= 0.68
        # confidence=0.75, rr=1.5 -> 0.75 * 2.5 - 1.0 = 0.875 (passes)
        signal = _make_signal(
            confidence=0.75,
            rr_ratio=1.5,
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert meta["quality_expected_edge"] >= 0.68
        # May still fail other checks
    
    def test_expected_edge_formula_correct(self):
        """Expected edge = prob * (rr + 1) - 1."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            confidence=0.80,
            rr_ratio=2.5,
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        # 0.80 * 3.5 - 1.0 = 1.8
        assert abs(meta["quality_expected_edge"] - 1.8) < 0.001


# ============== TEST: Tightened Imbalance Threshold ========================

class TestTightenedImbalanceThreshold:
    """Tests for anti_flat_min_abs_imbalance = 0.10 threshold."""
    
    def test_rejects_imbalance_below_0_10(self):
        """Imbalance with |value| < 0.10 should be rejected."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            confidence=0.90,
            rr_ratio=3.0,
            normalized_imbalance=0.08,  # |0.08| < 0.10
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "flat_orderflow"
    
    def test_passes_imbalance_at_0_10(self):
        """Imbalance with |value| >= 0.10 should pass this check."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            confidence=0.90,
            rr_ratio=3.0,
            normalized_imbalance=0.12,  # |0.12| >= 0.10
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        # Should NOT fail flat_orderflow
        assert reason != "flat_orderflow"
    
    def test_negative_imbalance_uses_absolute_value(self):
        """Negative imbalance -0.12 should pass (|−0.12| = 0.12 >= 0.10)."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            confidence=0.90,
            rr_ratio=3.0,
            normalized_imbalance=-0.12,
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert reason != "flat_orderflow"


# ============== TEST: Full LYNUSDT Scenario Simulation =====================

class TestLynusdtScenarioSimulation:
    """End-to-end tests simulating the LYNUSDT SELL loss scenario."""
    
    def test_weak_lynusdt_sell_rejected(self):
        """Weak SELL LYNUSDT signal (like the lost trade) should be rejected."""
        bot = _build_hotfix_bot()
        # Simulating weak SELL LYNUSDT: countertrend, low confidence, weak imbalance
        signal = _make_signal(
            side="SELL",
            confidence=0.75,  # Below countertrend threshold
            rr_ratio=2.8,
            htf_4h_trend=1,  # Bullish 4H (countertrend)
            entry_zone="ob_bearish",
            normalized_imbalance=-0.18,  # Weak imbalance
        )
        ok, reason, _ = bot._passes_signal_quality_gate("LYNUSDT", signal)
        assert ok is False
        # Should fail countertrend check
        assert reason in ("countertrend_low_confidence", "countertrend_weak_imbalance")
    
    def test_strong_lynusdt_sell_allowed(self):
        """Strong SELL LYNUSDT signal (high conf + strong imbalance) should pass."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="SELL",
            confidence=0.87,  # Above countertrend threshold
            rr_ratio=3.2,
            htf_4h_trend=1,  # Bullish 4H (countertrend)
            entry_zone="ob_bearish",
            normalized_imbalance=-0.35,  # Strong imbalance
        )
        ok, reason, meta = bot._passes_signal_quality_gate("LYNUSDT", signal)
        assert ok is True
        assert reason == "ok"
        assert meta["quality_expected_edge"] > 0.68


# ============== TEST: Order of Filter Checks (Regression) ==================

class TestFilterOrderRegression:
    """Ensure filters are checked in correct order."""
    
    def test_confidence_checked_first(self):
        """Low confidence should be caught before countertrend/no_zone checks."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="SELL",
            confidence=0.50,  # Very low - should fail first
            rr_ratio=3.0,
            htf_4h_trend=1,
            entry_zone="no_zone",
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_confidence"
    
    def test_countertrend_checked_before_no_zone(self):
        """Countertrend filter should be checked before no_zone filter."""
        bot = _build_hotfix_bot()
        # Signal that fails both countertrend AND no_zone
        signal = _make_signal(
            side="SELL",
            confidence=0.80,  # Fails countertrend (< 0.82) but passes general
            rr_ratio=3.0,
            htf_4h_trend=1,  # Countertrend
            entry_zone="no_zone",  # Would also fail no_zone
            normalized_imbalance=-0.30,  # Strong imbalance
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        # Should fail countertrend check first (it's checked before no_zone in code)
        assert reason == "countertrend_low_confidence"
    
    def test_no_zone_checked_before_expected_edge(self):
        """no_zone filter should be checked before expected_edge filter."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.80,  # Fails no_zone (< 0.84) 
            rr_ratio=3.0,  # Would pass expected_edge
            htf_4h_trend=1,  # Aligned (no countertrend)
            entry_zone="no_zone",
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "no_zone_low_confidence"


# ============== TEST: No Regression in Standard Filters ====================

class TestStandardFiltersRegression:
    """Ensure standard quality gate filters still work."""
    
    def test_chop_regime_still_rejected(self):
        """Chop regime should still be rejected."""
        bot = _build_hotfix_bot()
        signal = _make_signal(regime="chop")
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "chop_regime"
    
    def test_low_adx_still_rejected(self):
        """Low ADX should still be rejected."""
        bot = _build_hotfix_bot()
        signal = _make_signal(adx=10.0)  # Below 14.5
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_adx"
    
    def test_low_atr_pct_still_rejected(self):
        """Low ATR% should still be rejected."""
        bot = _build_hotfix_bot()
        signal = _make_signal(atr_pct=0.10)  # Below 0.16
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_atr"
    
    def test_strong_signal_passes_all_checks(self):
        """Strong signal should pass all quality gate checks."""
        bot = _build_hotfix_bot()
        signal = _make_signal(
            side="BUY",
            confidence=0.90,
            rr_ratio=3.5,
            regime="trend",
            adx=35.0,
            atr_pct=0.8,
            htf_trend="up",
            htf_4h_trend=1,
            entry_zone="fvg_bullish",
            normalized_imbalance=0.45,
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
        assert reason == "ok"
        assert meta["quality_expected_edge"] > 0.68
