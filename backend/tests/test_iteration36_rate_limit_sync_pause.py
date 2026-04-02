#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from main import TradingBot


class _ClientStub:
    def __init__(self, ts: float):
        self.last_rate_limit_at_monotonic = ts


def test_exchange_sync_pause_remaining_after_recent_rate_limit():
    bot = TradingBot.__new__(TradingBot)
    bot.exchange_closed_pause_after_rate_limit_sec = 180
    bot.client = _ClientStub(time.monotonic())

    rem = bot._exchange_closed_sync_pause_remaining()
    assert rem > 0
    assert rem <= 180


def test_exchange_sync_pause_remaining_zero_when_expired():
    bot = TradingBot.__new__(TradingBot)
    bot.exchange_closed_pause_after_rate_limit_sec = 180
    bot.client = _ClientStub(time.monotonic() - 400)

    assert bot._exchange_closed_sync_pause_remaining() == 0
