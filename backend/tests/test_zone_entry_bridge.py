"""Zone entry bridge: зоны, BOS, ретест, forced_side EntryEngine."""
from __future__ import annotations

from prd_agent.entry.entry_engine_bridge import (
    EntryEngineBridge,
    compute_zone_entry_price,
    should_apply_zone_entry,
)
from prd_agent.entry.impulse_retest import check_impulse_retest_confirmation
from prd_agent.signals.types import UnifiedSignal
from analysis.structure_zones import StructureZone, ZoneContext
from analysis.market_structure import BOSEvent, MarketStructure, StructureTrend


def _cfg() -> dict:
    return {
        "entry": {
            "entry_threshold": 0.55,
            "min_rr_ratio": 2.0,
            "zone_proximity_pct": 0.4,
            "impulse_min_body_atr": 0.45,
            "retest_max_body_ratio": 0.85,
        },
        "zone_entry": {
            "enabled": True,
            "prefer_bos_retest": True,
            "entry_at_zone": "edge",
            "require_entry_engine_pass": False,
            "block_if_no_zone": False,
        },
    }


def _klines_trend_up(n: int = 80, base: float = 100.0) -> list:
    rows = []
    for i in range(n):
        o = base + i * 0.08
        c = o + 0.12
        rows.append(
            {
                "open": o,
                "high": c + 0.05,
                "low": o - 0.03,
                "close": c,
                "volume": 1000 + i * 5,
            }
        )
    return rows


def test_should_apply_zone_entry_own_agent():
    sig = UnifiedSignal(symbol="BTCUSDT", side="Buy", confidence=0.9, source="own_multi_agent")
    assert should_apply_zone_entry(sig, _cfg()) is True


def test_should_skip_telegram_with_preset_entry():
    sig = UnifiedSignal(
        symbol="ETHUSDT",
        side="Buy",
        confidence=0.9,
        source="telegram",
        entry=2500.0,
    )
    assert should_apply_zone_entry(sig, _cfg()) is False


def test_compute_zone_entry_bos_retest_long():
    zone_ctx = ZoneContext(None, None, None, None, [], [])
    bos = BOSEvent(direction="up", broken_level=100.0, break_index=50, volume_confirmed=True)
    structure = MarketStructure(
        trend=StructureTrend.UP,
        swing_highs=[],
        swing_lows=[],
        last_bos=bos,
        last_sweep=None,
        volume_spike=False,
        spread_expansion=False,
        momentum_confirmed=False,
    )
    entry, mode = compute_zone_entry_price(
        side="Buy",
        market_price=101.5,
        zone_context=zone_ctx,
        structure=structure,
        atr_value=1.0,
        cfg=_cfg(),
    )
    assert mode == "bos_retest_long"
    assert entry <= 101.5
    assert entry >= 100.0


def test_compute_zone_entry_bullish_zone_edge():
    zone = StructureZone("ob", "bullish", 98.0, 100.0, 0.8, 10, False)
    zone_ctx = ZoneContext(
        zone, None, zone, None, [98.0], [], [zone], []
    )
    entry, mode = compute_zone_entry_price(
        side="Buy",
        market_price=102.0,
        zone_context=zone_ctx,
        structure=None,
        atr_value=1.0,
        cfg=_cfg(),
    )
    assert entry == 100.0
    assert "zone_edge" in mode


def test_impulse_retest_blocks_without_pattern():
    cfg = _cfg()
    klines = [
        {"open": 100, "high": 100.2, "low": 99.8, "close": 100.1},
        {"open": 100.1, "high": 100.3, "low": 99.9, "close": 100.0},
        {"open": 100.0, "high": 100.1, "low": 99.7, "close": 99.8},
    ]
    ok, reason = check_impulse_retest_confirmation(
        side="Buy",
        klines=klines,
        atr_value=1.0,
        confidence=0.7,
        cfg=cfg,
    )
    assert not ok
    assert "impulse_retest" in reason


def test_bridge_plan_levels_returns_structural_sl_tp():
    bridge = EntryEngineBridge(_cfg())
    sig = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.86,
        source="own_multi_agent",
        entry=0,
        reason="test",
    )
    plan = bridge.plan_levels(
        sig,
        klines=_klines_trend_up(),
        htf_klines=_klines_trend_up(60, 99.0),
        market_price=float(_klines_trend_up()[-1]["close"]),
    )
    assert plan.ok
    assert plan.entry > 0
    assert plan.stop_loss > 0
    assert plan.take_profit > plan.entry
