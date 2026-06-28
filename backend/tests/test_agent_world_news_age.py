"""AGENT-WORLD: не торговать по RSS-новостям старше max_news_age_hours."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from telegram_agent.world_feed import (
    is_news_too_stale_for_trade,
    news_age_hours,
    parse_event_published_utc,
)


def test_parse_event_published_utc_rfc2822():
    event = {"published_hint": "Thu, 15 May 2026 10:30:00 GMT"}
    pub = parse_event_published_utc(event)
    assert pub is not None
    assert pub.year == 2026
    assert pub.month == 5
    assert pub.day == 15


def test_parse_event_published_utc_iso():
    event = {"published_hint": "2026-06-28T04:22:27+00:00"}
    pub = parse_event_published_utc(event)
    assert pub is not None
    assert pub.hour == 4


def test_news_age_hours_fresh():
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    event = {"published_hint": "2026-06-28T08:00:00+00:00"}
    assert news_age_hours(event, now=now) == 2.0


def test_is_news_too_stale_blocks_old_article():
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    event = {
        "published_hint": "Thu, 15 May 2026 12:00:00 GMT",
        "title": "Thorchain exploit",
    }
    stale, reason = is_news_too_stale_for_trade(event, 5.0, now=now)
    assert stale is True
    assert "устарела" in reason


def test_is_news_too_stale_allows_recent():
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    event = {"published_hint": (now - timedelta(hours=3)).isoformat()}
    stale, reason = is_news_too_stale_for_trade(event, 5.0, now=now)
    assert stale is False
    assert reason == ""


def test_is_news_too_stale_disabled_when_max_zero():
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    event = {"published_hint": "Thu, 01 Jan 2020 00:00:00 GMT"}
    stale, _ = is_news_too_stale_for_trade(event, 0.0, now=now)
    assert stale is False


def test_is_news_too_stale_blocks_unparseable_date():
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    event = {"published_hint": "not-a-date", "title": "x"}
    stale, reason = is_news_too_stale_for_trade(event, 5.0, now=now)
    assert stale is True
    assert "не удалось определить дату" in reason
