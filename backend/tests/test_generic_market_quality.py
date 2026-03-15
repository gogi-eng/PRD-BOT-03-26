#!/usr/bin/env python3
"""Tests for generic market quality filtering - no symbol-specific guards.

Tests verify:
1. No symbol-specific guard remains
2. 15m structure is used to confirm breakout quality 
3. In chop regime only confirmed breakout is allowed
4. Low ATR breakout exception still works
5. All filtering is generic (works for any symbol)
"""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


@pytest.fixture
def trading_bot():
    """Create a TradingBot instance with mocked external dependencies."""
    from main import TradingBot
    
    bot = TradingBot()
    bot.tg = None
    bot._save_trade = lambda *args, **kwargs: None
    return bot


@pytest.fixture
def entry_engine():
    """Create an EntryEngine instance."""
    from core.config import BotConfig
    from engine.entry_engine import EntryEngine
    
    cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
    return EntryEngine(cfg)


def build_klines(length=100, start=100.0, direction="up", volatility=0.01):
    """Build synthetic klines with configurable direction and volatility."""
    klines = []
    price = start
    for i in range(length):
        if direction == "up":
            price += price * volatility
        elif direction == "down":
            price -= price * volatility
        else:  # chop
            price += price * volatility if i % 2 == 0 else -price * volatility
        
        klines.append({
            "open": price - price * volatility * 0.3,
            "high": price + price * volatility * 0.5,
            "low": price - price * volatility * 0.5,
            "close": price,
            "volume": 1000 + i * 10,
        })
    return klines


class TestNoSymbolSpecificGuard:
    """Verify no symbol-specific logic exists in the codebase."""
    
    def test_main_py_has_no_hardcoded_symbols(self):
        """Ensure main.py doesn't contain symbol-specific guards."""
        main_code = Path("/app/bot/main.py").read_text()
        
        # Should NOT have any symbol-specific checks like if symbol in ["BTCUSDT", ...]
        # or if "BTC" in symbol or is_major_symbol
        assert '"BTCUSDT"' not in main_code or main_code.count('"BTCUSDT"') == 0
        assert '"ETHUSDT"' not in main_code or main_code.count('"ETHUSDT"') == 0
        assert '"XRPUSDT"' not in main_code or main_code.count('"XRPUSDT"') == 0
        assert '"TRUMPUSDT"' not in main_code or main_code.count('"TRUMPUSDT"') == 0
        assert "is_major" not in main_code
        assert "major_symbol" not in main_code
        
    def test_entry_engine_has_no_hardcoded_symbols(self):
        """Ensure entry_engine.py doesn't contain symbol-specific guards."""
        engine_code = Path("/app/bot/engine/entry_engine.py").read_text()
        
        assert '"BTCUSDT"' not in engine_code
        assert '"ETHUSDT"' not in engine_code
        assert '"XRPUSDT"' not in engine_code
        assert "is_major" not in engine_code
        assert "major_symbol" not in engine_code
        
    def test_market_quality_filter_is_symbol_agnostic(self, trading_bot):
        """Verify the market quality filter doesn't check symbol names."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        # The filter should work identically for ANY symbol
        klines = build_klines(100, 100.0, "up")
        htf_klines = klines[-60:]
        current_price = klines[-1]["close"]
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=25,
            volume_expansion=1.1,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        
        orderflow = OrderflowSnapshot(
            bullish_ratio=1.2,
            bearish_ratio=0.9,
            volume_spike=1.1,
        )
        
        # This should NOT change based on what symbol we pass - it's NOT used at all
        result1, _ = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        # The function signature doesn't even take symbol as a parameter
        # proving it's completely symbol-agnostic
        import inspect
        sig = inspect.signature(trading_bot._passes_market_quality_filter)
        param_names = list(sig.parameters.keys())
        assert "symbol" not in param_names


class TestFifteenMinuteStructureFilter:
    """Verify 15m structure is used to confirm breakout quality."""
    
    def test_breakout_requires_15m_confirmation(self, trading_bot):
        """Breakout must be confirmed on 15m before entry."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        # Build 15m klines where price is NOT at 15m high
        htf_klines = build_klines(15, 100.0, "up")
        htf_high = max(float(k["high"]) for k in htf_klines[-13:-1])
        
        # Current price is BELOW the 15m high (not confirmed)
        current_price = htf_high * 0.995  # 0.5% below
        klines = build_klines(100, current_price * 0.9, "up")
        klines[-1]["close"] = current_price
        
        market = MarketAnalysis(
            regime=MarketRegime.BREAKOUT,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=30,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        
        orderflow = OrderflowSnapshot(volume_spike=1.1)
        
        ok, reason = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        assert not ok
        assert reason == "breakout_not_confirmed_on_15m"
        
    def test_breakout_passes_when_15m_confirmed(self, trading_bot):
        """Breakout passes when price is at/above 15m high."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        # Build 15m klines
        htf_klines = build_klines(15, 100.0, "up")
        htf_high = max(float(k["high"]) for k in htf_klines[-13:-1])
        
        # Current price is AT the 15m high (confirmed)
        current_price = htf_high * 1.001  # slightly above
        klines = build_klines(100, current_price * 0.9, "up")
        klines[-1]["close"] = current_price
        
        market = MarketAnalysis(
            regime=MarketRegime.BREAKOUT,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=30,
            volume_expansion=1.1,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        
        orderflow = OrderflowSnapshot(volume_spike=1.1)
        
        ok, reason = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        assert ok
        assert reason == ""
        
    def test_short_breakout_requires_15m_low_confirmation(self, trading_bot):
        """Short breakout must be confirmed on 15m low."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        # Build 15m klines
        htf_klines = build_klines(15, 100.0, "down")
        htf_low = min(float(k["low"]) for k in htf_klines[-13:-1])
        
        # Current price is ABOVE the 15m low (not confirmed for short)
        current_price = htf_low * 1.005  # 0.5% above low
        klines = build_klines(100, current_price * 1.1, "down")
        klines[-1]["close"] = current_price
        
        market = MarketAnalysis(
            regime=MarketRegime.BREAKOUT,
            trend=TrendDirection.BEARISH,
            htf_trend=TrendDirection.BEARISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=30,
        )
        
        signal = EntrySignal(should_enter=True, side="SELL")
        signal.metadata = {"structure_breakout": True}
        
        orderflow = OrderflowSnapshot(volume_spike=1.1)
        
        ok, reason = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        assert not ok
        assert reason == "breakout_not_confirmed_on_15m"


class TestChopOnlyBreakoutAllowed:
    """Verify chop regime only allows confirmed breakouts."""
    
    def test_chop_blocks_pullback_entry(self, trading_bot):
        """In chop, pullback entries are blocked."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        klines = build_klines(100, 100.0, "chop")
        htf_klines = klines[-60:]
        current_price = klines[-1]["close"]
        
        market = MarketAnalysis(
            regime=MarketRegime.CHOP,  # CHOP regime
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=15,
            volume_expansion=1.0,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": False, "structure_pullback": True}  # PULLBACK
        
        orderflow = OrderflowSnapshot(volume_spike=1.0)
        
        ok, reason = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        assert not ok
        assert reason == "chop_without_breakout"
        
    def test_chop_blocks_continuation_entry(self, trading_bot):
        """In chop, continuation entries are blocked."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        klines = build_klines(100, 100.0, "chop")
        htf_klines = klines[-60:]
        current_price = klines[-1]["close"]
        
        market = MarketAnalysis(
            regime=MarketRegime.CHOP,
            trend=TrendDirection.BEARISH,
            htf_trend=TrendDirection.BEARISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=12,
        )
        
        signal = EntrySignal(should_enter=True, side="SELL")
        signal.metadata = {"structure_breakout": False, "structure_pullback": False}  # Neither
        
        orderflow = OrderflowSnapshot(volume_spike=0.9)
        
        ok, reason = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        assert not ok
        assert reason == "chop_without_breakout"
        
    def test_chop_allows_confirmed_breakout_with_volume(self, trading_bot):
        """In chop, confirmed breakout with volume is allowed."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        # Create klines that support a breakout scenario
        htf_klines = build_klines(15, 100.0, "up")
        htf_high = max(float(k["high"]) for k in htf_klines[-13:-1])
        current_price = htf_high * 1.002  # Above 15m high
        
        klines = build_klines(100, current_price * 0.95, "up")
        klines[-1]["close"] = current_price
        
        market = MarketAnalysis(
            regime=MarketRegime.CHOP,  # CHOP regime
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=20,
            volume_expansion=1.1,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}  # BREAKOUT
        
        orderflow = OrderflowSnapshot(volume_spike=1.05)  # Volume spike
        
        ok, reason = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        assert ok
        assert reason == ""
        
    def test_chop_breakout_requires_volume_confirmation(self, trading_bot):
        """In chop, breakout without volume is blocked."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        htf_klines = build_klines(15, 100.0, "up")
        htf_high = max(float(k["high"]) for k in htf_klines[-13:-1])
        current_price = htf_high * 1.002
        
        klines = build_klines(100, current_price * 0.95, "up")
        klines[-1]["close"] = current_price
        
        market = MarketAnalysis(
            regime=MarketRegime.CHOP,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=15,
            volume_expansion=1.01,  # LOW volume expansion
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        
        orderflow = OrderflowSnapshot(volume_spike=1.01)  # LOW volume spike
        
        ok, reason = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        
        assert not ok
        assert reason == "weak_breakout_quality"


class TestEntryEngineChopHandling:
    """Verify entry engine properly handles chop regime."""
    
    def test_long_ready_blocks_non_breakout_in_chop(self, entry_engine):
        """_long_ready returns False for non-breakout in chop."""
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        from analysis.liquidation_clusters import LiquidationAnalysis
        from analysis.transformer_model import TransformerPrediction
        
        market = MagicMock()
        market.regime = MarketRegime.CHOP
        market.trend.value = 1
        market.htf_trend.value = 1
        market.volume_expansion = 1.05
        
        structure = {
            "breakout_long": False,  # NO breakout
            "pullback_long": True,   # Has pullback
            "continuation_long": False,
        }
        
        transformer = MagicMock()
        transformer.prob_up = 0.7
        transformer.prob_down = 0.2
        
        orderflow = MagicMock()
        orderflow.bullish_ratio = 1.2
        orderflow.volume_spike = 1.1
        
        liq = MagicMock()
        liq.signal = 1
        liq.max_liq_cluster_above = MagicMock()
        
        result = entry_engine._long_ready(
            regime_ok=True,
            liq_near=True,
            structure=structure,
            market=market,
            transformer=transformer,
            orderflow=orderflow,
            liq=liq
        )
        
        assert result is False  # Should be blocked due to chop without breakout
        
    def test_short_ready_blocks_non_breakout_in_chop(self, entry_engine):
        """_short_ready returns False for non-breakout in chop."""
        from analysis.market_analyzer import MarketRegime
        
        market = MagicMock()
        market.regime = MarketRegime.CHOP
        market.trend.value = -1
        market.htf_trend.value = -1
        market.volume_expansion = 1.05
        
        structure = {
            "breakout_short": False,  # NO breakout
            "pullback_short": True,   # Has pullback
            "continuation_short": False,
        }
        
        transformer = MagicMock()
        transformer.prob_down = 0.7
        transformer.prob_up = 0.2
        
        orderflow = MagicMock()
        orderflow.bearish_ratio = 1.2
        orderflow.volume_spike = 1.1
        
        liq = MagicMock()
        liq.signal = -1
        liq.max_liq_cluster_below = MagicMock()
        
        result = entry_engine._short_ready(
            regime_ok=True,
            liq_near=True,
            structure=structure,
            market=market,
            transformer=transformer,
            orderflow=orderflow,
            liq=liq
        )
        
        assert result is False
        
    def test_long_ready_allows_breakout_in_chop(self, entry_engine):
        """_long_ready returns True for breakout in chop."""
        from analysis.market_analyzer import MarketRegime
        
        market = MagicMock()
        market.regime = MarketRegime.CHOP
        market.trend.value = 1
        market.htf_trend.value = 0  # Neutral HTF is OK for breakout
        market.volume_expansion = 1.1
        
        structure = {
            "breakout_long": True,  # HAS breakout
            "pullback_long": False,
            "continuation_long": False,
        }
        
        transformer = MagicMock()
        transformer.prob_up = 0.65
        transformer.prob_down = 0.2
        
        orderflow = MagicMock()
        orderflow.bullish_ratio = 1.2
        orderflow.volume_spike = 1.1
        
        liq = MagicMock()
        liq.signal = 1
        liq.max_liq_cluster_above = MagicMock()
        
        result = entry_engine._long_ready(
            regime_ok=True,
            liq_near=True,
            structure=structure,
            market=market,
            transformer=transformer,
            orderflow=orderflow,
            liq=liq
        )
        
        assert result is True


class TestLowATRBreakoutException:
    """Verify low ATR breakout exception still works."""
    
    @pytest.mark.asyncio
    async def test_low_atr_breakout_allowed(self, trading_bot):
        """Breakout entries are allowed even with low ATR."""
        from engine.entry_engine import EntrySignal
        
        # Mock dependencies
        async def fake_get_klines(symbol, interval, limit):
            # Very low volatility klines
            klines = []
            price = 10.0
            for i in range(100):
                price += 0.001 if i < 95 else 0.02  # Small moves, then breakout
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
        
        trading_bot.client.get_klines = fake_get_klines
        trading_bot.client.get_orderbook = fake_get_orderbook
        trading_bot.client.get_recent_trades = fake_get_recent_trades
        trading_bot.client.get_liquidation_events = lambda symbol: []
        trading_bot.atr.get_atr_pct = lambda symbol, klines: 0.05  # Very low ATR
        
        # Create a breakout signal
        original_generate = trading_bot.entry_engine.generate_signal
        
        def breakout_signal(*args, **kwargs):
            signal = EntrySignal(
                should_enter=True,
                side="BUY",
                confidence=0.8,
                entry_price=10.1,
                stop_loss=10.0,
                take_profit=10.4,
                rr_ratio=2.0
            )
            signal.metadata = {"structure_breakout": True}
            return signal
        
        trading_bot.entry_engine.generate_signal = breakout_signal
        
        try:
            signal = await trading_bot._analyze_symbol("ANYUSDT")
            assert signal.should_enter  # Should be allowed despite low ATR
        finally:
            trading_bot.entry_engine.generate_signal = original_generate
            
    @pytest.mark.asyncio
    async def test_low_atr_pullback_blocked(self, trading_bot):
        """Pullback entries are blocked with low ATR."""
        from engine.entry_engine import EntrySignal
        
        async def fake_get_klines(symbol, interval, limit):
            klines = []
            price = 10.0
            for i in range(100):
                price += 0.001
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
        
        trading_bot.client.get_klines = fake_get_klines
        trading_bot.client.get_orderbook = fake_get_orderbook
        trading_bot.client.get_recent_trades = fake_get_recent_trades
        trading_bot.client.get_liquidation_events = lambda symbol: []
        trading_bot.atr.get_atr_pct = lambda symbol, klines: 0.05  # Very low ATR
        
        original_generate = trading_bot.entry_engine.generate_signal
        
        def pullback_signal(*args, **kwargs):
            signal = EntrySignal(
                should_enter=True,
                side="BUY",
                confidence=0.8,
                entry_price=10.1,
                stop_loss=10.0,
                take_profit=10.4,
                rr_ratio=2.0
            )
            signal.metadata = {"structure_breakout": False, "structure_pullback": True}
            return signal
        
        trading_bot.entry_engine.generate_signal = pullback_signal
        
        try:
            signal = await trading_bot._analyze_symbol("ANYUSDT")
            assert not signal.should_enter  # Should be blocked
            assert signal.metadata.get("reject_reason") in {
                "atr_too_low", 
                "no_live_heatmap_no_breakout",
                "htf_not_bullish",
                "weak_market_quality"
            }
        finally:
            trading_bot.entry_engine.generate_signal = original_generate


class TestGenericFilteringForAllSymbols:
    """Verify filtering works generically for any symbol."""
    
    def test_filter_works_for_btc(self, trading_bot):
        """Filter works for BTC (formerly had specific guard)."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        klines = build_klines(100, 71000.0, "up")
        htf_klines = klines[-60:]
        current_price = klines[-1]["close"]
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=25,
            volume_expansion=1.1,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        orderflow = OrderflowSnapshot(volume_spike=1.1)
        
        # No special treatment for BTC
        ok, _ = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        assert ok
        
    def test_filter_works_for_eth(self, trading_bot):
        """Filter works for ETH (formerly had specific guard)."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        klines = build_klines(100, 3500.0, "up")
        htf_klines = klines[-60:]
        current_price = klines[-1]["close"]
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=25,
            volume_expansion=1.1,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        orderflow = OrderflowSnapshot(volume_spike=1.1)
        
        ok, _ = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        assert ok
        
    def test_filter_works_for_xrp(self, trading_bot):
        """Filter works for XRP (formerly had specific guard)."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        klines = build_klines(100, 2.5, "up")
        htf_klines = klines[-60:]
        current_price = klines[-1]["close"]
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=25,
            volume_expansion=1.1,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        orderflow = OrderflowSnapshot(volume_spike=1.1)
        
        ok, _ = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        assert ok
        
    def test_filter_works_for_random_altcoin(self, trading_bot):
        """Filter works for any random altcoin."""
        from engine.entry_engine import EntrySignal
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.orderflow_analyzer import OrderflowSnapshot
        
        klines = build_klines(100, 0.001234, "up")  # Very low price coin
        htf_klines = klines[-60:]
        current_price = klines[-1]["close"]
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=1.0,
            adx=25,
            volume_expansion=1.1,
        )
        
        signal = EntrySignal(should_enter=True, side="BUY")
        signal.metadata = {"structure_breakout": True}
        orderflow = OrderflowSnapshot(volume_spike=1.1)
        
        ok, _ = trading_bot._passes_market_quality_filter(
            current_price, klines, htf_klines, market, signal, orderflow, has_live_liq=True
        )
        assert ok


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
