#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"
BOT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "bot" / "config.yaml"


def test_main_has_active_hours_scan_interval_logic():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "self.scan_interval_active_hours_sec" in source
    assert "def _should_scan_entries_now(self) -> bool:" in source
    assert "self.scalp_strategy.pump_hours_local" in source
    assert "self.scalp_strategy.dump_hours_local" in source
    assert "if local_hour in active_hours:" in source
    assert "interval = min(interval, active_interval)" in source


def test_config_has_active_hours_scan_interval_key():
    source = BOT_CONFIG_PATH.read_text(encoding="utf-8")
    assert "scan_interval_active_hours_sec: 20" in source
