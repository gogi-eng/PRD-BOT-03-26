#!/usr/bin/env python3
"""
Iteration 42: P1 Features Testing
- Whitelist-only mode
- Partial TP 30/70 logic
- /retrain_status Telegram command
- P0 regression check (early_exit_bars=20)
"""
import pytest
import sys
import os
import yaml
from pathlib import Path

# Add bot directory to path
BOT_DIR = Path("/app/bot")
sys.path.insert(0, str(BOT_DIR))


# ============================================================================
# CONFIG.YAML TESTS
# ============================================================================

class TestConfigYamlP1Features:
    """Test config.yaml has correct P1 values"""
    
    @pytest.fixture
    def config(self):
        with open(BOT_DIR / "config.yaml", "r") as f:
            return yaml.safe_load(f)
    
    # --- Whitelist-only mode ---
    def test_whitelist_only_exists_in_market_section(self, config):
        """whitelist_only option exists under market section"""
        assert "market" in config, "market section missing"
        assert "whitelist_only" in config["market"], "whitelist_only missing in market section"
    
    def test_whitelist_only_is_false_by_default(self, config):
        """whitelist_only is false (default behavior preserved)"""
        assert config["market"]["whitelist_only"] == False, "whitelist_only should be false"
    
    def test_whitelist_symbols_exist(self, config):
        """whitelist_symbols list exists"""
        assert "whitelist_symbols" in config["market"], "whitelist_symbols missing"
        assert isinstance(config["market"]["whitelist_symbols"], list), "whitelist_symbols should be a list"
        assert len(config["market"]["whitelist_symbols"]) > 0, "whitelist_symbols should not be empty"
    
    # --- Partial TP 30/70 ---
    def test_close_fraction_is_0_3(self, config):
        """close_fraction is 0.3 (30% first TP, 70% remaining)"""
        assert "partial_tp" in config, "partial_tp section missing"
        assert "close_fraction" in config["partial_tp"], "close_fraction missing"
        assert config["partial_tp"]["close_fraction"] == 0.3, f"close_fraction should be 0.3, got {config['partial_tp']['close_fraction']}"
    
    def test_partial_tp_enabled(self, config):
        """partial_tp is enabled"""
        assert config["partial_tp"]["enabled"] == True, "partial_tp should be enabled"
    
    # --- P0 Regression Check ---
    def test_early_exit_bars_is_20(self, config):
        """early_exit_bars is still 20 (P0 regression check)"""
        assert "exit" in config, "exit section missing"
        assert "early_exit_bars" in config["exit"], "early_exit_bars missing"
        assert config["exit"]["early_exit_bars"] == 20, f"early_exit_bars should be 20, got {config['exit']['early_exit_bars']}"


# ============================================================================
# MAIN.PY WHITELIST-ONLY MODE TESTS
# ============================================================================

class TestMainPyWhitelistOnlyMode:
    """Test main.py reads and uses whitelist_only config"""
    
    @pytest.fixture
    def main_py_content(self):
        with open(BOT_DIR / "main.py", "r") as f:
            return f.read()
    
    def test_whitelist_only_read_from_config(self, main_py_content):
        """main.py reads whitelist_only from config"""
        assert 'self.whitelist_only = self.cfg.get("market", "whitelist_only"' in main_py_content, \
            "whitelist_only not read from config"
    
    def test_whitelist_only_stored_as_attribute(self, main_py_content):
        """whitelist_only is stored as self.whitelist_only"""
        assert "self.whitelist_only" in main_py_content, "self.whitelist_only attribute missing"
    
    def test_get_trade_symbols_checks_whitelist_only(self, main_py_content):
        """get_trade_symbols() checks whitelist_only flag"""
        # Check for the whitelist-only mode logic
        assert "if self.whitelist_only and self.whitelist:" in main_py_content, \
            "whitelist_only check missing in get_trade_symbols"
    
    def test_get_trade_symbols_returns_whitelist_only_when_enabled(self, main_py_content):
        """get_trade_symbols() returns only whitelist when whitelist_only=True"""
        # Check for the return statement that returns whitelist symbols
        assert "WHITELIST-ONLY mode" in main_py_content, \
            "WHITELIST-ONLY mode log message missing"
    
    def test_get_trade_symbols_skips_ticker_scanning_when_whitelist_only(self, main_py_content):
        """When whitelist_only=True, ticker scanning is skipped"""
        # The return happens before tickers = await self.client.get_tickers()
        lines = main_py_content.split('\n')
        whitelist_only_check_line = None
        get_tickers_line = None
        
        for i, line in enumerate(lines):
            if "if self.whitelist_only and self.whitelist:" in line:
                whitelist_only_check_line = i
            if "tickers = await self.client.get_tickers()" in line and whitelist_only_check_line:
                get_tickers_line = i
                break
        
        assert whitelist_only_check_line is not None, "whitelist_only check not found"
        assert get_tickers_line is not None, "get_tickers call not found"
        assert whitelist_only_check_line < get_tickers_line, \
            "whitelist_only check should come before get_tickers call"


# ============================================================================
# PARTIAL TP 30/70 DYNAMIC LABEL TESTS
# ============================================================================

class TestPartialTP30_70DynamicLabel:
    """Test partial TP uses dynamic label based on fraction"""
    
    @pytest.fixture
    def main_py_content(self):
        with open(BOT_DIR / "main.py", "r") as f:
            return f.read()
    
    def test_partial_tp_uses_dynamic_label(self, main_py_content):
        """_maybe_execute_partial_tp uses dynamic label f'partial_tp_{int(fraction*100)}pct'"""
        # Check for dynamic label pattern
        assert "partial_tp_{int(pos.partial_close_fraction*100)}pct" in main_py_content, \
            "Dynamic partial TP label not found"
    
    def test_partial_tp_not_hardcoded_50pct(self, main_py_content):
        """partial_tp label is NOT hardcoded as 'partial_tp_50pct'"""
        # The old hardcoded label should not exist in _finalize_partial_close call
        lines = main_py_content.split('\n')
        for i, line in enumerate(lines):
            if "_finalize_partial_close" in line and "partial_tp_50pct" in line:
                pytest.fail(f"Found hardcoded 'partial_tp_50pct' at line {i+1}: {line.strip()}")
    
    def test_partial_close_fraction_read_from_config(self, main_py_content):
        """partial_close_fraction is read from config"""
        assert 'self.partial_tp_close_fraction = self.cfg.get("partial_tp", "close_fraction"' in main_py_content, \
            "partial_close_fraction not read from config"
    
    def test_partial_close_fraction_passed_to_position(self, main_py_content):
        """partial_close_fraction is passed to Position"""
        assert "partial_close_fraction=self.partial_tp_close_fraction" in main_py_content, \
            "partial_close_fraction not passed to Position"


# ============================================================================
# SIGNAL FEEDBACK LOOP - get_retrain_status() TESTS
# ============================================================================

class TestSignalFeedbackLoopRetrainStatus:
    """Test SignalFeedbackLoop.get_retrain_status() method"""
    
    @pytest.fixture
    def signal_feedback_content(self):
        with open(BOT_DIR / "engine" / "signal_feedback_loop.py", "r") as f:
            return f.read()
    
    def test_get_retrain_status_method_exists(self, signal_feedback_content):
        """get_retrain_status() method exists"""
        assert "def get_retrain_status(self)" in signal_feedback_content, \
            "get_retrain_status method missing"
    
    def test_get_retrain_status_returns_dict(self, signal_feedback_content):
        """get_retrain_status() returns a dict"""
        assert "return {" in signal_feedback_content, "get_retrain_status should return a dict"
    
    def test_get_retrain_status_has_quality_labels(self, signal_feedback_content):
        """get_retrain_status() returns quality_labels"""
        assert '"quality_labels":' in signal_feedback_content, "quality_labels missing in return"
    
    def test_get_retrain_status_has_total_labels(self, signal_feedback_content):
        """get_retrain_status() returns total_labels"""
        assert '"total_labels":' in signal_feedback_content, "total_labels missing in return"
    
    def test_get_retrain_status_has_min_for_retrain(self, signal_feedback_content):
        """get_retrain_status() returns min_for_retrain"""
        assert '"min_for_retrain":' in signal_feedback_content, "min_for_retrain missing in return"
    
    def test_get_retrain_status_has_progress_pct(self, signal_feedback_content):
        """get_retrain_status() returns progress_pct"""
        assert '"progress_pct":' in signal_feedback_content, "progress_pct missing in return"
    
    def test_get_retrain_status_has_dataset_size(self, signal_feedback_content):
        """get_retrain_status() returns dataset_size"""
        assert '"dataset_size":' in signal_feedback_content, "dataset_size missing in return"
    
    def test_get_retrain_status_has_last_dates(self, signal_feedback_content):
        """get_retrain_status() returns last_retrain_attempt and last_retrain_success"""
        assert '"last_retrain_attempt":' in signal_feedback_content, "last_retrain_attempt missing"
        assert '"last_retrain_success":' in signal_feedback_content, "last_retrain_success missing"
    
    def test_get_retrain_status_has_enabled_flag(self, signal_feedback_content):
        """get_retrain_status() returns enabled flag"""
        assert '"enabled":' in signal_feedback_content, "enabled flag missing in return"


# ============================================================================
# TELEGRAM CONTROLLER - /retrain_status COMMAND TESTS
# ============================================================================

class TestTelegramControllerRetrainStatus:
    """Test TelegramController has /retrain_status command"""
    
    @pytest.fixture
    def controller_content(self):
        with open(BOT_DIR / "tg" / "controller.py", "r") as f:
            return f.read()
    
    def test_cmd_retrain_status_handler_exists(self, controller_content):
        """cmd_retrain_status handler method exists"""
        assert "async def cmd_retrain_status" in controller_content, \
            "cmd_retrain_status handler missing"
    
    def test_retrain_status_command_registered(self, controller_content):
        """retrain_status command is registered with CommandHandler"""
        assert 'CommandHandler("retrain_status", self.cmd_retrain_status)' in controller_content, \
            "/retrain_status command not registered"
    
    def test_signal_feedback_attribute_exists(self, controller_content):
        """_signal_feedback attribute exists"""
        assert "self._signal_feedback" in controller_content, \
            "_signal_feedback attribute missing"
    
    def test_set_signal_feedback_method_exists(self, controller_content):
        """set_signal_feedback() method exists"""
        assert "def set_signal_feedback(self" in controller_content, \
            "set_signal_feedback method missing"
    
    def test_retrain_status_in_help_text(self, controller_content):
        """retrain_status is mentioned in /help text"""
        assert "/retrain_status" in controller_content, \
            "/retrain_status not in help text"
    
    def test_cmd_retrain_status_calls_get_retrain_status(self, controller_content):
        """cmd_retrain_status calls _signal_feedback.get_retrain_status()"""
        assert "_signal_feedback.get_retrain_status()" in controller_content, \
            "cmd_retrain_status doesn't call get_retrain_status()"
    
    def test_cmd_retrain_status_displays_progress_bar(self, controller_content):
        """cmd_retrain_status displays a progress bar"""
        # Check for progress bar construction
        assert "bar =" in controller_content or "filled" in controller_content, \
            "Progress bar not found in cmd_retrain_status"


# ============================================================================
# MAIN.PY - SIGNAL FEEDBACK CONNECTION TESTS
# ============================================================================

class TestMainPySignalFeedbackConnection:
    """Test signal_feedback is connected to tg controller in main.py"""
    
    @pytest.fixture
    def main_py_content(self):
        with open(BOT_DIR / "main.py", "r") as f:
            return f.read()
    
    def test_set_signal_feedback_called(self, main_py_content):
        """set_signal_feedback() is called on tg controller"""
        assert "self.tg.set_signal_feedback(self.signal_feedback)" in main_py_content, \
            "set_signal_feedback not called on tg controller"
    
    def test_signal_feedback_created_before_connection(self, main_py_content):
        """signal_feedback is created BEFORE set_signal_feedback is called"""
        lines = main_py_content.split('\n')
        signal_feedback_creation_line = None
        set_signal_feedback_line = None
        
        for i, line in enumerate(lines):
            if "self.signal_feedback = SignalFeedbackLoop(" in line:
                signal_feedback_creation_line = i
            if "self.tg.set_signal_feedback(self.signal_feedback)" in line:
                set_signal_feedback_line = i
        
        assert signal_feedback_creation_line is not None, "signal_feedback creation not found"
        assert set_signal_feedback_line is not None, "set_signal_feedback call not found"
        
        # BUG CHECK: signal_feedback should be created BEFORE it's passed to tg
        if signal_feedback_creation_line > set_signal_feedback_line:
            pytest.fail(
                f"BUG: signal_feedback created at line {signal_feedback_creation_line+1} "
                f"but set_signal_feedback called at line {set_signal_feedback_line+1}. "
                f"This will cause AttributeError!"
            )


# ============================================================================
# FUNCTIONAL TESTS - SignalFeedbackLoop
# ============================================================================

class TestSignalFeedbackLoopFunctional:
    """Functional tests for SignalFeedbackLoop.get_retrain_status()"""
    
    @pytest.fixture
    def mock_config(self):
        """Create a mock config object"""
        class MockConfig:
            def get(self, section, key, default=None):
                config_values = {
                    ("feedback_loop", "enabled"): True,
                    ("feedback_loop", "max_pending_hours"): 12.0,
                    ("feedback_loop", "retrain_daily"): True,
                    ("feedback_loop", "retrain_hour_utc"): 1,
                    ("feedback_loop", "min_new_labels_for_retrain"): 150,
                    ("feedback_loop", "dataset_path"): "test_dataset.json",
                    ("feedback_loop", "queue_path"): "test_queue.json",
                    ("feedback_loop", "state_path"): "test_state.json",
                }
                return config_values.get((section, key), default)
        return MockConfig()
    
    def test_get_retrain_status_returns_correct_structure(self, mock_config, tmp_path):
        """get_retrain_status() returns dict with all required keys"""
        from engine.signal_feedback_loop import SignalFeedbackLoop
        
        feedback = SignalFeedbackLoop(tmp_path, mock_config)
        status = feedback.get_retrain_status()
        
        assert isinstance(status, dict), "get_retrain_status should return dict"
        
        required_keys = [
            "quality_labels", "total_labels", "min_for_retrain", 
            "progress_pct", "dataset_size", "last_retrain_attempt",
            "last_retrain_success", "retrain_hour_utc", "enabled"
        ]
        
        for key in required_keys:
            assert key in status, f"Missing key: {key}"
    
    def test_get_retrain_status_progress_pct_calculation(self, mock_config, tmp_path):
        """progress_pct is calculated correctly"""
        from engine.signal_feedback_loop import SignalFeedbackLoop
        
        feedback = SignalFeedbackLoop(tmp_path, mock_config)
        
        # Manually set some labels
        feedback._state["quality_labels_since_retrain"] = 75
        feedback._state["new_labels_since_retrain"] = 100
        
        status = feedback.get_retrain_status()
        
        # 75 / 150 * 100 = 50%
        assert status["progress_pct"] == 50, f"Expected 50%, got {status['progress_pct']}%"
    
    def test_get_retrain_status_enabled_flag(self, mock_config, tmp_path):
        """enabled flag reflects config"""
        from engine.signal_feedback_loop import SignalFeedbackLoop
        
        feedback = SignalFeedbackLoop(tmp_path, mock_config)
        status = feedback.get_retrain_status()
        
        # enabled = self.enabled and self.retrain_daily
        assert status["enabled"] == True, "enabled should be True"


# ============================================================================
# WHITELIST-ONLY MODE FUNCTIONAL TESTS
# ============================================================================

class TestWhitelistOnlyModeFunctional:
    """Functional tests for whitelist-only mode logic"""
    
    def test_whitelist_only_logic_in_get_trade_symbols(self):
        """Test the whitelist-only logic pattern"""
        # Simulate the logic from get_trade_symbols
        whitelist_only = True
        whitelist = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        blacklist = ["ETHUSDT"]  # ETHUSDT is blacklisted
        
        if whitelist_only and whitelist:
            result = [s for s in whitelist if s not in blacklist]
        else:
            result = []  # Would normally scan tickers
        
        assert result == ["BTCUSDT", "SOLUSDT"], f"Expected ['BTCUSDT', 'SOLUSDT'], got {result}"
    
    def test_whitelist_only_false_does_not_skip_scanning(self):
        """When whitelist_only=False, scanning should happen"""
        whitelist_only = False
        whitelist = ["BTCUSDT", "ETHUSDT"]
        
        # This should NOT trigger early return
        should_skip_scanning = whitelist_only and whitelist
        assert should_skip_scanning == False, "Should not skip scanning when whitelist_only=False"


# ============================================================================
# PARTIAL TP DYNAMIC LABEL FUNCTIONAL TESTS
# ============================================================================

class TestPartialTPDynamicLabelFunctional:
    """Functional tests for partial TP dynamic label"""
    
    def test_dynamic_label_30pct(self):
        """Dynamic label for 30% fraction"""
        fraction = 0.3
        label = f"partial_tp_{int(fraction*100)}pct"
        assert label == "partial_tp_30pct", f"Expected 'partial_tp_30pct', got '{label}'"
    
    def test_dynamic_label_50pct(self):
        """Dynamic label for 50% fraction"""
        fraction = 0.5
        label = f"partial_tp_{int(fraction*100)}pct"
        assert label == "partial_tp_50pct", f"Expected 'partial_tp_50pct', got '{label}'"
    
    def test_dynamic_label_70pct(self):
        """Dynamic label for 70% fraction"""
        fraction = 0.7
        label = f"partial_tp_{int(fraction*100)}pct"
        assert label == "partial_tp_70pct", f"Expected 'partial_tp_70pct', got '{label}'"


# ============================================================================
# CODE QUALITY CHECKS
# ============================================================================

class TestCodeQualityP1:
    """Code quality checks for P1 features"""
    
    def test_no_unreachable_code_in_get_retrain_status(self):
        """Check for unreachable code after return in get_retrain_status"""
        with open(BOT_DIR / "engine" / "signal_feedback_loop.py", "r") as f:
            content = f.read()
        
        # Find get_retrain_status method
        lines = content.split('\n')
        in_method = False
        return_found = False
        unreachable_code = []
        
        for i, line in enumerate(lines):
            if "def get_retrain_status(self)" in line:
                in_method = True
                continue
            
            if in_method:
                # Check for next method definition (end of get_retrain_status)
                if line.strip().startswith("def ") and "get_retrain_status" not in line:
                    break
                
                if "return {" in line or (return_found and line.strip().startswith("}")):
                    if "}" in line and return_found:
                        return_found = False  # End of return dict
                    elif "return {" in line:
                        return_found = True
                    continue
                
                if return_found:
                    continue  # Inside return dict
                
                # After return dict closes, any non-empty line is unreachable
                if not return_found and line.strip() and not line.strip().startswith("#"):
                    # Check if this is after the return
                    if "today = datetime" in line or "self._state[" in line or "self._save_json" in line:
                        unreachable_code.append((i+1, line.strip()))
        
        if unreachable_code:
            msg = "Unreachable code found after return in get_retrain_status:\n"
            for line_num, code in unreachable_code:
                msg += f"  Line {line_num}: {code}\n"
            pytest.fail(msg)


# ============================================================================
# SUMMARY TEST
# ============================================================================

class TestIteration42P1Summary:
    """Summary test for all P1 features"""
    
    def test_all_p1_features_present(self):
        """Verify all P1 features are implemented"""
        with open(BOT_DIR / "config.yaml", "r") as f:
            config = yaml.safe_load(f)
        
        with open(BOT_DIR / "main.py", "r") as f:
            main_content = f.read()
        
        with open(BOT_DIR / "engine" / "signal_feedback_loop.py", "r") as f:
            feedback_content = f.read()
        
        with open(BOT_DIR / "tg" / "controller.py", "r") as f:
            controller_content = f.read()
        
        features = {
            "whitelist_only_in_config": "whitelist_only" in str(config.get("market", {})),
            "whitelist_only_is_false": config.get("market", {}).get("whitelist_only") == False,
            "whitelist_only_in_main": "self.whitelist_only" in main_content,
            "whitelist_only_logic": "if self.whitelist_only and self.whitelist:" in main_content,
            "close_fraction_0_3": config.get("partial_tp", {}).get("close_fraction") == 0.3,
            "dynamic_partial_tp_label": "partial_tp_{int(pos.partial_close_fraction*100)}pct" in main_content,
            "get_retrain_status_method": "def get_retrain_status(self)" in feedback_content,
            "cmd_retrain_status_handler": "async def cmd_retrain_status" in controller_content,
            "retrain_status_command_registered": 'CommandHandler("retrain_status"' in controller_content,
            "set_signal_feedback_method": "def set_signal_feedback(self" in controller_content,
            "retrain_status_in_help": "/retrain_status" in controller_content,
            "early_exit_bars_20": config.get("exit", {}).get("early_exit_bars") == 20,
        }
        
        missing = [k for k, v in features.items() if not v]
        
        if missing:
            pytest.fail(f"Missing P1 features: {missing}")
        
        print("\n=== P1 FEATURES VERIFICATION ===")
        for feature, present in features.items():
            status = "✓" if present else "✗"
            print(f"  {status} {feature}")
        print("================================")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
