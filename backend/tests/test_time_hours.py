"""Tests for local vs UTC entry hour blocking."""
from prd_agent.time_hours import entry_check_hour, format_blocked_hour_label, read_timezone_offset


def test_entry_check_hour_local_offset() -> None:
    assert entry_check_hour(15, 3) == 18
    assert entry_check_hour(18, 3) == 21
    assert entry_check_hour(22, 3) == 1


def test_entry_check_hour_utc_when_offset_zero() -> None:
    assert entry_check_hour(18, 0) == 18


def test_read_timezone_offset() -> None:
    assert read_timezone_offset({}) == 0
    assert read_timezone_offset({"timezone_offset": 3}) == 3


def test_format_blocked_hour_label_local() -> None:
    label = format_blocked_hour_label(15, 18, 3)
    assert "18" in label
    assert "UTC+3" in label
