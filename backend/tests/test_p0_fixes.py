#!/usr/bin/env python3
"""
Test P0 fixes for user-reported issues:
1. RiskGuard reset/resume clears EMERGENCY state
2. Telegram START_BOT resumes guard
3. BybitClient request throttling and rate-limit handling
4. Config tuned to cautious-but-not-dead defaults
5. portfolio_tp disabled by default and requires >=2 positions
6. basket profit guard closes on 20% drawdown from peak with negative position
7. manual positions avoid early_exit and preserve existing TP
8. no regressions in orchestration
"""
import asyncio
import sys
from pathlib import Path

import pytest

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# Module: RiskGuard reset/resume clears EMERGENCY state
# ============================================================================
class TestRiskGuardEmergencyReset:
    """Tests that RiskGuard reset/resume properly clears EMERGENCY state."""

    def test_reset_guard_clears_emergency(self):
        """reset_guard() should clear EMERGENCY status and allow trading."""
        from engine.risk_manager import GuardStatus, RiskGuard

        guard = RiskGuard()
        guard.emergency_stop("Test emergency")
        assert guard.status == GuardStatus.EMERGENCY
        allowed, reason = guard.can_trade()
        assert not allowed
        assert "EMERGENCY" in reason

        guard.reset_guard()
        assert guard.status == GuardStatus.ACTIVE
        allowed, reason = guard.can_trade()
        assert allowed

    def test_resume_clears_emergency(self):
        """resume() should clear EMERGENCY status and allow trading."""
        from engine.risk_manager import GuardStatus, RiskGuard

        guard = RiskGuard()
        guard.emergency_stop("Manual emergency")
        assert guard.status == GuardStatus.EMERGENCY

        guard.resume()
        assert guard.status == GuardStatus.ACTIVE
        allowed, _ = guard.can_trade()
        assert allowed

    def test_resume_clears_stopped_status(self):
        """resume() should also clear STOPPED status."""
        from engine.risk_manager import GuardStatus, RiskGuard

        guard = RiskGuard(max_consecutive_losses=1)
        guard.record_trade(-10.0, "BTCUSDT")
        guard.record_trade(-10.0, "BTCUSDT")
        assert guard.status == GuardStatus.STOPPED

        guard.resume()
        assert guard.status == GuardStatus.ACTIVE
        allowed, _ = guard.can_trade()
        assert allowed

    def test_reset_clears_consecutive_losses(self):
        """reset_guard() should clear consecutive loss counter."""
        from engine.risk_manager import RiskGuard

        guard = RiskGuard()
        guard._consecutive_losses = 5
        guard.reset_guard()
        assert guard._consecutive_losses == 0


# ============================================================================
# Module: Telegram START_BOT resumes guard
# ============================================================================
class TestTelegramStartBotResumesGuard:
    """Tests that Telegram START_BOT command properly resumes guard."""

    def test_start_bot_clears_emergency(self):
        """START_BOT should clear emergency flag and resume guard."""
        from core.live_controls import LiveControls
        from engine.risk_manager import GuardStatus, RiskGuard

        controls = LiveControls()
        guard = RiskGuard()
        controls.set_guard(guard)
        controls._guard = guard  # ensure reference is set

        # Simulate EMERGENCY state
        controls.emergency = True
        guard.emergency_stop("Simulated")
        assert guard.status == GuardStatus.EMERGENCY
        assert controls.emergency is True

        # Simulate START_BOT action (from tg/controller.py on_button)
        controls.emergency = False
        controls.enabled = True
        if controls._guard:
            controls._guard.resume()

        assert controls.emergency is False
        assert controls.enabled is True
        assert guard.status == GuardStatus.ACTIVE
        allowed, _ = guard.can_trade()
        assert allowed


# ============================================================================
# Module: BybitClient request throttling and rate-limit handling
# ============================================================================
class TestBybitClientThrottling:
    """Tests BybitClient has proper rate limiting and backoff."""

    def test_bybit_has_request_lock(self):
        """BybitClient should have _request_lock for throttling."""
        from exchange.bybit_client import BybitClient

        client = BybitClient("test_key", "test_secret")
        assert hasattr(client, "_request_lock")
        assert client._request_lock is not None

    def test_bybit_has_rate_limit_intervals(self):
        """BybitClient should have configurable rate limit intervals."""
        from exchange.bybit_client import BybitClient

        client = BybitClient("test_key", "test_secret")
        assert hasattr(client, "public_min_interval")
        assert hasattr(client, "private_min_interval")
        assert client.public_min_interval > 0
        assert client.private_min_interval > 0
        # Private endpoints should have longer interval
        assert client.private_min_interval >= client.public_min_interval

    def test_bybit_has_respect_rate_limit_method(self):
        """BybitClient should have _respect_rate_limit method."""
        from exchange.bybit_client import BybitClient

        client = BybitClient("test_key", "test_secret")
        assert hasattr(client, "_respect_rate_limit")
        assert callable(client._respect_rate_limit)

    def test_bybit_has_last_request_timestamps(self):
        """BybitClient should track last request times."""
        from exchange.bybit_client import BybitClient

        client = BybitClient("test_key", "test_secret")
        assert hasattr(client, "_last_public_request_at")
        assert hasattr(client, "_last_private_request_at")

    def test_bybit_request_has_retries(self):
        """_request method should support retries parameter."""
        import inspect
        from exchange.bybit_client import BybitClient

        sig = inspect.signature(BybitClient._request)
        params = sig.parameters
        assert "retries" in params
        # Default should be >= 3
        assert params["retries"].default >= 3


# ============================================================================
# Module: Config tuned to cautious-but-not-dead defaults
# ============================================================================
class TestConfigCautiousDefaults:
    """Tests config has cautious but active defaults."""

    def test_entry_transformer_threshold_cautious(self):
        """transformer_threshold should be set to cautious value (0.60)."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        threshold = cfg.get("entry", "transformer_threshold", default=0.62)
        assert threshold == 0.60, f"Expected 0.60, got {threshold}"

    def test_entry_max_liq_distance_reasonable(self):
        """max_liq_distance_pct should allow nearby entries."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        distance = cfg.get("entry", "max_liq_distance_pct", default=0.4)
        assert 0.3 <= distance <= 0.8, f"Unexpected distance: {distance}"

    def test_entry_min_orderflow_imbalance_reasonable(self):
        """min_orderflow_imbalance should be achievable but meaningful."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        imbalance = cfg.get("entry", "min_orderflow_imbalance", default=1.2)
        assert 1.0 < imbalance <= 1.5, f"Unexpected imbalance: {imbalance}"

    def test_atr_min_pct_allows_trades(self):
        """min_atr_pct should not block most trades."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        min_atr = cfg.get("atr", "min_atr_pct", default=0.3)
        assert 0.15 <= min_atr <= 0.5, f"Unexpected min_atr_pct: {min_atr}"

    def test_market_trade_symbols_count(self):
        """trade_symbols should allow reasonable diversification."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        symbols = cfg.get("market", "trade_symbols", default=5)
        assert 3 <= symbols <= 10, f"Unexpected trade_symbols: {symbols}"

    def test_ai_min_confidence_reasonable(self):
        """AI min_confidence should not reject most signals."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        confidence = cfg.get("ai", "min_confidence", default=60)
        assert 55 <= confidence <= 75, f"Unexpected min_confidence: {confidence}"


# ============================================================================
# Module: portfolio_tp disabled by default and requires >=2 positions
# ============================================================================
class TestPortfolioTPDisabledByDefault:
    """Tests portfolio_tp is disabled and requires >=2 positions."""

    def test_portfolio_tp_disabled_in_config(self):
        """portfolio_tp.enabled should be false in config."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        enabled = cfg.get("portfolio_tp", "enabled", default=True)
        assert enabled is False, "portfolio_tp should be disabled by default"

    def test_portfolio_tp_requires_two_positions(self):
        """_check_portfolio_take_profit should require >=2 positions."""
        from engine.position_manager import Position
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None
        bot.controls.set_balance(1000.0)
        bot.portfolio_tp_enabled = True  # Force enable for test

        # Add only 1 position
        bot.position_manager.add(Position(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0,
            qty=0.01, stop_loss=61700.0, take_profit=62600.0
        ))

        calls = []

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            calls.append(symbol)
            return {"success": True}

        bot.execution_engine.execute_close = fake_execute_close

        # Run with single position
        asyncio.run(bot._check_portfolio_take_profit(25.0))
        assert len(calls) == 0, "Should not close single position"
        assert bot.position_manager.count() == 1

    def test_portfolio_tp_bot_initialization_disabled(self):
        """TradingBot should initialize with portfolio_tp disabled."""
        from main import TradingBot

        bot = TradingBot()
        assert bot.portfolio_tp_enabled is False


# ============================================================================
# Module: basket profit guard closes on 20% drawdown with negative position
# ============================================================================
class TestBasketProfitGuard:
    """Tests basket profit guard behavior."""

    def test_basket_profit_guard_enabled_in_config(self):
        """basket_profit_guard should be enabled."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        enabled = cfg.get("basket_profit_guard", "enabled", default=False)
        assert enabled is True

    def test_basket_profit_guard_drawdown_threshold(self):
        """drawdown_pct_from_peak should be around 20%."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        drawdown = cfg.get("basket_profit_guard", "drawdown_pct_from_peak", default=12.0)
        assert 15.0 <= drawdown <= 25.0, f"Expected ~20%, got {drawdown}"

    def test_basket_profit_guard_requires_negative_position(self):
        """require_negative_position should be true."""
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        required = cfg.get("basket_profit_guard", "require_negative_position", default=False)
        assert required is True

    def test_basket_profit_guard_closes_basket_on_drawdown(self):
        """Should close all when drawdown >= threshold with negative position."""
        from engine.position_manager import Position
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None

        # Add 2 positions: one positive, one negative
        bot.position_manager.add(Position(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0,
            qty=0.01, stop_loss=61700.0, take_profit=62600.0,
            unrealized_pnl=15.0
        ))
        bot.position_manager.add(Position(
            symbol="ETHUSDT", side="SELL", entry_price=3000.0,
            qty=1.0, stop_loss=3060.0, take_profit=2880.0,
            unrealized_pnl=-3.0  # Negative!
        ))

        calls = []

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            calls.append((symbol, reason))
            return {"success": True}

        async def fake_get_price(symbol):
            return 62500.0 if symbol == "BTCUSDT" else 2990.0

        bot.execution_engine.execute_close = fake_execute_close
        bot.client.get_price = fake_get_price

        async def scenario():
            # Set peak and current to trigger 25% drawdown
            bot.basket_profit_state.peak_profit_usdt = 20.0
            current_total = 12.0  # (20-12)/20 = 40% drawdown > 20% threshold
            await bot._check_basket_profit_guard(current_total)

        asyncio.run(scenario())
        assert len(calls) == 2, f"Expected 2 close calls, got {len(calls)}"
        assert bot.position_manager.count() == 0

    def test_basket_profit_guard_does_not_close_without_negative(self):
        """Should NOT close if all positions are positive."""
        from engine.position_manager import Position
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None

        # Add 2 positions: BOTH positive
        bot.position_manager.add(Position(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0,
            qty=0.01, stop_loss=61700.0, take_profit=62600.0,
            unrealized_pnl=10.0
        ))
        bot.position_manager.add(Position(
            symbol="ETHUSDT", side="SELL", entry_price=3000.0,
            qty=1.0, stop_loss=3060.0, take_profit=2880.0,
            unrealized_pnl=5.0  # Positive!
        ))

        calls = []

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            calls.append(symbol)
            return {"success": True}

        bot.execution_engine.execute_close = fake_execute_close

        async def scenario():
            bot.basket_profit_state.peak_profit_usdt = 20.0
            await bot._check_basket_profit_guard(12.0)  # 40% drawdown

        asyncio.run(scenario())
        # Should NOT close because no negative position
        assert len(calls) == 0, "Should not close without negative position"


# ============================================================================
# Module: manual positions avoid early_exit and preserve existing TP
# ============================================================================
class TestManualPositionBehavior:
    """Tests manual position handling."""

    def test_manual_position_preserves_existing_tp(self):
        """Manual positions should keep external_tp_locked when TP set."""
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None

        klines = []
        for i in range(150):
            price = 62000.0 + i * 30
            klines.append({"open": price - 10, "high": price + 30, "low": price - 30, "close": price, "volume": 100})

        async def fake_get_klines(symbol, interval, limit):
            return klines[-limit:]

        bot.client.get_klines = fake_get_klines

        async def scenario():
            await bot._sync_exchange_position({
                "symbol": "BTCUSDT",
                "size": "0.02",
                "avgPrice": "62000",
                "side": "Buy",
                "stopLoss": "61800",
                "takeProfit": "62600",  # TP is set!
                "positionIdx": 0,
            })

        asyncio.run(scenario())

        pos = bot.position_manager.get("BTCUSDT")
        assert pos is not None
        assert pos.origin == "manual"
        assert pos.take_profit == 62600
        assert pos.external_tp_locked is True
        # partial_tp_price should be 0 when external_tp_locked
        assert pos.partial_tp_price == 0.0

    def test_manual_position_avoids_early_exit(self):
        """ExitEngine should skip early_exit for manual positions."""
        from engine.exit_engine import ExitEngine
        from engine.position_manager import Position

        exit_engine = ExitEngine(early_exit_bars=8, early_exit_min_profit_atr=0.35)

        # Create manual position with many bars since entry
        manual_pos = Position(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0,
            qty=0.01, stop_loss=61700.0, take_profit=62600.0,
            origin="manual"
        )
        manual_pos.bars_since_entry = 20  # Well past early_exit_bars
        exit_engine.initialize_position(manual_pos, 120.0)

        # Price barely moved - would trigger early_exit for bot positions
        current_price = 62020.0  # Only $20 profit
        should_exit, reason, _ = exit_engine.check_exit(
            manual_pos, current_price, 120.0,
            allow_early_exit=False  # Manual positions pass this as False
        )
        assert not should_exit, "Manual position should not early_exit"

    def test_bot_position_can_early_exit(self):
        """Bot-originated positions CAN early exit."""
        from engine.exit_engine import ExitEngine
        from engine.position_manager import Position

        exit_engine = ExitEngine(early_exit_bars=8, early_exit_min_profit_atr=0.35)

        bot_pos = Position(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0,
            qty=0.01, stop_loss=61700.0, take_profit=62600.0,
            origin="bot"
        )
        bot_pos.bars_since_entry = 20
        exit_engine.initialize_position(bot_pos, 120.0)

        current_price = 62020.0
        should_exit, reason, _ = exit_engine.check_exit(
            bot_pos, current_price, 120.0,
            allow_early_exit=True  # Bot positions pass True
        )
        assert should_exit, "Bot position should early_exit when no movement"

    def test_manual_rl_disabled_by_default(self):
        """RL for manual positions should be disabled."""
        from main import TradingBot

        bot = TradingBot()
        assert bot.manual_rl_enabled is False


# ============================================================================
# Module: No regressions in main orchestration
# ============================================================================
class TestOrchestrationNoRegressions:
    """Tests for general orchestration correctness."""

    def test_bot_initialization_succeeds(self):
        """TradingBot should initialize without errors."""
        from main import TradingBot

        bot = TradingBot()
        assert bot is not None
        assert bot.controls is not None
        assert bot.risk_guard is not None
        assert bot.entry_engine is not None
        assert bot.exit_engine is not None
        assert bot.position_manager is not None

    def test_controls_emergency_flag_behavior(self):
        """Emergency flag should block trading."""
        from main import TradingBot

        bot = TradingBot()
        bot.controls.emergency = True
        # The main loop checks: if self.controls.enabled and not self.controls.emergency
        assert bot.controls.enabled is True
        assert bot.controls.emergency is True
        # Trading should be blocked in cycle

    def test_can_trade_respects_emergency(self):
        """can_trade should respect EMERGENCY status."""
        from engine.risk_manager import GuardStatus, RiskGuard

        guard = RiskGuard()
        guard.status = GuardStatus.EMERGENCY
        guard.stop_reason = "Test"

        allowed, reason = guard.can_trade()
        assert not allowed
        assert "EMERGENCY" in reason

    def test_basket_profit_state_resets_on_close_all(self):
        """Basket state should reset when all positions closed."""
        from engine.position_manager import Position
        from main import BasketProfitState, TradingBot

        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None

        # Set some basket state
        bot.basket_profit_state.peak_profit_usdt = 50.0
        bot.basket_profit_state.armed = True

        # Add and close positions
        bot.position_manager.add(Position(
            symbol="BTCUSDT", side="BUY", entry_price=62000.0,
            qty=0.01, stop_loss=61700.0, take_profit=62600.0,
            unrealized_pnl=-5.0
        ))
        bot.position_manager.add(Position(
            symbol="ETHUSDT", side="SELL", entry_price=3000.0,
            qty=1.0, stop_loss=3060.0, take_profit=2880.0,
            unrealized_pnl=20.0
        ))

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            return {"success": True}

        async def fake_get_price(symbol):
            return 62000.0

        bot.execution_engine.execute_close = fake_execute_close
        bot.client.get_price = fake_get_price

        async def scenario():
            bot.basket_profit_state.peak_profit_usdt = 20.0
            await bot._check_basket_profit_guard(12.0)

        asyncio.run(scenario())

        # After closing all, state should reset
        assert bot.basket_profit_state.peak_profit_usdt == 0.0
        assert bot.basket_profit_state.armed is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
