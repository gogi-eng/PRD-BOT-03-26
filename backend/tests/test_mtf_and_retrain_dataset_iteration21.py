#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from main import TradingBot


class DummyZoneContext:
    def __init__(self, bull=False, bear=False):
        self._bull = bull
        self._bear = bear

    def price_in_bullish_zone(self, _price):
        return object() if self._bull else None

    def price_near_bullish_zone(self, _price, _tol):
        return object() if self._bull else None

    def price_in_bearish_zone(self, _price):
        return object() if self._bear else None

    def price_near_bearish_zone(self, _price, _tol):
        return object() if self._bear else None


def test_zone_matches_side_for_long_and_short():
    assert TradingBot._zone_matches_side(DummyZoneContext(bull=True), 100.0, "BUY") is True
    assert TradingBot._zone_matches_side(DummyZoneContext(bear=True), 100.0, "SELL") is True
    assert TradingBot._zone_matches_side(DummyZoneContext(bull=False, bear=False), 100.0, "BUY") is False


def test_build_retrain_dataset_merges_and_filters_quality_feedback(tmp_path: Path):
    bot = TradingBot.__new__(TradingBot)
    bot.feedback_use_merged_dataset_for_retrain = True
    bot.feedback_base_dataset_path = tmp_path / "training_data.json"
    bot.feedback_min_label_abs_pnl_pct = 0.4
    bot.feedback_min_label_hold_minutes = 8.0

    class SignalFeedbackStub:
        dataset_path = tmp_path / "signal_only_feedback_data.json"

    bot.signal_feedback = SignalFeedbackStub()

    base = [{"symbol": "BTCUSDT", "result": "win"}]
    feedback = [
        {
            "symbol": "AAAUSDT",
            "source": "signal_only_feedback",
            "result": "win",
            "exit_reason": "take_profit",
            "pnl_pct": 1.2,
            "entry_time": "2026-03-19T00:00:00+00:00",
            "exit_time": "2026-03-19T00:20:00+00:00",
        },
        {
            "symbol": "BBBUSDT",
            "source": "signal_only_feedback",
            "result": "loss",
            "exit_reason": "stop_loss",
            "pnl_pct": -0.1,
            "entry_time": "2026-03-19T00:00:00+00:00",
            "exit_time": "2026-03-19T00:04:00+00:00",
        },
    ]
    bot.feedback_base_dataset_path.write_text(json.dumps(base), encoding="utf-8")
    bot.signal_feedback.dataset_path.write_text(json.dumps(feedback), encoding="utf-8")

    out = bot._build_retrain_dataset()
    rows = json.loads(Path(out).read_text(encoding="utf-8"))
    symbols = {row.get("symbol") for row in rows}
    assert "BTCUSDT" in symbols
    assert "AAAUSDT" in symbols
    assert "BBBUSDT" not in symbols
