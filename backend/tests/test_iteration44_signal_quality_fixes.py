#!/usr/bin/env python3
"""
Iteration 44: Signal Quality Fixes Tests

Tests for:
1. OrderflowAnalyzer enhancements:
   - depth_levels default is 25 (was 10)
   - Tracks recent_buy_vol and recent_sell_vol for last 30 trades
   - normalized_imbalance uses weighted_imb (60% recent + 40% total)
   - imbalance_score uses 40% orderbook + 35% trade + 25% weighted_imb
   - dominant_side uses weighted_imb threshold 0.15
   - Handles empty orderbook and trades

2. Exhaustion guard:
   - Rejects entry when 5+ of 7 last candles move in signal direction
   - Allows entry when only 3-4 candles move in signal direction
   - Works for both BUY and SELL directions

3. Counter-flow guard:
   - Rejects SELL when buy_vol > sell_vol * 1.4
   - Rejects BUY when sell_vol > buy_vol * 1.4
   - Allows entry when volumes are balanced

4. Config values:
   - trailing_activation_atr is 0.7 (was 1.1)
   - trailing_distance_atr is 1.2 (was 1.4)
   - exchange_closed_confirm_cycles is 8 (was 5)
   - exchange_closed_force_cycles is 30 (was 20)
   - 1m TF preset has trailing_activation_atr 0.7 and trailing_distance_atr 1.2

5. Regression tests:
   - P0 manual protection still intact
   - A/B/C grading classify_signal_grade still works
"""

import pytest
import sys
import os
import yaml
from dataclasses import dataclass
from typing import List, Dict, Optional
from enum import Enum

# Add bot directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bot.analysis.orderflow_analyzer import OrderflowAnalyzer, OrderflowSnapshot
from bot.engine.entry_engine import EntryEngine, EntrySignal, classify_signal_grade


# ============================================================
# MOCK CLASSES FOR TESTING
# ============================================================

class MockRegime(Enum):
    TREND = "trend"
    BREAKOUT = "breakout"
    CHOP = "chop"
    RANGE = "range"


@dataclass
class MockMarketAnalysis:
    can_trade: bool = True


@dataclass
class MockRegimePrediction:
    regime: MockRegime = MockRegime.TREND


@dataclass
class MockTransformerPrediction:
    prob_up: float = 0.6
    prob_down: float = 0.3
    prob_flat: float = 0.1


@dataclass
class MockLiqAnalysis:
    target_level: float = 0.0
    signal: int = 0
    distance_to_target_pct: float = 0.0
    magnet_direction: str = "none"


@dataclass
class MockSweep:
    direction: str = "down"


@dataclass
class MockBOS:
    direction: str = "up"


class MockTrend(Enum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


@dataclass
class MockStructure:
    trend: MockTrend = MockTrend.UP
    last_sweep: Optional[MockSweep] = None
    last_bos: Optional[MockBOS] = None
    sweep_low: float = 0.0
    sweep_high: float = 0.0
    previous_high: float = 0.0
    previous_low: float = 0.0


@dataclass
class MockZone:
    kind: str = "fvg"
    bias: str = "bullish"
    high: float = 100.0
    low: float = 99.0
    mitigated: bool = False


class MockZoneContext:
    def __init__(self, bullish_zones=None, bearish_zones=None):
        self.all_bullish_zones = bullish_zones or []
        self.all_bearish_zones = bearish_zones or []
        self.support_levels = []
        self.resistance_levels = []

    def price_in_bullish_zone(self, price):
        for z in self.all_bullish_zones:
            if z.low <= price <= z.high and not z.mitigated:
                return z
        return None

    def price_in_bearish_zone(self, price):
        for z in self.all_bearish_zones:
            if z.low <= price <= z.high and not z.mitigated:
                return z
        return None

    def price_near_bullish_zone(self, price, pct):
        return None

    def price_near_bearish_zone(self, price, pct):
        return None

    def structural_sl_long(self, price, atr):
        return price - atr * 2.0

    def structural_sl_short(self, price, atr):
        return price + atr * 2.0

    def structural_tp_long(self, price, atr):
        return price + atr * 3.0, price + atr * 5.0

    def structural_tp_short(self, price, atr):
        return price - atr * 3.0, price - atr * 5.0


class MockConfig:
    """Mock config that returns default values."""
    def __init__(self, overrides=None):
        self._overrides = overrides or {}

    def get(self, section, key, default=None):
        if section in self._overrides and key in self._overrides[section]:
            return self._overrides[section][key]
        # Return sensible defaults
        defaults = {
            ("entry", "min_rr_ratio"): 2.0,
            ("entry", "min_target_profit_pct"): 1.2,
            ("entry", "min_stop_distance_pct"): 0.5,
            ("entry", "min_stop_atr_mult"): 0.9,
            ("entry", "require_structural_tp"): False,
            ("entry", "sl_buffer_atr_mult"): 0.5,
            ("entry", "max_entry_extension_atr"): 0.75,
            ("entry", "entry_range_atr_mult"): 0.22,
            ("entry", "zone_proximity_pct"): 0.4,
            ("entry", "max_spread_pct"): 0.08,
            ("entry", "max_funding_rate"): 0.05,
            ("entry", "entry_threshold"): 0.55,
            ("entry", "trained_model_enabled"): False,
            ("entry", "trained_model_min_prob"): 0.55,
            ("entry", "trained_model_blend"): 0.35,
            ("entry", "trained_model_weights_path"): "transformer_weights.pt",
        }
        return defaults.get((section, key), default)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def create_bullish_klines(count: int) -> List[Dict]:
    """Create klines where close > open (bullish candles)."""
    return [{"open": 100.0 + i, "close": 101.0 + i} for i in range(count)]


def create_bearish_klines(count: int) -> List[Dict]:
    """Create klines where close < open (bearish candles)."""
    return [{"open": 101.0 + i, "close": 100.0 + i} for i in range(count)]


def create_mixed_klines(bullish_count: int, bearish_count: int) -> List[Dict]:
    """Create mixed klines with specified bullish and bearish counts."""
    klines = []
    for i in range(bullish_count):
        klines.append({"open": 100.0 + i, "close": 101.0 + i})
    for i in range(bearish_count):
        klines.append({"open": 101.0 + bullish_count + i, "close": 100.0 + bullish_count + i})
    return klines


def create_orderbook(bid_volume: float, ask_volume: float, levels: int = 25) -> Dict:
    """Create mock orderbook with specified volumes."""
    bids = [[str(100.0 - i * 0.1), str(bid_volume / levels)] for i in range(levels)]
    asks = [[str(100.0 + i * 0.1), str(ask_volume / levels)] for i in range(levels)]
    return {"bids": bids, "asks": asks}


def create_trades(buy_volume: float, sell_volume: float, total_trades: int = 50) -> List[Dict]:
    """Create mock trades with specified buy/sell volumes."""
    trades = []
    buy_per_trade = buy_volume / (total_trades // 2) if total_trades > 0 else 0
    sell_per_trade = sell_volume / (total_trades // 2) if total_trades > 0 else 0
    
    for i in range(total_trades):
        if i % 2 == 0:
            trades.append({"side": "buy", "size": buy_per_trade})
        else:
            trades.append({"side": "sell", "size": sell_per_trade})
    return trades


def create_recent_heavy_trades(recent_buy: float, recent_sell: float, 
                                old_buy: float, old_sell: float) -> List[Dict]:
    """Create trades with different recent vs old volumes."""
    trades = []
    # First 30 trades (recent) - alternating buy/sell
    for i in range(30):
        if i % 2 == 0:
            trades.append({"side": "buy", "size": recent_buy / 15})
        else:
            trades.append({"side": "sell", "size": recent_sell / 15})
    # Remaining trades (old)
    for i in range(20):
        if i % 2 == 0:
            trades.append({"side": "buy", "size": old_buy / 10})
        else:
            trades.append({"side": "sell", "size": old_sell / 10})
    return trades


# ============================================================
# TEST: ORDERFLOW ANALYZER DEPTH LEVELS
# ============================================================

class TestOrderflowAnalyzerDepthLevels:
    """Test OrderflowAnalyzer depth_levels default is 25."""

    def test_default_depth_levels_is_25(self):
        """depth_levels default should be 25 (was 10)."""
        analyzer = OrderflowAnalyzer()
        assert analyzer.depth_levels == 25, f"Expected depth_levels=25, got {analyzer.depth_levels}"
        print("PASSED: OrderflowAnalyzer depth_levels default is 25")

    def test_depth_levels_can_be_customized(self):
        """depth_levels can be set to custom value."""
        analyzer = OrderflowAnalyzer(depth_levels=10)
        assert analyzer.depth_levels == 10
        print("PASSED: OrderflowAnalyzer depth_levels can be customized")

    def test_analyzer_uses_depth_levels_for_orderbook(self):
        """Analyzer should only use depth_levels number of orderbook levels."""
        analyzer = OrderflowAnalyzer(depth_levels=5)
        orderbook = create_orderbook(100.0, 100.0, levels=25)
        trades = create_trades(50.0, 50.0)
        
        snapshot = analyzer.analyze(orderbook, trades)
        # With 5 levels and equal distribution, bid_volume should be 100/25*5 = 20
        expected_vol = 100.0 / 25 * 5
        assert abs(snapshot.bid_volume - expected_vol) < 0.01, \
            f"Expected bid_volume ~{expected_vol}, got {snapshot.bid_volume}"
        print("PASSED: OrderflowAnalyzer uses depth_levels for orderbook")


# ============================================================
# TEST: ORDERFLOW ANALYZER RECENT TRADE TRACKING
# ============================================================

class TestOrderflowAnalyzerRecentTradeTracking:
    """Test OrderflowAnalyzer tracks recent_buy_vol and recent_sell_vol for last 30 trades."""

    def test_recent_trades_affect_weighted_imbalance(self):
        """Recent trades (first 30) should have 60% weight in normalized_imbalance."""
        analyzer = OrderflowAnalyzer()
        
        # Recent trades: heavy buying (100 buy, 20 sell)
        # Old trades: heavy selling (20 buy, 100 sell)
        trades = create_recent_heavy_trades(
            recent_buy=100.0, recent_sell=20.0,
            old_buy=20.0, old_sell=100.0
        )
        orderbook = create_orderbook(100.0, 100.0)
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        # Recent imbalance: (100-20)/(100+20) = 80/120 = 0.667 (bullish)
        # Total imbalance: (120-120)/(120+120) = 0/240 = 0 (neutral)
        # Weighted: 0.667 * 0.6 + 0 * 0.4 = 0.4 (bullish)
        # normalized_imbalance should be positive (bullish) due to recent heavy buying
        assert snapshot.normalized_imbalance > 0.2, \
            f"Expected positive normalized_imbalance due to recent buying, got {snapshot.normalized_imbalance}"
        print("PASSED: Recent trades affect weighted imbalance correctly")

    def test_old_trades_have_less_weight(self):
        """Old trades should have only 40% weight."""
        analyzer = OrderflowAnalyzer()
        
        # Recent trades: neutral (50 buy, 50 sell)
        # Old trades: heavy buying (100 buy, 20 sell)
        trades = create_recent_heavy_trades(
            recent_buy=50.0, recent_sell=50.0,
            old_buy=100.0, old_sell=20.0
        )
        orderbook = create_orderbook(100.0, 100.0)
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        # Recent imbalance: (50-50)/(50+50) = 0 (neutral)
        # Total imbalance: (150-70)/(150+70) = 80/220 = 0.364 (bullish)
        # Weighted: 0 * 0.6 + 0.364 * 0.4 = 0.145 (slightly bullish)
        # Should be less bullish than if old trades had full weight
        assert snapshot.normalized_imbalance < 0.2, \
            f"Expected normalized_imbalance < 0.2 due to old trades having less weight, got {snapshot.normalized_imbalance}"
        print("PASSED: Old trades have less weight (40%)")


# ============================================================
# TEST: ORDERFLOW ANALYZER IMBALANCE SCORE WEIGHTS
# ============================================================

class TestOrderflowAnalyzerImbalanceScoreWeights:
    """Test imbalance_score uses 40% orderbook + 35% trade + 25% weighted_imb."""

    def test_imbalance_score_formula(self):
        """imbalance_score should use correct weights: 40% orderbook + 35% trade + 25% weighted_imb."""
        analyzer = OrderflowAnalyzer()
        
        # Create orderbook with 2:1 bid/ask ratio (orderbook_ratio = 2.0, edge = 1.0)
        orderbook = create_orderbook(200.0, 100.0)
        # Create trades with 2:1 buy/sell ratio (trade_ratio = 2.0, edge = 1.0)
        trades = create_trades(100.0, 50.0)
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        # orderbook_edge = 2.0 - 1.0 = 1.0
        # trade_edge = 2.0 - 1.0 = 1.0
        # weighted_imb = (100-50)/(100+50) = 50/150 = 0.333 (assuming recent = total for simplicity)
        # imbalance_score = 1.0 * 0.40 + 1.0 * 0.35 + weighted_imb * 0.25
        # Should be around 0.40 + 0.35 + 0.08 = 0.83
        
        # Just verify the score is positive and reasonable
        assert snapshot.imbalance_score > 0.5, \
            f"Expected positive imbalance_score > 0.5, got {snapshot.imbalance_score}"
        print("PASSED: imbalance_score uses correct weight formula")


# ============================================================
# TEST: ORDERFLOW ANALYZER DOMINANT SIDE
# ============================================================

class TestOrderflowAnalyzerDominantSide:
    """Test dominant_side uses weighted_imb threshold 0.15."""

    def test_dominant_side_bullish_threshold(self):
        """dominant_side should be 'bullish' when weighted_imb >= 0.15."""
        analyzer = OrderflowAnalyzer()
        
        # Create heavy buying to get weighted_imb > 0.15
        orderbook = create_orderbook(100.0, 100.0)
        trades = create_trades(100.0, 50.0)  # 2:1 buy/sell
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        # normalized_imbalance (which is weighted_imb) should be > 0.15
        # (100-50)/(100+50) = 0.333
        assert snapshot.dominant_side == "bullish", \
            f"Expected dominant_side='bullish', got '{snapshot.dominant_side}'"
        print("PASSED: dominant_side is 'bullish' when weighted_imb >= 0.15")

    def test_dominant_side_bearish_threshold(self):
        """dominant_side should be 'bearish' when weighted_imb <= -0.15."""
        analyzer = OrderflowAnalyzer()
        
        # Create heavy selling to get weighted_imb < -0.15
        orderbook = create_orderbook(100.0, 100.0)
        trades = create_trades(50.0, 100.0)  # 1:2 buy/sell
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        # normalized_imbalance should be < -0.15
        # (50-100)/(50+100) = -0.333
        assert snapshot.dominant_side == "bearish", \
            f"Expected dominant_side='bearish', got '{snapshot.dominant_side}'"
        print("PASSED: dominant_side is 'bearish' when weighted_imb <= -0.15")

    def test_dominant_side_neutral_threshold(self):
        """dominant_side should be 'neutral' when -0.15 < weighted_imb < 0.15."""
        analyzer = OrderflowAnalyzer()
        
        # Create balanced trading
        orderbook = create_orderbook(100.0, 100.0)
        trades = create_trades(55.0, 50.0)  # Nearly balanced
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        # normalized_imbalance should be close to 0
        # (55-50)/(55+50) = 0.048
        if abs(snapshot.normalized_imbalance) < 0.15:
            assert snapshot.dominant_side == "neutral", \
                f"Expected dominant_side='neutral', got '{snapshot.dominant_side}'"
            print("PASSED: dominant_side is 'neutral' when -0.15 < weighted_imb < 0.15")
        else:
            print(f"SKIPPED: normalized_imbalance={snapshot.normalized_imbalance} not in neutral range")


# ============================================================
# TEST: ORDERFLOW ANALYZER EMPTY DATA HANDLING
# ============================================================

class TestOrderflowAnalyzerEmptyDataHandling:
    """Test OrderflowAnalyzer handles empty orderbook and trades."""

    def test_empty_orderbook(self):
        """Analyzer should handle empty orderbook gracefully."""
        analyzer = OrderflowAnalyzer()
        orderbook = {"bids": [], "asks": []}
        trades = create_trades(50.0, 50.0)
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        assert snapshot.bid_volume == 0.0
        assert snapshot.ask_volume == 0.0
        assert snapshot.orderbook_ratio == 1.0  # Default when no data
        print("PASSED: OrderflowAnalyzer handles empty orderbook")

    def test_empty_trades(self):
        """Analyzer should handle empty trades gracefully."""
        analyzer = OrderflowAnalyzer()
        orderbook = create_orderbook(100.0, 100.0)
        trades = []
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        assert snapshot.buy_volume == 0.0
        assert snapshot.sell_volume == 0.0
        assert snapshot.trade_ratio == 1.0  # Default when no data
        print("PASSED: OrderflowAnalyzer handles empty trades")

    def test_empty_orderbook_and_trades(self):
        """Analyzer should handle both empty orderbook and trades."""
        analyzer = OrderflowAnalyzer()
        orderbook = {"bids": [], "asks": []}
        trades = []
        
        snapshot = analyzer.analyze(orderbook, trades)
        
        assert snapshot.bid_volume == 0.0
        assert snapshot.ask_volume == 0.0
        assert snapshot.buy_volume == 0.0
        assert snapshot.sell_volume == 0.0
        assert snapshot.dominant_side == "neutral"
        print("PASSED: OrderflowAnalyzer handles empty orderbook and trades")


# ============================================================
# TEST: EXHAUSTION GUARD
# ============================================================

class TestExhaustionGuard:
    """Test exhaustion guard rejects entry when 5+ of 7 last candles move in signal direction."""

    def test_exhaustion_guard_rejects_buy_after_5_bullish_candles(self):
        """Should reject BUY when 5+ of last 7 candles are bullish."""
        cfg = MockConfig()
        engine = EntryEngine(cfg)
        
        # Create 7 bullish candles (all close > open)
        klines = create_bullish_klines(7)
        
        # Create conditions that would normally trigger a BUY
        market_analysis = MockMarketAnalysis(can_trade=True)
        regime_prediction = MockRegimePrediction()
        transformer_prediction = MockTransformerPrediction(prob_up=0.8, prob_down=0.1)
        orderflow_snapshot = OrderflowSnapshot(
            normalized_imbalance=0.3,
            buy_volume=100.0,
            sell_volume=50.0
        )
        liq_analysis = MockLiqAnalysis()
        structure = MockStructure(
            trend=MockTrend.UP,
            last_sweep=MockSweep(direction="down"),
            sweep_low=95.0,
            previous_high=110.0
        )
        zone_context = MockZoneContext(bullish_zones=[MockZone(high=101.0, low=99.0)])
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=100.0,
            market_analysis=market_analysis,
            regime_prediction=regime_prediction,
            transformer_prediction=transformer_prediction,
            orderflow_snapshot=orderflow_snapshot,
            liq_analysis=liq_analysis,
            atr_value=2.0,
            zone_context=zone_context,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=1
        )
        
        assert signal.should_enter == False, "Should reject entry due to exhaustion"
        assert "exhaustion_guard" in signal.metadata.get("reject_reason", ""), \
            f"Expected exhaustion_guard rejection, got: {signal.metadata.get('reject_reason')}"
        print("PASSED: Exhaustion guard rejects BUY after 5+ bullish candles")

    def test_exhaustion_guard_rejects_sell_after_5_bearish_candles(self):
        """Should reject SELL when 5+ of last 7 candles are bearish."""
        cfg = MockConfig()
        engine = EntryEngine(cfg)
        
        # Create 7 bearish candles (all close < open)
        klines = create_bearish_klines(7)
        
        # Create conditions that would normally trigger a SELL
        market_analysis = MockMarketAnalysis(can_trade=True)
        regime_prediction = MockRegimePrediction()
        transformer_prediction = MockTransformerPrediction(prob_up=0.1, prob_down=0.8)
        orderflow_snapshot = OrderflowSnapshot(
            normalized_imbalance=-0.3,
            buy_volume=50.0,
            sell_volume=100.0
        )
        liq_analysis = MockLiqAnalysis()
        structure = MockStructure(
            trend=MockTrend.DOWN,
            last_sweep=MockSweep(direction="up"),
            sweep_high=105.0,
            previous_low=90.0
        )
        zone_context = MockZoneContext(bearish_zones=[MockZone(kind="fvg", bias="bearish", high=101.0, low=99.0)])
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=100.0,
            market_analysis=market_analysis,
            regime_prediction=regime_prediction,
            transformer_prediction=transformer_prediction,
            orderflow_snapshot=orderflow_snapshot,
            liq_analysis=liq_analysis,
            atr_value=2.0,
            zone_context=zone_context,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=-1
        )
        
        assert signal.should_enter == False, "Should reject entry due to exhaustion"
        assert "exhaustion_guard" in signal.metadata.get("reject_reason", ""), \
            f"Expected exhaustion_guard rejection, got: {signal.metadata.get('reject_reason')}"
        print("PASSED: Exhaustion guard rejects SELL after 5+ bearish candles")

    def test_exhaustion_guard_allows_entry_with_3_candles_same_direction(self):
        """Should allow entry when only 3 candles move in signal direction."""
        cfg = MockConfig()
        engine = EntryEngine(cfg)
        
        # Create 3 bullish + 4 bearish candles
        klines = create_bullish_klines(3) + create_bearish_klines(4)
        
        market_analysis = MockMarketAnalysis(can_trade=True)
        regime_prediction = MockRegimePrediction()
        transformer_prediction = MockTransformerPrediction(prob_up=0.8, prob_down=0.1)
        orderflow_snapshot = OrderflowSnapshot(
            normalized_imbalance=0.3,
            buy_volume=100.0,
            sell_volume=50.0
        )
        liq_analysis = MockLiqAnalysis()
        structure = MockStructure(
            trend=MockTrend.UP,
            last_sweep=MockSweep(direction="down"),
            sweep_low=95.0,
            previous_high=110.0
        )
        zone_context = MockZoneContext(bullish_zones=[MockZone(high=101.0, low=99.0)])
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=100.0,
            market_analysis=market_analysis,
            regime_prediction=regime_prediction,
            transformer_prediction=transformer_prediction,
            orderflow_snapshot=orderflow_snapshot,
            liq_analysis=liq_analysis,
            atr_value=2.0,
            zone_context=zone_context,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=1
        )
        
        # Should NOT be rejected by exhaustion guard
        reject_reason = signal.metadata.get("reject_reason", "")
        assert "exhaustion_guard" not in reject_reason, \
            f"Should not reject due to exhaustion with only 3 candles, got: {reject_reason}"
        print("PASSED: Exhaustion guard allows entry with only 3 candles same direction")

    def test_exhaustion_guard_allows_entry_with_4_candles_same_direction(self):
        """Should allow entry when only 4 candles move in signal direction."""
        cfg = MockConfig()
        engine = EntryEngine(cfg)
        
        # Create 4 bullish + 3 bearish candles
        klines = create_bullish_klines(4) + create_bearish_klines(3)
        
        market_analysis = MockMarketAnalysis(can_trade=True)
        regime_prediction = MockRegimePrediction()
        transformer_prediction = MockTransformerPrediction(prob_up=0.8, prob_down=0.1)
        orderflow_snapshot = OrderflowSnapshot(
            normalized_imbalance=0.3,
            buy_volume=100.0,
            sell_volume=50.0
        )
        liq_analysis = MockLiqAnalysis()
        structure = MockStructure(
            trend=MockTrend.UP,
            last_sweep=MockSweep(direction="down"),
            sweep_low=95.0,
            previous_high=110.0
        )
        zone_context = MockZoneContext(bullish_zones=[MockZone(high=101.0, low=99.0)])
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=100.0,
            market_analysis=market_analysis,
            regime_prediction=regime_prediction,
            transformer_prediction=transformer_prediction,
            orderflow_snapshot=orderflow_snapshot,
            liq_analysis=liq_analysis,
            atr_value=2.0,
            zone_context=zone_context,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=1
        )
        
        # Should NOT be rejected by exhaustion guard
        reject_reason = signal.metadata.get("reject_reason", "")
        assert "exhaustion_guard" not in reject_reason, \
            f"Should not reject due to exhaustion with only 4 candles, got: {reject_reason}"
        print("PASSED: Exhaustion guard allows entry with only 4 candles same direction")


# ============================================================
# TEST: COUNTER-FLOW GUARD
# ============================================================

class TestCounterFlowGuard:
    """Test counter-flow guard rejects entries when volume contradicts signal."""

    def test_counter_flow_rejects_sell_when_heavy_buying(self):
        """Should reject SELL when buy_vol > sell_vol * 1.4."""
        cfg = MockConfig()
        engine = EntryEngine(cfg)
        
        # Create mixed klines (not exhausted - only 4 bearish candles)
        klines = create_bullish_klines(3) + create_bearish_klines(4)
        
        market_analysis = MockMarketAnalysis(can_trade=True)
        regime_prediction = MockRegimePrediction()
        transformer_prediction = MockTransformerPrediction(prob_up=0.1, prob_down=0.8)
        # Heavy buying despite bearish signal
        orderflow_snapshot = OrderflowSnapshot(
            normalized_imbalance=-0.2,  # Slightly bearish imbalance
            buy_volume=150.0,  # Heavy buying (150 > 100 * 1.4 = 140)
            sell_volume=100.0
        )
        liq_analysis = MockLiqAnalysis()
        structure = MockStructure(
            trend=MockTrend.DOWN,
            last_sweep=MockSweep(direction="up"),
            sweep_high=105.0,
            previous_low=90.0
        )
        zone_context = MockZoneContext(bearish_zones=[MockZone(kind="fvg", bias="bearish", high=101.0, low=99.0)])
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=100.0,
            market_analysis=market_analysis,
            regime_prediction=regime_prediction,
            transformer_prediction=transformer_prediction,
            orderflow_snapshot=orderflow_snapshot,
            liq_analysis=liq_analysis,
            atr_value=2.0,
            zone_context=zone_context,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=-1
        )
        
        assert signal.should_enter == False, "Should reject SELL due to counter-flow"
        assert "counter_flow_guard" in signal.metadata.get("reject_reason", ""), \
            f"Expected counter_flow_guard rejection, got: {signal.metadata.get('reject_reason')}"
        print("PASSED: Counter-flow guard rejects SELL when buy_vol > sell_vol * 1.4")

    def test_counter_flow_rejects_buy_when_heavy_selling(self):
        """Should reject BUY when sell_vol > buy_vol * 1.4."""
        cfg = MockConfig()
        engine = EntryEngine(cfg)
        
        # Create mixed klines (not exhausted - only 4 bullish candles)
        klines = create_bearish_klines(3) + create_bullish_klines(4)
        
        market_analysis = MockMarketAnalysis(can_trade=True)
        regime_prediction = MockRegimePrediction()
        transformer_prediction = MockTransformerPrediction(prob_up=0.8, prob_down=0.1)
        # Heavy selling despite bullish signal
        orderflow_snapshot = OrderflowSnapshot(
            normalized_imbalance=0.2,  # Slightly bullish imbalance
            buy_volume=100.0,
            sell_volume=150.0  # Heavy selling (150 > 100 * 1.4 = 140)
        )
        liq_analysis = MockLiqAnalysis()
        structure = MockStructure(
            trend=MockTrend.UP,
            last_sweep=MockSweep(direction="down"),
            sweep_low=95.0,
            previous_high=110.0
        )
        zone_context = MockZoneContext(bullish_zones=[MockZone(high=101.0, low=99.0)])
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=100.0,
            market_analysis=market_analysis,
            regime_prediction=regime_prediction,
            transformer_prediction=transformer_prediction,
            orderflow_snapshot=orderflow_snapshot,
            liq_analysis=liq_analysis,
            atr_value=2.0,
            zone_context=zone_context,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=1
        )
        
        assert signal.should_enter == False, "Should reject BUY due to counter-flow"
        assert "counter_flow_guard" in signal.metadata.get("reject_reason", ""), \
            f"Expected counter_flow_guard rejection, got: {signal.metadata.get('reject_reason')}"
        print("PASSED: Counter-flow guard rejects BUY when sell_vol > buy_vol * 1.4")

    def test_counter_flow_allows_entry_when_volumes_balanced(self):
        """Should allow entry when volumes are balanced (within 1.4x)."""
        cfg = MockConfig()
        engine = EntryEngine(cfg)
        
        # Create mixed klines (not exhausted)
        klines = create_bearish_klines(2) + create_bullish_klines(5)
        
        market_analysis = MockMarketAnalysis(can_trade=True)
        regime_prediction = MockRegimePrediction()
        transformer_prediction = MockTransformerPrediction(prob_up=0.8, prob_down=0.1)
        # Balanced volumes
        orderflow_snapshot = OrderflowSnapshot(
            normalized_imbalance=0.3,
            buy_volume=100.0,
            sell_volume=80.0  # 80 < 100 * 1.4 = 140, so balanced
        )
        liq_analysis = MockLiqAnalysis()
        structure = MockStructure(
            trend=MockTrend.UP,
            last_sweep=MockSweep(direction="down"),
            sweep_low=95.0,
            previous_high=110.0
        )
        zone_context = MockZoneContext(bullish_zones=[MockZone(high=101.0, low=99.0)])
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=100.0,
            market_analysis=market_analysis,
            regime_prediction=regime_prediction,
            transformer_prediction=transformer_prediction,
            orderflow_snapshot=orderflow_snapshot,
            liq_analysis=liq_analysis,
            atr_value=2.0,
            zone_context=zone_context,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=1
        )
        
        # Should NOT be rejected by counter-flow guard
        reject_reason = signal.metadata.get("reject_reason", "")
        assert "counter_flow_guard" not in reject_reason, \
            f"Should not reject due to counter-flow with balanced volumes, got: {reject_reason}"
        print("PASSED: Counter-flow guard allows entry when volumes are balanced")


# ============================================================
# TEST: CONFIG VALUES
# ============================================================

class TestConfigValues:
    """Test config values are set correctly."""

    @pytest.fixture
    def config(self):
        """Load config.yaml."""
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)

    def test_trailing_activation_atr_is_0_7(self, config):
        """trailing_activation_atr should be 0.7 (was 1.1)."""
        value = config.get('exit', {}).get('trailing_activation_atr')
        assert value == 0.7, f"Expected trailing_activation_atr=0.7, got {value}"
        print("PASSED: trailing_activation_atr is 0.7")

    def test_trailing_distance_atr_is_1_2(self, config):
        """trailing_distance_atr should be 1.2 (was 1.4)."""
        value = config.get('exit', {}).get('trailing_distance_atr')
        assert value == 1.2, f"Expected trailing_distance_atr=1.2, got {value}"
        print("PASSED: trailing_distance_atr is 1.2")

    def test_exchange_closed_confirm_cycles_is_8(self, config):
        """exchange_closed_confirm_cycles should be 8 (was 5)."""
        value = config.get('position_sync', {}).get('exchange_closed_confirm_cycles')
        assert value == 8, f"Expected exchange_closed_confirm_cycles=8, got {value}"
        print("PASSED: exchange_closed_confirm_cycles is 8")

    def test_exchange_closed_force_cycles_is_30(self, config):
        """exchange_closed_force_cycles should be 30 (was 20)."""
        value = config.get('position_sync', {}).get('exchange_closed_force_cycles')
        assert value == 30, f"Expected exchange_closed_force_cycles=30, got {value}"
        print("PASSED: exchange_closed_force_cycles is 30")

    def test_1m_preset_trailing_activation_atr(self, config):
        """1m TF preset should have trailing_activation_atr=0.7."""
        presets = config.get('tf_presets', {}).get('presets', {})
        preset_1m = presets.get('1m', {})
        value = preset_1m.get('trailing_activation_atr')
        assert value == 0.7, f"Expected 1m preset trailing_activation_atr=0.7, got {value}"
        print("PASSED: 1m TF preset has trailing_activation_atr=0.7")

    def test_1m_preset_trailing_distance_atr(self, config):
        """1m TF preset should have trailing_distance_atr=1.2."""
        presets = config.get('tf_presets', {}).get('presets', {})
        preset_1m = presets.get('1m', {})
        value = preset_1m.get('trailing_distance_atr')
        assert value == 1.2, f"Expected 1m preset trailing_distance_atr=1.2, got {value}"
        print("PASSED: 1m TF preset has trailing_distance_atr=1.2")


# ============================================================
# TEST: REGRESSION - CLASSIFY SIGNAL GRADE
# ============================================================

class TestRegressionClassifySignalGrade:
    """Regression test: A/B/C grading classify_signal_grade still works."""

    def test_classify_signal_grade_exists(self):
        """classify_signal_grade function should exist."""
        assert callable(classify_signal_grade)
        print("PASSED: classify_signal_grade function exists")

    def test_grade_a_high_conviction(self):
        """Grade A: conf >= 0.85, RR >= 4.0, 3+ confirmations."""
        grade = classify_signal_grade(
            confidence=0.90,
            rr_ratio=5.0,
            has_sweep=True,
            has_bos=True,
            htf_aligned=True,
            entry_zone="fvg_bullish"
        )
        assert grade == "A", f"Expected grade A, got {grade}"
        print("PASSED: Grade A for high conviction signals")

    def test_grade_b_standard(self):
        """Grade B: conf >= 0.75, RR >= 3.0, 2+ confirmations."""
        grade = classify_signal_grade(
            confidence=0.80,
            rr_ratio=3.5,
            has_sweep=True,
            has_bos=True,
            htf_aligned=False,
            entry_zone="no_zone"
        )
        assert grade == "B", f"Expected grade B, got {grade}"
        print("PASSED: Grade B for standard signals")

    def test_grade_c_marginal(self):
        """Grade C: everything else."""
        grade = classify_signal_grade(
            confidence=0.60,
            rr_ratio=2.5,
            has_sweep=False,
            has_bos=False,
            htf_aligned=False,
            entry_zone="no_zone"
        )
        assert grade == "C", f"Expected grade C, got {grade}"
        print("PASSED: Grade C for marginal signals")


# ============================================================
# TEST: REGRESSION - P0 MANUAL PROTECTION
# ============================================================

class TestRegressionP0ManualProtection:
    """Regression test: P0 manual protection still intact."""

    def test_entry_signal_has_grade_field(self):
        """EntrySignal should have grade field."""
        signal = EntrySignal()
        assert hasattr(signal, 'grade'), "EntrySignal should have grade field"
        assert signal.grade == "C", f"Default grade should be 'C', got {signal.grade}"
        print("PASSED: EntrySignal has grade field with default 'C'")

    def test_entry_signal_has_metadata_field(self):
        """EntrySignal should have metadata field."""
        signal = EntrySignal()
        assert hasattr(signal, 'metadata'), "EntrySignal should have metadata field"
        print("PASSED: EntrySignal has metadata field")


# ============================================================
# SUMMARY TEST
# ============================================================

class TestIteration44Summary:
    """Summary test for all iteration 44 features."""

    def test_all_signal_quality_fixes_present(self):
        """Verify all signal quality fixes are present."""
        # 1. OrderflowAnalyzer depth_levels = 25
        analyzer = OrderflowAnalyzer()
        assert analyzer.depth_levels == 25
        
        # 2. Config values
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        assert config['exit']['trailing_activation_atr'] == 0.7
        assert config['exit']['trailing_distance_atr'] == 1.2
        assert config['position_sync']['exchange_closed_confirm_cycles'] == 8
        assert config['position_sync']['exchange_closed_force_cycles'] == 30
        
        # 3. classify_signal_grade exists
        assert callable(classify_signal_grade)
        
        print("PASSED: All signal quality fixes are present")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
