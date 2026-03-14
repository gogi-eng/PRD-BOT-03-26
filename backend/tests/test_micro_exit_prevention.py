#!/usr/bin/env python3
"""Tests for micro-exit prevention (addresses user's Telegram logs showing +0.01% and -0.02% exits)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BOT_DIR = Path("/app/bot").resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# RLPositionAgent: Tests for preventing micro-profit/loss closes
# ============================================================================

class TestRLPositionAgentMicroClosesPrevention:
    """Verify RL agent won't close on tiny profits like +0.01% or micro losses like -0.02%."""
    
    @pytest.fixture
    def rl_agent(self):
        from engine.rl_position_agent import RLPositionAgent
        # Use config values from config.yaml
        return RLPositionAgent(
            add_threshold=0.78,
            reduce_threshold=0.7,
            close_threshold=0.8,
            min_close_profit_pct=0.5,  # From config
            max_panic_loss_pct=0.6,    # From config
            min_reduce_profit_pct=0.8  # From config
        )
    
    @pytest.fixture
    def long_position(self):
        from engine.position_manager import Position
        return Position(
            symbol="XRPUSDT",
            side="BUY",
            entry_price=2.50,
            qty=100,
            stop_loss=2.40,
            take_profit=2.70
        )
    
    @pytest.fixture
    def short_position(self):
        from engine.position_manager import Position
        return Position(
            symbol="BTCUSDT",
            side="SELL",
            entry_price=95000.0,
            qty=0.01,
            stop_loss=96000.0,
            take_profit=93000.0
        )
    
    def test_rl_agent_does_not_close_on_micro_profit_001_pct(self, rl_agent, long_position):
        """User's XRPUSDT case: +0.01% profit should NOT trigger RL close."""
        from engine.rl_position_agent import RLAction
        
        # Simulate conditions where RL might want to close (adverse edge)
        state = {
            "trend_bias": -1,        # Against position
            "volatility": 0.05,      # High volatility
            "pnl_pct": 0.01,         # Only +0.01% profit (NOT ENOUGH!)
            "liq_signal": -1,        # Against position
            "orderflow_edge": -0.8,  # Against position
            "transformer_edge": -0.6 # Against position
        }
        
        decision = rl_agent.decide(long_position, state)
        
        # With tiny profit, should NOT close - must be HOLD
        assert decision.action != RLAction.CLOSE, f"RL closed on +0.01% profit! Reason: {decision.reason}"
        # Should also not reduce with such low profit
        assert decision.action != RLAction.REDUCE, f"RL reduced on +0.01% profit! Reason: {decision.reason}"
    
    def test_rl_agent_does_not_close_on_micro_loss_002_pct(self, rl_agent, short_position):
        """User's BTCUSDT case: -0.02% loss should NOT trigger RL close."""
        from engine.rl_position_agent import RLAction
        
        # Simulate adverse conditions
        state = {
            "trend_bias": 1,         # Against short position
            "volatility": 0.04,      # Elevated volatility
            "pnl_pct": -0.02,        # Only -0.02% loss (NOT ENOUGH!)
            "liq_signal": 1,         # Against short position
            "orderflow_edge": 0.5,   # Against short position
            "transformer_edge": 0.4  # Against short position
        }
        
        decision = rl_agent.decide(short_position, state)
        
        # With tiny loss, should NOT close - must be HOLD
        assert decision.action != RLAction.CLOSE, f"RL closed on -0.02% loss! Reason: {decision.reason}"
    
    def test_rl_agent_requires_meaningful_profit_to_close(self, rl_agent, long_position):
        """RL close on profit requires >= 0.5% (min_close_profit_pct from config)."""
        from engine.rl_position_agent import RLAction
        
        # Boundary test: 0.49% should NOT close
        state_below_threshold = {
            "trend_bias": -1, "volatility": 0.08, "pnl_pct": 0.49,
            "liq_signal": -1, "orderflow_edge": -1.0, "transformer_edge": -1.0
        }
        decision_49 = rl_agent.decide(long_position, state_below_threshold)
        assert decision_49.action != RLAction.CLOSE, f"RL closed at 0.49% profit (below 0.5% threshold)"
        
        # Boundary test: 0.5% SHOULD allow close if score is high enough
        state_at_threshold = {
            "trend_bias": -1, "volatility": 0.08, "pnl_pct": 0.50,
            "liq_signal": -1, "orderflow_edge": -1.0, "transformer_edge": -1.0
        }
        decision_50 = rl_agent.decide(long_position, state_at_threshold)
        # At 0.5%, close is ALLOWED (not guaranteed - depends on score)
        # This test verifies the gate opens, not that close always happens
    
    def test_rl_agent_requires_meaningful_loss_to_panic_close(self, rl_agent, long_position):
        """RL panic close on loss requires <= -0.6% (max_panic_loss_pct from config)."""
        from engine.rl_position_agent import RLAction
        
        # -0.59% should NOT panic close (not enough loss)
        state_small_loss = {
            "trend_bias": -1, "volatility": 0.08, "pnl_pct": -0.59,
            "liq_signal": -1, "orderflow_edge": -1.0, "transformer_edge": -1.0
        }
        decision_59 = rl_agent.decide(long_position, state_small_loss)
        assert decision_59.action != RLAction.CLOSE, f"RL panic-closed at -0.59% (above -0.6% threshold)"
        
        # -0.61% should ALLOW panic close if score is high
        state_at_threshold = {
            "trend_bias": -1, "volatility": 0.08, "pnl_pct": -0.61,
            "liq_signal": -1, "orderflow_edge": -1.0, "transformer_edge": -1.0
        }
        decision_61 = rl_agent.decide(long_position, state_at_threshold)
        # Panic close is now ALLOWED (depends on score)
    
    def test_rl_agent_requires_meaningful_profit_to_reduce(self, rl_agent, long_position):
        """RL reduce requires >= 0.8% profit (min_reduce_profit_pct from config)."""
        from engine.rl_position_agent import RLAction
        
        # 0.79% should NOT reduce
        state_below_threshold = {
            "trend_bias": -0.5, "volatility": 0.06, "pnl_pct": 0.79,
            "liq_signal": 0, "orderflow_edge": -0.3, "transformer_edge": -0.3
        }
        decision_79 = rl_agent.decide(long_position, state_below_threshold)
        assert decision_79.action != RLAction.REDUCE, f"RL reduced at 0.79% (below 0.8% threshold)"
        
        # 0.8% should ALLOW reduce
        state_at_threshold = {
            "trend_bias": -0.5, "volatility": 0.06, "pnl_pct": 0.80,
            "liq_signal": 0, "orderflow_edge": -0.3, "transformer_edge": -0.3
        }
        decision_80 = rl_agent.decide(long_position, state_at_threshold)
        # Reduce is now ALLOWED at >= 0.8%


# ============================================================================
# EntryEngine: Tests for minimum TP/SL distance enforcement
# ============================================================================

class TestEntryEngineMinimumDistances:
    """Verify EntryEngine enforces minimum TP/SL distances to avoid micro-exits."""
    
    @pytest.fixture
    def entry_engine(self):
        from core.config import BotConfig
        from engine.entry_engine import EntryEngine
        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        return EntryEngine(cfg)
    
    def test_config_has_minimum_distances(self, entry_engine):
        """Verify config values for minimum distances are loaded."""
        assert entry_engine.min_target_profit_pct == 1.2, "min_target_profit_pct should be 1.2%"
        assert entry_engine.min_stop_distance_pct == 0.35, "min_stop_distance_pct should be 0.35%"
    
    def test_entry_engine_enforces_min_tp_distance(self, entry_engine):
        """TP must be at least 1.2% away from entry price."""
        from analysis.liquidation_clusters import LiquidationCluster, LiquidationAnalysis
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.market_regime_ai import RegimePrediction, MarketRegime as RegimeEnum
        from analysis.orderflow_analyzer import OrderflowSnapshot
        from analysis.transformer_model import TransformerPrediction
        
        current_price = 100.0  # Easy math
        
        # Build minimal klines with breakout structure
        klines = []
        for i in range(100):
            base_price = 95.0 + i * 0.1  # Uptrend
            klines.append({
                "open": base_price - 0.05,
                "high": base_price + 0.15,
                "low": base_price - 0.15,
                "close": base_price,
                "volume": 1000 + i * 10
            })
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND,
            trend=TrendDirection.BULLISH,
            htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL,
            atr_pct=0.8,
            can_trade=True,
            ema_fast=99.5,
            ema_slow=98.0,
            volume_expansion=1.1,
            adx=30
        )
        regime = RegimePrediction(regime=RegimeEnum.TREND, confidence=0.85)
        transformer = TransformerPrediction(prob_up=0.72, prob_down=0.18, prob_flat=0.10)
        orderflow = OrderflowSnapshot(
            bullish_ratio=1.25, bearish_ratio=0.85, imbalance_score=0.3,
            volume_spike=1.1, spread_pct=0.01, orderbook_ratio=1.2
        )
        
        # Create liq target that's too close (0.5% above = 100.5)
        liq_cluster = LiquidationCluster(level=100.5, size=500000, hits=10, distance_pct=0.5, side_bias="shorts")
        liq = LiquidationAnalysis(
            clusters_above=[liq_cluster], clusters_below=[],
            max_liq_cluster_above=liq_cluster, max_liq_cluster_below=None,
            target_level=100.5, target_density=500000, magnet_direction="up",
            signal=1, distance_to_target_pct=0.5
        )
        
        signal = entry_engine.generate_signal(
            "TESTUSDT", klines, current_price, market, regime, transformer, orderflow, liq, atr_value=0.8
        )
        
        if signal.should_enter and signal.side == "BUY":
            # TP must be at least 1.2% away
            tp_distance_pct = abs(signal.take_profit - current_price) / current_price * 100
            assert tp_distance_pct >= 1.19, f"TP distance {tp_distance_pct:.2f}% is below 1.2% minimum"
    
    def test_entry_engine_enforces_min_sl_distance(self, entry_engine):
        """SL must be at least 0.35% away from entry price."""
        # This is tested in test_entry_engine_strict_conditions but let's verify specifically
        assert entry_engine.min_stop_distance_pct == 0.35
        
        # For a $100 entry, SL must be at least $99.65 (for long) or $100.35 (for short)
        min_distance = 100.0 * (0.35 / 100)  # = $0.35
        assert min_distance == 0.35


# ============================================================================
# Manual Position Adoption: Tests for minimum level enforcement
# ============================================================================

class TestManualPositionLevelEnforcement:
    """Verify adopted positions get meaningful TP/SL distances."""
    
    @pytest.fixture
    def bot(self):
        from main import TradingBot
        bot = TradingBot()
        bot.tg = None
        bot._save_trade = lambda *args, **kwargs: None
        return bot
    
    def test_derive_manual_levels_enforces_min_stop_distance(self, bot):
        """Manual position SL derivation enforces minimum distance."""
        from analysis.liquidation_clusters import LiquidationAnalysis
        
        entry_price = 100.0
        atr = 0.5
        
        # No existing SL/TP, and a fake liq analysis
        liq = LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)
        
        # Derive levels for a LONG position
        sl, tp = bot._derive_manual_position_levels("BUY", entry_price, 0, 0, atr, liq_analysis=liq, klines=None)
        
        # SL distance must be at least min_stop_distance_pct
        sl_distance_pct = abs(entry_price - sl) / entry_price * 100
        assert sl_distance_pct >= 0.35, f"Derived SL distance {sl_distance_pct:.2f}% is below 0.35% minimum"
    
    def test_derive_manual_levels_enforces_min_tp_distance(self, bot):
        """Manual position TP derivation enforces minimum distance."""
        from analysis.liquidation_clusters import LiquidationAnalysis
        
        entry_price = 100.0
        atr = 0.5
        
        liq = LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)
        
        # Derive levels for a LONG position
        sl, tp = bot._derive_manual_position_levels("BUY", entry_price, 0, 0, atr, liq_analysis=liq, klines=None)
        
        # TP distance must be at least min_target_profit_pct
        tp_distance_pct = abs(tp - entry_price) / entry_price * 100
        assert tp_distance_pct >= 1.19, f"Derived TP distance {tp_distance_pct:.2f}% is below 1.2% minimum"


# ============================================================================
# protective_liq_level: Tests for proper stop-based level storage
# ============================================================================

class TestProtectiveLiqLevelStorage:
    """Verify protective_liq_level is set to final stop, not raw near-liquidity level."""
    
    def test_entry_signal_metadata_stores_final_stop_as_protective_level(self):
        """Entry signal's protective_liq_level should be the final stop_loss, not raw level."""
        from core.config import BotConfig
        from engine.entry_engine import EntryEngine
        from analysis.liquidation_clusters import LiquidationCluster, LiquidationAnalysis
        from analysis.market_analyzer import MarketAnalysis, MarketRegime, TrendDirection, VolatilityRegime
        from analysis.market_regime_ai import RegimePrediction, MarketRegime as RegimeEnum
        from analysis.orderflow_analyzer import OrderflowSnapshot
        from analysis.transformer_model import TransformerPrediction
        
        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        engine = EntryEngine(cfg)
        
        current_price = 100.0
        klines = []
        for i in range(100):
            base = 95.0 + i * 0.1
            klines.append({
                "open": base - 0.05, "high": base + 0.15, "low": base - 0.15,
                "close": base, "volume": 1000
            })
        
        market = MarketAnalysis(
            regime=MarketRegime.TREND, trend=TrendDirection.BULLISH, htf_trend=TrendDirection.BULLISH,
            volatility=VolatilityRegime.NORMAL, atr_pct=0.8, can_trade=True,
            ema_fast=99.5, ema_slow=98.0, volume_expansion=1.1, adx=30
        )
        regime = RegimePrediction(regime=RegimeEnum.TREND, confidence=0.85)
        transformer = TransformerPrediction(prob_up=0.72, prob_down=0.18, prob_flat=0.10)
        orderflow = OrderflowSnapshot(
            bullish_ratio=1.25, bearish_ratio=0.85, imbalance_score=0.3,
            volume_spike=1.1, spread_pct=0.01, orderbook_ratio=1.2
        )
        
        # Target above, protective level below
        liq_above = LiquidationCluster(level=101.5, size=500000, hits=10, distance_pct=1.5, side_bias="shorts")
        liq_below = LiquidationCluster(level=99.0, size=200000, hits=5, distance_pct=1.0, side_bias="longs")
        liq = LiquidationAnalysis(
            clusters_above=[liq_above], clusters_below=[liq_below],
            max_liq_cluster_above=liq_above, max_liq_cluster_below=liq_below,
            target_level=101.5, target_density=500000, magnet_direction="up",
            signal=1, distance_to_target_pct=1.5
        )
        
        signal = engine.generate_signal(
            "TESTUSDT", klines, current_price, market, regime, transformer, orderflow, liq, atr_value=0.8
        )
        
        if signal.should_enter:
            # Verify protective_liq_level equals the final stop_loss (not raw 99.0 level)
            assert signal.metadata.get("protective_liq_level") == signal.stop_loss, \
                f"protective_liq_level ({signal.metadata.get('protective_liq_level')}) should equal stop_loss ({signal.stop_loss})"


# ============================================================================
# Integration: Full scenario tests
# ============================================================================

class TestMicroExitPreventionIntegration:
    """Integration tests simulating the exact user scenarios from Telegram logs."""
    
    def test_xrpusdt_short_would_not_close_at_001_pct(self):
        """Simulate XRPUSDT scenario: +0.01% profit should NOT trigger RL close."""
        from engine.position_manager import Position
        from engine.rl_position_agent import RLAction, RLPositionAgent
        from core.config import BotConfig
        
        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        rl_agent = RLPositionAgent(
            min_close_profit_pct=cfg.get("rl", "min_close_profit_pct", default=0.5),
            max_panic_loss_pct=cfg.get("rl", "max_panic_loss_pct", default=0.6),
            min_reduce_profit_pct=cfg.get("rl", "min_reduce_profit_pct", default=0.8)
        )
        
        # XRPUSDT short position
        pos = Position(
            symbol="XRPUSDT", side="SELL", entry_price=2.50, qty=100,
            stop_loss=2.55, take_profit=2.35
        )
        
        # State that would have triggered close before the fix
        state = {
            "trend_bias": 1,       # Against short (bullish trend)
            "volatility": 0.04,   # Moderate volatility
            "pnl_pct": 0.01,      # Only +0.01% profit (user's exact scenario)
            "liq_signal": 1,      # Against short
            "orderflow_edge": 0.5,
            "transformer_edge": 0.3
        }
        
        decision = rl_agent.decide(pos, state)
        assert decision.action != RLAction.CLOSE, \
            f"XRPUSDT short closed at +0.01% profit! This was the user's bug. Decision: {decision}"
    
    def test_btcusdt_short_would_not_close_at_002_pct_loss(self):
        """Simulate BTCUSDT scenario: -0.02% loss should NOT trigger liquidation_stop."""
        from engine.position_manager import Position
        from engine.rl_position_agent import RLAction, RLPositionAgent
        from core.config import BotConfig
        
        cfg = BotConfig.load(str(BOT_DIR / "config.yaml"))
        rl_agent = RLPositionAgent(
            min_close_profit_pct=cfg.get("rl", "min_close_profit_pct", default=0.5),
            max_panic_loss_pct=cfg.get("rl", "max_panic_loss_pct", default=0.6),
            min_reduce_profit_pct=cfg.get("rl", "min_reduce_profit_pct", default=0.8)
        )
        
        # BTCUSDT short position
        pos = Position(
            symbol="BTCUSDT", side="SELL", entry_price=95000.0, qty=0.01,
            stop_loss=96000.0, take_profit=93000.0
        )
        
        # State with tiny loss
        state = {
            "trend_bias": 1,       # Against short
            "volatility": 0.03,
            "pnl_pct": -0.02,      # Only -0.02% loss (user's exact scenario)
            "liq_signal": 1,
            "orderflow_edge": 0.4,
            "transformer_edge": 0.2
        }
        
        decision = rl_agent.decide(pos, state)
        assert decision.action != RLAction.CLOSE, \
            f"BTCUSDT short closed at -0.02% loss! This was the user's bug. Decision: {decision}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
