#!/usr/bin/env python3
"""Backend smoke tests for the AI-fund trading bot architecture."""
from __future__ import annotations

import asyncio
import inspect
import sys
import traceback
from pathlib import Path

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


class BotTester:
    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.errors: list[str] = []

    def run_test(self, name: str, func):
        self.tests_run += 1
        print(f"\n🔍 Testing {name}...")
        try:
            result = func()
            if result:
                self.tests_passed += 1
                print(f"✅ PASSED: {name}")
            else:
                self.errors.append(f"{name}: returned False")
                print(f"❌ FAILED: {name}")
        except Exception as exc:
            self.errors.append(f"{name}: {exc}")
            print(f"❌ ERROR: {name} - {exc}")
            traceback.print_exc()

    @staticmethod
    def _build_trend_klines(length: int = 180, start: float = 62000.0, direction: str = "up"):
        klines = []
        price = start
        for i in range(length):
            drift = 35.0 if direction == "up" else -35.0
            pulse = 8.0 if i % 11 == 0 else -4.0 if i % 7 == 0 else 0.0
            price += drift + pulse
            klines.append(
                {
                    "open": price - 18 if direction == "up" else price + 18,
                    "high": price + 45,
                    "low": price - 45,
                    "close": price,
                    "volume": 1500 + i * 12,
                }
            )
        return klines

    def test_imports(self):
        from analysis.ai_analyzer import AITradeAnalyzer
        from analysis.feature_engineering import FeatureEngineer
        from analysis.liquidation_clusters import LiquidationClusterDetector
        from analysis.market_analyzer import MarketAnalyzer
        from analysis.market_regime_ai import MarketRegimeAI
        from analysis.orderflow_analyzer import OrderflowAnalyzer
        from analysis.transformer_model import TransformerPriceModel
        from core.config import BotConfig
        from core.live_controls import LiveControls
        from core.security import SecureStore
        from engine.capital_allocator import MultiSymbolCapitalAllocator
        from engine.entry_engine import EntryEngine
        from engine.execution_engine import ExecutionEngine
        from engine.exit_engine import ExitEngine
        from engine.position_manager import PositionManager
        from engine.risk_manager import RiskGuard
        from engine.rl_position_agent import RLPositionAgent
        from exchange.bybit_client import BybitClient
        from main import TradingBot
        from tg.controller import TelegramController
        from utils import ATRCalculator

        imports = [
            AITradeAnalyzer, FeatureEngineer, LiquidationClusterDetector, MarketAnalyzer,
            MarketRegimeAI, OrderflowAnalyzer, TransformerPriceModel, BotConfig,
            LiveControls, SecureStore, MultiSymbolCapitalAllocator, EntryEngine,
            ExecutionEngine, ExitEngine, PositionManager, RiskGuard, RLPositionAgent,
            BybitClient, TradingBot, TelegramController, ATRCalculator,
        ]
        assert all(imports)
        return True

    def test_config_loading(self):
        from core.config import BotConfig

        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        assert cfg.get("bot", "candle_interval") == "1"
        assert cfg.get("entry", "transformer_threshold") == 0.56
        assert cfg.get("entry", "min_rr_ratio") == 1.4
        assert cfg.get("entry", "min_target_profit_pct") == 1.2
        assert cfg.get("risk", "max_daily_loss_pct") == 2.5
        assert cfg.get("heatmap", "cluster_step") == 20
        assert cfg.get("partial_tp", "close_fraction") == 0.5
        assert cfg.get("portfolio_tp", "enabled") is False
        assert cfg.get("basket_profit_guard", "enabled") is True
        assert cfg.get("profit_drawdown_guard", "activation_profit_pct") == 3.0
        return True

    def test_market_analysis_and_regime(self):
        from analysis.market_analyzer import MarketAnalyzer, TrendDirection, MarketRegime
        from analysis.market_regime_ai import MarketRegimeAI

        klines = self._build_trend_klines(direction="up")
        analyzer = MarketAnalyzer()
        analysis = analyzer.analyze(klines, klines[-140:])
        regime = MarketRegimeAI().classify(analysis)

        assert analysis.can_trade
        assert analysis.trend == TrendDirection.BULLISH
        assert analysis.htf_trend == TrendDirection.BULLISH
        assert analysis.adx >= 20
        assert regime.regime in {MarketRegime.TREND, MarketRegime.BREAKOUT}
        assert 0 < regime.confidence <= 1
        return True

    def test_orderflow_analyzer(self):
        from analysis.orderflow_analyzer import OrderflowAnalyzer

        orderbook = {
            "bids": [["62100", "200"], ["62090", "180"], ["62080", "170"]],
            "asks": [["62110", "90"], ["62120", "85"], ["62130", "80"]],
        }
        trades = [{"price": 62100, "size": 25 + i, "side": "Buy" if i < 12 else "Sell", "timestamp": i} for i in range(20)]
        snapshot = OrderflowAnalyzer().analyze(orderbook, trades)

        assert snapshot.orderbook_ratio > 1.0
        assert snapshot.bullish_ratio > 1.0
        assert snapshot.spread_pct > 0
        return True

    def test_liquidation_heatmap(self):
        from analysis.liquidation_clusters import LiquidationClusterDetector

        current_price = 62350.0
        events = [
            {"price": 62440, "size": 230000, "side": "Sell", "timestamp": 1},
            {"price": 62480, "size": 400000, "side": "Sell", "timestamp": 2},
            {"price": 62510, "size": 800000, "side": "Sell", "timestamp": 3},
            {"price": 62210, "size": 120000, "side": "Buy", "timestamp": 4},
        ]
        analysis = LiquidationClusterDetector(cluster_step=20).analyze(current_price, events)

        assert analysis.max_liq_cluster_above is not None
        assert analysis.max_liq_cluster_above.level == 62500.0
        assert analysis.target_level == 62500.0
        assert analysis.signal == 1
        return True

    def test_liquidation_heatmap_low_price_symbol(self):
        from analysis.liquidation_clusters import LiquidationClusterDetector

        current_price = 4.19
        events = [
            {"price": 4.24, "size": 120000, "side": "Sell", "timestamp": 1},
            {"price": 4.26, "size": 180000, "side": "Sell", "timestamp": 2},
            {"price": 4.11, "size": 90000, "side": "Buy", "timestamp": 3},
        ]
        analysis = LiquidationClusterDetector(cluster_step=20).analyze(current_price, events)

        assert analysis.target_level > current_price
        assert analysis.distance_to_target_pct > 0
        assert analysis.signal == 1
        return True

    def test_feature_engineering_and_transformer(self):
        from analysis.feature_engineering import FeatureEngineer
        from analysis.liquidation_clusters import LiquidationClusterDetector
        from analysis.market_analyzer import MarketAnalyzer
        from analysis.market_regime_ai import MarketRegimeAI
        from analysis.orderflow_analyzer import OrderflowAnalyzer
        from analysis.transformer_model import TransformerPriceModel

        klines = self._build_trend_klines(direction="down")
        market = MarketAnalyzer().analyze(klines, klines[-140:])
        regime = MarketRegimeAI().classify(market)
        orderbook = {"bids": [["55640", "90"]], "asks": [["55660", "210"]]}
        trades = [{"price": 55650, "size": 35 + i, "side": "Sell" if i < 14 else "Buy", "timestamp": i} for i in range(20)]
        orderflow = OrderflowAnalyzer().analyze(orderbook, trades)
        liq = LiquidationClusterDetector().analyze(klines[-1]["close"], [
            {"price": klines[-1]["close"] - 90, "size": 400000, "side": "Buy", "timestamp": 1},
            {"price": klines[-1]["close"] - 110, "size": 350000, "side": "Buy", "timestamp": 2},
        ])
        batch = FeatureEngineer(sequence_length=128).build(klines, orderflow, liq, 120.0)
        prediction = TransformerPriceModel(sequence_length=128).predict(batch, regime, orderflow, liq)

        total = prediction.prob_up + prediction.prob_down + prediction.prob_flat
        assert batch.sequence_length == 128
        assert batch.feature_count >= 10
        assert abs(total - 1.0) < 0.01
        assert prediction.prob_down > prediction.prob_up
        return True

    def test_entry_engine_strict_conditions(self):
        from analysis.feature_engineering import FeatureEngineer
        from analysis.liquidation_clusters import LiquidationClusterDetector
        from analysis.market_analyzer import MarketAnalyzer
        from analysis.market_regime_ai import MarketRegimeAI
        from analysis.orderflow_analyzer import OrderflowAnalyzer
        from analysis.transformer_model import TransformerPriceModel
        from core.config import BotConfig
        from engine.entry_engine import EntryEngine

        klines = self._build_trend_klines(direction="down")
        current_price = klines[-1]["close"]
        market = MarketAnalyzer().analyze(klines, klines[-140:])
        regime = MarketRegimeAI().classify(market)
        orderbook = {"bids": [[str(current_price - 10), "90"]], "asks": [[str(current_price + 10), "220"]]}
        trades = [{"price": current_price, "size": 45 + i, "side": "Sell" if i < 16 else "Buy", "timestamp": i} for i in range(22)]
        orderflow = OrderflowAnalyzer().analyze(orderbook, trades)
        liq = LiquidationClusterDetector().analyze(current_price, [
            {"price": current_price - 70, "size": 700000, "side": "Buy", "timestamp": 1},
            {"price": current_price - 90, "size": 500000, "side": "Buy", "timestamp": 2},
            {"price": current_price + 180, "size": 100000, "side": "Sell", "timestamp": 3},
        ])
        features = FeatureEngineer().build(klines, orderflow, liq, 110.0)
        transformer = TransformerPriceModel().predict(features, regime, orderflow, liq)
        engine = EntryEngine(BotConfig.load(str(BOT_DIR / "config.yaml")))
        signal = engine.generate_signal("BTCUSDT", klines, current_price, market, regime, transformer, orderflow, liq, 110.0)

        assert signal.should_enter
        assert signal.side == "SELL"
        assert signal.rr_ratio >= 1.4
        assert signal.metadata["liq_distance_pct"] <= 0.55
        assert abs(signal.take_profit - current_price) / current_price * 100 >= 1.19

        weak_signal = engine.generate_signal(
            "BTCUSDT",
            klines,
            current_price,
            market,
            regime,
            transformer,
            type("WeakOrderflow", (), {**orderflow.__dict__, "bearish_ratio": 0.98, "bullish_ratio": 0.95, "spread_pct": orderflow.spread_pct, "volume_spike": 1.0})(),
            liq,
            110.0,
        )
        assert not weak_signal.should_enter
        return True

    def test_allocator_and_rl(self):
        from engine.capital_allocator import MultiSymbolCapitalAllocator
        from engine.position_manager import Position
        from engine.rl_position_agent import RLAction, RLPositionAgent

        ranked = MultiSymbolCapitalAllocator().allocate([
            {"symbol": "BTCUSDT", "signal_strength": 1.6, "liquidity": 2_000_000, "volatility": 0.01, "spread": 0.0002},
            {"symbol": "ETHUSDT", "signal_strength": 0.8, "liquidity": 900_000, "volatility": 0.02, "spread": 0.0005},
        ])
        assert ranked[0]["symbol"] == "BTCUSDT"
        assert ranked[0]["capital_weight"] > ranked[1]["capital_weight"]

        position = Position(symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=0.01, stop_loss=61700.0, take_profit=62600.0)
        decision = RLPositionAgent().decide(position, {
            "trend_bias": 1,
            "volatility": 0.01,
            "pnl_pct": 2.8,
            "liq_signal": 1,
            "orderflow_edge": 0.7,
            "transformer_edge": 0.5,
        })
        assert decision.action in {RLAction.HOLD, RLAction.ADD, RLAction.REDUCE, RLAction.CLOSE}

        tiny_profit = RLPositionAgent().decide(position, {
            "trend_bias": -1,
            "volatility": 0.04,
            "pnl_pct": 0.01,
            "liq_signal": -1,
            "orderflow_edge": -1.0,
            "transformer_edge": -1.0,
        })
        assert tiny_profit.action != RLAction.CLOSE
        return True

    def test_position_exit_and_controls(self):
        from core.live_controls import LiveControls
        from engine.exit_engine import ExitEngine, ExitReason
        from engine.position_manager import Position, PositionManager

        manager = PositionManager()
        pos = Position(symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=0.01, stop_loss=61700.0, take_profit=62540.0, heatmap_target=62500.0)
        manager.add(pos)
        manager.increase("BTCUSDT", 0.005, 62100.0)
        assert manager.get("BTCUSDT").qty > 0.01

        exit_engine = ExitEngine(trailing_activation_atr=0.5, trailing_distance_atr=1.0)
        exit_engine.initialize_position(manager.get("BTCUSDT"), 120.0, protective_liq_level=61680.0)
        activated = exit_engine.update_trailing(manager.get("BTCUSDT"), 62250.0)
        assert activated
        should_exit, reason, _ = exit_engine.check_exit(manager.get("BTCUSDT"), 61670.0, 120.0, protective_level=61680.0)
        assert should_exit and reason == ExitReason.LIQUIDATION_STOP

        manual = Position(symbol="ETHUSDT", side="BUY", entry_price=3000.0, qty=1.0, stop_loss=2940.0, take_profit=3120.0, origin="manual")
        manual.bars_since_entry = 20
        should_exit, reason, _ = exit_engine.check_exit(manual, 3002.0, 120.0, allow_early_exit=False)
        assert not should_exit and reason is None

        controls = LiveControls()
        controls.set_positions(manager.to_controls_dict())
        controls.set_balance(1000.0)
        controls.set_unrealized_pnl(12.5)
        controls.set_candidates([{"symbol": "BTCUSDT", "signal_strength": 1.4, "capital_weight": 0.7}])
        assert "BTCUSDT" in controls.positions_report()
        assert "HEATMAP" in controls.heatmap_report() or "Нет данных" in controls.heatmap_report()
        return True

    def test_execution_engine_signatures(self):
        from engine.execution_engine import ExecutionEngine

        params = inspect.signature(ExecutionEngine.execute_entry).parameters
        assert "preferred_price" in params
        assert "reason" in params
        add_params = inspect.signature(ExecutionEngine.execute_add).parameters
        assert "reason" in add_params
        close_params = inspect.signature(ExecutionEngine.execute_close).parameters
        assert "position_idx" in close_params
        return True

    def test_manual_position_sync_and_take_profit_management(self):
        from engine.position_manager import Position
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None

        klines = self._build_trend_klines(length=140, start=62000.0, direction="up")

        async def fake_get_klines(symbol, interval, limit):
            return klines[-limit:]

        async def fake_update_sl(symbol, new_sl, position_idx=0):
            return True

        async def fake_update_tp(symbol, new_tp, position_idx=0):
            return True

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            return {"success": True, "orderId": "partial", "error": "", "executed_qty": qty or 0.0, "avg_price": 62500.0}

        bot.client.get_klines = fake_get_klines
        bot.execution_engine.update_sl = fake_update_sl
        bot.execution_engine.update_tp = fake_update_tp
        bot.execution_engine.execute_close = fake_execute_close

        async def scenario():
            await bot._sync_exchange_position(
                {
                    "symbol": "BTCUSDT",
                    "size": "0.02",
                    "avgPrice": "62000",
                    "markPrice": "62400",
                    "side": "Buy",
                    "stopLoss": "61800",
                    "takeProfit": "62600",
                    "positionIdx": 1,
                    "unrealisedPnl": "8.5",
                }
            )
            adopted = bot.position_manager.get("BTCUSDT")
            assert adopted is not None
            assert adopted.origin == "manual"
            assert adopted.position_idx == 1
            assert adopted.stop_loss == 61800
            assert adopted.take_profit == 62600
            assert adopted.external_tp_locked
            assert adopted.partial_tp_price == 0.0
            assert adopted.trailing_activation_price >= adopted.entry_price * 1.03
            assert adopted.trailing_distance > 0

            partial = Position(
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
            bot.position_manager.add(partial)
            closed = await bot._maybe_execute_partial_tp(partial, 3065.0)
            assert closed
            remaining = bot.position_manager.get("ETHUSDT")
            assert remaining is not None
            assert round(remaining.qty, 4) == 0.5
            assert remaining.partial_tp_done
            assert remaining.stop_loss >= remaining.entry_price

        asyncio.run(scenario())
        return True

    def test_portfolio_total_tp_closes_all_positions(self):
        from engine.position_manager import Position
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None
        bot.controls.set_balance(1000.0)
        bot.position_manager.add(Position(symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=0.01, stop_loss=61700.0, take_profit=62600.0))
        bot.position_manager.add(Position(symbol="ETHUSDT", side="SELL", entry_price=3000.0, qty=1.0, stop_loss=3060.0, take_profit=2880.0))

        calls = []

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            calls.append((symbol, side, position_idx))
            return {"success": True, "orderId": f"{symbol}-closed", "error": ""}

        async def fake_get_price(symbol):
            return 62500.0 if symbol == "BTCUSDT" else 2950.0

        bot.execution_engine.execute_close = fake_execute_close
        bot.client.get_price = fake_get_price

        asyncio.run(bot._check_portfolio_take_profit(25.0))
        assert len(calls) == 0
        assert bot.position_manager.count() == 2

        bot.portfolio_tp_enabled = True
        asyncio.run(bot._check_portfolio_take_profit(25.0))
        assert len(calls) == 2
        assert bot.position_manager.count() == 0
        return True

    def test_manual_mode_configuration(self):
        from main import TradingBot

        bot = TradingBot()
        assert bot.manual_rl_enabled is False
        assert bot.manual_preserve_existing_tp is True
        assert bot.manual_trailing_activation_atr > bot.exit_engine.trailing_activation_atr
        assert bot.manual_trailing_distance_atr > bot.exit_engine.trailing_distance_atr
        assert bot.basket_profit_guard_enabled is True
        assert bot.portfolio_tp_enabled is False
        assert bot.profit_drawdown_guard_enabled is True
        assert bot.profit_drawdown_activation_pct == 3.0
        return True

    def test_synthetic_heatmap_fallback_builds_context(self):
        from main import TradingBot

        bot = TradingBot()
        klines = []
        price = 4.10
        for i in range(50):
            price += 0.01 if i % 5 else 0.015
            klines.append({
                "open": price - 0.01,
                "high": price + 0.03,
                "low": price - 0.025,
                "close": price,
                "volume": 50000 + i * 1200,
            })
        liq = bot._resolve_liquidation_context("TRUMPUSDT", klines[-1]["close"], klines)
        assert liq.target_level > 0
        assert liq.distance_to_target_pct >= 0
        return True

    def test_directional_heatmap_fallback_builds_target_without_events(self):
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        from main import TradingBot

        bot = TradingBot()
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=0.8,
        )
        orderflow = OrderflowSnapshot(bullish_ratio=1.15, bearish_ratio=0.92, imbalance_score=0.2)
        liq = bot._build_directional_liq_fallback(4.19, market, orderflow, 0.08)
        assert liq.target_level > 4.19
        assert liq.signal == 1
        return True

    def test_profit_drawdown_guard_activates_at_three_percent_and_closes_on_retrace(self):
        from engine.position_manager import Position
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None
        pos = Position(symbol="TRUMPUSDT", side="BUY", entry_price=4.19, qty=100, stop_loss=4.00, take_profit=4.60)
        bot._apply_profit_drawdown_profile(pos)
        assert round(pos.trailing_activation_price, 4) >= round(4.19 * 1.03, 4)

        async def scenario():
            armed, _ = await bot._check_profit_drawdown_guard(pos, 4.3160)  # ~+3%
            assert not armed
            assert pos.profit_guard_armed
            assert pos.profit_peak_pct >= 2.99

            triggered, reason = await bot._check_profit_drawdown_guard(pos, 4.3576)  # ~+4%
            assert not triggered
            assert pos.profit_peak_pct > 4.0 - 0.1

            triggered, reason = await bot._check_profit_drawdown_guard(pos, 4.3120)  # slight drop below +3%
            assert triggered
            assert "profit_drawdown_guard" in reason

        asyncio.run(scenario())
        return True

    def test_low_atr_breakout_allowed_but_pullback_blocked(self):
        from main import TradingBot
        from engine.entry_engine import EntrySignal

        bot = TradingBot()
        bot.tg = None

        async def fake_get_klines(symbol, interval, limit):
            klines = []
            price = 10.0
            for i in range(100):
                price += 0.001 if i < 95 else 0.02
                klines.append({
                    "open": price - 0.002,
                    "high": price + 0.004,
                    "low": price - 0.004,
                    "close": price,
                    "volume": 1000 + i * 10,
                })
            return klines[-limit:]

        async def fake_get_orderbook(symbol, limit=25):
            return {"bids": [["10.10", "100"]], "asks": [["10.11", "90"]], "ts": 0}

        async def fake_get_recent_trades(symbol, limit=120):
            return [{"price": 10.1, "size": 40, "side": "Buy", "timestamp": i} for i in range(limit)]

        bot.client.get_klines = fake_get_klines
        bot.client.get_orderbook = fake_get_orderbook
        bot.client.get_recent_trades = fake_get_recent_trades
        bot.client.get_liquidation_events = lambda symbol: []
        bot.atr.get_atr_pct = lambda symbol, klines: 0.05

        original_generate_signal = bot.entry_engine.generate_signal

        def breakout_signal(*args, **kwargs):
            signal = EntrySignal(should_enter=True, side="BUY", confidence=0.8, entry_price=10.1, stop_loss=10.0, take_profit=10.4, rr_ratio=2.0)
            signal.metadata = {"structure_breakout": True}
            return signal

        def pullback_signal(*args, **kwargs):
            signal = EntrySignal(should_enter=True, side="BUY", confidence=0.8, entry_price=10.1, stop_loss=10.0, take_profit=10.4, rr_ratio=2.0)
            signal.metadata = {"structure_breakout": False, "structure_pullback": True}
            return signal

        async def scenario():
            bot.entry_engine.generate_signal = breakout_signal
            signal = await bot._analyze_symbol("TESTUSDT")
            assert signal.should_enter

            bot.entry_engine.generate_signal = pullback_signal
            signal = await bot._analyze_symbol("TESTUSDT")
            assert not signal.should_enter
            assert signal.metadata.get("reject_reason") in {"atr_too_low", "no_live_heatmap_no_breakout", "htf_not_bullish", "weak_market_quality"}

        asyncio.run(scenario())
        bot.entry_engine.generate_signal = original_generate_signal
        return True

    def test_chop_without_breakout_blocked_by_market_quality_filter(self):
        from analysis.market_analyzer import MarketRegime
        from main import TradingBot
        from engine.entry_engine import EntrySignal

        bot = TradingBot()
        bot.tg = None

        async def fake_get_klines(symbol, interval, limit):
            klines = []
            price = 71000.0
            for i in range(100):
                price += 10 if i % 2 == 0 else -9
                klines.append({
                    "open": price - 6,
                    "high": price + 12,
                    "low": price - 12,
                    "close": price,
                    "volume": 1000 + i * 5,
                })
            return klines[-limit:]

        async def fake_get_orderbook(symbol, limit=25):
            return {"bids": [["71100", "100"]], "asks": [["71101", "90"]], "ts": 0}

        async def fake_get_recent_trades(symbol, limit=120):
            return [{"price": 71100, "size": 10, "side": "Sell", "timestamp": i} for i in range(limit)]

        bot.client.get_klines = fake_get_klines
        bot.client.get_orderbook = fake_get_orderbook
        bot.client.get_recent_trades = fake_get_recent_trades
        bot.client.get_liquidation_events = lambda symbol: []
        bot.atr.get_atr_pct = lambda symbol, klines: 0.30

        original_generate_signal = bot.entry_engine.generate_signal
        original_analyze = bot.market_analyzer.analyze

        def fake_signal(*args, **kwargs):
            signal = EntrySignal(should_enter=True, side="SELL", confidence=0.9, entry_price=71100, stop_loss=71350, take_profit=70000, rr_ratio=2.0)
            signal.metadata = {"structure_breakout": False, "structure_pullback": True}
            return signal

        def fake_market(*args, **kwargs):
            result = original_analyze(*args, **kwargs)
            result.regime = MarketRegime.CHOP
            result.adx = 14
            return result

        async def scenario():
            bot.entry_engine.generate_signal = fake_signal
            bot.market_analyzer.analyze = fake_market
            signal = await bot._analyze_symbol("BTCUSDT")
            assert not signal.should_enter
            assert signal.metadata.get("reject_reason") in {"chop_without_breakout", "breakout_not_confirmed_on_15m", "weak_breakout_quality"}

        asyncio.run(scenario())
        bot.entry_engine.generate_signal = original_generate_signal
        bot.market_analyzer.analyze = original_analyze
        return True

    def test_risk_guard_reset_clears_emergency(self):
        from engine.risk_manager import GuardStatus, RiskGuard

        guard = RiskGuard()
        guard.emergency_stop("Manual")
        assert guard.status == GuardStatus.EMERGENCY
        guard.reset_guard()
        assert guard.status == GuardStatus.ACTIVE
        allowed, _ = guard.can_trade()
        assert allowed
        return True

    def test_basket_profit_guard_closes_on_drawdown_with_negative_position(self):
        from engine.position_manager import Position
        from main import TradingBot

        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None
        bot.position_manager.add(Position(symbol="BTCUSDT", side="BUY", entry_price=62000.0, qty=0.01, stop_loss=61700.0, take_profit=62600.0, unrealized_pnl=18.0))
        bot.position_manager.add(Position(symbol="ETHUSDT", side="SELL", entry_price=3000.0, qty=1.0, stop_loss=3060.0, take_profit=2880.0, unrealized_pnl=-2.0))

        calls = []

        async def fake_execute_close(symbol, side, qty=None, reason="", position_idx=0):
            calls.append((symbol, reason))
            return {"success": True, "orderId": f"{symbol}-closed", "error": ""}

        async def fake_get_price(symbol):
            return 62500.0 if symbol == "BTCUSDT" else 2990.0

        bot.execution_engine.execute_close = fake_execute_close
        bot.client.get_price = fake_get_price

        async def scenario():
            bot.basket_profit_state.peak_profit_usdt = 20.0
            await bot._check_basket_profit_guard(15.0)

        asyncio.run(scenario())
        assert len(calls) == 2
        assert bot.position_manager.count() == 0
        return True

    def test_trading_bot_initialization(self):
        from main import TradingBot

        bot = TradingBot()
        assert bot.controls.get_strategy_mode_display() == "Transformer + Heatmap + Orderflow"
        assert bot.entry_engine.transformer_threshold == 0.56
        assert bot.allocator is not None
        assert bot.rl_agent is not None
        assert bot.feature_engineer.sequence_length == 128
        return True

    def run_all(self):
        tests = [
            ("Imports", self.test_imports),
            ("Config loading", self.test_config_loading),
            ("Market analysis + regime AI", self.test_market_analysis_and_regime),
            ("Orderflow analyzer", self.test_orderflow_analyzer),
            ("Liquidation heatmap", self.test_liquidation_heatmap),
            ("Liquidation heatmap low-price symbol", self.test_liquidation_heatmap_low_price_symbol),
            ("Feature engineering + transformer", self.test_feature_engineering_and_transformer),
            ("Entry engine strict conditions", self.test_entry_engine_strict_conditions),
            ("Allocator + RL agent", self.test_allocator_and_rl),
            ("Position manager + exit engine + controls", self.test_position_exit_and_controls),
            ("Execution engine signatures", self.test_execution_engine_signatures),
            ("Manual position sync + partial TP", self.test_manual_position_sync_and_take_profit_management),
            ("Portfolio total TP closes all", self.test_portfolio_total_tp_closes_all_positions),
            ("Manual mode configuration", self.test_manual_mode_configuration),
            ("Synthetic heatmap fallback", self.test_synthetic_heatmap_fallback_builds_context),
            ("Directional heatmap fallback", self.test_directional_heatmap_fallback_builds_target_without_events),
            ("Profit drawdown guard", self.test_profit_drawdown_guard_activates_at_three_percent_and_closes_on_retrace),
            ("Low ATR breakout allowed", self.test_low_atr_breakout_allowed_but_pullback_blocked),
            ("Chop without breakout blocked", self.test_chop_without_breakout_blocked_by_market_quality_filter),
            ("Risk guard reset clears emergency", self.test_risk_guard_reset_clears_emergency),
            ("Basket profit guard closes basket", self.test_basket_profit_guard_closes_on_drawdown_with_negative_position),
            ("TradingBot initialization", self.test_trading_bot_initialization),
        ]
        for name, func in tests:
            self.run_test(name, func)

        print("\n" + "=" * 72)
        print(f"RESULT: {self.tests_passed}/{self.tests_run} tests passed")
        if self.errors:
            print("Errors:")
            for error in self.errors:
                print(f" - {error}")
        print("=" * 72)
        return self.tests_passed == self.tests_run


if __name__ == "__main__":
    tester = BotTester()
    raise SystemExit(0 if tester.run_all() else 1)