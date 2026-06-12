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
    cfg = {"trading": {"strategies": {"scalp_hours_utc": [14, 15, 16]}}}
    p = resolve_active_strategy(cfg, utc_hour=15)
    assert p.name == "scalp"
    p2 = resolve_active_strategy(cfg, utc_hour=3)
    assert p2.name == "swing"


def test_router_refresh():
    cfg = {"trading": {"active_strategy": "swing"}}
    r = StrategyRouter(cfg)
    assert r.profile.name == "swing"
    cfg["trading"]["active_strategy"] = "scalp"
    r.cfg = cfg
    r.refresh()
    assert r.profile.name == "scalp"
