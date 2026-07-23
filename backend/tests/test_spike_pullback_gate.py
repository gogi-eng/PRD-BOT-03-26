"""Тесты spike_pullback_gate: WAIT / ENTER_NOW / SKIP / ENTER after retest."""
from __future__ import annotations

from prd_agent.entry.spike_pullback_gate import (
    SpikePullbackAction,
    SpikePullbackConfig,
    decide_spike_pullback,
    evaluate_pending_pullback,
    find_latest_fvg,
    price_in_pullback_zone,
    read_spike_pullback_cfg,
    spike_pullback_enabled,
)


def _bar(o: float, h: float, lo: float, c: float, vol: float = 1000.0) -> dict:
    return {"open": o, "high": h, "low": lo, "close": c, "volume": vol}


def _book(bid_vol: float, ask_vol: float, mid: float = 100.0) -> dict:
    """Простой стакан: несколько уровней с суммарным объёмом."""
    n = 5
    bid_each = bid_vol / n
    ask_each = ask_vol / n
    return {
        "bids": [[mid - 0.01 * (i + 1), bid_each] for i in range(n)],
        "asks": [[mid + 0.01 * (i + 1), ask_each] for i in range(n)],
    }


def _cfg(**kwargs) -> SpikePullbackConfig:
    base = dict(
        enabled=True,
        wait_timeout_sec=300,
        min_retrace_pct=0.15,
        require_fvg=False,
        enter_immediate_if_no_fvg=True,
        enter_immediate_if_book_confirms=True,
        orderbook_depth=50,
        direction_guard_ratio=1.3,
        min_fvg_pct=0.12,
        impulse_extend_pct=0.25,
        absorption_ratio=1.2,
        synthetic_retrace_pct=0.20,
    )
    base.update(kwargs)
    return SpikePullbackConfig(**base)


def test_read_cfg_sandbox_style():
    cfg = {
        "market_scanner": {
            "spike_scalp": {
                "pullback_entry": {"enabled": True, "orderbook_depth": 40},
            }
        },
        "orderbook_entry": {"direction_guard_ratio": 1.5},
    }
    pe = read_spike_pullback_cfg(cfg)
    assert pe.enabled is True
    assert pe.orderbook_depth == 40
    assert spike_pullback_enabled(cfg) is True


def test_disabled_enters_now():
    d = decide_spike_pullback(
        side="BUY",
        price=100.0,
        cfg=SpikePullbackConfig(enabled=False),
    )
    assert d.action == SpikePullbackAction.ENTER_NOW


def test_wait_when_fvg_below_buy():
    # Bullish FVG: prev2.high=98, cur.low=99 → gap 98-99; price выше зоны.
    klines = [
        _bar(97, 98, 96.5, 97.5),
        _bar(97.5, 98.5, 97, 98),
        _bar(99.5, 101, 99, 100.5),
    ]
    # Дополним до lookback и импульс вверх на 1m без сильного отката.
    k1 = [_bar(95 + i * 0.1, 95.2 + i * 0.1, 94.9 + i * 0.1, 95.1 + i * 0.1) for i in range(10)]
    k1.extend(klines)
    d = decide_spike_pullback(
        side="BUY",
        price=100.5,
        orderbook=_book(100, 100),  # нейтральный
        klines_1m=k1,
        klines_15m=klines,
        fvg_low=98.0,
        fvg_high=99.0,
        cfg=_cfg(enter_immediate_if_book_confirms=False),
    )
    assert d.action == SpikePullbackAction.WAIT_PULLBACK
    assert d.has_fvg is True
    assert d.zone_low == 98.0
    assert d.zone_high == 99.0


def test_enter_now_when_no_fvg_and_book_ok():
    # Плоские свечи без FVG; книга подтверждает BUY.
    flats = [_bar(100, 100.2, 99.8, 100.0) for _ in range(12)]
    d = decide_spike_pullback(
        side="BUY",
        price=100.0,
        orderbook=_book(200, 100),  # bid >> ask
        klines_1m=flats,
        klines_15m=flats,
        cfg=_cfg(),
    )
    assert d.action == SpikePullbackAction.ENTER_NOW
    assert d.has_fvg is False


def test_skip_on_opposite_book():
    flats = [_bar(100, 100.2, 99.8, 100.0) for _ in range(12)]
    d = decide_spike_pullback(
        side="BUY",
        price=100.0,
        orderbook=_book(100, 200),  # ask >> bid против лонга
        klines_1m=flats,
        klines_15m=flats,
        cfg=_cfg(),
    )
    assert d.action == SpikePullbackAction.SKIP
    assert "opposite" in d.reason


def test_enter_after_retest_when_price_in_zone():
    d = evaluate_pending_pullback(
        side="BUY",
        price=98.5,
        zone_low=98.0,
        zone_high=99.0,
        orderbook=_book(120, 100),
        cfg=_cfg(),
        timed_out=False,
    )
    assert d.action == SpikePullbackAction.ENTER_AFTER_RETEST
    assert price_in_pullback_zone(98.5, 98.0, 99.0)


def test_skip_timeout_on_pending():
    d = evaluate_pending_pullback(
        side="BUY",
        price=105.0,
        zone_low=98.0,
        zone_high=99.0,
        cfg=_cfg(),
        timed_out=True,
    )
    assert d.action == SpikePullbackAction.SKIP
    assert d.reason == "timeout"


def test_find_latest_fvg_bullish():
    klines = [
        _bar(10, 11, 9.5, 10.5),
        _bar(10.5, 11.2, 10.2, 11.0),
        _bar(12.0, 12.5, 11.8, 12.2),  # low 11.8 > prev2 high 11 → FVG 11-11.8
    ]
    lo, hi, reason = find_latest_fvg(klines, "BUY", min_fvg_pct=0.1)
    assert lo == 11.0
    assert hi == 11.8
    assert "bullish" in reason


def test_continuation_enter_now_with_fvg_and_book():
    """Импульс 1m + книга → ENTER_NOW даже при FVG."""
    # Сильный рост на 1m
    k1 = [_bar(100 + i, 100.5 + i, 99.8 + i, 100.4 + i) for i in range(6)]
    d = decide_spike_pullback(
        side="BUY",
        price=105.0,
        orderbook=_book(300, 100),
        klines_1m=k1,
        fvg_low=101.0,
        fvg_high=102.0,
        cfg=_cfg(enter_immediate_if_book_confirms=True, impulse_extend_pct=0.2),
    )
    assert d.action == SpikePullbackAction.ENTER_NOW
    assert "continuation" in d.reason or "book" in d.reason.lower()


def test_production_flag_off_by_default_key():
    cfg = {
        "market_scanner": {
            "spike_scalp": {"pullback_entry": {"enabled": False}},
        }
    }
    assert spike_pullback_enabled(cfg) is False
