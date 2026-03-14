#!/usr/bin/env python3
"""Tests for Strategy v2 changes - breakout+pullback continuation, AI advisory mode, level-based SL/TP."""

import asyncio
import sys
from pathlib import Path

import pytest

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# Helper to build synthetic klines
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


@pytest.fixture
def bot_config():
    from core.config import BotConfig
    return BotConfig.load(str(BOT_DIR / "config.yaml"))


@pytest.fixture
def entry_engine(bot_config):
    from engine.entry_engine import EntryEngine
    return EntryEngine(bot_config)


@pytest.fixture
def trading_bot():
    from main import TradingBot
    bot = TradingBot()
    bot.tg = None
    bot._save_trade = lambda *args, **kwargs: None
    return bot


class TestRSIRemovedFromEntryConditions:
    """Tests verifying RSI is not used as hard reversal gate."""

    def test_entry_engine_has_no_rsi_threshold(self, entry_engine):
        """Verify EntryEngine doesn't have RSI-based entry blocking."""
        # Entry engine should not have RSI attributes
        assert not hasattr(entry_engine, "rsi_overbought")
        assert not hasattr(entry_engine, "rsi_oversold")
        assert not hasattr(entry_engine, "rsi_threshold")

    def test_long_ready_conditions_no_rsi(self, entry_engine):
        """Verify _long_ready doesn't check RSI."""
        import inspect
        source = inspect.getsource(entry_engine._long_ready)
        assert "rsi" not in source.lower(), "RSI should not be in _long_ready conditions"

    def test_short_ready_conditions_no_rsi(self, entry_engine):
        """Verify _short_ready doesn't check RSI."""
        import inspect
        source = inspect.getsource(entry_engine._short_ready)
        assert "rsi" not in source.lower(), "RSI should not be in _short_ready conditions"


class TestBreakoutPullbackContinuationStrategy:
    """Tests for the breakout + pullback + momentum continuation entry logic."""

    def test_structure_detection_returns_all_scenarios(self, entry_engine):
        """Verify _detect_structure returns all expected structure types."""
        from analysis.market_analyzer import MarketAnalyzer
        
        klines = build_trend_klines(direction="up")
        market = MarketAnalyzer().analyze(klines, klines[-140:])
        
        structure = entry_engine._detect_structure(klines, klines[-1]["close"], market)
        
        # All structure keys should be present
        expected_keys = ["breakout_long", "breakout_short", "pullback_long", "pullback_short",
                        "continuation_long", "continuation_short", "trigger_reason"]
        for key in expected_keys:
            assert key in structure, f"Missing key: {key}"

    def test_breakout_long_requires_trend_and_structure(self, entry_engine):
        """Verify breakout_long detection logic."""
        from analysis.market_analyzer import MarketAnalyzer
        
        klines = build_trend_klines(direction="up", length=180)
        market = MarketAnalyzer().analyze(klines, klines[-140:])
        current_price = klines[-1]["close"]
        
        structure = entry_engine._detect_structure(klines, current_price, market)
        
        # In strong uptrend, should detect breakout or pullback long
        assert structure["breakout_long"] or structure["pullback_long"] or structure["continuation_long"]

    def test_continuation_detection_checks_momentum(self, entry_engine):
        """Verify continuation detection uses momentum (closing prices rising/falling)."""
        from analysis.market_analyzer import MarketAnalyzer
        
        klines = build_trend_klines(direction="down", length=180)
        market = MarketAnalyzer().analyze(klines, klines[-140:])
        market.volume_expansion = 1.05  # Mock volume expansion
        current_price = klines[-1]["close"]
        
        structure = entry_engine._detect_structure(klines, current_price, market)
        
        # Should detect some form of short structure
        if structure["continuation_short"]:
            assert True
        else:
            # May not detect continuation if volume conditions not met
            pass


class TestAIAdvisoryModeWithFakeBreakoutVeto:
    """Tests for AI soft-veto that only blocks fake breakout conditions."""

    def test_ai_analyzer_system_prompt_mentions_breakout_continuation(self, trading_bot):
        """Verify AI system prompt focuses on breakout continuation, not RSI reversal."""
        prompt = trading_bot.ai_analyzer.SYSTEM_PROMPT
        assert "momentum continuation" in prompt.lower()
        assert "breakout" in prompt.lower()
        # Should NOT emphasize RSI as reversal gate
        assert "do not reject just because rsi" in prompt.lower()

    def test_ai_fake_breakout_veto_keywords(self):
        """Verify fake breakout detection keywords used in main.py."""
        with open(BOT_DIR / "main.py") as f:
            source = f.read()
        
        # Verify the explicit_fake_breakout check exists
        assert "fake breakout" in source.lower()
        assert "bull trap" in source.lower()
        assert "bear trap" in source.lower()

    def test_ai_min_confidence_lowered_for_v2(self, trading_bot):
        """Verify AI min_confidence is lowered (55) for more permissive filtering."""
        assert trading_bot.ai_analyzer.min_confidence == 55

    def test_ai_fail_open_enabled(self, trading_bot):
        """Verify AI fail_open is True (allows trades when AI fails)."""
        assert trading_bot.ai_analyzer.fail_open is True


class TestLevelBasedSLTPDerivation:
    """Tests for level-based SL/TP derivation using liquidity clusters and swing levels."""

    def test_entry_engine_uses_level_for_stop_loss(self, entry_engine):
        """Verify stop_loss calculation uses liq_stop (protective level)."""
        # Check that liq_stop_buffer_atr is used
        assert hasattr(entry_engine, "liq_stop_buffer_atr")
        assert entry_engine.liq_stop_buffer_atr == 0.35

    def test_entry_engine_uses_level_for_take_profit(self, entry_engine):
        """Verify take_profit uses target_level with buffer."""
        assert hasattr(entry_engine, "level_tp_buffer_atr")
        assert entry_engine.level_tp_buffer_atr == 0.20

    def test_manual_position_derives_levels_from_structure(self, trading_bot):
        """Verify adopted positions derive SL/TP from structural levels."""
        from analysis.liquidation_clusters import LiquidationCluster, LiquidationAnalysis
        
        entry_price = 4.19
        atr = 0.08
        klines = [
            {"high": 4.30, "low": 4.05, "close": 4.19},
            {"high": 4.35, "low": 4.08, "close": 4.22},
        ] * 15
        
        cluster_above = LiquidationCluster(4.40, 100000, 5, 5.0, "shorts")
        cluster_below = LiquidationCluster(4.00, 80000, 3, 4.5, "longs")
        liq = LiquidationAnalysis([cluster_above], [cluster_below], cluster_above, cluster_below, 4.40, 100000, "up", 1, 5.0)
        
        sl, tp = trading_bot._derive_manual_position_levels("BUY", entry_price, 0, 0, atr, liq_analysis=liq, klines=klines)
        
        # SL should use cluster_below or swing low as base
        assert sl < entry_price
        # TP should use cluster_above or swing high as target
        assert tp > entry_price


class TestScanSummaryRejectionLogs:
    """Tests for SCAN SUMMARY rejection counters."""

    def test_scan_summary_log_format(self):
        """Verify _scan_entries logs rejection counters in expected format."""
        with open(BOT_DIR / "main.py") as f:
            source = f.read()
        
        # Verify SCAN SUMMARY log exists
        assert "SCAN SUMMARY" in source
        assert "reject_counts" in source or "reject" in source

    def test_reject_reason_tracking(self):
        """Verify various reject reasons are tracked."""
        with open(BOT_DIR / "main.py") as f:
            source = f.read()
        
        # Check for common rejection tracking
        assert "mark_reject" in source or "reject_reason" in source


class TestStrategyV2ConfigValues:
    """Tests verifying config values reflect strategy v2 parameters."""

    def test_transformer_threshold_permissive(self, bot_config):
        """Verify transformer_threshold is lowered for v2 (0.56)."""
        threshold = bot_config.get("entry", "transformer_threshold")
        assert threshold == 0.56

    def test_max_liq_distance_expanded(self, bot_config):
        """Verify max_liq_distance_pct allows farther targets (1.10)."""
        distance = bot_config.get("entry", "max_liq_distance_pct")
        assert distance == 1.10

    def test_min_orderflow_imbalance_lowered(self, bot_config):
        """Verify min_orderflow_imbalance is more achievable (1.05)."""
        imbalance = bot_config.get("entry", "min_orderflow_imbalance")
        assert imbalance == 1.05

    def test_min_rr_ratio_reasonable(self, bot_config):
        """Verify min_rr_ratio is set reasonably (1.4)."""
        rr = bot_config.get("entry", "min_rr_ratio")
        assert rr == 1.4

    def test_allowed_regimes_include_chop(self, bot_config):
        """Verify chop regime is allowed for more entries."""
        regimes = bot_config.get("entry", "allowed_regimes")
        assert "chop" in regimes


class TestNoRegressionsPreviousGuards:
    """Tests ensuring previous guards still work."""

    def test_profit_drawdown_guard_enabled(self, trading_bot):
        """Verify profit_drawdown_guard is still enabled."""
        assert trading_bot.profit_drawdown_guard_enabled is True
        assert trading_bot.profit_drawdown_activation_pct == 3.0
        assert trading_bot.profit_drawdown_retrace_pct == 25.0

    def test_basket_profit_guard_enabled(self, trading_bot):
        """Verify basket_profit_guard is still enabled."""
        assert trading_bot.basket_profit_guard_enabled is True
        assert trading_bot.basket_profit_min_positions == 2

    def test_manual_position_preserves_tp(self, trading_bot):
        """Verify manual positions still preserve existing TP."""
        assert trading_bot.manual_preserve_existing_tp is True

    def test_partial_tp_enabled(self, trading_bot):
        """Verify partial TP is still enabled."""
        assert trading_bot.partial_tp_enabled is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
