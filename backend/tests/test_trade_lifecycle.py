#!/usr/bin/env python3
"""Тесты trade_lifecycle (без сети)."""
from __future__ import annotations

from prd_agent.analysis.trade_lifecycle import (
    TradeLifecycleTracker,
    build_exit_context,
    serialize_orderflow,
    volume_gate_ratio,
)
from prd_agent.positions.position_steward import TrackedPosition


class _FakeOrderflow:
    normalized_imbalance = 0.25
    spread_pct = 0.02
    orderbook_ratio = 1.1
    trade_delta = 50.0
    volume_spike = 1.3
    dominant_side = "buy"
    bid_volume = 100.0
    ask_volume = 90.0
    buy_volume = 60.0
    sell_volume = 40.0


def test_build_exit_context_long_profit():
    ctx = build_exit_context(
        side="Buy",
        entry=100.0,
        exit_price=105.0,
        pnl_usdt=5.0,
        reason="tp",
        mfe_pct=6.0,
        mae_pct=-0.5,
        peak_profit_pct=6.0,
        hold_minutes=45.0,
        leverage=10,
        stop_loss=98.0,
        take_profit=110.0,
        sample_count=2,
    )
    assert ctx["pnl_pct"] == 5.0
    assert ctx["mfe_pct"] == 6.0
    assert ctx["leverage"] == 10
    assert ctx["rr_realized"] > 0
    assert ctx["lifecycle_samples"] == 2


def test_serialize_orderflow():
    data = serialize_orderflow(_FakeOrderflow())
    assert data["normalized_imbalance"] == 0.25
    assert data["dominant_side"] == "buy"


def test_volume_gate_ratio():
    assert volume_gate_ratio(20_000_000, 10_000_000) == 2.0


def test_lifecycle_update_and_pop():
    tracker = TradeLifecycleTracker.__new__(TradeLifecycleTracker)
    tracker._states = {}
    tracker._cfg = type("C", (), {"enabled": True, "bot_positions_only": False})()
    pos = TrackedPosition(
        symbol="BTCUSDT",
        side="Buy",
        entry=100.0,
        qty=0.01,
        opened_at_utc="2026-07-21T06:00:00+00:00",
        stop_loss=98.0,
        take_profit=110.0,
    )
    tracker._states["BTCUSDT:Buy"] = tracker._ensure_state(pos, leverage=10)
    tracker.update_mark_prices(
        [{"symbol": "BTCUSDT", "markPrice": 103.0}],
        {"BTCUSDT": pos},
    )
    st = tracker._states["BTCUSDT:Buy"]
    assert st.mfe_pct == 3.0
    ctx = tracker.pop_exit_context(
        "BTCUSDT",
        "Buy",
        exit_price=102.0,
        pnl_usdt=2.0,
        reason="test",
    )
    assert ctx is not None
    assert ctx["mfe_pct"] == 3.0
    assert "BTCUSDT:Buy" not in tracker._states
