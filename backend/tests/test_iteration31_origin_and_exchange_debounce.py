#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import main as main_module
from main import TradingBot


def test_save_trade_persists_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
    bot = TradingBot.__new__(TradingBot)
    bot._save_trade(
        symbol="RDNTUSDT",
        side="BUY",
        qty=10,
        entry=0.123,
        exit_price=0.126,
        pnl=0.03,
        reason="tp",
        origin="manual",
    )

    history_path = tmp_path / "trade_history.json"
    assert history_path.exists()
    rows = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["origin"] == "manual"


def test_exchange_closed_needs_multiple_missing_cycles():
    bot = TradingBot.__new__(TradingBot)
    bot.exchange_closed_confirm_cycles = 3
    bot._missing_exchange_cycles = {}

    assert bot._should_finalize_exchange_closed("RDNTUSDT") is False
    assert bot._should_finalize_exchange_closed("RDNTUSDT") is False
    assert bot._should_finalize_exchange_closed("RDNTUSDT") is True


def test_exchange_closed_finalize_requires_closed_pnl_or_force_cycles():
    bot = TradingBot.__new__(TradingBot)
    bot.exchange_closed_require_closed_pnl = True
    bot.exchange_closed_force_cycles = 8

    assert bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=0) is False
    assert bot._can_finalize_exchange_closed(missing_cycles=3, closed_records_count=1) is True
    assert bot._can_finalize_exchange_closed(missing_cycles=8, closed_records_count=0) is True
