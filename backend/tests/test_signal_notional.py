#!/usr/bin/env python3
"""Тесты plan_signal_notional."""
from __future__ import annotations

from prd_agent.risk.signal_notional import plan_signal_notional


def test_fixed_max_notional_default_path():
    n, reason = plan_signal_notional(
        leverage=15,
        margin_usdt=3.0,
        max_notional_usdt=30.0,
        max_notional_balance_pct=0,
        wallet_balance=324.0,
        available_balance=324.0,
        reserve_pct=18,
    )
    assert abs(n - 30.0) < 1e-6
    assert "fixed" in reason


def test_balance_pct_80_of_wallet():
    n, reason = plan_signal_notional(
        leverage=15,
        margin_usdt=3.0,
        max_notional_usdt=30.0,
        max_notional_balance_pct=80,
        wallet_balance=324.0,
        available_balance=324.0,
        reserve_pct=18,
    )
    # 80% of 324 = 259.2; available after 18% reserve still allows it
    assert abs(n - 259.2) < 1e-6
    assert "balance_pct=80" in reason


def test_balance_pct_capped_by_available_reserve():
    n, reason = plan_signal_notional(
        leverage=15,
        margin_usdt=3.0,
        max_notional_usdt=30.0,
        max_notional_balance_pct=80,
        wallet_balance=324.0,
        available_balance=20.0,
        reserve_pct=18,
    )
    # usable = 20 * 0.82 = 16.4; max notional = 16.4 * 15 = 246
    assert abs(n - 246.0) < 1e-6
    assert "capped_by_available" in reason
