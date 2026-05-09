#!/usr/bin/env python3
"""Tests for enhanced quant modules: RegimeDetector, Orderflow, FeatureBuilder, Backtester."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from dataclasses import dataclass
from analysis.market_regime_ai import MarketRegimeAI, RegimePrediction
from analysis.market_analyzer import MarketRegime, TrendDirection, VolatilityRegime
from analysis.orderflow_analyzer import OrderflowAnalyzer, OrderflowSnapshot
from analysis.feature_engineering import FeatureEngineer
from analysis.liquidation_clusters import LiquidationAnalysis


# ─── Mock objects ───────────────────────────────────────────────

@dataclass
class MockMarket:
    adx: float = 30.0
    trend: TrendDirection = TrendDirection.BULLISH
    htf_trend: TrendDirection = TrendDirection.BULLISH
    atr_pct: float = 1.5
    range_compression: float = 0.9
    volume_expansion: float = 1.3
    current_range_pct: float = 0.015


# ═══════════════════════════════════════════════════════════════
# REGIME DETECTOR TESTS
# ═══════════════════════════════════════════════════════════════

class TestRegimeDetector:

    def test_strong_trend_detected(self):
        ai = MarketRegimeAI()
        market = MockMarket(adx=30, trend=TrendDirection.BULLISH, htf_trend=TrendDirection.BULLISH)
        result = ai.classify(market)
        assert result.regime == MarketRegime.TREND

    def test_chop_detected_low_adx(self):
        ai = MarketRegimeAI()
        market = MockMarket(adx=12, trend=TrendDirection.NEUTRAL, htf_trend=TrendDirection.NEUTRAL,
                            atr_pct=0.5, range_compression=1.1, volume_expansion=0.9)
        result = ai.classify(market)
        assert result.regime == MarketRegime.CHOP

    def test_breakout_detected(self):
        ai = MarketRegimeAI()
        market = MockMarket(adx=15, trend=TrendDirection.NEUTRAL,
                            atr_pct=3.0, range_compression=0.7, volume_expansion=1.5)
        result = ai.classify(market)
        assert result.regime == MarketRegime.BREAKOUT

    def test_detect_from_klines_trend(self):
        ai = MarketRegimeAI()
        # Strong uptrend: 100 -> 200
        klines = [{"close": 100 + i * 5} for i in range(20)]
        regime = ai.detect_from_klines(klines)
        assert regime == "trend"

    def test_detect_from_klines_range(self):
        ai = MarketRegimeAI()
        # Truly flat market: same close, no trend, no volatility
        klines = [{"close": 100.0} for _ in range(20)]
        regime = ai.detect_from_klines(klines)
        assert regime == "range"

    def test_confidence_is_valid(self):
        ai = MarketRegimeAI()
        market = MockMarket()
        result = ai.classify(market)
        assert 0.0 <= result.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════
# ORDERFLOW IMBALANCE TESTS
# ═══════════════════════════════════════════════════════════════

class TestOrderflowImbalance:

    def test_normalized_imbalance_bullish(self):
        analyzer = OrderflowAnalyzer()
        orderbook = {"bids": [["50000", "1.0"]], "asks": [["50010", "1.0"]]}
        trades = [
            {"size": 10.0, "side": "Buy"},
            {"size": 3.0, "side": "Sell"},
        ]
        result = analyzer.analyze(orderbook, trades)
        # (10 - 3) / (10 + 3) = 0.538
        assert result.normalized_imbalance > 0.5
        assert result.dominant_side == "bullish"

    def test_normalized_imbalance_bearish(self):
        analyzer = OrderflowAnalyzer()
        orderbook = {"bids": [["50000", "1.0"]], "asks": [["50010", "1.0"]]}
        trades = [
            {"size": 2.0, "side": "Buy"},
            {"size": 8.0, "side": "Sell"},
        ]
        result = analyzer.analyze(orderbook, trades)
        # (2 - 8) / (2 + 8) = -0.6
        assert result.normalized_imbalance < -0.5
        assert result.dominant_side == "bearish"

    def test_normalized_imbalance_neutral(self):
        analyzer = OrderflowAnalyzer()
        orderbook = {"bids": [["50000", "1.0"]], "asks": [["50010", "1.0"]]}
        trades = [
            {"size": 5.0, "side": "Buy"},
            {"size": 5.0, "side": "Sell"},
        ]
        result = analyzer.analyze(orderbook, trades)
        assert result.normalized_imbalance == 0.0
        assert result.dominant_side == "neutral"

    def test_normalized_imbalance_no_trades(self):
        analyzer = OrderflowAnalyzer()
        orderbook = {"bids": [["50000", "1.0"]], "asks": [["50010", "1.0"]]}
        result = analyzer.analyze(orderbook, [])
        assert result.normalized_imbalance == 0.0

    def test_imbalance_range(self):
        """Normalized imbalance must be in [-1, +1]."""
        analyzer = OrderflowAnalyzer()
        orderbook = {"bids": [["50000", "10.0"]], "asks": [["50010", "0.1"]]}
        trades = [{"size": 100.0, "side": "Buy"}]
        result = analyzer.analyze(orderbook, trades)
        assert -1.0 <= result.normalized_imbalance <= 1.0


# ═══════════════════════════════════════════════════════════════
# FEATURE BUILDER TESTS (enhanced with imbalance feature)
# ═══════════════════════════════════════════════════════════════

class TestFeatureBuilder:

    def test_feature_count_increased(self):
        """Feature vector includes orderflow + Open Interest tail (4)."""
        fe = FeatureEngineer(sequence_length=20)
        klines = [{"open": 100, "high": 101, "low": 99, "close": 100 + i * 0.1, "volume": 1000}
                  for i in range(30)]
        orderflow = OrderflowSnapshot(normalized_imbalance=0.3)
        liq = LiquidationAnalysis([], [], None, None, 50500, 1.0, "up", 1, 1.0)
        result = fe.build(klines, orderflow, liq, 1.0)
        assert result.feature_count == 19
        assert result.latest_vector[-5] == 0.3
        assert result.latest_vector[-4:] == [0.0, 0.0, 0.0, 0.0]

    def test_empty_klines(self):
        fe = FeatureEngineer()
        orderflow = OrderflowSnapshot()
        liq = LiquidationAnalysis([], [], None, None, 0, 0, "neutral", 0, 0)
        result = fe.build([], orderflow, liq, 1.0)
        assert result.sequence == []


# ═══════════════════════════════════════════════════════════════
# BACKTESTER UNIT TESTS
# ═══════════════════════════════════════════════════════════════

class TestBacktesterHelpers:

    def test_determine_4h_trend_bullish(self):
        from backtester import Backtester
        bt = Backtester.__new__(Backtester)
        # Rising closes with EMA20 > EMA50
        klines = [{"close": 50000 + i * 100} for i in range(25)]
        trend = bt._determine_4h_trend(klines)
        assert trend == 1  # bullish

    def test_determine_4h_trend_bearish(self):
        from backtester import Backtester
        bt = Backtester.__new__(Backtester)
        # Falling closes
        klines = [{"close": 60000 - i * 100} for i in range(25)]
        trend = bt._determine_4h_trend(klines)
        assert trend == -1  # bearish

    def test_determine_4h_trend_insufficient_data(self):
        from backtester import Backtester
        bt = Backtester.__new__(Backtester)
        klines = [{"close": 50000} for _ in range(5)]
        trend = bt._determine_4h_trend(klines)
        assert trend == 0  # neutral

    def test_candles_needed(self):
        from backtester import Backtester
        # 1 day with 15m candles = 96
        assert Backtester._candles_needed(1, "15") == 96
        # 7 days with 1h candles = 168
        assert Backtester._candles_needed(7, "60") == 168

    def test_compute_result_empty(self):
        from backtester import Backtester, BacktestTrade
        bt = Backtester.__new__(Backtester)
        result = bt._compute_result("BTCUSDT", 7, 100, [], {"no_sweep": 50})
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.rejected_reasons["no_sweep"] == 50

    def test_compute_result_with_trades(self):
        from backtester import Backtester, BacktestTrade
        bt = Backtester.__new__(Backtester)
        trades = [
            BacktestTrade("BTC", "BUY", 50000, 49000, 53000, 3.0, "t1",
                          exit_price=53000, pnl_pct=6.0, result="win"),
            BacktestTrade("BTC", "BUY", 50000, 49000, 53000, 3.0, "t2",
                          exit_price=49000, pnl_pct=-2.0, result="loss"),
            BacktestTrade("BTC", "SELL", 50000, 51000, 47000, 3.0, "t3",
                          exit_price=47000, pnl_pct=6.0, result="win"),
        ]
        result = bt._compute_result("BTCUSDT", 7, 200, trades, {})
        assert result.total_trades == 3
        assert result.wins == 2
        assert result.losses == 1
        assert result.win_rate == pytest.approx(66.7, abs=0.1)
        assert result.profit_factor > 0
        assert result.total_pnl_pct == pytest.approx(10.0, abs=0.1)

    def test_check_trade_exit_sl_long(self):
        from backtester import Backtester, BacktestTrade
        bt = Backtester.__new__(Backtester)
        trade = BacktestTrade("BTC", "BUY", 50000, 49000, 53000, 3.0, "t1")
        hit = bt._check_trade_exit(trade, high=50500, low=48500, close=49000, time_str="t2")
        assert hit is True
        assert trade.result == "loss"
        assert trade.exit_reason == "stop_loss"

    def test_check_trade_exit_tp_short(self):
        from backtester import Backtester, BacktestTrade
        bt = Backtester.__new__(Backtester)
        trade = BacktestTrade("BTC", "SELL", 50000, 51000, 47000, 3.0, "t1")
        hit = bt._check_trade_exit(trade, high=50500, low=46500, close=47000, time_str="t2")
        assert hit is True
        assert trade.result == "win"
        assert trade.exit_reason == "take_profit"

    def test_check_trade_no_exit(self):
        from backtester import Backtester, BacktestTrade
        bt = Backtester.__new__(Backtester)
        trade = BacktestTrade("BTC", "BUY", 50000, 49000, 53000, 3.0, "t1", result="open")
        hit = bt._check_trade_exit(trade, high=50500, low=49500, close=50200, time_str="t2")
        assert hit is False
        assert trade.result == "open"

    def test_format_report(self):
        from backtester import format_report, BacktestResult
        results = [BacktestResult(
            symbol="BTCUSDT", period_days=7, total_signals=100,
            total_trades=5, wins=3, losses=2, win_rate=60.0,
            avg_rr=2.5, profit_factor=3.0, total_pnl_pct=8.5,
            max_drawdown_pct=2.0, avg_win_pct=4.0, avg_loss_pct=-2.0,
        )]
        report = format_report(results)
        assert "BTCUSDT" in report
        assert "60.0%" in report
        assert "8.5" in report


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
