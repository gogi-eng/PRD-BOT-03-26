"""Тесты StrategyRouter: scalp vs swing."""
from __future__ import annotations

from prd_agent.strategies.router import StrategyRouter, resolve_active_strategy


def test_explicit_swing():
    cfg = {"trading": {"active_strategy": "swing"}}
    p = resolve_active_strategy(cfg)
    assert p.name == "swing"
    assert p.require_htf is True


def test_explicit_scalp():
    cfg = {"trading": {"active_strategy": "scalp"}}
    p = resolve_active_strategy(cfg)
    assert p.name == "scalp"
    assert p.require_htf is False
    assert p.zone_entry_enabled is False


def test_hour_based_scalp():
    cfg = {
        "timezone_offset": 3,
        "trading": {"strategies": {"scalp_hours_local": [14, 15, 16]}},
    }
    # UTC 12 = местное 15 → scalp
    p = resolve_active_strategy(cfg, utc_hour=12)
    assert p.name == "scalp"
    p2 = resolve_active_strategy(cfg, utc_hour=0)
    assert p2.name == "swing"


def test_hour_based_scalp_legacy_utc_list_with_offset():
    """scalp_hours_utc при timezone_offset трактуется как местные часы (обратная совместимость)."""
    cfg = {
        "timezone_offset": 3,
        "trading": {"strategies": {"scalp_hours_utc": [15]}},
    }
    assert resolve_active_strategy(cfg, utc_hour=12).name == "scalp"
    assert resolve_active_strategy(cfg, utc_hour=11).name == "swing"


def test_router_refresh():
    cfg = {"trading": {"active_strategy": "swing"}}
    r = StrategyRouter(cfg)
    assert r.profile.name == "swing"
    cfg["trading"]["active_strategy"] = "scalp"
    r.cfg = cfg
    r.refresh()
    assert r.profile.name == "scalp"
