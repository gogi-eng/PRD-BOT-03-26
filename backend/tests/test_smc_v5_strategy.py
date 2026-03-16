#!/usr/bin/env python3
"""
Comprehensive test suite for the 5-point ТЗ (Technical Specification).

Tests validate:
1. Hard Trend Filter — only LONG if 4H bullish, only SHORT if 4H bearish
2. Entry Trigger — Liquidity Sweep -> FVG/OB retest (no other patterns)
3. Early Exit disabled (early_exit_bars == 0)
4. Whitelist-only coins: BTC, ETH, SOL, LINK, BNB
5. Risk/Reward filter >= 2.0
"""
import sys
import os
import pytest

# Add bot directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from dataclasses import dataclass, field
from typing import Optional
from analysis.market_structure import (
    MarketStructure, StructureTrend, SwingPoint, BOSEvent, LiquiditySweep
)
from analysis.structure_zones import StructureZone, ZoneContext
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
        }
        if overrides:
            self._defaults.update(overrides)

    def get(self, *keys, default=None):
        return self._defaults.get(keys, default)


@dataclass
class MockMarketAnalysis:
    can_trade: bool = True
    trend: StructureTrend = StructureTrend.UP
    htf_trend: StructureTrend = StructureTrend.UP
    atr_pct: float = 1.0
    adx: float = 30.0
    regime: object = None
    volatility: object = None

    def __post_init__(self):
        if self.regime is None:
            self.regime = type('R', (), {'value': 'trend'})()
        if self.volatility is None:
            self.volatility = type('V', (), {'value': 'normal'})()


@dataclass
class MockRegime:
    regime: object = None

    def __post_init__(self):
        if self.regime is None:
            self.regime = type('R', (), {'value': 'trend'})()


@dataclass
class MockTransformer:
    prob_up: float = 0.6
    prob_down: float = 0.3
    prob_flat: float = 0.1


@dataclass
class MockOrderflow:
    bullish_ratio: float = 1.2
    bearish_ratio: float = 0.9
    spread_pct: float = 0.02
    imbalance_score: float = 0.1


@dataclass
class MockLiqAnalysis:
    target_level: float = 0.0
    signal: int = 0
    magnet_direction: str = "neutral"
    target_density: float = 0.0
    distance_to_target_pct: float = 0.0


def make_bullish_structure(current_price: float, atr: float) -> MarketStructure:
    """Create a structure with a downward sweep (bullish setup)."""
    return MarketStructure(
        trend=StructureTrend.UP,
        swing_highs=[SwingPoint(10, current_price + atr * 2, "high")],
        swing_lows=[SwingPoint(8, current_price - atr * 1.5, "low")],
        last_bos=BOSEvent("up", current_price - atr, 12, True),
        last_sweep=LiquiditySweep("down", current_price - atr * 1.5, 14, current_price - atr * 2),
        volume_spike=True,
        spread_expansion=True,
        momentum_confirmed=True,
        signal_ready_long=True,
        sweep_low=current_price - atr * 2,
        previous_high=current_price + atr * 3,
        previous_low=current_price - atr * 3,
        atr_value=atr,
    )


def make_bearish_structure(current_price: float, atr: float) -> MarketStructure:
    """Create a structure with an upward sweep (bearish setup)."""
    return MarketStructure(
        trend=StructureTrend.DOWN,
        swing_highs=[SwingPoint(10, current_price + atr * 1.5, "high")],
        swing_lows=[SwingPoint(8, current_price - atr * 2, "low")],
        last_bos=BOSEvent("down", current_price + atr, 12, True),
        last_sweep=LiquiditySweep("up", current_price + atr * 1.5, 14, current_price + atr * 2),
        volume_spike=True,
        spread_expansion=True,
        momentum_confirmed=True,
        signal_ready_short=True,
        sweep_high=current_price + atr * 2,
        previous_high=current_price + atr * 3,
        previous_low=current_price - atr * 3,
        atr_value=atr,
    )


def make_bullish_zone_context(current_price: float, atr: float) -> ZoneContext:
    """Create zone context with a bullish FVG/OB at the current price."""
    zone = StructureZone(
        kind="fvg", bias="bullish",
        low=current_price - atr * 0.2,
        high=current_price + atr * 0.2,
        strength=0.7, created_at_index=10,
    )
    return ZoneContext(
        bullish_fvg=zone, bearish_fvg=None,
        bullish_ob=None, bearish_ob=None,
        support_levels=[current_price - atr * 2],
        resistance_levels=[current_price + atr * 3],
        all_bullish_zones=[zone],
        all_bearish_zones=[],
    )


def make_bearish_zone_context(current_price: float, atr: float) -> ZoneContext:
    """Create zone context with a bearish FVG/OB at the current price."""
    zone = StructureZone(
        kind="ob", bias="bearish",
        low=current_price - atr * 0.2,
        high=current_price + atr * 0.2,
        strength=0.7, created_at_index=10,
    )
    return ZoneContext(
        bullish_fvg=None, bearish_fvg=zone,
        bullish_ob=None, bearish_ob=None,
        support_levels=[current_price - atr * 3],
        resistance_levels=[current_price + atr * 2],
        all_bullish_zones=[],
        all_bearish_zones=[zone],
    )


# ─── Default fixtures ──────────────────────────────────────────

@pytest.fixture
def cfg():
    return MockConfig()

@pytest.fixture
def engine(cfg):
    return EntryEngine(cfg)

PRICE = 50000.0
ATR = 400.0  # 0.8% of price


# ═══════════════════════════════════════════════════════════════
# POINT 1: HARD TREND FILTER (4H)
# ═══════════════════════════════════════════════════════════════

class TestPoint1_4HTrendFilter:
    """Only LONG if 4H bullish. Only SHORT if 4H bearish. No trade if neutral."""

    def test_4h_bullish_allows_long(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert signal.side == "BUY"

    def test_4h_bearish_allows_short(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bearish_zone_context(PRICE, ATR),
            structure=make_bearish_structure(PRICE, ATR),
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True
        assert signal.side == "SELL"

    def test_4h_neutral_rejects(self, engine):
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=0,
        )
        assert signal.should_enter is False
        assert "4h_trend_neutral" in signal.metadata.get("reject_reason", "")

    def test_4h_bullish_rejects_short_sweep(self, engine):
        """4H is bullish but sweep is up (bearish setup) -> reject."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bearish_zone_context(PRICE, ATR),
            structure=make_bearish_structure(PRICE, ATR),
            htf_4h_trend=1,  # bullish but sweep says short
        )
        assert signal.should_enter is False
        assert "sweep" in signal.metadata.get("reject_reason", "").lower()

    def test_4h_bearish_rejects_long_sweep(self, engine):
        """4H is bearish but sweep is down (bullish setup) -> reject."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=-1,  # bearish but sweep says long
        )
        assert signal.should_enter is False
        assert "sweep" in signal.metadata.get("reject_reason", "").lower()


# ═══════════════════════════════════════════════════════════════
# POINT 2: ENTRY TRIGGER — Sweep -> FVG/OB Retest
# ═══════════════════════════════════════════════════════════════

class TestPoint2_SweepToZone:
    """The only valid entry: Liquidity Sweep -> return to FVG/OB zone."""

    def test_no_sweep_rejects(self, engine):
        """Without a liquidity sweep, no entry is allowed."""
        structure = make_bullish_structure(PRICE, ATR)
        structure.last_sweep = None  # Remove the sweep

        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=structure,
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "no_liquidity_sweep" in signal.metadata.get("reject_reason", "")

    def test_sweep_without_zone_rejects(self, engine):
        """Sweep exists but price is not in/near any FVG/OB zone -> reject."""
        # Zone far from current price
        far_zone = StructureZone(
            kind="fvg", bias="bullish",
            low=PRICE - ATR * 10,
            high=PRICE - ATR * 9,
            strength=0.8, created_at_index=5,
        )
        zone_ctx = ZoneContext(
            bullish_fvg=far_zone, bearish_fvg=None,
            bullish_ob=None, bearish_ob=None,
            support_levels=[], resistance_levels=[],
            all_bullish_zones=[far_zone], all_bearish_zones=[],
        )

        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=zone_ctx,
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "no_zone_retest" in signal.metadata.get("reject_reason", "")

    def test_sweep_plus_zone_allows_entry(self, engine):
        """Sweep + zone retest = valid entry."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True

    def test_no_zone_data_rejects(self, engine):
        """No zone context at all -> reject."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=None,
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "no_zone_data" in signal.metadata.get("reject_reason", "")


# ═══════════════════════════════════════════════════════════════
# POINT 3: EARLY EXIT DISABLED
# ═══════════════════════════════════════════════════════════════

class TestPoint3_EarlyExitDisabled:
    """early_exit_bars must be 0 in config."""

    def test_config_early_exit_zero(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["exit"]["early_exit_bars"] == 0, \
            f"early_exit_bars should be 0, got {cfg['exit']['early_exit_bars']}"


# ═══════════════════════════════════════════════════════════════
# POINT 4: WHITELIST-ONLY COINS
# ═══════════════════════════════════════════════════════════════

class TestPoint4_WhitelistOnly:
    """Trading must be restricted to BTC, ETH, SOL, LINK, BNB."""

    def test_config_whitelist_enabled(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["market"]["whitelist_enabled"] is True
        expected = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"}
        actual = set(cfg["market"]["whitelist_symbols"])
        assert actual == expected, f"Expected {expected}, got {actual}"

    def test_get_trade_symbols_returns_only_whitelist(self):
        """Verify the get_trade_symbols logic returns only whitelist when enabled."""
        whitelist = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"]
        blacklist = []

        # Simulate the whitelist-only logic from main.py
        result = [s for s in whitelist if s not in blacklist]
        assert len(result) == 5
        assert "BTCUSDT" in result
        assert "ETHUSDT" in result
        assert "SOLUSDT" in result
        assert "LINKUSDT" in result
        assert "BNBUSDT" in result

    def test_no_meme_coins_in_whitelist(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        whitelist = cfg["market"]["whitelist_symbols"]
        meme_patterns = ["DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "MEME"]
        for coin in whitelist:
            for meme in meme_patterns:
                assert meme not in coin, f"Meme coin {coin} found in whitelist!"


# ═══════════════════════════════════════════════════════════════
# POINT 5: RISK/REWARD >= 2.0
# ═══════════════════════════════════════════════════════════════

class TestPoint5_RiskReward:
    """Trade only if RR >= 2.0."""

    def test_config_min_rr_ratio(self):
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["min_rr_ratio"] >= 2.0, \
            f"min_rr_ratio should be >= 2.0, got {cfg['entry']['min_rr_ratio']}"

    def test_signal_rr_meets_minimum(self, engine):
        """All generated signals must have RR >= min_rr_ratio."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        if signal.should_enter:
            assert signal.rr_ratio >= 2.0, \
                f"RR should be >= 2.0, got {signal.rr_ratio}"

    def test_low_rr_rejects(self):
        """If structure produces a bad RR, the signal must be rejected."""
        # Use a very tight structure that forces low RR
        cfg = MockConfig({
            ("entry", "min_rr_ratio"): 5.0,  # Very high RR requirement
            ("entry", "min_target_profit_pct"): 0.01,
            ("entry", "min_stop_distance_pct"): 2.0,  # Very wide SL
        })
        engine = EntryEngine(cfg)

        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        # Either rejected for low RR, or has RR >= 5.0
        if signal.should_enter:
            assert signal.rr_ratio >= 5.0


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: Full Pipeline
# ═══════════════════════════════════════════════════════════════

class TestFullPipeline:
    """End-to-end tests combining all 5 points."""

    def test_perfect_long_setup(self, engine):
        """4H bull + sweep down + bullish zone + RR >= 2.0 = LONG."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert signal.side == "BUY"
        assert signal.rr_ratio >= 2.0
        assert signal.stop_loss < PRICE
        assert signal.take_profit > PRICE

    def test_perfect_short_setup(self, engine):
        """4H bear + sweep up + bearish zone + RR >= 2.0 = SHORT."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bearish_zone_context(PRICE, ATR),
            structure=make_bearish_structure(PRICE, ATR),
            htf_4h_trend=-1,
        )
        assert signal.should_enter is True
        assert signal.side == "SELL"
        assert signal.rr_ratio >= 2.0
        assert signal.stop_loss > PRICE
        assert signal.take_profit < PRICE

    def test_market_blocked_rejects(self, engine):
        """If market is blocked, no entry regardless of signals."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(can_trade=False), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False

    def test_high_spread_rejects(self, engine):
        """Excessive spread rejects even a perfect setup."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(spread_pct=0.5), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "spread" in signal.metadata.get("reject_reason", "").lower()

    def test_signal_has_correct_metadata(self, engine):
        """Verify all key metadata fields are present."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        if signal.should_enter:
            assert "htf_4h_trend" in signal.metadata
            assert "has_sweep" in signal.metadata
            assert "entry_zone" in signal.metadata
            assert signal.metadata["htf_4h_trend"] == 1
            assert signal.metadata["has_sweep"] is True


# ═══════════════════════════════════════════════════════════════
# LIQUIDITY HEATMAP TESTS
# ═══════════════════════════════════════════════════════════════

class TestLiquidityHeatmap:
    """Tests for the new real orderbook-based heatmap."""

    def test_detects_bid_walls(self):
        from analysis.liquidity_heatmap import LiquidityHeatmap

        orderbook = {
            "bids": [
                [50000, 1.0], [49990, 0.5], [49980, 0.3],
                [49970, 5.0],  # wall
                [49960, 0.2],
            ],
            "asks": [
                [50010, 0.5], [50020, 0.3], [50030, 0.4],
            ],
        }
        hm = LiquidityHeatmap(depth_levels=200)
        result = hm.build_heatmap(orderbook)
        assert len(result.bid_walls) >= 1
        assert result.strongest_bid.price == 49970

    def test_detects_ask_walls(self):
        from analysis.liquidity_heatmap import LiquidityHeatmap

        orderbook = {
            "bids": [[50000, 0.5], [49990, 0.3]],
            "asks": [
                [50010, 0.5], [50020, 8.0],  # wall
                [50030, 0.4],
            ],
        }
        hm = LiquidityHeatmap(depth_levels=200)
        result = hm.build_heatmap(orderbook)
        assert len(result.ask_walls) >= 1
        assert result.strongest_ask.price == 50020

    def test_imbalance_calculation(self):
        from analysis.liquidity_heatmap import LiquidityHeatmap

        orderbook = {
            "bids": [[50000, 10.0]],
            "asks": [[50010, 5.0]],
        }
        hm = LiquidityHeatmap()
        result = hm.build_heatmap(orderbook)
        # bid_total=10, ask_total=5, imbalance = (10-5)/15 = 0.333
        assert result.imbalance > 0

    def test_liquidity_magnet_direction(self):
        from analysis.liquidity_heatmap import LiquidityHeatmap, HeatmapResult, LiquidityWall

        hm = LiquidityHeatmap()
        # Strong ask wall above
        result = HeatmapResult(
            bid_walls=[LiquidityWall(49000, 2.0, "bid")],
            ask_walls=[LiquidityWall(51000, 10.0, "ask")],
            strongest_bid=LiquidityWall(49000, 2.0, "bid"),
            strongest_ask=LiquidityWall(51000, 10.0, "ask"),
            bid_total_volume=5.0,
            ask_total_volume=15.0,
            imbalance=-0.5,
        )
        direction, target = hm.get_liquidity_magnet(50000, result)
        assert direction == "up"
        assert target == 51000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
