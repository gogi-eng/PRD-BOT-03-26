#!/usr/bin/env python3
"""Focused tests for polymarket_autobet core checks."""
from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))

from polymarket_autobet import (  # type: ignore
    BotConfig,
    ChecklistConfig,
    ExitConfig,
    PolymarketAutoBetBot,
    RiskConfig,
    WatchlistItem,
    iso,
    side_snapshot,
    utc_now,
)


def _bot(tmp_path):
    state = tmp_path / "state.json"
    log = tmp_path / "log.jsonl"
    cfg = BotConfig(
        dry_run=True,
        poll_interval_sec=5,
        state_path=str(state),
        log_path=str(log),
        watchlist=[WatchlistItem(slug="dummy", side="YES", entry_price_max=0.55)],
        risk=RiskConfig(
            bankroll_usdc=100.0,
            risk_per_bet_usdc=1.5,
            max_risk_per_bet_usdc=2.0,
            max_daily_loss_usdc=6.0,
            max_trades_per_day=4,
            max_open_positions=2,
            max_total_open_risk_usdc=3.0,
            max_notional_per_bet_usdc=20.0,
        ),
        checklist=ChecklistConfig(
            min_volume_24h=100000.0,
            min_liquidity=20000.0,
            max_spread=0.02,
            min_days_to_end=0.2,
            max_days_to_end=14.0,
            min_price=0.03,
            max_price=0.97,
        ),
        exits=ExitConfig(
            stop_loss_delta=0.05,
            take_profit_delta=0.08,
            reduce_half_after_hours=12.0,
            time_stop_hours=24.0,
        ),
    )
    return PolymarketAutoBetBot(config=cfg)


def _market(end_days=3.0, volume=250000.0, liquidity=400000.0, ask=0.45, bid=0.44):
    end_dt = utc_now() + timedelta(days=end_days)
    return {
        "slug": "dummy",
        "question": "Test market",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "endDate": iso(end_dt),
        "volume24hr": volume,
        "liquidityClob": liquidity,
        "bestBid": bid,
        "bestAsk": ask,
        "lastTradePrice": ask,
        "clobTokenIds": ["111", "222"],
    }


def test_entry_checks_and_position_build_pass(tmp_path):
    bot = _bot(tmp_path)
    market = _market()
    snap = side_snapshot(market, "YES")
    ok, reason = bot._entry_check(market, snap)
    assert ok is True
    assert reason == "ok"

    pos = bot._build_position(bot.config.watchlist[0], market, snap)
    assert pos is not None
    assert pos["entry_price"] <= bot.config.watchlist[0].entry_price_max
    assert pos["qty"] > 0
    assert pos["stop_price"] < pos["entry_price"]
    assert pos["tp_price"] > pos["entry_price"]


def test_entry_rejects_on_spread_and_volume(tmp_path):
    bot = _bot(tmp_path)

    wide_spread_market = _market(ask=0.50, bid=0.40)  # spread 0.10
    snap1 = side_snapshot(wide_spread_market, "YES")
    ok1, reason1 = bot._entry_check(wide_spread_market, snap1)
    assert ok1 is False
    assert reason1 == "spread_too_wide"

    low_volume_market = _market(volume=5000.0)
    snap2 = side_snapshot(low_volume_market, "YES")
    ok2, reason2 = bot._entry_check(low_volume_market, snap2)
    assert ok2 is False
    assert reason2 == "low_volume_24h"


def test_guard_rejects_limits(tmp_path):
    bot = _bot(tmp_path)

    # max open positions
    bot.state["open_positions"] = [{"risk_usdc": 1.0}, {"risk_usdc": 1.0}]
    can_open, reason = bot._can_open_new(1.0)
    assert can_open is False
    assert reason == "max_open_positions"

    # max trades/day
    bot.state["open_positions"] = []
    bot.state["day_trades"] = bot.config.risk.max_trades_per_day
    can_open, reason = bot._can_open_new(1.0)
    assert can_open is False
    assert reason == "max_trades_per_day"

    # max daily loss
    bot.state["day_trades"] = 0
    bot.state["day_realized_pnl"] = -7.0
    can_open, reason = bot._can_open_new(1.0)
    assert can_open is False
    assert reason == "max_daily_loss_reached"
