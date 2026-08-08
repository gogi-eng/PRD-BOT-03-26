#!/usr/bin/env python3
"""Тесты trade_companion (без сети)."""
from __future__ import annotations

from prd_agent.positions.trade_companion import (
    TradeCompanionConfig,
    evaluate_companion_actions,
    progress_to_tp_pct,
    tp_extension_improves,
    trend_confirms,
    trend_prior_confirmed,
    trend_reversal_against,
    trend_reversal_flip,
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


def _klines_up_then_down(
    up_n: int = 50,
    down_n: int = 10,
    start: float = 100.0,
    up_step: float = 0.5,
    down_step: float = 2.0,
):
    """Сначала бычий импульс (prior confirm), затем резкий разворот вниз."""
    out = _klines_uptrend(n=up_n, start=start, step=up_step)
    price = float(out[-1]["close"])
    for _ in range(down_n):
        price -= down_step
        out.append(
            {
                "open": price,
                "high": price + 0.2,
                "low": price - 0.3,
                "close": price,
                "volume": 10,
            }
        )
    return out


def test_progress_to_tp_pct_long():
    pct = progress_to_tp_pct("Buy", entry=100.0, price=106.0, take_profit=110.0)
    assert 55 < pct < 65


def test_trend_confirms_long_uptrend():
    assert trend_confirms("Buy", _klines_uptrend())


def test_trend_reversal_against_long():
    assert trend_reversal_against("Buy", _klines_downtrend())


def test_trend_reversal_flip_requires_prior_confirm():
    # Чисто медвежий ряд: против Buy, но prior не подтверждал Long → не flip
    assert trend_reversal_against("Buy", _klines_downtrend())
    assert not trend_prior_confirmed("Buy", _klines_downtrend())
    assert not trend_reversal_flip("Buy", _klines_downtrend())
    # Было вверх, стало вниз → flip
    flip_kl = _klines_up_then_down()
    assert trend_prior_confirmed("Buy", flip_kl)
    assert trend_reversal_against("Buy", flip_kl)
    assert trend_reversal_flip("Buy", flip_kl)


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
        close_reversal_require_prior_trend=True,
        close_reversal_min_hold_sec=0,
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
        klines=_klines_up_then_down(),
        sr_params={},
        position_age_sec=600,
    )
    assert decision is not None
    assert decision.action == "close"
    assert "разворот" in decision.reason


def test_no_close_lingering_bearish_sma_like_bless():
    """BLESS-кейс: SPIKE Long при ещё медвежьем SMA — не закрывать как разворот."""
    cfg = TradeCompanionConfig(
        enabled=True,
        close_giveback_enabled=False,
        close_reversal_enabled=True,
        close_reversal_min_profit_pct=0.8,
        close_reversal_max_loss_pct=-3.5,
        close_reversal_require_prior_trend=True,
        close_reversal_min_hold_sec=300,
        extend_tp_enabled=False,
        tighten_sl_on_weakness=False,
    )
    # Убыток -1.67% как на проде, но prior Long не подтверждался
    decision = evaluate_companion_actions(
        cfg=cfg,
        side="Buy",
        entry=100.0,
        price=98.33,
        take_profit=110.0,
        stop_loss=95.0,
        peak_profit_pct=0.5,
        klines=_klines_downtrend(),
        sr_params={},
        position_age_sec=400,
    )
    assert decision is None


def test_no_close_reversal_before_min_hold():
    cfg = TradeCompanionConfig(
        enabled=True,
        close_giveback_enabled=False,
        close_reversal_enabled=True,
        close_reversal_min_profit_pct=0.5,
        close_reversal_require_prior_trend=True,
        close_reversal_min_hold_sec=300,
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
        klines=_klines_up_then_down(),
        sr_params={},
        position_age_sec=60,
    )
    assert decision is None


def test_no_close_shallow_loss_until_deeper_threshold():
    cfg = TradeCompanionConfig(
        enabled=True,
        close_giveback_enabled=False,
        close_reversal_enabled=True,
        close_reversal_min_profit_pct=0.8,
        close_reversal_max_loss_pct=-3.5,
        close_reversal_require_prior_trend=True,
        close_reversal_min_hold_sec=0,
        extend_tp_enabled=False,
        tighten_sl_on_weakness=False,
    )
    # True flip, но убыток только -1.67% — раньше закрывало, теперь ждём глубже / SL
    decision = evaluate_companion_actions(
        cfg=cfg,
        side="Buy",
        entry=100.0,
        price=98.33,
        take_profit=110.0,
        stop_loss=95.0,
        peak_profit_pct=1.0,
        klines=_klines_up_then_down(),
        sr_params={},
        position_age_sec=600,
    )
    assert decision is None
