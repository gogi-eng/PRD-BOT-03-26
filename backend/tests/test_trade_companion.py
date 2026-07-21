#!/usr/bin/env python3
"""Тесты trade_companion (без сети)."""
from __future__ import annotations

from prd_agent.positions.trade_companion import (
    CompanionDecision,
    TradeCompanionConfig,
    evaluate_companion_actions,
    progress_to_tp_pct,
    tp_extension_improves,
    trend_confirms,
    trend_reversal_against,
)


def _klines_uptrend(n: int = 40, start: float = 100.0, step: float = 0.5):
    out = []
    price = start
    for _ in range(n):
        out.append(
            {
                "open": price,
                "high": price + 0.3,
                "low": price - 0.2,
                "close": price,
                "volume": 10,
            }
        )
        price += step
    return out


def _klines_downtrend(n: int = 40, start: float = 130.0, step: float = 0.5):
    out = []
    price = start
    for _ in range(n):
        out.append(
            {
                "open": price,
                "high": price + 0.2,
                "low": price - 0.3,
                "close": price,
                "volume": 10,
            }
        )
        price -= step
    return out


def test_progress_to_tp_pct_long():
    pct = progress_to_tp_pct("Buy", entry=100.0, price=106.0, take_profit=110.0)
    assert 55 < pct < 65


def test_trend_confirms_long_uptrend():
    assert trend_confirms("Buy", _klines_uptrend())


def test_trend_reversal_against_long():
    assert trend_reversal_against("Buy", _klines_downtrend())


def test_tp_extension_improves_long():
    assert tp_extension_improves("Buy", 110.0, 115.0)
    assert not tp_extension_improves("Buy", 115.0, 110.0)


def test_close_on_giveback_from_peak():
    cfg = TradeCompanionConfig(
        enabled=True,
        close_giveback_enabled=True,
        close_giveback_peak_min_pct=2.0,
        close_giveback_from_peak_pct=40.0,
        extend_tp_enabled=False,
        close_reversal_enabled=False,
        tighten_sl_on_weakness=False,
    )
    decision = evaluate_companion_actions(
        cfg=cfg,
        side="Buy",
        entry=100.0,
        price=101.0,
        take_profit=110.0,
        stop_loss=98.0,
        peak_profit_pct=5.0,
        klines=_klines_uptrend(),
        sr_params={},
    )
    assert decision is not None
    assert decision.action == "close"
    assert "откат" in decision.reason


def test_close_on_reversal_with_small_profit():
    cfg = TradeCompanionConfig(
        enabled=True,
        close_giveback_enabled=False,
        close_reversal_enabled=True,
        close_reversal_min_profit_pct=0.5,
        extend_tp_enabled=False,
        tighten_sl_on_weakness=False,
    )
    decision = evaluate_companion_actions(
        cfg=cfg,
        side="Buy",
        entry=100.0,
        price=100.2,
        take_profit=110.0,
        stop_loss=98.0,
        peak_profit_pct=0.25,
        klines=_klines_downtrend(),
        sr_params={},
    )
    assert decision is not None
    assert decision.action == "close"
    assert "разворот" in decision.reason
