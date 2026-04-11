#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"


def test_manage_positions_counts_only_closed_candles_for_bars_since_entry():
    source = MAIN_PATH.read_text(encoding="utf-8")
    assert "def _last_closed_kline_ts(klines: list) -> int:" in source
    assert "latest_closed_ts = self._last_closed_kline_ts(klines)" in source
    assert "prev_ts = int(getattr(pos, \"last_counted_kline_ts\", 0) or 0)" in source
    assert "bars_delta = max(1, int(round(delta_ms / max(1, interval_sec * 1000))))" in source
    assert "pos.bars_since_entry += bars_delta" in source
    assert "pos.last_counted_kline_ts = latest_closed_ts" in source
