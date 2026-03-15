#!/usr/bin/env python3
"""
Pytest test suite for new Trading Bot features:
- Position sync/adoption from exchange
- Partial TP logic (50% path to final TP closes 50% size)
- Portfolio total TP (aggregate PnL target closes all positions)
- ExecutionEngine/BybitClient position_idx support
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

# Configure pytest-asyncio mode
pytestmark = pytest.mark.asyncio(loop_scope="function")


# === Fixtures ===

@pytest.fixture
def mock_klines():
    """Generate realistic klines for testing."""
    klines = []
    price = 62000.0
    for i in range(180):
        drift = 35.0
        price += drift
        klines.append({
            "open": price - 18,
            "high": price + 45,
            "low": price - 45,
            "close": price,
            "volume": 1500 + i * 12,
        })
    return klines


@pytest.fixture
def trading_bot():
    """Create TradingBot instance with mocked external dependencies."""
    from main import TradingBot
    bot = TradingBot()
    bot.tg = None
    bot._save_trade = lambda *args, **kwargs: None
    return bot


@pytest.fixture
def position_manager():
    """Create clean PositionManager instance."""
    from engine.position_manager import PositionManager
    return PositionManager()


@pytest.fixture
def position_class():
    """Return Position class for creating test positions."""
    from engine.position_manager import Position
    return Position


# === Module: Position Sync/Adoption Tests ===

class TestPositionSync:
    """Tests for adopting/syncing exchange positions into local position manager."""

    @pytest.mark.asyncio
    async def test_adopt_new_position_from_exchange(self, trading_bot, mock_klines):
        """Verify bot adopts a new exchange position with correct attributes."""
        async def fake_get_klines(symbol, interval, limit):
            return mock_klines[-limit:]

        trading_bot.client.get_klines = fake_get_klines
        trading_bot.execution_engine.update_sl = AsyncMock(return_value=True)
        trading_bot.execution_engine.update_tp = AsyncMock(return_value=True)

        exchange_pos = {
            "symbol": "BTCUSDT",
            "size": "0.05",
            "avgPrice": "62000",
            "markPrice": "62500",
            "side": "Buy",
            "stopLoss": "0",
            "takeProfit": "0",
            "positionIdx": 2,
            "unrealisedPnl": "25.0",
        }

        await trading_bot._sync_exchange_position(exchange_pos)
        adopted = trading_bot.position_manager.get("BTCUSDT")

        assert adopted is not None, "Position should be adopted"
        assert adopted.origin == "manual", "Origin should be 'manual' for external positions"
        assert adopted.position_idx == 2, "position_idx should be preserved"
        assert adopted.qty == 0.05, "Quantity should match exchange position"
        assert adopted.entry_price == 62000.0, "Entry price should match avgPrice"
        assert adopted.stop_loss > 0, "SL should be derived if not set"
        assert adopted.take_profit > 0, "TP should be derived if not set"

    @pytest.mark.asyncio
    async def test_preserve_existing_sl_tp_when_adopting(self, trading_bot, mock_klines):
        """Verify existing SL/TP from exchange are preserved and not overwritten."""
        async def fake_get_klines(symbol, interval, limit):
            return mock_klines[-limit:]

        trading_bot.client.get_klines = fake_get_klines
        trading_bot.execution_engine.update_sl = AsyncMock(return_value=True)
        trading_bot.execution_engine.update_tp = AsyncMock(return_value=True)
        trading_bot.preserve_existing_sl_tp = True

        exchange_pos = {
            "symbol": "ETHUSDT",
            "size": "1.0",
            "avgPrice": "3000",
            "markPrice": "3050",
            "side": "Buy",
            "stopLoss": "2900",
            "takeProfit": "3200",
            "positionIdx": 1,
            "unrealisedPnl": "50.0",
        }

        await trading_bot._sync_exchange_position(exchange_pos)
        adopted = trading_bot.position_manager.get("ETHUSDT")

        assert adopted.stop_loss == 2900, "SL should be preserved from exchange"
        assert adopted.take_profit == 3200, "TP should be preserved from exchange"
        assert adopted.total_tp_price == 3200, "Total TP price should match take_profit"

    @pytest.mark.asyncio
    async def test_update_existing_position_on_resync(self, trading_bot, mock_klines, position_class):
        """Verify position manager updates existing position without duplicating."""
        async def fake_get_klines(symbol, interval, limit):
            return mock_klines[-limit:]

        trading_bot.client.get_klines = fake_get_klines
        trading_bot.execution_engine.update_sl = AsyncMock(return_value=True)
        trading_bot.execution_engine.update_tp = AsyncMock(return_value=True)

        # Add existing position first
        existing = position_class(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=61000.0,
            qty=0.02,
            stop_loss=60500.0,
            take_profit=62000.0,
            origin="bot",
        )
        trading_bot.position_manager.add(existing)

        # Sync from exchange with updated values
        exchange_pos = {
            "symbol": "BTCUSDT",
            "size": "0.05",
            "avgPrice": "61500",
            "markPrice": "62000",
            "side": "Buy",
            "stopLoss": "61000",
            "takeProfit": "63000",
            "positionIdx": 1,
            "unrealisedPnl": "25.0",
        }

        await trading_bot._sync_exchange_position(exchange_pos)
        updated = trading_bot.position_manager.get("BTCUSDT")

        assert trading_bot.position_manager.count() == 1, "Should not duplicate position"
        assert updated.qty == 0.05, "Quantity should be updated from exchange"
        assert updated.position_idx == 1, "position_idx should be updated"

    @pytest.mark.asyncio
    async def test_short_position_adoption(self, trading_bot, mock_klines):
        """Verify short positions are correctly adopted with proper SL/TP direction."""
        async def fake_get_klines(symbol, interval, limit):
            return mock_klines[-limit:]

        trading_bot.client.get_klines = fake_get_klines
        trading_bot.execution_engine.update_sl = AsyncMock(return_value=True)
        trading_bot.execution_engine.update_tp = AsyncMock(return_value=True)

        exchange_pos = {
            "symbol": "SOLUSDT",
            "size": "10.0",
            "avgPrice": "100",
            "markPrice": "98",
            "side": "Sell",
            "stopLoss": "105",
            "takeProfit": "90",
            "positionIdx": 0,
            "unrealisedPnl": "20.0",
        }

        await trading_bot._sync_exchange_position(exchange_pos)
        adopted = trading_bot.position_manager.get("SOLUSDT")

        assert adopted.side == "SELL", "Side should be SELL for short position"
        assert not adopted.is_long, "is_long should be False"
        assert adopted.stop_loss == 105, "SL should be above entry for short"
        assert adopted.take_profit == 90, "TP should be below entry for short"


# === Module: Partial Take Profit Tests ===

class TestPartialTakeProfit:
    """Tests for partial TP logic: at 50% path to final TP, close 50% size."""

    def test_compute_partial_tp_price_long(self, trading_bot):
        """Verify partial TP price calculation for long positions."""
        # Entry=3000, TP=3120, 50% path = 3060
        price = trading_bot._compute_partial_tp_price(3000.0, 3120.0, "BUY")
        assert abs(price - 3060.0) < 0.01, f"Partial TP for long should be 3060, got {price}"

    def test_compute_partial_tp_price_short(self, trading_bot):
        """Verify partial TP price calculation for short positions."""
        # Entry=3000, TP=2880, 50% path = 2940
        price = trading_bot._compute_partial_tp_price(3000.0, 2880.0, "SELL")
        assert abs(price - 2940.0) < 0.01, f"Partial TP for short should be 2940, got {price}"

    def test_compute_partial_tp_invalid_inputs(self, trading_bot):
        """Verify partial TP returns 0 for invalid inputs."""
        assert trading_bot._compute_partial_tp_price(0, 3000, "BUY") == 0.0
        assert trading_bot._compute_partial_tp_price(3000, 0, "BUY") == 0.0
        # Invalid TP direction for long (TP below entry)
        assert trading_bot._compute_partial_tp_price(3000, 2900, "BUY") == 0.0
        # Invalid TP direction for short (TP above entry)
        assert trading_bot._compute_partial_tp_price(3000, 3100, "SELL") == 0.0

    @pytest.mark.asyncio
    async def test_partial_tp_execution_long(self, trading_bot, position_class):
        """Verify partial TP closes 50% of position when price hits trigger."""
        trading_bot.partial_tp_enabled = True
        trading_bot.partial_tp_close_fraction = 0.5
        trading_bot.partial_tp_move_stop_to_entry = True
        trading_bot.min_position_usdt = 5.0

        trading_bot.execution_engine.execute_close = AsyncMock(
            return_value={"success": True, "orderId": "partial123"}
        )
        trading_bot.execution_engine.update_sl = AsyncMock(return_value=True)

        pos = position_class(
            symbol="ETHUSDT",
            side="BUY",
            entry_price=3000.0,
            qty=1.0,
            stop_loss=2940.0,
            take_profit=3120.0,
            origin="manual",
            partial_tp_price=3060.0,
            partial_close_fraction=0.5,
            total_tp_price=3120.0,
        )
        trading_bot.position_manager.add(pos)

        # Price at partial TP level
        closed = await trading_bot._maybe_execute_partial_tp(pos, 3065.0)

        assert closed, "Should have executed partial TP"
        remaining = trading_bot.position_manager.get("ETHUSDT")
        assert remaining is not None, "Position should still exist"
        assert round(remaining.qty, 4) == 0.5, f"Remaining qty should be 0.5, got {remaining.qty}"
        assert remaining.partial_tp_done, "partial_tp_done flag should be True"
        assert remaining.stop_loss >= remaining.entry_price, "SL should be moved to entry (breakeven)"

    @pytest.mark.asyncio
    async def test_partial_tp_execution_short(self, trading_bot, position_class):
        """Verify partial TP works correctly for short positions."""
        trading_bot.partial_tp_enabled = True
        trading_bot.partial_tp_close_fraction = 0.5
        trading_bot.partial_tp_move_stop_to_entry = True
        trading_bot.min_position_usdt = 5.0

        trading_bot.execution_engine.execute_close = AsyncMock(
            return_value={"success": True, "orderId": "partial456"}
        )
        trading_bot.execution_engine.update_sl = AsyncMock(return_value=True)

        pos = position_class(
            symbol="BTCUSDT",
            side="SELL",
            entry_price=62000.0,
            qty=0.1,
            stop_loss=62500.0,
            take_profit=61200.0,
            origin="manual",
            partial_tp_price=61600.0,  # 50% path from 62000 to 61200
            partial_close_fraction=0.5,
            total_tp_price=61200.0,
        )
        trading_bot.position_manager.add(pos)

        # Price at partial TP level for short
        closed = await trading_bot._maybe_execute_partial_tp(pos, 61550.0)

        assert closed, "Should have executed partial TP for short"
        remaining = trading_bot.position_manager.get("BTCUSDT")
        assert remaining.partial_tp_done, "partial_tp_done flag should be True"
        # For short, SL should be moved down to entry
        assert remaining.stop_loss <= remaining.entry_price, "SL should be at entry for short"

    @pytest.mark.asyncio
    async def test_partial_tp_skipped_when_disabled(self, trading_bot, position_class):
        """Verify partial TP is skipped when disabled."""
        trading_bot.partial_tp_enabled = False

        pos = position_class(
            symbol="ETHUSDT",
            side="BUY",
            entry_price=3000.0,
            qty=1.0,
            stop_loss=2940.0,
            take_profit=3120.0,
            partial_tp_price=3060.0,
        )
        trading_bot.position_manager.add(pos)

        closed = await trading_bot._maybe_execute_partial_tp(pos, 3065.0)
        assert not closed, "Should not execute partial TP when disabled"

    @pytest.mark.asyncio
    async def test_partial_tp_skipped_when_already_done(self, trading_bot, position_class):
        """Verify partial TP is skipped when already executed."""
        trading_bot.partial_tp_enabled = True

        pos = position_class(
            symbol="ETHUSDT",
            side="BUY",
            entry_price=3000.0,
            qty=0.5,
            stop_loss=3000.0,
            take_profit=3120.0,
            partial_tp_price=3060.0,
            partial_tp_done=True,  # Already done
        )
        trading_bot.position_manager.add(pos)

        closed = await trading_bot._maybe_execute_partial_tp(pos, 3100.0)
        assert not closed, "Should not execute partial TP when already done"


# === Module: Portfolio Total TP Tests ===

class TestPortfolioTotalTP:
    """Tests for portfolio-wide total TP: close all positions when aggregate PnL target is reached."""

    @pytest.mark.asyncio
    async def test_portfolio_tp_triggers_all_positions_closed(self, trading_bot, position_class):
        """Verify all positions are closed when portfolio PnL target is reached."""
        trading_bot.portfolio_tp_enabled = True
        trading_bot.portfolio_tp_target_pct = 2.0
        trading_bot.controls.set_balance(1000.0)

        trading_bot.position_manager.add(position_class(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=0.01,
            stop_loss=61700.0, take_profit=62600.0
        ))
        trading_bot.position_manager.add(position_class(
            symbol="ETHUSDT", side="SELL", entry_price=3000.0, qty=1.0,
            stop_loss=3060.0, take_profit=2880.0
        ))

        calls = []

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            calls.append((symbol, side, position_idx))
            return {"success": True, "orderId": f"{symbol}-closed"}

        async def fake_get_price(symbol):
            return 62500.0 if symbol == "BTCUSDT" else 2950.0

        trading_bot.execution_engine.execute_close = fake_execute_close
        trading_bot.client.get_price = fake_get_price

        # Total unrealized = $25 which is 2.5% of $1000 (above 2% target)
        await trading_bot._check_portfolio_take_profit(25.0)

        assert len(calls) == 2, f"Should close 2 positions, got {len(calls)}"
        assert trading_bot.position_manager.count() == 0, "All positions should be removed"

    @pytest.mark.asyncio
    async def test_portfolio_tp_not_triggered_below_target(self, trading_bot, position_class):
        """Verify positions are not closed when PnL is below target."""
        trading_bot.portfolio_tp_enabled = True
        trading_bot.portfolio_tp_target_pct = 2.0
        trading_bot.controls.set_balance(1000.0)

        trading_bot.position_manager.add(position_class(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=0.01,
            stop_loss=61700.0, take_profit=62600.0
        ))

        calls = []

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            calls.append(symbol)
            return {"success": True}

        trading_bot.execution_engine.execute_close = fake_execute_close

        # Total unrealized = $15 which is 1.5% (below 2% target)
        await trading_bot._check_portfolio_take_profit(15.0)

        assert len(calls) == 0, "Should not close any positions below target"
        assert trading_bot.position_manager.count() == 1, "Position should remain"

    @pytest.mark.asyncio
    async def test_portfolio_tp_disabled(self, trading_bot, position_class):
        """Verify portfolio TP does nothing when disabled."""
        trading_bot.portfolio_tp_enabled = False
        trading_bot.controls.set_balance(1000.0)

        trading_bot.position_manager.add(position_class(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=0.01,
            stop_loss=61700.0, take_profit=62600.0
        ))

        calls = []
        trading_bot.execution_engine.execute_close = AsyncMock(side_effect=lambda *a, **k: calls.append(a))

        await trading_bot._check_portfolio_take_profit(50.0)  # 5% which is above any reasonable target

        assert len(calls) == 0, "Should not execute when disabled"

    @pytest.mark.asyncio
    async def test_portfolio_tp_with_no_positions(self, trading_bot):
        """Verify portfolio TP handles empty position manager gracefully."""
        trading_bot.portfolio_tp_enabled = True
        trading_bot.controls.set_balance(1000.0)

        # Should not raise any exceptions
        await trading_bot._check_portfolio_take_profit(25.0)
        assert trading_bot.position_manager.count() == 0


# === Module: ExecutionEngine/BybitClient position_idx Support ===

class TestPositionIdxSupport:
    """Tests for position_idx parameter support in execution methods."""

    def test_execute_close_signature_has_position_idx(self):
        """Verify execute_close accepts position_idx parameter."""
        import inspect
        from engine.execution_engine import ExecutionEngine

        sig = inspect.signature(ExecutionEngine.execute_close)
        params = sig.parameters
        assert "position_idx" in params, "execute_close should accept position_idx"
        assert params["position_idx"].default == 0, "position_idx default should be 0"

    def test_update_sl_signature_has_position_idx(self):
        """Verify update_sl accepts position_idx parameter."""
        import inspect
        from engine.execution_engine import ExecutionEngine

        sig = inspect.signature(ExecutionEngine.update_sl)
        params = sig.parameters
        assert "position_idx" in params, "update_sl should accept position_idx"

    def test_update_tp_signature_has_position_idx(self):
        """Verify update_tp accepts position_idx parameter."""
        import inspect
        from engine.execution_engine import ExecutionEngine

        sig = inspect.signature(ExecutionEngine.update_tp)
        params = sig.parameters
        assert "position_idx" in params, "update_tp should accept position_idx"

    def test_bybit_client_close_position_has_position_idx(self):
        """Verify BybitClient.close_position accepts position_idx."""
        import inspect
        from exchange.bybit_client import BybitClient

        sig = inspect.signature(BybitClient.close_position)
        params = sig.parameters
        assert "position_idx" in params, "close_position should accept position_idx"

    def test_bybit_client_update_stop_loss_has_position_idx(self):
        """Verify BybitClient.update_stop_loss accepts position_idx."""
        import inspect
        from exchange.bybit_client import BybitClient

        sig = inspect.signature(BybitClient.update_stop_loss)
        params = sig.parameters
        assert "position_idx" in params, "update_stop_loss should accept position_idx"

    def test_bybit_client_update_take_profit_has_position_idx(self):
        """Verify BybitClient.update_take_profit accepts position_idx."""
        import inspect
        from exchange.bybit_client import BybitClient

        sig = inspect.signature(BybitClient.update_take_profit)
        params = sig.parameters
        assert "position_idx" in params, "update_take_profit should accept position_idx"


# === Module: Position Manager Tests ===

class TestPositionManager:
    """Tests for Position dataclass and PositionManager operations."""

    def test_position_has_new_fields(self, position_class):
        """Verify Position dataclass has all new required fields."""
        pos = position_class(
            symbol="TEST",
            side="BUY",
            entry_price=100.0,
            qty=1.0,
            stop_loss=95.0,
            take_profit=110.0,
        )
        assert hasattr(pos, "partial_tp_price"), "Position should have partial_tp_price"
        assert hasattr(pos, "partial_tp_done"), "Position should have partial_tp_done"
        assert hasattr(pos, "partial_close_fraction"), "Position should have partial_close_fraction"
        assert hasattr(pos, "total_tp_price"), "Position should have total_tp_price"
        assert hasattr(pos, "position_idx"), "Position should have position_idx"
        assert hasattr(pos, "origin"), "Position should have origin field"

    def test_position_is_long_property(self, position_class):
        """Verify is_long property works correctly."""
        long_pos = position_class(
            symbol="TEST", side="BUY", entry_price=100.0, qty=1.0,
            stop_loss=95.0, take_profit=110.0
        )
        short_pos = position_class(
            symbol="TEST", side="SELL", entry_price=100.0, qty=1.0,
            stop_loss=105.0, take_profit=90.0
        )
        assert long_pos.is_long is True
        assert short_pos.is_long is False

    def test_position_manager_reduce(self, position_manager, position_class):
        """Verify reduce operation updates position quantity."""
        pos = position_class(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=1.0,
            stop_loss=61000.0, take_profit=63000.0
        )
        position_manager.add(pos)

        position_manager.reduce("BTCUSDT", 0.5)
        remaining = position_manager.get("BTCUSDT")

        assert remaining.qty == 0.5, "Quantity should be reduced by 0.5"

    def test_position_manager_reduce_removes_zero_qty(self, position_manager, position_class):
        """Verify position is removed when quantity becomes zero."""
        pos = position_class(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=1.0,
            stop_loss=61000.0, take_profit=63000.0
        )
        position_manager.add(pos)

        position_manager.reduce("BTCUSDT", 1.0)
        assert position_manager.get("BTCUSDT") is None, "Position should be removed when qty=0"


# === Module: Edge Cases and Regression Tests ===

class TestEdgeCasesAndRegression:
    """Edge cases and regression tests for the new features."""

    @pytest.mark.asyncio
    async def test_partial_tp_with_min_position_size(self, trading_bot, position_class):
        """Verify partial TP respects minimum position size."""
        trading_bot.partial_tp_enabled = True
        trading_bot.partial_tp_close_fraction = 0.5
        trading_bot.min_position_usdt = 10.0

        # Position with small value that would result in < min_position_usdt after partial close
        pos = position_class(
            symbol="ETHUSDT",
            side="BUY",
            entry_price=3000.0,
            qty=0.003,  # $9 notional
            stop_loss=2940.0,
            take_profit=3120.0,
            partial_tp_price=3060.0,
            partial_close_fraction=0.5,
        )
        trading_bot.position_manager.add(pos)

        closed = await trading_bot._maybe_execute_partial_tp(pos, 3065.0)

        # Should skip partial TP because 50% of $9 = $4.5 which is below $10 minimum
        assert not closed, "Should skip partial TP for positions below minimum size"
        remaining = trading_bot.position_manager.get("ETHUSDT")
        assert remaining.partial_tp_done, "partial_tp_done should be set to prevent future attempts"

    def test_derive_manual_position_levels_long(self, trading_bot):
        """Verify SL/TP derivation for manual long positions."""
        derived_sl, derived_tp, partial_tp = trading_bot._derive_manual_position_levels(
            side="BUY",
            entry_price=62000.0,
            stop_loss=0,
            take_profit=0,
            atr_val=300.0,
        )

        assert derived_sl < 62000.0, "SL should be below entry for long"
        assert derived_tp > 62000.0, "TP should be above entry for long"
        assert derived_sl > 0, "SL should be derived"
        assert derived_tp > 0, "TP should be derived"

    def test_derive_manual_position_levels_short(self, trading_bot):
        """Verify SL/TP derivation for manual short positions."""
        derived_sl, derived_tp, partial_tp = trading_bot._derive_manual_position_levels(
            side="SELL",
            entry_price=62000.0,
            stop_loss=0,
            take_profit=0,
            atr_val=300.0,
        )

        assert derived_sl > 62000.0, "SL should be above entry for short"
        assert derived_tp < 62000.0, "TP should be below entry for short"

    def test_config_has_new_sections(self):
        """Verify config.yaml has all new feature sections."""
        from core.config import BotConfig
        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))

        # position_sync section
        assert cfg.get("position_sync", "adopt_all_positions") is not None
        assert cfg.get("position_sync", "preserve_existing_sl_tp") is not None

        # partial_tp section
        assert cfg.get("partial_tp", "enabled") is not None
        assert cfg.get("partial_tp", "trigger_progress") is not None
        assert cfg.get("partial_tp", "close_fraction") is not None
        assert cfg.get("partial_tp", "move_stop_to_entry") is not None

        # portfolio_tp section
        assert cfg.get("portfolio_tp", "enabled") is not None
        assert cfg.get("portfolio_tp", "target_profit_pct") is not None

    def test_trading_bot_initializes_new_config_values(self, trading_bot):
        """Verify TradingBot reads new config values on initialization."""
        assert hasattr(trading_bot, "adopt_all_positions")
        assert hasattr(trading_bot, "preserve_existing_sl_tp")
        assert hasattr(trading_bot, "partial_tp_enabled")
        assert hasattr(trading_bot, "partial_tp_trigger_progress")
        assert hasattr(trading_bot, "partial_tp_close_fraction")
        assert hasattr(trading_bot, "partial_tp_move_stop_to_entry")
        assert hasattr(trading_bot, "portfolio_tp_enabled")
        assert hasattr(trading_bot, "portfolio_tp_target_pct")

    def test_position_controls_dict_includes_partial_tp(self, position_manager, position_class):
        """Verify to_controls_dict includes partial TP info."""
        pos = position_class(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=62000.0,
            qty=0.01,
            stop_loss=61700.0,
            take_profit=62600.0,
            partial_tp_price=62300.0,
            partial_tp_done=True,
            origin="manual",
        )
        position_manager.add(pos)

        controls_dict = position_manager.to_controls_dict()

        assert "BTCUSDT" in controls_dict
        assert "partial_tp_price" in controls_dict["BTCUSDT"]
        assert "partial_tp_done" in controls_dict["BTCUSDT"]
        assert "origin" in controls_dict["BTCUSDT"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
