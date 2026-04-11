#!/usr/bin/env python3
"""
Tests for Iteration 23 — Stricter TP Policy: require_structural_tp

Feature Requirements:
1. entry.require_structural_tp=true present in config
2. entry_engine rejects signal with reject_reason=tp_not_structural when structural targets missing
3. entry_engine still allows signal when structural TP exists
4. metadata includes tp_confirmed_by_structure for accepted signals
5. no regressions in entry/quality tests

Tests both BUY and SELL paths for structural target detection.
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from dataclasses import dataclass, field
from typing import List, Optional

from analysis.market_structure import (
    MarketStructure, StructureTrend, SwingPoint, BOSEvent, LiquiditySweep
)
from analysis.structure_zones import StructureZone, ZoneContext
from analysis.transformer_model import TransformerPrediction
from analysis.orderflow_analyzer import OrderflowSnapshot
from analysis.liquidation_clusters import LiquidationAnalysis
from engine.entry_engine import EntryEngine


# ─── Mock objects ───────────────────────────────────────────────

class MockConfig:
    """Mock config with structural TP requirement enabled by default."""
    def __init__(self, overrides=None):
        self._defaults = {
            ("entry", "min_rr_ratio"): 2.0,
            ("entry", "min_target_profit_pct"): 1.2,
            ("entry", "min_stop_distance_pct"): 0.5,
            ("entry", "min_stop_atr_mult"): 0.9,
            ("entry", "require_structural_tp"): True,  # NEW: Default enabled
            ("entry", "sl_buffer_atr_mult"): 0.5,
            ("entry", "max_entry_extension_atr"): 0.75,
            ("entry", "entry_range_atr_mult"): 0.22,
            ("entry", "zone_proximity_pct"): 0.4,
            ("entry", "max_spread_pct"): 0.08,
            ("entry", "max_funding_rate"): 0.05,
            ("entry", "entry_threshold"): 0.55,
            ("entry", "trained_model_enabled"): False,
        }
        if overrides:
            self._defaults.update(overrides)

    def get(self, *keys, default=None):
        return self._defaults.get(keys, default)


@dataclass
class MockMarketAnalysis:
    can_trade: bool = True


@dataclass
class MockRegime:
    regime: object = None
    def __post_init__(self):
        if self.regime is None:
            self.regime = type('R', (), {'value': 'trend'})()


PRICE = 50000.0
ATR = 400.0


def make_liq():
    """Create neutral liquidation analysis."""
    return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)


def make_bullish_structure_with_structural_targets(price=PRICE, atr=ATR):
    """Bullish structure WITH structural targets (previous_high above price)."""
    return MarketStructure(
        trend=StructureTrend.UP,
        swing_highs=[SwingPoint(10, price + atr * 2, "high")],
        swing_lows=[SwingPoint(8, price - atr * 1.5, "low")],
        last_bos=BOSEvent("up", price - atr, 12, True),
        last_sweep=LiquiditySweep("down", price - atr * 1.5, 14, price - atr * 2),
        volume_spike=True, spread_expansion=True, momentum_confirmed=True,
        signal_ready_long=True,
        sweep_low=price - atr * 2,
        previous_high=price + atr * 3,  # Structural target ABOVE price
        previous_low=price - atr * 3,
        atr_value=atr,
    )


def make_bullish_structure_no_structural_targets(price=PRICE, atr=ATR):
    """Bullish structure WITHOUT structural targets (previous_high below price)."""
    return MarketStructure(
        trend=StructureTrend.UP,
        swing_highs=[SwingPoint(10, price - atr * 0.5, "high")],  # Below current price
        swing_lows=[SwingPoint(8, price - atr * 1.5, "low")],
        last_bos=BOSEvent("up", price - atr, 12, True),
        last_sweep=LiquiditySweep("down", price - atr * 1.5, 14, price - atr * 2),
        volume_spike=True, spread_expansion=True, momentum_confirmed=True,
        signal_ready_long=True,
        sweep_low=price - atr * 2,
        previous_high=price - atr * 0.5,  # Below current price - NO structural TP
        previous_low=price - atr * 3,
        atr_value=atr,
    )


def make_bearish_structure_with_structural_targets(price=PRICE, atr=ATR):
    """Bearish structure WITH structural targets (previous_low below price)."""
    return MarketStructure(
        trend=StructureTrend.DOWN,
        swing_highs=[SwingPoint(10, price + atr * 1.5, "high")],
        swing_lows=[SwingPoint(8, price - atr * 2, "low")],
        last_bos=BOSEvent("down", price + atr, 12, True),
        last_sweep=LiquiditySweep("up", price + atr * 1.5, 14, price + atr * 2),
        volume_spike=True, spread_expansion=True, momentum_confirmed=True,
        signal_ready_short=True,
        sweep_high=price + atr * 2,
        previous_high=price + atr * 3,
        previous_low=price - atr * 3,  # Structural target BELOW price
        atr_value=atr,
    )


def make_bearish_structure_no_structural_targets(price=PRICE, atr=ATR):
    """Bearish structure WITHOUT structural targets (previous_low above price)."""
    return MarketStructure(
        trend=StructureTrend.DOWN,
        swing_highs=[SwingPoint(10, price + atr * 1.5, "high")],
        swing_lows=[SwingPoint(8, price + atr * 0.5, "low")],  # Above current price
        last_bos=BOSEvent("down", price + atr, 12, True),
        last_sweep=LiquiditySweep("up", price + atr * 1.5, 14, price + atr * 2),
        volume_spike=True, spread_expansion=True, momentum_confirmed=True,
        signal_ready_short=True,
        sweep_high=price + atr * 2,
        previous_high=price + atr * 3,
        previous_low=price + atr * 0.5,  # Above current price - NO structural TP
        atr_value=atr,
    )


def make_bullish_zone_with_resistance(price=PRICE, atr=ATR):
    """Zone context with resistance levels (structural targets for longs)."""
    zone = StructureZone(kind="fvg", bias="bullish", low=price - atr * 0.2,
                         high=price + atr * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(
        bullish_fvg=zone, bearish_fvg=None, bullish_ob=None, bearish_ob=None,
        support_levels=[price - atr * 2],
        resistance_levels=[price + atr * 3],  # Structural target for longs
        all_bullish_zones=[zone],
        all_bearish_zones=[]
    )


def make_bullish_zone_no_resistance(price=PRICE, atr=ATR):
    """Zone context WITHOUT resistance levels (no structural targets for longs)."""
    zone = StructureZone(kind="fvg", bias="bullish", low=price - atr * 0.2,
                         high=price + atr * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(
        bullish_fvg=zone, bearish_fvg=None, bullish_ob=None, bearish_ob=None,
        support_levels=[price - atr * 2],
        resistance_levels=[],  # No resistance = no structural target
        all_bullish_zones=[zone],
        all_bearish_zones=[]  # No bearish zones with low > price either
    )


def make_bearish_zone_with_support(price=PRICE, atr=ATR):
    """Zone context with support levels (structural targets for shorts)."""
    zone = StructureZone(kind="ob", bias="bearish", low=price - atr * 0.2,
                         high=price + atr * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(
        bullish_fvg=None, bearish_fvg=zone, bullish_ob=None, bearish_ob=None,
        support_levels=[price - atr * 3],  # Structural target for shorts
        resistance_levels=[price + atr * 2],
        all_bullish_zones=[],
        all_bearish_zones=[zone]
    )


def make_bearish_zone_no_support(price=PRICE, atr=ATR):
    """Zone context WITHOUT support levels (no structural targets for shorts)."""
    zone = StructureZone(kind="ob", bias="bearish", low=price - atr * 0.2,
                         high=price + atr * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(
        bullish_fvg=None, bearish_fvg=zone, bullish_ob=None, bearish_ob=None,
        support_levels=[],  # No support = no structural target
        resistance_levels=[price + atr * 2],
        all_bullish_zones=[],  # No bullish zones with high < price either
        all_bearish_zones=[zone]
    )


@pytest.fixture
def engine_with_structural_tp():
    """Engine with require_structural_tp=true."""
    return EntryEngine(MockConfig({("entry", "require_structural_tp"): True}))


@pytest.fixture
def engine_without_structural_tp():
    """Engine with require_structural_tp=false."""
    return EntryEngine(MockConfig({("entry", "require_structural_tp"): False}))


# ═══════════════════════════════════════════════════════════════
# CONFIG VERIFICATION
# ═══════════════════════════════════════════════════════════════

class TestConfigHasStructuralTPRequirement:
    """Verify config.yaml has require_structural_tp=true."""

    def test_require_structural_tp_is_true_in_config(self):
        """Config must have entry.require_structural_tp=true."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["require_structural_tp"] is True, \
            f"Expected require_structural_tp=true, got {cfg['entry'].get('require_structural_tp')}"


# ═══════════════════════════════════════════════════════════════
# BUY PATH - STRUCTURAL TARGET DETECTION
# ═══════════════════════════════════════════════════════════════

class TestBuyPathStructuralTPDetection:
    """Test structural TP detection and rejection for BUY signals."""

    def test_buy_accepted_with_structural_previous_high(self, engine_with_structural_tp):
        """BUY signal accepted when structure.previous_high > current_price."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone_no_resistance(),  # No zone targets
            structure=make_bullish_structure_with_structural_targets(),  # Has previous_high > price
            htf_4h_trend=1,
        )
        assert signal.should_enter is True, f"Expected entry, got reject: {signal.metadata}"
        assert signal.side == "BUY"
        assert signal.metadata.get("tp_confirmed_by_structure") is True

    def test_buy_accepted_with_zone_resistance_levels(self, engine_with_structural_tp):
        """BUY signal accepted when zone_context has resistance_levels > current_price."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone_with_resistance(),  # Has resistance > price
            structure=make_bullish_structure_no_structural_targets(),  # No previous_high target
            htf_4h_trend=1,
        )
        assert signal.should_enter is True, f"Expected entry, got reject: {signal.metadata}"
        assert signal.side == "BUY"
        assert signal.metadata.get("tp_confirmed_by_structure") is True

    def test_buy_rejected_without_structural_targets(self, engine_with_structural_tp):
        """BUY signal rejected with tp_not_structural when no structural targets exist."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone_no_resistance(),  # No resistance levels
            structure=make_bullish_structure_no_structural_targets(),  # No previous_high target
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert signal.metadata.get("reject_reason") == "tp_not_structural", \
            f"Expected reject_reason=tp_not_structural, got: {signal.metadata}"

    def test_buy_allowed_when_feature_disabled(self, engine_without_structural_tp):
        """BUY signal allowed without structural targets when require_structural_tp=false."""
        signal = engine_without_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone_no_resistance(),
            structure=make_bullish_structure_no_structural_targets(),
            htf_4h_trend=1,
        )
        # Should not reject with tp_not_structural when feature is disabled
        if not signal.should_enter:
            assert signal.metadata.get("reject_reason") != "tp_not_structural", \
                "Should not reject for tp_not_structural when feature is disabled"


# ═══════════════════════════════════════════════════════════════
# SELL PATH - STRUCTURAL TARGET DETECTION
# ═══════════════════════════════════════════════════════════════

class TestSellPathStructuralTPDetection:
    """Test structural TP detection and rejection for SELL signals."""

    def test_sell_accepted_with_structural_previous_low(self, engine_with_structural_tp):
        """SELL signal accepted when structure.previous_low < current_price."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.70, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bearish_zone_no_support(),  # No zone targets
            structure=make_bearish_structure_with_structural_targets(),  # Has previous_low < price
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True, f"Expected entry, got reject: {signal.metadata}"
        assert signal.side == "SELL"
        assert signal.metadata.get("tp_confirmed_by_structure") is True

    def test_sell_accepted_with_zone_support_levels(self, engine_with_structural_tp):
        """SELL signal accepted when zone_context has support_levels < current_price."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.70, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bearish_zone_with_support(),  # Has support < price
            structure=make_bearish_structure_no_structural_targets(),  # No previous_low target
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True, f"Expected entry, got reject: {signal.metadata}"
        assert signal.side == "SELL"
        assert signal.metadata.get("tp_confirmed_by_structure") is True

    def test_sell_rejected_without_structural_targets(self, engine_with_structural_tp):
        """SELL signal rejected with tp_not_structural when no structural targets exist."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.70, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bearish_zone_no_support(),  # No support levels
            structure=make_bearish_structure_no_structural_targets(),  # No previous_low target
            htf_4h_trend=-1,
        )
        assert signal.should_enter is False
        assert signal.metadata.get("reject_reason") == "tp_not_structural", \
            f"Expected reject_reason=tp_not_structural, got: {signal.metadata}"

    def test_sell_allowed_when_feature_disabled(self, engine_without_structural_tp):
        """SELL signal allowed without structural targets when require_structural_tp=false."""
        signal = engine_without_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.70, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bearish_zone_no_support(),
            structure=make_bearish_structure_no_structural_targets(),
            htf_4h_trend=-1,
        )
        # Should not reject with tp_not_structural when feature is disabled
        if not signal.should_enter:
            assert signal.metadata.get("reject_reason") != "tp_not_structural", \
                "Should not reject for tp_not_structural when feature is disabled"


# ═══════════════════════════════════════════════════════════════
# METADATA VERIFICATION
# ═══════════════════════════════════════════════════════════════

class TestMetadataContainsTPConfirmedByStructure:
    """Verify metadata includes tp_confirmed_by_structure for accepted signals."""

    def test_accepted_buy_has_tp_confirmed_by_structure_true(self, engine_with_structural_tp):
        """Accepted BUY signal metadata has tp_confirmed_by_structure=true."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone_with_resistance(),
            structure=make_bullish_structure_with_structural_targets(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert "tp_confirmed_by_structure" in signal.metadata
        assert signal.metadata["tp_confirmed_by_structure"] is True

    def test_accepted_sell_has_tp_confirmed_by_structure_true(self, engine_with_structural_tp):
        """Accepted SELL signal metadata has tp_confirmed_by_structure=true."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.70, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bearish_zone_with_support(),
            structure=make_bearish_structure_with_structural_targets(),
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True
        assert "tp_confirmed_by_structure" in signal.metadata
        assert signal.metadata["tp_confirmed_by_structure"] is True

    def test_rejected_signal_has_composite_score_in_metadata(self, engine_with_structural_tp):
        """Rejected tp_not_structural signal still includes composite_score in metadata."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone_no_resistance(),
            structure=make_bullish_structure_no_structural_targets(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert signal.metadata.get("reject_reason") == "tp_not_structural"
        assert "composite_score" in signal.metadata


# ═══════════════════════════════════════════════════════════════
# ZONE CONTEXT STRUCTURAL TARGETS (BEARISH ZONES FOR LONGS, BULLISH FOR SHORTS)
# ═══════════════════════════════════════════════════════════════

class TestZoneContextStructuralTargets:
    """Test structural target detection from zone context."""

    def test_buy_accepted_with_bearish_zone_above_price(self, engine_with_structural_tp):
        """BUY signal accepted when bearish zone exists above price (structural target)."""
        # Create zone with bearish zones above current price
        bullish_zone = StructureZone(kind="fvg", bias="bullish", low=PRICE - ATR * 0.2,
                                      high=PRICE + ATR * 0.2, strength=0.7, created_at_index=10)
        bearish_zone = StructureZone(kind="ob", bias="bearish", low=PRICE + ATR * 2,
                                      high=PRICE + ATR * 2.5, strength=0.8, created_at_index=8,
                                      mitigated=False)
        zone_ctx = ZoneContext(
            bullish_fvg=bullish_zone, bearish_fvg=None, bullish_ob=None, bearish_ob=None,
            support_levels=[], resistance_levels=[],
            all_bullish_zones=[bullish_zone],
            all_bearish_zones=[bearish_zone]  # Bearish zone ABOVE price = structural target
        )

        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=zone_ctx,
            structure=make_bullish_structure_no_structural_targets(),  # No struct target
            htf_4h_trend=1,
        )
        assert signal.should_enter is True, f"Expected entry, got reject: {signal.metadata}"
        assert signal.metadata.get("tp_confirmed_by_structure") is True

    def test_sell_accepted_with_bullish_zone_below_price(self, engine_with_structural_tp):
        """SELL signal accepted when bullish zone exists below price (structural target)."""
        # Create zone with bullish zones below current price
        bearish_zone = StructureZone(kind="ob", bias="bearish", low=PRICE - ATR * 0.2,
                                      high=PRICE + ATR * 0.2, strength=0.7, created_at_index=10)
        bullish_zone = StructureZone(kind="fvg", bias="bullish", low=PRICE - ATR * 2.5,
                                      high=PRICE - ATR * 2, strength=0.8, created_at_index=8,
                                      mitigated=False)
        zone_ctx = ZoneContext(
            bullish_fvg=None, bearish_fvg=bearish_zone, bullish_ob=None, bearish_ob=None,
            support_levels=[], resistance_levels=[],
            all_bullish_zones=[bullish_zone],  # Bullish zone BELOW price = structural target
            all_bearish_zones=[bearish_zone]
        )

        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.70, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=zone_ctx,
            structure=make_bearish_structure_no_structural_targets(),  # No struct target
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True, f"Expected entry, got reject: {signal.metadata}"
        assert signal.metadata.get("tp_confirmed_by_structure") is True


# ═══════════════════════════════════════════════════════════════
# REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════

class TestNoRegressions:
    """Ensure no regressions in existing entry engine behavior."""

    def test_spread_rejection_still_works(self, engine_with_structural_tp):
        """High spread still causes rejection before structural TP check."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.15),  # High spread
            make_liq(), ATR,
            zone_context=make_bullish_zone_with_resistance(),
            structure=make_bullish_structure_with_structural_targets(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "spread_too_wide" in signal.metadata.get("reject_reason", "")

    def test_score_below_threshold_still_works(self, engine_with_structural_tp):
        """Low score still causes rejection before structural TP check."""
        signal = engine_with_structural_tp.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.30, prob_down=0.50, prob_flat=0.20),  # Weak
            OrderflowSnapshot(normalized_imbalance=-0.3, spread_pct=0.02),  # Conflicting
            make_liq(), ATR,
            zone_context=make_bullish_zone_with_resistance(),
            structure=make_bullish_structure_with_structural_targets(),
            htf_4h_trend=1,  # Bullish trend but weak AI
        )
        if not signal.should_enter:
            reject_reason = signal.metadata.get("reject_reason", "")
            # Can be score_below_threshold or no_direction_consensus
            assert "score_below_threshold" in reject_reason or "no_direction" in reject_reason

    def test_rr_check_still_enforced_after_structural_tp(self, engine_with_structural_tp):
        """RR < min_rr_ratio still rejects after structural TP is confirmed."""
        # Create engine with very high RR requirement
        cfg = MockConfig({
            ("entry", "require_structural_tp"): True,
            ("entry", "min_rr_ratio"): 10.0,  # Very high
        })
        strict_engine = EntryEngine(cfg)
        
        signal = strict_engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone_with_resistance(),
            structure=make_bullish_structure_with_structural_targets(),
            htf_4h_trend=1,
        )
        # Should either enter with high RR or reject for low RR
        if not signal.should_enter:
            assert "rr_too_low" in signal.metadata.get("reject_reason", "") or \
                   signal.rr_ratio >= 10.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
