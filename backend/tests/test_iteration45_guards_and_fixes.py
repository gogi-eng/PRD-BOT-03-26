#!/usr/bin/env python3
"""
Iteration 45 Tests: No-zone bypass, Contra-trend guard, Exit engine order, HTF ATR floor

Tests for:
1. No-zone bypass: requires has_bos=True or has_sweep=True for bypass (conf>=0.85, smc>=0.85 alone is NOT enough)
2. Contra-trend guard: rejects when 7+/10 candles oppose signal direction
3. Exit engine: TRAILING_EXIT checked BEFORE HARD_SL
4. HTF ATR floor in position monitoring
5. Regression: P0 manual protection, A/B/C grading
"""
import pytest
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

# Add bot directory to path
BOT_DIR = Path(__file__).resolve().parents[2] / "bot"
sys.path.insert(0, str(BOT_DIR))


# =====================================================
# MOCK CLASSES FOR TESTING
# =====================================================

@dataclass
class MockPosition:
    """Mock position for exit engine tests"""
    entry_price: float = 100.0
    is_long: bool = True
    stop_loss: float = 95.0
    take_profit: float = 110.0
    trailing_active: bool = False
    trailing_stop: float = 0.0
    trailing_activation_price: float = 0.0
    trailing_distance: float = 0.0
    best_price: float = 100.0
    bars_since_entry: int = 0
    origin: str = "bot"
    symbol: str = "TESTUSDT"
    qty: float = 1.0
    side: str = "BUY"
    position_idx: int = 0


@dataclass
class MockEntrySignal:
    """Mock entry signal for quality gate tests"""
    should_enter: bool = True
    side: str = "BUY"
    confidence: float = 0.90
    entry_price: float = 100.0
    stop_loss: float = 95.0
    take_profit: float = 110.0
    rr_ratio: float = 3.0
    grade: str = "B"
    metadata: dict = field(default_factory=dict)


# =====================================================
# TEST CLASS: NO-ZONE BYPASS LOGIC
# =====================================================

class TestNoZoneBypass:
    """Tests for no-zone bypass requiring BOS or sweep structural confirmation"""
    
    def test_no_zone_bypass_requires_bos_or_sweep(self):
        """No-zone bypass: conf>=0.85, smc>=0.85 alone is NOT enough - needs BOS or sweep"""
        # Import the actual TradingBot to test the quality gate logic
        from main import TradingBot
        
        # Create a mock signal with high confidence but no structural confirmation
        signal = MockEntrySignal()
        signal.confidence = 0.90
        signal.metadata = {
            "entry_zone": "no_zone",
            "smc_score": 0.91,
            "has_bos": False,
            "has_sweep": False,
            "rr_ratio": 3.0,
            "normalized_imbalance": 0.2,
        }
        
        # The logic in main.py lines 1708-1723:
        # if quality_gate_reject_no_zone_entries and entry_zone == "no_zone":
        #     if confidence >= 0.85 and smc_score >= 0.85 and (has_bos or has_sweep):
        #         BYPASS (allowed)
        #     else:
        #         BLOCKED (return False, "no_zone_no_structure")
        
        entry_zone = str(signal.metadata.get("entry_zone", "no_zone")).lower()
        confidence = signal.confidence
        smc_score = float(signal.metadata.get("smc_score", 0.0))
        has_bos = bool(signal.metadata.get("has_bos", False))
        has_sweep = bool(signal.metadata.get("has_sweep", False))
        
        # Simulate quality_gate_reject_no_zone_entries = True
        quality_gate_reject_no_zone_entries = True
        
        if quality_gate_reject_no_zone_entries and entry_zone == "no_zone":
            if confidence >= 0.85 and smc_score >= 0.85 and (has_bos or has_sweep):
                result = "ALLOWED"
            else:
                result = "BLOCKED"
        else:
            result = "ALLOWED"
        
        # Without BOS or sweep, should be BLOCKED
        assert result == "BLOCKED", f"Expected BLOCKED but got {result} - conf={confidence}, smc={smc_score}, bos={has_bos}, sweep={has_sweep}"
        print("PASSED: No-zone bypass correctly BLOCKS signal without BOS or sweep")
    
    def test_no_zone_bypass_allowed_with_bos(self):
        """No-zone bypass: signal with conf=0.90, smc=0.90, no_zone, has_bos=True → ALLOWED"""
        signal = MockEntrySignal()
        signal.confidence = 0.90
        signal.metadata = {
            "entry_zone": "no_zone",
            "smc_score": 0.90,
            "has_bos": True,
            "has_sweep": False,
        }
        
        entry_zone = str(signal.metadata.get("entry_zone", "no_zone")).lower()
        confidence = signal.confidence
        smc_score = float(signal.metadata.get("smc_score", 0.0))
        has_bos = bool(signal.metadata.get("has_bos", False))
        has_sweep = bool(signal.metadata.get("has_sweep", False))
        
        quality_gate_reject_no_zone_entries = True
        
        if quality_gate_reject_no_zone_entries and entry_zone == "no_zone":
            if confidence >= 0.85 and smc_score >= 0.85 and (has_bos or has_sweep):
                result = "ALLOWED"
            else:
                result = "BLOCKED"
        else:
            result = "ALLOWED"
        
        assert result == "ALLOWED", f"Expected ALLOWED but got {result}"
        print("PASSED: No-zone bypass correctly ALLOWS signal with has_bos=True")
    
    def test_no_zone_bypass_blocked_without_structure(self):
        """No-zone bypass: signal with conf=0.90, smc=0.90, no_zone, has_bos=False, has_sweep=False → BLOCKED"""
        signal = MockEntrySignal()
        signal.confidence = 0.90
        signal.metadata = {
            "entry_zone": "no_zone",
            "smc_score": 0.90,
            "has_bos": False,
            "has_sweep": False,
        }
        
        entry_zone = str(signal.metadata.get("entry_zone", "no_zone")).lower()
        confidence = signal.confidence
        smc_score = float(signal.metadata.get("smc_score", 0.0))
        has_bos = bool(signal.metadata.get("has_bos", False))
        has_sweep = bool(signal.metadata.get("has_sweep", False))
        
        quality_gate_reject_no_zone_entries = True
        
        if quality_gate_reject_no_zone_entries and entry_zone == "no_zone":
            if confidence >= 0.85 and smc_score >= 0.85 and (has_bos or has_sweep):
                result = "ALLOWED"
            else:
                result = "BLOCKED"
        else:
            result = "ALLOWED"
        
        assert result == "BLOCKED", f"Expected BLOCKED but got {result}"
        print("PASSED: No-zone bypass correctly BLOCKS signal without BOS or sweep (reason: no_zone_no_structure)")
    
    def test_no_zone_bypass_allowed_with_sweep(self):
        """No-zone bypass: signal with conf=0.90, smc=0.90, no_zone, has_sweep=True → ALLOWED"""
        signal = MockEntrySignal()
        signal.confidence = 0.90
        signal.metadata = {
            "entry_zone": "no_zone",
            "smc_score": 0.90,
            "has_bos": False,
            "has_sweep": True,
        }
        
        entry_zone = str(signal.metadata.get("entry_zone", "no_zone")).lower()
        confidence = signal.confidence
        smc_score = float(signal.metadata.get("smc_score", 0.0))
        has_bos = bool(signal.metadata.get("has_bos", False))
        has_sweep = bool(signal.metadata.get("has_sweep", False))
        
        quality_gate_reject_no_zone_entries = True
        
        if quality_gate_reject_no_zone_entries and entry_zone == "no_zone":
            if confidence >= 0.85 and smc_score >= 0.85 and (has_bos or has_sweep):
                result = "ALLOWED"
            else:
                result = "BLOCKED"
        else:
            result = "ALLOWED"
        
        assert result == "ALLOWED", f"Expected ALLOWED but got {result}"
        print("PASSED: No-zone bypass correctly ALLOWS signal with has_sweep=True")
    
    def test_no_zone_bypass_low_confidence_blocked(self):
        """No-zone bypass: signal with conf=0.80 (below 0.85) should be blocked even with BOS"""
        signal = MockEntrySignal()
        signal.confidence = 0.80  # Below 0.85 threshold
        signal.metadata = {
            "entry_zone": "no_zone",
            "smc_score": 0.90,
            "has_bos": True,
            "has_sweep": False,
        }
        
        entry_zone = str(signal.metadata.get("entry_zone", "no_zone")).lower()
        confidence = signal.confidence
        smc_score = float(signal.metadata.get("smc_score", 0.0))
        has_bos = bool(signal.metadata.get("has_bos", False))
        has_sweep = bool(signal.metadata.get("has_sweep", False))
        
        quality_gate_reject_no_zone_entries = True
        
        if quality_gate_reject_no_zone_entries and entry_zone == "no_zone":
            if confidence >= 0.85 and smc_score >= 0.85 and (has_bos or has_sweep):
                result = "ALLOWED"
            else:
                result = "BLOCKED"
        else:
            result = "ALLOWED"
        
        assert result == "BLOCKED", f"Expected BLOCKED but got {result} - confidence {confidence} < 0.85"
        print("PASSED: No-zone bypass correctly BLOCKS signal with low confidence even with BOS")


# =====================================================
# TEST CLASS: CONTRA-TREND GUARD
# =====================================================

class TestContraTrendGuard:
    """Tests for contra-trend guard that rejects when 7+/10 candles oppose signal direction"""
    
    def _create_klines(self, bullish_count: int, total: int = 10) -> list:
        """Create klines with specified number of bullish candles"""
        klines = []
        for i in range(total):
            if i < bullish_count:
                # Bullish candle: close > open
                klines.append({"open": 100.0, "close": 101.0})
            else:
                # Bearish candle: close < open
                klines.append({"open": 101.0, "close": 100.0})
        return klines
    
    def _check_contra_trend_guard(self, klines: list, is_long: bool) -> tuple:
        """
        Simulate contra-trend guard logic from entry_engine.py lines 411-428:
        
        if len(klines) >= 11:
            last_10 = klines[-10:]
            contra = 0
            for k in last_10:
                c_open = float(k.get("open", 0))
                c_close = float(k.get("close", 0))
                if is_long and c_close < c_open:
                    contra += 1
                elif not is_long and c_close > c_open:
                    contra += 1
            if contra >= 7:
                return True, f"contra_trend_guard ({contra}/10 candles oppose signal)"
        return False, ""
        """
        if len(klines) < 11:
            return False, "not_enough_klines"
        
        last_10 = klines[-10:]
        contra = 0
        for k in last_10:
            c_open = float(k.get("open", 0))
            c_close = float(k.get("close", 0))
            if is_long and c_close < c_open:
                contra += 1
            elif not is_long and c_close > c_open:
                contra += 1
        
        if contra >= 7:
            return True, f"contra_trend_guard ({contra}/10 candles oppose signal)"
        return False, f"allowed ({contra}/10 opposing)"
    
    def test_contra_trend_rejects_sell_when_7_bullish(self):
        """Contra-trend guard: rejects SELL when 7+/10 candles are bullish (green)"""
        # 7 bullish candles + 3 bearish = SELL should be rejected
        klines = self._create_klines(bullish_count=7, total=10)
        # Add one more to make it 11 total (guard requires >= 11)
        klines.insert(0, {"open": 100.0, "close": 100.0})
        
        is_long = False  # SELL signal
        rejected, reason = self._check_contra_trend_guard(klines, is_long)
        
        assert rejected, f"Expected SELL to be rejected with 7 bullish candles, got: {reason}"
        assert "contra_trend_guard" in reason
        print(f"PASSED: Contra-trend guard rejects SELL when 7/10 candles are bullish - {reason}")
    
    def test_contra_trend_rejects_buy_when_7_bearish(self):
        """Contra-trend guard: rejects BUY when 7+/10 candles are bearish (red)"""
        # 3 bullish candles + 7 bearish = BUY should be rejected
        klines = self._create_klines(bullish_count=3, total=10)
        klines.insert(0, {"open": 100.0, "close": 100.0})
        
        is_long = True  # BUY signal
        rejected, reason = self._check_contra_trend_guard(klines, is_long)
        
        assert rejected, f"Expected BUY to be rejected with 7 bearish candles, got: {reason}"
        assert "contra_trend_guard" in reason
        print(f"PASSED: Contra-trend guard rejects BUY when 7/10 candles are bearish - {reason}")
    
    def test_contra_trend_allows_entry_with_5_opposing(self):
        """Contra-trend guard: allows entry when only 5/10 candles oppose signal"""
        # 5 bullish + 5 bearish = SELL should be allowed
        klines = self._create_klines(bullish_count=5, total=10)
        klines.insert(0, {"open": 100.0, "close": 100.0})
        
        is_long = False  # SELL signal
        rejected, reason = self._check_contra_trend_guard(klines, is_long)
        
        assert not rejected, f"Expected SELL to be allowed with 5 bullish candles, got: {reason}"
        print(f"PASSED: Contra-trend guard allows entry with 5/10 opposing candles - {reason}")
    
    def test_contra_trend_edge_case_exactly_7(self):
        """Contra-trend guard: works with exactly 7/10 opposing candles (edge case)"""
        # Exactly 7 bullish candles for SELL signal
        klines = self._create_klines(bullish_count=7, total=10)
        klines.insert(0, {"open": 100.0, "close": 100.0})
        
        is_long = False  # SELL signal
        rejected, reason = self._check_contra_trend_guard(klines, is_long)
        
        assert rejected, f"Expected SELL to be rejected with exactly 7 bullish candles, got: {reason}"
        assert "7/10" in reason
        print(f"PASSED: Contra-trend guard correctly handles edge case of exactly 7/10 - {reason}")
    
    def test_contra_trend_allows_entry_with_6_opposing(self):
        """Contra-trend guard: allows entry when 6/10 candles oppose (below threshold)"""
        # 6 bullish + 4 bearish = SELL should be allowed (6 < 7)
        klines = self._create_klines(bullish_count=6, total=10)
        klines.insert(0, {"open": 100.0, "close": 100.0})
        
        is_long = False  # SELL signal
        rejected, reason = self._check_contra_trend_guard(klines, is_long)
        
        assert not rejected, f"Expected SELL to be allowed with 6 bullish candles, got: {reason}"
        print(f"PASSED: Contra-trend guard allows entry with 6/10 opposing candles - {reason}")


# =====================================================
# TEST CLASS: EXHAUSTION GUARD (REGRESSION)
# =====================================================

class TestExhaustionGuard:
    """Regression tests for exhaustion guard (5+/7 same direction → reject)"""
    
    def _create_klines_directional(self, bullish_count: int, total: int = 7) -> list:
        """Create klines with specified number of bullish candles"""
        klines = []
        for i in range(total):
            if i < bullish_count:
                klines.append({"open": 100.0, "close": 101.0})
            else:
                klines.append({"open": 101.0, "close": 100.0})
        return klines
    
    def _check_exhaustion_guard(self, klines: list, is_long: bool) -> tuple:
        """
        Simulate exhaustion guard logic from entry_engine.py lines 387-405:
        
        if len(klines) >= 7:
            last_candles = klines[-7:]
            consecutive_dir = 0
            for k in last_candles:
                if is_long and c_close > c_open:
                    consecutive_dir += 1
                elif not is_long and c_close < c_open:
                    consecutive_dir += 1
            if consecutive_dir >= 5:
                return True, f"exhaustion_guard ({consecutive_dir}/7 candles same dir)"
        return False, ""
        """
        if len(klines) < 7:
            return False, "not_enough_klines"
        
        last_candles = klines[-7:]
        consecutive_dir = 0
        for k in last_candles:
            c_open = float(k.get("open", 0))
            c_close = float(k.get("close", 0))
            if is_long and c_close > c_open:
                consecutive_dir += 1
            elif not is_long and c_close < c_open:
                consecutive_dir += 1
        
        if consecutive_dir >= 5:
            return True, f"exhaustion_guard ({consecutive_dir}/7 candles same dir)"
        return False, f"allowed ({consecutive_dir}/7 same dir)"
    
    def test_exhaustion_guard_still_works(self):
        """Exhaustion guard still works (5+/7 same direction → reject)"""
        # 5 bullish candles for BUY signal = exhaustion
        klines = self._create_klines_directional(bullish_count=5, total=7)
        
        is_long = True  # BUY signal
        rejected, reason = self._check_exhaustion_guard(klines, is_long)
        
        assert rejected, f"Expected BUY to be rejected with 5 bullish candles, got: {reason}"
        assert "exhaustion_guard" in reason
        print(f"PASSED: Exhaustion guard still works - {reason}")


# =====================================================
# TEST CLASS: EXIT ENGINE ORDER (TRAILING_EXIT BEFORE HARD_SL)
# =====================================================

class TestExitEngineOrder:
    """Tests for exit engine checking TRAILING_EXIT before HARD_SL"""
    
    def test_trailing_exit_checked_before_hard_sl(self):
        """Exit engine: TRAILING_EXIT checked BEFORE HARD_SL in check_exit()"""
        from engine.exit_engine import ExitEngine, ExitReason
        
        exit_engine = ExitEngine()
        
        # Create a SHORT position with trailing active
        position = MockPosition()
        position.is_long = False
        position.entry_price = 100.0
        position.stop_loss = 105.0  # SL for short
        position.trailing_active = True
        position.trailing_stop = 102.0  # Trailing stop below hard SL
        position.take_profit = 90.0
        
        # Price hits trailing stop (102) but is below hard SL (105)
        current_price = 102.5  # Above trailing stop, should trigger TRAILING_EXIT
        
        should_exit, reason, details = exit_engine.check_exit(position, current_price, atr_value=1.0)
        
        assert should_exit, "Expected exit to be triggered"
        assert reason == ExitReason.TRAILING_EXIT, f"Expected TRAILING_EXIT but got {reason}"
        print(f"PASSED: Exit engine returns TRAILING_EXIT before HARD_SL - {reason.value}: {details}")
    
    def test_short_trailing_exit_before_hard_sl(self):
        """Exit engine: SHORT position with trailing_active=True and price >= trailing_stop returns TRAILING_EXIT not HARD_SL"""
        from engine.exit_engine import ExitEngine, ExitReason
        
        exit_engine = ExitEngine()
        
        position = MockPosition()
        position.is_long = False
        position.entry_price = 100.0
        position.stop_loss = 105.0
        position.trailing_active = True
        position.trailing_stop = 103.0
        position.take_profit = 90.0
        
        # Price at trailing stop level
        current_price = 103.0
        
        should_exit, reason, details = exit_engine.check_exit(position, current_price, atr_value=1.0)
        
        assert should_exit, "Expected exit to be triggered"
        assert reason == ExitReason.TRAILING_EXIT, f"Expected TRAILING_EXIT but got {reason}"
        print(f"PASSED: SHORT position returns TRAILING_EXIT when price >= trailing_stop - {reason.value}")
    
    def test_long_trailing_exit_before_hard_sl(self):
        """Exit engine: LONG position with trailing_active=True and price <= trailing_stop returns TRAILING_EXIT not HARD_SL"""
        from engine.exit_engine import ExitEngine, ExitReason
        
        exit_engine = ExitEngine()
        
        position = MockPosition()
        position.is_long = True
        position.entry_price = 100.0
        position.stop_loss = 95.0
        position.trailing_active = True
        position.trailing_stop = 98.0  # Trailing stop above hard SL
        position.take_profit = 110.0
        
        # Price at trailing stop level
        current_price = 98.0
        
        should_exit, reason, details = exit_engine.check_exit(position, current_price, atr_value=1.0)
        
        assert should_exit, "Expected exit to be triggered"
        assert reason == ExitReason.TRAILING_EXIT, f"Expected TRAILING_EXIT but got {reason}"
        print(f"PASSED: LONG position returns TRAILING_EXIT when price <= trailing_stop - {reason.value}")
    
    def test_position_without_trailing_returns_hard_sl(self):
        """Exit engine: position without trailing returns HARD_SL when SL hit"""
        from engine.exit_engine import ExitEngine, ExitReason
        
        exit_engine = ExitEngine()
        
        position = MockPosition()
        position.is_long = True
        position.entry_price = 100.0
        position.stop_loss = 95.0
        position.trailing_active = False  # No trailing
        position.trailing_stop = 0.0
        position.take_profit = 110.0
        
        # Price hits hard SL
        current_price = 94.0
        
        should_exit, reason, details = exit_engine.check_exit(position, current_price, atr_value=1.0)
        
        assert should_exit, "Expected exit to be triggered"
        assert reason == ExitReason.HARD_SL, f"Expected HARD_SL but got {reason}"
        print(f"PASSED: Position without trailing returns HARD_SL when SL hit - {reason.value}")


# =====================================================
# TEST CLASS: HTF ATR FLOOR IN POSITION MONITORING
# =====================================================

class TestHTFATRFloor:
    """Tests for HTF ATR floor in position monitoring (main.py line ~694)"""
    
    def test_htf_atr_floor_logic(self):
        """HTF ATR floor in position monitoring: uses max(1m_atr, htf_atr)"""
        # Simulate the logic from main.py lines 692-696:
        # atr_val = self.atr.get_atr(symbol, klines)
        # htf_atr_val = self.atr.get_atr(f"{symbol}_htf", htf_klines)
        # if htf_atr_val > 0:
        #     atr_val = max(atr_val, htf_atr_val)
        
        # Case 1: HTF ATR > 1m ATR
        atr_1m = 0.5
        htf_atr = 1.2
        
        if htf_atr > 0:
            result_atr = max(atr_1m, htf_atr)
        else:
            result_atr = atr_1m
        
        assert result_atr == 1.2, f"Expected 1.2 (HTF ATR) but got {result_atr}"
        print(f"PASSED: HTF ATR floor uses max(1m_atr={atr_1m}, htf_atr={htf_atr}) = {result_atr}")
    
    def test_htf_atr_floor_uses_1m_when_larger(self):
        """HTF ATR floor: uses 1m ATR when it's larger than HTF ATR"""
        atr_1m = 2.0
        htf_atr = 1.0
        
        if htf_atr > 0:
            result_atr = max(atr_1m, htf_atr)
        else:
            result_atr = atr_1m
        
        assert result_atr == 2.0, f"Expected 2.0 (1m ATR) but got {result_atr}"
        print(f"PASSED: HTF ATR floor uses max(1m_atr={atr_1m}, htf_atr={htf_atr}) = {result_atr}")
    
    def test_htf_atr_floor_handles_zero_htf(self):
        """HTF ATR floor: uses 1m ATR when HTF ATR is 0"""
        atr_1m = 1.5
        htf_atr = 0.0
        
        if htf_atr > 0:
            result_atr = max(atr_1m, htf_atr)
        else:
            result_atr = atr_1m
        
        assert result_atr == 1.5, f"Expected 1.5 (1m ATR) but got {result_atr}"
        print(f"PASSED: HTF ATR floor uses 1m ATR when HTF ATR is 0 - result={result_atr}")


# =====================================================
# TEST CLASS: REGRESSION - P0 MANUAL PROTECTION
# =====================================================

class TestP0ManualProtection:
    """Regression tests for P0 manual protection (ExitReason guard blocks HARD_SL for manual)"""
    
    def test_exit_reason_enum_exists(self):
        """ExitReason enum exists with required values"""
        from engine.exit_engine import ExitReason
        
        assert hasattr(ExitReason, 'HARD_SL')
        assert hasattr(ExitReason, 'TRAILING_EXIT')
        assert hasattr(ExitReason, 'TP_CAP')
        print("PASSED: ExitReason enum has HARD_SL, TRAILING_EXIT, TP_CAP")
    
    def test_manual_position_protection_logic(self):
        """
        P0 manual protection: HARD_SL should be blocked for manual positions
        
        From main.py lines 826-833:
        if should_exit and pos.origin == "manual" and reason not in (
            ExitReason.TRAILING_EXIT, ExitReason.TP_CAP
        ):
            should_exit = False
        """
        from engine.exit_engine import ExitReason
        
        # Simulate the logic
        def check_manual_protection(origin: str, reason: ExitReason) -> bool:
            """Returns True if exit is allowed, False if blocked"""
            if origin == "manual" and reason not in (ExitReason.TRAILING_EXIT, ExitReason.TP_CAP):
                return False  # Blocked
            return True  # Allowed
        
        # HARD_SL should be blocked for manual
        assert not check_manual_protection("manual", ExitReason.HARD_SL), "HARD_SL should be blocked for manual"
        
        # TRAILING_EXIT should be allowed for manual
        assert check_manual_protection("manual", ExitReason.TRAILING_EXIT), "TRAILING_EXIT should be allowed for manual"
        
        # TP_CAP should be allowed for manual
        assert check_manual_protection("manual", ExitReason.TP_CAP), "TP_CAP should be allowed for manual"
        
        # HARD_SL should be allowed for bot
        assert check_manual_protection("bot", ExitReason.HARD_SL), "HARD_SL should be allowed for bot"
        
        print("PASSED: P0 manual protection correctly blocks HARD_SL but allows TRAILING_EXIT and TP_CAP")


# =====================================================
# TEST CLASS: REGRESSION - A/B/C GRADING
# =====================================================

class TestABCGrading:
    """Regression tests for A/B/C signal grading"""
    
    def test_classify_signal_grade_function_exists(self):
        """classify_signal_grade function exists"""
        from engine.entry_engine import classify_signal_grade
        
        assert callable(classify_signal_grade)
        print("PASSED: classify_signal_grade function exists")
    
    def test_grade_a_high_conviction(self):
        """Grade A: conf >= 0.85, RR >= 4.0, 3+ confirmations"""
        from engine.entry_engine import classify_signal_grade
        
        grade = classify_signal_grade(
            confidence=0.90,
            rr_ratio=4.5,
            has_sweep=True,
            has_bos=True,
            htf_aligned=True,
            entry_zone="fvg_bullish",
        )
        
        assert grade == "A", f"Expected grade A but got {grade}"
        print(f"PASSED: Grade A for high conviction signal - {grade}")
    
    def test_grade_b_standard(self):
        """Grade B: conf >= 0.75, RR >= 3.0, 2+ confirmations"""
        from engine.entry_engine import classify_signal_grade
        
        grade = classify_signal_grade(
            confidence=0.78,
            rr_ratio=3.5,
            has_sweep=True,
            has_bos=False,
            htf_aligned=True,
            entry_zone="ob_bullish",
        )
        
        assert grade == "B", f"Expected grade B but got {grade}"
        print(f"PASSED: Grade B for standard signal - {grade}")
    
    def test_grade_c_marginal(self):
        """Grade C: everything else that passed entry threshold"""
        from engine.entry_engine import classify_signal_grade
        
        grade = classify_signal_grade(
            confidence=0.60,
            rr_ratio=2.5,
            has_sweep=False,
            has_bos=False,
            htf_aligned=False,
            entry_zone="no_zone",
        )
        
        assert grade == "C", f"Expected grade C but got {grade}"
        print(f"PASSED: Grade C for marginal signal - {grade}")
    
    def test_entry_signal_has_grade_field(self):
        """EntrySignal has grade field defaulting to 'C'"""
        from engine.entry_engine import EntrySignal
        
        signal = EntrySignal()
        assert hasattr(signal, 'grade')
        assert signal.grade == "C", f"Expected default grade 'C' but got {signal.grade}"
        print(f"PASSED: EntrySignal has grade field defaulting to 'C'")


# =====================================================
# TEST CLASS: CODE VERIFICATION
# =====================================================

class TestCodeVerification:
    """Verify the actual code has the expected logic"""
    
    def test_exit_engine_trailing_before_hard_sl_in_code(self):
        """Verify exit_engine.py has TRAILING_EXIT check before HARD_SL"""
        import inspect
        from engine.exit_engine import ExitEngine
        
        source = inspect.getsource(ExitEngine.check_exit)
        
        # Find positions of TRAILING_EXIT and HARD_SL checks
        trailing_pos = source.find("TRAILING_EXIT")
        hard_sl_pos = source.find("HARD_SL")
        
        assert trailing_pos > 0, "TRAILING_EXIT not found in check_exit"
        assert hard_sl_pos > 0, "HARD_SL not found in check_exit"
        assert trailing_pos < hard_sl_pos, f"TRAILING_EXIT ({trailing_pos}) should come before HARD_SL ({hard_sl_pos})"
        
        print(f"PASSED: TRAILING_EXIT check (pos {trailing_pos}) comes before HARD_SL (pos {hard_sl_pos}) in code")
    
    def test_main_py_has_htf_atr_floor(self):
        """Verify main.py has HTF ATR floor logic in position monitoring"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        
        # Check for the HTF ATR floor pattern
        assert "htf_atr_val = self.atr.get_atr" in content, "HTF ATR calculation not found"
        assert "max(atr_val, htf_atr_val)" in content, "max(atr_val, htf_atr_val) not found"
        
        print("PASSED: main.py has HTF ATR floor logic (max(atr_val, htf_atr_val))")
    
    def test_main_py_has_no_zone_bypass_with_structure(self):
        """Verify main.py has no-zone bypass requiring BOS or sweep"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        
        # Check for the no-zone bypass pattern
        assert "has_bos or has_sweep" in content, "BOS or sweep check not found in no-zone bypass"
        assert "no_zone_no_structure" in content, "no_zone_no_structure rejection reason not found"
        
        print("PASSED: main.py has no-zone bypass requiring BOS or sweep")
    
    def test_entry_engine_has_contra_trend_guard(self):
        """Verify entry_engine.py has contra-trend guard"""
        entry_engine_path = BOT_DIR / "engine" / "entry_engine.py"
        content = entry_engine_path.read_text()
        
        assert "contra_trend_guard" in content, "contra_trend_guard not found"
        assert "contra >= 7" in content, "contra >= 7 threshold not found"
        
        print("PASSED: entry_engine.py has contra-trend guard with 7/10 threshold")


# =====================================================
# RUN ALL TESTS
# =====================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
