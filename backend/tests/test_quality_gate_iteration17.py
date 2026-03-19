#!/usr/bin/env python3
"""
Comprehensive tests for Quality Gate feature in Signal-Only mode.

Tests cover:
1. Quality gate is applied before signal reaches Telegram send path
2. Expected edge threshold filter works and rejects weak edge
3. Anti-flat filters: regime chop / low ADX / low ATR / low imbalance
4. Quality gate metadata (quality_expected_edge) is added for passed signals
5. Config-driven behavior via quality_gate section in config.yaml
6. No regressions in feedback-loop, SL guards, trained-model integration
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from engine.entry_engine import EntrySignal
from main import TradingBot


# ============== HELPER: Build TradingBot with Quality Gate ================

def _build_quality_gate_bot(overrides: dict = None) -> TradingBot:
    """Build a TradingBot instance with quality gate attributes configured."""
    bot = TradingBot.__new__(TradingBot)
    # Default config from config.yaml quality_gate section
    bot.quality_gate_enabled = True
    bot.quality_gate_min_confidence = 0.68
    bot.quality_gate_min_expected_edge = 0.75
    bot.quality_gate_min_adx = 16.0
    bot.quality_gate_min_atr_pct = 0.20
    bot.quality_gate_min_abs_imbalance = 0.08
    bot.quality_gate_allow_chop = False
    bot.quality_gate_require_htf_trend = False
    bot.quality_gate_countertrend_min_confidence = 0.82
    bot.quality_gate_countertrend_min_abs_imbalance = 0.20
    bot.quality_gate_no_zone_min_confidence = 0.84
    
    if overrides:
        for key, value in overrides.items():
            setattr(bot, key, value)
    return bot


def _make_signal(
    confidence: float = 0.90,
    rr_ratio: float = 3.0,
    side: str = "BUY",
    regime: str = "trend",
    adx: float = 30.0,
    atr_pct: float = 0.6,
    htf_trend: str = "up",
    htf_4h_trend: int = 1,
    entry_zone: str = "fvg_bullish",
    normalized_imbalance: float = 0.4,
    trained_model_prob: float = None,
) -> EntrySignal:
    """Create an EntrySignal with configurable metadata for testing."""
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


# ============== TEST: Expected Edge Threshold Filter ======================

class TestExpectedEdgeThresholdFilter:
    """Tests for expected edge calculation and filtering."""
    
    def test_expected_edge_formula_without_trained_model(self):
        """Expected edge = confidence * (rr + 1) - 1."""
        bot = _build_quality_gate_bot()
        # confidence=0.90, rr=3.0 -> 0.90 * 4.0 - 1.0 = 2.6
        signal = _make_signal(confidence=0.90, rr_ratio=3.0)
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
        assert reason == "ok"
        assert abs(meta["quality_expected_edge"] - 2.6) < 0.001
    
    def test_expected_edge_formula_with_trained_model(self):
        """Expected edge uses trained_model_prob when available."""
        bot = _build_quality_gate_bot()
        # trained_model_prob=0.75, rr=3.0 -> 0.75 * 4.0 - 1.0 = 2.0
        signal = _make_signal(confidence=0.90, rr_ratio=3.0, trained_model_prob=0.75)
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
        assert abs(meta["quality_expected_edge"] - 2.0) < 0.001
    
    def test_rejects_low_expected_edge(self):
        """Signals with edge < min_expected_edge are rejected."""
        bot = _build_quality_gate_bot()
        # confidence=0.70, rr=1.2 -> 0.70 * 2.2 - 1.0 = 0.54 < 0.75
        signal = _make_signal(confidence=0.70, rr_ratio=1.2)
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_expected_edge"
        assert meta["quality_expected_edge"] < 0.75
    
    def test_passes_sufficient_expected_edge(self):
        """Signals with edge >= min_expected_edge pass this check."""
        bot = _build_quality_gate_bot()
        # confidence=0.80, rr=2.5 -> 0.80 * 3.5 - 1.0 = 1.8 > 0.75
        signal = _make_signal(confidence=0.80, rr_ratio=2.5)
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
        assert meta["quality_expected_edge"] >= 0.75
    
    def test_boundary_expected_edge_exactly_at_threshold(self):
        """Edge exactly at threshold should pass."""
        bot = _build_quality_gate_bot({"quality_gate_min_expected_edge": 1.0})
        # Need confidence * (rr + 1) - 1 = 1.0 -> confidence=0.80, rr=1.5 -> 0.80*2.5-1=1.0
        signal = _make_signal(confidence=0.80, rr_ratio=1.5)
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
        assert abs(meta["quality_expected_edge"] - 1.0) < 0.001


# ============== TEST: Anti-Flat Filters ===================================

class TestAntiFlatFilters:
    """Tests for anti-flat filters: chop regime, low ADX, low ATR, low imbalance."""
    
    def test_rejects_chop_regime_when_not_allowed(self):
        """Chop regime is rejected when anti_flat_allow_chop=False."""
        bot = _build_quality_gate_bot({"quality_gate_allow_chop": False})
        signal = _make_signal(regime="chop")
        ok, reason, _ = bot._passes_signal_quality_gate("ETHUSDT", signal)
        assert ok is False
        assert reason == "chop_regime"
    
    def test_allows_chop_regime_when_enabled(self):
        """Chop regime passes when anti_flat_allow_chop=True."""
        bot = _build_quality_gate_bot({"quality_gate_allow_chop": True})
        signal = _make_signal(regime="chop")
        ok, reason, _ = bot._passes_signal_quality_gate("ETHUSDT", signal)
        assert ok is True
        assert reason == "ok"
    
    def test_rejects_low_adx(self):
        """Signals with ADX below threshold are rejected."""
        bot = _build_quality_gate_bot({"quality_gate_min_adx": 16.0})
        signal = _make_signal(adx=12.0)  # Below 16.0
        ok, reason, _ = bot._passes_signal_quality_gate("SOLUSDT", signal)
        assert ok is False
        assert reason == "low_adx"
    
    def test_passes_sufficient_adx(self):
        """Signals with ADX at or above threshold pass."""
        bot = _build_quality_gate_bot({"quality_gate_min_adx": 16.0})
        signal = _make_signal(adx=20.0)  # Above 16.0
        ok, reason, _ = bot._passes_signal_quality_gate("SOLUSDT", signal)
        assert ok is True
    
    def test_rejects_low_atr_pct(self):
        """Signals with ATR% below threshold are rejected."""
        bot = _build_quality_gate_bot({"quality_gate_min_atr_pct": 0.20})
        signal = _make_signal(atr_pct=0.10)  # Below 0.20
        ok, reason, _ = bot._passes_signal_quality_gate("LINKUSDT", signal)
        assert ok is False
        assert reason == "low_atr"
    
    def test_passes_sufficient_atr_pct(self):
        """Signals with ATR% at or above threshold pass."""
        bot = _build_quality_gate_bot({"quality_gate_min_atr_pct": 0.20})
        signal = _make_signal(atr_pct=0.35)  # Above 0.20
        ok, reason, _ = bot._passes_signal_quality_gate("LINKUSDT", signal)
        assert ok is True
    
    def test_rejects_flat_orderflow_low_imbalance(self):
        """Signals with low absolute imbalance are rejected."""
        bot = _build_quality_gate_bot({"quality_gate_min_abs_imbalance": 0.08})
        signal = _make_signal(normalized_imbalance=0.03)  # |0.03| < 0.08
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "flat_orderflow"
    
    def test_passes_sufficient_imbalance(self):
        """Signals with absolute imbalance at or above threshold pass."""
        bot = _build_quality_gate_bot({"quality_gate_min_abs_imbalance": 0.08})
        signal = _make_signal(normalized_imbalance=0.15)  # |0.15| > 0.08
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
    
    def test_negative_imbalance_uses_absolute_value(self):
        """Negative imbalance should use absolute value for comparison."""
        bot = _build_quality_gate_bot({"quality_gate_min_abs_imbalance": 0.08})
        signal = _make_signal(normalized_imbalance=-0.20)  # |-0.20| = 0.20 > 0.08
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
    
    def test_rejects_flat_htf_trend_when_required(self):
        """Rejects neutral HTF trend when require_htf_trend=True."""
        bot = _build_quality_gate_bot({"quality_gate_require_htf_trend": True})
        for flat_trend in ["neutral", "flat", "range", "sideways"]:
            signal = _make_signal(htf_trend=flat_trend)
            ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
            assert ok is False
            assert reason == "flat_htf_trend"
    
    def test_allows_flat_htf_trend_when_not_required(self):
        """Flat HTF trend passes when require_htf_trend=False."""
        bot = _build_quality_gate_bot({"quality_gate_require_htf_trend": False})
        signal = _make_signal(htf_trend="neutral")
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True


# ============== TEST: Low Confidence Rejection ============================

class TestLowConfidenceRejection:
    """Tests for confidence threshold filtering."""
    
    def test_rejects_low_confidence(self):
        """Signals with confidence below threshold are rejected early."""
        bot = _build_quality_gate_bot({"quality_gate_min_confidence": 0.68})
        signal = _make_signal(confidence=0.55)  # Below 0.68
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_confidence"
    
    def test_passes_sufficient_confidence(self):
        """Signals with confidence at or above threshold pass this check."""
        bot = _build_quality_gate_bot({"quality_gate_min_confidence": 0.68})
        signal = _make_signal(confidence=0.75)  # Above 0.68
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
    
    def test_confidence_checked_before_anti_flat(self):
        """Low confidence is rejected before checking anti-flat filters."""
        bot = _build_quality_gate_bot({"quality_gate_min_confidence": 0.68})
        # Even with good anti-flat metrics, low confidence fails first
        signal = _make_signal(confidence=0.50, adx=50.0, atr_pct=2.0)
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_confidence"


# ============== TEST: Quality Gate Metadata for Passed Signals ============

class TestQualityGateMetadata:
    """Tests for metadata added to passed signals."""
    
    def test_passed_signal_contains_expected_edge(self):
        """Passed signals include quality_expected_edge in metadata."""
        bot = _build_quality_gate_bot()
        signal = _make_signal(confidence=0.90, rr_ratio=3.0)
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is True
        assert "quality_expected_edge" in meta
        assert meta["quality_expected_edge"] > 0
    
    def test_passed_signal_contains_symbol(self):
        """Passed signals include quality_gate_symbol in metadata."""
        bot = _build_quality_gate_bot()
        signal = _make_signal()
        ok, reason, meta = bot._passes_signal_quality_gate("SOLUSDT", signal)
        assert ok is True
        assert meta.get("quality_gate_symbol") == "SOLUSDT"
    
    def test_rejected_signal_still_has_expected_edge(self):
        """Even rejected signals return expected_edge for logging."""
        bot = _build_quality_gate_bot()
        signal = _make_signal(confidence=0.50)  # Low confidence
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert "quality_expected_edge" in meta


# ============== TEST: Config-Driven Behavior ==============================

class TestConfigDrivenBehavior:
    """Tests verifying config.yaml drives quality gate behavior."""
    
    def test_config_yaml_has_quality_gate_section(self):
        """config.yaml contains quality_gate section."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "quality_gate:" in content
    
    def test_config_contains_enabled_setting(self):
        """config.yaml has enabled: true in quality_gate."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "enabled: true" in content
    
    def test_config_contains_min_confidence(self):
        """config.yaml has min_confidence in quality_gate."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "min_confidence:" in content
    
    def test_config_contains_min_expected_edge(self):
        """config.yaml has min_expected_edge in quality_gate."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "min_expected_edge:" in content
    
    def test_config_contains_anti_flat_settings(self):
        """config.yaml has all anti_flat_* settings."""
        config_path = Path(__file__).parent.parent.parent / "bot" / "config.yaml"
        content = config_path.read_text()
        assert "anti_flat_min_adx:" in content
        assert "anti_flat_min_atr_pct:" in content
        assert "anti_flat_min_abs_imbalance:" in content
        assert "anti_flat_allow_chop:" in content
        assert "anti_flat_require_htf_trend:" in content


# ============== TEST: Quality Gate Applied in Signal-Only Mode ============

class TestQualityGateAppliedInSignalOnlyMode:
    """Tests verifying quality gate is checked in _scan_entries for signal_only."""
    
    def test_scan_entries_checks_quality_gate_in_signal_only(self):
        """_scan_entries has quality gate check for signal_only mode."""
        main_path = Path(__file__).parent.parent.parent / "bot" / "main.py"
        content = main_path.read_text()
        # Check that quality gate is invoked inside _scan_entries
        assert "if self.signal_only and self.quality_gate_enabled:" in content
        assert "_passes_signal_quality_gate" in content
    
    def test_quality_gate_rejection_is_logged(self):
        """Quality gate rejections are logged in _scan_entries."""
        main_path = Path(__file__).parent.parent.parent / "bot" / "main.py"
        content = main_path.read_text()
        assert 'QUALITY GATE REJECT' in content
        assert 'mark_reject(f"quality_gate_{gate_reason}")' in content
    
    def test_quality_gate_metadata_updated_for_passed_signals(self):
        """Passed signals have metadata updated with quality gate info."""
        main_path = Path(__file__).parent.parent.parent / "bot" / "main.py"
        content = main_path.read_text()
        assert "signal.metadata.update(gate_meta)" in content


# ============== TEST: Telegram Message Contains Expected Edge =============

class TestTelegramMessageContainsExpectedEdge:
    """Tests verifying Telegram message includes expected edge."""
    
    def test_telegram_message_builder_has_expected_edge(self):
        """Signal-only Telegram message includes expected edge."""
        main_path = Path(__file__).parent.parent.parent / "bot" / "main.py"
        content = main_path.read_text()
        # Check for expected_edge in signal-only message builder
        assert 'expected_edge = float(signal.metadata.get("quality_expected_edge"' in content
        assert 'Expected Edge:' in content
        assert '{expected_edge:.2f}R' in content


# ============== TEST: No Regressions in Related Features ==================

class TestNoRegressionsInRelatedFeatures:
    """Tests verifying no regressions in feedback-loop, SL guards, trained-model."""
    
    def test_signal_feedback_register_still_called(self):
        """signal_feedback.register_signal is still called for passed signals."""
        main_path = Path(__file__).parent.parent.parent / "bot" / "main.py"
        content = main_path.read_text()
        # register_signal should be called after quality gate passes
        assert "self.signal_feedback.register_signal(symbol, signal)" in content
    
    def test_sl_validation_code_still_present(self):
        """SL validation in entry_engine is still present."""
        entry_engine_path = Path(__file__).parent.parent.parent / "bot" / "engine" / "entry_engine.py"
        content = entry_engine_path.read_text()
        assert "invalid_sl_long" in content or "invalid_sl_short" in content
    
    def test_trained_model_integration_paths_exist(self):
        """Trained model integration code paths are present."""
        entry_engine_path = Path(__file__).parent.parent.parent / "bot" / "engine" / "entry_engine.py"
        content = entry_engine_path.read_text()
        assert "trained_model" in content.lower()
    
    def test_feedback_loop_enabled_check_exists(self):
        """Feedback loop enabled check exists in main.py."""
        main_path = Path(__file__).parent.parent.parent / "bot" / "main.py"
        content = main_path.read_text()
        assert "self.signal_feedback.enabled" in content


# ============== TEST: Quality Gate Disabled Behavior ======================

class TestQualityGateDisabledBehavior:
    """Tests for when quality gate is disabled."""
    
    def test_quality_gate_disabled_skips_anti_flat_checks(self):
        """When quality_gate_enabled=False, anti-flat checks are skipped."""
        bot = _build_quality_gate_bot({"quality_gate_enabled": False})
        # Normally would fail chop regime, but gate is disabled
        signal = _make_signal(
            regime="chop",
            adx=5.0,  # Very low
            atr_pct=0.01,  # Very low
            normalized_imbalance=0.01,  # Very low
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        # Should still check confidence and expected edge (those are always checked)
        # But anti-flat filters should be skipped
        # Actually looking at code: confidence is checked first, then expected_edge
        # then IF quality_gate_enabled, run anti-flat checks
        # So this should pass if confidence/expected_edge are good
        assert ok is True or reason in ("low_confidence", "low_expected_edge")


# ============== TEST: Edge Cases and Boundary Conditions ==================

class TestEdgeCasesAndBoundaryConditions:
    """Tests for edge cases and boundary conditions."""
    
    def test_zero_confidence_rejected(self):
        """Zero confidence is rejected."""
        bot = _build_quality_gate_bot()
        signal = _make_signal(confidence=0.0)
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
    
    def test_zero_rr_ratio_handled(self):
        """Zero RR ratio is handled without error."""
        bot = _build_quality_gate_bot()
        signal = _make_signal(confidence=0.90, rr_ratio=0.0)
        # expected_edge = 0.90 * 1.0 - 1.0 = -0.1 (negative)
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False  # Negative edge is below threshold
    
    def test_none_values_in_metadata_handled(self):
        """None values in metadata are handled gracefully."""
        bot = _build_quality_gate_bot()
        signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.90,
            rr_ratio=3.0,
            metadata={
                "regime": None,
                "adx": None,
                "atr_pct": None,
                "htf_trend": None,
                "normalized_imbalance": None,
            },
        )
        # Should not raise exception
        try:
            ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
            # With None values converted to 0, anti-flat checks may fail
            assert isinstance(ok, bool)
            assert isinstance(reason, str)
        except Exception as e:
            pytest.fail(f"Exception raised with None metadata values: {e}")
    
    def test_missing_metadata_keys_handled(self):
        """Missing metadata keys are handled with defaults."""
        bot = _build_quality_gate_bot()
        signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.90,
            rr_ratio=3.0,
            metadata={},  # Empty metadata
        )
        try:
            ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
            assert isinstance(ok, bool)
        except Exception as e:
            pytest.fail(f"Exception raised with empty metadata: {e}")


# ============== TEST: Order of Filter Checks ==============================

class TestOrderOfFilterChecks:
    """Tests verifying the correct order of filter checks."""
    
    def test_confidence_checked_before_expected_edge(self):
        """Low confidence is rejected before expected edge calculation matters."""
        bot = _build_quality_gate_bot({"quality_gate_min_confidence": 0.90})
        signal = _make_signal(confidence=0.50, rr_ratio=10.0)  # High RR but low conf
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_confidence"
    
    def test_expected_edge_checked_before_anti_flat(self):
        """Low expected edge is rejected before anti-flat checks."""
        bot = _build_quality_gate_bot()
        signal = _make_signal(
            confidence=0.70,  # Above min confidence (0.68)
            rr_ratio=1.0,  # 0.70 * 2.0 - 1.0 = 0.4 < 0.75
            adx=50.0,  # High ADX would pass
        )
        ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
        assert ok is False
        assert reason == "low_expected_edge"
    
    def test_anti_flat_checks_run_in_order_chop_adx_atr_imbalance_htf(self):
        """Anti-flat checks run in order: chop -> adx -> atr -> imbalance -> htf."""
        bot = _build_quality_gate_bot()
        
        # Test chop rejected first (when all other metrics bad too)
        signal1 = _make_signal(regime="chop", adx=5, atr_pct=0.01, normalized_imbalance=0.01)
        ok1, reason1, _ = bot._passes_signal_quality_gate("BTCUSDT", signal1)
        assert reason1 == "chop_regime"
        
        # Test adx rejected next (when chop passes)
        signal2 = _make_signal(regime="trend", adx=5, atr_pct=0.01, normalized_imbalance=0.01)
        ok2, reason2, _ = bot._passes_signal_quality_gate("BTCUSDT", signal2)
        assert reason2 == "low_adx"
        
        # Test atr rejected next (when adx passes)
        signal3 = _make_signal(regime="trend", adx=30, atr_pct=0.01, normalized_imbalance=0.01)
        ok3, reason3, _ = bot._passes_signal_quality_gate("BTCUSDT", signal3)
        assert reason3 == "low_atr"
        
        # Test imbalance rejected next (when atr passes)
        signal4 = _make_signal(regime="trend", adx=30, atr_pct=0.5, normalized_imbalance=0.01)
        ok4, reason4, _ = bot._passes_signal_quality_gate("BTCUSDT", signal4)
        assert reason4 == "flat_orderflow"
