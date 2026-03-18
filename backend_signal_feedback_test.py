#!/usr/bin/env python3
"""
Backend Testing for Signal Feedback Loop Final Verification

Tests per review request:
1. Signal-only feedback loop in /app/bot/engine/signal_feedback_loop.py (register, SL/TP/timeout labeling, append training_data)
2. Integration points in /app/bot/main.py (_process_signal_feedback_loop invocation, register_signal in signal-only path, daily retrain call)
3. /app/bot/analysis/ai_analyzer.py cache TTL = 600
4. /app/bot/engine/entry_engine.py invalid SL guards (invalid_sl_long/invalid_sl_short)
5. /app/bot/config.yaml feedback_loop section + config sync for leverage/max_positions
"""
import sys
import os
import time
import json
import tempfile
import asyncio
from pathlib import Path
import subprocess
import logging
import yaml
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

# Add bot directory to path
bot_dir = Path(__file__).parent / "bot"
sys.path.insert(0, str(bot_dir))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [FEEDBACK_TEST] %(message)s")
logger = logging.getLogger("SIGNAL_FEEDBACK_TEST")

class SignalFeedbackLoopTester:
    def __init__(self):
        self.bot_dir = Path(__file__).parent / "bot"
        self.results = {"tests_passed": 0, "tests_failed": 0, "failures": []}

    def log_result(self, test_name, passed, details=""):
        if passed:
            self.results["tests_passed"] += 1
            logger.info(f"✅ {test_name}: PASSED")
        else:
            self.results["tests_failed"] += 1
            self.results["failures"].append(f"{test_name}: {details}")
            logger.error(f"❌ {test_name}: FAILED - {details}")

    def test_signal_feedback_loop_implementation(self):
        """Test 1: Verify signal_feedback_loop.py implementation"""
        logger.info("Testing signal feedback loop implementation...")
        
        try:
            sfl_file = self.bot_dir / "engine" / "signal_feedback_loop.py"
            if not sfl_file.exists():
                self.log_result("Signal feedback loop - file exists", False, "signal_feedback_loop.py not found")
                return
                
            content = sfl_file.read_text()
            
            # Test register_signal method exists
            if "def register_signal(" not in content:
                self.log_result("Signal feedback loop - register_signal method", False, "register_signal method not found")
                return
                
            # Test SL/TP/timeout labeling logic in _resolve_outcome_reason
            if "_resolve_outcome_reason" not in content:
                self.log_result("Signal feedback loop - outcome resolution", False, "_resolve_outcome_reason method not found")
                return
                
            # Test training data append in _append_training_record
            if "_append_training_record" not in content:
                self.log_result("Signal feedback loop - training data append", False, "_append_training_record method not found")
                return
                
            # Test outcome reasons: stop_loss, take_profit, timeout
            required_outcomes = ["stop_loss", "take_profit", "timeout"]
            for outcome in required_outcomes:
                if f'"{outcome}"' not in content:
                    self.log_result(f"Signal feedback loop - {outcome} outcome", False, f"{outcome} outcome not implemented")
                    return
                    
            # Test dataset path configuration
            if "dataset_path" not in content:
                self.log_result("Signal feedback loop - dataset path", False, "dataset_path configuration not found")
                return
                
            # Test queue processing with process_pending
            if "def process_pending(" not in content:
                self.log_result("Signal feedback loop - process pending", False, "process_pending method not found")
                return
                
            self.log_result("Signal feedback loop implementation", True)
            
        except Exception as e:
            self.log_result("Signal feedback loop implementation", False, str(e))

    def test_main_py_integration_points(self):
        """Test 2: Verify main.py integration points"""
        logger.info("Testing main.py integration points...")
        
        try:
            main_py = self.bot_dir / "main.py"
            if not main_py.exists():
                self.log_result("Main.py integration - file exists", False, "main.py not found")
                return
                
            content = main_py.read_text()
            
            # Test _process_signal_feedback_loop method exists
            if "def _process_signal_feedback_loop(" not in content:
                self.log_result("Main.py integration - _process_signal_feedback_loop method", False, "_process_signal_feedback_loop method not found")
                return
                
            # Test _process_signal_feedback_loop invocation in signal_only mode
            if "await self._process_signal_feedback_loop()" not in content:
                self.log_result("Main.py integration - feedback loop invocation", False, "Feedback loop not called in run cycle")
                return
                
            # Test register_signal call in signal-only path
            if "self.signal_feedback.register_signal" not in content:
                self.log_result("Main.py integration - register_signal call", False, "register_signal not called in signal-only path")
                return
                
            # Test daily retrain call
            if "should_run_daily_retrain" not in content:
                self.log_result("Main.py integration - daily retrain check", False, "Daily retrain check not implemented")
                return
                
            # Test _run_feedback_daily_retrain method
            if "def _run_feedback_daily_retrain(" not in content:
                self.log_result("Main.py integration - daily retrain method", False, "_run_feedback_daily_retrain method not found")
                return
                
            # Test SignalFeedbackLoop initialization
            if "SignalFeedbackLoop" not in content:
                self.log_result("Main.py integration - SignalFeedbackLoop import", False, "SignalFeedbackLoop not imported")
                return
                
            if "self.signal_feedback = SignalFeedbackLoop" not in content:
                self.log_result("Main.py integration - SignalFeedbackLoop initialization", False, "SignalFeedbackLoop not initialized")
                return
                
            self.log_result("Main.py integration points", True)
            
        except Exception as e:
            self.log_result("Main.py integration points", False, str(e))

    def test_ai_analyzer_cache_ttl(self):
        """Test 3: Verify ai_analyzer.py cache TTL = 600"""
        logger.info("Testing AI analyzer cache TTL...")
        
        try:
            ai_analyzer_file = self.bot_dir / "analysis" / "ai_analyzer.py"
            if not ai_analyzer_file.exists():
                self.log_result("AI analyzer - file exists", False, "ai_analyzer.py not found")
                return
                
            content = ai_analyzer_file.read_text()
            
            # Test cache TTL = 600
            if "_cache_ttl = 600" not in content:
                self.log_result("AI analyzer - cache TTL", False, "Cache TTL not set to 600 seconds")
                return
                
            # Test cache implementation
            if "_cache" not in content:
                self.log_result("AI analyzer - cache implementation", False, "Cache not implemented")
                return
                
            # Test cache usage in analyze method
            if "self._cache_ttl" not in content:
                self.log_result("AI analyzer - cache TTL usage", False, "Cache TTL not used in implementation")
                return
                
            self.log_result("AI analyzer cache TTL verification", True)
            
        except Exception as e:
            self.log_result("AI analyzer cache TTL verification", False, str(e))

    def test_entry_engine_invalid_sl_guards(self):
        """Test 4: Verify entry_engine.py invalid SL guards"""
        logger.info("Testing entry engine invalid SL guards...")
        
        try:
            entry_engine_file = self.bot_dir / "engine" / "entry_engine.py"
            if not entry_engine_file.exists():
                self.log_result("Entry engine - file exists", False, "entry_engine.py not found")
                return
                
            content = entry_engine_file.read_text()
            
            # Test invalid_sl_long guard
            if '"invalid_sl_long"' not in content:
                self.log_result("Entry engine - invalid_sl_long guard", False, "invalid_sl_long guard not found")
                return
                
            # Test invalid_sl_short guard
            if '"invalid_sl_short"' not in content:
                self.log_result("Entry engine - invalid_sl_short guard", False, "invalid_sl_short guard not found")
                return
                
            # Test SL validation logic for long positions
            if "sl >= current_price" not in content:
                self.log_result("Entry engine - long SL validation", False, "Long position SL validation not found")
                return
                
            # Test SL validation logic for short positions  
            if "sl <= current_price" not in content:
                self.log_result("Entry engine - short SL validation", False, "Short position SL validation not found")
                return
                
            self.log_result("Entry engine invalid SL guards", True)
            
        except Exception as e:
            self.log_result("Entry engine invalid SL guards", False, str(e))

    def test_config_feedback_loop_section(self):
        """Test 5: Verify config.yaml feedback_loop section and config sync"""
        logger.info("Testing config.yaml feedback_loop section...")
        
        try:
            config_path = self.bot_dir / "config.yaml"
            if not config_path.exists():
                self.log_result("Config - file exists", False, "config.yaml not found")
                return
                
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            
            # Test feedback_loop section exists
            if "feedback_loop" not in cfg:
                self.log_result("Config - feedback_loop section", False, "feedback_loop section not found")
                return
                
            feedback_cfg = cfg["feedback_loop"]
            
            # Test required feedback_loop configuration keys
            required_keys = [
                "enabled", "notify_labeling", "max_pending_hours", 
                "dataset_path", "queue_path", "state_path",
                "retrain_daily", "retrain_hour_utc", "min_new_labels_for_retrain",
                "train_epochs", "train_lr", "train_batch_size", "train_val_ratio",
                "train_decision_threshold", "train_seed", "augment_wins_factor", "augment_noise_std"
            ]
            
            for key in required_keys:
                if key not in feedback_cfg:
                    self.log_result(f"Config - feedback_loop.{key}", False, f"Key {key} not found in feedback_loop")
                    return
                    
            # Test leverage sync (trading section)
            if "trading" not in cfg or "leverage" not in cfg["trading"]:
                self.log_result("Config - leverage sync", False, "trading.leverage not found")
                return
                
            # Test max_positions sync (trading section)
            if "max_positions" not in cfg["trading"]:
                self.log_result("Config - max_positions sync", False, "trading.max_positions not found")
                return
                
            self.log_result("Config feedback_loop section verification", True)
            
        except Exception as e:
            self.log_result("Config feedback_loop section verification", False, str(e))

    async def test_signal_feedback_functional(self):
        """Test 6: Functional test of signal feedback loop with mock data"""
        logger.info("Testing signal feedback loop functionality...")
        
        try:
            # Import the SignalFeedbackLoop class
            from engine.signal_feedback_loop import SignalFeedbackLoop
            
            # Create temporary directory for test
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                
                # Mock config
                mock_cfg = MagicMock()
                mock_cfg.get.side_effect = lambda section, key, default=None: {
                    ("feedback_loop", "enabled"): True,
                    ("feedback_loop", "max_pending_hours"): 12.0,
                    ("feedback_loop", "retrain_daily"): True,
                    ("feedback_loop", "retrain_hour_utc"): 0,
                    ("feedback_loop", "min_new_labels_for_retrain"): 8,
                    ("feedback_loop", "dataset_path"): str(temp_path / "training_data.json"),
                    ("feedback_loop", "queue_path"): str(temp_path / "queue.json"),
                    ("feedback_loop", "state_path"): str(temp_path / "state.json")
                }.get((section, key), default)
                
                # Initialize SignalFeedbackLoop
                sfl = SignalFeedbackLoop(temp_path, mock_cfg)
                
                # Test register_signal
                mock_signal = MagicMock()
                mock_signal.side = "BUY"
                mock_signal.entry_price = 50000.0
                mock_signal.stop_loss = 49000.0
                mock_signal.take_profit = 52000.0
                mock_signal.rr_ratio = 2.0
                mock_signal.confidence = 0.75
                mock_signal.metadata = {
                    "composite_score": 0.8,
                    "trend_score": 0.7,
                    "orderflow_score": 0.9,
                    "ai_score": 0.8,
                    "normalized_imbalance": 0.5,
                    "htf_4h_trend": 1,
                    "trained_model_prob": 0.65,
                    "entry_zone": "fvg_bull"
                }
                
                sfl.register_signal("BTCUSDT", mock_signal)
                
                # Verify signal was registered
                if len(sfl._queue) != 1:
                    self.log_result("Signal feedback - register signal", False, "Signal not registered in queue")
                    return
                    
                # Test process_pending with mock price function
                async def mock_get_price(symbol):
                    if symbol == "BTCUSDT":
                        return 52500.0  # Above take profit
                    return 0.0
                    
                outcomes = await sfl.process_pending(mock_get_price)
                
                # Verify outcome processed
                if len(outcomes) != 1:
                    self.log_result("Signal feedback - process pending", False, f"Expected 1 outcome, got {len(outcomes)}")
                    return
                    
                outcome = outcomes[0]
                if outcome.reason != "take_profit":
                    self.log_result("Signal feedback - outcome reason", False, f"Expected take_profit, got {outcome.reason}")
                    return
                    
                # Verify training record created
                dataset_file = temp_path / "training_data.json"
                if not dataset_file.exists():
                    self.log_result("Signal feedback - training data file", False, "Training data file not created")
                    return
                    
                with open(dataset_file) as f:
                    dataset = json.load(f)
                    
                if len(dataset) != 1:
                    self.log_result("Signal feedback - training record", False, f"Expected 1 record, got {len(dataset)}")
                    return
                    
                record = dataset[0]
                if record["result"] != "win":
                    self.log_result("Signal feedback - win result", False, f"Expected win, got {record['result']}")
                    return
                    
                if record["exit_reason"] != "take_profit":
                    self.log_result("Signal feedback - exit reason", False, f"Expected take_profit, got {record['exit_reason']}")
                    return
                    
                self.log_result("Signal feedback functional test", True)
                
        except Exception as e:
            self.log_result("Signal feedback functional test", False, str(e))

    def run_all_tests(self):
        """Run all signal feedback loop verification tests"""
        logger.info("Starting Signal Feedback Loop Final Verification...")
        logger.info("=" * 70)
        
        # Test 1: Signal feedback loop implementation
        self.test_signal_feedback_loop_implementation()
        
        # Test 2: Main.py integration points
        self.test_main_py_integration_points()
        
        # Test 3: AI analyzer cache TTL
        self.test_ai_analyzer_cache_ttl()
        
        # Test 4: Entry engine invalid SL guards
        self.test_entry_engine_invalid_sl_guards()
        
        # Test 5: Config feedback_loop section
        self.test_config_feedback_loop_section()
        
        # Test 6: Functional test
        asyncio.run(self.test_signal_feedback_functional())
        
        # Print summary
        logger.info("=" * 70)
        logger.info("SIGNAL FEEDBACK LOOP VERIFICATION SUMMARY")
        logger.info("=" * 70)
        logger.info(f"✅ Tests Passed: {self.results['tests_passed']}")
        logger.info(f"❌ Tests Failed: {self.results['tests_failed']}")
        
        if self.results['failures']:
            logger.info("\nFAILURE DETAILS:")
            for failure in self.results['failures']:
                logger.info(f"  - {failure}")
        
        total = self.results['tests_passed'] + self.results['tests_failed']
        if total > 0:
            success_rate = (self.results['tests_passed'] / total) * 100
            logger.info(f"\nSuccess Rate: {success_rate:.1f}%")
        
        return self.results['tests_failed'] == 0

if __name__ == "__main__":
    tester = SignalFeedbackLoopTester()
    success = tester.run_all_tests()
    
    if success:
        logger.info("🎉 All signal feedback loop verification tests PASSED!")
        exit(0)
    else:
        logger.error("💥 Some signal feedback loop verification tests FAILED!")
        exit(1)