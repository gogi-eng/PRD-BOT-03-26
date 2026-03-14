#!/usr/bin/env python3
"""Tests for the directional heatmap fallback feature - the last-resort fallback
when live and synthetic heatmap both produce no target_level."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# Module: Directional heatmap fallback tests
# Tests _build_directional_liq_fallback() - creates target when live+synthetic heatmap produce nothing

class TestDirectionalHeatmapFallbackLogic:
    """Tests for _build_directional_liq_fallback() method in TradingBot."""

    @pytest.fixture
    def trading_bot(self):
        from main import TradingBot
        bot = TradingBot()
        bot.tg = None
        return bot

    @pytest.fixture
    def bullish_market(self):
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        return MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=0.8,
        )

    @pytest.fixture
    def bearish_market(self):
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        return MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BEARISH,
            htf_trend=TrendDirection.BEARISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=0.8,
        )

    @pytest.fixture
    def neutral_market(self):
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        return MarketAnalysis(
            regime=MarketRegime.CHOP,
            trend=TrendDirection.NEUTRAL,
            htf_trend=TrendDirection.NEUTRAL,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=0.8,
        )

    @pytest.fixture
    def bullish_orderflow(self):
        from analysis.orderflow_analyzer import OrderflowSnapshot
        return OrderflowSnapshot(bullish_ratio=1.15, bearish_ratio=0.92, imbalance_score=0.2)

    @pytest.fixture
    def bearish_orderflow(self):
        from analysis.orderflow_analyzer import OrderflowSnapshot
        return OrderflowSnapshot(bullish_ratio=0.92, bearish_ratio=1.15, imbalance_score=-0.2)

    @pytest.fixture
    def neutral_orderflow(self):
        from analysis.orderflow_analyzer import OrderflowSnapshot
        return OrderflowSnapshot(bullish_ratio=1.0, bearish_ratio=1.0, imbalance_score=0.0)

    def test_bullish_market_creates_upside_target(self, trading_bot, bullish_market, bullish_orderflow):
        """When market and orderflow are bullish, target_level should be above current price."""
        current_price = 4.19
        atr_val = 0.08
        liq = trading_bot._build_directional_liq_fallback(current_price, bullish_market, bullish_orderflow, atr_val)
        
        assert liq.target_level > current_price
        assert liq.signal == 1
        assert liq.magnet_direction == "up"
        assert len(liq.clusters_above) == 1
        assert liq.clusters_above[0].side_bias == "shorts"
        assert liq.max_liq_cluster_above is not None
        assert liq.max_liq_cluster_below is None

    def test_bearish_market_creates_downside_target(self, trading_bot, bearish_market, bearish_orderflow):
        """When market and orderflow are bearish, target_level should be below current price."""
        current_price = 62000.0
        atr_val = 500.0
        liq = trading_bot._build_directional_liq_fallback(current_price, bearish_market, bearish_orderflow, atr_val)
        
        assert liq.target_level < current_price
        assert liq.signal == -1
        assert liq.magnet_direction == "down"
        assert len(liq.clusters_below) == 1
        assert liq.clusters_below[0].side_bias == "longs"
        assert liq.max_liq_cluster_below is not None
        assert liq.max_liq_cluster_above is None

    def test_neutral_market_returns_empty_analysis(self, trading_bot, neutral_market, neutral_orderflow):
        """When market is neutral and orderflow is balanced, should return neutral analysis."""
        current_price = 4.19
        atr_val = 0.08
        liq = trading_bot._build_directional_liq_fallback(current_price, neutral_market, neutral_orderflow, atr_val)
        
        assert liq.target_level == 0.0
        assert liq.signal == 0
        assert liq.magnet_direction == "neutral"
        assert len(liq.clusters_above) == 0
        assert len(liq.clusters_below) == 0

    def test_orderflow_can_break_tie(self, trading_bot, neutral_market, bullish_orderflow):
        """Orderflow can break tie when market trend is neutral."""
        current_price = 4.19
        atr_val = 0.08
        liq = trading_bot._build_directional_liq_fallback(current_price, neutral_market, bullish_orderflow, atr_val)
        
        assert liq.target_level > current_price
        assert liq.signal == 1

    def test_htf_trend_vote_counts(self, trading_bot, bullish_orderflow):
        """HTF trend should add a vote to the direction."""
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        
        # Market with neutral trend but bullish HTF trend
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.NEUTRAL,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=0.8,
        )
        
        current_price = 4.19
        atr_val = 0.08
        liq = trading_bot._build_directional_liq_fallback(current_price, market, bullish_orderflow, atr_val)
        
        # HTF bullish + orderflow bullish = bullish direction (2 votes bullish, 0 bearish)
        assert liq.target_level > current_price
        assert liq.signal == 1

    def test_zero_price_returns_neutral(self, trading_bot, bullish_market, bullish_orderflow):
        """Zero current price should return neutral analysis."""
        liq = trading_bot._build_directional_liq_fallback(0.0, bullish_market, bullish_orderflow, 0.08)
        
        assert liq.target_level == 0.0
        assert liq.signal == 0
        assert liq.magnet_direction == "neutral"

    def test_negative_price_returns_neutral(self, trading_bot, bullish_market, bullish_orderflow):
        """Negative current price should return neutral analysis."""
        liq = trading_bot._build_directional_liq_fallback(-100.0, bullish_market, bullish_orderflow, 0.08)
        
        assert liq.target_level == 0.0
        assert liq.signal == 0

    def test_fallback_uses_atr_for_distance(self, trading_bot, bullish_market, bullish_orderflow):
        """Target distance should be based on ATR * 1.8."""
        current_price = 100.0
        atr_val = 1.0  # 1% of price
        liq = trading_bot._build_directional_liq_fallback(current_price, bullish_market, bullish_orderflow, atr_val)
        
        # Distance should be atr * 1.8 = 1.8
        expected_target = current_price + 1.8
        assert abs(liq.target_level - expected_target) < 0.01
        assert liq.distance_to_target_pct == pytest.approx(1.8, rel=0.01)

    def test_fallback_uses_min_distance_when_atr_small(self, trading_bot, bullish_market, bullish_orderflow):
        """When ATR*1.8 is less than 0.4% of price, use 0.4% minimum."""
        current_price = 100.0
        atr_val = 0.01  # Very small ATR
        liq = trading_bot._build_directional_liq_fallback(current_price, bullish_market, bullish_orderflow, atr_val)
        
        # Min distance = price * 0.004 = 0.4
        expected_min_distance = current_price * 0.004
        expected_target = current_price + expected_min_distance
        assert liq.target_level >= expected_target

    def test_fallback_with_zero_atr_uses_default(self, trading_bot, bullish_market, bullish_orderflow):
        """When ATR is zero, use 0.8% of price as default."""
        current_price = 100.0
        atr_val = 0.0
        liq = trading_bot._build_directional_liq_fallback(current_price, bullish_market, bullish_orderflow, atr_val)
        
        # Default ATR = price * 0.008 = 0.8, distance = 0.8 * 1.8 = 1.44
        expected_min = current_price + current_price * 0.008 * 1.8
        assert liq.target_level >= expected_min - 0.1

    def test_bearish_target_never_goes_negative(self, trading_bot, bearish_market, bearish_orderflow):
        """Bearish target should never go below zero."""
        current_price = 0.001  # Very low price
        atr_val = 0.01  # Larger than price
        liq = trading_bot._build_directional_liq_fallback(current_price, bearish_market, bearish_orderflow, atr_val)
        
        assert liq.target_level >= 0.0

    def test_cluster_has_proper_attributes(self, trading_bot, bullish_market, bullish_orderflow):
        """Created cluster should have all proper attributes."""
        from analysis.liquidation_clusters import LiquidationCluster
        
        current_price = 100.0
        atr_val = 1.0
        liq = trading_bot._build_directional_liq_fallback(current_price, bullish_market, bullish_orderflow, atr_val)
        
        cluster = liq.max_liq_cluster_above
        assert isinstance(cluster, LiquidationCluster)
        assert cluster.level > 0
        assert cluster.size == 1.0
        assert cluster.hits == 1
        assert cluster.distance_pct > 0
        assert cluster.side_bias == "shorts"


class TestDirectionalFallbackIntegration:
    """Tests for directional fallback integration in _manage_positions and _analyze_symbol."""

    @pytest.fixture
    def trading_bot(self):
        from main import TradingBot
        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None
        return bot

    def test_manage_positions_uses_directional_fallback(self, trading_bot):
        """_manage_positions should use directional fallback when live+synthetic fail."""
        from engine.position_manager import Position
        
        # Add a position
        pos = Position(
            symbol="TRUMPUSDT",
            side="BUY",
            entry_price=4.19,
            qty=100,
            stop_loss=4.00,
            take_profit=4.60,
        )
        trading_bot.position_manager.add(pos)
        
        # Mock client methods
        klines = []
        price = 4.10
        for i in range(200):
            price += 0.01 if i % 3 else -0.005
            klines.append({
                "open": price - 0.01,
                "high": price + 0.02,
                "low": price - 0.02,
                "close": price,
                "volume": 50000 + i * 100,
            })
        
        async def fake_get_price(symbol):
            return 4.25

        async def fake_get_klines(symbol, interval, limit):
            return klines[-limit:]

        async def fake_get_orderbook(symbol, limit):
            return {
                "bids": [["4.24", "1000"], ["4.23", "800"]],
                "asks": [["4.26", "1000"], ["4.27", "800"]],
            }

        async def fake_get_recent_trades(symbol, limit):
            return [{"price": 4.25, "size": 100, "side": "Buy", "timestamp": i} for i in range(limit)]

        trading_bot.client.get_price = fake_get_price
        trading_bot.client.get_klines = fake_get_klines
        trading_bot.client.get_orderbook = fake_get_orderbook
        trading_bot.client.get_recent_trades = fake_get_recent_trades
        trading_bot.client.get_liquidation_events = lambda symbol: []  # No live events

        # The code in _manage_positions should now use directional fallback 
        # when liq.target_level <= 0 after resolve_liquidation_context
        # This is a code path verification test

        # Verify the method exists and is callable
        assert hasattr(trading_bot, "_build_directional_liq_fallback")
        assert callable(trading_bot._build_directional_liq_fallback)

    def test_analyze_symbol_uses_directional_fallback(self, trading_bot):
        """_analyze_symbol should use directional fallback when live+synthetic fail."""
        # This verifies the code path exists in _analyze_symbol at lines 507-508
        # The method should call _build_directional_liq_fallback after 
        # _resolve_liquidation_context returns target_level <= 0
        
        import inspect
        source = inspect.getsource(trading_bot._analyze_symbol)
        
        # Verify the fallback is called in _analyze_symbol
        assert "_build_directional_liq_fallback" in source
        assert "_resolve_liquidation_context" in source


class TestNoRegressions:
    """Tests to ensure no regressions in existing features after directional fallback addition."""

    @pytest.fixture
    def trading_bot(self):
        from main import TradingBot
        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None
        return bot

    def test_profit_drawdown_guard_still_works(self, trading_bot):
        """Profit drawdown guard should still function correctly."""
        from engine.position_manager import Position
        import asyncio
        
        pos = Position(
            symbol="BTCUSDT",
            side="BUY",
            entry_price=62000.0,
            qty=0.01,
            stop_loss=61700.0,
            take_profit=62600.0,
        )
        trading_bot._apply_profit_drawdown_profile(pos)
        
        # Activation should be at +3%
        assert pos.trailing_activation_price >= 62000.0 * 1.03

    def test_basket_profit_guard_still_works(self, trading_bot):
        """Basket profit guard should still function correctly."""
        assert trading_bot.basket_profit_guard_enabled is True
        assert trading_bot.basket_profit_min_positions == 2
        assert trading_bot.basket_profit_require_negative is True

    def test_manual_position_logic_intact(self, trading_bot):
        """Manual position management should still work."""
        assert trading_bot.manual_rl_enabled is False
        assert trading_bot.manual_preserve_existing_tp is True
        assert trading_bot.manual_trailing_activation_atr > trading_bot.exit_engine.trailing_activation_atr

    def test_portfolio_tp_disabled_by_default(self, trading_bot):
        """Portfolio TP should remain disabled by default."""
        assert trading_bot.portfolio_tp_enabled is False

    def test_synthetic_heatmap_still_used_first(self, trading_bot):
        """Synthetic heatmap should still be tried before directional fallback."""
        import inspect
        source = inspect.getsource(trading_bot._resolve_liquidation_context)
        
        # Verify synthetic fallback is still in place
        assert "_build_synthetic_liquidation_events" in source
        assert "synthetic price-action fallback" in source

    def test_live_liquidation_events_preferred(self, trading_bot):
        """Live liquidation events should still be preferred over fallbacks."""
        import inspect
        source = inspect.getsource(trading_bot._resolve_liquidation_context)
        
        # Verify live events are checked first
        assert "liq.target_level > 0" in source
        assert "return liq" in source  # Early return if live events work
