"""Тесты Hermes → Cursor feed."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.learning.hermes_cursor_feed import (
    CURSOR_LIVE_FILENAME,
    FEED_JSONL_NAME,
    MIRROR_MD_NAME,
    build_cursor_brief,
    pick_zero_one_recommendation,
    report_fingerprint,
    write_cursor_feed_files,
)
from prd_agent.learning.winning_entry_rules import (
    WeightRecommendation,
    WinningEntryRulesReport,
)


def test_build_cursor_brief_contains_zero_one():
    rec = WeightRecommendation(
        filter_id="derivatives_guard",
        action="consider_remove",
        suggested_weight_mult=0.0,
        confidence="high",
        reason_ru="Отсекает много виртуальных TP",
        n_samples=12,
    )
    report = WinningEntryRulesReport(
        hours=168,
        tp_winners=5,
        tp_skipped_virtual=3,
        tp_opened_real=2,
        sl_losers=2,
        outcome_counts={"profit": 5, "loss": 2, "neutral": 1},
        weight_recommendations=[rec],
    )
    md = build_cursor_brief(report, source_label="AGENT-WORLD")
    assert "hermes_feed: true" in md
    assert "derivatives_guard" in md
    assert "ZeroOne" in md
    assert pick_zero_one_recommendation(report) is rec


def test_write_cursor_feed_files(tmp_path: Path):
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    report = WinningEntryRulesReport(
        hours=24,
        tp_winners=1,
        tp_skipped_virtual=1,
        tp_opened_real=0,
        sl_losers=0,
        outcome_counts={"profit": 1, "loss": 0, "neutral": 0},
    )
    cursor_p, mirror_p, feed_p = write_cursor_feed_files(
        report, repo_root=repo, data_dir=data, source_label="test"
    )
    assert cursor_p.name == CURSOR_LIVE_FILENAME
    assert mirror_p.name == MIRROR_MD_NAME
    assert feed_p.name == FEED_JSONL_NAME
    assert cursor_p.is_file()
    assert mirror_p.read_text(encoding="utf-8") == cursor_p.read_text(encoding="utf-8")
    lines = feed_p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["event"] == "hermes_analysis"
    assert report_fingerprint(report) == row["fingerprint"]
