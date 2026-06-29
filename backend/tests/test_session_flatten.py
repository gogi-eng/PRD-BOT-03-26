"""Тесты session_flatten: триггер за lead_minutes до local_hour."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from prd_agent.risk.session_flatten import SessionFlattenConfig, SessionFlattenGuard


def _cfg(*, hours=None, lead=30, enabled=True):
    return {
        "timezone_offset": 3,
        "session_flatten": {
            "enabled": enabled,
            "local_hours": hours or [16],
            "lead_minutes": lead,
            "skip_manual": True,
        },
    }


def test_flatten_disabled():
    guard = SessionFlattenGuard(_cfg(enabled=False))
    assert guard.due_trigger() is None


def test_flatten_triggers_at_1530_before_16():
    guard = SessionFlattenGuard(_cfg(hours=[16], lead=30))
    # UTC 12:30 → local 15:30 Moscow
    fake_utc = datetime(2026, 6, 28, 12, 30, 0, tzinfo=timezone.utc)
    with patch("prd_agent.risk.session_flatten.datetime") as mock_dt:
        mock_dt.now.return_value = fake_utc
        mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
        result = guard.due_trigger(_last_keys=set())
    assert result == (16, "2026-06-28-16")


def test_flatten_not_due_too_early():
    guard = SessionFlattenGuard(_cfg(hours=[16], lead=30))
    # UTC 11:00 → local 14:00 (60 min to 16:00)
    fake_utc = datetime(2026, 6, 28, 11, 0, 0, tzinfo=timezone.utc)
    with patch("prd_agent.risk.session_flatten.datetime") as mock_dt:
        mock_dt.now.return_value = fake_utc
        result = guard.due_trigger(_last_keys=set())
    assert result is None


def test_flatten_dedup_same_key():
    guard = SessionFlattenGuard(_cfg(hours=[16], lead=30))
    fake_utc = datetime(2026, 6, 28, 12, 45, 0, tzinfo=timezone.utc)
    with patch("prd_agent.risk.session_flatten.datetime") as mock_dt:
        mock_dt.now.return_value = fake_utc
        first = guard.due_trigger(_last_keys=set())
        assert first is not None
        second = guard.due_trigger(_last_keys={first[1]})
    assert second is None


def test_sandbox_two_windows():
    guard = SessionFlattenGuard(_cfg(hours=[12, 16], lead=30))
    # UTC 08:30 → local 11:30
    fake_utc = datetime(2026, 6, 28, 8, 30, 0, tzinfo=timezone.utc)
    with patch("prd_agent.risk.session_flatten.datetime") as mock_dt:
        mock_dt.now.return_value = fake_utc
        result = guard.due_trigger(_last_keys=set())
    assert result == (12, "2026-06-28-12")
