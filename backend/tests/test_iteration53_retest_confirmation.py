#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"
BOT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "bot" / "config.yaml"


def test_retest_confirmation_config_exists():
    source = BOT_CONFIG_PATH.read_text(encoding="utf-8")
    assert "impulse_retest_confirm_enabled: true" in source
    assert "impulse_min_body_atr: 0.45" in source
    assert "retest_max_body_ratio: 0.85" in source
    assert "confirm_min_body_ratio: 0.35" in source


def test_retest_confirmation_loaded_in_bot_init():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert 'self.entry_impulse_retest_confirm_enabled = bool(' in source
    assert 'self.cfg.get("entry", "impulse_retest_confirmation_enabled", default=True)' in source
    assert 'self.entry_impulse_min_body_atr = float(' in source
    assert 'self.cfg.get("entry", "impulse_retest_impulse_atr_mult", default=0.8)' in source
    assert 'self.entry_retest_max_body_atr = float(' in source
    assert 'self.cfg.get("entry", "impulse_retest_retest_atr_mult", default=0.35)' in source


def test_retest_confirmation_guard_present_in_analyze_symbol():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "IMPULSE → RETEST → CONFIRM GUARD" in source
    assert "_passes_impulse_retest_confirmation(" in source
    assert "impulse_retest_confirm_guard (no_retest_candle)" in source
    assert "impulse_retest_confirm_guard (no_confirmation_candle)" in source

