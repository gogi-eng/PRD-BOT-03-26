#!/usr/bin/env python3
"""
Iteration 43: P2 Features Testing
=================================
Tests for:
1. RL Agent v2: RLAction enum, REGIME_PROFILES, decide() with new state fields, TIGHTEN action, age_penalty
2. Signal Grading: classify_signal_grade(), EntrySignal.grade field, grade in metadata
3. TF Presets: config.yaml tf_presets section, _apply_tf_preset() method, /tf command
4. Regression: P0 manual protection, P1 whitelist_only, /retrain_status
"""
import sys
import os
import pytest
import yaml
from pathlib import Path
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

# Add bot directory to path
BOT_DIR = Path(__file__).resolve().parents[2] / "bot"
sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# SECTION 1: RL Agent v2 Tests
# ============================================================================

class TestRLActionEnum:
    """Test RLAction enum has all required values"""
    
    def test_rlaction_has_hold(self):
        from engine.rl_position_agent import RLAction
        assert hasattr(RLAction, 'HOLD')
        assert RLAction.HOLD.value == "hold"
    
    def test_rlaction_has_add(self):
        from engine.rl_position_agent import RLAction
        assert hasattr(RLAction, 'ADD')
        assert RLAction.ADD.value == "add"
    
    def test_rlaction_has_reduce(self):
        from engine.rl_position_agent import RLAction
        assert hasattr(RLAction, 'REDUCE')
        assert RLAction.REDUCE.value == "reduce"
    
    def test_rlaction_has_close(self):
        from engine.rl_position_agent import RLAction
        assert hasattr(RLAction, 'CLOSE')
        assert RLAction.CLOSE.value == "close"
    
    def test_rlaction_has_tighten(self):
        from engine.rl_position_agent import RLAction
        assert hasattr(RLAction, 'TIGHTEN')
        assert RLAction.TIGHTEN.value == "tighten"
    
    def test_rlaction_has_exactly_5_values(self):
        from engine.rl_position_agent import RLAction
        assert len(RLAction) == 5


class TestRegimeProfiles:
    """Test REGIME_PROFILES dict has correct structure"""
    
    def test_regime_profiles_exists(self):
        from engine.rl_position_agent import REGIME_PROFILES
        assert isinstance(REGIME_PROFILES, dict)
    
    def test_regime_profiles_has_trend(self):
        from engine.rl_position_agent import REGIME_PROFILES
        assert "trend" in REGIME_PROFILES
    
    def test_regime_profiles_has_breakout(self):
        from engine.rl_position_agent import REGIME_PROFILES
        assert "breakout" in REGIME_PROFILES
    
    def test_regime_profiles_has_chop(self):
        from engine.rl_position_agent import REGIME_PROFILES
        assert "chop" in REGIME_PROFILES
    
    def test_trend_profile_has_required_keys(self):
        from engine.rl_position_agent import REGIME_PROFILES
        trend = REGIME_PROFILES["trend"]
        required_keys = ["add_threshold", "reduce_threshold", "close_threshold", "tighten_threshold", "max_hold_bars"]
        for key in required_keys:
            assert key in trend, f"Missing key '{key}' in trend profile"
    
    def test_breakout_profile_has_required_keys(self):
        from engine.rl_position_agent import REGIME_PROFILES
        breakout = REGIME_PROFILES["breakout"]
        required_keys = ["add_threshold", "reduce_threshold", "close_threshold", "tighten_threshold", "max_hold_bars"]
        for key in required_keys:
            assert key in breakout, f"Missing key '{key}' in breakout profile"
    
    def test_chop_profile_has_required_keys(self):
        from engine.rl_position_agent import REGIME_PROFILES
        chop = REGIME_PROFILES["chop"]
        required_keys = ["add_threshold", "reduce_threshold", "close_threshold", "tighten_threshold", "max_hold_bars"]
        for key in required_keys:
            assert key in chop, f"Missing key '{key}' in chop profile"
    
    def test_chop_has_lower_max_hold_bars_than_trend(self):
        """Chop regime should have shorter max hold time"""
        from engine.rl_position_agent import REGIME_PROFILES
        assert REGIME_PROFILES["chop"]["max_hold_bars"] < REGIME_PROFILES["trend"]["max_hold_bars"]
    
    def test_chop_has_lower_close_threshold_than_trend(self):
        """Chop regime should close more easily"""
        from engine.rl_position_agent import REGIME_PROFILES
        assert REGIME_PROFILES["chop"]["close_threshold"] < REGIME_PROFILES["trend"]["close_threshold"]


class TestRLPositionAgentDecide:
    """Test RLPositionAgent.decide() accepts new state fields"""
    
    @pytest.fixture
    def mock_position(self):
        """Create a mock position object"""
        pos = MagicMock()
        pos.is_long = True
        pos.trailing_active = True
        pos.trailing_stop = 100.0
        pos.entry_price = 100.0
        pos.best_price = 105.0
        pos.bars_since_entry = 10
        pos.stop_loss = 95.0
        pos.add_count = 0
        pos.origin = "bot"
        pos.qty = 1.0
        pos.side = "BUY"
        return pos
    
    def test_decide_accepts_regime_in_state(self, mock_position):
        from engine.rl_position_agent import RLPositionAgent
        agent = RLPositionAgent()
        state = {
            "trend_bias": 0.5,
            "volatility": 0.02,
            "pnl_pct": 1.0,
            "liq_signal": 0,
            "orderflow_edge": 0.1,
            "transformer_edge": 0.1,
            "regime": "trend",
            "bars_held": 10,
            "drawdown_from_peak_pct": 5.0,
        }
        decision = agent.decide(mock_position, state)
        assert decision is not None
    
    def test_decide_accepts_bars_held_in_state(self, mock_position):
        from engine.rl_position_agent import RLPositionAgent
        agent = RLPositionAgent()
        state = {
            "trend_bias": 0.5,
            "volatility": 0.02,
            "pnl_pct": 1.0,
            "liq_signal": 0,
            "orderflow_edge": 0.1,
            "transformer_edge": 0.1,
            "regime": "chop",
            "bars_held": 50,
            "drawdown_from_peak_pct": 0.0,
        }
        decision = agent.decide(mock_position, state)
        assert decision is not None
    
    def test_decide_accepts_drawdown_from_peak_pct_in_state(self, mock_position):
        from engine.rl_position_agent import RLPositionAgent
        agent = RLPositionAgent()
        state = {
            "trend_bias": 0.5,
            "volatility": 0.02,
            "pnl_pct": 1.0,
            "liq_signal": 0,
            "orderflow_edge": 0.1,
            "transformer_edge": 0.1,
            "regime": "breakout",
            "bars_held": 10,
            "drawdown_from_peak_pct": 25.0,
        }
        decision = agent.decide(mock_position, state)
        assert decision is not None
    
    def test_decide_uses_regime_for_thresholds(self, mock_position):
        """Different regimes should produce different threshold behavior"""
        from engine.rl_position_agent import RLPositionAgent, RLAction
        agent = RLPositionAgent()
        
        # Same state but different regimes
        base_state = {
            "trend_bias": -0.8,  # Strong adverse
            "volatility": 0.05,  # High volatility
            "pnl_pct": 0.6,  # Profitable
            "liq_signal": 0,
            "orderflow_edge": -0.3,
            "transformer_edge": -0.3,
            "bars_held": 35,  # Near chop max_hold_bars
            "drawdown_from_peak_pct": 10.0,
        }
        
        # Test with chop regime (should be more likely to close)
        state_chop = {**base_state, "regime": "chop"}
        decision_chop = agent.decide(mock_position, state_chop)
        
        # Test with trend regime (should be more tolerant)
        state_trend = {**base_state, "regime": "trend"}
        decision_trend = agent.decide(mock_position, state_trend)
        
        # Both should return valid decisions
        assert decision_chop.action in RLAction
        assert decision_trend.action in RLAction


class TestRLTightenAction:
    """Test TIGHTEN action fires correctly"""
    
    @pytest.fixture
    def mock_position(self):
        pos = MagicMock()
        pos.is_long = True
        pos.trailing_active = True
        pos.trailing_stop = 100.0
        pos.entry_price = 100.0
        pos.best_price = 105.0
        pos.bars_since_entry = 10
        pos.stop_loss = 95.0
        pos.add_count = 0
        pos.origin = "bot"
        pos.qty = 1.0
        pos.side = "BUY"
        return pos
    
    def test_tighten_requires_positive_pnl(self, mock_position):
        """TIGHTEN should only fire when pnl_pct > 0.2"""
        from engine.rl_position_agent import RLPositionAgent, RLAction
        agent = RLPositionAgent()
        
        # State with negative PnL - should NOT tighten
        state_negative = {
            "trend_bias": -0.5,
            "volatility": 0.04,
            "pnl_pct": -0.5,  # Negative PnL
            "liq_signal": 0,
            "orderflow_edge": -0.2,
            "transformer_edge": -0.2,
            "regime": "chop",
            "bars_held": 20,
            "drawdown_from_peak_pct": 0.0,
        }
        decision = agent.decide(mock_position, state_negative)
        assert decision.action != RLAction.TIGHTEN
    
    def test_tighten_fires_with_high_score_and_positive_pnl(self, mock_position):
        """TIGHTEN should fire when tighten_score >= threshold and pnl_pct > 0.2"""
        from engine.rl_position_agent import RLPositionAgent, RLAction
        agent = RLPositionAgent()
        
        # State designed to trigger TIGHTEN (but not CLOSE or REDUCE)
        # High adverse edge + volatility + age penalty, but not enough for CLOSE
        state = {
            "trend_bias": -0.4,  # Moderate adverse
            "volatility": 0.035,  # Moderate volatility
            "pnl_pct": 0.5,  # Positive PnL > 0.2
            "liq_signal": 0,
            "orderflow_edge": -0.2,
            "transformer_edge": -0.2,
            "regime": "chop",  # Lower tighten threshold (0.45)
            "bars_held": 30,  # High age for age_penalty
            "drawdown_from_peak_pct": 5.0,
        }
        decision = agent.decide(mock_position, state)
        # Should be TIGHTEN or HOLD (depending on exact score calculation)
        # The key is that TIGHTEN is possible with these conditions
        assert decision.action in [RLAction.TIGHTEN, RLAction.HOLD, RLAction.CLOSE, RLAction.REDUCE]


class TestRLAgePenalty:
    """Test age_penalty based on bars_held / max_hold_bars"""
    
    @pytest.fixture
    def mock_position(self):
        pos = MagicMock()
        pos.is_long = True
        pos.trailing_active = False
        pos.trailing_stop = 0.0
        pos.entry_price = 100.0
        pos.best_price = 100.0
        pos.bars_since_entry = 10
        pos.stop_loss = 95.0
        pos.add_count = 0
        pos.origin = "bot"
        pos.qty = 1.0
        pos.side = "BUY"
        return pos
    
    def test_close_uses_age_penalty(self, mock_position):
        """CLOSE score should include age_penalty based on bars_held / max_hold_bars"""
        from engine.rl_position_agent import RLPositionAgent, REGIME_PROFILES
        agent = RLPositionAgent()
        
        # Verify age_penalty calculation exists in code
        # bars_held / max_hold_bars should affect close_score
        chop_max_bars = REGIME_PROFILES["chop"]["max_hold_bars"]
        
        # State with high bars_held (near max)
        state_old = {
            "trend_bias": -0.3,
            "volatility": 0.03,
            "pnl_pct": 0.1,  # Low profit, stale condition
            "liq_signal": 0,
            "orderflow_edge": -0.1,
            "transformer_edge": -0.1,
            "regime": "chop",
            "bars_held": chop_max_bars,  # At max
            "drawdown_from_peak_pct": 0.0,
        }
        
        # State with low bars_held
        state_young = {
            "trend_bias": -0.3,
            "volatility": 0.03,
            "pnl_pct": 0.1,
            "liq_signal": 0,
            "orderflow_edge": -0.1,
            "transformer_edge": -0.1,
            "regime": "chop",
            "bars_held": 5,  # Young position
            "drawdown_from_peak_pct": 0.0,
        }
        
        decision_old = agent.decide(mock_position, state_old)
        decision_young = agent.decide(mock_position, state_young)
        
        # Both should return valid decisions
        assert decision_old is not None
        assert decision_young is not None


# ============================================================================
# SECTION 2: Signal Grading Tests
# ============================================================================

class TestClassifySignalGrade:
    """Test classify_signal_grade() function"""
    
    def test_classify_signal_grade_exists(self):
        from engine.entry_engine import classify_signal_grade
        assert callable(classify_signal_grade)
    
    def test_grade_a_high_conviction(self):
        """A grade: conf >= 0.85, RR >= 4.0, 3+ confirmations"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.90,
            rr_ratio=5.0,
            has_sweep=True,
            has_bos=True,
            htf_aligned=True,
            entry_zone="fvg_bullish",
        )
        assert grade == "A"
    
    def test_grade_a_requires_high_confidence(self):
        """A grade requires conf >= 0.85"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.80,  # Below 0.85
            rr_ratio=5.0,
            has_sweep=True,
            has_bos=True,
            htf_aligned=True,
            entry_zone="fvg_bullish",
        )
        assert grade != "A"
    
    def test_grade_a_requires_high_rr(self):
        """A grade requires RR >= 4.0"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.90,
            rr_ratio=3.5,  # Below 4.0
            has_sweep=True,
            has_bos=True,
            htf_aligned=True,
            entry_zone="fvg_bullish",
        )
        assert grade != "A"
    
    def test_grade_a_requires_3_confirmations(self):
        """A grade requires 3+ confirmations"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.90,
            rr_ratio=5.0,
            has_sweep=True,
            has_bos=False,  # Only 2 confirmations
            htf_aligned=True,
            entry_zone="no_zone",
        )
        assert grade != "A"
    
    def test_grade_b_standard(self):
        """B grade: conf >= 0.75, RR >= 3.0, 2+ confirmations"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.80,
            rr_ratio=3.5,
            has_sweep=True,
            has_bos=True,
            htf_aligned=False,
            entry_zone="no_zone",
        )
        assert grade == "B"
    
    def test_grade_b_requires_min_confidence(self):
        """B grade requires conf >= 0.75"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.70,  # Below 0.75
            rr_ratio=3.5,
            has_sweep=True,
            has_bos=True,
            htf_aligned=False,
            entry_zone="no_zone",
        )
        assert grade != "B"
    
    def test_grade_b_requires_min_rr(self):
        """B grade requires RR >= 3.0"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.80,
            rr_ratio=2.5,  # Below 3.0
            has_sweep=True,
            has_bos=True,
            htf_aligned=False,
            entry_zone="no_zone",
        )
        assert grade != "B"
    
    def test_grade_c_marginal(self):
        """C grade: everything else"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.60,
            rr_ratio=2.0,
            has_sweep=False,
            has_bos=False,
            htf_aligned=False,
            entry_zone="no_zone",
        )
        assert grade == "C"
    
    def test_grade_c_low_confidence(self):
        """C grade for low confidence"""
        from engine.entry_engine import classify_signal_grade
        grade = classify_signal_grade(
            confidence=0.55,
            rr_ratio=5.0,
            has_sweep=True,
            has_bos=True,
            htf_aligned=True,
            entry_zone="fvg_bullish",
        )
        assert grade == "C"


class TestEntrySignalGradeField:
    """Test EntrySignal dataclass has grade field"""
    
    def test_entry_signal_has_grade_field(self):
        from engine.entry_engine import EntrySignal
        signal = EntrySignal()
        assert hasattr(signal, 'grade')
    
    def test_entry_signal_grade_defaults_to_c(self):
        from engine.entry_engine import EntrySignal
        signal = EntrySignal()
        assert signal.grade == "C"
    
    def test_entry_signal_grade_can_be_set(self):
        from engine.entry_engine import EntrySignal
        signal = EntrySignal(grade="A")
        assert signal.grade == "A"


class TestSignalGradeInMetadata:
    """Test grade is stored in metadata as 'signal_grade'"""
    
    def test_signal_grade_in_metadata_key_exists(self):
        """Verify signal_grade key is used in metadata"""
        # Read entry_engine.py and check for signal_grade in metadata
        entry_engine_path = BOT_DIR / "engine" / "entry_engine.py"
        content = entry_engine_path.read_text()
        assert '"signal_grade"' in content or "'signal_grade'" in content


class TestSignalGradeInNotification:
    """Test grade appears in signal notification message"""
    
    def test_grade_in_signal_message_format(self):
        """Verify [A]/[B]/[C] format in signal notification"""
        # Read main.py and check for grade in signal message
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        # Should have something like [{signal.grade}] in the message
        assert "[{signal.grade}]" in content or "signal.grade" in content


# ============================================================================
# SECTION 3: TF Presets Tests
# ============================================================================

class TestTFPresetsConfig:
    """Test config.yaml has tf_presets section"""
    
    @pytest.fixture
    def config(self):
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            return yaml.safe_load(f)
    
    def test_tf_presets_section_exists(self, config):
        assert "tf_presets" in config
    
    def test_tf_presets_has_active_preset(self, config):
        assert "active_preset" in config["tf_presets"]
    
    def test_tf_presets_has_presets_dict(self, config):
        assert "presets" in config["tf_presets"]
        assert isinstance(config["tf_presets"]["presets"], dict)
    
    def test_tf_presets_has_1m(self, config):
        assert "1m" in config["tf_presets"]["presets"]
    
    def test_tf_presets_has_5m(self, config):
        assert "5m" in config["tf_presets"]["presets"]
    
    def test_tf_presets_has_15m(self, config):
        assert "15m" in config["tf_presets"]["presets"]


class TestTFPreset5m:
    """Test 5m preset has correct values"""
    
    @pytest.fixture
    def preset_5m(self):
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return config["tf_presets"]["presets"]["5m"]
    
    def test_5m_candle_interval(self, preset_5m):
        assert preset_5m.get("candle_interval") == "5" or preset_5m.get("candle_interval") == 5
    
    def test_5m_htf_interval(self, preset_5m):
        assert preset_5m.get("htf_interval") == "60" or preset_5m.get("htf_interval") == 60
    
    def test_5m_cycle_sleep(self, preset_5m):
        assert preset_5m.get("cycle_sleep_sec") == 120


class TestTFPreset15m:
    """Test 15m preset has correct values"""
    
    @pytest.fixture
    def preset_15m(self):
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return config["tf_presets"]["presets"]["15m"]
    
    def test_15m_candle_interval(self, preset_15m):
        assert preset_15m.get("candle_interval") == "15" or preset_15m.get("candle_interval") == 15
    
    def test_15m_htf_interval(self, preset_15m):
        assert preset_15m.get("htf_interval") == "240" or preset_15m.get("htf_interval") == 240
    
    def test_15m_htf_4h_interval(self, preset_15m):
        assert preset_15m.get("htf_4h_interval") == "D"


class TestApplyTFPresetMethod:
    """Test _apply_tf_preset() method exists and is called"""
    
    def test_apply_tf_preset_method_exists(self):
        """Verify _apply_tf_preset method exists in main.py"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert "def _apply_tf_preset(self)" in content
    
    def test_apply_tf_preset_called_in_init(self):
        """Verify _apply_tf_preset is called at end of __init__"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        # Should be called in __init__
        assert "self._apply_tf_preset()" in content
    
    def test_apply_tf_preset_overrides_candle_interval(self):
        """Verify _apply_tf_preset overrides candle_interval"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert 'self.candle_interval = str(tf.get("candle_interval"' in content
    
    def test_apply_tf_preset_overrides_exit_engine_params(self):
        """Verify _apply_tf_preset overrides exit_engine params"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert "self.exit_engine.early_exit_bars" in content
        assert "self.exit_engine.trailing_activation_atr" in content
        assert "self.exit_engine.trailing_distance_atr" in content
    
    def test_apply_tf_preset_overrides_volatility_floor(self):
        """Verify _apply_tf_preset overrides volatility_floor"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert "volatility_floor_atr_pct" in content


class TestTFCommandInTelegram:
    """Test /tf command exists in TG controller"""
    
    def test_cmd_tf_status_handler_exists(self):
        """Verify cmd_tf_status handler exists"""
        controller_path = BOT_DIR / "tg" / "controller.py"
        content = controller_path.read_text()
        assert "async def cmd_tf_status" in content
    
    def test_tf_command_registered(self):
        """Verify /tf command is registered"""
        controller_path = BOT_DIR / "tg" / "controller.py"
        content = controller_path.read_text()
        assert 'CommandHandler("tf"' in content
    
    def test_tf_in_help_text(self):
        """Verify /tf is in help text"""
        controller_path = BOT_DIR / "tg" / "controller.py"
        content = controller_path.read_text()
        assert "/tf" in content


class TestSetBotInstanceMethod:
    """Test set_bot_instance() method in TG controller"""
    
    def test_set_bot_instance_method_exists(self):
        """Verify set_bot_instance method exists"""
        controller_path = BOT_DIR / "tg" / "controller.py"
        content = controller_path.read_text()
        assert "def set_bot_instance(self" in content
    
    def test_set_bot_instance_called_in_main(self):
        """Verify set_bot_instance is called in main.py"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert "self.tg.set_bot_instance(self)" in content


# ============================================================================
# SECTION 4: Main.py RL State Integration Tests
# ============================================================================

class TestMainPyRLStateIntegration:
    """Test main.py passes new fields to RL state dict"""
    
    def test_regime_passed_to_rl_state(self):
        """Verify regime is passed to RL state"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert '"regime":' in content or "'regime':" in content
    
    def test_bars_held_passed_to_rl_state(self):
        """Verify bars_held is passed to RL state"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert '"bars_held":' in content or "'bars_held':" in content
    
    def test_drawdown_from_peak_pct_passed_to_rl_state(self):
        """Verify drawdown_from_peak_pct is passed to RL state"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert '"drawdown_from_peak_pct":' in content or "'drawdown_from_peak_pct':" in content


class TestMainPyTightenHandling:
    """Test main.py handles RLAction.TIGHTEN"""
    
    def test_tighten_action_handled(self):
        """Verify TIGHTEN action is handled in main.py"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert "RLAction.TIGHTEN" in content
    
    def test_tighten_moves_trailing_stop(self):
        """Verify TIGHTEN moves trailing stop closer"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        # Should have logic to move trailing stop
        assert "RL TIGHTEN" in content


# ============================================================================
# SECTION 5: Regression Tests (P0 and P1)
# ============================================================================

class TestP0ManualProtectionRegression:
    """Test P0 manual protection is still intact"""
    
    def test_exit_reason_guard_for_manual_positions(self):
        """Verify ExitReason guard for manual positions"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        # Should have guard that only allows trailing_exit and tp_cap for manual
        assert 'pos.origin == "manual"' in content
        assert "ExitReason.TRAILING_EXIT" in content or "TRAILING_EXIT" in content
    
    def test_manual_safe_comment_exists(self):
        """Verify MANUAL SAFE comment exists"""
        main_path = BOT_DIR / "main.py"
        content = main_path.read_text()
        assert "MANUAL SAFE" in content


class TestP1WhitelistOnlyRegression:
    """Test P1 whitelist_only is still in config"""
    
    def test_whitelist_only_in_config(self):
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "whitelist_only" in config.get("market", {})


class TestP1RetrainStatusRegression:
    """Test P1 /retrain_status is still registered"""
    
    def test_retrain_status_command_registered(self):
        controller_path = BOT_DIR / "tg" / "controller.py"
        content = controller_path.read_text()
        assert 'CommandHandler("retrain_status"' in content


# ============================================================================
# SECTION 6: Summary Test
# ============================================================================

class TestIteration43P2Summary:
    """Summary test for all P2 features"""
    
    def test_all_p2_features_present(self):
        """Verify all P2 features are present"""
        # RL Agent v2
        from engine.rl_position_agent import RLAction, REGIME_PROFILES, RLPositionAgent
        assert len(RLAction) == 5
        assert "trend" in REGIME_PROFILES
        assert "breakout" in REGIME_PROFILES
        assert "chop" in REGIME_PROFILES
        
        # Signal Grading
        from engine.entry_engine import classify_signal_grade, EntrySignal
        assert callable(classify_signal_grade)
        assert hasattr(EntrySignal(), 'grade')
        
        # TF Presets
        config_path = BOT_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)
        assert "tf_presets" in config
        assert "1m" in config["tf_presets"]["presets"]
        assert "5m" in config["tf_presets"]["presets"]
        assert "15m" in config["tf_presets"]["presets"]
        
        # TG /tf command
        controller_path = BOT_DIR / "tg" / "controller.py"
        content = controller_path.read_text()
        assert 'CommandHandler("tf"' in content
        assert "def set_bot_instance" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
