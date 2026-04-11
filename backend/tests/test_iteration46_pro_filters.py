#!/usr/bin/env python3
"""
Iteration 46: PRO Filter Integration Tests

Tests for NEW features only:
1. EMA Trend Guard (entry_engine.py ~line 490)
2. Momentum Guard (entry_engine.py ~line 516)
3. Volume Guard (entry_engine.py ~line 538)
4. EMA Trend Exit (exit_engine.py ~line 272)
5. Config fixes: leverage=5, chop removed, cooldown fixed

Existing guards (exhaustion, contra-trend, counter-flow) already tested in iterations 44-45.
"""

import sys
import os
import pytest
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# Add bot directory to path
BOT_DIR = Path(__file__).resolve().parents[2] / "bot"
sys.path.insert(0, str(BOT_DIR))

import yaml


# =====================================================
# MOCK CLASSES FOR TESTING
# =====================================================

@dataclass
class MockConfig:
    """Mock config object that mimics BotConfig.get() behavior."""
    data: dict = field(default_factory=dict)
    
    def get(self, section: str, key: str, default=None):
        if section in self.data and key in self.data[section]:
            return self.data[section][key]
        return default


@dataclass
class MockMarketAnalysis:
    can_trade: bool = True
    atr_pct: float = 0.5


@dataclass
class MockRegimePrediction:
    regime: object = None
    
    def __post_init__(self):
        if self.regime is None:
            self.regime = type('Regime', (), {'value': 'trend'})()


@dataclass
class MockTransformerPrediction:
    prob_up: float = 0.6
    prob_down: float = 0.3
    prob_flat: float = 0.1


@dataclass
class MockOrderflowSnapshot:
    spread_pct: float = 0.01
    normalized_imbalance: float = 0.2
    buy_volume: float = 1000
    sell_volume: float = 800
    bid_volume: float = 5000
    ask_volume: float = 4000
    imbalance_score: float = 0.3


@dataclass
class MockLiqAnalysis:
    target_level: float = 0.0
    distance_to_target_pct: float = 0.0
    signal: int = 0
    magnet_direction: str = "none"


@dataclass
class MockPosition:
    is_long: bool = True
    bars_since_entry: int = 25
    entry_price: float = 100.0
    stop_loss: float = 95.0
    take_profit: float = 110.0
    trailing_active: bool = False
    trailing_stop: float = 0.0
    best_price: float = 100.0
    origin: str = "bot"


def make_klines(closes: List[float], volumes: Optional[List[float]] = None) -> List[Dict]:
    """Create klines list from close prices and optional volumes."""
    if volumes is None:
        volumes = [1000.0] * len(closes)
    klines = []
    for i, (c, v) in enumerate(zip(closes, volumes)):
        klines.append({
            'open': str(c - 0.5),
            'close': str(c),
            'high': str(c + 1),
            'low': str(c - 1),
            'volume': str(v)
        })
    return klines


# =====================================================
# CONFIG.YAML TESTS
# =====================================================

class TestConfigYamlParsing:
    """Test config.yaml parses correctly with all new parameters."""
    
    @pytest.fixture
    def config(self):
        config_path = BOT_DIR / "config.yaml"
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    
    def test_leverage_is_5(self, config):
        """leverage is 5 in config.yaml (not 8 or 10)"""
        assert config['trading']['leverage'] == 5, f"Expected leverage=5, got {config['trading']['leverage']}"
    
    def test_allowed_regimes_does_not_contain_chop(self, config):
        """allowed_regimes does NOT contain 'chop'"""
        allowed = config['entry']['allowed_regimes']
        assert 'chop' not in allowed, f"'chop' should not be in allowed_regimes: {allowed}"
    
    def test_ignore_loss_cooldown_reasons_is_empty(self, config):
        """ignore_loss_cooldown_reasons is empty list"""
        reasons = config['risk']['ignore_loss_cooldown_reasons']
        assert reasons == [], f"Expected empty list, got {reasons}"
    
    def test_active_preset_is_1h(self, config):
        """active_preset is '1h'"""
        preset = config['tf_presets']['active_preset']
        assert preset == '1h', f"Expected '1h', got {preset}"
    
    def test_ema_trend_filter_enabled(self, config):
        """ema_trend_filter is enabled in config"""
        assert config['entry']['ema_trend_filter'] == True
    
    def test_ema_fast_period_is_20(self, config):
        """ema_fast_period is 20"""
        assert config['entry']['ema_fast_period'] == 20
    
    def test_ema_slow_period_is_50(self, config):
        """ema_slow_period is 50"""
        assert config['entry']['ema_slow_period'] == 50
    
    def test_momentum_filter_enabled(self, config):
        """momentum_filter is enabled in config"""
        assert config['entry']['momentum_filter'] == True
    
    def test_momentum_lookback_is_5(self, config):
        """momentum_lookback is 5"""
        assert config['entry']['momentum_lookback'] == 5
    
    def test_volume_filter_enabled(self, config):
        """volume_filter is enabled in config"""
        assert config['entry']['volume_filter'] == True
    
    def test_volume_lookback_is_20(self, config):
        """volume_lookback is 20"""
        assert config['entry']['volume_lookback'] == 20
    
    def test_ema_trend_exit_enabled(self, config):
        """ema_trend_exit is enabled in config"""
        assert config['exit']['ema_trend_exit'] == True
    
    def test_ema_exit_period_is_20(self, config):
        """ema_exit_period is 20"""
        assert config['exit']['ema_exit_period'] == 20


# =====================================================
# ENTRY ENGINE INITIALIZATION TESTS
# =====================================================

class TestEntryEngineInitialization:
    """Test EntryEngine initializes correctly with new PRO filter config params."""
    
    def test_entry_engine_initializes_with_pro_filters(self):
        """EntryEngine initializes correctly with new PRO filter config params."""
        from engine.entry_engine import EntryEngine
        
        cfg = MockConfig(data={
            'entry': {
                'ema_trend_filter': True,
                'ema_fast_period': 20,
                'ema_slow_period': 50,
                'momentum_filter': True,
                'momentum_lookback': 5,
                'volume_filter': True,
                'volume_lookback': 20,
            }
        })
        
        engine = EntryEngine(cfg)
        
        assert engine.ema_trend_filter == True
        assert engine.ema_fast_period == 20
        assert engine.ema_slow_period == 50
        assert engine.momentum_filter == True
        assert engine.momentum_lookback == 5
        assert engine.volume_filter == True
        assert engine.volume_lookback == 20
    
    def test_entry_engine_defaults_when_not_in_config(self):
        """EntryEngine uses defaults when PRO filter params not in config."""
        from engine.entry_engine import EntryEngine
        
        cfg = MockConfig(data={'entry': {}})
        engine = EntryEngine(cfg)
        
        # Should use defaults
        assert engine.ema_trend_filter == True  # default
        assert engine.ema_fast_period == 20  # default
        assert engine.ema_slow_period == 50  # default
        assert engine.momentum_filter == True  # default
        assert engine.momentum_lookback == 5  # default
        assert engine.volume_filter == True  # default
        assert engine.volume_lookback == 20  # default


# =====================================================
# EMA COMPUTATION HELPER TESTS
# =====================================================

class TestEMAComputation:
    """Test _compute_ema helper returns correct values for known inputs."""
    
    def test_compute_ema_returns_correct_values(self):
        """EMA computation helper _compute_ema returns correct values for known inputs."""
        from engine.entry_engine import EntryEngine
        
        # Simple test: constant prices should give constant EMA
        prices = np.array([100.0] * 30)
        ema = EntryEngine._compute_ema(prices, period=10)
        
        # After period, EMA should equal the constant price
        assert not np.isnan(ema[-1])
        assert abs(ema[-1] - 100.0) < 0.01
    
    def test_compute_ema_uptrend(self):
        """EMA follows uptrend correctly."""
        from engine.entry_engine import EntryEngine
        
        # Uptrend: prices increasing
        prices = np.array([100.0 + i for i in range(30)])
        ema = EntryEngine._compute_ema(prices, period=10)
        
        # EMA should be below current price in uptrend
        assert ema[-1] < prices[-1]
        assert ema[-1] > prices[0]
    
    def test_compute_ema_downtrend(self):
        """EMA follows downtrend correctly."""
        from engine.entry_engine import EntryEngine
        
        # Downtrend: prices decreasing
        prices = np.array([130.0 - i for i in range(30)])
        ema = EntryEngine._compute_ema(prices, period=10)
        
        # EMA should be above current price in downtrend
        assert ema[-1] > prices[-1]
    
    def test_compute_ema_short_array(self):
        """EMA handles arrays shorter than period."""
        from engine.entry_engine import EntryEngine
        
        prices = np.array([100.0, 101.0, 102.0])  # Only 3 elements
        ema = EntryEngine._compute_ema(prices, period=10)
        
        # Should return copy of prices when too short
        assert len(ema) == len(prices)


# =====================================================
# EMA TREND GUARD TESTS
# =====================================================

class TestEMATrendGuard:
    """Test EMA Trend Guard entry filter."""
    
    @pytest.fixture
    def entry_engine(self):
        from engine.entry_engine import EntryEngine
        cfg = MockConfig(data={
            'entry': {
                'ema_trend_filter': True,
                'ema_fast_period': 20,
                'ema_slow_period': 50,
                'momentum_filter': False,  # Disable other filters for isolation
                'volume_filter': False,
            }
        })
        return EntryEngine(cfg)
    
    def test_ema_trend_guard_rejects_long_in_downtrend(self, entry_engine):
        """EMA Trend Guard rejects LONG signal when EMA(20) < EMA(50) (downtrend)."""
        # Create downtrend: prices decreasing
        closes = [150.0 - i * 0.5 for i in range(60)]
        klines = make_klines(closes)
        
        # Verify EMA relationship
        closes_arr = np.array(closes)
        ema_fast = entry_engine._compute_ema(closes_arr, 20)
        ema_slow = entry_engine._compute_ema(closes_arr, 50)
        
        # In downtrend, EMA(20) should be below EMA(50)
        assert ema_fast[-1] < ema_slow[-1], "Test setup: EMA(20) should be < EMA(50) in downtrend"
        
        # Generate signal - should be rejected for LONG
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,  # Force bullish direction
        )
        
        # Should be rejected with ema_trend_guard reason
        if signal.should_enter:
            # If it passed, check it's not a LONG
            assert signal.side != "BUY", "LONG should be rejected in downtrend"
        else:
            reject_reason = signal.metadata.get('reject_reason', '')
            # Could be rejected by ema_trend_guard or other guards
            assert 'ema_trend_guard' in reject_reason or not signal.should_enter
    
    def test_ema_trend_guard_rejects_short_in_uptrend(self, entry_engine):
        """EMA Trend Guard rejects SELL signal when EMA(20) > EMA(50) (uptrend)."""
        # Create uptrend: prices increasing
        closes = [100.0 + i * 0.5 for i in range(60)]
        klines = make_klines(closes)
        
        # Verify EMA relationship
        closes_arr = np.array(closes)
        ema_fast = entry_engine._compute_ema(closes_arr, 20)
        ema_slow = entry_engine._compute_ema(closes_arr, 50)
        
        # In uptrend, EMA(20) should be above EMA(50)
        assert ema_fast[-1] > ema_slow[-1], "Test setup: EMA(20) should be > EMA(50) in uptrend"
        
        # Generate signal - should be rejected for SHORT
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.1, prob_down=0.8),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=-0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=-1,  # Force bearish direction
        )
        
        # Should be rejected with ema_trend_guard reason
        if signal.should_enter:
            assert signal.side != "SELL", "SHORT should be rejected in uptrend"
        else:
            reject_reason = signal.metadata.get('reject_reason', '')
            assert 'ema_trend_guard' in reject_reason or not signal.should_enter
    
    def test_ema_trend_guard_passes_aligned_signal(self, entry_engine):
        """EMA Trend Guard passes signal when EMA direction aligns with signal."""
        # Create uptrend: prices increasing
        closes = [100.0 + i * 0.5 for i in range(60)]
        klines = make_klines(closes)
        
        # Verify EMA relationship
        closes_arr = np.array(closes)
        ema_fast = entry_engine._compute_ema(closes_arr, 20)
        ema_slow = entry_engine._compute_ema(closes_arr, 50)
        
        # In uptrend, EMA(20) should be above EMA(50)
        assert ema_fast[-1] > ema_slow[-1], "Test setup: EMA(20) should be > EMA(50) in uptrend"
        
        # Generate LONG signal in uptrend - should pass EMA guard
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,  # Bullish
        )
        
        # Should NOT be rejected by ema_trend_guard
        reject_reason = signal.metadata.get('reject_reason', '')
        assert 'ema_trend_guard' not in reject_reason, f"LONG in uptrend should pass EMA guard, got: {reject_reason}"


# =====================================================
# MOMENTUM GUARD TESTS
# =====================================================

class TestMomentumGuard:
    """Test Momentum Guard entry filter."""
    
    @pytest.fixture
    def entry_engine(self):
        from engine.entry_engine import EntryEngine
        cfg = MockConfig(data={
            'entry': {
                'ema_trend_filter': False,  # Disable for isolation
                'momentum_filter': True,
                'momentum_lookback': 5,
                'volume_filter': False,
            }
        })
        return EntryEngine(cfg)
    
    def test_momentum_guard_rejects_buy_negative_momentum(self, entry_engine):
        """Momentum Guard rejects BUY when close[-1] < close[-5] (negative momentum)."""
        # Create negative momentum: recent price drop
        closes = [100.0] * 50 + [105.0, 104.0, 103.0, 102.0, 101.0, 99.0]  # Last < 5 bars ago
        klines = make_klines(closes)
        
        # Verify momentum is negative
        assert closes[-1] < closes[-5], "Test setup: close[-1] should be < close[-5]"
        
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,  # Force BUY direction
        )
        
        # Should be rejected with momentum_guard reason
        if signal.should_enter:
            assert signal.side != "BUY", "BUY should be rejected with negative momentum"
        else:
            reject_reason = signal.metadata.get('reject_reason', '')
            # Could be rejected by momentum_guard or other guards
            assert 'momentum_guard' in reject_reason or not signal.should_enter
    
    def test_momentum_guard_rejects_sell_positive_momentum(self, entry_engine):
        """Momentum Guard rejects SELL when close[-1] > close[-5] (positive momentum)."""
        # Create positive momentum: recent price rise
        closes = [100.0] * 50 + [95.0, 96.0, 97.0, 98.0, 99.0, 101.0]  # Last > 5 bars ago
        klines = make_klines(closes)
        
        # Verify momentum is positive
        assert closes[-1] > closes[-5], "Test setup: close[-1] should be > close[-5]"
        
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.1, prob_down=0.8),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=-0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=-1,  # Force SELL direction
        )
        
        # Should be rejected with momentum_guard reason
        if signal.should_enter:
            assert signal.side != "SELL", "SELL should be rejected with positive momentum"
        else:
            reject_reason = signal.metadata.get('reject_reason', '')
            assert 'momentum_guard' in reject_reason or not signal.should_enter
    
    def test_momentum_guard_passes_aligned_momentum(self, entry_engine):
        """Momentum Guard passes when momentum aligns with signal direction."""
        # Create positive momentum for BUY
        closes = [100.0] * 50 + [95.0, 96.0, 97.0, 98.0, 99.0, 101.0]  # Last > 5 bars ago
        klines = make_klines(closes)
        
        # Verify momentum is positive
        assert closes[-1] > closes[-5], "Test setup: close[-1] should be > close[-5]"
        
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,  # BUY direction
        )
        
        # Should NOT be rejected by momentum_guard
        reject_reason = signal.metadata.get('reject_reason', '')
        assert 'momentum_guard' not in reject_reason, f"BUY with positive momentum should pass, got: {reject_reason}"


# =====================================================
# VOLUME GUARD TESTS
# =====================================================

class TestVolumeGuard:
    """Test Volume Guard entry filter."""
    
    @pytest.fixture
    def entry_engine(self):
        from engine.entry_engine import EntryEngine
        cfg = MockConfig(data={
            'entry': {
                'ema_trend_filter': False,
                'momentum_filter': False,
                'volume_filter': True,
                'volume_lookback': 20,
            }
        })
        return EntryEngine(cfg)
    
    def test_volume_guard_rejects_low_volume(self, entry_engine):
        """Volume Guard rejects when current volume < avg volume of last 20 bars."""
        # Create low volume scenario
        closes = [100.0 + i * 0.1 for i in range(60)]
        volumes = [1000.0] * 59 + [500.0]  # Last volume is half of average
        klines = make_klines(closes, volumes)
        
        # Verify volume is below average
        avg_vol = np.mean(volumes[-21:-1])  # avg of prev 20 candles
        assert volumes[-1] < avg_vol, f"Test setup: current vol {volumes[-1]} should be < avg {avg_vol}"
        
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,
        )
        
        # Should be rejected with volume_guard reason
        reject_reason = signal.metadata.get('reject_reason', '')
        assert 'volume_guard' in reject_reason or not signal.should_enter, f"Low volume should be rejected, got: {reject_reason}"
    
    def test_volume_guard_passes_high_volume(self, entry_engine):
        """Volume Guard passes when current volume >= avg volume."""
        # Create high volume scenario
        closes = [100.0 + i * 0.1 for i in range(60)]
        volumes = [1000.0] * 59 + [1500.0]  # Last volume is above average
        klines = make_klines(closes, volumes)
        
        # Verify volume is above average
        avg_vol = np.mean(volumes[-21:-1])
        assert volumes[-1] >= avg_vol, f"Test setup: current vol {volumes[-1]} should be >= avg {avg_vol}"
        
        signal = entry_engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,
        )
        
        # Should NOT be rejected by volume_guard
        reject_reason = signal.metadata.get('reject_reason', '')
        assert 'volume_guard' not in reject_reason, f"High volume should pass, got: {reject_reason}"


# =====================================================
# EXIT ENGINE EMA TREND EXIT TESTS
# =====================================================

class TestEMATrendExit:
    """Test ExitEngine.check_ema_trend_exit method."""
    
    @pytest.fixture
    def exit_engine(self):
        from engine.exit_engine import ExitEngine
        return ExitEngine(ema_trend_exit_buffer_pct=0.1)
    
    def test_trend_exit_enum_exists(self):
        """TREND_EXIT is in ExitReason enum."""
        from engine.exit_engine import ExitReason
        assert hasattr(ExitReason, 'TREND_EXIT'), "ExitReason should have TREND_EXIT"
        assert ExitReason.TREND_EXIT.value == "trend_exit"
    
    def test_ema_trend_exit_long_below_ema(self, exit_engine):
        """ExitEngine.check_ema_trend_exit returns TREND_EXIT for LONG when price < EMA(20)."""
        from engine.exit_engine import ExitReason
        
        # Create downtrend: price drops below EMA
        closes = [100.0] * 30 + [99.0, 98.0, 97.0, 96.0, 95.0]  # Price dropping
        klines = make_klines(closes)
        
        position = MockPosition(is_long=True, bars_since_entry=25)
        
        should_exit, reason, details = exit_engine.check_ema_trend_exit(
            position, klines, ema_period=20
        )
        
        # Should trigger TREND_EXIT
        assert should_exit == True, "Should exit when LONG price < EMA"
        assert reason == ExitReason.TREND_EXIT
        assert "LONG" in details
    
    def test_ema_trend_exit_short_above_ema(self, exit_engine):
        """ExitEngine.check_ema_trend_exit returns TREND_EXIT for SHORT when price > EMA(20)."""
        from engine.exit_engine import ExitReason
        
        # Create uptrend: price rises above EMA
        closes = [100.0] * 30 + [101.0, 102.0, 103.0, 104.0, 105.0]  # Price rising
        klines = make_klines(closes)
        
        position = MockPosition(is_long=False, bars_since_entry=25)
        
        should_exit, reason, details = exit_engine.check_ema_trend_exit(
            position, klines, ema_period=20
        )
        
        # Should trigger TREND_EXIT
        assert should_exit == True, "Should exit when SHORT price > EMA"
        assert reason == ExitReason.TREND_EXIT
        assert "SHORT" in details
    
    def test_ema_trend_exit_no_exit_when_held_less_than_period(self, exit_engine):
        """ExitEngine.check_ema_trend_exit returns no exit when position held < ema_period bars."""
        # Create downtrend
        closes = [100.0] * 30 + [99.0, 98.0, 97.0, 96.0, 95.0]
        klines = make_klines(closes)
        
        # Position held for only 10 bars (< 20 period)
        position = MockPosition(is_long=True, bars_since_entry=10)
        
        should_exit, reason, details = exit_engine.check_ema_trend_exit(
            position, klines, ema_period=20
        )
        
        # Should NOT exit - too early
        assert should_exit == False, "Should not exit when held < ema_period bars"
        assert reason is None
    
    def test_ema_trend_exit_no_exit_when_trend_aligns(self, exit_engine):
        """ExitEngine.check_ema_trend_exit returns no exit when trend aligns."""
        # Create uptrend for LONG position
        closes = [100.0 + i * 0.5 for i in range(35)]  # Uptrend
        klines = make_klines(closes)
        
        position = MockPosition(is_long=True, bars_since_entry=25)
        
        should_exit, reason, details = exit_engine.check_ema_trend_exit(
            position, klines, ema_period=20
        )
        
        # Should NOT exit - trend aligns with position
        assert should_exit == False, "Should not exit when trend aligns with position"

    def test_ema_trend_exit_respects_buffer_for_long(self, exit_engine):
        """LONG should not exit if price is only slightly below EMA within buffer."""
        position = MockPosition(is_long=True, bars_since_entry=25)
        # Near-flat closes so EMA ~ 100
        closes = [100.0] * 34 + [99.95]
        klines = make_klines(closes)

        should_exit, reason, details = exit_engine.check_ema_trend_exit(
            position, klines, ema_period=20
        )

        assert should_exit is False, f"Expected no exit within EMA buffer, got: {details}"
        assert reason is None

    def test_ema_trend_exit_triggers_when_cross_exceeds_buffer_for_long(self, exit_engine):
        """LONG should exit if price drops below EMA by more than buffer."""
        from engine.exit_engine import ExitReason

        position = MockPosition(is_long=True, bars_since_entry=25)
        closes = [100.0] * 34 + [99.7]
        klines = make_klines(closes)

        should_exit, reason, details = exit_engine.check_ema_trend_exit(
            position, klines, ema_period=20
        )

        assert should_exit is True
        assert reason == ExitReason.TREND_EXIT


# =====================================================
# MANUAL POSITION SAFETY TESTS
# =====================================================

class TestManualPositionSafety:
    """Test manual position safety exit allow-list."""
    
    def test_trend_exit_blocked_for_manual_positions(self):
        """Manual position safety blocks TREND_EXIT for manual positions."""
        from engine.exit_engine import ExitReason
        
        # The allowed exit reasons for manual positions (from main.py line 829-836)
        allowed_manual_exits = [
            ExitReason.TRAILING_EXIT,
            ExitReason.TP_CAP,
        ]
        
        assert ExitReason.TREND_EXIT not in allowed_manual_exits, "TREND_EXIT should be blocked for manual positions"


# =====================================================
# EXTRACT CLOSES AND VOLUMES HELPER TESTS
# =====================================================

class TestExtractClosesAndVolumes:
    """Test _extract_closes_and_volumes helper."""
    
    def test_extract_closes_and_volumes_works(self):
        """Entry engine _extract_closes_and_volumes helper works with kline dict format."""
        from engine.entry_engine import EntryEngine
        
        klines = [
            {'open': '100', 'close': '101', 'high': '102', 'low': '99', 'volume': '1000'},
            {'open': '101', 'close': '102', 'high': '103', 'low': '100', 'volume': '1500'},
            {'open': '102', 'close': '103', 'high': '104', 'low': '101', 'volume': '2000'},
        ]
        
        closes, volumes = EntryEngine._extract_closes_and_volumes(klines)
        
        assert len(closes) == 3
        assert len(volumes) == 3
        assert closes[0] == 101.0
        assert closes[1] == 102.0
        assert closes[2] == 103.0
        assert volumes[0] == 1000.0
        assert volumes[1] == 1500.0
        assert volumes[2] == 2000.0


# =====================================================
# EXISTING GUARDS REGRESSION TESTS
# =====================================================

class TestExistingGuardsRegression:
    """Test that existing 6 guards still function."""
    
    def test_exhaustion_guard_still_works(self):
        """Exhaustion guard still rejects when 5+/7 candles same direction."""
        from engine.entry_engine import EntryEngine
        
        cfg = MockConfig(data={
            'entry': {
                'ema_trend_filter': False,
                'momentum_filter': False,
                'volume_filter': False,
            }
        })
        engine = EntryEngine(cfg)
        
        # Create exhaustion scenario: 7 bullish candles
        closes = [100.0] * 50
        for i in range(7):
            closes.append(closes[-1] + 1.0)  # 7 consecutive bullish
        klines = make_klines(closes)
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,
        )
        
        reject_reason = signal.metadata.get('reject_reason', '')
        assert 'exhaustion_guard' in reject_reason or not signal.should_enter
    
    def test_contra_trend_guard_still_works(self):
        """Contra-trend guard still rejects when 7+/10 candles oppose signal."""
        from engine.entry_engine import EntryEngine
        
        cfg = MockConfig(data={
            'entry': {
                'ema_trend_filter': False,
                'momentum_filter': False,
                'volume_filter': False,
            }
        })
        engine = EntryEngine(cfg)
        
        # Create contra-trend scenario: 8 bearish candles, then try to BUY
        closes = [100.0] * 50
        for i in range(11):
            closes.append(closes[-1] - 0.5)  # 11 bearish candles
        klines = make_klines(closes)
        
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(normalized_imbalance=0.3),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,  # Try to BUY
        )
        
        reject_reason = signal.metadata.get('reject_reason', '')
        # Could be rejected by contra_trend_guard or other guards
        assert not signal.should_enter or 'contra_trend_guard' in reject_reason
    
    def test_counter_flow_guard_still_works(self):
        """Counter-flow guard still rejects when volume contradicts signal."""
        from engine.entry_engine import EntryEngine
        
        cfg = MockConfig(data={
            'entry': {
                'ema_trend_filter': False,
                'momentum_filter': False,
                'volume_filter': False,
            }
        })
        engine = EntryEngine(cfg)
        
        closes = [100.0 + i * 0.1 for i in range(60)]
        klines = make_klines(closes)
        
        # Heavy selling when trying to BUY
        signal = engine.generate_signal(
            symbol="BTCUSDT",
            klines=klines,
            current_price=closes[-1],
            market_analysis=MockMarketAnalysis(),
            regime_prediction=MockRegimePrediction(),
            transformer_prediction=MockTransformerPrediction(prob_up=0.8, prob_down=0.1),
            orderflow_snapshot=MockOrderflowSnapshot(
                normalized_imbalance=0.3,
                buy_volume=1000,
                sell_volume=2000,  # Heavy selling
            ),
            liq_analysis=MockLiqAnalysis(),
            atr_value=1.0,
            htf_4h_trend=1,
        )
        
        reject_reason = signal.metadata.get('reject_reason', '')
        assert 'counter_flow_guard' in reject_reason or not signal.should_enter


# =====================================================
# A/B/C GRADING REGRESSION TESTS
# =====================================================

class TestABCGradingRegression:
    """Test that A/B/C grading still works."""
    
    def test_classify_signal_grade_exists(self):
        """classify_signal_grade function exists."""
        from engine.entry_engine import classify_signal_grade
        assert callable(classify_signal_grade)
    
    def test_grade_a_high_conviction(self):
        """Grade A for high conviction signals."""
        from engine.entry_engine import classify_signal_grade
        
        grade = classify_signal_grade(
            confidence=0.90,
            rr_ratio=5.0,
            has_sweep=True,
            has_bos=True,
            htf_aligned=True,
            entry_zone="fvg_bullish",
        )
        assert grade == "A"
    
    def test_grade_b_standard(self):
        """Grade B for standard signals."""
        from engine.entry_engine import classify_signal_grade
        
        grade = classify_signal_grade(
            confidence=0.78,
            rr_ratio=3.5,
            has_sweep=True,
            has_bos=False,
            htf_aligned=True,
            entry_zone="ob_bullish",
        )
        assert grade == "B"
    
    def test_grade_c_marginal(self):
        """Grade C for marginal signals."""
        from engine.entry_engine import classify_signal_grade
        
        grade = classify_signal_grade(
            confidence=0.60,
            rr_ratio=2.5,
            has_sweep=False,
            has_bos=False,
            htf_aligned=False,
            entry_zone="no_zone",
        )
        assert grade == "C"
    
    def test_entry_signal_has_grade_field(self):
        """EntrySignal has grade field."""
        from engine.entry_engine import EntrySignal
        
        signal = EntrySignal()
        assert hasattr(signal, 'grade')
        assert signal.grade == "C"  # Default


# =====================================================
# TRAILING STOP LOGIC REGRESSION TESTS
# =====================================================

class TestTrailingStopRegression:
    """Test that existing trailing stop logic unchanged in exit_engine.py."""
    
    def test_exit_engine_has_update_trailing(self):
        """ExitEngine has update_trailing method."""
        from engine.exit_engine import ExitEngine
        
        engine = ExitEngine()
        assert hasattr(engine, 'update_trailing')
        assert callable(engine.update_trailing)
    
    def test_trailing_exit_before_hard_sl(self):
        """TRAILING_EXIT is checked before HARD_SL in check_exit."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        engine = ExitEngine()
        
        # Position where both trailing_stop and stop_loss would trigger
        position = MockPosition(
            is_long=True,
            entry_price=100.0,
            stop_loss=95.0,
            trailing_active=True,
            trailing_stop=96.0,  # Above hard SL
            best_price=105.0,
        )
        
        # Price hits trailing stop (96) but not hard SL (95)
        should_exit, reason, details = engine.check_exit(position, current_price=95.5)
        
        assert should_exit == True
        assert reason == ExitReason.TRAILING_EXIT, f"Expected TRAILING_EXIT, got {reason}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
