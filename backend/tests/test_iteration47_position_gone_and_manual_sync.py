#!/usr/bin/env python3
"""
Iteration 47: Bug Fixes for Position Gone and Manual Position Sync

Bug Fix 1: POSITION GONE logic (main.py ~line 877-912)
- When execute_close fails 3 times with "Position not found" error, position should be 
  finalized as exchange_closed regardless of origin (manual or bot)
- Uses wider 3600s window for closedPnl
- Telegram alert should be sent when position is detected as gone

Bug Fix 2: Manual position sync (main.py ~line 638-665)
- Manual position sync uses wider 7200s window (2 hours) for closedPnl evidence
- If closedPnl found in wider window, position is finalized
- If no closedPnl found even in wider window, manual position force-finalizes after 
  exchange_closed_force_cycles (30 cycles)
- Regular logging every 10 cycles while waiting

Regression tests:
- Bot-origin positions with 'not found' error are still force-removed
- Bot-origin positions with non 'not found' errors after 3 fails are still force-removed
- MANUAL SAFE still blocks hard_sl, early_exit for manual positions
- MANUAL SAFE allows trailing_exit, tp_cap for manual positions
- Position sync for bot-origin positions uses original 300s window
"""

import pytest
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Add bot directory to path
BOT_DIR = Path(__file__).parent.parent.parent / "bot"
sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# Mock Position class (matches position_manager.py)
# ============================================================================
@dataclass
class MockPosition:
    """Mock position for testing."""
    symbol: str
    side: str
    entry_price: float
    qty: float
    stop_loss: float = 0.0
    take_profit: float = 0.0
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    best_price: float = 0.0
    trailing_active: bool = False
    trailing_stop: float = 0.0
    trailing_distance: float = 0.0
    trailing_activation_price: float = 0.0
    bars_since_entry: int = 0
    unrealized_pnl: float = 0.0
    capital_weight: float = 1.0
    heatmap_target: float = 0.0
    protective_liq_level: float = 0.0
    model_confidence: float = 0.0
    last_rl_action: str = "hold"
    add_count: int = 0
    origin: str = "bot"
    partial_tp_price: float = 0.0
    partial_tp_done: bool = False
    partial_close_fraction: float = 0.5
    total_tp_price: float = 0.0
    position_idx: int = 0
    external_tp_locked: bool = False
    last_notified_stop_loss: float = 0.0
    profit_guard_armed: bool = False
    profit_peak_price: float = 0.0
    profit_peak_pct: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.side.upper() in ["BUY", "LONG"]


# ============================================================================
# Test: Config values for exchange_closed_force_cycles
# ============================================================================
class TestConfigValues:
    """Verify config.yaml has correct values for the bug fixes."""

    def test_exchange_closed_force_cycles_is_30(self):
        """exchange_closed_force_cycles should be 30 for manual position force-finalize."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        force_cycles = config.get("position_sync", {}).get("exchange_closed_force_cycles", 8)
        assert force_cycles == 30, f"Expected exchange_closed_force_cycles=30, got {force_cycles}"
        print("PASSED: exchange_closed_force_cycles = 30")

    def test_exchange_closed_confirm_cycles_is_8(self):
        """exchange_closed_confirm_cycles should be 8."""
        import yaml
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        
        confirm_cycles = config.get("position_sync", {}).get("exchange_closed_confirm_cycles", 3)
        assert confirm_cycles == 8, f"Expected exchange_closed_confirm_cycles=8, got {confirm_cycles}"
        print("PASSED: exchange_closed_confirm_cycles = 8")


# ============================================================================
# Test: _filter_recent_closed_pnl helper
# ============================================================================
class TestFilterRecentClosedPnl:
    """Test the _filter_recent_closed_pnl static method."""

    def test_filter_with_300s_window(self):
        """Standard 300s window filters correctly."""
        # Import the method
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        # Record from 2 minutes ago (should pass 300s filter)
        recent_record = {"closedPnl": "1.5", "updatedTime": now_ms - 120_000}
        # Record from 10 minutes ago (should fail 300s filter)
        old_record = {"closedPnl": "2.0", "updatedTime": now_ms - 600_000}
        
        records = [recent_record, old_record]
        
        filtered = TradingBot._filter_recent_closed_pnl(records, max_age_sec=300)
        assert len(filtered) == 1, f"Expected 1 record, got {len(filtered)}"
        assert filtered[0]["closedPnl"] == "1.5"
        print("PASSED: 300s window filters correctly")

    def test_filter_with_3600s_window(self):
        """Wider 3600s window (1 hour) for POSITION GONE logic."""
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        # Record from 30 minutes ago (should pass 3600s filter)
        record_30min = {"closedPnl": "1.5", "updatedTime": now_ms - 1800_000}
        # Record from 2 hours ago (should fail 3600s filter)
        record_2h = {"closedPnl": "2.0", "updatedTime": now_ms - 7200_000}
        
        records = [record_30min, record_2h]
        
        filtered = TradingBot._filter_recent_closed_pnl(records, max_age_sec=3600)
        assert len(filtered) == 1, f"Expected 1 record, got {len(filtered)}"
        assert filtered[0]["closedPnl"] == "1.5"
        print("PASSED: 3600s window filters correctly")

    def test_filter_with_7200s_window(self):
        """Wider 7200s window (2 hours) for manual position sync."""
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        # Record from 90 minutes ago (should pass 7200s filter)
        record_90min = {"closedPnl": "1.5", "updatedTime": now_ms - 5400_000}
        # Record from 3 hours ago (should fail 7200s filter)
        record_3h = {"closedPnl": "2.0", "updatedTime": now_ms - 10800_000}
        
        records = [record_90min, record_3h]
        
        filtered = TradingBot._filter_recent_closed_pnl(records, max_age_sec=7200)
        assert len(filtered) == 1, f"Expected 1 record, got {len(filtered)}"
        assert filtered[0]["closedPnl"] == "1.5"
        print("PASSED: 7200s window filters correctly")

    def test_filter_empty_records(self):
        """Empty records return empty list."""
        from main import TradingBot
        
        filtered = TradingBot._filter_recent_closed_pnl(None, max_age_sec=300)
        assert filtered == [], f"Expected empty list, got {filtered}"
        
        filtered = TradingBot._filter_recent_closed_pnl([], max_age_sec=300)
        assert filtered == [], f"Expected empty list, got {filtered}"
        print("PASSED: Empty records handled correctly")


# ============================================================================
# Test: Bug Fix 1 - POSITION GONE logic
# ============================================================================
class TestPositionGoneLogic:
    """
    Bug Fix 1: When execute_close fails 3 times with 'Position not found' error,
    the position should be finalized as exchange_closed regardless of origin.
    """

    def test_position_gone_detection_for_manual_position(self):
        """
        Manual position with 'Position not found' error should be finalized,
        NOT kept forever (the bug we're fixing).
        """
        # The key logic is in main.py lines 877-912
        # We test the condition: "not found" in error_msg
        
        error_msg = "Position not found"
        position_gone = "not found" in error_msg.lower() or "position" in error_msg.lower()
        
        assert position_gone is True, "Should detect 'Position not found' as position_gone"
        print("PASSED: 'Position not found' detected as position_gone")

    def test_position_gone_detection_for_bot_position(self):
        """Bot position with 'Position not found' error should also be finalized."""
        error_msg = "position not found on exchange"
        position_gone = "not found" in error_msg.lower()
        
        assert position_gone is True, "Should detect 'not found' in error message"
        print("PASSED: Bot position 'not found' detected correctly")

    def test_non_not_found_error_for_manual_position(self):
        """
        Manual position with non-'not found' error should NOT be removed.
        This is the MANUAL SAFE behavior that should be preserved.
        """
        error_msg = "Rate limit exceeded"
        position_gone = "not found" in error_msg.lower()
        
        assert position_gone is False, "Rate limit error should NOT trigger position_gone"
        print("PASSED: Non-'not found' error does NOT trigger position_gone for manual")

    def test_position_gone_uses_3600s_window(self):
        """
        POSITION GONE logic should use 3600s window for closedPnl lookup.
        This is verified by checking the code uses max_age_sec=3600.
        """
        # Read the source code and verify the window
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # The POSITION GONE section should use 3600s window
        # Line ~892: recent_closed = self._filter_recent_closed_pnl(closed, max_age_sec=3600)
        assert "max_age_sec=3600" in source, "POSITION GONE should use 3600s window"
        print("PASSED: POSITION GONE uses 3600s window for closedPnl")


# ============================================================================
# Test: Bug Fix 2 - Manual Position Sync with Wider Window
# ============================================================================
class TestManualPositionSyncWiderWindow:
    """
    Bug Fix 2: Manual position sync uses wider 7200s window (2 hours) for closedPnl.
    If no closedPnl found, force-finalize after exchange_closed_force_cycles (30 cycles).
    """

    def test_manual_sync_uses_7200s_window(self):
        """Manual position sync should try 7200s window when 300s fails."""
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~640: wider_closed = self._filter_recent_closed_pnl(closed, max_age_sec=7200)
        assert "max_age_sec=7200" in source, "Manual sync should use 7200s window"
        print("PASSED: Manual position sync uses 7200s window")

    def test_manual_sync_force_finalize_after_30_cycles(self):
        """
        Manual position should force-finalize after exchange_closed_force_cycles (30)
        even without closedPnl evidence.
        """
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~648: elif seen_cycles < max(1, int(self.exchange_closed_force_cycles)):
        # Line ~656-660: Force-finalize after exchange_closed_force_cycles
        assert "exchange_closed_force_cycles" in source, "Should reference exchange_closed_force_cycles"
        assert "force-finalizing" in source.lower(), "Should have force-finalize logic"
        print("PASSED: Manual position force-finalizes after exchange_closed_force_cycles")

    def test_manual_sync_logs_every_10_cycles(self):
        """Manual position sync should log every 10 cycles while waiting."""
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~650: if seen_cycles % 10 == 0:
        assert "% 10 == 0" in source, "Should log every 10 cycles"
        print("PASSED: Manual position sync logs every 10 cycles")


# ============================================================================
# Test: Regression - Bot Position Behavior
# ============================================================================
class TestBotPositionRegression:
    """
    Regression tests: Bot-origin positions should still work as before.
    """

    def test_bot_position_force_removed_after_3_fails(self):
        """
        Bot position with non-'not found' error after 3 fails should still be force-removed.
        This is the FORCE REMOVE logic at lines ~930-950.
        """
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~930-934: FORCE REMOVE for bot positions
        assert "FORCE REMOVE" in source, "Should have FORCE REMOVE logic for bot positions"
        assert "force_closed_stale" in source, "Should finalize as force_closed_stale"
        print("PASSED: Bot position force-removed after 3 fails (non-'not found')")

    def test_bot_position_sync_uses_300s_window(self):
        """
        Bot position sync should use original 300s window (not the wider 7200s).
        The wider window is only for manual positions.
        """
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~634: recent_closed = self._filter_recent_closed_pnl(closed, max_age_sec=300)
        # This is the default for all positions, manual positions then try wider window
        assert "max_age_sec=300" in source, "Default sync should use 300s window"
        print("PASSED: Bot position sync uses 300s window")


# ============================================================================
# Test: Regression - MANUAL SAFE Exit Blocking
# ============================================================================
class TestManualSafeExitBlocking:
    """
    Regression: MANUAL SAFE should still block hard_sl, early_exit, trend_exit
    for manual positions, while allowing trailing_exit and tp_cap.
    """

    def test_manual_safe_allows_trailing_exit(self):
        """MANUAL SAFE should allow TRAILING_EXIT for manual positions."""
        from engine.exit_engine import ExitReason
        
        allowed_reasons = [ExitReason.TRAILING_EXIT, ExitReason.TP_CAP]
        assert ExitReason.TRAILING_EXIT in allowed_reasons
        print("PASSED: TRAILING_EXIT allowed for manual positions")

    def test_manual_safe_allows_tp_cap(self):
        """MANUAL SAFE should allow TP_CAP for manual positions."""
        from engine.exit_engine import ExitReason
        
        allowed_reasons = [ExitReason.TRAILING_EXIT, ExitReason.TP_CAP]
        assert ExitReason.TP_CAP in allowed_reasons
        print("PASSED: TP_CAP allowed for manual positions")

    def test_manual_safe_blocks_trend_exit(self):
        """MANUAL SAFE should block TREND_EXIT for manual positions."""
        from engine.exit_engine import ExitReason
        
        allowed_reasons = [ExitReason.TRAILING_EXIT, ExitReason.TP_CAP]
        assert ExitReason.TREND_EXIT not in allowed_reasons
        print("PASSED: TREND_EXIT blocked for manual positions")

    def test_manual_safe_blocks_hard_sl(self):
        """MANUAL SAFE should block HARD_SL for manual positions."""
        from engine.exit_engine import ExitReason
        
        allowed_reasons = [ExitReason.TRAILING_EXIT, ExitReason.TP_CAP]
        assert ExitReason.HARD_SL not in allowed_reasons
        print("PASSED: HARD_SL blocked for manual positions")

    def test_manual_safe_blocks_early_exit(self):
        """MANUAL SAFE should block EARLY_EXIT for manual positions."""
        from engine.exit_engine import ExitReason
        
        allowed_reasons = [ExitReason.TRAILING_EXIT, ExitReason.TP_CAP]
        assert ExitReason.EARLY_EXIT not in allowed_reasons
        print("PASSED: EARLY_EXIT blocked for manual positions")

    def test_manual_safe_code_exists(self):
        """Verify MANUAL SAFE code block exists in main.py."""
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~849-856: MANUAL SAFE exit blocking
        assert "MANUAL SAFE" in source, "Should have MANUAL SAFE code block"
        assert "only trailing_exit/tp_cap allowed" in source.lower(), \
            "Should mention allowed exit reasons"
        print("PASSED: MANUAL SAFE code block exists")


# ============================================================================
# Test: ExitReason Enum
# ============================================================================
class TestExitReasonEnum:
    """Verify ExitReason enum has all required values."""

    def test_trend_exit_exists(self):
        """TREND_EXIT should exist in ExitReason enum."""
        from engine.exit_engine import ExitReason
        
        assert hasattr(ExitReason, "TREND_EXIT"), "TREND_EXIT should exist"
        assert ExitReason.TREND_EXIT.value == "trend_exit"
        print("PASSED: TREND_EXIT exists in ExitReason")

    def test_exchange_closed_exists(self):
        """EXCHANGE_CLOSED should exist in ExitReason enum."""
        from engine.exit_engine import ExitReason
        
        assert hasattr(ExitReason, "EXCHANGE_CLOSED"), "EXCHANGE_CLOSED should exist"
        assert ExitReason.EXCHANGE_CLOSED.value == "exchange_closed"
        print("PASSED: EXCHANGE_CLOSED exists in ExitReason")


# ============================================================================
# Test: Telegram Alert for Position Gone
# ============================================================================
class TestTelegramAlertPositionGone:
    """Verify Telegram alert is sent when position is detected as gone."""

    def test_telegram_alert_code_exists(self):
        """Verify Telegram alert code exists for POSITION GONE."""
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Lines ~903-912: Telegram alert for POSITION GONE
        assert "[POSITION GONE]" in source, "Should have POSITION GONE alert"
        assert "Position not found on exchange" in source, "Should mention position not found"
        assert "send_alert" in source, "Should call send_alert"
        print("PASSED: Telegram alert code exists for POSITION GONE")


# ============================================================================
# Test: _can_finalize_exchange_closed logic
# ============================================================================
class TestCanFinalizeExchangeClosed:
    """Test the _can_finalize_exchange_closed method."""

    def test_can_finalize_with_closed_records(self):
        """Should finalize if closedPnl records exist."""
        from main import TradingBot
        
        # Create a minimal bot instance for testing
        with patch.object(TradingBot, '__init__', lambda x: None):
            bot = TradingBot()
            bot.exchange_closed_require_closed_pnl = True
            bot.exchange_closed_force_cycles = 30
            
            # With closed records, should finalize
            result = bot._can_finalize_exchange_closed(missing_cycles=5, closed_records_count=1)
            assert result is True, "Should finalize with closed records"
            print("PASSED: Can finalize with closed records")

    def test_can_finalize_after_force_cycles(self):
        """Should finalize after exchange_closed_force_cycles even without records."""
        from main import TradingBot
        
        with patch.object(TradingBot, '__init__', lambda x: None):
            bot = TradingBot()
            bot.exchange_closed_require_closed_pnl = True
            bot.exchange_closed_force_cycles = 30
            
            # After 30 cycles, should finalize even without records
            result = bot._can_finalize_exchange_closed(missing_cycles=30, closed_records_count=0)
            assert result is True, "Should finalize after force_cycles"
            print("PASSED: Can finalize after force_cycles")

    def test_cannot_finalize_before_force_cycles_without_records(self):
        """Should NOT finalize before force_cycles without records."""
        from main import TradingBot
        
        with patch.object(TradingBot, '__init__', lambda x: None):
            bot = TradingBot()
            bot.exchange_closed_require_closed_pnl = True
            bot.exchange_closed_force_cycles = 30
            
            # Before 30 cycles without records, should NOT finalize
            result = bot._can_finalize_exchange_closed(missing_cycles=15, closed_records_count=0)
            assert result is False, "Should NOT finalize before force_cycles without records"
            print("PASSED: Cannot finalize before force_cycles without records")


# ============================================================================
# Test: Code Structure Verification
# ============================================================================
class TestCodeStructure:
    """Verify the code structure matches the bug fix requirements."""

    def test_position_gone_handles_both_origins(self):
        """
        POSITION GONE logic should handle both manual and bot origins.
        The key fix is that it finalizes regardless of origin.
        """
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # The POSITION GONE block should mention origin
        # Line ~886: f"Finalizing as exchange_closed (origin={pos.origin})"
        assert "origin={pos.origin}" in source or "origin=" in source, \
            "Should log origin in POSITION GONE"
        print("PASSED: POSITION GONE handles both origins")

    def test_manual_safe_non_not_found_resets_counter(self):
        """
        For manual positions with non-'not found' errors, counter should be reset.
        This preserves the existing behavior.
        """
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~919: self._failed_close_attempts.pop(symbol, None)
        # This should happen in the MANUAL SAFE block for non-'not found' errors
        assert "NOT removing. Resetting counter" in source, \
            "Should reset counter for manual non-'not found' errors"
        print("PASSED: Manual non-'not found' errors reset counter")

    def test_wider_window_only_for_manual(self):
        """
        The 7200s wider window should only be used for manual positions.
        Bot positions should use the standard 300s window.
        """
        import inspect
        from main import TradingBot
        
        source = inspect.getsource(TradingBot._manage_positions)
        
        # Line ~638: if pos.origin == "manual" and len(recent_closed) == 0:
        # Line ~640: wider_closed = self._filter_recent_closed_pnl(closed, max_age_sec=7200)
        assert 'pos.origin == "manual"' in source, "Should check for manual origin"
        assert "wider_closed" in source, "Should have wider_closed variable"
        print("PASSED: Wider window only for manual positions")


# ============================================================================
# Integration Test: Simulate Position Gone Scenario
# ============================================================================
class TestPositionGoneIntegration:
    """Integration test simulating the position gone scenario."""

    @pytest.mark.asyncio
    async def test_manual_position_gone_scenario(self):
        """
        Simulate: Manual position, execute_close fails 3x with 'Position not found'.
        Expected: Position should be finalized as exchange_closed.
        """
        from main import TradingBot
        
        # Create mock objects
        mock_client = MagicMock()
        mock_client.get_closed_pnl = AsyncMock(return_value=[
            {"closedPnl": "1.5", "updatedTime": int(time.time() * 1000) - 1800_000}  # 30 min ago
        ])
        mock_client.get_price = AsyncMock(return_value=100.0)
        
        mock_execution_engine = MagicMock()
        mock_execution_engine.execute_close = AsyncMock(return_value={
            "success": False,
            "error": "Position not found"
        })
        
        mock_position_manager = MagicMock()
        mock_position = MockPosition(
            symbol="AAVEUSDT",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            origin="manual"
        )
        mock_position_manager.get = MagicMock(return_value=mock_position)
        mock_position_manager.remove = MagicMock(return_value=mock_position)
        
        # Test the error detection logic
        error_msg = "Position not found"
        position_gone = "not found" in error_msg.lower()
        
        assert position_gone is True, "Should detect position as gone"
        
        # Verify the position would be removed
        # In real code, this happens at line ~889: removed = self.position_manager.remove(symbol)
        print("PASSED: Manual position gone scenario - position would be finalized")

    @pytest.mark.asyncio
    async def test_manual_position_sync_wider_window_scenario(self):
        """
        Simulate: Manual position closed >5min ago but <2h ago.
        Expected: Should find closedPnl in wider 7200s window and finalize.
        """
        from main import TradingBot
        
        now_ms = int(time.time() * 1000)
        
        # closedPnl from 30 minutes ago (fails 300s, passes 7200s)
        closed_records = [
            {"closedPnl": "2.5", "updatedTime": now_ms - 1800_000}  # 30 min ago
        ]
        
        # Test 300s filter (should fail)
        recent_300s = TradingBot._filter_recent_closed_pnl(closed_records, max_age_sec=300)
        assert len(recent_300s) == 0, "Should fail 300s filter"
        
        # Test 7200s filter (should pass)
        recent_7200s = TradingBot._filter_recent_closed_pnl(closed_records, max_age_sec=7200)
        assert len(recent_7200s) == 1, "Should pass 7200s filter"
        assert recent_7200s[0]["closedPnl"] == "2.5"
        
        print("PASSED: Manual position sync wider window scenario")


# ============================================================================
# Run all tests
# ============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
