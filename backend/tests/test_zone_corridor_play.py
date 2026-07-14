#!/usr/bin/env python3
"""Тесты zone_corridor_play: bounce / breakout / mid_range."""
from __future__ import annotations

from analysis.structure_zones import StructureZone, ZoneContext
from prd_agent.entry.zone_corridor_play import evaluate_zone_corridor_play


def _bar(o: float, h: float, lo: float, c: float) -> dict:
    return {"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}


def _zone_ctx(support: float, resistance: float) -> ZoneContext:
    bull = StructureZone("ob", "bullish", support - 1, support + 0.5, 0.8, 10, False)
    bear = StructureZone("ob", "bearish", resistance - 0.5, resistance + 1, 0.8, 20, False)
    return ZoneContext(
        bullish_fvg=None,
        bearish_fvg=None,
        bullish_ob=bull,
        bearish_ob=bear,
        support_levels=[support],
        resistance_levels=[resistance],
        all_bullish_zones=[bull],
        all_bearish_zones=[bear],
    )


_CFG_ON = {
    "trading": {
        "zone_corridor_play": {
            "enabled": True,
            "require_play": True,
            "allow_bounce": True,
            "allow_breakout": True,
            "mid_range_skip": True,
            "edge_fraction": 0.28,
            "bounce_lookback_bars": 3,
            "bounce_wick_atr_mult": 0.35,
            "breakout_confirm_bars": 2,
            "skip_fast_sources": True,
            "apply_to_sources": ["own_multi_agent", "ta_volatility", "hybrid"],
        }
    }
}


def test_disabled_allows():
    r = evaluate_zone_corridor_play(
        side="BUY",
        price=100.0,
        klines=[],
        cfg={"trading": {"zone_corridor_play": {"enabled": False}}},
        source="own_multi_agent",
    )
    assert r.allowed and r.play == "disabled"


def test_mid_range_blocks():
    # S=90 R=110 → mid ~100
    zc = _zone_ctx(90.0, 110.0)
    bars = [_bar(99, 101, 98, 100) for _ in range(8)]
    r = evaluate_zone_corridor_play(
        side="BUY",
        price=100.0,
        klines=bars,
        cfg=_CFG_ON,
        source="own_multi_agent",
        zone_ctx=zc,
        atr=1.0,
    )
    assert r.play == "mid_range"
    assert r.allowed is False


def test_bounce_buy_from_support():
    zc = _zone_ctx(100.0, 120.0)
    # касание поддержки + бычье закрытие
    bars = [
        _bar(103, 104, 102, 103),
        _bar(103, 103.5, 99.5, 102.5),
        _bar(102.5, 104, 100.2, 103.2),
    ]
    r = evaluate_zone_corridor_play(
        side="BUY",
        price=103.0,
        klines=bars,
        cfg=_CFG_ON,
        source="own_multi_agent",
        zone_ctx=zc,
        atr=1.5,
    )
    assert r.play == "bounce"
    assert r.allowed is True
    assert r.score_bonus >= 0.5


def test_breakout_buy_with_bos():
    zc = _zone_ctx(100.0, 110.0)
    bars = [
        _bar(109, 111, 108, 110.5),
        _bar(110.5, 112, 110.2, 111.5),
    ]
    r = evaluate_zone_corridor_play(
        side="BUY",
        price=111.5,
        klines=bars,
        cfg=_CFG_ON,
        source="own_multi_agent",
        zone_ctx=zc,
        has_bos=True,
        atr=1.0,
    )
    assert r.play == "breakout"
    assert r.allowed is True


def test_spike_source_not_gated():
    zc = _zone_ctx(90.0, 110.0)
    bars = [_bar(99, 101, 98, 100) for _ in range(5)]
    r = evaluate_zone_corridor_play(
        side="BUY",
        price=100.0,
        klines=bars,
        cfg=_CFG_ON,
        source="spike_scalp",
        zone_ctx=zc,
    )
    assert r.allowed is True
    assert r.play == "disabled"


def test_pipeline_zone_play_bonus():
    from prd_agent.entry.entry_pipeline import evaluate_entry_pipeline
    from prd_agent.signals.types import UnifiedSignal

    sig = UnifiedSignal(
        symbol="BTCUSDT",
        side="BUY",
        confidence=0.93,
        source="own_multi_agent",
        entry=100.0,
        reason="test",
    )
    cfg = {"entry_pipeline": {"enabled": True, "mode": "balanced"}}
    base = evaluate_entry_pipeline(sig, cfg, entry=100, sl=98, tp=106, has_zone=True)
    with_play = evaluate_entry_pipeline(
        sig,
        cfg,
        entry=100,
        sl=98,
        tp=106,
        has_zone=True,
        zone_play="bounce",
        zone_play_bonus=0.75,
    )
    assert with_play.score > base.score
    assert with_play.breakdown.get("zone_play", 0) >= 0.75
