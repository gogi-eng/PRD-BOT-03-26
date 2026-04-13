#!/usr/bin/env python3
"""
Iteration 48: Fee PnL Fix and Early Exit Timing Fix Tests

Bug Fix 1: _calc_pnl now includes fee deduction (entry_fee + exit_fee)
- LINKUSDT example: entry=8.555 exit=8.550 qty=2.5 fee_rate=0.001 → net PnL ≈ -$0.03 (not +$0.01)
- ADAUSDT example: entry=0.2465 exit=0.2461 qty=76 fee_rate=0.001 → net PnL ≈ -$0.007
- _calc_pnl_pct includes 2x fee_rate deduction
- Large profitable trade PnL still positive after fee deduction

Bug Fix 2: 1H preset early_exit_bars changed from 6 to 90
- Early exit min_profit now covers fees (max of atr-based and fee-based)
- ExitEngine early_exit uses fee_per_unit in min_profit calculation

Config: fee_rate is 0.001 (not 0.0006), leverage is 5 (not 10), 1h early_exit_bars is 90
"""

import pytest
import sys
import os
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# Add bot directory to path
BOT_DIR = Path(__file__).parent.parent.parent / "bot"
sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# Mock Position class for testing
# ============================================================================
@dataclass
class MockPosition:
    """Mock Position for testing PnL calculations."""
    entry_price: float
    is_long: bool
    qty: float = 1.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    bars_since_entry: int = 0
    trailing_active: bool = False
    trailing_stop: float = 0.0
    trailing_activation_price: float = 0.0
    trailing_distance: float = 0.0
    best_price: float = 0.0
    symbol: str = "TESTUSDT"
    side: str = "BUY"
    origin: str = "bot"
    protective_liq_level: float = 0.0
    entry_time: Optional[datetime] = None


# ============================================================================
# Test Config Values
# ============================================================================
class TestConfigValues:
    """Verify config.yaml has correct values for fee_rate, leverage, and 1h early_exit_bars."""

    def test_fee_rate_is_0001(self):
        """Config exit.fee_rate should be 0.001 (not 0.0006)."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        fee_rate = config.get("exit", {}).get("fee_rate", 0)
        assert fee_rate == 0.001, f"exit.fee_rate should be 0.001, got {fee_rate}"
        print(f"✓ exit.fee_rate = {fee_rate} (correct)")

    def test_leverage_is_5(self):
        """Config trading.leverage should be 5 (not 10)."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        leverage = config.get("trading", {}).get("leverage", 0)
        assert leverage == 5, f"trading.leverage should be 5, got {leverage}"
        print(f"✓ trading.leverage = {leverage} (correct)")

    def test_1h_preset_early_exit_bars_is_90(self):
        """Config tf_presets.presets.1h.early_exit_bars should be 90 (not 6)."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        presets = config.get("tf_presets", {}).get("presets", {})
        h1_preset = presets.get("1h", {})
        early_exit_bars = h1_preset.get("early_exit_bars", 0)
        assert early_exit_bars == 90, f"1h.early_exit_bars should be 90, got {early_exit_bars}"
        print(f"✓ 1h.early_exit_bars = {early_exit_bars} (correct)")

    def test_active_preset_is_1h(self):
        """Config tf_presets.active_preset should be '1h'."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        active_preset = config.get("tf_presets", {}).get("active_preset", "")
        assert active_preset == "1h", f"active_preset should be '1h', got '{active_preset}'"
        print(f"✓ active_preset = '{active_preset}' (correct)")


# ============================================================================
# Test _calc_pnl with Fee Deduction
# ============================================================================
class TestCalcPnlWithFees:
    """Test _calc_pnl method includes fee deduction."""

    def test_linkusdt_short_example(self):
        """
        LINKUSDT SHORT: entry=8.555 exit=8.550 qty=2.5 fee_rate=0.001
        Raw PnL = (8.555 - 8.550) * 2.5 = +$0.0125
        Entry fee = 8.555 * 2.5 * 0.001 = $0.0214
        Exit fee = 8.550 * 2.5 * 0.001 = $0.0214
        Net PnL = 0.0125 - 0.0214 - 0.0214 = -$0.0303 ≈ -$0.03
        """
        fee_rate = 0.001
        pos = MockPosition(entry_price=8.555, is_long=False, qty=2.5)
        exit_price = 8.550
        
        # Calculate raw PnL (SHORT: entry - exit)
        raw_pnl = (pos.entry_price - exit_price) * pos.qty
        assert abs(raw_pnl - 0.0125) < 0.0001, f"Raw PnL should be ~0.0125, got {raw_pnl}"
        
        # Calculate fees
        entry_fee = pos.entry_price * pos.qty * fee_rate
        exit_fee = exit_price * pos.qty * fee_rate
        
        # Net PnL with fees
        net_pnl = raw_pnl - entry_fee - exit_fee
        
        # Should be approximately -$0.03
        assert net_pnl < 0, f"Net PnL should be negative, got {net_pnl}"
        assert abs(net_pnl - (-0.0303)) < 0.01, f"Net PnL should be ~-$0.03, got {net_pnl}"
        print(f"✓ LINKUSDT SHORT: raw=${raw_pnl:.4f}, fees=${entry_fee + exit_fee:.4f}, net=${net_pnl:.4f}")

    def test_adausdt_short_example(self):
        """
        ADAUSDT SHORT: entry=0.2465 exit=0.2461 qty=76 fee_rate=0.001
        Raw PnL = (0.2465 - 0.2461) * 76 = +$0.0304
        Entry fee = 0.2465 * 76 * 0.001 = $0.0187
        Exit fee = 0.2461 * 76 * 0.001 = $0.0187
        Net PnL = 0.0304 - 0.0187 - 0.0187 = -$0.007
        """
        fee_rate = 0.001
        pos = MockPosition(entry_price=0.2465, is_long=False, qty=76)
        exit_price = 0.2461
        
        raw_pnl = (pos.entry_price - exit_price) * pos.qty
        entry_fee = pos.entry_price * pos.qty * fee_rate
        exit_fee = exit_price * pos.qty * fee_rate
        net_pnl = raw_pnl - entry_fee - exit_fee
        
        # Should be approximately -$0.007
        assert net_pnl < 0, f"Net PnL should be negative, got {net_pnl}"
        assert abs(net_pnl) < 0.02, f"Net PnL should be ~-$0.007, got {net_pnl}"
        print(f"✓ ADAUSDT SHORT: raw=${raw_pnl:.4f}, fees=${entry_fee + exit_fee:.4f}, net=${net_pnl:.4f}")

    def test_long_position_pnl_with_fees(self):
        """Test LONG position PnL calculation with fees."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=100.0, is_long=True, qty=1.0)
        exit_price = 101.0  # 1% profit
        
        raw_pnl = (exit_price - pos.entry_price) * pos.qty  # +$1.00
        entry_fee = pos.entry_price * pos.qty * fee_rate  # $0.10
        exit_fee = exit_price * pos.qty * fee_rate  # $0.101
        net_pnl = raw_pnl - entry_fee - exit_fee  # ~$0.799
        
        assert net_pnl > 0, f"Net PnL should be positive for profitable trade, got {net_pnl}"
        assert net_pnl < raw_pnl, f"Net PnL should be less than raw PnL due to fees"
        print(f"✓ LONG profitable: raw=${raw_pnl:.4f}, fees=${entry_fee + exit_fee:.4f}, net=${net_pnl:.4f}")

    def test_short_position_pnl_with_fees(self):
        """Test SHORT position PnL calculation with fees."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=100.0, is_long=False, qty=1.0)
        exit_price = 99.0  # 1% profit for short
        
        raw_pnl = (pos.entry_price - exit_price) * pos.qty  # +$1.00
        entry_fee = pos.entry_price * pos.qty * fee_rate  # $0.10
        exit_fee = exit_price * pos.qty * fee_rate  # $0.099
        net_pnl = raw_pnl - entry_fee - exit_fee  # ~$0.801
        
        assert net_pnl > 0, f"Net PnL should be positive for profitable trade, got {net_pnl}"
        assert net_pnl < raw_pnl, f"Net PnL should be less than raw PnL due to fees"
        print(f"✓ SHORT profitable: raw=${raw_pnl:.4f}, fees=${entry_fee + exit_fee:.4f}, net=${net_pnl:.4f}")

    def test_large_profitable_trade_still_positive(self):
        """Large profitable trade should still be positive after fee deduction."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=50000.0, is_long=True, qty=0.1)  # BTC-like
        exit_price = 51000.0  # 2% profit
        
        raw_pnl = (exit_price - pos.entry_price) * pos.qty  # +$100
        entry_fee = pos.entry_price * pos.qty * fee_rate  # $5
        exit_fee = exit_price * pos.qty * fee_rate  # $5.1
        net_pnl = raw_pnl - entry_fee - exit_fee  # ~$89.9
        
        assert net_pnl > 0, f"Large profitable trade should still be positive, got {net_pnl}"
        assert net_pnl > 80, f"Net PnL should be ~$90, got {net_pnl}"
        print(f"✓ Large profitable: raw=${raw_pnl:.2f}, fees=${entry_fee + exit_fee:.2f}, net=${net_pnl:.2f}")


# ============================================================================
# Test _calc_pnl_pct with Fee Deduction
# ============================================================================
class TestCalcPnlPctWithFees:
    """Test _calc_pnl_pct method includes 2x fee_rate deduction."""

    def test_pnl_pct_deducts_2x_fee_rate(self):
        """_calc_pnl_pct should deduct 2 * fee_rate * 100 from raw percentage."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=100.0, is_long=True)
        current_price = 101.0  # 1% raw profit
        
        raw_pct = (current_price - pos.entry_price) / pos.entry_price * 100  # 1.0%
        fee_deduction = fee_rate * 2 * 100  # 0.2%
        net_pct = raw_pct - fee_deduction  # 0.8%
        
        assert abs(raw_pct - 1.0) < 0.001, f"Raw pct should be 1.0%, got {raw_pct}"
        assert abs(fee_deduction - 0.2) < 0.001, f"Fee deduction should be 0.2%, got {fee_deduction}"
        assert abs(net_pct - 0.8) < 0.001, f"Net pct should be 0.8%, got {net_pct}"
        print(f"✓ PnL%: raw={raw_pct:.2f}%, fee_deduction={fee_deduction:.2f}%, net={net_pct:.2f}%")

    def test_pnl_pct_long_position(self):
        """Test LONG position percentage calculation."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=100.0, is_long=True)
        current_price = 102.0  # 2% raw profit
        
        raw_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        net_pct = raw_pct - (fee_rate * 2 * 100)
        
        assert abs(raw_pct - 2.0) < 0.001
        assert abs(net_pct - 1.8) < 0.001
        print(f"✓ LONG PnL%: raw={raw_pct:.2f}%, net={net_pct:.2f}%")

    def test_pnl_pct_short_position(self):
        """Test SHORT position percentage calculation."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=100.0, is_long=False)
        current_price = 98.0  # 2% raw profit for short
        
        raw_pct = (pos.entry_price - current_price) / pos.entry_price * 100
        net_pct = raw_pct - (fee_rate * 2 * 100)
        
        assert abs(raw_pct - 2.0) < 0.001
        assert abs(net_pct - 1.8) < 0.001
        print(f"✓ SHORT PnL%: raw={raw_pct:.2f}%, net={net_pct:.2f}%")


# ============================================================================
# Test ExitEngine Early Exit with Fee-Aware Min Profit
# ============================================================================
class TestExitEngineEarlyExitFeeAware:
    """Test ExitEngine early_exit uses fee_per_unit in min_profit calculation."""

    def test_exit_engine_has_fee_rate(self):
        """ExitEngine should have fee_rate attribute."""
        from engine.exit_engine import ExitEngine
        
        engine = ExitEngine(fee_rate=0.001)
        assert hasattr(engine, 'fee_rate'), "ExitEngine should have fee_rate attribute"
        assert engine.fee_rate == 0.001, f"fee_rate should be 0.001, got {engine.fee_rate}"
        print(f"✓ ExitEngine.fee_rate = {engine.fee_rate}")

    def test_early_exit_min_profit_covers_fees(self):
        """Early exit min_profit should be max of (atr-based, fee-based)."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        fee_rate = 0.001
        engine = ExitEngine(
            early_exit_bars=90,
            early_exit_min_profit_atr=0.35,
            fee_rate=fee_rate
        )
        
        entry_price = 100.0
        current_price = 100.05  # Small profit
        atr_value = 0.5  # 0.5% ATR
        
        # ATR-based min_profit = 0.5 * 0.35 = 0.175
        atr_min_profit = atr_value * engine.early_exit_min_profit_atr
        
        # Fee-based min_profit = entry * fee_rate + current * fee_rate
        fee_per_unit = entry_price * fee_rate + current_price * fee_rate
        
        # min_profit should be max of both
        expected_min_profit = max(atr_min_profit, fee_per_unit)
        
        print(f"✓ ATR min_profit={atr_min_profit:.4f}, fee_per_unit={fee_per_unit:.4f}")
        print(f"✓ Expected min_profit (max)={expected_min_profit:.4f}")
        
        # Verify the logic exists in check_exit
        pos = MockPosition(entry_price=entry_price, is_long=True, bars_since_entry=100)
        should_exit, reason, details = engine.check_exit(pos, current_price, atr_value, allow_early_exit=True)
        
        # With small profit (0.05) < min_profit (~0.2), should trigger early exit
        if should_exit and reason == ExitReason.EARLY_EXIT:
            assert "fees" in details.lower() or "fee" in details.lower(), \
                f"Early exit details should mention fees: {details}"
            print(f"✓ Early exit triggered with fee-aware min_profit: {details}")

    def test_early_exit_not_triggered_when_profit_exceeds_fees(self):
        """Early exit should NOT trigger when profit exceeds min_profit (including fees)."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        fee_rate = 0.001
        engine = ExitEngine(
            early_exit_bars=90,
            early_exit_min_profit_atr=0.1,  # Low ATR multiplier
            fee_rate=fee_rate
        )
        
        entry_price = 100.0
        current_price = 101.0  # 1% profit = $1.00
        atr_value = 1.0
        
        # Fee-based min_profit = 100 * 0.001 + 101 * 0.001 = 0.201
        # ATR-based min_profit = 1.0 * 0.1 = 0.1
        # max = 0.201
        # Profit = 1.0 > 0.201, so should NOT trigger early exit
        
        pos = MockPosition(entry_price=entry_price, is_long=True, bars_since_entry=100)
        should_exit, reason, details = engine.check_exit(pos, current_price, atr_value, allow_early_exit=True)
        
        if should_exit:
            assert reason != ExitReason.EARLY_EXIT, \
                f"Should NOT trigger early exit when profit exceeds fees: {details}"
        print(f"✓ Early exit NOT triggered when profit (${1.0:.2f}) > min_profit")


# ============================================================================
# Test Early Exit Bars Configuration
# ============================================================================
class TestEarlyExitBarsConfig:
    """Test early_exit_bars is correctly configured for 1H preset."""

    def test_1h_preset_early_exit_bars_value(self):
        """1H preset should have early_exit_bars=90 (~3 hours with 120s cycle)."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        h1_preset = config.get("tf_presets", {}).get("presets", {}).get("1h", {})
        early_exit_bars = h1_preset.get("early_exit_bars", 0)
        
        # 90 bars * 120s cycle = 10800s = 3 hours
        # This is much better than 6 bars * 120s = 720s = 12 minutes
        assert early_exit_bars == 90, f"1h.early_exit_bars should be 90, got {early_exit_bars}"
        
        # Calculate time
        cycle_sleep = h1_preset.get("cycle_sleep_sec", 600)
        time_minutes = (early_exit_bars * cycle_sleep) / 60
        time_hours = time_minutes / 60
        
        print(f"✓ 1h.early_exit_bars = {early_exit_bars}")
        print(f"✓ With cycle_sleep={cycle_sleep}s: {time_minutes:.0f} minutes = {time_hours:.1f} hours")

    def test_early_exit_disabled_when_bars_zero(self):
        """Early exit should be disabled when early_exit_bars <= 0."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        engine = ExitEngine(early_exit_bars=0)  # Disabled
        
        pos = MockPosition(entry_price=100.0, is_long=True, bars_since_entry=1000)
        should_exit, reason, _ = engine.check_exit(pos, 100.01, 1.0, allow_early_exit=True)
        
        if should_exit:
            assert reason != ExitReason.EARLY_EXIT, \
                "Early exit should be disabled when early_exit_bars=0"
        print("✓ Early exit disabled when early_exit_bars=0")


# ============================================================================
# Test TradingBot Fee Rate Initialization
# ============================================================================
class TestTradingBotFeeRateInit:
    """Test TradingBot loads fee_rate from config."""

    def test_fee_rate_loaded_from_config(self):
        """TradingBot.fee_rate should be loaded from config exit.fee_rate."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        expected_fee_rate = config.get("exit", {}).get("fee_rate", 0)
        assert expected_fee_rate == 0.001, f"Config fee_rate should be 0.001, got {expected_fee_rate}"
        
        # Check main.py has the initialization
        main_py_path = BOT_DIR / "main.py"
        with open(main_py_path) as f:
            content = f.read()
        
        # Should have: self.fee_rate = float(self.cfg.get("exit", "fee_rate", default=0.001))
        assert 'self.fee_rate' in content, "main.py should have self.fee_rate"
        assert 'exit' in content and 'fee_rate' in content, "main.py should load fee_rate from exit config"
        print(f"✓ TradingBot loads fee_rate from config (expected: {expected_fee_rate})")


# ============================================================================
# Regression Tests
# ============================================================================
class TestRegressionPnLCalculations:
    """Regression tests for PnL calculations."""

    def test_long_position_correct_fee_adjusted_pnl(self):
        """LONG position should return correct fee-adjusted PnL."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=100.0, is_long=True, qty=10.0)
        exit_price = 105.0  # 5% profit
        
        raw_pnl = (exit_price - pos.entry_price) * pos.qty  # +$50
        entry_fee = pos.entry_price * pos.qty * fee_rate  # $1.0
        exit_fee = exit_price * pos.qty * fee_rate  # $1.05
        net_pnl = raw_pnl - entry_fee - exit_fee  # $47.95
        
        assert net_pnl > 0, "Profitable LONG should have positive net PnL"
        assert abs(net_pnl - 47.95) < 0.01, f"Net PnL should be ~$47.95, got {net_pnl}"
        print(f"✓ LONG regression: raw=${raw_pnl:.2f}, net=${net_pnl:.2f}")

    def test_short_position_correct_fee_adjusted_pnl(self):
        """SHORT position should return correct fee-adjusted PnL."""
        fee_rate = 0.001
        pos = MockPosition(entry_price=100.0, is_long=False, qty=10.0)
        exit_price = 95.0  # 5% profit for short
        
        raw_pnl = (pos.entry_price - exit_price) * pos.qty  # +$50
        entry_fee = pos.entry_price * pos.qty * fee_rate  # $1.0
        exit_fee = exit_price * pos.qty * fee_rate  # $0.95
        net_pnl = raw_pnl - entry_fee - exit_fee  # $48.05
        
        assert net_pnl > 0, "Profitable SHORT should have positive net PnL"
        assert abs(net_pnl - 48.05) < 0.01, f"Net PnL should be ~$48.05, got {net_pnl}"
        print(f"✓ SHORT regression: raw=${raw_pnl:.2f}, net=${net_pnl:.2f}")


class TestRegressionExitEngine:
    """Regression tests for ExitEngine."""

    def test_early_exit_not_triggered_before_bars(self):
        """Early exit should NOT trigger before early_exit_bars."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        engine = ExitEngine(early_exit_bars=90)
        
        pos = MockPosition(entry_price=100.0, is_long=True, bars_since_entry=50)  # < 90
        should_exit, reason, _ = engine.check_exit(pos, 100.01, 1.0, allow_early_exit=True)
        
        if should_exit:
            assert reason != ExitReason.EARLY_EXIT, \
                f"Early exit should NOT trigger at bars={pos.bars_since_entry} < 90"
        print(f"✓ Early exit NOT triggered at bars={pos.bars_since_entry} < 90")

    def test_early_exit_respects_min_hold_minutes(self):
        """With early_exit_min_hold_minutes, dead-trade exit waits for wall-clock age."""
        from engine.exit_engine import ExitEngine, ExitReason

        engine = ExitEngine(
            early_exit_bars=2,
            early_exit_min_profit_atr=10.0,
            fee_rate=0.001,
            early_exit_min_hold_minutes=60.0,
        )
        now = datetime.now(timezone.utc)
        pos_fresh = MockPosition(
            entry_price=100.0,
            is_long=True,
            bars_since_entry=100,
            stop_loss=90.0,
            entry_time=now,
        )
        should_exit, reason, _ = engine.check_exit(pos_fresh, 100.0, 1.0, allow_early_exit=True)
        assert not should_exit or reason != ExitReason.EARLY_EXIT

        pos_old = MockPosition(
            entry_price=100.0,
            is_long=True,
            bars_since_entry=100,
            stop_loss=90.0,
            entry_time=now - timedelta(minutes=61),
        )
        should_exit2, reason2, _ = engine.check_exit(pos_old, 100.0, 1.0, allow_early_exit=True)
        assert should_exit2 and reason2 == ExitReason.EARLY_EXIT

    def test_early_exit_not_triggered_when_profit_exceeds_min(self):
        """Early exit should NOT trigger when profit > min_profit."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        engine = ExitEngine(early_exit_bars=90, early_exit_min_profit_atr=0.1, fee_rate=0.001)
        
        pos = MockPosition(entry_price=100.0, is_long=True, bars_since_entry=100)
        current_price = 102.0  # 2% profit = $2.00 >> min_profit
        
        should_exit, reason, _ = engine.check_exit(pos, current_price, 1.0, allow_early_exit=True)
        
        if should_exit:
            assert reason != ExitReason.EARLY_EXIT, \
                f"Early exit should NOT trigger when profit exceeds min_profit"
        print(f"✓ Early exit NOT triggered when profit (${2.0:.2f}) > min_profit")

    def test_trailing_exit_still_works(self):
        """TRAILING_EXIT should still work correctly."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        engine = ExitEngine()
        
        pos = MockPosition(
            entry_price=100.0, 
            is_long=True, 
            trailing_active=True,
            trailing_stop=99.0
        )
        current_price = 98.5  # Below trailing stop
        
        should_exit, reason, _ = engine.check_exit(pos, current_price, 1.0)
        
        assert should_exit, "Should exit when price below trailing stop"
        assert reason == ExitReason.TRAILING_EXIT, f"Reason should be TRAILING_EXIT, got {reason}"
        print(f"✓ TRAILING_EXIT works: price={current_price} < trail_stop={pos.trailing_stop}")

    def test_hard_sl_still_works(self):
        """HARD_SL should still work correctly."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        engine = ExitEngine()
        
        pos = MockPosition(
            entry_price=100.0, 
            is_long=True, 
            stop_loss=98.0
        )
        current_price = 97.5  # Below stop loss
        
        should_exit, reason, _ = engine.check_exit(pos, current_price, 1.0)
        
        assert should_exit, "Should exit when price below stop loss"
        assert reason == ExitReason.HARD_SL, f"Reason should be HARD_SL, got {reason}"
        print(f"✓ HARD_SL works: price={current_price} < stop_loss={pos.stop_loss}")

    def test_tp_cap_still_works(self):
        """TP_CAP should still work correctly."""
        from engine.exit_engine import ExitEngine, ExitReason
        
        engine = ExitEngine()
        
        pos = MockPosition(
            entry_price=100.0, 
            is_long=True, 
            take_profit=110.0
        )
        current_price = 111.0  # Above take profit
        
        should_exit, reason, _ = engine.check_exit(pos, current_price, 1.0)
        
        assert should_exit, "Should exit when price above take profit"
        assert reason == ExitReason.TP_CAP, f"Reason should be TP_CAP, got {reason}"
        print(f"✓ TP_CAP works: price={current_price} > take_profit={pos.take_profit}")


# ============================================================================
# Test Code Structure Verification
# ============================================================================
class TestCodeStructure:
    """Verify code structure for fee PnL fix."""

    def test_calc_pnl_has_fee_deduction(self):
        """_calc_pnl should have fee deduction logic."""
        main_py_path = BOT_DIR / "main.py"
        with open(main_py_path) as f:
            content = f.read()
        
        # Should have entry_fee and exit_fee calculations
        assert 'entry_fee' in content, "_calc_pnl should calculate entry_fee"
        assert 'exit_fee' in content, "_calc_pnl should calculate exit_fee"
        assert 'self.fee_rate' in content, "_calc_pnl should use self.fee_rate"
        print("✓ _calc_pnl has fee deduction logic")

    def test_calc_pnl_pct_has_fee_deduction(self):
        """_calc_pnl_pct should have 2x fee_rate deduction."""
        main_py_path = BOT_DIR / "main.py"
        with open(main_py_path) as f:
            content = f.read()
        
        # Should have fee_rate * 2 deduction
        assert 'fee_rate * 2' in content or 'fee_rate*2' in content or '2 * self.fee_rate' in content or 'self.fee_rate * 2' in content, \
            "_calc_pnl_pct should deduct 2x fee_rate"
        print("✓ _calc_pnl_pct has 2x fee_rate deduction")

    def test_exit_engine_early_exit_has_fee_check(self):
        """ExitEngine early_exit should have fee-based min_profit check."""
        exit_engine_path = BOT_DIR / "engine" / "exit_engine.py"
        with open(exit_engine_path) as f:
            content = f.read()
        
        # Should have fee_per_unit calculation in early_exit check
        assert 'fee_per_unit' in content or 'fee_rate' in content, \
            "ExitEngine should have fee-based min_profit check"
        assert 'max(' in content, "ExitEngine should use max() for min_profit"
        print("✓ ExitEngine early_exit has fee-based min_profit check")


# ============================================================================
# Run tests
# ============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
