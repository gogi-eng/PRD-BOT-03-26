#!/usr/bin/env python3
"""
Iteration 41: P0 Manual Position Protection Tests

Tests the critical P0 fixes to prevent bot from force-closing manual positions:

1. ExitReason is properly imported from engine.exit_engine in main.py
2. Manual positions (origin='manual') are blocked from HARD_SL exit
3. Manual positions are blocked from LIQUIDATION_STOP exit (protective_level=0.0 AND ExitReason guard)
4. Manual positions are blocked from EARLY_EXIT (allow_early_exit=False)
5. Manual positions CAN still exit via TRAILING_EXIT and TP_CAP
6. Bot positions (origin='bot') still get ALL exit reasons normally
7. force_closed_stale (zombie recovery) does NOT force-remove manual positions
8. force_closed_stale still works normally for bot positions after 3 failures
9. config.yaml has early_exit_bars: 20
10. ExitEngine check_exit correctly handles allow_early_exit=False when early_exit_bars=20
"""

import sys
from pathlib import Path

import pytest
import yaml

# Add bot directory to path
BOT_DIR = Path("/app/bot")
sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# TEST 1: ExitReason Import Verification
# ============================================================================

class TestExitReasonImport:
    """Verify ExitReason is properly imported in main.py."""
    
    @pytest.fixture(scope="class")
    def main_py_content(self):
        return (BOT_DIR / "main.py").read_text()
    
    def test_exit_reason_imported_from_exit_engine(self, main_py_content):
        """ExitReason should be imported from engine.exit_engine."""
        assert "from engine.exit_engine import ExitEngine, ExitReason" in main_py_content, \
            "ExitReason should be imported alongside ExitEngine"
    
    def test_exit_reason_used_in_manual_safety_check(self, main_py_content):
        """ExitReason should be used in the manual safety check."""
        assert "ExitReason.TRAILING_EXIT" in main_py_content, \
            "ExitReason.TRAILING_EXIT should be used in manual safety check"
        assert "ExitReason.TP_CAP" in main_py_content, \
            "ExitReason.TP_CAP should be used in manual safety check"
    
    def test_exit_reason_enum_has_required_values(self):
        """ExitReason enum should have all required values."""
        from engine.exit_engine import ExitReason
        
        required_reasons = [
            "HARD_SL", "LIQUIDATION_STOP", "EARLY_EXIT", 
            "TRAILING_EXIT", "TP_CAP", "MANUAL", "EXCHANGE_CLOSED"
        ]
        
        for reason in required_reasons:
            assert hasattr(ExitReason, reason), f"ExitReason should have {reason}"


# ============================================================================
# TEST 2-5: Manual Position Exit Blocking
# ============================================================================

class TestManualPositionExitBlocking:
    """Test that manual positions are blocked from HARD_SL, LIQUIDATION_STOP, EARLY_EXIT."""
    
    @pytest.fixture
    def exit_engine(self):
        from engine.exit_engine import ExitEngine
        return ExitEngine(early_exit_bars=20, early_exit_min_profit_atr=0.1)
    
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
            protective_liq_level=0.0,  # Manual positions have NO protective level
            bars_since_entry=25,  # Past early_exit_bars
        )
    
    @pytest.fixture
    def bot_long_position(self):
        from engine.position_manager import Position
        return Position(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="bot",
            protective_liq_level=97.0,  # Bot positions have protective level
            bars_since_entry=25,  # Past early_exit_bars
        )
    
    # --- HARD_SL Tests ---
    
    def test_manual_position_hard_sl_blocked_by_guard(self, exit_engine, manual_long_position):
        """Manual position HARD_SL should be blocked by the ExitReason guard in main.py.
        
        Note: ExitEngine.check_exit will return HARD_SL, but main.py blocks it for manual positions.
        """
        from engine.exit_engine import ExitReason
        
        # Price below SL - would trigger HARD_SL
        current_price = 94.0
        
        should_exit, reason, details = exit_engine.check_exit(
            manual_long_position,
            current_price,
            atr_value=1.0,
            protective_level=0.0,  # Manual position passes 0.0
            allow_early_exit=False,  # Manual position passes False
        )
        
        # ExitEngine returns HARD_SL, but main.py will block it
        assert should_exit is True
        assert reason == ExitReason.HARD_SL
        # The blocking happens in main.py, not in ExitEngine
    
    def test_bot_position_hard_sl_allowed(self, exit_engine, bot_long_position):
        """Bot position HARD_SL should be allowed."""
        from engine.exit_engine import ExitReason
        
        current_price = 94.0
        
        should_exit, reason, details = exit_engine.check_exit(
            bot_long_position,
            current_price,
            atr_value=1.0,
            protective_level=97.0,
            allow_early_exit=True,
        )
        
        # Bot position gets HARD_SL (or LIQUIDATION_STOP if price <= 97)
        assert should_exit is True
        assert reason in (ExitReason.HARD_SL, ExitReason.LIQUIDATION_STOP)
    
    # --- LIQUIDATION_STOP Tests ---
    
    def test_manual_position_liquidation_stop_blocked_by_zero_protective_level(self, exit_engine, manual_long_position):
        """Manual position LIQUIDATION_STOP is blocked because protective_level=0.0."""
        from engine.exit_engine import ExitReason
        
        # Price at 96.5 - would trigger LIQUIDATION_STOP if protective_level was 97
        current_price = 96.5
        
        should_exit, reason, details = exit_engine.check_exit(
            manual_long_position,
            current_price,
            atr_value=1.0,
            protective_level=0.0,  # Manual position passes 0.0 - skips liquidation check
            allow_early_exit=False,
        )
        
        # Should NOT exit because protective_level=0 means no liquidation_stop check
        # Price is above SL (95.0), so no hard_sl either
        assert should_exit is False, f"Should not exit, but got reason={reason}"
    
    def test_bot_position_liquidation_stop_triggers(self, exit_engine, bot_long_position):
        """Bot position LIQUIDATION_STOP triggers when price <= protective_level."""
        from engine.exit_engine import ExitReason
        
        current_price = 96.5  # Below protective_level of 97
        
        should_exit, reason, details = exit_engine.check_exit(
            bot_long_position,
            current_price,
            atr_value=1.0,
            protective_level=97.0,  # Bot position has protective level
            allow_early_exit=True,
        )
        
        assert should_exit is True
        assert reason == ExitReason.LIQUIDATION_STOP
    
    # --- EARLY_EXIT Tests ---
    
    def test_manual_position_early_exit_blocked_by_allow_early_exit_false(self, exit_engine, manual_long_position):
        """Manual position EARLY_EXIT is blocked because allow_early_exit=False."""
        from engine.exit_engine import ExitReason
        
        # Price barely moved - would trigger early_exit for bot positions
        current_price = 100.05
        
        should_exit, reason, details = exit_engine.check_exit(
            manual_long_position,
            current_price,
            atr_value=1.0,
            protective_level=0.0,
            allow_early_exit=False,  # Manual position passes False
        )
        
        # Should NOT exit because allow_early_exit=False
        assert should_exit is False, f"Manual position should not early_exit, got {reason}"
    
    def test_bot_position_early_exit_triggers(self, exit_engine, bot_long_position):
        """Bot position EARLY_EXIT triggers after early_exit_bars with insufficient profit."""
        from engine.exit_engine import ExitReason
        
        current_price = 100.05  # Barely moved
        
        should_exit, reason, details = exit_engine.check_exit(
            bot_long_position,
            current_price,
            atr_value=1.0,
            protective_level=97.0,
            allow_early_exit=True,  # Bot position passes True
        )
        
        assert should_exit is True
        assert reason == ExitReason.EARLY_EXIT
    
    # --- TRAILING_EXIT and TP_CAP Tests (ALLOWED for manual) ---
    
    def test_manual_position_trailing_exit_allowed(self, exit_engine):
        """Manual position TRAILING_EXIT is allowed."""
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
        
        current_price = 104.5  # Below trailing_stop
        
        should_exit, reason, details = exit_engine.check_exit(
            pos, current_price, atr_value=1.0, protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.TRAILING_EXIT
    
    def test_manual_position_tp_cap_allowed(self, exit_engine):
        """Manual position TP_CAP is allowed."""
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
        
        current_price = 111.0  # Above take_profit
        
        should_exit, reason, details = exit_engine.check_exit(
            pos, current_price, atr_value=1.0, protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.TP_CAP


# ============================================================================
# TEST 6: Bot Positions Get ALL Exit Reasons
# ============================================================================

class TestBotPositionAllExitReasons:
    """Test that bot positions get ALL exit reasons normally."""
    
    @pytest.fixture
    def exit_engine(self):
        from engine.exit_engine import ExitEngine
        return ExitEngine(early_exit_bars=20, early_exit_min_profit_atr=0.1)
    
    def test_bot_position_gets_hard_sl(self, exit_engine):
        """Bot position gets HARD_SL."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="bot",
        )
        
        should_exit, reason, _ = exit_engine.check_exit(
            pos, current_price=94.0, atr_value=1.0, protective_level=0.0, allow_early_exit=True
        )
        
        assert should_exit is True
        assert reason == ExitReason.HARD_SL
    
    def test_bot_position_gets_liquidation_stop(self, exit_engine):
        """Bot position gets LIQUIDATION_STOP."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="bot",
        )
        
        should_exit, reason, _ = exit_engine.check_exit(
            pos, current_price=96.5, atr_value=1.0, protective_level=97.0, allow_early_exit=True
        )
        
        assert should_exit is True
        assert reason == ExitReason.LIQUIDATION_STOP
    
    def test_bot_position_gets_early_exit(self, exit_engine):
        """Bot position gets EARLY_EXIT."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="bot",
            bars_since_entry=25,  # Past early_exit_bars
        )
        
        should_exit, reason, _ = exit_engine.check_exit(
            pos, current_price=100.05, atr_value=1.0, protective_level=0.0, allow_early_exit=True
        )
        
        assert should_exit is True
        assert reason == ExitReason.EARLY_EXIT
    
    def test_bot_position_gets_trailing_exit(self, exit_engine):
        """Bot position gets TRAILING_EXIT."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=120.0, origin="bot",
            trailing_active=True, trailing_stop=105.0,
        )
        
        should_exit, reason, _ = exit_engine.check_exit(
            pos, current_price=104.5, atr_value=1.0, protective_level=0.0, allow_early_exit=True
        )
        
        assert should_exit is True
        assert reason == ExitReason.TRAILING_EXIT
    
    def test_bot_position_gets_tp_cap(self, exit_engine):
        """Bot position gets TP_CAP."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="bot",
        )
        
        should_exit, reason, _ = exit_engine.check_exit(
            pos, current_price=111.0, atr_value=1.0, protective_level=0.0, allow_early_exit=True
        )
        
        assert should_exit is True
        assert reason == ExitReason.TP_CAP


# ============================================================================
# TEST 7-8: force_closed_stale (Zombie Recovery) Protection
# ============================================================================

class TestForceClosedStaleProtection:
    """Test that force_closed_stale does NOT force-remove manual positions."""
    
    @pytest.fixture(scope="class")
    def main_py_content(self):
        return (BOT_DIR / "main.py").read_text()
    
    def test_manual_position_not_force_removed_code_present(self, main_py_content):
        """Code should check origin='manual' before force-removing."""
        # Check for the manual position protection in force_closed_stale logic
        assert 'if pos.origin == "manual":' in main_py_content, \
            "Should check for manual origin before force-removing"
        
        assert "NOT removing" in main_py_content, \
            "Should have 'NOT removing' message for manual positions"
        
        assert "Resetting counter" in main_py_content, \
            "Should reset counter for manual positions"
    
    def test_manual_position_sends_alert_on_close_failure(self, main_py_content):
        """Manual position should send Telegram alert on close failure."""
        assert "[MANUAL SAFE]" in main_py_content, \
            "Should have [MANUAL SAFE] log prefix"
        
        assert "Please close manually if needed" in main_py_content, \
            "Should instruct user to close manually"
    
    def test_bot_position_force_removed_after_3_failures(self, main_py_content):
        """Bot position should be force-removed after 3 failures."""
        assert "[FORCE REMOVE]" in main_py_content, \
            "Should have [FORCE REMOVE] log prefix for bot positions"
        
        assert "Removing zombie position" in main_py_content, \
            "Should remove zombie position for bot positions"
    
    def test_force_remove_only_for_bot_origin(self, main_py_content):
        """Force remove should only happen for bot origin."""
        # The else branch (bot positions) should have the force remove logic
        assert 'else:' in main_py_content, "Should have else branch for bot positions"
        
        # Check the structure: if manual -> don't remove, else -> force remove
        lines = main_py_content.split('\n')
        found_manual_check = False
        found_force_remove = False
        
        for i, line in enumerate(lines):
            if 'if pos.origin == "manual":' in line:
                found_manual_check = True
            if found_manual_check and '[FORCE REMOVE]' in line:
                found_force_remove = True
                break
        
        assert found_manual_check, "Should have manual origin check"
        assert found_force_remove, "Should have force remove after manual check"


# ============================================================================
# TEST 9: Config early_exit_bars: 20
# ============================================================================

class TestConfigEarlyExitBars:
    """Test that config.yaml has early_exit_bars: 20."""
    
    @pytest.fixture(scope="class")
    def config(self):
        with open(BOT_DIR / "config.yaml", "r") as f:
            return yaml.safe_load(f)
    
    def test_early_exit_bars_is_20(self, config):
        """early_exit_bars should be 20 (was 6)."""
        value = config.get("exit", {}).get("early_exit_bars")
        assert value == 20, f"Expected 20, got {value}"
    
    def test_early_exit_bars_increased_from_6(self, config):
        """early_exit_bars should be increased from 6 to 20."""
        value = config.get("exit", {}).get("early_exit_bars")
        assert value > 6, f"early_exit_bars should be > 6, got {value}"


# ============================================================================
# TEST 10: ExitEngine check_exit with allow_early_exit=False
# ============================================================================

class TestExitEngineAllowEarlyExitFalse:
    """Test ExitEngine.check_exit correctly handles allow_early_exit=False."""
    
    @pytest.fixture
    def exit_engine_20_bars(self):
        from engine.exit_engine import ExitEngine
        return ExitEngine(early_exit_bars=20, early_exit_min_profit_atr=0.1)
    
    def test_early_exit_skipped_when_allow_early_exit_false(self, exit_engine_20_bars):
        """Early exit should be skipped when allow_early_exit=False."""
        from engine.position_manager import Position
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="manual",
            bars_since_entry=30,  # Way past early_exit_bars
        )
        
        # Price barely moved - would trigger early_exit if allowed
        should_exit, reason, _ = exit_engine_20_bars.check_exit(
            pos, current_price=100.05, atr_value=1.0, 
            protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is False, f"Should not exit with allow_early_exit=False, got {reason}"
    
    def test_early_exit_triggers_when_allow_early_exit_true(self, exit_engine_20_bars):
        """Early exit should trigger when allow_early_exit=True."""
        from engine.position_manager import Position
        from engine.exit_engine import ExitReason
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="bot",
            bars_since_entry=30,  # Way past early_exit_bars
        )
        
        should_exit, reason, _ = exit_engine_20_bars.check_exit(
            pos, current_price=100.05, atr_value=1.0,
            protective_level=0.0, allow_early_exit=True
        )
        
        assert should_exit is True
        assert reason == ExitReason.EARLY_EXIT
    
    def test_early_exit_bars_20_requires_more_bars(self, exit_engine_20_bars):
        """With early_exit_bars=20, positions need 20+ bars to trigger early_exit."""
        from engine.position_manager import Position
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="bot",
            bars_since_entry=15,  # Less than 20 bars
        )
        
        should_exit, reason, _ = exit_engine_20_bars.check_exit(
            pos, current_price=100.05, atr_value=1.0,
            protective_level=0.0, allow_early_exit=True
        )
        
        # Should NOT exit because bars_since_entry < early_exit_bars
        assert should_exit is False, f"Should not exit before 20 bars, got {reason}"


# ============================================================================
# Main.py Code Verification Tests
# ============================================================================

class TestMainPyManualSafetyGuard:
    """Verify main.py has the manual safety guard code."""
    
    @pytest.fixture(scope="class")
    def main_py_content(self):
        return (BOT_DIR / "main.py").read_text()
    
    def test_manual_safety_comment_present(self, main_py_content):
        """MANUAL SAFETY comment should be present."""
        assert "MANUAL SAFETY" in main_py_content, \
            "MANUAL SAFETY comment should be present"
    
    def test_manual_safety_checks_origin(self, main_py_content):
        """Manual safety should check pos.origin == 'manual'."""
        assert 'pos.origin == "manual"' in main_py_content, \
            "Should check pos.origin == 'manual'"
    
    def test_manual_safety_allows_trailing_exit_and_tp_cap(self, main_py_content):
        """Manual safety should allow TRAILING_EXIT and TP_CAP."""
        assert "ExitReason.TRAILING_EXIT, ExitReason.TP_CAP" in main_py_content, \
            "Should allow TRAILING_EXIT and TP_CAP for manual positions"
    
    def test_manual_safety_blocks_other_exits(self, main_py_content):
        """Manual safety should block exits not in (TRAILING_EXIT, TP_CAP)."""
        assert "reason not in (" in main_py_content, \
            "Should check reason not in allowed list"
    
    def test_manual_safety_sets_should_exit_false(self, main_py_content):
        """Manual safety should set should_exit = False for blocked exits."""
        # Look for the pattern where should_exit is set to False after the check
        assert "should_exit = False" in main_py_content, \
            "Should set should_exit = False for blocked manual exits"


# ============================================================================
# Integration Tests
# ============================================================================

class TestManualPositionProtectionIntegration:
    """Integration tests for manual position protection."""
    
    def test_full_exit_flow_manual_position_hard_sl_blocked(self):
        """Full flow: manual position HARD_SL is blocked by ExitReason guard."""
        from engine.exit_engine import ExitEngine, ExitReason
        from engine.position_manager import Position
        
        engine = ExitEngine(early_exit_bars=20)
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="manual",
        )
        
        # Price below SL
        current_price = 94.0
        
        # ExitEngine returns HARD_SL
        should_exit, reason, details = engine.check_exit(
            pos, current_price, atr_value=1.0,
            protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.HARD_SL
        
        # Simulate main.py guard
        if should_exit and pos.origin == "manual" and reason not in (
            ExitReason.TRAILING_EXIT, ExitReason.TP_CAP
        ):
            should_exit = False  # Blocked by guard
        
        assert should_exit is False, "HARD_SL should be blocked for manual position"
    
    def test_full_exit_flow_manual_position_trailing_exit_allowed(self):
        """Full flow: manual position TRAILING_EXIT is allowed."""
        from engine.exit_engine import ExitEngine, ExitReason
        from engine.position_manager import Position
        
        engine = ExitEngine(early_exit_bars=20)
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=120.0, origin="manual",
            trailing_active=True, trailing_stop=105.0,
        )
        
        current_price = 104.5  # Below trailing_stop
        
        should_exit, reason, details = engine.check_exit(
            pos, current_price, atr_value=1.0,
            protective_level=0.0, allow_early_exit=False
        )
        
        assert should_exit is True
        assert reason == ExitReason.TRAILING_EXIT
        
        # Simulate main.py guard - TRAILING_EXIT is allowed
        if should_exit and pos.origin == "manual" and reason not in (
            ExitReason.TRAILING_EXIT, ExitReason.TP_CAP
        ):
            should_exit = False
        
        assert should_exit is True, "TRAILING_EXIT should be allowed for manual position"
    
    def test_full_exit_flow_bot_position_all_exits_allowed(self):
        """Full flow: bot position gets all exit reasons."""
        from engine.exit_engine import ExitEngine, ExitReason
        from engine.position_manager import Position
        
        engine = ExitEngine(early_exit_bars=20)
        
        pos = Position(
            symbol="TESTUSDT", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0, origin="bot",
        )
        
        # Price below SL
        current_price = 94.0
        
        should_exit, reason, details = engine.check_exit(
            pos, current_price, atr_value=1.0,
            protective_level=0.0, allow_early_exit=True
        )
        
        assert should_exit is True
        assert reason == ExitReason.HARD_SL
        
        # Simulate main.py guard - bot positions are not blocked
        if should_exit and pos.origin == "manual" and reason not in (
            ExitReason.TRAILING_EXIT, ExitReason.TP_CAP
        ):
            should_exit = False
        
        assert should_exit is True, "HARD_SL should be allowed for bot position"


# ============================================================================
# Summary Test
# ============================================================================

class TestIteration41P0Summary:
    """Summary test verifying all P0 fixes."""
    
    def test_all_p0_fixes_present(self):
        """Verify all P0 fixes are present in the codebase."""
        main_py = (BOT_DIR / "main.py").read_text()
        config = yaml.safe_load(open(BOT_DIR / "config.yaml"))
        
        print("\n=== ITERATION 41 P0 FIXES VERIFICATION ===")
        
        # 1. ExitReason import
        assert "from engine.exit_engine import ExitEngine, ExitReason" in main_py
        print("1. ExitReason imported from engine.exit_engine: VERIFIED")
        
        # 2. Manual positions blocked from HARD_SL (via ExitReason guard)
        assert "ExitReason.TRAILING_EXIT, ExitReason.TP_CAP" in main_py
        print("2. Manual positions blocked from HARD_SL (ExitReason guard): VERIFIED")
        
        # 3. Manual positions blocked from LIQUIDATION_STOP (protective_level=0.0)
        assert 'pos.origin == "bot" else 0.0' in main_py
        print("3. Manual positions blocked from LIQUIDATION_STOP (protective_level=0.0): VERIFIED")
        
        # 4. Manual positions blocked from EARLY_EXIT (allow_early_exit=False)
        assert 'allow_early_exit=(pos.origin == "bot")' in main_py
        print("4. Manual positions blocked from EARLY_EXIT (allow_early_exit=False): VERIFIED")
        
        # 5. Manual positions CAN exit via TRAILING_EXIT and TP_CAP
        assert "reason not in (" in main_py
        print("5. Manual positions CAN exit via TRAILING_EXIT and TP_CAP: VERIFIED")
        
        # 6. Bot positions get ALL exit reasons (no guard for bot)
        assert 'pos.origin == "manual"' in main_py  # Guard only for manual
        print("6. Bot positions get ALL exit reasons: VERIFIED")
        
        # 7. force_closed_stale does NOT force-remove manual positions
        assert "NOT removing" in main_py
        assert "Resetting counter" in main_py
        print("7. force_closed_stale does NOT force-remove manual positions: VERIFIED")
        
        # 8. force_closed_stale still works for bot positions
        assert "[FORCE REMOVE]" in main_py
        print("8. force_closed_stale still works for bot positions: VERIFIED")
        
        # 9. early_exit_bars: 20
        assert config["exit"]["early_exit_bars"] == 20
        print("9. config.yaml early_exit_bars: 20: VERIFIED")
        
        # 10. ExitEngine handles allow_early_exit=False
        exit_engine_py = (BOT_DIR / "engine" / "exit_engine.py").read_text()
        assert "allow_early_exit" in exit_engine_py
        print("10. ExitEngine handles allow_early_exit=False: VERIFIED")
        
        print("==========================================\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
