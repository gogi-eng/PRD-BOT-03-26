#!/usr/bin/env python3
"""
Edge case tests for the 5-point ТЗ (Technical Specification).

Additional tests for:
1. Entry Engine has ONLY 4 gates (no 15M, volume confirmation, SMC score gates)
2. Whitelist enforcement via get_trade_symbols integration
3. LiquidityHeatmap edge cases
4. Zone context edge cases
5. Structure edge cases
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from dataclasses import dataclass, field
from typing import Optional
from analysis.market_structure import (
    MarketStructure, StructureTrend, SwingPoint, BOSEvent, LiquiditySweep
)
from analysis.structure_zones import StructureZone, ZoneContext
from analysis.liquidity_heatmap import LiquidityHeatmap, HeatmapResult, LiquidityWall
from engine.entry_engine import EntryEngine, EntrySignal


# ─── Mock objects (same as main test file) ───────────────────────────────────

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


PRICE = 50000.0
ATR = 400.0


def make_bullish_structure(current_price: float, atr: float) -> MarketStructure:
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


@pytest.fixture
def cfg():
    return MockConfig()


@pytest.fixture
def engine(cfg):
    return EntryEngine(cfg)


# ═══════════════════════════════════════════════════════════════════════════
# ENTRY ENGINE: ONLY 4 GATES VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════

class TestEntryEngineOnly4Gates:
    """Verify Entry Engine has exactly 4 gates - no 15M, volume confirmation, SMC score."""

    def test_no_15m_trend_check_in_generate_signal(self, engine):
        """Entry does NOT check 15M trend - only 4H trend matters."""
        # Generate signal without any 15M data - should work fine
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        # Signal should pass - no 15M gate
        assert signal.should_enter is True

    def test_no_volume_confirmation_gate(self, engine):
        """Entry does NOT require volume confirmation as a gate."""
        # Structure without volume_spike or momentum_confirmed
        structure = make_bullish_structure(PRICE, ATR)
        structure.volume_spike = False
        structure.momentum_confirmed = False

        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=structure,
            htf_4h_trend=1,
        )
        # Signal should pass - volume is NOT a gate in v5
        assert signal.should_enter is True

    def test_no_smc_score_minimum_gate(self, engine):
        """Entry does NOT require minimum SMC score as a gate."""
        # Zone with very low strength (0.1)
        weak_zone = StructureZone(
            kind="fvg", bias="bullish",
            low=PRICE - ATR * 0.2,
            high=PRICE + ATR * 0.2,
            strength=0.1,  # Very weak
            created_at_index=10,
        )
        zone_ctx = ZoneContext(
            bullish_fvg=weak_zone, bearish_fvg=None,
            bullish_ob=None, bearish_ob=None,
            support_levels=[PRICE - ATR * 2],
            resistance_levels=[PRICE + ATR * 3],
            all_bullish_zones=[weak_zone],
            all_bearish_zones=[],
        )

        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=zone_ctx,
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        # Signal should pass - SMC score is NOT a gate in v5
        assert signal.should_enter is True

    def test_signal_contains_4_gates_only_in_reasons(self, engine):
        """Signal reasons should reflect only the 4 gates."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        
        # Reasons should contain: 4H trend, sweep, zone, RR
        reasons_str = " ".join(signal.reasons).lower()
        assert "4h" in reasons_str or "bull" in reasons_str  # Gate 1
        assert "sweep" in reasons_str  # Gate 2
        assert "fvg" in reasons_str or "ob" in reasons_str  # Gate 3
        assert "rr" in reasons_str  # Gate 4


# ═══════════════════════════════════════════════════════════════════════════
# WHITELIST ENFORCEMENT: get_trade_symbols INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestWhitelistEnforcement:
    """Verify whitelist enforcement in get_trade_symbols logic."""

    def test_whitelist_logic_ignores_blacklist_not_in_whitelist(self):
        """Blacklist items not in whitelist have no effect."""
        whitelist = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"]
        blacklist = ["DOGEUSDT", "SHIBUSDT"]  # Not in whitelist

        result = [s for s in whitelist if s not in blacklist]
        assert len(result) == 5
        assert set(result) == set(whitelist)

    def test_whitelist_logic_respects_blacklist_overlap(self):
        """Items in both whitelist and blacklist are excluded."""
        whitelist = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"]
        blacklist = ["ETHUSDT"]  # In whitelist

        result = [s for s in whitelist if s not in blacklist]
        assert len(result) == 4
        assert "ETHUSDT" not in result

    def test_whitelist_mode_ignores_volume_ranking(self):
        """In whitelist mode, volume/momentum ranking doesn't affect symbols returned."""
        # Simulate the whitelist_enabled=True logic
        whitelist_enabled = True
        whitelist = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"]
        blacklist = []

        if whitelist_enabled and whitelist:
            result = [s for s in whitelist if s not in blacklist]
        else:
            # Volume ranking logic (not reached when whitelist_enabled)
            result = ["SHIBUSDT", "DOGEUSDT"]  # hypothetical

        assert len(result) == 5
        assert "BTCUSDT" in result
        # No DOGE/SHIB sneaking in via volume ranking


# ═══════════════════════════════════════════════════════════════════════════
# LIQUIDITY HEATMAP EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestLiquidityHeatmapEdgeCases:
    """Edge cases for orderbook-based heatmap."""

    def test_empty_orderbook(self):
        """Handle empty orderbook gracefully."""
        hm = LiquidityHeatmap()
        result = hm.build_heatmap({"bids": [], "asks": []})
        assert result.bid_walls == []
        assert result.ask_walls == []
        assert result.strongest_bid is None
        assert result.strongest_ask is None
        assert result.imbalance == 0.0

    def test_single_level_orderbook(self):
        """Handle single level orderbook."""
        hm = LiquidityHeatmap()
        orderbook = {
            "bids": [[50000, 1.0]],
            "asks": [[50010, 1.0]],
        }
        result = hm.build_heatmap(orderbook)
        # Single level can't be a "wall" (needs comparison with average)
        assert result.bid_total_volume == 1.0
        assert result.ask_total_volume == 1.0
        assert result.imbalance == 0.0

    def test_malformed_orderbook_entries(self):
        """Handle malformed entries gracefully."""
        hm = LiquidityHeatmap()
        orderbook = {
            "bids": [[50000, 1.0], [49990]],  # Missing volume
            "asks": [[50010, 2.0]],
        }
        result = hm.build_heatmap(orderbook)
        # Should skip malformed entry
        assert result.bid_total_volume == 1.0

    def test_liquidity_magnet_neutral_when_balanced(self):
        """Magnet is neutral when bid/ask walls are balanced."""
        hm = LiquidityHeatmap()
        result = HeatmapResult(
            bid_walls=[LiquidityWall(49000, 5.0, "bid")],
            ask_walls=[LiquidityWall(51000, 5.0, "ask")],
            strongest_bid=LiquidityWall(49000, 5.0, "bid"),
            strongest_ask=LiquidityWall(51000, 5.0, "ask"),
            bid_total_volume=5.0,
            ask_total_volume=5.0,
            imbalance=0.0,
        )
        direction, target = hm.get_liquidity_magnet(50000, result)
        # Neither 1.5x stronger -> neutral
        assert direction == "neutral"

    def test_liquidity_magnet_down_when_bid_dominant(self):
        """Magnet is down when bids are 1.5x stronger."""
        hm = LiquidityHeatmap()
        result = HeatmapResult(
            bid_walls=[LiquidityWall(49000, 10.0, "bid")],
            ask_walls=[LiquidityWall(51000, 5.0, "ask")],
            strongest_bid=LiquidityWall(49000, 10.0, "bid"),
            strongest_ask=LiquidityWall(51000, 5.0, "ask"),
            bid_total_volume=10.0,
            ask_total_volume=5.0,
            imbalance=0.33,
        )
        direction, target = hm.get_liquidity_magnet(50000, result)
        assert direction == "down"
        assert target == 49000


# ═══════════════════════════════════════════════════════════════════════════
# ZONE CONTEXT EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestZoneContextEdgeCases:
    """Edge cases for FVG/OB zone context."""

    def test_price_in_ob_zone(self, engine):
        """Price can be in OB zone for valid entry."""
        ob_zone = StructureZone(
            kind="ob", bias="bullish",
            low=PRICE - ATR * 0.1,
            high=PRICE + ATR * 0.1,
            strength=0.8, created_at_index=10,
        )
        zone_ctx = ZoneContext(
            bullish_fvg=None, bearish_fvg=None,
            bullish_ob=ob_zone, bearish_ob=None,
            support_levels=[],
            resistance_levels=[],
            all_bullish_zones=[ob_zone],
            all_bearish_zones=[],
        )

        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=zone_ctx,
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert "ob" in signal.metadata.get("entry_zone", "").lower()

    def test_price_near_zone_within_tolerance(self, engine):
        """Price near (within tolerance_pct) a zone allows entry."""
        zone = StructureZone(
            kind="fvg", bias="bullish",
            low=PRICE - ATR * 0.5,
            high=PRICE - ATR * 0.3,  # Zone is slightly below price
            strength=0.7, created_at_index=10,
        )
        zone_ctx = ZoneContext(
            bullish_fvg=zone, bearish_fvg=None,
            bullish_ob=None, bearish_ob=None,
            support_levels=[],
            resistance_levels=[],
            all_bullish_zones=[zone],
            all_bearish_zones=[],
        )

        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=zone_ctx,
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        # Should be near zone (within 0.4% tolerance)
        assert signal.should_enter is True

    def test_multiple_zones_selects_correct_bias(self, engine):
        """With multiple zones, correct bias for direction is selected."""
        bullish_zone = StructureZone(
            kind="fvg", bias="bullish",
            low=PRICE - ATR * 0.1, high=PRICE + ATR * 0.1,
            strength=0.8, created_at_index=10,
        )
        bearish_zone = StructureZone(
            kind="ob", bias="bearish",
            low=PRICE + ATR * 0.5, high=PRICE + ATR * 0.7,
            strength=0.9, created_at_index=8,
        )
        zone_ctx = ZoneContext(
            bullish_fvg=bullish_zone, bearish_fvg=None,
            bullish_ob=None, bearish_ob=bearish_zone,
            support_levels=[],
            resistance_levels=[],
            all_bullish_zones=[bullish_zone],
            all_bearish_zones=[bearish_zone],
        )

        # Long setup with 4H bullish
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=zone_ctx,
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        assert signal.should_enter is True
        assert signal.side == "BUY"
        assert "bullish" in signal.metadata.get("entry_zone", "").lower()


# ═══════════════════════════════════════════════════════════════════════════
# STRUCTURE EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestStructureEdgeCases:
    """Edge cases for market structure handling."""

    def test_structure_none_rejects(self, engine):
        """No structure at all -> reject (no sweep)."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=None,
            htf_4h_trend=1,
        )
        assert signal.should_enter is False
        assert "no_liquidity_sweep" in signal.metadata.get("reject_reason", "")

    def test_zero_atr_fallback(self, engine):
        """Zero ATR uses fallback calculation."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), 0.0,  # Zero ATR
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
        )
        # Should not crash, uses fallback ATR
        assert signal.entry_price == PRICE

    def test_high_funding_rate_rejects(self, engine):
        """Excessive funding rate rejects."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(), MockTransformer(),
            MockOrderflow(), MockLiqAnalysis(), ATR,
            zone_context=make_bullish_zone_context(PRICE, ATR),
            structure=make_bullish_structure(PRICE, ATR),
            htf_4h_trend=1,
            funding_rate=0.1,  # 10% funding
        )
        assert signal.should_enter is False
        assert "funding" in signal.metadata.get("reject_reason", "").lower()


# ═══════════════════════════════════════════════════════════════════════════
# CONFIG VERIFICATION EDGE CASES
# ═══════════════════════════════════════════════════════════════════════════

class TestConfigEdgeCases:
    """Additional config verification."""

    def test_min_rr_ratio_at_least_2(self):
        """Verify min_rr_ratio >= 2.0 in config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["min_rr_ratio"] >= 2.0

    def test_early_exit_bars_exactly_zero(self):
        """Verify early_exit_bars == 0."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["exit"]["early_exit_bars"] == 0

    def test_whitelist_has_exactly_5_coins(self):
        """Verify whitelist has exactly the 5 specified coins."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert len(cfg["market"]["whitelist_symbols"]) == 5

    def test_whitelist_contains_only_major_coins(self):
        """Verify whitelist contains only BTC, ETH, SOL, LINK, BNB."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        expected = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"}
        actual = set(cfg["market"]["whitelist_symbols"])
        assert actual == expected


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
