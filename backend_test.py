#!/usr/bin/env python3
"""
Backend test for Quality-Gate feature validation as per review request.

Tests the following requirements:
1. Validate /app/bot/main.py quality-gate logic before Telegram send in signal-only mode
2. Validate expected edge formula and anti-flat checks
3. Validate /app/bot/config.yaml quality_gate section is used
4. Validate no regressions in feedback loop + SL validation + trained model paths
5. Run related tests and report pass/fail and defects
"""
import os
import sys
import subprocess
from pathlib import Path

# Add bot directory to path
BOT_DIR = Path(__file__).parent / "bot"
sys.path.insert(0, str(BOT_DIR))

def test_quality_gate_config_section():
    """Test 1: Validate config.yaml has quality_gate section."""
    print("=== TEST 1: Quality Gate Config Section ===")
    config_path = BOT_DIR / "config.yaml"
    if not config_path.exists():
        print("❌ FAIL: config.yaml not found")
        return False
    
    content = config_path.read_text()
    required_keys = [
        "quality_gate:",
        "enabled:",
        "min_confidence:",
        "min_expected_edge:",
        "anti_flat_min_adx:",
        "anti_flat_min_atr_pct:",
        "anti_flat_min_abs_imbalance:",
        "anti_flat_allow_chop:",
        "anti_flat_require_htf_trend:"
    ]
    
    missing_keys = []
    for key in required_keys:
        if key not in content:
            missing_keys.append(key)
    
    if missing_keys:
        print(f"❌ FAIL: Missing config keys: {missing_keys}")
        return False
    
    print("✅ PASS: All quality_gate config keys present")
    return True

def test_quality_gate_main_integration():
    """Test 2: Validate main.py has quality gate integration."""
    print("\n=== TEST 2: Main.py Quality Gate Integration ===")
    main_path = BOT_DIR / "main.py"
    if not main_path.exists():
        print("❌ FAIL: main.py not found")
        return False
    
    content = main_path.read_text()
    required_patterns = [
        # Config loading
        'self.quality_gate_enabled = self.cfg.get("quality_gate", "enabled"',
        'self.quality_gate_min_confidence = float(self.cfg.get("quality_gate", "min_confidence"',
        'self.quality_gate_min_expected_edge = float(',
        'self.quality_gate_min_adx = float(self.cfg.get("quality_gate", "anti_flat_min_adx"',
        'self.quality_gate_min_atr_pct = float(self.cfg.get("quality_gate", "anti_flat_min_atr_pct"',
        'self.quality_gate_min_abs_imbalance = float(',
        'self.quality_gate_allow_chop = self.cfg.get("quality_gate", "anti_flat_allow_chop"',
        'self.quality_gate_require_htf_trend = self.cfg.get("quality_gate", "anti_flat_require_htf_trend"',
        
        # Signal-only mode integration
        'if self.signal_only and self.quality_gate_enabled:',
        'gate_ok, gate_reason, gate_meta = self._passes_signal_quality_gate(symbol, signal)',
        'QUALITY GATE REJECT',
        'signal.metadata.update(gate_meta)',
        
        # Quality gate method
        'def _passes_signal_quality_gate(self, symbol: str, signal: EntrySignal)',
        
        # Expected edge formula
        'expected_edge = base_prob * (rr_ratio + 1.0) - 1.0',
        
        # Anti-flat checks
        'if confidence < self.quality_gate_min_confidence:',
        'if expected_edge < self.quality_gate_min_expected_edge:',
        'if adx < self.quality_gate_min_adx:',
        'if atr_pct < self.quality_gate_min_atr_pct:',
        'if abs_imbalance < self.quality_gate_min_abs_imbalance:',
        'if not self.quality_gate_allow_chop and regime == "chop":',
        'if self.quality_gate_require_htf_trend and htf_trend in {"neutral", "flat", "range", "sideways"}:',
        
        # Telegram message with expected edge
        'expected_edge = float(signal.metadata.get("quality_expected_edge"',
        'Expected Edge:',
        
        # Quality gate startup logging  
        'Signal quality gate:',
        'min_conf={self.quality_gate_min_confidence',
        'min_edge={self.quality_gate_min_expected_edge'
    ]
    
    missing_patterns = []
    for pattern in required_patterns:
        if pattern not in content:
            missing_patterns.append(pattern)
    
    if missing_patterns:
        print(f"❌ FAIL: Missing implementation patterns:")
        for pattern in missing_patterns[:10]:  # Show first 10
            print(f"  - {pattern}")
        if len(missing_patterns) > 10:
            print(f"  ... and {len(missing_patterns) - 10} more")
        return False
    
    print("✅ PASS: Quality gate integration patterns found in main.py")
    return True

def test_no_regressions():
    """Test 3: Check for no regressions in related features."""
    print("\n=== TEST 3: No Regressions Check ===")
    main_path = BOT_DIR / "main.py"
    entry_engine_path = BOT_DIR / "engine" / "entry_engine.py"
    
    if not main_path.exists() or not entry_engine_path.exists():
        print("❌ FAIL: Required files not found")
        return False
    
    main_content = main_path.read_text()
    entry_content = entry_engine_path.read_text()
    
    # Check feedback loop integration
    feedback_patterns = [
        'self.signal_feedback.register_signal(symbol, signal)',
        'self.signal_feedback.enabled',
        'await self._process_signal_feedback_loop()',
        'SignalFeedbackLoop'
    ]
    
    # Check SL validation
    sl_patterns = [
        'invalid_sl_long',
        'invalid_sl_short'
    ]
    
    # Check trained model integration
    model_patterns = [
        'trained_model',
        '_load_trained_model',
        'trained_model_prob'
    ]
    
    missing_feedback = [p for p in feedback_patterns if p not in main_content]
    missing_sl = [p for p in sl_patterns if p not in entry_content]
    missing_model = [p for p in model_patterns if p.lower() not in (main_content.lower() + entry_content.lower())]
    
    if missing_feedback:
        print(f"❌ FAIL: Missing feedback loop patterns: {missing_feedback}")
        return False
    
    if missing_sl:
        print(f"❌ FAIL: Missing SL validation patterns: {missing_sl}")
        return False
        
    if missing_model:
        print(f"❌ FAIL: Missing trained model patterns: {missing_model}")
        return False
    
    print("✅ PASS: No regressions detected in feedback loop, SL validation, trained model")
    return True

def test_run_existing_quality_gate_tests():
    """Test 4: Run existing comprehensive quality gate test suite."""
    print("\n=== TEST 4: Run Quality Gate Test Suite ===")
    test_file = Path(__file__).parent / "backend" / "tests" / "test_quality_gate_iteration17.py"
    
    if not test_file.exists():
        print("❌ FAIL: Quality gate test file not found")
        return False
    
    try:
        # Run the test file
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent
        )
        
        print("PYTEST OUTPUT:")
        print(result.stdout)
        if result.stderr:
            print("STDERR:")
            print(result.stderr)
        
        if result.returncode == 0:
            print("✅ PASS: Quality gate test suite passed")
            return True
        else:
            print(f"❌ FAIL: Quality gate test suite failed with return code {result.returncode}")
            return False
    except Exception as e:
        print(f"❌ FAIL: Error running quality gate tests: {e}")
        return False

def test_expected_edge_formula():
    """Test 5: Validate expected edge formula manually."""
    print("\n=== TEST 5: Expected Edge Formula Validation ===")
    try:
        # Import required modules
        from engine.entry_engine import EntrySignal
        from main import TradingBot
        
        # Create a bot instance (minimal setup)
        bot = TradingBot.__new__(TradingBot)
        bot.quality_gate_enabled = True
        bot.quality_gate_min_confidence = 0.68
        bot.quality_gate_min_expected_edge = 0.75
        bot.quality_gate_min_adx = 16.0
        bot.quality_gate_min_atr_pct = 0.20
        bot.quality_gate_min_abs_imbalance = 0.08
        bot.quality_gate_allow_chop = False
        bot.quality_gate_require_htf_trend = False
        
        # Test cases for expected edge formula
        test_cases = [
            # (confidence, rr_ratio, trained_model_prob, expected_result)
            (0.90, 3.0, None, 2.6),  # 0.90 * 4.0 - 1.0 = 2.6
            (0.75, 2.5, None, 1.625),  # 0.75 * 3.5 - 1.0 = 1.625
            (0.80, 1.5, None, 1.0),  # 0.80 * 2.5 - 1.0 = 1.0
            (0.90, 3.0, 0.75, 2.0),  # Uses trained model: 0.75 * 4.0 - 1.0 = 2.0
        ]
        
        all_passed = True
        for confidence, rr_ratio, trained_prob, expected in test_cases:
            metadata = {
                "regime": "trend",
                "adx": 30.0,
                "atr_pct": 0.5,
                "htf_trend": "up",
                "normalized_imbalance": 0.3,
            }
            if trained_prob is not None:
                metadata["trained_model_prob"] = trained_prob
            
            signal = EntrySignal(
                should_enter=True,
                side="BUY",
                confidence=confidence,
                rr_ratio=rr_ratio,
                metadata=metadata,
            )
            
            ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", signal)
            actual_edge = meta.get("quality_expected_edge", 0.0)
            
            if abs(actual_edge - expected) > 0.001:
                print(f"❌ FAIL: Expected edge mismatch. Input: conf={confidence}, rr={rr_ratio}, trained={trained_prob}")
                print(f"  Expected: {expected}, Got: {actual_edge}")
                all_passed = False
            else:
                print(f"✅ Edge formula correct: conf={confidence}, rr={rr_ratio} -> edge={actual_edge:.3f}")
        
        if all_passed:
            print("✅ PASS: Expected edge formula working correctly")
            return True
        else:
            print("❌ FAIL: Expected edge formula has issues")
            return False
            
    except Exception as e:
        print(f"❌ FAIL: Error testing expected edge formula: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all quality gate backend validation tests."""
    print("🚀 QUALITY GATE FEATURE BACKEND VALIDATION")
    print("=" * 60)
    
    tests = [
        test_quality_gate_config_section,
        test_quality_gate_main_integration,
        test_no_regressions,
        test_expected_edge_formula,
        test_run_existing_quality_gate_tests,
    ]
    
    results = []
    for test_func in tests:
        try:
            result = test_func()
            results.append(result)
        except Exception as e:
            print(f"❌ EXCEPTION in {test_func.__name__}: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)
    
    print("\n" + "=" * 60)
    print("📊 SUMMARY")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Tests passed: {passed}/{total}")
    for i, (test_func, result) in enumerate(zip(tests, results)):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{i+1}. {test_func.__name__}: {status}")
    
    if passed == total:
        print("\n🎉 ALL QUALITY GATE VALIDATION TESTS PASSED")
        return True
    else:
        print(f"\n⚠️  {total - passed} TEST(S) FAILED")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)