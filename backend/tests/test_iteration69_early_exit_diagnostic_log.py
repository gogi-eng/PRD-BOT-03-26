#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"


def test_main_logs_early_exit_diagnostics_before_close():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "[EARLY_EXIT] " in source
    assert "required_profit=" in source
    assert "effective_profit=" in source
    assert "raw_profit=" in source
    assert "best_profit=" in source
    assert "fee_floor=" in source
    assert "bars=" in source
