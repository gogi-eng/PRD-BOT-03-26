"""Тесты ApiCallJournal."""
from __future__ import annotations

from prd_agent.exchange.api_stats import ApiCallJournal


def test_cycle_counting():
    j = ApiCallJournal()
    j.begin_cycle(1)
    j.record("klines")
    j.record("klines")
    j.record("tickers")
    j.record("price", cached=True)
    snap = j.end_cycle()
    assert snap["calls"] == 3
    assert snap["by_endpoint"]["klines"] == 2
    assert snap["cache_hits"].get("price") == 1
