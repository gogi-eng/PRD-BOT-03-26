"""Тесты kill-switch и spread guard."""
from __future__ import annotations

import tempfile
from pathlib import Path

from prd_agent.entry.entry_engine_bridge import _check_spread_guard
from prd_agent.risk.guard import RiskGuard
from prd_agent.risk.kill_switch import kill_switch_active
from analysis.orderflow_analyzer import OrderflowSnapshot


def test_kill_switch_inactive_without_file():
    blocked, reason = kill_switch_active({"risk": {}})
    assert not blocked and reason == ""


def test_kill_switch_blocks_when_file_exists():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        flag = root / "data" / "kill_switch" / "STOP_TRADING"
        flag.parent.mkdir(parents=True)
        flag.touch()
        cfg = {
            "_config_path": str(root / "config.yaml"),
            "risk": {"kill_switch_file": "data/kill_switch/STOP_TRADING"},
        }
        blocked, reason = kill_switch_active(cfg)
        assert blocked
        assert "kill-switch" in reason


def test_risk_guard_can_trade_respects_kill_switch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        flag = root / "STOP_TRADING"
        flag.touch()
        cfg = {
            "_config_path": str(root / "config.yaml"),
            "risk": {"kill_switch_file": "STOP_TRADING"},
            "trading": {"max_positions": 3},
        }
        guard = RiskGuard(cfg)
        ok, reason = guard.can_trade()
        assert not ok
        assert "kill-switch" in reason


def test_spread_guard_blocks_wide_spread():
    snap = OrderflowSnapshot(
        bid_volume=100.0,
        ask_volume=100.0,
        normalized_imbalance=0.1,
        spread_pct=0.25,
    )
    cfg = {"orderbook_entry": {"max_spread_pct": 0.12}}
    reason = _check_spread_guard(snap, cfg)
    assert "spread_guard" in reason


def test_spread_guard_allows_tight_spread():
    snap = OrderflowSnapshot(
        bid_volume=100.0,
        ask_volume=80.0,
        normalized_imbalance=0.11,
        spread_pct=0.05,
    )
    cfg = {"orderbook_entry": {"max_spread_pct": 0.12}}
    assert _check_spread_guard(snap, cfg) == ""
