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


risk_manager_module = _load_module("risk_manager_module_iter73", "bot/engine/risk_manager.py")
RiskGuard = risk_manager_module.RiskGuard


def test_calculate_position_size_accepts_legacy_mode_kwarg():
    guard = RiskGuard()
    qty = guard.calculate_position_size(
        balance=66.0,
        risk_pct=1.0,
        entry=1.0,
        stop_loss=0.9,
        leverage=10,
        capital_weight=1.0,
        margin_cap_pct=10.0,
        mode="margin_cap",  # legacy alias from caller
    )
    assert qty > 0
    assert abs(qty - 66.0) < 1e-6


def test_config_has_manual_trailing_min_distance_pct_key():
    cfg = (ROOT / "bot" / "config.yaml").read_text(encoding="utf-8")
    assert "manual_management:" in cfg
    assert "trailing_min_distance_pct:" in cfg

