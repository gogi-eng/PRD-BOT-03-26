"""Режим pump/dump: без pullback, быстрый трейлинг."""
from __future__ import annotations

from prd_agent.risk.pullback_entry import check_pullback_entry
from prd_agent.signals.pump_dump_mode import (
    TrailingProfile,
    is_pump_dump_signal,
)
from prd_agent.signals.types import UnifiedSignal


def test_is_pump_dump_from_source():
    sig = UnifiedSignal(
        symbol="XLMUSDT",
        side="Buy",
        confidence=0.9,
        source="mirror_pump_dump_agent",
    )
    assert is_pump_dump_signal(sig)


def test_pullback_skipped_for_pump_dump():
    sig = UnifiedSignal(
        symbol="XLMUSDT",
        side="Buy",
        confidence=0.9,
        source="mirror_pump_dump_agent",
    )
    klines = [{"close": 1.0 + i * 0.01} for i in range(10)]
    ok, reason = check_pullback_entry(
        sig,
        klines,
        {
            "pullback_entry": {"enabled": True, "momentum_bars": 5},
            "pump_dump_trade": {"enabled": True},
        },
    )
    assert ok is True
    assert "pump_dump" in reason


def test_pump_dump_trailing_profile_faster_activation():
    cfg = {
        "trailing_activation_pct": 1.35,
        "pump_dump_trailing": {
            "trailing_activation_pct": 0.42,
            "trailing_distance_pct": 0.52,
        },
    }
    normal = TrailingProfile.from_positions_cfg(cfg)
    fast = TrailingProfile.from_positions_cfg(cfg, subsection="pump_dump_trailing")
    assert fast.activation_pct < normal.activation_pct
    assert fast.distance_pct < normal.distance_pct
