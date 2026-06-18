"""Тесты SL/TP для MARKET SCANNER."""
from __future__ import annotations

from prd_agent.risk.rr_enforce import rr_ratio
from telegram_agent.market_scanner_levels import market_scanner_invalidation_and_target


def test_pump_sl_not_at_range_low_when_bos_tighter():
    """LONG: SL у BOS, не на дне всего диапазона (иначе риск >> TP)."""
    price = 0.6372
    range_low = 0.58
    range_high = 0.635
    bos_level = range_high
    inv, tgt = market_scanner_invalidation_and_target(
        scenario="PUMP",
        price=price,
        range_low=range_low,
        range_high=range_high,
        bos_level=bos_level,
        bos_buffer_pct=0.5,
        min_rr=2.0,
    )
    assert inv > range_low
    assert inv < price
    risk = price - inv
    reward = tgt - price
    assert reward >= risk * 2.0 - 1e-9


def test_virtual_like_geometry_reward_ge_risk():
    """Как VIRTUALUSDT: вход ~0.6372, SL не дальше чем TP по дистанции при min_rr=6."""
    price = 0.6372
    range_low = 0.60
    range_high = 0.632
    inv, tgt = market_scanner_invalidation_and_target(
        scenario="PUMP",
        price=price,
        range_low=range_low,
        range_high=range_high,
        bos_level=range_high,
        bos_buffer_pct=0.5,
        min_rr=6.0,
    )
    assert rr_ratio(price, inv, tgt, "Buy") >= 6.0 - 1e-6
    assert tgt - price > price - inv


def test_dump_symmetric_short():
    price = 91.0
    range_low = 92.0
    range_high = 98.0
    inv, tgt = market_scanner_invalidation_and_target(
        scenario="DUMP",
        price=price,
        range_low=range_low,
        range_high=range_high,
        bos_level=range_low,
        bos_buffer_pct=0.5,
        min_rr=2.0,
    )
    assert inv < range_high
    assert inv > price
    assert price - tgt >= (inv - price) * 2.0 - 1e-9
