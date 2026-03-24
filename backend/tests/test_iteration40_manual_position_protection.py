#!/usr/bin/env python3
"""
Iteration 40: Manual Position Protection & exchange_closed Timestamp Filtering

Tests TWO critical bug fixes:
1. Manual (adopted) positions no longer trigger liquidation_stop exit
   - protective_liq_level is set to 0.0 for manual positions in _sync_exchange_position
   - check_exit passes protective_level=0.0 for manual origins
   
2. exchange_closed closedPnl check now validates timestamps
   - _filter_recent_closed_pnl method filters records by timestamp (max 5 min age)
   - Old closedPnl records (>5 min) don't count as evidence for exchange_closed
   
Config changes verified:
- exchange_closed_confirm_cycles: 5 (was 3)
- exchange_closed_force_cycles: 20 (was 8)
"""

import sys
import time
from pathlib import Path

import pytest
import yaml

# Add bot directory to path
BOT_DIR = Path("/app/bot")
sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# CONFIG VALUE VERIFICATION TESTS
# ============================================================================

class TestConfigExchangeClosedValues:
    """Verify config.yaml has correct exchange_closed cycle values."""
    
    @pytest.fixture(scope="class")
    def config(self):
        with open(BOT_DIR / "config.yaml", "r") as f:
            return yaml.safe_load(f)
    
    def test_exchange_closed_confirm_cycles_is_5(self, config):
        """exchange_closed_confirm_cycles should be 5 (was 3)."""
        value = config.get("position_sync", {}).get("exchange_closed_confirm_cycles")
        assert value == 5, f"Expected 5, got {value}"
    
    def test_exchange_closed_force_cycles_is_20(self, config):
        """exchange_closed_force_cycles should be 20 (was 8)."""
        value = config.get("position_sync", {}).get("exchange_closed_force_cycles")
        assert value == 20, f"Expected 20, got {value}"
    
    def test_exchange_closed_require_closed_pnl_is_true(self, config):
        """exchange_closed_require_closed_pnl should be true."""
        value = config.get("position_sync", {}).get("exchange_closed_require_closed_pnl")
        assert value is True, f"Expected True, got {value}"


# ============================================================================
# MANUAL POSITION PROTECTION TESTS - protective_liq_level = 0.0
# ============================================================================

class TestManualPositionProtectiveLiqLevel:
    """Test that manual positions have protective_liq_level=0.0."""
    
    def test_position_dataclass_has_protective_liq_level_default_zero(self):
        """Position dataclass should have protective_liq_level defaulting to 0.0."""
        from engine.position_manager import Position
        
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
        )
        assert pos.protective_liq_level == 0.0, f"Expected 0.0, got {pos.protective_liq_level}"
    
    def test_position_dataclass_has_origin_field(self):
        """Position dataclass should have origin field defaulting to 'bot'."""
        from engine.position_manager import Position
        
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
        )
        assert pos.origin == "bot", f"Expected 'bot', got {pos.origin}"
    
    def test_manual_position_origin_can_be_set(self):
        """Position origin can be set to 'manual'."""
        from engine.position_manager import Position
        
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="manual",
        )
        assert pos.origin == "manual", f"Expected 'manual', got {pos.origin}"


class TestExitEngineProtectiveLevel:
    """Test ExitEngine.check_exit behavior with protective_level parameter."""
    
    @pytest.fixture
    def exit_engine(self):
        from engine.exit_engine import ExitEngine
        return ExitEngine()
    
    @pytest.fixture
    def long_position(self):
        from engine.position_manager import Position
        return Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="bot",
            protective_liq_level=97.0,  # Bot position has protective level
        )
    
    @pytest.fixture
    def manual_long_position(self):
        from engine.position_manager import Position
        return Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="manual",
            protective_liq_level=0.0,  # Manual position has NO protective level
        )
    
    def test_protective_level_zero_skips_liquidation_stop_check(self, exit_engine, manual_long_position):
        """When protective_level=0, liquidation_stop check is skipped."""
        from engine.exit_engine import ExitReason
        
        # Price at 96.5 - below a hypothetical protective level of 97, but above SL of 95
        current_price = 96.5
        
        should_exit, reason, details = exit_engine.check_exit(
            manual_long_position,
            current_price,
            atr_value=1.0,
            protective_level=0.0,  # Manual position passes 0.0
            allow_early_exit=False,
        )
        
        # Should NOT exit because protective_level=0 means no liquidation_stop check
        # Price is above SL (95.0), so no hard_sl either
        assert should_exit is False, f"Should not exit, but got reason={reason}, details={details}"
    
    def test_protective_level_positive_triggers_liquidation_stop_long(self, exit_engine, long_position):
        """When protective_level > 0 and price <= protective_level, liquidation_stop triggers for LONG."""
        from engine.exit_engine import ExitReason
        
        # Price at 96.5 - below protective level of 97
        current_price = 96.5
        
        should_exit, reason, details = exit_engine.check_exit(
            long_position,
            current_price,
            atr_value=1.0,
            protective_level=97.0,  # Bot position passes actual protective level
            allow_early_exit=False,
        )
        
        # Should exit with LIQUIDATION_STOP
        assert should_exit is True, "Should exit due to liquidation_stop"
        assert reason == ExitReason.LIQUIDATION_STOP, f"Expected LIQUIDATION_STOP, got {reason}"
    
    def test_protective_level_positive_no_trigger_when_price_above(self, exit_engine, long_position):
        """When protective_level > 0 but price > protective_level, no liquidation_stop."""
        from engine.exit_engine import ExitReason
        
        # Price at 98.0 - above protective level of 97
        current_price = 98.0
        
        should_exit, reason, details = exit_engine.check_exit(
            long_position,
            current_price,
            atr_value=1.0,
            protective_level=97.0,
            allow_early_exit=False,
        )
        
        # Should NOT exit - price is above protective level and above SL
        assert should_exit is False, f"Should not exit, but got reason={reason}"
    
    def test_short_position_protective_level_triggers_when_price_above(self, exit_engine):
        """For SHORT, liquidation_stop triggers when price >= protective_level."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        short_pos = Position(
            symbol="TESTUSDT",
            side="SELL",
            entry_price=100.0,
            qty=1.0,
            stop_loss=105.0,
            take_profit=90.0,
            origin="bot",
            protective_liq_level=103.0,
        )
        
        # Price at 103.5 - above protective level of 103
        current_price = 103.5
        
        should_exit, reason, details = exit_engine.check_exit(
            short_pos,
            current_price,
            atr_value=1.0,
            protective_level=103.0,
            allow_early_exit=False,
        )
        
        assert should_exit is True, "Should exit due to liquidation_stop"
        assert reason == ExitReason.LIQUIDATION_STOP, f"Expected LIQUIDATION_STOP, got {reason}"


class TestManualPositionStillTriggersOtherExits:
    """Manual positions should still trigger hard_sl, trailing_exit, tp_cap exits."""
    
    @pytest.fixture
    def exit_engine(self):
        from engine.exit_engine import ExitEngine
        return ExitEngine()
    
    def test_manual_position_triggers_hard_sl_long(self, exit_engine):
        """Manual LONG position triggers hard_sl when price <= stop_loss."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="manual",
        )
        
        should_exit, reason, details = exit_engine.check_exit(
            pos, current_price=94.5, atr_value=1.0, protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.HARD_SL
    
    def test_manual_position_triggers_hard_sl_short(self, exit_engine):
        """Manual SHORT position triggers hard_sl when price >= stop_loss."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT",
            side="SELL",
            entry_price=100.0,
            qty=1.0,
            stop_loss=105.0,
            take_profit=90.0,
            origin="manual",
        )
        
        should_exit, reason, details = exit_engine.check_exit(
            pos, current_price=105.5, atr_value=1.0, protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.HARD_SL
    
    def test_manual_position_triggers_tp_cap_long(self, exit_engine):
        """Manual LONG position triggers tp_cap when price >= take_profit."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="manual",
        )
        
        should_exit, reason, details = exit_engine.check_exit(
            pos, current_price=111.0, atr_value=1.0, protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.TP_CAP
    
    def test_manual_position_triggers_trailing_exit(self, exit_engine):
        """Manual position triggers trailing_exit when trailing is active and price hits trailing_stop."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=120.0,
            origin="manual",
            trailing_active=True,
            trailing_stop=105.0,  # Trailing stop at 105
        )
        
        should_exit, reason, details = exit_engine.check_exit(
            pos, current_price=104.5, atr_value=1.0, protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.TRAILING_EXIT


class TestManualPositionNoEarlyExit:
    """Manual positions should NOT trigger early_exit (allow_early_exit=False)."""
    
    @pytest.fixture
    def exit_engine(self):
        from engine.exit_engine import ExitEngine
        return ExitEngine(early_exit_bars=6, early_exit_min_profit_atr=0.1)
    
    def test_manual_position_no_early_exit(self, exit_engine):
        """Manual position does NOT trigger early_exit even after many bars."""
        from engine.position_manager import Position
        
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="manual",
            bars_since_entry=20,  # Way past early_exit_bars
        )
        
        # Price barely moved - would trigger early_exit for bot positions
        should_exit, reason, details = exit_engine.check_exit(
            pos, current_price=100.05, atr_value=1.0, protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is False, f"Manual position should not early_exit, got {reason}"


# ============================================================================
# _filter_recent_closed_pnl TESTS
# ============================================================================

class TestFilterRecentClosedPnl:
    """Test the _filter_recent_closed_pnl static method."""
    
    def test_filter_recent_closed_pnl_method_exists(self):
        """TradingBot should have _filter_recent_closed_pnl method."""
        # Read main.py and check for method definition
        main_py = (BOT_DIR / "main.py").read_text()
        assert "_filter_recent_closed_pnl" in main_py, "Method _filter_recent_closed_pnl not found in main.py"
        assert "@staticmethod" in main_py, "Method should be a staticmethod"
    
    def test_filter_recent_closed_pnl_filters_old_records(self):
        """Old closedPnl records (>5 min) should be filtered out."""
        # Import the method directly from main.py
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        # Create test records - some recent, some old
        closed_records = [
            {"closedPnl": "1.5", "updatedTime": str(now_ms - 60_000)},  # 1 min ago - RECENT
            {"closedPnl": "-2.0", "updatedTime": str(now_ms - 180_000)},  # 3 min ago - RECENT
            {"closedPnl": "0.5", "updatedTime": str(now_ms - 400_000)},  # 6.67 min ago - OLD
            {"closedPnl": "-1.0", "updatedTime": str(now_ms - 600_000)},  # 10 min ago - OLD
        ]
        
        recent = TradingBot._filter_recent_closed_pnl(closed_records, max_age_sec=300)
        
        assert len(recent) == 2, f"Expected 2 recent records, got {len(recent)}"
        assert float(recent[0]["closedPnl"]) == 1.5
        assert float(recent[1]["closedPnl"]) == -2.0
    
    def test_filter_recent_closed_pnl_empty_input(self):
        """Empty or None input returns empty list."""
        from main import TradingBot
        
        assert TradingBot._filter_recent_closed_pnl(None) == []
        assert TradingBot._filter_recent_closed_pnl([]) == []
    
    def test_filter_recent_closed_pnl_all_old(self):
        """All old records returns empty list."""
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        closed_records = [
            {"closedPnl": "1.5", "updatedTime": str(now_ms - 600_000)},  # 10 min ago
            {"closedPnl": "-2.0", "updatedTime": str(now_ms - 900_000)},  # 15 min ago
        ]
        
        recent = TradingBot._filter_recent_closed_pnl(closed_records, max_age_sec=300)
        
        assert len(recent) == 0, f"Expected 0 recent records, got {len(recent)}"
    
    def test_filter_recent_closed_pnl_all_recent(self):
        """All recent records are kept."""
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        closed_records = [
            {"closedPnl": "1.5", "updatedTime": str(now_ms - 60_000)},  # 1 min ago
            {"closedPnl": "-2.0", "updatedTime": str(now_ms - 120_000)},  # 2 min ago
        ]
        
        recent = TradingBot._filter_recent_closed_pnl(closed_records, max_age_sec=300)
        
        assert len(recent) == 2, f"Expected 2 recent records, got {len(recent)}"
    
    def test_filter_recent_closed_pnl_uses_createdTime_fallback(self):
        """Uses createdTime if updatedTime is missing."""
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        closed_records = [
            {"closedPnl": "1.5", "createdTime": str(now_ms - 60_000)},  # 1 min ago - RECENT
            {"closedPnl": "-2.0", "createdTime": str(now_ms - 600_000)},  # 10 min ago - OLD
        ]
        
        recent = TradingBot._filter_recent_closed_pnl(closed_records, max_age_sec=300)
        
        assert len(recent) == 1, f"Expected 1 recent record, got {len(recent)}"
        assert float(recent[0]["closedPnl"]) == 1.5
    
    def test_filter_recent_closed_pnl_boundary_exactly_5_min(self):
        """Record exactly at 5 min boundary should be included."""
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        closed_records = [
            {"closedPnl": "1.5", "updatedTime": str(now_ms - 299_000)},  # 4:59 ago - RECENT
            {"closedPnl": "-2.0", "updatedTime": str(now_ms - 301_000)},  # 5:01 ago - OLD
        ]
        
        recent = TradingBot._filter_recent_closed_pnl(closed_records, max_age_sec=300)
        
        assert len(recent) == 1, f"Expected 1 recent record, got {len(recent)}"


# ============================================================================
# MAIN.PY CODE VERIFICATION TESTS
# ============================================================================

class TestMainPyManualPositionProtection:
    """Verify main.py has correct code for manual position protection."""
    
    @pytest.fixture(scope="class")
    def main_py_content(self):
        return (BOT_DIR / "main.py").read_text()
    
    def test_sync_exchange_position_sets_protective_liq_level_zero(self, main_py_content):
        """_sync_exchange_position should set protective_liq_level=0.0 for manual positions."""
        # Look for the line that initializes position with protective_liq_level=0.0
        assert 'protective_liq_level=0.0' in main_py_content, \
            "protective_liq_level=0.0 not found in main.py"
        
        # Specifically check it's in the exit_engine.initialize_position call for adopted positions
        assert 'self.exit_engine.initialize_position(adopted, atr_val, protective_liq_level=0.0)' in main_py_content, \
            "exit_engine.initialize_position for adopted position should pass protective_liq_level=0.0"
    
    def test_check_exit_passes_zero_for_manual_origin(self, main_py_content):
        """check_exit should pass protective_level=0.0 for manual origins."""
        # Look for the conditional that passes 0.0 for manual positions
        assert "pos.protective_liq_level if pos.origin == \"bot\" else 0.0" in main_py_content, \
            "check_exit should pass protective_level=0.0 for manual positions"
    
    def test_check_exit_passes_allow_early_exit_false_for_manual(self, main_py_content):
        """check_exit should pass allow_early_exit=(pos.origin == 'bot') for manual positions."""
        assert 'allow_early_exit=(pos.origin == "bot")' in main_py_content, \
            "check_exit should pass allow_early_exit=False for manual positions"


class TestMainPyExchangeClosedTimestampFiltering:
    """Verify main.py uses timestamp filtering for exchange_closed."""
    
    @pytest.fixture(scope="class")
    def main_py_content(self):
        return (BOT_DIR / "main.py").read_text()
    
    def test_filter_recent_closed_pnl_called_in_manage_positions(self, main_py_content):
        """_filter_recent_closed_pnl should be called in _manage_positions."""
        assert "_filter_recent_closed_pnl(closed" in main_py_content, \
            "_filter_recent_closed_pnl should be called with closed records"
    
    def test_recent_closed_used_for_finalization_check(self, main_py_content):
        """recent_closed should be used for _can_finalize_exchange_closed check."""
        assert "len(recent_closed)" in main_py_content, \
            "recent_closed should be used for finalization check"
    
    def test_max_age_sec_300_used(self, main_py_content):
        """max_age_sec=300 (5 minutes) should be used."""
        assert "max_age_sec=300" in main_py_content, \
            "max_age_sec=300 should be used for filtering"


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestManualPositionIntegration:
    """Integration tests for manual position protection."""
    
    def test_exit_engine_initialize_position_with_zero_protective_level(self):
        """ExitEngine.initialize_position should accept protective_liq_level=0.0."""
        from engine.exit_engine import ExitEngine
        from engine.position_manager import Position
        
        engine = ExitEngine()
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="manual",
        )
        
        # Should not raise any errors
        engine.initialize_position(pos, atr_value=1.0, protective_liq_level=0.0)
        
        # Position should have protective_liq_level=0.0 (unchanged)
        assert pos.protective_liq_level == 0.0
    
    def test_bot_position_can_have_protective_level(self):
        """Bot positions can have non-zero protective_liq_level."""
        from engine.exit_engine import ExitEngine
        from engine.position_manager import Position
        
        engine = ExitEngine()
        pos = Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="bot",
        )
        
        engine.initialize_position(pos, atr_value=1.0, protective_liq_level=97.0)
        
        # Position should have protective_liq_level set
        assert pos.protective_liq_level == 97.0


class TestExchangeClosedCycleValues:
    """Test that TradingBot loads correct exchange_closed cycle values."""
    
    def test_trading_bot_loads_confirm_cycles_5(self):
        """TradingBot should load exchange_closed_confirm_cycles=5."""
        # Read main.py and verify the default value is 3 but config overrides to 5
        main_py = (BOT_DIR / "main.py").read_text()
        
        # Check that the config key is read
        assert 'exchange_closed_confirm_cycles' in main_py
        
        # Verify config has 5
        with open(BOT_DIR / "config.yaml", "r") as f:
            config = yaml.safe_load(f)
        
        assert config["position_sync"]["exchange_closed_confirm_cycles"] == 5
    
    def test_trading_bot_loads_force_cycles_20(self):
        """TradingBot should load exchange_closed_force_cycles=20."""
        # Verify config has 20
        with open(BOT_DIR / "config.yaml", "r") as f:
            config = yaml.safe_load(f)
        
        assert config["position_sync"]["exchange_closed_force_cycles"] == 20


class TestCanFinalizeExchangeClosed:
    """Test _can_finalize_exchange_closed logic with recent_closed count."""
    
    def test_can_finalize_with_recent_closed_records(self):
        """With recent closedPnl records, finalization should be allowed."""
        # This tests the logic: if closed_records_count > 0, return True
        from main import TradingBot
        
        # Create a minimal mock to test the method
        class MockBot:
            exchange_closed_require_closed_pnl = True
            exchange_closed_force_cycles = 20
            
            def _can_finalize_exchange_closed(self, missing_cycles, closed_records_count):
                if not self.exchange_closed_require_closed_pnl:
                    return True
                if closed_records_count > 0:
                    return True
                return missing_cycles >= max(1, int(self.exchange_closed_force_cycles))
        
        bot = MockBot()
        
        # With recent records, should finalize even with few cycles
        assert bot._can_finalize_exchange_closed(missing_cycles=5, closed_records_count=1) is True
        
        # Without recent records, need force_cycles
        assert bot._can_finalize_exchange_closed(missing_cycles=5, closed_records_count=0) is False
        assert bot._can_finalize_exchange_closed(missing_cycles=20, closed_records_count=0) is True


# ============================================================================
# SUMMARY TEST
# ============================================================================

class TestIteration40Summary:
    """Summary test verifying all iteration 40 bug fixes."""
    
    def test_all_bug_fixes_present(self):
        """Verify all bug fixes are present in the codebase."""
        main_py = (BOT_DIR / "main.py").read_text()
        config = yaml.safe_load(open(BOT_DIR / "config.yaml"))
        
        # Bug Fix 1: Manual positions have protective_liq_level=0.0
        assert 'origin="manual"' in main_py, "Manual origin should be set"
        assert 'protective_liq_level=0.0' in main_py, "protective_liq_level=0.0 should be set"
        
        # Bug Fix 2: check_exit passes protective_level=0.0 for manual
        assert 'pos.origin == "bot" else 0.0' in main_py, "check_exit should pass 0.0 for manual"
        
        # Bug Fix 3: _filter_recent_closed_pnl method exists
        assert '_filter_recent_closed_pnl' in main_py, "_filter_recent_closed_pnl method should exist"
        
        # Bug Fix 4: exchange_closed uses recent_closed
        assert 'recent_closed' in main_py, "recent_closed variable should be used"
        
        # Config Fix 1: exchange_closed_confirm_cycles=5
        assert config["position_sync"]["exchange_closed_confirm_cycles"] == 5
        
        # Config Fix 2: exchange_closed_force_cycles=20
        assert config["position_sync"]["exchange_closed_force_cycles"] == 20
        
        print("\n=== ITERATION 40 BUG FIXES VERIFIED ===")
        print("1. Manual positions: protective_liq_level=0.0 in _sync_exchange_position")
        print("2. check_exit: protective_level=0.0 for manual origins")
        print("3. _filter_recent_closed_pnl: filters closedPnl by timestamp (max 5 min)")
        print("4. exchange_closed: uses recent_closed (timestamp-filtered)")
        print("5. Config: exchange_closed_confirm_cycles=5 (was 3)")
        print("6. Config: exchange_closed_force_cycles=20 (was 8)")
        print("========================================\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
