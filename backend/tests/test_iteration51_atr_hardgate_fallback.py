#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"


def test_entry_hardgate_uses_atr_resolver_instead_of_raw_metadata_default():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "atr_pct = self._resolve_signal_atr_pct(signal, market)" in source
    gate_anchor = source.index("# ENTRY HARD-GATES")
    gate_block = source[gate_anchor: gate_anchor + 700]
    assert 'atr_pct = float(signal.metadata.get("atr_pct", 0.0) or 0.0)' not in gate_block


def test_atr_resolver_falls_back_to_market_when_metadata_missing():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "def _resolve_signal_atr_pct(signal: EntrySignal, market) -> float:" in source
    assert 'meta_atr_pct = signal.metadata.get("atr_pct")' in source
    assert "if meta_atr_pct is None:" in source
    assert 'return float(getattr(market, "atr_pct", 0.0) or 0.0)' in source


def test_atr_resolver_handles_invalid_metadata_values():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert "except (TypeError, ValueError):" in source
    assert 'return float(getattr(market, "atr_pct", 0.0) or 0.0)' in source
