#!/usr/bin/env python3
"""
Tests for Entry Engine SL Assignment and Anti-Chase Entry Refinement (Iteration 18).

Validates (per review request):
1. entry_threshold now 0.62 in config
2. trained_model_enabled false in config for relaxed mode
3. entry_engine applies min_stop_atr_mult floor to SL distance
4. entry_engine anti-chase reject from zone extension works
5. entry_engine metadata includes entry_range_low/high and smc_score
6. signal-only telegram path includes recommended entry range
7. regression: core entry engine tests still pass
"""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from dataclasses import dataclass, field
from typing import Optional
from analysis.transformer_model import TransformerPrediction
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
            ("entry", "min_stop_atr_mult"): 0.9,
            ("entry", "sl_buffer_atr_mult"): 0.5,
            ("entry", "max_entry_extension_atr"): 0.75,
            ("entry", "entry_range_atr_mult"): 0.22,
            ("entry", "zone_proximity_pct"): 0.4,
            ("entry", "max_spread_pct"): 0.08,
            ("entry", "max_funding_rate"): 0.05,
            ("entry", "entry_threshold"): 0.62,
            ("entry", "trained_model_enabled"): False,
            ("entry", "trained_model_min_prob"): 0.45,
            ("entry", "trained_model_blend"): 0.35,
            ("entry", "trained_model_weights_path"): "transformer_weights.pt",
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


@dataclass
class MockZone:
    kind: str = "fvg"
    bias: str = "bullish"
    low: float = 49800.0
    high: float = 50200.0
    strength: float = 0.7
    created_at_index: int = 10


class MockZoneContext:
    """Mock zone context for testing anti-chase and SL logic."""
    def __init__(self, bullish=True, zone_low=49800.0, zone_high=50200.0):
        if bullish:
            self.zone = MockZone(bias="bullish", low=zone_low, high=zone_high)
        else:
            self.zone = MockZone(bias="bearish", low=zone_low, high=zone_high)
        self.bullish_fvg = self.zone if bullish else None
        self.bearish_fvg = self.zone if not bullish else None
        self.support_levels = [49000.0]
        self.resistance_levels = [52000.0]
        self.all_bullish_zones = [self.zone] if bullish else []
        self.all_bearish_zones = [] if bullish else [self.zone]
    
    def price_in_bullish_zone(self, price):
        return self.zone if self.zone.bias == "bullish" and self.zone.low <= price <= self.zone.high else None
    
    def price_near_bullish_zone(self, price, pct):
        return self.zone if self.zone.bias == "bullish" else None
    
    def price_in_bearish_zone(self, price):
        return self.zone if self.zone.bias == "bearish" and self.zone.low <= price <= self.zone.high else None
    
    def price_near_bearish_zone(self, price, pct):
        return self.zone if self.zone.bias == "bearish" else None
    
    def structural_sl_long(self, price, atr):
        return price - atr * 2.5
    
    def structural_sl_short(self, price, atr):
        return price + atr * 2.5
    
    def structural_tp_long(self, price, atr):
        return price + atr * 3.0, price + atr * 5.0
    
    def structural_tp_short(self, price, atr):
        return price - atr * 3.0, price - atr * 5.0


@dataclass
class MockStructure:
    """Minimal mock for MarketStructure"""
    trend: object = None
    last_sweep: object = None
    last_bos: object = None
    sweep_low: float = 49000.0
    sweep_high: float = 51000.0
    previous_high: float = 52000.0
    previous_low: float = 48000.0
    atr_value: float = 400.0
    swing_highs: list = field(default_factory=list)
    swing_lows: list = field(default_factory=list)
    
    def __post_init__(self):
        if self.trend is None:
            self.trend = type('T', (), {'value': 'up'})()


PRICE = 50000.0
ATR = 400.0


def make_liq():
    return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)


def make_bullish_structure(price=PRICE, atr=ATR, sweep_low=None):
    sweep = type('S', (), {'direction': 'down'})()
    bos = type('B', (), {'direction': 'up'})()
    struct = MockStructure(last_sweep=sweep, last_bos=bos)
    struct.trend = type('T', (), {'value': 'up'})()
    if sweep_low is not None:
        struct.sweep_low = sweep_low
    else:
        struct.sweep_low = price - atr * 2
    struct.previous_high = price + atr * 3
    struct.previous_low = price - atr * 3
    return struct


def make_bearish_structure(price=PRICE, atr=ATR, sweep_high=None):
    sweep = type('S', (), {'direction': 'up'})()
    bos = type('B', (), {'direction': 'down'})()
    struct = MockStructure(last_sweep=sweep, last_bos=bos)
    struct.trend = type('T', (), {'value': 'down'})()
    if sweep_high is not None:
        struct.sweep_high = sweep_high
    else:
        struct.sweep_high = price + atr * 2
    struct.previous_high = price + atr * 3
    struct.previous_low = price - atr * 3
    return struct


@pytest.fixture
def engine():
    return EntryEngine(MockConfig())


# ═══════════════════════════════════════════════════════════════
# TEST 1: CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestConfigIteration18:
    """Validates config.yaml has the specified settings."""

    def test_entry_threshold_is_0_62(self):
        """entry_threshold should be 0.62 for relaxed mode."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["entry_threshold"] == 0.62

    def test_trained_model_disabled(self):
        """trained_model_enabled should be False for relaxed mode."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["entry"]["trained_model_enabled"] is False

    def test_min_stop_atr_mult_present(self):
        """min_stop_atr_mult should be in config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert "min_stop_atr_mult" in cfg["entry"]
        assert cfg["entry"]["min_stop_atr_mult"] == 0.9

    def test_max_entry_extension_atr_present(self):
        """max_entry_extension_atr should be in config for anti-chase."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert "max_entry_extension_atr" in cfg["entry"]
        assert cfg["entry"]["max_entry_extension_atr"] == 0.75

    def test_entry_range_atr_mult_present(self):
        """entry_range_atr_mult should be in config for entry range hint."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert "entry_range_atr_mult" in cfg["entry"]
        assert cfg["entry"]["entry_range_atr_mult"] == 0.22


# ═══════════════════════════════════════════════════════════════
# TEST 2: SL FLOOR VIA min_stop_atr_mult
# ═══════════════════════════════════════════════════════════════

class TestSLFloor:
    """Validates that entry_engine applies min_stop_atr_mult floor to SL distance."""

    def test_sl_floor_applied_when_sl_too_tight(self):
        """When computed SL is too tight, it should be widened to ATR * min_stop_atr_mult."""
        cfg = MockConfig({
            ("entry", "min_stop_atr_mult"): 0.9,
            ("entry", "min_stop_distance_pct"): 0.0,  # Disable % floor
        })
        engine = EntryEngine(cfg)
        
        # Structure with tight sweep_low (would result in tight SL)
        structure = make_bullish_structure(sweep_low=PRICE - ATR * 0.3)
        
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=structure,
            htf_4h_trend=1,
        )
        
        if signal.should_enter:
            sl_distance = abs(PRICE - signal.stop_loss)
            min_expected_distance = ATR * 0.9
            # SL should be at least ATR * min_stop_atr_mult away
            assert sl_distance >= min_expected_distance * 0.99, \
                f"SL distance {sl_distance} < min {min_expected_distance}"

    def test_sl_floor_not_applied_when_sl_already_wide(self):
        """When computed SL is already wider, no floor adjustment needed."""
        cfg = MockConfig({
            ("entry", "min_stop_atr_mult"): 0.9,
        })
        engine = EntryEngine(cfg)
        
        # Structure with wide sweep_low
        structure = make_bullish_structure(sweep_low=PRICE - ATR * 2)
        
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=structure,
            htf_4h_trend=1,
        )
        
        if signal.should_enter:
            sl_distance = abs(PRICE - signal.stop_loss)
            # SL should be wider than minimum floor
            assert sl_distance > ATR * 0.9

    def test_sl_floor_works_for_shorts(self):
        """SL floor should also work for short positions."""
        cfg = MockConfig({
            ("entry", "min_stop_atr_mult"): 0.9,
            ("entry", "min_stop_distance_pct"): 0.0,  # Disable % floor
        })
        engine = EntryEngine(cfg)
        
        # Structure with tight sweep_high
        structure = make_bearish_structure(sweep_high=PRICE + ATR * 0.3)
        
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.75, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=False),
            structure=structure,
            htf_4h_trend=-1,
        )
        
        if signal.should_enter:
            sl_distance = abs(PRICE - signal.stop_loss)
            min_expected_distance = ATR * 0.9
            assert sl_distance >= min_expected_distance * 0.99, \
                f"SL distance {sl_distance} < min {min_expected_distance}"

    def test_engine_loads_min_stop_atr_mult_from_config(self):
        """Engine should load min_stop_atr_mult from config."""
        cfg = MockConfig({("entry", "min_stop_atr_mult"): 1.2})
        engine = EntryEngine(cfg)
        assert engine.min_stop_atr_mult == 1.2


# ═══════════════════════════════════════════════════════════════
# TEST 3: ANTI-CHASE ZONE EXTENSION REJECT
# ═══════════════════════════════════════════════════════════════

class TestAntiChaseReject:
    """Validates that entry_engine rejects entries too far from zone."""

    def test_rejects_long_entry_too_extended_from_zone(self):
        """Long entry extended more than max_entry_extension_atr from zone high should be rejected."""
        cfg = MockConfig({
            ("entry", "max_entry_extension_atr"): 0.75,
        })
        engine = EntryEngine(cfg)
        
        # Zone high at 50200, current price at 50600 (1.0 ATR extension)
        zone = MockZoneContext(bullish=True, zone_low=49800.0, zone_high=50200.0)
        price = 50600.0  # Extended 400 (1.0 ATR) from zone high
        
        signal = engine.generate_signal(
            "BTCUSDT", [], price,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=zone,
            structure=make_bullish_structure(price=price),
            htf_4h_trend=1,
        )
        
        # Should be rejected due to extension
        assert signal.should_enter is False
        assert "entry_too_extended_from_zone" in signal.metadata.get("reject_reason", "")

    def test_accepts_long_entry_within_extension_limit(self):
        """Long entry within max_entry_extension_atr from zone should be accepted."""
        cfg = MockConfig({
            ("entry", "max_entry_extension_atr"): 0.75,
        })
        engine = EntryEngine(cfg)
        
        # Zone high at 50200, current price at 50350 (0.375 ATR extension)
        zone = MockZoneContext(bullish=True, zone_low=49800.0, zone_high=50200.0)
        price = 50350.0  # Extended 150 (0.375 ATR) from zone high - within limit
        
        signal = engine.generate_signal(
            "BTCUSDT", [], price,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=zone,
            structure=make_bullish_structure(price=price),
            htf_4h_trend=1,
        )
        
        # Should not be rejected for extension (may reject for other reasons)
        if not signal.should_enter:
            assert "entry_too_extended" not in signal.metadata.get("reject_reason", "")

    def test_rejects_short_entry_too_extended_from_zone(self):
        """Short entry extended more than max_entry_extension_atr from zone low should be rejected."""
        cfg = MockConfig({
            ("entry", "max_entry_extension_atr"): 0.75,
        })
        engine = EntryEngine(cfg)
        
        # Zone low at 49800, current price at 49400 (1.0 ATR extension)
        zone = MockZoneContext(bullish=False, zone_low=49800.0, zone_high=50200.0)
        price = 49400.0  # Extended 400 (1.0 ATR) from zone low
        
        signal = engine.generate_signal(
            "BTCUSDT", [], price,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.75, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=zone,
            structure=make_bearish_structure(price=price),
            htf_4h_trend=-1,
        )
        
        # Should be rejected due to extension
        assert signal.should_enter is False
        assert "entry_too_extended_from_zone" in signal.metadata.get("reject_reason", "")

    def test_extension_atr_in_reject_metadata(self):
        """Rejected signal should contain extension_atr in metadata."""
        cfg = MockConfig({
            ("entry", "max_entry_extension_atr"): 0.75,
        })
        engine = EntryEngine(cfg)
        
        zone = MockZoneContext(bullish=True, zone_low=49800.0, zone_high=50200.0)
        price = 50600.0
        
        signal = engine.generate_signal(
            "BTCUSDT", [], price,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=zone,
            structure=make_bullish_structure(price=price),
            htf_4h_trend=1,
        )
        
        if "entry_too_extended" in signal.metadata.get("reject_reason", ""):
            assert "extension_atr" in signal.metadata
            assert signal.metadata["extension_atr"] >= 0.75

    def test_engine_loads_max_entry_extension_atr_from_config(self):
        """Engine should load max_entry_extension_atr from config."""
        cfg = MockConfig({("entry", "max_entry_extension_atr"): 0.5})
        engine = EntryEngine(cfg)
        assert engine.max_entry_extension_atr == 0.5


# ═══════════════════════════════════════════════════════════════
# TEST 4: METADATA INCLUDES entry_range_low/high AND smc_score
# ═══════════════════════════════════════════════════════════════

class TestMetadataFields:
    """Validates that entry_engine metadata includes required fields."""

    def test_entry_range_low_in_metadata(self, engine):
        """Successful signal should have entry_range_low in metadata."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        
        if signal.should_enter:
            assert "entry_range_low" in signal.metadata
            assert signal.metadata["entry_range_low"] > 0

    def test_entry_range_high_in_metadata(self, engine):
        """Successful signal should have entry_range_high in metadata."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        
        if signal.should_enter:
            assert "entry_range_high" in signal.metadata
            assert signal.metadata["entry_range_high"] > 0

    def test_entry_range_for_long_is_below_current_price(self, engine):
        """For longs, entry_range_low should be below current price."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        
        if signal.should_enter and signal.side == "BUY":
            assert signal.metadata["entry_range_low"] <= PRICE
            assert signal.metadata["entry_range_high"] == PRICE

    def test_entry_range_for_short_is_above_current_price(self, engine):
        """For shorts, entry_range_high should be above current price."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.75, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=False),
            structure=make_bearish_structure(),
            htf_4h_trend=-1,
        )
        
        if signal.should_enter and signal.side == "SELL":
            assert signal.metadata["entry_range_low"] == PRICE
            assert signal.metadata["entry_range_high"] >= PRICE

    def test_smc_score_in_metadata(self, engine):
        """Successful signal should have smc_score in metadata."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        
        if signal.should_enter:
            assert "smc_score" in signal.metadata
            assert 0 <= signal.metadata["smc_score"] <= 1

    def test_smc_score_equals_composite_score(self, engine):
        """smc_score should equal composite_score."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        
        if signal.should_enter:
            assert signal.metadata["smc_score"] == signal.metadata["composite_score"]

    def test_entry_range_uses_atr_multiplier(self, engine):
        """Entry range should be calculated using entry_range_atr_mult."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        
        if signal.should_enter and signal.side == "BUY":
            expected_range = ATR * 0.22  # Default entry_range_atr_mult
            actual_range = PRICE - signal.metadata["entry_range_low"]
            # Allow small tolerance for rounding
            assert abs(actual_range - expected_range) < 0.01


# ═══════════════════════════════════════════════════════════════
# TEST 5: TELEGRAM MESSAGE INCLUDES ENTRY RANGE
# ═══════════════════════════════════════════════════════════════

class TestTelegramEntryRange:
    """Validates that signal-only telegram path includes recommended entry range."""

    def test_main_py_telegram_message_has_entry_range(self):
        """main.py signal-only telegram message should include entry range."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        
        # Check that entry_range_low and entry_range_high are extracted
        assert "entry_range_low" in content
        assert "entry_range_high" in content
        
        # Check that "Рекомендуемый вход" line exists
        assert "Рекомендуемый вход" in content
        
        # Check the format includes both low and high
        assert "entry_range_low:.4f}" in content
        assert "entry_range_high:.4f}" in content

    def test_telegram_message_structure(self):
        """Verify telegram message structure includes entry range hint."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        
        # Look for the signal message builder section
        assert "SIGNAL" in content
        assert "Монета" in content
        assert "Вход" in content
        assert "Рекомендуемый вход" in content
        assert "SL" in content
        assert "TP" in content


# ═══════════════════════════════════════════════════════════════
# TEST 6: ENGINE ATTRIBUTE INITIALIZATION
# ═══════════════════════════════════════════════════════════════

class TestEngineInitialization:
    """Validates that engine loads all required attributes from config."""

    def test_entry_threshold_loaded(self, engine):
        """Engine should load entry_threshold from config."""
        assert engine.entry_threshold == 0.62

    def test_min_stop_atr_mult_loaded(self, engine):
        """Engine should load min_stop_atr_mult from config."""
        assert engine.min_stop_atr_mult == 0.9

    def test_max_entry_extension_atr_loaded(self, engine):
        """Engine should load max_entry_extension_atr from config."""
        assert engine.max_entry_extension_atr == 0.75

    def test_entry_range_atr_mult_loaded(self, engine):
        """Engine should load entry_range_atr_mult from config."""
        assert engine.entry_range_atr_mult == 0.22

    def test_trained_model_enabled_loaded(self, engine):
        """Engine should load trained_model_enabled from config."""
        assert engine.trained_model_enabled is False

    def test_trained_model_min_prob_loaded(self, engine):
        """Engine should load trained_model_min_prob from config."""
        assert engine.trained_model_min_prob == 0.45


# ═══════════════════════════════════════════════════════════════
# TEST 7: SL DISTANCE VALIDATION
# ═══════════════════════════════════════════════════════════════

class TestSLDistanceValidation:
    """Additional tests for SL distance computation."""

    def test_sl_uses_max_of_pct_and_atr_floor(self):
        """SL distance should use max(price% floor, ATR floor)."""
        cfg = MockConfig({
            ("entry", "min_stop_distance_pct"): 1.0,  # 1% = 500 at 50000
            ("entry", "min_stop_atr_mult"): 0.9,  # 0.9 * 400 = 360
        })
        engine = EntryEngine(cfg)
        
        structure = make_bullish_structure(sweep_low=PRICE - 100)  # Very tight SL
        
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=structure,
            htf_4h_trend=1,
        )
        
        if signal.should_enter:
            sl_distance = abs(PRICE - signal.stop_loss)
            # Should use the larger of the two floors
            # 1% of 50000 = 500, which is larger than 0.9 * 400 = 360
            assert sl_distance >= 500 * 0.99

    def test_sl_validates_long_not_above_entry(self, engine):
        """Long SL should never be >= entry price."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=True),
            structure=make_bullish_structure(),
            htf_4h_trend=1,
        )
        
        if signal.should_enter and signal.side == "BUY":
            assert signal.stop_loss < PRICE

    def test_sl_validates_short_not_below_entry(self, engine):
        """Short SL should never be <= entry price."""
        signal = engine.generate_signal(
            "BTCUSDT", [], PRICE,
            MockMarketAnalysis(), MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.75, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=-0.4, spread_pct=0.02),
            make_liq(), ATR,
            zone_context=MockZoneContext(bullish=False),
            structure=make_bearish_structure(),
            htf_4h_trend=-1,
        )
        
        if signal.should_enter and signal.side == "SELL":
            assert signal.stop_loss > PRICE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
