#!/usr/bin/env python3
"""
Backend Testing for Crypto Bot Updates Verification

Tests:
1. Same-side cooldown logic in main.py (3600s cooldown for repeated symbol+side signals)
2. Config.yaml validation (entry_threshold=0.85, signal_cooldown_sec=3600)
3. Train transformer training flow (BCEWithLogitsLoss+pos_weight, weighted sampler, win augmentation, precision-first checkpointing)
4. Entry engine trained model integration (checkpoint loading, trained prob gate, confidence blending)
5. Execute test files: test_trained_model_integration.py and test_entry_engine_v6.py
"""
import sys
import os
import time
import json
import tempfile
from pathlib import Path
import subprocess
import logging
import yaml

# Add bot directory to path
bot_dir = Path(__file__).parent / "bot"
sys.path.insert(0, str(bot_dir))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TEST] %(message)s")
logger = logging.getLogger("BACKEND_TEST")

class CryptoBotTester:
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

    def test_same_side_cooldown_logic(self):
        """Test 1: Validate same-side cooldown logic in main.py"""
        logger.info("Testing same-side cooldown logic in main.py...")
        
        try:
            main_py = self.bot_dir / "main.py"
            if not main_py.exists():
                self.log_result("Same-side cooldown - file exists", False, "main.py not found")
                return
            
            content = main_py.read_text()
            
            # Check for required methods
            required_methods = [
                "_same_side_cooldown_remaining",
                "_register_signal_timestamp"
            ]
            
            for method in required_methods:
                if method not in content:
                    self.log_result(f"Same-side cooldown - {method} method", False, f"Method {method} not found")
                    return
            
            # Check for cooldown logic in _scan_entries
            if "_same_side_cooldown_remaining" not in content:
                self.log_result("Same-side cooldown - scan_entries integration", False, "Cooldown check not in _scan_entries")
                return
                
            # Check for 3600s cooldown usage
            if "3600" not in content:
                self.log_result("Same-side cooldown - 3600s configured", False, "3600 second cooldown not found")
                return
                
            # Check for _last_signal_ts dictionary
            if "_last_signal_ts" not in content:
                self.log_result("Same-side cooldown - signal tracking dict", False, "_last_signal_ts dict not found")
                return
                
            # Check cooldown remaining logic returns int
            if "remaining = int(" not in content or "return remaining" not in content:
                self.log_result("Same-side cooldown - remaining seconds logic", False, "Cooldown remaining logic not properly implemented")
                return
                
            self.log_result("Same-side cooldown logic validation", True)
            
        except Exception as e:
            self.log_result("Same-side cooldown logic validation", False, str(e))

    def test_config_validation(self):
        """Test 2: Validate config.yaml has correct values"""
        logger.info("Testing config.yaml validation...")
        
        try:
            config_path = self.bot_dir / "config.yaml"
            if not config_path.exists():
                self.log_result("Config validation - file exists", False, "config.yaml not found")
                return
                
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            
            # Test entry_threshold = 0.85
            entry_threshold = cfg.get("entry", {}).get("entry_threshold")
            if entry_threshold != 0.85:
                self.log_result("Config validation - entry_threshold", False, f"Expected 0.85, got {entry_threshold}")
                return
                
            # Test signal_cooldown_sec = 3600
            signal_cooldown = cfg.get("bot", {}).get("signal_cooldown_sec")
            if signal_cooldown != 3600:
                self.log_result("Config validation - signal_cooldown_sec", False, f"Expected 3600, got {signal_cooldown}")
                return
                
            self.log_result("Config validation (entry_threshold=0.85, signal_cooldown_sec=3600)", True)
            
        except Exception as e:
            self.log_result("Config validation", False, str(e))

    def test_train_transformer_flow(self):
        """Test 3: Validate training flow in train_transformer.py"""
        logger.info("Testing training flow in train_transformer.py...")
        
        try:
            train_file = self.bot_dir / "train_transformer.py"
            if not train_file.exists():
                self.log_result("Training flow - file exists", False, "train_transformer.py not found")
                return
                
            content = train_file.read_text()
            
            # Check for BCEWithLogitsLoss with pos_weight
            if "BCEWithLogitsLoss" not in content:
                self.log_result("Training flow - BCEWithLogitsLoss", False, "BCEWithLogitsLoss not found")
                return
                
            if "pos_weight" not in content:
                self.log_result("Training flow - pos_weight", False, "pos_weight parameter not found")
                return
                
            # Check for weighted sampler
            if "WeightedRandomSampler" not in content:
                self.log_result("Training flow - WeightedRandomSampler", False, "WeightedRandomSampler not found")
                return
                
            # Check for win augmentation
            if "augment_wins" not in content:
                self.log_result("Training flow - win augmentation", False, "Win augmentation not found")
                return
                
            # Check for precision-first checkpointing
            if "precision" not in content.lower() or "best_precision" not in content:
                self.log_result("Training flow - precision checkpointing", False, "Precision-first checkpointing not found")
                return
                
            # Check for stratified split
            if "stratified_split" not in content:
                self.log_result("Training flow - stratified split", False, "Stratified split not found")
                return
                
            self.log_result("Training transformer flow validation", True)
            
        except Exception as e:
            self.log_result("Training transformer flow validation", False, str(e))

    def test_entry_engine_model_integration(self):
        """Test 4: Validate entry engine trained model integration"""
        logger.info("Testing entry engine trained model integration...")
        
        try:
            entry_engine_file = self.bot_dir / "engine" / "entry_engine.py"
            if not entry_engine_file.exists():
                self.log_result("Entry engine - file exists", False, "entry_engine.py not found")
                return
                
            content = entry_engine_file.read_text()
            
            # Check for checkpoint loading behavior
            if "_load_trained_model" not in content:
                self.log_result("Entry engine - checkpoint loading", False, "_load_trained_model method not found")
                return
                
            # Check for trained probability gate
            if "trained_model_min_prob" not in content:
                self.log_result("Entry engine - trained prob gate", False, "trained_model_min_prob not found")
                return
                
            # Check for confidence blending
            if "trained_model_blend" not in content:
                self.log_result("Entry engine - confidence blending", False, "trained_model_blend not found")
                return
                
            # Check for _predict_trained_win_prob method
            if "_predict_trained_win_prob" not in content:
                self.log_result("Entry engine - win probability prediction", False, "_predict_trained_win_prob method not found")
                return
                
            # Check for blended confidence calculation
            if "blended_confidence" not in content:
                self.log_result("Entry engine - blended confidence", False, "blended_confidence calculation not found")
                return
                
            # Check torch/nn integration
            if "torch" not in content or "nn.Module" not in content:
                self.log_result("Entry engine - torch integration", False, "PyTorch integration not found")
                return
                
            self.log_result("Entry engine trained model integration", True)
            
        except Exception as e:
            self.log_result("Entry engine trained model integration", False, str(e))

    def run_test_file(self, test_file_path, test_name):
        """Run a specific test file and capture results"""
        logger.info(f"Running {test_name}...")
        
        try:
            if not Path(test_file_path).exists():
                self.log_result(f"{test_name} - file exists", False, f"Test file {test_file_path} not found")
                return
                
            # Run pytest on the test file
            result = subprocess.run([
                sys.executable, "-m", "pytest", 
                str(test_file_path), 
                "-v", "--tb=short", "--no-header"
            ], capture_output=True, text=True, cwd=str(Path(__file__).parent))
            
            # Parse results
            if result.returncode == 0:
                # Count passed tests
                passed_count = result.stdout.count(" PASSED")
                self.log_result(f"{test_name} ({passed_count} tests)", True, f"All tests passed")
            else:
                # Extract failure information
                failed_count = result.stdout.count(" FAILED")
                passed_count = result.stdout.count(" PASSED")
                skipped_count = result.stdout.count(" SKIPPED")
                
                failure_details = f"{passed_count} passed, {failed_count} failed, {skipped_count} skipped"
                if result.stderr:
                    failure_details += f" | Error: {result.stderr[:200]}"
                    
                self.log_result(f"{test_name}", False, failure_details)
            
        except Exception as e:
            self.log_result(f"{test_name}", False, f"Exception running test: {str(e)}")

    def run_all_tests(self):
        """Run all validation tests"""
        logger.info("Starting Crypto Bot Updates Verification...")
        logger.info("=" * 60)
        
        # Test 1: Same-side cooldown logic
        self.test_same_side_cooldown_logic()
        
        # Test 2: Config validation
        self.test_config_validation()
        
        # Test 3: Training flow validation
        self.test_train_transformer_flow()
        
        # Test 4: Entry engine model integration
        self.test_entry_engine_model_integration()
        
        # Test 5: Run test_trained_model_integration.py
        test_file_1 = Path(__file__).parent / "backend" / "tests" / "test_trained_model_integration.py"
        self.run_test_file(test_file_1, "Trained model integration tests")
        
        # Test 6: Run test_entry_engine_v6.py
        test_file_2 = Path(__file__).parent / "backend" / "tests" / "test_entry_engine_v6.py"
        self.run_test_file(test_file_2, "Entry engine v6 tests")
        
        # Print summary
        logger.info("=" * 60)
        logger.info("CRYPTO BOT VERIFICATION SUMMARY")
        logger.info("=" * 60)
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
    tester = CryptoBotTester()
    success = tester.run_all_tests()
    
    if success:
        logger.info("🎉 All crypto bot verification tests PASSED!")
        exit(0)
    else:
        logger.error("💥 Some crypto bot verification tests FAILED!")
        exit(1)