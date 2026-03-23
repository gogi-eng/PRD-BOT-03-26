#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from main import TradingBot


class _PMStub:
    def __init__(self, count_value: int):
        self._count = count_value

    def count(self):
        return self._count


def test_get_cycle_sleep_shorter_when_positions_active_live():
    bot = TradingBot.__new__(TradingBot)
    bot.cycle_sleep = 60
    bot.position_active_sleep_sec = 15
    bot.signal_only = False
    bot.position_manager = _PMStub(2)

    assert bot._get_cycle_sleep_sec() == 15


def test_get_cycle_sleep_default_when_no_positions():
    bot = TradingBot.__new__(TradingBot)
    bot.cycle_sleep = 60
    bot.position_active_sleep_sec = 15
    bot.signal_only = False
    bot.position_manager = _PMStub(0)

    assert bot._get_cycle_sleep_sec() == 60


def test_get_cycle_sleep_default_in_signal_only():
    bot = TradingBot.__new__(TradingBot)
    bot.cycle_sleep = 60
    bot.position_active_sleep_sec = 15
    bot.signal_only = True
    bot.position_manager = _PMStub(3)

    assert bot._get_cycle_sleep_sec() == 60


def test_scan_interval_gate_works():
    bot = TradingBot.__new__(TradingBot)
    bot.scan_interval_sec = 5
    bot._last_scan_ts = 0.0

    assert bot._should_scan_entries_now() is True
    assert bot._should_scan_entries_now() is False
    bot._last_scan_ts = time.time() - 6
    assert bot._should_scan_entries_now() is True
