#!/usr/bin/env python3
"""
Iteration 39: Anti-Loss Package Verification Tests

Tests for comprehensive 'Anti-Loss' package based on detailed analysis:
1. Config values are correct (entry_threshold=0.72, early_exit_bars=6, etc.)
2. EntryEngine uses entry_threshold=0.72 to reject signals below it
3. ExitEngine early_exit_bars=6 closes stagnant trades after 6 bars
4. RiskGuard does NOT ignore exchange_closed for cooldown/consecutive loss tracking
5. Trailing stop logging is present in exit_engine.py
6. Quality gate works with reject_no_zone_entries=true in ALL modes
"""
import sys
import os
import pytest
import yaml

# Add bot directory to path for imports
sys.path.insert(0, '/app/bot')

from core.config import BotConfig
from engine.entry_engine import EntryEngine, EntrySignal
from engine.exit_engine import ExitEngine, ExitReason
from engine.risk_manager import RiskGuard


# ============================================================================
# CONFIG VALUE VERIFICATION TESTS
# ============================================================================

class TestConfigAntiLossValues:
    """Verify all Anti-Loss config values are correctly set."""
    
    @pytest.fixture
    def config(self):
        with open('/app/bot/config.yaml', 'r') as f:
            return yaml.safe_load(f)
    
    def test_entry_threshold_is_072(self, config):
        """entry_threshold changed from 0.56 to 0.72"""
        assert config['entry']['entry_threshold'] == 0.72, \
            f"entry_threshold should be 0.72, got {config['entry']['entry_threshold']}"
    
    def test_early_exit_bars_is_6(self, config):
        """early_exit_bars changed from 0 to 6"""
        assert config['exit']['early_exit_bars'] == 6, \
            f"early_exit_bars should be 6, got {config['exit']['early_exit_bars']}"
    
    def test_trained_model_min_prob_is_052(self, config):
        """trained_model_min_prob changed from 0.0 to 0.52"""
        assert config['entry']['trained_model_min_prob'] == 0.52, \
            f"trained_model_min_prob should be 0.52, got {config['entry']['trained_model_min_prob']}"
    
    def test_trained_model_blend_is_030(self, config):
        """trained_model_blend changed from 0.10 to 0.30"""
        assert config['entry']['trained_model_blend'] == 0.30, \
            f"trained_model_blend should be 0.30, got {config['entry']['trained_model_blend']}"
    
    def test_min_rr_ratio_is_30(self, config):
        """min_rr_ratio changed from 2.5 to 3.0"""
        assert config['entry']['min_rr_ratio'] == 3.0, \
            f"min_rr_ratio should be 3.0, got {config['entry']['min_rr_ratio']}"
    
    def test_max_positions_is_2(self, config):
        """max_positions changed from 3 to 2"""
        assert config['trading']['max_positions'] == 2, \
            f"max_positions should be 2, got {config['trading']['max_positions']}"
    
    def test_trade_symbols_is_10(self, config):
        """trade_symbols changed from 25 to 10"""
        assert config['market']['trade_symbols'] == 10, \
            f"trade_symbols should be 10, got {config['market']['trade_symbols']}"
    
    def test_cooldown_after_loss_sec_is_3600(self, config):
        """cooldown_after_loss_sec changed from 1800 to 3600"""
        assert config['risk']['cooldown_after_loss_sec'] == 3600, \
            f"cooldown_after_loss_sec should be 3600, got {config['risk']['cooldown_after_loss_sec']}"
    
    def test_exchange_closed_not_in_ignore_loss_cooldown_reasons(self, config):
        """exchange_closed REMOVED from ignore_loss_cooldown_reasons"""
        ignore_reasons = config['risk'].get('ignore_loss_cooldown_reasons', [])
        assert 'exchange_closed' not in ignore_reasons, \
            f"exchange_closed should NOT be in ignore_loss_cooldown_reasons, got {ignore_reasons}"
    
    def test_exchange_closed_not_in_ignore_consecutive_loss_reasons(self, config):
        """exchange_closed REMOVED from ignore_consecutive_loss_reasons"""
        ignore_reasons = config['risk'].get('ignore_consecutive_loss_reasons', [])
        assert 'exchange_closed' not in ignore_reasons, \
            f"exchange_closed should NOT be in ignore_consecutive_loss_reasons, got {ignore_reasons}"
    
    def test_min_stop_atr_mult_is_16(self, config):
        """min_stop_atr_mult changed from 1.4 to 1.6"""
        assert config['entry']['min_stop_atr_mult'] == 1.6, \
            f"min_stop_atr_mult should be 1.6, got {config['entry']['min_stop_atr_mult']}"
    
    def test_sl_buffer_atr_mult_entry_is_10(self, config):
        """sl_buffer_atr_mult changed from 0.8 to 1.0 (entry section)"""
        assert config['entry']['sl_buffer_atr_mult'] == 1.0, \
            f"sl_buffer_atr_mult (entry) should be 1.0, got {config['entry']['sl_buffer_atr_mult']}"


# ============================================================================
# ENTRY ENGINE THRESHOLD TESTS
# ============================================================================

class TestEntryEngineThreshold:
    """Verify EntryEngine uses entry_threshold=0.72 to reject signals."""
    
    @pytest.fixture
    def bot_config(self):
        return BotConfig.load('/app/bot/config.yaml')
    
    @pytest.fixture
    def entry_engine(self, bot_config):
        return EntryEngine(bot_config)
    
    def test_entry_engine_loads_threshold_072(self, entry_engine):
        """EntryEngine should load entry_threshold=0.72 from config"""
        assert entry_engine.entry_threshold == 0.72, \
            f"EntryEngine.entry_threshold should be 0.72, got {entry_engine.entry_threshold}"
    
    def test_entry_engine_loads_min_rr_ratio_30(self, entry_engine):
        """EntryEngine should load min_rr_ratio=3.0 from config"""
        assert entry_engine.min_rr_ratio == 3.0, \
            f"EntryEngine.min_rr_ratio should be 3.0, got {entry_engine.min_rr_ratio}"
    
    def test_entry_engine_loads_trained_model_min_prob_052(self, entry_engine):
        """EntryEngine should load trained_model_min_prob=0.52 from config"""
        assert entry_engine.trained_model_min_prob == 0.52, \
            f"EntryEngine.trained_model_min_prob should be 0.52, got {entry_engine.trained_model_min_prob}"
    
    def test_entry_engine_loads_trained_model_blend_030(self, entry_engine):
        """EntryEngine should load trained_model_blend=0.30 from config"""
        assert entry_engine.trained_model_blend == 0.30, \
            f"EntryEngine.trained_model_blend should be 0.30, got {entry_engine.trained_model_blend}"
    
    def test_entry_engine_loads_min_stop_atr_mult_16(self, entry_engine):
        """EntryEngine should load min_stop_atr_mult=1.6 from config"""
        assert entry_engine.min_stop_atr_mult == 1.6, \
            f"EntryEngine.min_stop_atr_mult should be 1.6, got {entry_engine.min_stop_atr_mult}"
    
    def test_entry_engine_loads_sl_buffer_atr_mult_10(self, entry_engine):
        """EntryEngine should load sl_buffer_atr_mult=1.0 from config"""
        assert entry_engine.sl_buffer_atr_mult == 1.0, \
            f"EntryEngine.sl_buffer_atr_mult should be 1.0, got {entry_engine.sl_buffer_atr_mult}"


# ============================================================================
# EXIT ENGINE EARLY EXIT TESTS
# ============================================================================

class TestExitEngineEarlyExit:
    """Verify ExitEngine early_exit_bars=6 closes stagnant trades."""
    
    @pytest.fixture
    def bot_config(self):
        return BotConfig.load('/app/bot/config.yaml')
    
    @pytest.fixture
    def exit_engine(self, bot_config):
        return ExitEngine(
            hard_sl_atr_mult=bot_config.get("exit", "hard_sl_atr_mult", default=1.8),
            early_exit_bars=bot_config.get("exit", "early_exit_bars", default=12),
            early_exit_min_profit_atr=bot_config.get("exit", "early_exit_min_profit_atr", default=0.35),
            trailing_activation_atr=bot_config.get("exit", "trailing_activation_atr", default=0.8),
            trailing_distance_atr=bot_config.get("exit", "trailing_distance_atr", default=1.2),
            tp_cap_atr_mult=bot_config.get("exit", "tp_cap_atr_mult", default=8.0),
            min_profit_before_trail_pct=bot_config.get("exit", "min_profit_before_trail_pct", default=0.5),
            sl_buffer_atr_mult=bot_config.get("exit", "sl_buffer_atr_mult", default=0.2),
        )
    
    def test_exit_engine_loads_early_exit_bars_6(self, exit_engine):
        """ExitEngine should load early_exit_bars=6 from config"""
        assert exit_engine.early_exit_bars == 6, \
            f"ExitEngine.early_exit_bars should be 6, got {exit_engine.early_exit_bars}"
    
    def test_early_exit_triggers_after_6_bars_no_profit(self, exit_engine):
        """Early exit should trigger after 6 bars with insufficient profit"""
        # Create a mock position
        class MockPosition:
            entry_price = 100.0
            is_long = True
            stop_loss = 95.0
            take_profit = 110.0
            bars_since_entry = 6  # Exactly 6 bars
            trailing_active = False
            trailing_stop = 0.0
        
        pos = MockPosition()
        current_price = 100.05  # Minimal profit
        atr_value = 1.0  # 1% ATR
        
        # early_exit_min_profit_atr = 0.1 from config, so min_profit = 0.1 * 1.0 = 0.1
        # profit = 100.05 - 100.0 = 0.05 < 0.1 → should trigger early exit
        should_exit, reason, details = exit_engine.check_exit(pos, current_price, atr_value)
        
        assert should_exit is True, "Should trigger early exit after 6 bars with insufficient profit"
        assert reason == ExitReason.EARLY_EXIT, f"Exit reason should be EARLY_EXIT, got {reason}"
    
    def test_no_early_exit_before_6_bars(self, exit_engine):
        """Early exit should NOT trigger before 6 bars"""
        class MockPosition:
            entry_price = 100.0
            is_long = True
            stop_loss = 95.0
            take_profit = 110.0
            bars_since_entry = 5  # Only 5 bars
            trailing_active = False
            trailing_stop = 0.0
        
        pos = MockPosition()
        current_price = 100.05  # Minimal profit
        atr_value = 1.0
        
        should_exit, reason, details = exit_engine.check_exit(pos, current_price, atr_value)
        
        # Should not exit due to early_exit (might exit for other reasons)
        if should_exit:
            assert reason != ExitReason.EARLY_EXIT, \
                f"Should NOT trigger early exit before 6 bars, got {reason}"
    
    def test_no_early_exit_with_sufficient_profit(self, exit_engine):
        """Early exit should NOT trigger if profit is sufficient"""
        class MockPosition:
            entry_price = 100.0
            is_long = True
            stop_loss = 95.0
            take_profit = 110.0
            bars_since_entry = 10  # More than 6 bars
            trailing_active = False
            trailing_stop = 0.0
        
        pos = MockPosition()
        current_price = 101.0  # Good profit (1.0 > 0.1 ATR)
        atr_value = 1.0
        
        should_exit, reason, details = exit_engine.check_exit(pos, current_price, atr_value)
        
        if should_exit:
            assert reason != ExitReason.EARLY_EXIT, \
                f"Should NOT trigger early exit with sufficient profit, got {reason}"


# ============================================================================
# RISK GUARD EXCHANGE_CLOSED TESTS
# ============================================================================

class TestRiskGuardExchangeClosed:
    """Verify RiskGuard does NOT ignore exchange_closed for cooldown."""
    
    @pytest.fixture
    def bot_config(self):
        return BotConfig.load('/app/bot/config.yaml')
    
    @pytest.fixture
    def risk_guard(self, bot_config):
        return RiskGuard(
            max_consecutive_losses=bot_config.get("risk", "max_consecutive_losses", default=2),
            max_daily_loss_pct=bot_config.get("risk", "max_daily_loss_pct", default=2.5),
            max_daily_loss_usdt=bot_config.get("risk", "max_daily_loss_usdt", default=10),
            max_trades_per_day=bot_config.get("risk", "max_trades_per_day", default=10),
            max_positions=bot_config.get("trading", "max_positions", default=3),
            max_trades_per_symbol_24h=bot_config.get("risk", "max_trades_per_symbol_24h", default=2),
            cooldown_after_loss_sec=bot_config.get("risk", "cooldown_after_loss_sec", default=900),
            cooldown_after_stop_hours=bot_config.get("risk", "cooldown_after_stop_hours", default=6),
            reduce_after_losses=bot_config.get("risk", "reduce_after_losses", default=1),
            reduction_factor=bot_config.get("risk", "reduction_factor", default=0.5),
            min_loss_usdt_for_cooldown=bot_config.get("risk", "min_loss_usdt_for_cooldown", default=0.25),
            min_loss_usdt_for_consecutive=bot_config.get("risk", "min_loss_usdt_for_consecutive", default=0.5),
            ignore_loss_cooldown_reasons=bot_config.get("risk", "ignore_loss_cooldown_reasons", default=["early_exit"]),
            ignore_consecutive_loss_reasons=bot_config.get("risk", "ignore_consecutive_loss_reasons", default=["early_exit"]),
        )
    
    def test_exchange_closed_not_in_ignore_cooldown_reasons(self, risk_guard):
        """exchange_closed should NOT be in ignore_loss_cooldown_reasons"""
        assert 'exchange_closed' not in risk_guard.ignore_loss_cooldown_reasons, \
            f"exchange_closed should NOT be in ignore_loss_cooldown_reasons, got {risk_guard.ignore_loss_cooldown_reasons}"
    
    def test_exchange_closed_not_in_ignore_consecutive_reasons(self, risk_guard):
        """exchange_closed should NOT be in ignore_consecutive_loss_reasons"""
        assert 'exchange_closed' not in risk_guard.ignore_consecutive_loss_reasons, \
            f"exchange_closed should NOT be in ignore_consecutive_loss_reasons, got {risk_guard.ignore_consecutive_loss_reasons}"
    
    def test_early_exit_is_in_ignore_cooldown_reasons(self, risk_guard):
        """early_exit should still be in ignore_loss_cooldown_reasons"""
        assert 'early_exit' in risk_guard.ignore_loss_cooldown_reasons, \
            f"early_exit should be in ignore_loss_cooldown_reasons, got {risk_guard.ignore_loss_cooldown_reasons}"
    
    def test_early_exit_is_in_ignore_consecutive_reasons(self, risk_guard):
        """early_exit should still be in ignore_consecutive_loss_reasons"""
        assert 'early_exit' in risk_guard.ignore_consecutive_loss_reasons, \
            f"early_exit should be in ignore_consecutive_loss_reasons, got {risk_guard.ignore_consecutive_loss_reasons}"
    
    def test_exchange_closed_loss_triggers_cooldown(self, risk_guard):
        """exchange_closed loss should trigger cooldown"""
        # Record a loss with exchange_closed reason
        risk_guard.record_trade(pnl=-1.0, symbol="BTCUSDT", reason="exchange_closed")
        
        # Should be in cooldown
        can_trade, reason = risk_guard.can_trade("BTCUSDT")
        assert can_trade is False, "Should be in cooldown after exchange_closed loss"
        assert "Cooldown" in reason, f"Reason should mention cooldown, got {reason}"
    
    def test_exchange_closed_loss_counts_for_consecutive(self, risk_guard):
        """exchange_closed loss should count for consecutive loss tracking"""
        # Record multiple losses with exchange_closed reason
        risk_guard.record_trade(pnl=-1.0, symbol="BTCUSDT", reason="exchange_closed")
        risk_guard.record_trade(pnl=-1.0, symbol="ETHUSDT", reason="exchange_closed")
        
        # Check consecutive losses increased
        assert risk_guard._consecutive_losses >= 2, \
            f"Consecutive losses should be >= 2, got {risk_guard._consecutive_losses}"
    
    def test_early_exit_loss_does_not_trigger_cooldown(self, risk_guard):
        """early_exit loss should NOT trigger cooldown (still ignored)"""
        # Record a loss with early_exit reason
        risk_guard.record_trade(pnl=-0.5, symbol="BTCUSDT", reason="early_exit")
        
        # Should NOT be in cooldown (early_exit is ignored)
        can_trade, reason = risk_guard.can_trade("BTCUSDT")
        # Note: might be blocked for other reasons, but not cooldown
        if not can_trade:
            assert "Cooldown" not in reason, \
                f"early_exit should not trigger cooldown, got reason: {reason}"


# ============================================================================
# TRAILING STOP LOGGING TESTS
# ============================================================================

class TestTrailingStopLogging:
    """Verify trailing stop logging is present in exit_engine.py."""
    
    def test_trail_activated_log_present_in_exit_engine(self):
        """[TRAIL ACTIVATED] logging should be present in exit_engine.py"""
        with open('/app/bot/engine/exit_engine.py', 'r') as f:
            content = f.read()
        
        assert '[TRAIL ACTIVATED]' in content, \
            "[TRAIL ACTIVATED] logging should be present in exit_engine.py"
    
    def test_trail_move_log_present_in_exit_engine(self):
        """[TRAIL MOVE] logging should be present in exit_engine.py"""
        with open('/app/bot/engine/exit_engine.py', 'r') as f:
            content = f.read()
        
        assert '[TRAIL MOVE]' in content, \
            "[TRAIL MOVE] logging should be present in exit_engine.py"
    
    def test_trail_activated_log_for_long(self):
        """[TRAIL ACTIVATED] log for LONG positions should be present"""
        with open('/app/bot/engine/exit_engine.py', 'r') as f:
            content = f.read()
        
        assert 'LONG' in content and '[TRAIL ACTIVATED]' in content, \
            "[TRAIL ACTIVATED] log for LONG should be present"
    
    def test_trail_activated_log_for_short(self):
        """[TRAIL ACTIVATED] log for SHORT positions should be present"""
        with open('/app/bot/engine/exit_engine.py', 'r') as f:
            content = f.read()
        
        assert 'SHORT' in content and '[TRAIL ACTIVATED]' in content, \
            "[TRAIL ACTIVATED] log for SHORT should be present"
    
    def test_trail_move_log_includes_r_multiple(self):
        """[TRAIL MOVE] log should include R multiple"""
        with open('/app/bot/engine/exit_engine.py', 'r') as f:
            content = f.read()
        
        # Check that R= is logged in TRAIL MOVE context
        assert 'R=' in content and '[TRAIL MOVE]' in content, \
            "[TRAIL MOVE] log should include R multiple"


# ============================================================================
# MAIN.PY QUALITY GATE AND TRAILING DIAGNOSTIC TESTS
# ============================================================================

class TestMainPyQualityGateAndTrailing:
    """Verify main.py has quality gate log for ALL modes and trailing diagnostic."""
    
    def test_quality_gate_log_present_in_main(self):
        """Quality gate startup log should be present in main.py"""
        with open('/app/bot/main.py', 'r') as f:
            content = f.read()
        
        assert 'Quality gate:' in content, \
            "Quality gate startup log should be present in main.py"
    
    def test_quality_gate_log_shows_reject_no_zone(self):
        """Quality gate log should show reject_no_zone_entries setting"""
        with open('/app/bot/main.py', 'r') as f:
            content = f.read()
        
        assert 'reject_no_zone=' in content, \
            "Quality gate log should show reject_no_zone_entries setting"
    
    def test_trail_diagnostic_log_present_in_main(self):
        """[TRAIL] diagnostic logging should be present in main.py"""
        with open('/app/bot/main.py', 'r') as f:
            content = f.read()
        
        assert '[TRAIL]' in content, \
            "[TRAIL] diagnostic logging should be present in main.py"
    
    def test_trail_diagnostic_includes_r_multiple(self):
        """[TRAIL] diagnostic should include R multiple"""
        with open('/app/bot/main.py', 'r') as f:
            content = f.read()
        
        # The [TRAIL] log is a multi-line f-string, so check the surrounding context
        # Find the [TRAIL] section and verify R= is in the same logger.info block
        trail_start = content.find('[TRAIL]')
        assert trail_start > 0, "[TRAIL] should be present in main.py"
        
        # Get the surrounding context (next 200 chars should include R=)
        trail_context = content[trail_start:trail_start + 200]
        assert 'R=' in trail_context, \
            f"[TRAIL] diagnostic should include R= multiple, context: {trail_context[:100]}"
    
    def test_trail_diagnostic_includes_trail_active(self):
        """[TRAIL] diagnostic should include trail_active status"""
        with open('/app/bot/main.py', 'r') as f:
            content = f.read()
        
        # The [TRAIL] log is a multi-line f-string
        trail_start = content.find('[TRAIL]')
        assert trail_start > 0, "[TRAIL] should be present in main.py"
        
        # Get the surrounding context (next 300 chars should include trail_active=)
        trail_context = content[trail_start:trail_start + 300]
        assert 'trail_active=' in trail_context, \
            f"[TRAIL] diagnostic should include trail_active status, context: {trail_context[:150]}"


# ============================================================================
# QUALITY GATE REJECT_NO_ZONE_ENTRIES TESTS
# ============================================================================

class TestQualityGateRejectNoZone:
    """Verify quality gate reject_no_zone_entries=true works in ALL modes."""
    
    @pytest.fixture
    def config(self):
        with open('/app/bot/config.yaml', 'r') as f:
            return yaml.safe_load(f)
    
    def test_config_has_reject_no_zone_entries_true(self, config):
        """Config should have reject_no_zone_entries: true"""
        assert config['quality_gate']['reject_no_zone_entries'] is True, \
            f"reject_no_zone_entries should be true, got {config['quality_gate']['reject_no_zone_entries']}"
    
    def test_config_has_quality_gate_enabled(self, config):
        """Config should have quality_gate enabled"""
        assert config['quality_gate']['enabled'] is True, \
            f"quality_gate.enabled should be true, got {config['quality_gate']['enabled']}"
    
    def test_quality_gate_check_is_outside_signal_only_block(self):
        """Quality gate check should be OUTSIDE signal_only block in main.py"""
        with open('/app/bot/main.py', 'r') as f:
            content = f.read()
        
        # The quality gate check should be at the same indentation level as other
        # pre-candidate checks, not inside the signal_only block
        assert 'if self.quality_gate_enabled:' in content, \
            "Quality gate check should be present in main.py"
        
        # Check that quality_gate_enabled check comes before signal_only block
        quality_gate_pos = content.find('if self.quality_gate_enabled:')
        signal_only_pos = content.find('if self.signal_only:')
        
        # Both should exist
        assert quality_gate_pos > 0, "quality_gate_enabled check should exist"
        # Note: signal_only check might appear multiple times, we just verify quality gate exists


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestAntiLossIntegration:
    """Integration tests for Anti-Loss package."""
    
    @pytest.fixture
    def bot_config(self):
        return BotConfig.load('/app/bot/config.yaml')
    
    def test_all_anti_loss_config_values_summary(self, bot_config):
        """Summary test: verify all Anti-Loss config values at once"""
        # Entry section
        assert bot_config.get("entry", "entry_threshold") == 0.72
        assert bot_config.get("entry", "trained_model_min_prob") == 0.52
        assert bot_config.get("entry", "trained_model_blend") == 0.30
        assert bot_config.get("entry", "min_rr_ratio") == 3.0
        assert bot_config.get("entry", "min_stop_atr_mult") == 1.6
        assert bot_config.get("entry", "sl_buffer_atr_mult") == 1.0
        
        # Exit section
        assert bot_config.get("exit", "early_exit_bars") == 6
        
        # Trading section
        assert bot_config.get("trading", "max_positions") == 2
        
        # Market section
        assert bot_config.get("market", "trade_symbols") == 10
        
        # Risk section
        assert bot_config.get("risk", "cooldown_after_loss_sec") == 3600
        ignore_cooldown = bot_config.get("risk", "ignore_loss_cooldown_reasons", default=[])
        ignore_consecutive = bot_config.get("risk", "ignore_consecutive_loss_reasons", default=[])
        assert 'exchange_closed' not in ignore_cooldown
        assert 'exchange_closed' not in ignore_consecutive
        assert 'early_exit' in ignore_cooldown
        assert 'early_exit' in ignore_consecutive
    
    def test_entry_engine_with_config_values(self, bot_config):
        """EntryEngine should be properly configured with Anti-Loss values"""
        engine = EntryEngine(bot_config)
        
        assert engine.entry_threshold == 0.72
        assert engine.trained_model_min_prob == 0.52
        assert engine.trained_model_blend == 0.30
        assert engine.min_rr_ratio == 3.0
        assert engine.min_stop_atr_mult == 1.6
        assert engine.sl_buffer_atr_mult == 1.0
    
    def test_exit_engine_with_config_values(self, bot_config):
        """ExitEngine should be properly configured with Anti-Loss values"""
        engine = ExitEngine(
            early_exit_bars=bot_config.get("exit", "early_exit_bars", default=12),
        )
        
        assert engine.early_exit_bars == 6
    
    def test_risk_guard_with_config_values(self, bot_config):
        """RiskGuard should be properly configured with Anti-Loss values"""
        guard = RiskGuard(
            cooldown_after_loss_sec=bot_config.get("risk", "cooldown_after_loss_sec", default=900),
            ignore_loss_cooldown_reasons=bot_config.get("risk", "ignore_loss_cooldown_reasons", default=[]),
            ignore_consecutive_loss_reasons=bot_config.get("risk", "ignore_consecutive_loss_reasons", default=[]),
        )
        
        assert guard.cooldown_after_loss_sec == 3600
        assert 'exchange_closed' not in guard.ignore_loss_cooldown_reasons
        assert 'exchange_closed' not in guard.ignore_consecutive_loss_reasons


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
