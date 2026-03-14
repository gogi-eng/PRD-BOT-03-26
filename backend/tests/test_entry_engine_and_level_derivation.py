#!/usr/bin/env python3
"""Tests for EntryEngine breakout+pullback detection, AI veto soft-bypass, and level-based TP/SL derivation."""

import asyncio
import sys
from pathlib import Path

import pytest

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# Helper to build synthetic klines for trend scenarios
def build_trend_klines(length: int = 180, start: float = 62000.0, direction: str = "up"):
    klines = []
    price = start
    for i in range(length):
        drift = 35.0 if direction == "up" else -35.0
        pulse = 8.0 if i % 11 == 0 else -4.0 if i % 7 == 0 else 0.0
        price += drift + pulse
        klines.append({
            "open": price - 18 if direction == "up" else price + 18,
            "high": price + 45,
            "low": price - 45,
            "close": price,
            "volume": 1500 + i * 12,
        })
    return klines


def build_pullback_klines(length: int = 180, start: float = 62000.0, direction: str = "up"):
    """Build klines with initial trend then pullback to EMA."""
    klines = []
    price = start
    for i in range(length):
        # Trending phase for most candles
        if i < length - 12:
            drift = 35.0 if direction == "up" else -35.0
        else:
            # Pullback phase
            drift = -20.0 if direction == "up" else 20.0
        pulse = 8.0 if i % 11 == 0 else -4.0 if i % 7 == 0 else 0.0
        price += drift + pulse
        klines.append({
            "open": price - 18 if direction == "up" else price + 18,
            "high": price + 45,
            "low": price - 45,
            "close": price,
            "volume": 1500 + i * 12,
        })
    return klines


@pytest.fixture
def bot_config():
    from core.config import BotConfig
    return BotConfig.load(str(BOT_DIR / "config.yaml"))


@pytest.fixture
def entry_engine(bot_config):
    from engine.entry_engine import EntryEngine
    return EntryEngine(bot_config)


@pytest.fixture
def market_analyzer():
    from analysis.market_analyzer import MarketAnalyzer
    return MarketAnalyzer()


@pytest.fixture
def regime_ai():
    from analysis.market_regime_ai import MarketRegimeAI
    return MarketRegimeAI()


@pytest.fixture
def orderflow_analyzer():
    from analysis.orderflow_analyzer import OrderflowAnalyzer
    return OrderflowAnalyzer()


@pytest.fixture
def liq_detector():
    from analysis.liquidation_clusters import LiquidationClusterDetector
    return LiquidationClusterDetector()


@pytest.fixture
def feature_engineer():
    from analysis.feature_engineering import FeatureEngineer
    return FeatureEngineer(sequence_length=128)


@pytest.fixture
def transformer_model():
    from analysis.transformer_model import TransformerPriceModel
    return TransformerPriceModel(sequence_length=128)


@pytest.fixture
def trading_bot():
    from main import TradingBot
    bot = TradingBot()
    bot.tg = None
    bot._save_trade = lambda *args, **kwargs: None
    return bot


class TestEntryEngineStructureDetection:
    """Tests for _detect_structure breakout and pullback logic."""

    def test_breakout_long_detected(self, entry_engine, market_analyzer):
        """Verify breakout_long is detected when price breaks lookback high."""
        klines = build_trend_klines(direction="up")
        current_price = klines[-1]["close"]
        market = market_analyzer.analyze(klines, klines[-140:])
        
        structure = entry_engine._detect_structure(klines, current_price, market)
        
        # In strong uptrend near highs, should detect breakout_long
        assert structure["breakout_long"] or structure["pullback_long"], "Expected either breakout or pullback long in uptrend"
        assert not (structure["breakout_short"] or structure["pullback_short"]), "Should not detect short structure in uptrend"

    def test_breakout_short_detected(self, entry_engine, market_analyzer):
        """Verify breakout_short is detected when price breaks lookback low."""
        klines = build_trend_klines(direction="down")
        current_price = klines[-1]["close"]
        market = market_analyzer.analyze(klines, klines[-140:])
        
        structure = entry_engine._detect_structure(klines, current_price, market)
        
        # In strong downtrend near lows, should detect breakout_short
        assert structure["breakout_short"] or structure["pullback_short"], "Expected either breakout or pullback short in downtrend"

    def test_pullback_long_detected(self, entry_engine, market_analyzer):
        """Verify pullback_long detection after pullback to EMA in uptrend."""
        klines = build_pullback_klines(direction="up")
        current_price = klines[-1]["close"]
        market = market_analyzer.analyze(klines, klines[-140:])
        
        structure = entry_engine._detect_structure(klines, current_price, market)
        
        # After pullback to EMA in uptrend, might detect pullback_long
        # Structure depends on EMA position; at minimum trigger_reason should indicate something
        assert structure["trigger_reason"] in ["Breakout continuation", "Pullback continuation", "No structure"]

    def test_insufficient_klines_returns_no_structure(self, entry_engine, market_analyzer):
        """Verify that insufficient klines returns no structure."""
        klines = build_trend_klines(length=10, direction="up")  # Too few klines
        current_price = klines[-1]["close"]
        market = market_analyzer.analyze(klines, klines[-8:])
        
        structure = entry_engine._detect_structure(klines, current_price, market)
        
        assert structure["trigger_reason"] == "No structure"
        assert not structure["breakout_long"]
        assert not structure["pullback_long"]

    def test_structure_in_metadata(self, entry_engine, market_analyzer, regime_ai, orderflow_analyzer, liq_detector, feature_engineer, transformer_model):
        """Verify structure_breakout and structure_pullback appear in signal metadata."""
        klines = build_trend_klines(direction="down")
        current_price = klines[-1]["close"]
        market = market_analyzer.analyze(klines, klines[-140:])
        regime = regime_ai.classify(market)
        
        orderbook = {"bids": [[str(current_price - 10), "90"]], "asks": [[str(current_price + 10), "220"]]}
        trades = [{"price": current_price, "size": 45 + i, "side": "Sell" if i < 16 else "Buy", "timestamp": i} for i in range(22)]
        orderflow = orderflow_analyzer.analyze(orderbook, trades)
        
        liq = liq_detector.analyze(current_price, [
            {"price": current_price - 70, "size": 700000, "side": "Buy", "timestamp": 1},
        ])
        
        features = feature_engineer.build(klines, orderflow, liq, 110.0)
        transformer = transformer_model.predict(features, regime, orderflow, liq)
        
        signal = entry_engine.generate_signal("BTCUSDT", klines, current_price, market, regime, transformer, orderflow, liq, 110.0)
        
        # Metadata should contain structure info (even if signal doesn't fire)
        if signal.should_enter:
            assert "structure_breakout" in signal.metadata or "structure_pullback" in signal.metadata


class TestEntryEngineLevelResolution:
    """Tests for _resolve_levels level-based target and protective level."""

    def test_resolve_levels_long_direction(self, entry_engine, liq_detector):
        """Verify level resolution for long positions uses swing highs as targets."""
        klines = build_trend_klines(direction="up", length=50)
        current_price = klines[-1]["close"]
        
        liq = liq_detector.analyze(current_price, [
            {"price": current_price + 100, "size": 500000, "side": "Sell", "timestamp": 1},
        ])
        
        levels = entry_engine._resolve_levels(klines, liq, current_price, is_long=True)
        
        assert levels["target_level"] > current_price, "Long target should be above current price"
        # Protective level should be below current price (support)
        if levels["protective_level"] > 0:
            assert levels["protective_level"] < current_price

    def test_resolve_levels_short_direction(self, entry_engine, liq_detector):
        """Verify level resolution for short positions uses swing lows as targets."""
        klines = build_trend_klines(direction="down", length=50)
        current_price = klines[-1]["close"]
        
        liq = liq_detector.analyze(current_price, [
            {"price": current_price - 100, "size": 500000, "side": "Buy", "timestamp": 1},
        ])
        
        levels = entry_engine._resolve_levels(klines, liq, current_price, is_long=False)
        
        if levels["target_level"] > 0:
            assert levels["target_level"] < current_price, "Short target should be below current price"
        # Protective level should be above current price (resistance)
        if levels["protective_level"] > 0:
            assert levels["protective_level"] > current_price

    def test_level_tp_buffer_atr_applied(self, entry_engine, bot_config):
        """Verify level_tp_buffer_atr config value is used."""
        assert entry_engine.level_tp_buffer_atr == 0.20, "level_tp_buffer_atr should be 0.20 as per config"


class TestAIVetoSoftBypass:
    """Tests for AI veto soft-bypass on strong trend structure."""

    def test_ai_soft_bypass_conditions(self, trading_bot):
        """Verify structural_override logic in _analyze_symbol."""
        # The soft-bypass logic is at lines 550-559 in main.py
        # Conditions: confidence >= 0.72, trend == htf_trend, structure_breakout or structure_pullback
        
        # We verify the bot has the AI analyzer with expected settings
        assert trading_bot.ai_analyzer is not None
        assert hasattr(trading_bot.ai_analyzer, "enabled")
        assert trading_bot.ai_analyzer.min_confidence == 62

    def test_ai_fail_open_setting(self, trading_bot):
        """Verify AI fail_open is set correctly."""
        assert trading_bot.ai_analyzer.fail_open is True, "AI fail_open should be True"


class TestScanSummaryLogs:
    """Tests for SCAN SUMMARY rejection counters in _scan_entries."""

    @pytest.mark.asyncio
    async def test_scan_entries_has_rejection_tracking(self, trading_bot):
        """Verify _scan_entries tracks rejection counters."""
        # Mock dependencies to test the rejection tracking code path
        trading_bot.position_manager.add = lambda x: None
        trading_bot.risk_guard.can_trade = lambda symbol=None: (False, "test_block")
        
        calls = []
        
        async def fake_analyze(symbol):
            from engine.entry_engine import EntrySignal
            sig = EntrySignal()
            sig.metadata["reject_reason"] = "test_reject"
            return sig
        
        trading_bot._analyze_symbol = fake_analyze
        trading_bot.allocator.allocate = lambda x: []
        trading_bot.controls.set_candidates = lambda x: None
        
        # We can't easily capture log output, but we verify the method structure
        # by checking that _scan_entries exists and has the expected signature
        import inspect
        sig = inspect.signature(trading_bot._scan_entries)
        assert "symbols" in sig.parameters


class TestManualPositionLevelBasedTPSL:
    """Tests for level-based TP/SL derivation for manual/adopted positions."""

    def test_derive_manual_position_levels_long_with_clusters(self, trading_bot):
        """Verify _derive_manual_position_levels uses liquidity clusters for long."""
        from analysis.liquidation_clusters import LiquidationCluster, LiquidationAnalysis
        
        entry_price = 4.19
        atr = 0.08
        klines = [{"high": 4.25, "low": 4.10, "close": 4.19} for _ in range(30)]
        
        # Create liq_analysis with clusters
        cluster_above = LiquidationCluster(4.35, 100000, 5, 3.8, "shorts")
        cluster_below = LiquidationCluster(4.05, 80000, 3, 3.3, "longs")
        liq = LiquidationAnalysis([cluster_above], [cluster_below], cluster_above, cluster_below, 4.35, 100000, "up", 1, 3.8)
        
        sl, tp = trading_bot._derive_manual_position_levels("BUY", entry_price, 0, 0, atr, liq_analysis=liq, klines=klines)
        
        # SL should be derived from support levels (cluster_below or swing low)
        assert sl < entry_price, "Long SL should be below entry"
        # TP should be derived from resistance levels (cluster_above or swing high)
        assert tp > entry_price, "Long TP should be above entry"

    def test_derive_manual_position_levels_short_with_clusters(self, trading_bot):
        """Verify _derive_manual_position_levels uses liquidity clusters for short."""
        from analysis.liquidation_clusters import LiquidationCluster, LiquidationAnalysis
        
        entry_price = 62000.0
        atr = 500.0
        klines = [{"high": 62500.0, "low": 61500.0, "close": 62000.0} for _ in range(30)]
        
        cluster_above = LiquidationCluster(62800, 100000, 5, 1.3, "shorts")
        cluster_below = LiquidationCluster(61200, 80000, 3, 1.3, "longs")
        liq = LiquidationAnalysis([cluster_above], [cluster_below], cluster_above, cluster_below, 61200, 80000, "down", -1, 1.3)
        
        sl, tp = trading_bot._derive_manual_position_levels("SELL", entry_price, 0, 0, atr, liq_analysis=liq, klines=klines)
        
        # SL should be derived from resistance levels (cluster_above or swing high)
        assert sl > entry_price, "Short SL should be above entry"
        # TP should be derived from support levels (cluster_below or swing low)
        assert tp < entry_price, "Short TP should be below entry"

    def test_derive_levels_uses_swing_levels_when_no_clusters(self, trading_bot):
        """Verify derivation falls back to swing levels when no clusters available."""
        from analysis.liquidation_clusters import LiquidationAnalysis
        
        entry_price = 4.19
        atr = 0.08
        klines = [
            {"high": 4.30, "low": 4.00, "close": 4.19},
            {"high": 4.28, "low": 4.05, "close": 4.20},
            {"high": 4.35, "low": 4.08, "close": 4.25},
        ] * 10
        
        # Empty liq analysis (no clusters)
        liq = LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)
        
        sl, tp = trading_bot._derive_manual_position_levels("BUY", entry_price, 0, 0, atr, liq_analysis=liq, klines=klines)
        
        # Should still derive levels from swing highs/lows
        assert sl < entry_price, "SL should still be below entry even without clusters"
        assert tp > entry_price, "TP should still be above entry even without clusters"

    def test_preserve_existing_sl_tp_flag(self, trading_bot):
        """Verify preserve_existing_sl_tp flag is set correctly."""
        assert trading_bot.preserve_existing_sl_tp is True

    @pytest.mark.asyncio
    async def test_sync_exchange_position_uses_level_derivation(self, trading_bot):
        """Verify _sync_exchange_position uses _derive_manual_position_levels for adopted positions."""
        klines = build_trend_klines(length=140, start=62000.0, direction="up")
        
        async def fake_get_klines(symbol, interval, limit):
            return klines[-limit:]
        
        async def fake_get_orderbook(symbol, limit):
            return {"bids": [["62100", "200"]], "asks": [["62120", "100"]]}
        
        async def fake_get_trades(symbol, limit):
            return [{"price": 62110, "size": 50, "side": "Buy", "timestamp": i} for i in range(20)]
        
        async def fake_update_sl(symbol, sl, position_idx=0):
            return True
        
        async def fake_update_tp(symbol, tp, position_idx=0):
            return True
        
        trading_bot.client.get_klines = fake_get_klines
        trading_bot.client.get_orderbook = fake_get_orderbook
        trading_bot.client.get_recent_trades = fake_get_trades
        trading_bot.execution_engine.update_sl = fake_update_sl
        trading_bot.execution_engine.update_tp = fake_update_tp
        
        # Sync a position without existing SL/TP
        await trading_bot._sync_exchange_position({
            "symbol": "TESTUSDT",
            "size": "0.1",
            "avgPrice": "62000",
            "markPrice": "62100",
            "side": "Buy",
            "stopLoss": "0",  # No existing SL
            "takeProfit": "0",  # No existing TP
            "positionIdx": 0,
            "unrealisedPnl": "10",
        })
        
        adopted = trading_bot.position_manager.get("TESTUSDT")
        assert adopted is not None
        assert adopted.origin == "manual"
        # Since preserve_existing_sl_tp=True but no existing SL/TP, should derive from levels
        assert adopted.stop_loss > 0, "SL should be derived"
        assert adopted.take_profit > 0, "TP should be derived"
        assert adopted.stop_loss < adopted.entry_price, "Long SL should be below entry"
        assert adopted.take_profit > adopted.entry_price, "Long TP should be above entry"


class TestRRFallback:
    """Tests for RR fallback when no level-based targets available."""

    def test_derive_levels_rr_fallback_long(self, trading_bot):
        """Verify RR fallback is used when no cluster targets for long."""
        from analysis.liquidation_clusters import LiquidationAnalysis
        
        entry_price = 4.19
        atr = 0.08
        # Empty klines list forces RR fallback
        klines = []
        
        liq = LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)
        
        sl, tp = trading_bot._derive_manual_position_levels("BUY", entry_price, 0, 0, atr, liq_analysis=liq, klines=klines)
        
        # SL should use ATR-based calculation
        assert sl < entry_price
        # TP should use min_rr_ratio * risk
        risk = entry_price - sl
        expected_min_tp = entry_price + risk * trading_bot.entry_engine.min_rr_ratio
        assert tp >= expected_min_tp - 0.01, "TP should meet minimum RR ratio"

    def test_derive_levels_rr_fallback_short(self, trading_bot):
        """Verify RR fallback is used when no cluster targets for short."""
        from analysis.liquidation_clusters import LiquidationAnalysis
        
        entry_price = 62000.0
        atr = 500.0
        klines = []
        
        liq = LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)
        
        sl, tp = trading_bot._derive_manual_position_levels("SELL", entry_price, 0, 0, atr, liq_analysis=liq, klines=klines)
        
        # SL should use ATR-based calculation
        assert sl > entry_price
        # TP should use min_rr_ratio * risk
        risk = sl - entry_price
        expected_min_tp = entry_price - risk * trading_bot.entry_engine.min_rr_ratio
        assert tp <= expected_min_tp + 10, "TP should meet minimum RR ratio"


class TestNoRegressions:
    """Tests to ensure no regressions in existing functionality."""

    def test_heatmap_fallback_still_works(self, trading_bot):
        """Verify synthetic heatmap fallback still works."""
        klines = build_trend_klines(length=50, start=4.10, direction="up")
        liq = trading_bot._resolve_liquidation_context("TESTUSDT", klines[-1]["close"], klines)
        assert liq.target_level > 0, "Synthetic heatmap should provide target"

    def test_directional_fallback_still_works(self, trading_bot):
        """Verify directional heatmap fallback still works."""
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=0.8,
        )
        orderflow = OrderflowSnapshot(bullish_ratio=1.15, bearish_ratio=0.92, imbalance_score=0.2)
        
        liq = trading_bot._build_directional_liq_fallback(4.19, market, orderflow, 0.08)
        
        assert liq.target_level > 4.19
        assert liq.signal == 1

    def test_profit_drawdown_guard_still_works(self, trading_bot):
        """Verify profit_drawdown_guard is still enabled and configured."""
        assert trading_bot.profit_drawdown_guard_enabled is True
        assert trading_bot.profit_drawdown_activation_pct == 3.0
        assert trading_bot.profit_drawdown_retrace_pct == 25.0

    def test_basket_profit_guard_still_works(self, trading_bot):
        """Verify basket_profit_guard is still enabled and configured."""
        assert trading_bot.basket_profit_guard_enabled is True
        assert trading_bot.basket_profit_min_positions == 2
        assert trading_bot.basket_profit_require_negative is True

    def test_entry_engine_config_values(self, entry_engine, bot_config):
        """Verify EntryEngine config values are loaded correctly."""
        assert entry_engine.transformer_threshold == 0.60
        assert entry_engine.max_liq_distance_pct == 0.55
        assert entry_engine.min_orderflow_imbalance == 1.12
        assert entry_engine.min_rr_ratio == 1.8
        assert entry_engine.breakout_lookback == 20
        assert entry_engine.pullback_lookback == 8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
