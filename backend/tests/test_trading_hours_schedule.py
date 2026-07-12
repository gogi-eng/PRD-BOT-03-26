#!/usr/bin/env python3
"""Тесты trading_hours_schedule."""
from __future__ import annotations

from prd_agent.analysis.trading_hours_schedule import (
    merge_consecutive_hours,
    windows_from_blocked_hours,
)


def test_merge_consecutive_hours():
    assert merge_consecutive_hours({3, 4, 6, 7, 8, 11, 12, 13}) == [
        (3, 4),
        (6, 8),
        (11, 13),
    ]


def test_windows_resume_five_min_before():
    windows = windows_from_blocked_hours({6, 7, 8, 11, 12, 13, 16, 17, 18}, resume_before_minutes=5)
    by_stop = {w.stop_at: w.resume_at for w in windows}
    assert by_stop["06:00"] == "08:55"
    assert by_stop["11:00"] == "13:55"
    assert by_stop["16:00"] == "18:55"
