"""Поиск сделок в постах Telegram-каналов по умолчанию выключен."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from types import SimpleNamespace

from scripts.telegram_signal_agent import TelegramSignalAgent, channel_trade_search_enabled


def test_channel_trade_search_missing_key_is_off():
    assert channel_trade_search_enabled({}) is False
    assert channel_trade_search_enabled(None) is False
    assert channel_trade_search_enabled({"auto_execute": True}) is False
    assert channel_trade_search_enabled({"channel_trade_search": False}) is False
    assert channel_trade_search_enabled({"channel_trade_search": True}) is True


def test_process_message_does_not_parse_when_search_off(monkeypatch):
    agent = object.__new__(TelegramSignalAgent)
    agent.channel_trade_search = False
    seen: list[tuple[str, int]] = []
    agent._is_seen = lambda source, message_id: False
    agent._is_recent_message = lambda message_dt: True
    agent._is_ignored_source = lambda source: False
    agent._touch_channel_activity = lambda *args, **kwargs: None
    agent._mark_seen = lambda source, message_id: seen.append((source, message_id))

    def _parse_must_not_run(*args, **kwargs):
        raise AssertionError("разбор поста на сделку не должен запускаться")

    monkeypatch.setattr("scripts.telegram_signal_agent.parse_signal_text", _parse_must_not_run)
    asyncio.run(
        agent.process_message(
            "Cornix Trading Signals (free)",
            42,
            datetime.now(timezone.utc),
            "BTCUSDT LONG\nEntry: 100\nSL: 90\nTP: 120",
        )
    )
    assert seen == [("Cornix Trading Signals (free)", 42)]


def test_process_message_parses_when_search_on(monkeypatch):
    agent = object.__new__(TelegramSignalAgent)
    agent.channel_trade_search = True
    agent.channel_auto_block_cfg = SimpleNamespace(enabled=False)
    agent.photo_ocr_enabled = False
    agent.default_leverage = 10
    agent.signal_parse_simple = True
    agent.infer_side_from_sr = False
    agent.log_unparsed_signal_hints = False
    called = {"parse": False, "seen": False}
    agent._is_seen = lambda source, message_id: False
    agent._is_recent_message = lambda message_dt: True
    agent._is_ignored_source = lambda source: False
    agent._touch_channel_activity = lambda *args, **kwargs: None
    agent._mark_seen = lambda source, message_id: called.__setitem__("seen", True)

    def _parse(*args, **kwargs):
        called["parse"] = True
        return None

    monkeypatch.setattr("scripts.telegram_signal_agent.parse_signal_text", _parse)
    asyncio.run(
        agent.process_message(
            "канал",
            7,
            datetime.now(timezone.utc),
            "ETHUSDT SHORT",
        )
    )
    assert called["parse"] is True
    assert called["seen"] is True


def test_run_once_skips_channel_history_when_search_off():
    agent = object.__new__(TelegramSignalAgent)
    agent.channel_trade_search = False
    calls = {"reviews": 0, "excursions": 0, "world": 0, "scan": 0}

    async def _reviews():
        calls["reviews"] += 1

    async def _excursions():
        calls["excursions"] += 1

    async def _world():
        calls["world"] += 1

    async def _scan():
        calls["scan"] += 1

    async def _client_must_not_run():
        raise AssertionError("Telethon не нужен, когда поиск сделок выключен")

    agent._evaluate_pending_signal_reviews = _reviews
    agent._evaluate_pending_excursions = _excursions
    agent._drain_world_queue = _world
    agent.run_market_scan_once = _scan
    agent._telegram_client = _client_must_not_run
    asyncio.run(agent.run_once(30))
    assert calls == {"reviews": 2, "excursions": 2, "world": 2, "scan": 1}
