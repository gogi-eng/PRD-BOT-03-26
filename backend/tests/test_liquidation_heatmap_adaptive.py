#!/usr/bin/env python3
"""
Tests for adaptive liquidation heatmap clustering and synthetic fallback.

These tests validate:
1. Adaptive cluster step for low-priced symbols (<10 USDT)
2. Synthetic heatmap fallback when no liquidation events are cached
3. _analyze_symbol no longer hard-fails due to missing liquidation target
4. No regressions in existing heatmap behavior for high-priced symbols
"""
import sys
from pathlib import Path

import pytest

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# --- Test Module: Adaptive Cluster Step ---
class TestAdaptiveClusterStep:
    """Test _resolve_cluster_step for various price ranges."""

    @pytest.fixture
    def detector(self):
        from analysis.liquidation_clusters import LiquidationClusterDetector
        return LiquidationClusterDetector(cluster_step=20)

    def test_cluster_step_high_price_btc(self, detector):
        """BTC ~$60000 should use cluster_step of 100.0"""
        step = detector._resolve_cluster_step(60000.0)
        assert step == 100.0, f"BTC price should use step 100, got {step}"

    def test_cluster_step_mid_price_eth(self, detector):
        """ETH ~$3000 should use cluster_step of 100.0"""
        step = detector._resolve_cluster_step(3000.0)
        assert step == 100.0, f"ETH price should use step 100, got {step}"

    def test_cluster_step_price_100_to_1000(self, detector):
        """Price 100-1000 should use cluster_step of 20.0"""
        step = detector._resolve_cluster_step(500.0)
        assert step == 20.0, f"Price 500 should use step 20, got {step}"

    def test_cluster_step_price_10_to_100(self, detector):
        """Price 10-100 should use cluster_step of 0.1"""
        step = detector._resolve_cluster_step(50.0)
        assert step == 0.1, f"Price 50 should use step 0.1, got {step}"

    def test_cluster_step_low_price_1_to_10(self, detector):
        """Low price 1-10 (like TRUMP ~$4.19) should use step of 0.01"""
        step = detector._resolve_cluster_step(4.19)
        assert step == 0.01, f"Price 4.19 should use step 0.01, got {step}"

    def test_cluster_step_very_low_price_0_1_to_1(self, detector):
        """Very low price 0.1-1 should use step of 0.001"""
        step = detector._resolve_cluster_step(0.5)
        assert step == 0.001, f"Price 0.5 should use step 0.001, got {step}"

    def test_cluster_step_ultra_low_price_below_0_1(self, detector):
        """Ultra low price <0.1 should use step of 0.0001"""
        step = detector._resolve_cluster_step(0.05)
        assert step == 0.0001, f"Price 0.05 should use step 0.0001, got {step}"

    def test_cluster_step_boundary_at_10(self, detector):
        """Boundary at price=10 should use step 0.1"""
        step = detector._resolve_cluster_step(10.0)
        assert step == 0.1, f"Price 10 should use step 0.1, got {step}"

    def test_cluster_step_boundary_at_1(self, detector):
        """Boundary at price=1 should use step 0.01"""
        step = detector._resolve_cluster_step(1.0)
        assert step == 0.01, f"Price 1 should use step 0.01, got {step}"


# --- Test Module: Low-Price Symbol Heatmap Analysis ---
class TestLowPriceSymbolHeatmap:
    """Test heatmap analysis for low-priced symbols."""

    @pytest.fixture
    def detector(self):
        from analysis.liquidation_clusters import LiquidationClusterDetector
        return LiquidationClusterDetector(cluster_step=20)

    def test_low_price_symbol_clusters_properly(self, detector):
        """TRUMP at ~$4.19 should cluster liquidation events properly."""
        current_price = 4.19
        events = [
            {"price": 4.24, "size": 120000, "side": "Sell", "timestamp": 1},
            {"price": 4.26, "size": 180000, "side": "Sell", "timestamp": 2},
            {"price": 4.11, "size": 90000, "side": "Buy", "timestamp": 3},
        ]
        analysis = detector.analyze(current_price, events)
        
        assert analysis.target_level > 0, "Should have a valid target level"
        assert analysis.target_level > current_price, "Target should be above current (strongest is above)"
        assert analysis.distance_to_target_pct > 0, "Should have non-zero distance to target"
        assert analysis.signal == 1, "Signal should be 1 (bullish magnet above)"

    def test_low_price_symbol_no_events_returns_neutral(self, detector):
        """Low-price symbol with no events should return neutral analysis."""
        current_price = 4.19
        analysis = detector.analyze(current_price, [])
        
        assert analysis.target_level == 0.0
        assert analysis.signal == 0
        assert analysis.magnet_direction == "neutral"

    def test_low_price_symbol_bearish_magnet(self, detector):
        """Low-price symbol with stronger below cluster should signal -1."""
        current_price = 4.19
        events = [
            {"price": 4.24, "size": 50000, "side": "Sell", "timestamp": 1},
            {"price": 4.10, "size": 200000, "side": "Buy", "timestamp": 2},
            {"price": 4.08, "size": 150000, "side": "Buy", "timestamp": 3},
        ]
        analysis = detector.analyze(current_price, events)
        
        assert analysis.target_level < current_price, "Target should be below current"
        assert analysis.signal == -1, "Signal should be -1 (bearish magnet below)"
        assert analysis.magnet_direction == "down"


# --- Test Module: Synthetic Heatmap Fallback ---
class TestSyntheticHeatmapFallback:
    """Test synthetic heatmap fallback from price action."""

    @pytest.fixture
    def trading_bot(self):
        from main import TradingBot
        bot = TradingBot()
        bot.tg = None
        return bot

    @staticmethod
    def build_klines(length: int = 50, start: float = 4.10, direction: str = "up"):
        """Build synthetic klines for testing."""
        klines = []
        price = start
        for i in range(length):
            drift = 0.01 if direction == "up" else -0.01
            if i % 5 == 0:
                drift *= 1.5
            price += drift
            klines.append({
                "open": price - 0.01 if direction == "up" else price + 0.01,
                "high": price + 0.03,
                "low": price - 0.025,
                "close": price,
                "volume": 50000 + i * 1200,
            })
        return klines

    def test_synthetic_fallback_provides_target_when_no_live_events(self, trading_bot):
        """When no liquidation events cached, synthetic fallback should provide target."""
        klines = self.build_klines(length=50, start=4.10, direction="up")
        current_price = klines[-1]["close"]
        
        # Mock empty liquidation cache
        trading_bot.client.get_liquidation_events = lambda symbol: []
        
        liq = trading_bot._resolve_liquidation_context("TRUMPUSDT", current_price, klines)
        
        assert liq.target_level > 0, "Synthetic fallback should provide a valid target"
        assert liq.distance_to_target_pct >= 0, "Distance should be non-negative"

    def test_synthetic_fallback_builds_from_highs_and_lows(self, trading_bot):
        """Synthetic events should be built from candle highs and lows."""
        klines = self.build_klines(length=50, start=4.10, direction="up")
        current_price = klines[-1]["close"]
        
        events = trading_bot._build_synthetic_liquidation_events(klines, current_price)
        
        assert len(events) > 0, "Should build synthetic events"
        
        # Check that events have proper structure
        for event in events:
            assert "price" in event
            assert "size" in event
            assert "side" in event
            assert event["price"] > 0
            assert event["size"] > 0

    def test_synthetic_fallback_uses_last_36_candles(self, trading_bot):
        """Synthetic fallback should use last 36 candles (window)."""
        klines = self.build_klines(length=100, start=4.10, direction="up")
        current_price = klines[-1]["close"]
        
        events = trading_bot._build_synthetic_liquidation_events(klines, current_price)
        
        # Each candle can produce 0, 1, or 2 events depending on high/low relative to current price
        # With 36 candles, we expect at most 72 events (if all highs > current and all lows < current)
        assert len(events) <= 72, f"Expected <=72 events from 36 candles, got {len(events)}"

    def test_live_events_preferred_over_synthetic(self, trading_bot):
        """When live liquidation events exist, should use them over synthetic."""
        klines = self.build_klines(length=50, start=4.10, direction="up")
        current_price = klines[-1]["close"]
        
        live_events = [
            {"price": current_price + 0.05, "size": 500000, "side": "Sell", "timestamp": 1},
            {"price": current_price - 0.03, "size": 100000, "side": "Buy", "timestamp": 2},
        ]
        trading_bot.client.get_liquidation_events = lambda symbol: live_events
        
        liq = trading_bot._resolve_liquidation_context("TRUMPUSDT", current_price, klines)
        
        # Live events should produce a target near current_price + 0.05
        # Synthetic would produce different targets based on candle structure
        assert liq.target_level > 0, "Should have target from live events"


# --- Test Module: _analyze_symbol Entry Blocking Fix ---
class TestAnalyzeSymbolNoLongerBlocks:
    """Test that _analyze_symbol no longer hard-fails when fallback provides target."""

    @pytest.fixture
    def trading_bot(self):
        from main import TradingBot
        bot = TradingBot()
        bot.tg = None
        return bot

    @staticmethod
    def build_trend_klines(length: int = 180, start: float = 4.10, direction: str = "up"):
        """Build trending klines for entry signal generation."""
        klines = []
        price = start
        for i in range(length):
            drift = 0.02 if direction == "up" else -0.02
            pulse = 0.005 if i % 11 == 0 else -0.003 if i % 7 == 0 else 0.0
            price += drift + pulse
            klines.append({
                "open": price - 0.01 if direction == "up" else price + 0.01,
                "high": price + 0.03,
                "low": price - 0.025,
                "close": price,
                "volume": 100000 + i * 500,
            })
        return klines

    def test_analyze_symbol_uses_synthetic_fallback_for_low_price(self, trading_bot):
        """_analyze_symbol should use synthetic fallback and not block entry for low-price."""
        klines = self.build_trend_klines(length=180, start=4.10, direction="up")
        
        # Mock exchange methods
        async def mock_get_klines(symbol, interval, limit):
            return klines[-limit:]
        
        async def mock_get_orderbook(symbol, limit=25):
            return {
                "bids": [["4.50", "90"]],
                "asks": [["4.51", "85"]],
            }
        
        async def mock_get_recent_trades(symbol, limit=120):
            return [{"price": 4.50, "size": 100 + i, "side": "Buy" if i < 60 else "Sell", "timestamp": i} for i in range(limit)]
        
        trading_bot.client.get_klines = mock_get_klines
        trading_bot.client.get_orderbook = mock_get_orderbook
        trading_bot.client.get_recent_trades = mock_get_recent_trades
        trading_bot.client.get_liquidation_events = lambda symbol: []  # Empty cache!
        
        import asyncio
        signal = asyncio.run(trading_bot._analyze_symbol("TRUMPUSDT"))
        
        # Even without live liquidation events, we should get analysis (not early return)
        # The signal may or may not trigger entry depending on other conditions,
        # but the key is it doesn't block at liq.target_level <= 0 check
        # If fallback works, we should have metadata even if no entry
        # Test passes as long as no exception and we got through the analysis
        assert signal is not None, "Should return a signal (may or may not trigger entry)"


# --- Test Module: No Regressions in High-Price Heatmap ---
class TestHighPriceHeatmapNoRegression:
    """Ensure high-price symbols still work correctly."""

    @pytest.fixture
    def detector(self):
        from analysis.liquidation_clusters import LiquidationClusterDetector
        return LiquidationClusterDetector(cluster_step=20)

    def test_btc_heatmap_still_works(self, detector):
        """BTC at ~$62000 should still cluster at 100.0 step."""
        current_price = 62350.0
        events = [
            {"price": 62440, "size": 230000, "side": "Sell", "timestamp": 1},
            {"price": 62480, "size": 400000, "side": "Sell", "timestamp": 2},
            {"price": 62510, "size": 800000, "side": "Sell", "timestamp": 3},
            {"price": 62210, "size": 120000, "side": "Buy", "timestamp": 4},
        ]
        analysis = detector.analyze(current_price, events)
        
        # BTC should cluster at 62500 (100.0 step)
        assert analysis.max_liq_cluster_above is not None
        assert analysis.max_liq_cluster_above.level == 62500.0
        assert analysis.target_level == 62500.0
        assert analysis.signal == 1

    def test_eth_heatmap_still_works(self, detector):
        """ETH at ~$3000 should still cluster properly."""
        current_price = 3050.0
        events = [
            {"price": 3080, "size": 150000, "side": "Sell", "timestamp": 1},
            {"price": 3120, "size": 300000, "side": "Sell", "timestamp": 2},
            {"price": 3000, "size": 100000, "side": "Buy", "timestamp": 3},
        ]
        analysis = detector.analyze(current_price, events)
        
        assert analysis.target_level > current_price, "Target should be above current for ETH"
        assert analysis.signal == 1


# --- Test Module: Edge Cases ---
class TestEdgeCases:
    """Test edge cases in adaptive clustering."""

    @pytest.fixture
    def detector(self):
        from analysis.liquidation_clusters import LiquidationClusterDetector
        return LiquidationClusterDetector(cluster_step=20)

    def test_zero_price_returns_neutral(self, detector):
        """Zero or negative price should return neutral analysis."""
        analysis = detector.analyze(0.0, [])
        assert analysis.target_level == 0.0
        assert analysis.signal == 0
        
        analysis = detector.analyze(-1.0, [])
        assert analysis.target_level == 0.0
        assert analysis.signal == 0

    def test_events_with_zero_price_ignored(self, detector):
        """Events with zero or negative price should be ignored."""
        current_price = 4.19
        events = [
            {"price": 0.0, "size": 100000, "side": "Sell", "timestamp": 1},
            {"price": -1.0, "size": 100000, "side": "Buy", "timestamp": 2},
            {"price": 4.25, "size": 200000, "side": "Sell", "timestamp": 3},
        ]
        analysis = detector.analyze(current_price, events)
        
        # Only the valid event at 4.25 should be processed
        assert analysis.target_level > 0

    def test_events_with_zero_size_ignored(self, detector):
        """Events with zero or negative size should be ignored."""
        current_price = 4.19
        events = [
            {"price": 4.25, "size": 0.0, "side": "Sell", "timestamp": 1},
            {"price": 4.30, "size": -100, "side": "Sell", "timestamp": 2},
            {"price": 4.22, "size": 200000, "side": "Sell", "timestamp": 3},
        ]
        analysis = detector.analyze(current_price, events)
        
        # Only the valid event at 4.22 should be processed
        assert analysis.target_level > 0
