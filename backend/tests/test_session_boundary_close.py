#!/usr/bin/env python3
"""Тесты session_boundary_close."""
from __future__ import annotations

from datetime import datetime, timezone

from prd_agent.positions.session_boundary_close import (
    SessionBoundaryCloseConfig,
    active_session_close_slot,
    should_run_session_flush,
)


def test_active_slot_at_0631_local():
    cfg = SessionBoundaryCloseConfig(
        enabled=True,
        timezone_offset=3,
        close_at_local=("06:30", "11:00", "17:00"),
        window_minutes=3,
    )
    now_local = datetime(2026, 7, 10, 6, 31, tzinfo=timezone.utc)
    assert active_session_close_slot(cfg, now=now_local) == "06:30"
    assert active_session_close_slot(cfg, now=datetime(2026, 7, 10, 10, 59, tzinfo=timezone.utc)) is None


def test_should_run_once_per_day():
    cfg = SessionBoundaryCloseConfig(
        enabled=True,
        timezone_offset=3,
        close_at_local=("11:00",),
        window_minutes=5,
    )
    now = datetime(2026, 7, 10, 11, 2, tzinfo=timezone.utc)
    ok, slot = should_run_session_flush(cfg, [], now=now)
    assert ok is True
    assert slot == "11:00"
    ok2, _ = should_run_session_flush(cfg, ["2026-07-10:11:00"], now=now)
    assert ok2 is False
