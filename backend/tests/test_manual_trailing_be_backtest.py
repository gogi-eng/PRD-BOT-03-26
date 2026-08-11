"""Бэктест trailing/BE+ на ручных позициях: hold vs manage."""
from __future__ import annotations

from prd_agent.positions.manual_trailing_be_backtest import (
    ManualTrailBeParams,
    compare_hold_vs_manage,
    simulate_manual_trailing_be,
    summarize_comparisons,
)


def _klines_up_then_down():
    """Рост +5%, потом откат −3% от пика — трейлинг может закрыть раньше широкого SL."""
    base = 1_700_000_000_000
    out = []
    # 10 свечей вверх
    for i in range(10):
        c = 100.0 * (1.0 + 0.005 * i)
        out.append(
            {
                "startTime": base + i * 60_000,
                "open": c * 0.999,
                "high": c * 1.002,
                "low": c * 0.998,
                "close": c,
            }
        )
    # откат
    peak = out[-1]["close"]
    for j in range(8):
        c = peak * (1.0 - 0.004 * (j + 1))
        out.append(
            {
                "startTime": base + (10 + j) * 60_000,
                "open": c * 1.001,
                "high": c * 1.003,
                "low": c * 0.997,
                "close": c,
            }
        )
    return out


def test_hold_hits_wide_sl_or_stays_open():
    kl = _klines_up_then_down()
    # широкий SL −8%, TP +20% — на этом пути скорее still_open или далёкий SL
    r = simulate_manual_trailing_be(
        side="Buy",
        entry=100.0,
        stop_loss=92.0,
        take_profit=120.0,
        klines=kl,
        manage=False,
    )
    assert r.mode == "hold"
    assert r.outcome in ("still_open", "stop_loss", "take_profit")
    assert r.sl_updates == 0


def test_manage_moves_sl_and_can_exit_on_pullback():
    kl = _klines_up_then_down()
    params = ManualTrailBeParams(
        trailing_activation_pct=1.5,
        trailing_distance_pct=2.0,
        trailing_min_distance_pct=1.0,
        breakeven_after_pct=1.0,
        be_at_profit_pct=1.0,
        be_fee_buffer_pct=0.3,
        be_lock_extra_pct=1.0,
        trailing_after_be_enabled=True,
        trailing_after_be_reduce_pct=0.5,
    )
    r = simulate_manual_trailing_be(
        side="Buy",
        entry=100.0,
        stop_loss=92.0,
        take_profit=120.0,
        klines=kl,
        params=params,
        manage=True,
    )
    assert r.mode == "manage"
    assert r.sl_updates >= 1
    # После роста BE+/trail должен подтянуть SL выше 92
    assert r.final_sl > 92.0


def test_compare_hold_vs_manage_returns_delta():
    kl = _klines_up_then_down()
    cmp = compare_hold_vs_manage(
        side="Buy",
        entry=100.0,
        stop_loss=92.0,
        take_profit=120.0,
        klines=kl,
        params=ManualTrailBeParams(
            trailing_activation_pct=1.5,
            trailing_distance_pct=2.0,
            trailing_min_distance_pct=1.0,
            be_at_profit_pct=1.0,
            be_lock_extra_pct=1.0,
        ),
    )
    assert "hold" in cmp and "manage" in cmp
    assert "delta_pnl_pct" in cmp
    s = summarize_comparisons([cmp])
    assert s["n"] == 1


def test_apply_to_manual_false_forces_hold_behavior():
    kl = _klines_up_then_down()
    params = ManualTrailBeParams(apply_to_manual=False, trailing_activation_pct=1.0)
    r = simulate_manual_trailing_be(
        side="Buy",
        entry=100.0,
        stop_loss=92.0,
        take_profit=120.0,
        klines=kl,
        params=params,
        manage=True,
    )
    assert r.mode == "hold"
    assert r.sl_updates == 0


def test_sell_manage_tightens_sl_down():
    base = 1_700_000_000_000
    kl = []
    for i in range(12):
        c = 100.0 * (1.0 - 0.004 * i)  # цена вниз — прибыль для Sell
        kl.append(
            {
                "startTime": base + i * 60_000,
                "open": c * 1.001,
                "high": c * 1.002,
                "low": c * 0.997,
                "close": c,
            }
        )
    r = simulate_manual_trailing_be(
        side="Sell",
        entry=100.0,
        stop_loss=108.0,
        take_profit=85.0,
        klines=kl,
        params=ManualTrailBeParams(
            trailing_activation_pct=1.5,
            trailing_distance_pct=2.0,
            trailing_min_distance_pct=1.0,
            be_at_profit_pct=1.0,
            be_lock_extra_pct=1.0,
        ),
        manage=True,
    )
    assert r.sl_updates >= 1
    assert r.final_sl < 108.0
