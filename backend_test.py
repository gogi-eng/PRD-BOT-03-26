#!/usr/bin/env python3
"""Backend smoke tests for the AI-fund trading bot architecture."""
from __future__ import annotations

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
        assert cfg.get("entry", "transformer_threshold") == 0.62
        assert cfg.get("entry", "min_rr_ratio") == 1.8
        assert cfg.get("risk", "max_daily_loss_pct") == 2.5
        assert cfg.get("heatmap", "cluster_step") == 20
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
        signal = engine.generate_signal("BTCUSDT", current_price, market, regime, transformer, orderflow, liq, 110.0)

        assert signal.should_enter
        assert signal.side == "SELL"
        assert signal.rr_ratio >= 1.8
        assert signal.metadata["liq_distance_pct"] <= 0.4

        weak_signal = engine.generate_signal(
            "BTCUSDT",
            current_price,
            market,
            regime,
            transformer,
            type("WeakOrderflow", (), {**orderflow.__dict__, "bearish_ratio": 1.05, "bullish_ratio": 0.8, "spread_pct": orderflow.spread_pct})(),
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
        return True

    def test_trading_bot_initialization(self):
        from main import TradingBot

        bot = TradingBot()
        assert bot.controls.get_strategy_mode_display() == "Transformer + Heatmap + Orderflow"
        assert bot.entry_engine.transformer_threshold == 0.62
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
            ("Feature engineering + transformer", self.test_feature_engineering_and_transformer),
            ("Entry engine strict conditions", self.test_entry_engine_strict_conditions),
            ("Allocator + RL agent", self.test_allocator_and_rl),
            ("Position manager + exit engine + controls", self.test_position_exit_and_controls),
            ("Execution engine signatures", self.test_execution_engine_signatures),
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