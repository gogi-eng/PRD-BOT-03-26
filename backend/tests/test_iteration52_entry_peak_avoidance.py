#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"
BOT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "bot" / "config.yaml"


def test_peak_guard_config_exists():
    source = BOT_CONFIG_PATH.read_text(encoding="utf-8")
    assert "anti_peak_guard_enabled: true" in source
    assert "anti_peak_lookback: 24" in source
    assert "anti_peak_atr_buffer_mult: 0.35" in source


def test_peak_guard_is_loaded_in_bot_init():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert 'self.entry_peak_reversal_guard = bool(' in source
    assert 'self.cfg.get("entry", "anti_peak_guard_enabled", default=True)' in source
    assert 'self.entry_peak_lookback_bars = int(' in source
    assert 'self.cfg.get("entry", "anti_peak_lookback", default=24)' in source
    assert 'self.entry_peak_distance_atr = float(' in source
    assert 'self.cfg.get("entry", "anti_peak_atr_buffer_mult", default=0.35)' in source


def test_peak_guard_rejects_near_local_extremes_in_analyze_symbol():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "PEAK REVERSAL GUARD" in source
    assert "peak_reversal_guard" in source
    assert "BUY near local high" in source
    assert "SELL near local low" in source

