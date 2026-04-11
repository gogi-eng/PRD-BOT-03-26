#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from analysis.correlation_filter import CorrelationFilter
from engine.symbol_quality_filter import SymbolQualityFilter


class MockCfg:
    def __init__(self, values: dict[tuple[str, str], object]):
        self.values = values

    def get(self, *keys, default=None):
        return self.values.get(tuple(keys), default)


def test_symbol_quality_blocks_poor_symbol(tmp_path: Path):
    data_path = tmp_path / "feedback.json"
    rows = []
    for i in range(5):
        rows.append(
            {
                "symbol": "BANUSDT",
                "result": "loss",
                "pnl_pct": -1.2,
                "source": "signal_only_feedback",
                "entry_time": f"2026-03-1{i}T00:00:00+00:00",
            }
        )
    data_path.write_text(json.dumps(rows), encoding="utf-8")

    cfg = MockCfg(
        {
            ("symbol_quality", "enabled"): True,
            ("symbol_quality", "dataset_path"): str(data_path),
            ("symbol_quality", "feedback_only"): True,
            ("symbol_quality", "min_trades"): 4,
            ("symbol_quality", "min_winrate"): 0.35,
            ("symbol_quality", "min_avg_pnl_pct"): -0.8,
            ("symbol_quality", "max_recent_losses"): 3,
            ("symbol_quality", "lookback_per_symbol"): 20,
            ("symbol_quality", "cache_ttl_sec"): 1,
            ("symbol_quality", "whitelist_bypass"): False,
        }
    )
    filt = SymbolQualityFilter(tmp_path, cfg)
    allowed, reason, stats = filt.allow("BANUSDT", is_whitelisted=False)
    assert allowed is False
    assert reason in {"consecutive_losses", "low_quality"}
    assert stats.get("trades", 0) >= 4


def test_symbol_quality_allows_good_symbol(tmp_path: Path):
    data_path = tmp_path / "feedback.json"
    rows = [
        {"symbol": "RIVERUSDT", "result": "win", "pnl_pct": 2.0, "source": "signal_only_feedback", "entry_time": "2026-03-10T00:00:00+00:00"},
        {"symbol": "RIVERUSDT", "result": "win", "pnl_pct": 1.1, "source": "signal_only_feedback", "entry_time": "2026-03-11T00:00:00+00:00"},
        {"symbol": "RIVERUSDT", "result": "loss", "pnl_pct": -0.3, "source": "signal_only_feedback", "entry_time": "2026-03-12T00:00:00+00:00"},
        {"symbol": "RIVERUSDT", "result": "win", "pnl_pct": 0.8, "source": "signal_only_feedback", "entry_time": "2026-03-13T00:00:00+00:00"},
    ]
    data_path.write_text(json.dumps(rows), encoding="utf-8")

    cfg = MockCfg(
        {
            ("symbol_quality", "enabled"): True,
            ("symbol_quality", "dataset_path"): str(data_path),
            ("symbol_quality", "feedback_only"): True,
            ("symbol_quality", "min_trades"): 4,
            ("symbol_quality", "min_winrate"): 0.35,
            ("symbol_quality", "min_avg_pnl_pct"): -0.8,
            ("symbol_quality", "max_recent_losses"): 3,
            ("symbol_quality", "lookback_per_symbol"): 20,
            ("symbol_quality", "cache_ttl_sec"): 1,
            ("symbol_quality", "whitelist_bypass"): False,
        }
    )
    filt = SymbolQualityFilter(tmp_path, cfg)
    allowed, reason, _ = filt.allow("RIVERUSDT", is_whitelisted=False)
    assert allowed is True
    assert reason == "ok"


def test_correlation_filter_blocks_strongly_correlated_pair():
    filt = CorrelationFilter(threshold=0.7, max_correlated=1, lookback=20)
    base = [100 + i for i in range(25)]
    twin = [200 + 2 * i for i in range(25)]
    filt.update_prices("BTCUSDT", base)
    filt.update_prices("ETHUSDT", twin)
    should, reason = filt.should_filter("ETHUSDT", ["BTCUSDT"])
    assert should is True
    assert "Correlated with BTCUSDT" in reason
