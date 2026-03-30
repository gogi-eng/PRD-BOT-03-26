#!/usr/bin/env python3
"""Integration-oriented source checks for SCALP wiring in TradingBot."""
from __future__ import annotations

from pathlib import Path

BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"


def test_trading_bot_initializes_scalp_strategy():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "ScalpSessionStrategy" in source
    assert "self.scalp_strategy =" in source
    assert 'self.cfg.get("scalp"' in source


def test_analyze_symbol_uses_scalp_before_entry_engine():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "self.scalp_strategy.analyze" in source
    assert 'signal.metadata["reject_reason"] = reason' in source
    assert '"strategy": "scalp_session"' in source
    assert "SCALP SIGNAL" in source
