#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from main import TradingBot


def test_exchange_closed_reentry_cooldown_applies_and_expires():
    bot = TradingBot.__new__(TradingBot)
    bot.exchange_closed_reentry_cooldown_sec = 1
    bot._exchange_closed_reentry_until = {}

    bot._set_exchange_closed_reentry_block("RDNTUSDT")
    first = bot._exchange_closed_reentry_remaining("RDNTUSDT")
    assert first > 0

    time.sleep(1.1)
    assert bot._exchange_closed_reentry_remaining("RDNTUSDT") == 0
