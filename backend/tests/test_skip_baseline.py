"""Тесты skip baseline telemetry."""
from __future__ import annotations

from prd_agent.telemetry.skip_baseline import bucket_skip_reason, skip_baseline_from_rows


def test_bucket_quality_gate_rr():
    assert bucket_skip_reason("quality_gate: RR 1.5 < 2.0") == "quality_gate_rr"


def test_baseline_percentages():
    rows = [
        {"status": "skipped", "reason": "quality_gate: RR low"},
        {"status": "skipped", "reason": "pullback: нет отката"},
        {"status": "executed", "reason": ""},
    ]
    rep = skip_baseline_from_rows(rows, hours=24)
    assert rep["skipped"] == 2
    assert rep["total_signals"] == 3
    assert rep["pct_skipped_of_total"] == 66.7
    assert "pullback_entry" in rep["by_bucket"]
