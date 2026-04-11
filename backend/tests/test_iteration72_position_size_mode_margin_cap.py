#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


risk_manager_module = _load_module("risk_manager_module_iter72", "bot/engine/risk_manager.py")
RiskGuard = risk_manager_module.RiskGuard


def test_margin_cap_mode_targets_expected_notional():
    guard = RiskGuard()
    balance = 66.0
    margin_pct = 10.0
    leverage = 10
    entry = 1.0

    qty = guard.calculate_position_size(
        balance=balance,
        risk_pct=1.0,
        entry=entry,
        stop_loss=0.95,
        leverage=leverage,
        capital_weight=1.0,
        margin_cap_pct=margin_pct,
        size_mode="margin_cap",
    )
    notional = qty * entry
    expected_notional = balance * (margin_pct / 100.0) * leverage
    assert abs(notional - expected_notional) < 1e-6


def test_hybrid_mode_is_not_less_than_risk_mode():
    guard = RiskGuard()
    kwargs = dict(
        balance=66.0,
        risk_pct=1.0,
        entry=100.0,
        stop_loss=99.0,
        leverage=10,
        capital_weight=1.0,
        margin_cap_pct=10.0,
    )
    qty_risk = guard.calculate_position_size(**kwargs, size_mode="risk_based")
    qty_hybrid = guard.calculate_position_size(**kwargs, size_mode="hybrid")
    assert qty_hybrid >= qty_risk

