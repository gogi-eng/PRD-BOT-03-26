#!/usr/bin/env python3
"""
Additional backend test for Quality-Gate anti-flat filters and signal-only integration.
"""
import os
import sys
from pathlib import Path

# Add bot directory to path
BOT_DIR = Path(__file__).parent / "bot"
sys.path.insert(0, str(BOT_DIR))

def test_anti_flat_rejection_scenarios():
    """Test specific anti-flat rejection scenarios."""
    print("=== ANTI-FLAT REJECTION SCENARIOS TEST ===")
    try:
        from engine.entry_engine import EntrySignal
        from main import TradingBot
        
        # Create bot with default quality gate config
        bot = TradingBot.__new__(TradingBot)
        bot.quality_gate_enabled = True
        bot.quality_gate_min_confidence = 0.68
        bot.quality_gate_min_expected_edge = 0.75
        bot.quality_gate_min_adx = 16.0
        bot.quality_gate_min_atr_pct = 0.20
        bot.quality_gate_min_abs_imbalance = 0.08
        bot.quality_gate_allow_chop = False
        bot.quality_gate_require_htf_trend = False
        
        # Test case 1: Good signal should pass
        good_signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.85,  # Above 0.68
            rr_ratio=3.0,     # Edge = 0.85 * 4 - 1 = 2.4 > 0.75
            metadata={
                "regime": "trend",      # Not chop
                "adx": 25.0,           # Above 16.0
                "atr_pct": 0.35,       # Above 0.20
                "htf_trend": "up",     # Not flat
                "normalized_imbalance": 0.15,  # |0.15| > 0.08
            },
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", good_signal)
        if not ok or reason != "ok":
            print(f"❌ FAIL: Good signal rejected: {reason}")
            return False
        print(f"✅ Good signal passed: edge={meta['quality_expected_edge']:.3f}")
        
        # Test case 2: Chop regime rejection
        chop_signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.85,
            rr_ratio=3.0,
            metadata={
                "regime": "chop",      # Should reject
                "adx": 25.0,
                "atr_pct": 0.35,
                "htf_trend": "up",
                "normalized_imbalance": 0.15,
            },
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", chop_signal)
        if ok or reason != "chop_regime":
            print(f"❌ FAIL: Chop regime not rejected: ok={ok}, reason={reason}")
            return False
        print("✅ Chop regime correctly rejected")
        
        # Test case 3: Low ADX rejection
        low_adx_signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.85,
            rr_ratio=3.0,
            metadata={
                "regime": "trend",
                "adx": 12.0,          # Below 16.0, should reject
                "atr_pct": 0.35,
                "htf_trend": "up",
                "normalized_imbalance": 0.15,
            },
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", low_adx_signal)
        if ok or reason != "low_adx":
            print(f"❌ FAIL: Low ADX not rejected: ok={ok}, reason={reason}")
            return False
        print("✅ Low ADX correctly rejected")
        
        # Test case 4: Low ATR rejection
        low_atr_signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.85,
            rr_ratio=3.0,
            metadata={
                "regime": "trend",
                "adx": 25.0,
                "atr_pct": 0.15,      # Below 0.20, should reject
                "htf_trend": "up",
                "normalized_imbalance": 0.15,
            },
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", low_atr_signal)
        if ok or reason != "low_atr":
            print(f"❌ FAIL: Low ATR not rejected: ok={ok}, reason={reason}")
            return False
        print("✅ Low ATR correctly rejected")
        
        # Test case 5: Low imbalance rejection
        low_imb_signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.85,
            rr_ratio=3.0,
            metadata={
                "regime": "trend",
                "adx": 25.0,
                "atr_pct": 0.35,
                "htf_trend": "up",
                "normalized_imbalance": 0.05,  # |0.05| < 0.08, should reject
            },
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", low_imb_signal)
        if ok or reason != "flat_orderflow":
            print(f"❌ FAIL: Low imbalance not rejected: ok={ok}, reason={reason}")
            return False
        print("✅ Low imbalance correctly rejected")
        
        # Test case 6: Low expected edge rejection
        low_edge_signal = EntrySignal(
            should_enter=True,
            side="BUY",
            confidence=0.70,  # 0.70 * 2.0 - 1 = 0.4 < 0.75
            rr_ratio=1.0,     
            metadata={
                "regime": "trend",
                "adx": 25.0,
                "atr_pct": 0.35,
                "htf_trend": "up",
                "normalized_imbalance": 0.15,
            },
        )
        ok, reason, meta = bot._passes_signal_quality_gate("BTCUSDT", low_edge_signal)
        if ok or reason != "low_expected_edge":
            print(f"❌ FAIL: Low expected edge not rejected: ok={ok}, reason={reason}")
            return False
        print(f"✅ Low expected edge correctly rejected: edge={meta['quality_expected_edge']:.3f}")
        
        print("✅ PASS: All anti-flat rejection scenarios working correctly")
        return True
        
    except Exception as e:
        print(f"❌ FAIL: Error in anti-flat rejection test: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_signal_only_integration_path():
    """Test that quality gate is correctly integrated in signal-only path."""
    print("\n=== SIGNAL-ONLY INTEGRATION PATH TEST ===")
    
    # Check main.py for the specific integration
    main_path = Path(__file__).parent / "bot" / "main.py"
    content = main_path.read_text()
    
    # Find the _scan_entries method and check signal-only integration
    scan_entries_start = content.find('async def _scan_entries(self, symbols: list):')
    if scan_entries_start == -1:
        print("❌ FAIL: _scan_entries method not found")
        return False
    
    # Look for signal-only mode section
    signal_only_section = content.find('if self.signal_only:', scan_entries_start)
    if signal_only_section == -1:
        print("❌ FAIL: signal_only section not found in _scan_entries")
        return False
    
    # Check quality gate is called before Telegram send
    quality_gate_check = content.find('if self.signal_only and self.quality_gate_enabled:', scan_entries_start)
    if quality_gate_check == -1:
        print("❌ FAIL: Quality gate check not found in signal-only section")
        return False
    
    # Check that the quality gate is before the Telegram message construction
    telegram_send = content.find('await self.tg.send_message(msg)', signal_only_section)
    if telegram_send == -1:
        print("❌ FAIL: Telegram send not found")
        return False
    
    # Verify quality gate comes before Telegram send
    if quality_gate_check > telegram_send:
        print("❌ FAIL: Quality gate check comes after Telegram send")
        return False
    
    # Check for signal registration after quality gate
    signal_register = content.find('self.signal_feedback.register_signal(symbol, signal)', quality_gate_check)
    if signal_register == -1 or signal_register > telegram_send:
        print("❌ FAIL: Signal registration not found after quality gate and before Telegram")
        return False
    
    print("✅ PASS: Quality gate correctly integrated in signal-only path before Telegram send")
    return True

def test_config_values_match():
    """Test that config.yaml values match the review request expectations."""
    print("\n=== CONFIG VALUES VALIDATION ===")
    config_path = Path(__file__).parent / "bot" / "config.yaml"
    content = config_path.read_text()
    
    # Parse the quality_gate section
    lines = content.split('\n')
    quality_gate_section = False
    config_values = {}
    
    for line in lines:
        original_line = line
        line = line.strip()
        if line == "quality_gate:":
            quality_gate_section = True
            continue
        elif quality_gate_section:
            if line and not original_line.startswith('  ') and ':' in line:
                break  # End of quality_gate section (new top-level key)
            if ':' in line and original_line.startswith('  '):
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                config_values[key] = value
    
    expected_values = {
        "enabled": "true",
        "min_confidence": "0.68",
        "min_expected_edge": "0.75",
        "anti_flat_min_adx": "16.0",
        "anti_flat_min_atr_pct": "0.20",
        "anti_flat_min_abs_imbalance": "0.08",
        "anti_flat_allow_chop": "false",
        "anti_flat_require_htf_trend": "false",
    }
    
    all_match = True
    for key, expected in expected_values.items():
        actual = config_values.get(key)
        if actual != expected:
            print(f"❌ Config mismatch {key}: expected={expected}, actual={actual}")
            all_match = False
        else:
            print(f"✅ Config match {key}: {actual}")
    
    if all_match:
        print("✅ PASS: All config values match expectations")
        return True
    else:
        print("❌ FAIL: Some config values don't match")
        return False

def main():
    """Run additional quality gate tests."""
    print("🔍 ADDITIONAL QUALITY GATE VALIDATION TESTS")
    print("=" * 60)
    
    tests = [
        test_anti_flat_rejection_scenarios,
        test_signal_only_integration_path,
        test_config_values_match,
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
    print("📊 ADDITIONAL TESTS SUMMARY")
    print("=" * 60)
    
    passed = sum(results)
    total = len(results)
    
    print(f"Tests passed: {passed}/{total}")
    for i, (test_func, result) in enumerate(zip(tests, results)):
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{i+1}. {test_func.__name__}: {status}")
    
    if passed == total:
        print("\n🎉 ALL ADDITIONAL QUALITY GATE TESTS PASSED")
        return True
    else:
        print(f"\n⚠️  {total - passed} ADDITIONAL TEST(S) FAILED")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)