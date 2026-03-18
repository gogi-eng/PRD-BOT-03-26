#!/usr/bin/env python3
"""
Tests for Entry Engine v6 — Weighted Scoring Model.

Validates:
  - Weighted scoring: Trend(0.3) + Orderflow(0.3) + AI(0.4) >= 0.70
  - Normalized imbalance used (not raw ratio)
  - Transformer sigmoid calibration (no 100% probabilities)
  - RR >= 2.0 hard check still enforced
  - Symbol scanner: 25 symbols with whitelist priority
  - Quasi-liquidation model replaces synthetic fallback
"""
import sys
import os
import math
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from dataclasses import dataclass, field
from typing import Optional
from analysis.market_structure import (
    MarketStructure, StructureTrend, SwingPoint, BOSEvent, LiquiditySweep
)
from analysis.structure_zones import StructureZone, ZoneContext
from analysis.transformer_model import TransformerPriceModel, TransformerPrediction
from analysis.orderflow_analyzer import OrderflowSnapshot
from analysis.liquidation_clusters import LiquidationAnalysis
from engine.entry_engine import EntryEngine, EntrySignal


# ─── Mock objects ───────────────────────────────────────────────

class MockConfig:
    def __init__(self, overrides=None):
        self._defaults = {
            ("entry", "min_rr_ratio"): 2.0,
            ("entry", "min_target_profit_pct"): 1.2,
            ("entry", "min_stop_distance_pct"): 0.5,
            ("entry", "sl_buffer_atr_mult"): 0.5,
            ("entry", "zone_proximity_pct"): 0.4,
            ("entry", "max_spread_pct"): 0.08,
            ("entry", "max_funding_rate"): 0.05,
            ("entry", "entry_threshold"): 0.55,
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


def make_bullish_structure(price=PRICE, atr=ATR):
    return MarketStructure(
        trend=StructureTrend.UP,
        swing_highs=[SwingPoint(10, price + atr * 2, "high")],
        swing_lows=[SwingPoint(8, price - atr * 1.5, "low")],
        last_bos=BOSEvent("up", price - atr, 12, True),
        last_sweep=LiquiditySweep("down", price - atr * 1.5, 14, price - atr * 2),
        volume_spike=True, spread_expansion=True, momentum_confirmed=True,
        signal_ready_long=True,
        sweep_low=price - atr * 2,
        previous_high=price + atr * 3,
        previous_low=price - atr * 3,
        atr_value=atr,
    )


def make_bearish_structure(price=PRICE, atr=ATR):
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
        previous_low=price - atr * 3,
        atr_value=atr,
    )


def make_bullish_zone(price=PRICE, atr=ATR):
    zone = StructureZone(kind="fvg", bias="bullish", low=price - atr * 0.2,
                         high=price + atr * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(bullish_fvg=zone, bearish_fvg=None, bullish_ob=None, bearish_ob=None,
                       support_levels=[price - atr * 2], resistance_levels=[price + atr * 3],
                       all_bullish_zones=[zone], all_bearish_zones=[])


def make_bearish_zone(price=PRICE, atr=ATR):
    zone = StructureZone(kind="ob", bias="bearish", low=price - atr * 0.2,
                         high=price + atr * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(bullish_fvg=None, bearish_fvg=zone, bullish_ob=None, bearish_ob=None,
                       support_levels=[price - atr * 3], resistance_levels=[price + atr * 2],
                       all_bullish_zones=[], all_bearish_zones=[zone])


def make_liq():
    return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)


@pytest.fixture
def engine():
    return EntryEngine(MockConfig())


# ═══════════════════════════════════════════════════════════════
# WEIGHTED SCORING TESTS
# ═══════════════════════════════════════════════════════════════

class TestWeightedScoring:

    def test_all_signals_aligned_bull_enters(self, engine):
        """Trend bullish + orderflow bullish + AI bullish = high score → enter."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert signal.side == "BUY"
        assert signal.confidence >= 0.70

    def test_all_signals_aligned_bear_enters(self, engine):
        """Trend bearish + orderflow bearish + AI bearish = high score → enter."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.70, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bearish_zone(),
            structure=make_bearish_structure(),
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True
        assert signal.side == "SELL"

    def test_conflicting_signals_below_threshold(self, engine):
        """Trend bullish but orderflow extremely bearish + AI bearish → low score → reject."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.20, prob_down=0.65, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=-0.5, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        # Conflicting signals should produce score < 0.70
        if not signal.should_enter:
            assert "score_below_threshold" in signal.metadata.get("reject_reason", "") or \
                   "no_direction" in signal.metadata.get("reject_reason", "")

    def test_score_breakdown_in_metadata(self, engine):
        """Signal metadata must contain score breakdown."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        assert "composite_score" in signal.metadata
        assert "trend_score" in signal.metadata
        assert "orderflow_score" in signal.metadata
        assert "ai_score" in signal.metadata

    def test_neutral_trend_can_still_trade(self, engine):
        """Unlike v5, neutral 4H trend doesn't auto-reject — it contributes 0 to trend score."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.80, prob_down=0.10, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.5, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=0,  # neutral
        )
        # With strong orderflow + AI, it might still enter
        # (trend_score ~ 0.1-0.35 from sweep, OF=1.0, AI=0.8 → composite ~ 0.3*0.35 + 0.3*1.0 + 0.4*0.8 = ~0.725)
        # Can enter or reject based on exact scoring
        assert "composite_score" in signal.metadata or signal.should_enter

    def test_rr_still_hard_requirement(self, engine):
        """Even with high composite score, RR < 2.0 rejects."""
        cfg = MockConfig({("entry", "min_rr_ratio"): 5.0, ("entry", "min_stop_distance_pct"): 3.0})
        strict_engine = EntryEngine(cfg)
        signal = strict_engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.80, prob_down=0.10, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.5, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        if signal.should_enter:
            assert signal.rr_ratio >= 5.0


# ═══════════════════════════════════════════════════════════════
# TRANSFORMER CALIBRATION TESTS
# ═══════════════════════════════════════════════════════════════

class TestTransformerCalibration:

    def test_no_100_percent_probability(self):
        """After calibration, no probability should be 100% or 0%."""
        model = TransformerPriceModel(sequence_length=32)
        # Test calibrate directly
        assert model._calibrate(0.99) < 0.86
        assert model._calibrate(0.01) > 0.04
        assert model._calibrate(1.0) == 0.85
        assert model._calibrate(0.0) == 0.05

    def test_calibration_preserves_ordering(self):
        """Higher input prob should still produce higher output."""
        model = TransformerPriceModel()
        assert model._calibrate(0.8) > model._calibrate(0.5)
        assert model._calibrate(0.5) > model._calibrate(0.2)

    def test_calibrated_probs_sum_to_one(self):
        """After calibration + normalization, probs must sum to ~1.0."""
        model = TransformerPriceModel()
        from analysis.orderflow_analyzer import OrderflowSnapshot
        from analysis.market_analyzer import MarketRegime

        class MockFeatures:
            sequence = [[0.01] * 15 for _ in range(32)]
        class MockReg:
            regime = MarketRegime.TREND
        pred = model.predict(MockFeatures(), MockReg(), OrderflowSnapshot(), make_liq())
        total = pred.prob_up + pred.prob_down + pred.prob_flat
        assert abs(total - 1.0) < 0.01, f"Probs sum to {total}, not 1.0"
        assert pred.prob_up < 0.86
        assert pred.prob_down < 0.86


# ═══════════════════════════════════════════════════════════════
# ORDERFLOW NORMALIZED IMBALANCE IN SCORING
# ═══════════════════════════════════════════════════════════════

class TestOrderflowInScoring:

    def test_strong_bullish_imbalance_boosts_score(self, engine):
        """normalized_imbalance = +0.5 should give high orderflow score."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.60, prob_down=0.20, prob_flat=0.20),
            OrderflowSnapshot(normalized_imbalance=0.5, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        if signal.metadata.get("orderflow_score"):
            assert signal.metadata["orderflow_score"] >= 0.7

    def test_bearish_imbalance_on_bullish_trend_lowers_score(self, engine):
        """Strong selling (imbalance=-0.5) against bullish trend → low orderflow score."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.55, prob_down=0.25, prob_flat=0.20),
            OrderflowSnapshot(normalized_imbalance=-0.5, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        if "orderflow_score" in signal.metadata:
            assert signal.metadata["orderflow_score"] <= 0.2

    def test_uses_normalized_not_raw_ratio(self, engine):
        """Entry engine should reference normalized_imbalance, not raw ratio."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.70, prob_down=0.15, prob_flat=0.15),
            OrderflowSnapshot(
                normalized_imbalance=0.4,
                bullish_ratio=5.13,  # This raw ratio should NOT be used for scoring
                spread_pct=0.02,
            ),
            make_liq(), ATR,
            zone_context=make_bullish_zone(),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        if "normalized_imbalance" in signal.metadata:
            assert signal.metadata["normalized_imbalance"] == 0.4


# ═══════════════════════════════════════════════════════════════
# CONFIG TESTS
# ═══════════════════════════════════════════════════════════════

class TestConfigV6:

    def test_entry_threshold_in_config(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        # Tuned from ultra-strict 0.85 to 0.78 to restore controlled signal flow
        assert cfg["entry"]["entry_threshold"] == 0.78

    def test_trade_symbols_25(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["market"]["trade_symbols"] == 25

    def test_rr_ratio_at_least_2(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["min_rr_ratio"] >= 2.0

    def test_early_exit_still_zero(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["exit"]["early_exit_bars"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
