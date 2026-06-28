"""Блок исполнения MARKET SCANNER по локальному часу (timezone_offset)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from scripts.telegram_signal_agent import TelegramSignalAgent


def _minimal_agent(**overrides) -> TelegramSignalAgent:
    agent = TelegramSignalAgent.__new__(TelegramSignalAgent)
    defaults = {
        "market_scanner_execute_local_hours_min": 9,
        "timezone_offset": 3,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(agent, key, value)
    return agent


def test_execute_not_blocked_when_local_hours_min_zero():
    agent = _minimal_agent(market_scanner_execute_local_hours_min=0)
    blocked, reason = agent._market_scanner_execute_blocked_by_hour()
    assert not blocked
    assert reason == ""


def test_execute_blocked_before_local_hours_min():
    agent = _minimal_agent()
    # UTC 04:00 → local 07:00 при offset +3
    fake_now = datetime(2026, 6, 28, 4, 0, 0, tzinfo=timezone.utc)
    with patch("scripts.telegram_signal_agent.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        blocked, reason = agent._market_scanner_execute_blocked_by_hour()
    assert blocked
    assert "local_hour=7<9" in reason


def test_execute_allowed_at_or_after_local_hours_min():
    agent = _minimal_agent()
    # UTC 07:00 → local 10:00 при offset +3
    fake_now = datetime(2026, 6, 28, 7, 0, 0, tzinfo=timezone.utc)
    with patch("scripts.telegram_signal_agent.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        blocked, reason = agent._market_scanner_execute_blocked_by_hour()
    assert not blocked
    assert reason == ""
