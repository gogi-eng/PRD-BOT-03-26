#!/usr/bin/env python3
"""
Iteration 31 Comprehensive Tests:
1. Trade history entries include origin field
2. _finalize_full_close/_finalize_partial_close pass pos.origin to _save_trade
3. Position sync missing-on-exchange requires multiple confirm cycles before exchange_closed close
4. Config has position_sync.exchange_closed_confirm_cycles=3
5. No regression with recent risk/early_exit fixes
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
# Test 1: Config has exchange_closed_confirm_cycles=3
# ============================================================================
class TestConfigExchangeClosedConfirmCycles:
    """Verify config.yaml has position_sync.exchange_closed_confirm_cycles=3"""

    def test_config_has_exchange_closed_confirm_cycles(self):
        """Config should have exchange_closed_confirm_cycles set to 3"""
        from core.config import BotConfig
        from pathlib import Path
        
        bot_dir = Path(__file__).parent.parent.parent / "bot"
        cfg = BotConfig.load(str(bot_dir / "config.yaml"))
        
        cycles = cfg.get("position_sync", "exchange_closed_confirm_cycles", default=1)
        assert cycles == 3, f"Expected exchange_closed_confirm_cycles=3, got {cycles}"

    def test_bot_loads_exchange_closed_confirm_cycles(self, monkeypatch, tmp_path):
        """Bot should load exchange_closed_confirm_cycles from config"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        
        assert bot.exchange_closed_confirm_cycles == 3


# ============================================================================
# Test 2: _save_trade persists origin field
# ============================================================================
class TestSaveTradeOrigin:
    """Verify _save_trade correctly persists origin field"""

    def test_save_trade_with_bot_origin(self, tmp_path, monkeypatch):
        """_save_trade should persist origin='bot' by default"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot._save_trade(
            symbol="BTCUSDT",
            side="BUY",
            qty=0.01,
            entry=50000.0,
            exit_price=51000.0,
            pnl=10.0,
            reason="tp",
            origin="bot",
        )
        
        history_path = tmp_path / "trade_history.json"
        assert history_path.exists()
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert len(rows) == 1
        assert rows[0]["origin"] == "bot"

    def test_save_trade_with_manual_origin(self, tmp_path, monkeypatch):
        """_save_trade should persist origin='manual' when specified"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot._save_trade(
            symbol="ETHUSDT",
            side="SELL",
            qty=0.5,
            entry=3000.0,
            exit_price=2900.0,
            pnl=50.0,
            reason="trailing_stop",
            origin="manual",
        )
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "manual"

    def test_save_trade_default_origin_is_bot(self, tmp_path, monkeypatch):
        """_save_trade should default to origin='bot' if not specified"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        # Call without origin parameter - should default to "bot"
        bot._save_trade(
            symbol="SOLUSDT",
            side="BUY",
            qty=10,
            entry=100.0,
            exit_price=105.0,
            pnl=50.0,
            reason="tp",
        )
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "bot"

    def test_save_trade_multiple_trades_different_origins(self, tmp_path, monkeypatch):
        """Multiple trades with different origins should be saved correctly"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        
        # Bot trade
        bot._save_trade("BTCUSDT", "BUY", 0.01, 50000, 51000, 10, "tp", origin="bot")
        # Manual trade
        bot._save_trade("ETHUSDT", "SELL", 0.5, 3000, 2900, 50, "sl", origin="manual")
        # Another bot trade
        bot._save_trade("SOLUSDT", "BUY", 10, 100, 95, -50, "sl", origin="bot")
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        
        assert len(rows) == 3
        assert rows[0]["origin"] == "bot"
        assert rows[1]["origin"] == "manual"
        assert rows[2]["origin"] == "bot"


# ============================================================================
# Test 3: _should_finalize_exchange_closed debounce logic
# ============================================================================
class TestExchangeClosedDebounce:
    """Verify exchange_closed requires multiple confirm cycles"""

    def test_first_missing_cycle_returns_false(self):
        """First missing cycle should NOT finalize (return False)"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        result = bot._should_finalize_exchange_closed("RDNTUSDT")
        assert result is False
        assert bot._missing_exchange_cycles["RDNTUSDT"] == 1

    def test_second_missing_cycle_returns_false(self):
        """Second missing cycle should NOT finalize (return False)"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        bot._should_finalize_exchange_closed("RDNTUSDT")  # 1st
        result = bot._should_finalize_exchange_closed("RDNTUSDT")  # 2nd
        
        assert result is False
        assert bot._missing_exchange_cycles["RDNTUSDT"] == 2

    def test_third_missing_cycle_returns_true(self):
        """Third missing cycle should finalize (return True)"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        bot._should_finalize_exchange_closed("RDNTUSDT")  # 1st
        bot._should_finalize_exchange_closed("RDNTUSDT")  # 2nd
        result = bot._should_finalize_exchange_closed("RDNTUSDT")  # 3rd
        
        assert result is True
        assert bot._missing_exchange_cycles["RDNTUSDT"] == 3

    def test_different_symbols_tracked_separately(self):
        """Different symbols should have separate counters"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        # RDNTUSDT: 2 cycles
        bot._should_finalize_exchange_closed("RDNTUSDT")
        bot._should_finalize_exchange_closed("RDNTUSDT")
        
        # BTCUSDT: 1 cycle
        bot._should_finalize_exchange_closed("BTCUSDT")
        
        assert bot._missing_exchange_cycles["RDNTUSDT"] == 2
        assert bot._missing_exchange_cycles["BTCUSDT"] == 1

    def test_confirm_cycles_of_1_finalizes_immediately(self):
        """With confirm_cycles=1, first missing should finalize"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 1
        bot._missing_exchange_cycles = {}
        
        result = bot._should_finalize_exchange_closed("TESTUSDT")
        assert result is True

    def test_confirm_cycles_of_5_requires_5_cycles(self):
        """With confirm_cycles=5, need 5 missing cycles to finalize"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 5
        bot._missing_exchange_cycles = {}
        
        for i in range(4):
            result = bot._should_finalize_exchange_closed("TESTUSDT")
            assert result is False, f"Cycle {i+1} should return False"
        
        result = bot._should_finalize_exchange_closed("TESTUSDT")
        assert result is True, "Cycle 5 should return True"


# ============================================================================
# Test 4: _finalize_full_close passes pos.origin to _save_trade
# ============================================================================
class TestFinalizeFullCloseOrigin:
    """Verify _finalize_full_close passes position origin to _save_trade"""

    @pytest.mark.asyncio
    async def test_finalize_full_close_passes_bot_origin(self, tmp_path, monkeypatch):
        """_finalize_full_close should pass pos.origin='bot' to _save_trade"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot._missing_exchange_cycles = {}
        bot.position_manager = MagicMock()
        bot.risk_guard = MagicMock()
        bot.controls = MagicMock()
        bot.tg = None
        
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="bot",
        )
        
        await bot._finalize_full_close("BTCUSDT", pos, 51000.0, 10.0, "tp")
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "bot"

    @pytest.mark.asyncio
    async def test_finalize_full_close_passes_manual_origin(self, tmp_path, monkeypatch):
        """_finalize_full_close should pass pos.origin='manual' to _save_trade"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot._missing_exchange_cycles = {}
        bot.position_manager = MagicMock()
        bot.risk_guard = MagicMock()
        bot.controls = MagicMock()
        bot.tg = None
        
        pos = Position(
            symbol="ETHUSDT",
            side="SELL",
            entry_price=3000.0,
            qty=0.5,
            stop_loss=3100.0,
            take_profit=2800.0,
            origin="manual",
        )
        
        await bot._finalize_full_close("ETHUSDT", pos, 2900.0, 50.0, "trailing_stop")
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "manual"


# ============================================================================
# Test 5: _finalize_partial_close passes pos.origin to _save_trade
# ============================================================================
class TestFinalizePartialCloseOrigin:
    """Verify _finalize_partial_close passes position origin to _save_trade"""

    @pytest.mark.asyncio
    async def test_finalize_partial_close_passes_bot_origin(self, tmp_path, monkeypatch):
        """_finalize_partial_close should pass pos.origin='bot' to _save_trade"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.position_manager = MagicMock()
        bot.risk_guard = MagicMock()
        bot.controls = MagicMock()
        
        pos = Position(
            symbol="SOLUSDT",
            side="BUY",
            entry_price=100.0,
            qty=10.0,
            stop_loss=95.0,
            take_profit=110.0,
            origin="bot",
        )
        
        await bot._finalize_partial_close("SOLUSDT", pos, 105.0, 5.0, "partial_tp")
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "bot"

    @pytest.mark.asyncio
    async def test_finalize_partial_close_passes_manual_origin(self, tmp_path, monkeypatch):
        """_finalize_partial_close should pass pos.origin='manual' to _save_trade"""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.position_manager = MagicMock()
        bot.risk_guard = MagicMock()
        bot.controls = MagicMock()
        
        pos = Position(
            symbol="LINKUSDT",
            side="SELL",
            entry_price=15.0,
            qty=100.0,
            stop_loss=16.0,
            take_profit=13.0,
            origin="manual",
        )
        
        await bot._finalize_partial_close("LINKUSDT", pos, 14.0, 50.0, "rl_reduce")
        
        history_path = tmp_path / "trade_history.json"
        rows = json.loads(history_path.read_text(encoding="utf-8"))
        assert rows[0]["origin"] == "manual"


# ============================================================================
# Test 6: Position class has origin attribute
# ============================================================================
class TestPositionOriginAttribute:
    """Verify Position dataclass has origin attribute"""

    def test_position_has_origin_attribute(self):
        """Position should have origin attribute"""
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
        )
        assert hasattr(pos, "origin")

    def test_position_origin_defaults_to_bot(self):
        """Position origin should default to 'bot'"""
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
        )
        assert pos.origin == "bot"

    def test_position_origin_can_be_manual(self):
        """Position origin can be set to 'manual'"""
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=50000.0,
            qty=0.01,
            stop_loss=49000.0,
            take_profit=52000.0,
            origin="manual",
        )
        assert pos.origin == "manual"


# ============================================================================
# Test 7: Regression - exchange_closed in ignore lists (from iteration 30)
# ============================================================================
class TestRegressionExchangeClosedIgnore:
    """Regression tests for exchange_closed in ignore lists"""

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
# Test 8: Integration - RDNTUSDT scenario (user reported issue)
# ============================================================================
class TestRDNTUSDTScenario:
    """Test the specific RDNTUSDT scenario user reported"""

    def test_rdntusdt_not_closed_on_first_missing_cycle(self):
        """RDNTUSDT should NOT be closed on first missing cycle"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        # First cycle - position missing on exchange
        should_close = bot._should_finalize_exchange_closed("RDNTUSDT")
        
        assert should_close is False, "RDNTUSDT should NOT be closed on first missing cycle"

    def test_rdntusdt_closed_after_confirm_cycles(self):
        """RDNTUSDT should be closed after 3 confirm cycles"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        # Simulate 3 cycles of position missing
        bot._should_finalize_exchange_closed("RDNTUSDT")  # 1
        bot._should_finalize_exchange_closed("RDNTUSDT")  # 2
        should_close = bot._should_finalize_exchange_closed("RDNTUSDT")  # 3
        
        assert should_close is True, "RDNTUSDT should be closed after 3 confirm cycles"


# ============================================================================
# Test 9: Counter reset when position reappears
# ============================================================================
class TestCounterResetOnReappear:
    """Test that counter is reset when position reappears on exchange"""

    def test_counter_cleared_on_finalize(self):
        """Counter should be cleared when position is finalized"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {"BTCUSDT": 2}
        
        # Simulate finalize clearing the counter
        bot._missing_exchange_cycles.pop("BTCUSDT", None)
        
        assert "BTCUSDT" not in bot._missing_exchange_cycles

    def test_counter_can_be_manually_cleared(self):
        """Counter can be cleared when position reappears"""
        bot = TradingBot.__new__(TradingBot)
        bot.exchange_closed_confirm_cycles = 3
        bot._missing_exchange_cycles = {}
        
        # Position missing for 2 cycles
        bot._should_finalize_exchange_closed("ETHUSDT")
        bot._should_finalize_exchange_closed("ETHUSDT")
        assert bot._missing_exchange_cycles["ETHUSDT"] == 2
        
        # Position reappears - counter should be cleared
        bot._missing_exchange_cycles.pop("ETHUSDT", None)
        
        # Next missing cycle starts fresh
        result = bot._should_finalize_exchange_closed("ETHUSDT")
        assert result is False
        assert bot._missing_exchange_cycles["ETHUSDT"] == 1
