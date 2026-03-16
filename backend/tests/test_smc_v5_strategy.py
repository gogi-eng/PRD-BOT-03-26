#!/usr/bin/env python3
"""
SMC v6 Strategy Tests — Weighted Scoring Model.

Covers all original 5-point ТЗ requirements adapted for v6:
  1. 4H trend contributes to score (not hard gate)
  2. Sweep + zone enhance score
  3. early_exit_bars = 0
  4. Whitelist priority (not exclusive)
  5. RR >= 2.0 hard requirement
  + Weighted scoring: trend(0.3) + orderflow(0.3) + AI(0.4) >= 0.70
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from analysis.market_structure import (
    MarketStructure, StructureTrend, SwingPoint, BOSEvent, LiquiditySweep
)
from analysis.structure_zones import StructureZone, ZoneContext
from analysis.transformer_model import TransformerPrediction
from analysis.orderflow_analyzer import OrderflowSnapshot
from analysis.liquidation_clusters import LiquidationAnalysis
from engine.entry_engine import EntryEngine, EntrySignal
from dataclasses import dataclass


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
            ("entry", "entry_threshold"): 0.70,
        }
        if overrides:
            self._defaults.update(overrides)

    def get(self, *keys, default=None):
        return self._defaults.get(keys, default)


@dataclass
class MockMarket:
    can_trade: bool = True

@dataclass
class MockRegime:
    regime: object = None
    def __post_init__(self):
        if self.regime is None:
            self.regime = type('R', (), {'value': 'trend'})()


PRICE = 50000.0
ATR = 400.0


def _liq():
    return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)


# Strong bullish signals that pass threshold
def _bull_tf():
    return TransformerPrediction(prob_up=0.75, prob_down=0.10, prob_flat=0.15)

def _bull_of():
    return OrderflowSnapshot(normalized_imbalance=0.45, spread_pct=0.02)

# Strong bearish signals that pass threshold
def _bear_tf():
    return TransformerPrediction(prob_up=0.10, prob_down=0.75, prob_flat=0.15)

def _bear_of():
    return OrderflowSnapshot(normalized_imbalance=-0.45, spread_pct=0.02)

# Neutral (low conviction)
def _neutral_tf():
    return TransformerPrediction(prob_up=0.35, prob_down=0.35, prob_flat=0.30)

def _neutral_of():
    return OrderflowSnapshot(normalized_imbalance=0.0, spread_pct=0.02)


def make_bullish_structure():
    return MarketStructure(
        trend=StructureTrend.UP,
        swing_highs=[SwingPoint(10, PRICE + ATR * 2, "high")],
        swing_lows=[SwingPoint(8, PRICE - ATR * 1.5, "low")],
        last_bos=BOSEvent("up", PRICE - ATR, 12, True),
        last_sweep=LiquiditySweep("down", PRICE - ATR * 1.5, 14, PRICE - ATR * 2),
        volume_spike=True, spread_expansion=True, momentum_confirmed=True,
        signal_ready_long=True,
        sweep_low=PRICE - ATR * 2,
        previous_high=PRICE + ATR * 3,
        previous_low=PRICE - ATR * 3,
        atr_value=ATR,
    )


def make_bearish_structure():
    return MarketStructure(
        trend=StructureTrend.DOWN,
        swing_highs=[SwingPoint(10, PRICE + ATR * 1.5, "high")],
        swing_lows=[SwingPoint(8, PRICE - ATR * 2, "low")],
        last_bos=BOSEvent("down", PRICE + ATR, 12, True),
        last_sweep=LiquiditySweep("up", PRICE + ATR * 1.5, 14, PRICE + ATR * 2),
        volume_spike=True, spread_expansion=True, momentum_confirmed=True,
        signal_ready_short=True,
        sweep_high=PRICE + ATR * 2,
        previous_high=PRICE + ATR * 3,
        previous_low=PRICE - ATR * 3,
        atr_value=ATR,
    )


def make_bullish_zone():
    zone = StructureZone(kind="fvg", bias="bullish", low=PRICE - ATR * 0.2,
                         high=PRICE + ATR * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(bullish_fvg=zone, bearish_fvg=None, bullish_ob=None, bearish_ob=None,
                       support_levels=[PRICE - ATR * 2], resistance_levels=[PRICE + ATR * 3],
                       all_bullish_zones=[zone], all_bearish_zones=[])


def make_bearish_zone():
    zone = StructureZone(kind="ob", bias="bearish", low=PRICE - ATR * 0.2,
                         high=PRICE + ATR * 0.2, strength=0.7, created_at_index=10)
    return ZoneContext(bullish_fvg=None, bearish_fvg=zone, bullish_ob=None, bearish_ob=None,
                       support_levels=[PRICE - ATR * 3], resistance_levels=[PRICE + ATR * 2],
                       all_bullish_zones=[], all_bearish_zones=[zone])


@pytest.fixture
def engine():
    return EntryEngine(MockConfig())


# ═══════════════════════════════════════════════════════════════
# TREND CONTRIBUTION (not hard gate anymore)
# ═══════════════════════════════════════════════════════════════

class TestTrendScoring:

    def test_4h_bullish_boosts_long(self, engine):
        """4H bullish + strong orderflow + strong AI = LONG."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert signal.side == "BUY"
        assert signal.metadata["trend_score"] > 0.5

    def test_4h_bearish_boosts_short(self, engine):
        """4H bearish + strong bearish orderflow + bearish AI = SHORT."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bear_tf(), _bear_of(), _liq(), ATR,
            zone_context=make_bearish_zone(), structure=make_bearish_structure(),
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True
        assert signal.side == "SELL"

    def test_neutral_trend_lowers_score(self, engine):
        """Neutral 4H trend gives 0 trend points — but doesn't auto-reject."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=0,
        )
        # Trend score should be lower without 4H trend
        score = signal.metadata.get("trend_score", signal.confidence)
        assert score < 0.6

    def test_conflicting_4h_and_sweep_lowers_score(self, engine):
        """4H bearish but sweep down (bullish) → conflicting → lower score."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=-1,  # bearish but structure has sweep_down (bullish)
        )
        # Conflicting → should either reject or have lower confidence
        if signal.should_enter:
            assert signal.confidence < 0.85


# ═══════════════════════════════════════════════════════════════
# SWEEP AND ZONE CONTRIBUTION
# ═══════════════════════════════════════════════════════════════

class TestSweepAndZone:

    def test_no_sweep_lowers_trend_score(self, engine):
        """Without a sweep, trend score should be lower."""
        structure = make_bullish_structure()
        structure.last_sweep = None
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=structure,
            htf_4h_trend=1,
        )
        # Without sweep, trend_score ~ 0.5 (4H only) + 0.15 (struct)
        if "trend_score" in signal.metadata:
            assert signal.metadata["trend_score"] <= 0.7

    def test_no_zone_still_evaluates(self, engine):
        """Without zone context, engine still evaluates (just no zone refinement)."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=None, structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        # Should have a score in metadata
        assert "composite_score" in signal.metadata or signal.should_enter


# ═══════════════════════════════════════════════════════════════
# CONFIG TESTS (unchanged from v5)
# ═══════════════════════════════════════════════════════════════

class TestConfigRequirements:

    def test_early_exit_zero(self):
        import yaml
        with open(os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')) as f:
            cfg = yaml.safe_load(f)
        assert cfg["exit"]["early_exit_bars"] == 0

    def test_whitelist_enabled(self):
        import yaml
        with open(os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')) as f:
            cfg = yaml.safe_load(f)
        assert cfg["market"]["whitelist_enabled"] is True
        expected = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"}
        assert set(cfg["market"]["whitelist_symbols"]) == expected

    def test_trade_symbols_25(self):
        import yaml
        with open(os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')) as f:
            cfg = yaml.safe_load(f)
        assert cfg["market"]["trade_symbols"] == 25

    def test_rr_at_least_2(self):
        import yaml
        with open(os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["min_rr_ratio"] >= 2.0

    def test_entry_threshold_070(self):
        import yaml
        with open(os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["entry_threshold"] == 0.70


# ═══════════════════════════════════════════════════════════════
# FULL PIPELINE
# ═══════════════════════════════════════════════════════════════

class TestFullPipelineV6:

    def test_perfect_long(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert signal.side == "BUY"
        assert signal.rr_ratio >= 2.0
        assert signal.stop_loss < PRICE
        assert signal.take_profit > PRICE

    def test_perfect_short(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bear_tf(), _bear_of(), _liq(), ATR,
            zone_context=make_bearish_zone(), structure=make_bearish_structure(),
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True
        assert signal.side == "SELL"
        assert signal.rr_ratio >= 2.0

    def test_market_blocked_rejects(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(can_trade=False), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "market_blocked" in signal.metadata.get("reject_reason", "")

    def test_high_spread_rejects(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.5),
            _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "spread" in signal.metadata.get("reject_reason", "").lower()

    def test_low_conviction_rejects(self, engine):
        """Neutral everything → score too low."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _neutral_tf(), _neutral_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=0,
        )
        assert signal.should_enter is False

    def test_score_in_metadata(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE, MockMarket(), MockRegime(),
            _bull_tf(), _bull_of(), _liq(), ATR,
            zone_context=make_bullish_zone(), structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        assert "composite_score" in signal.metadata
        assert "trend_score" in signal.metadata
        assert "orderflow_score" in signal.metadata
        assert "ai_score" in signal.metadata


# ═══════════════════════════════════════════════════════════════
# HEATMAP TESTS (unchanged)
# ═══════════════════════════════════════════════════════════════

class TestLiquidityHeatmapV6:

    def test_detects_bid_walls(self):
        from analysis.liquidity_heatmap import LiquidityHeatmap
        orderbook = {"bids": [[50000, 1.0], [49990, 0.5], [49970, 5.0]], "asks": [[50010, 0.5]]}
        result = LiquidityHeatmap().build_heatmap(orderbook)
        assert len(result.bid_walls) >= 1
        assert result.strongest_bid.price == 49970

    def test_imbalance_positive(self):
        from analysis.liquidity_heatmap import LiquidityHeatmap
        orderbook = {"bids": [[50000, 10.0]], "asks": [[50010, 5.0]]}
        result = LiquidityHeatmap().build_heatmap(orderbook)
        assert result.imbalance > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
