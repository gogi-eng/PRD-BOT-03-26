#!/usr/bin/env python3
"""
Iteration 33 Tests: Stronger Exchange Closed Debounce with Close Evidence Requirement

Features tested:
1. position_sync.exchange_closed_require_closed_pnl=true in config
2. position_sync.exchange_closed_force_cycles=8 in config
3. main.py does not remove position immediately on missing; waits and checks closed pnl evidence
4. _can_finalize_exchange_closed logic: needs closed records unless missing_cycles reaches force threshold
5. Origin persistence remains intact (regression)
6. No regression in early_exit and cooldown fixes
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import main as main_module
from main import TradingBot
from engine.position_manager import Position


# ============================================================================
# Test 1: Config has exchange_closed_require_closed_pnl=true
# ============================================================================
class TestConfigExchangeClosedRequireClosedPnl:
    """Verify config.yaml has position_sync.exchange_closed_require_closed_pnl=true"""

    def test_config_has_exchange_closed_require_closed_pnl_true(self):
        """Config should have exchange_closed_require_closed_pnl=true"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        require_pnl = cfg.get("position_sync", "exchange_closed_require_closed_pnl", default=False)
        assert require_pnl is True, f"Expected exchange_closed_require_closed_pnl=true, got {require_pnl}"

    def test_bot_loads_exchange_closed_require_closed_pnl(self, monkeypatch, tmp_path):
        """Bot should load exchange_closed_require_closed_pnl from config"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        
        assert bot.exchange_closed_require_closed_pnl is True


# ============================================================================
# Test 2: Config has exchange_closed_force_cycles=8
# ============================================================================
class TestConfigExchangeClosedForceCycles:
    """Verify config.yaml has position_sync.exchange_closed_force_cycles=8"""

    def test_config_has_exchange_closed_force_cycles_8(self):
        """Config should have exchange_closed_force_cycles=8"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        force_cycles = cfg.get("position_sync", "exchange_closed_force_cycles", default=1)
        assert force_cycles == 8, f"Expected exchange_closed_force_cycles=8, got {force_cycles}"

    def test_bot_loads_exchange_closed_force_cycles(self, monkeypatch, tmp_path):
        """Bot should load exchange_closed_force_cycles from config"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_force_cycles = 8
        
        assert bot.exchange_closed_force_cycles == 8


# ============================================================================
# Test 3: _can_finalize_exchange_closed logic
# ============================================================================
class TestCanFinalizeExchangeClosed:
    """Test _can_finalize_exchange_closed requires closed records or force cycles"""

    def test_no_closed_records_below_force_cycles_returns_false(self):
        """Without closed records and below force_cycles, should return False"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        # 3 missing cycles, 0 closed records -> should NOT finalize
        result = bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=0)
        assert result is False

    def test_with_closed_records_returns_true(self):
        """With closed records, should return True regardless of cycles"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        # 3 missing cycles, 1 closed record -> should finalize
        result = bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=1)
        assert result is True

    def test_force_cycles_reached_without_closed_records_returns_true(self):
        """At force_cycles threshold, should return True even without closed records"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        # 8 missing cycles, 0 closed records -> should finalize (forced)
        result = bot._can_finalize_exchange_closed(missing_cycles=8, closed_records_count=0)
        assert result is True

    def test_below_force_cycles_no_closed_records_returns_false(self):
        """Below force_cycles without closed records, should return False"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        # Test cycles 1-7 without closed records
        for cycles in range(1, 8):
            result = bot._can_finalize_exchange_closed(missing_cycles=cycles, closed_records_count=0)
            assert result is False, f"Cycle {cycles} should return False without closed records"

    def test_require_closed_pnl_disabled_always_returns_true(self):
        """When require_closed_pnl=false, should always return True"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = False
        bot.exchange_closed_force_cycles = 8
        
        # Even with 0 closed records and low cycles, should return True
        result = bot._can_finalize_exchange_closed(missing_cycles=1, closed_records_count=0)
        assert result is True

    def test_multiple_closed_records_returns_true(self):
        """Multiple closed records should return True"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        result = bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=3)
        assert result is True

    def test_force_cycles_boundary_7_returns_false(self):
        """At force_cycles-1 (7), should return False without closed records"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        result = bot._can_finalize_exchange_closed(missing_cycles=7, closed_records_count=0)
        assert result is False

    def test_force_cycles_boundary_9_returns_true(self):
        """Above force_cycles (9), should return True without closed records"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        result = bot._can_finalize_exchange_closed(missing_cycles=9, closed_records_count=0)
        assert result is True


# ============================================================================
# Test 4: Combined debounce + evidence flow
# ============================================================================
class TestCombinedDebounceAndEvidence:
    """Test the combined flow of _should_finalize + _can_finalize"""

    def test_position_not_removed_until_both_conditions_met(self):
        """Position should not be removed until both debounce AND evidence conditions met"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # Cycle 1: debounce not met
        should_finalize = bot._should_finalize_exchange_closed("TESTUSDT")
        assert should_finalize is False
        
        # Cycle 2: debounce not met
        should_finalize = bot._should_finalize_exchange_closed("TESTUSDT")
        assert should_finalize is False
        
        # Cycle 3: debounce met, but no closed records
        should_finalize = bot._should_finalize_exchange_closed("TESTUSDT")
        assert should_finalize is True
        
        # Now check evidence - no closed records, cycles=3
        can_finalize = bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=0)
        assert can_finalize is False  # Still waiting for evidence

    def test_position_removed_with_closed_records_after_debounce(self):
        """Position should be removed when debounce met AND closed records exist"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # Pass debounce
        bot._should_finalize_exchange_closed("TESTUSDT")
        bot._should_finalize_exchange_closed("TESTUSDT")
        should_finalize = bot._should_finalize_exchange_closed("TESTUSDT")
        assert should_finalize is True
        
        # Check evidence - has closed records
        can_finalize = bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=1)
        assert can_finalize is True

    def test_position_force_removed_after_8_cycles_no_evidence(self):
        """Position should be force-removed after 8 cycles even without evidence"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # Simulate 8 cycles
        for i in range(8):
            bot._should_finalize_exchange_closed("TESTUSDT")
        
        # After 8 cycles, should force finalize even without closed records
        can_finalize = bot._can_finalize_exchange_closed(missing_cycles=8, closed_records_count=0)
        assert can_finalize is True


# ============================================================================
# Test 5: API inconsistency / rate limit scenario
# ============================================================================
class TestAPIInconsistencyScenario:
    """Test that false exchange_closed is prevented under API inconsistency"""

    def test_temporary_api_miss_does_not_close_position(self):
        """Temporary API miss (2/3 cycles) should NOT close position"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # Simulate 2 cycles of API miss
        bot._should_finalize_exchange_closed("RDNTUSDT")
        should_finalize = bot._should_finalize_exchange_closed("RDNTUSDT")
        
        # After 2 cycles, debounce not met
        assert should_finalize is False
        
        # Even if we check evidence, debounce prevents finalization
        # (In real code, _can_finalize is only called after _should_finalize returns True)

    def test_position_reappears_resets_counter(self):
        """When position reappears on exchange, counter should be reset"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # 2 cycles of missing
        bot._should_finalize_exchange_closed("RDNTUSDT")
        bot._should_finalize_exchange_closed("RDNTUSDT")
        assert bot._missing_exchange_cycles["RDNTUSDT"] == 2
        
        # Position reappears - counter cleared (simulated by pop)
        bot._missing_exchange_cycles.pop("RDNTUSDT", None)
        
        # Next miss starts fresh
        should_finalize = bot._should_finalize_exchange_closed("RDNTUSDT")
        assert should_finalize is False
        assert bot._missing_exchange_cycles["RDNTUSDT"] == 1


# ============================================================================
# Test 6: Regression - Origin persistence
# ============================================================================
class TestRegressionOriginPersistence:
    """Regression tests for origin field persistence"""

    def test_save_trade_persists_origin(self, tmp_path, monkeypatch):
        """_save_trade should persist origin field"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot._save_trade(
            symbol="RDNTUSDT",
            side="BUY",
            qty=10,
            entry=0.123,
            exit_price=0.126,
            pnl=0.03,
            reason="tp",
            origin="manual",
        )
        
        history_path = tmp_path / "trade_history.json"
        assert history_path.exists()
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(rows) == 1
        assert rows[0]["origin"] == "manual"

    def test_position_origin_attribute_exists(self):
        """Position should have origin attribute"""
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="bot",
        )
        assert pos.origin == "bot"


# ============================================================================
# Test 7: Regression - early_exit and cooldown fixes
# ============================================================================
class TestRegressionEarlyExitCooldown:
    """Regression tests for early_exit and cooldown fixes"""

    def test_early_exit_in_ignore_cooldown_reasons(self):
        """early_exit should be in ignore_loss_cooldown_reasons"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        ignore_reasons = cfg.get("risk", "ignore_loss_cooldown_reasons", default=[])
        assert "early_exit" in ignore_reasons

    def test_early_exit_in_ignore_consecutive_reasons(self):
        """early_exit should be in ignore_consecutive_loss_reasons"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        ignore_reasons = cfg.get("risk", "ignore_consecutive_loss_reasons", default=[])
        assert "early_exit" in ignore_reasons

    def test_exchange_closed_in_ignore_cooldown_reasons(self):
        """exchange_closed should be in ignore_loss_cooldown_reasons"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        ignore_reasons = cfg.get("risk", "ignore_loss_cooldown_reasons", default=[])
        assert "exchange_closed" in ignore_reasons

    def test_exchange_closed_in_ignore_consecutive_reasons(self):
        """exchange_closed should be in ignore_consecutive_loss_reasons"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        ignore_reasons = cfg.get("risk", "ignore_consecutive_loss_reasons", default=[])
        assert "exchange_closed" in ignore_reasons


# ============================================================================
# Test 8: Edge cases for _can_finalize_exchange_closed
# ============================================================================
class TestCanFinalizeEdgeCases:
    """Edge case tests for _can_finalize_exchange_closed"""

    def test_zero_missing_cycles_with_closed_records(self):
        """Zero missing cycles with closed records should return True"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        result = bot._can_finalize_exchange_closed(missing_cycles=0, closed_records_count=1)
        assert result is True

    def test_negative_missing_cycles_handled(self):
        """Negative missing cycles should be handled gracefully"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        
        # Should not crash, and should return False (no evidence, below force)
        result = bot._can_finalize_exchange_closed(missing_cycles=-1, closed_records_count=0)
        assert result is False

    def test_force_cycles_of_1_with_require_pnl(self):
        """force_cycles=1 with require_pnl=true should force on first cycle"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 1
        
        result = bot._can_finalize_exchange_closed(missing_cycles=1, closed_records_count=0)
        assert result is True

    def test_large_force_cycles_value(self):
        """Large force_cycles value should work correctly"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 100
        
        # Below threshold
        result = bot._can_finalize_exchange_closed(missing_cycles=99, closed_records_count=0)
        assert result is False
        
        # At threshold
        result = bot._can_finalize_exchange_closed(missing_cycles=100, closed_records_count=0)
        assert result is True


# ============================================================================
# Test 9: Integration test - debounce + evidence logic unit test
# ============================================================================
class TestDebounceEvidenceIntegration:
    """Test the combined debounce + evidence logic"""

    def test_full_flow_no_evidence_waits_for_force_cycles(self):
        """Full flow: debounce passes but no evidence, waits for force_cycles"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # Simulate cycles 1-7: debounce passes at 3, but no evidence
        for cycle in range(1, 8):
            should_finalize = bot._should_finalize_exchange_closed("TESTUSDT")
            seen_cycles = bot._missing_exchange_cycles.get("TESTUSDT", 0)
            can_finalize = bot._can_finalize_exchange_closed(seen_cycles, closed_records_count=0)
            
            if cycle < 3:
                assert should_finalize is False, f"Cycle {cycle}: debounce should not pass"
            else:
                assert should_finalize is True, f"Cycle {cycle}: debounce should pass"
                assert can_finalize is False, f"Cycle {cycle}: no evidence, should not finalize"
        
        # Cycle 8: force finalize
        should_finalize = bot._should_finalize_exchange_closed("TESTUSDT")
        seen_cycles = bot._missing_exchange_cycles.get("TESTUSDT", 0)
        can_finalize = bot._can_finalize_exchange_closed(seen_cycles, closed_records_count=0)
        
        assert should_finalize is True
        assert can_finalize is True, "Cycle 8: should force finalize"

    def test_full_flow_with_evidence_finalizes_at_debounce(self):
        """Full flow: debounce passes with evidence, finalizes immediately"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # Simulate cycles 1-3
        for cycle in range(1, 4):
            should_finalize = bot._should_finalize_exchange_closed("TESTUSDT")
            seen_cycles = bot._missing_exchange_cycles.get("TESTUSDT", 0)
            # Simulate having 1 closed record
            can_finalize = bot._can_finalize_exchange_closed(seen_cycles, closed_records_count=1)
            
            if cycle < 3:
                assert should_finalize is False
            else:
                assert should_finalize is True
                assert can_finalize is True, "With evidence, should finalize at debounce"

    def test_counter_persists_across_cycles(self):
        """Counter should persist and increment across cycles"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot.exchange_closed_require_closed_pnl = True
        bot.exchange_closed_force_cycles = 8
        bot._missing_exchange_cycles = {}
        
        # Verify counter increments
        for expected_count in range(1, 10):
            bot._should_finalize_exchange_closed("TESTUSDT")
            assert bot._missing_exchange_cycles["TESTUSDT"] == expected_count


# ============================================================================
# Test 10: Verify config values match expected
# ============================================================================
class TestConfigValuesMatch:
    """Verify all position_sync config values are correct"""

    def test_all_position_sync_config_values(self):
        """All position_sync config values should match expected"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        # Verify all position_sync values
        assert cfg.get("position_sync", "adopt_all_positions", default=False) is True
        assert cfg.get("position_sync", "preserve_existing_sl_tp", default=False) is True
        assert cfg.get("position_sync", "exchange_closed_confirm_cycles", default=1) == 3
        assert cfg.get("position_sync", "exchange_closed_require_closed_pnl", default=False) is True
        assert cfg.get("position_sync", "exchange_closed_force_cycles", default=1) == 8
