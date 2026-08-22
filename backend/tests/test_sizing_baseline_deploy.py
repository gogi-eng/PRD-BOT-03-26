"""Инварианты ×1.5 sizing + dynamic_leverage.min в deploy yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str) -> dict:
    path = ROOT / "deploy" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_production_sizing_baseline() -> None:
    cfg = _load("config.production.yaml")
    assert float(cfg["trading"]["risk_pct_per_trade"]) == 0.225
    assert int(cfg["trading"]["dynamic_leverage"]["min"]) == 10
    assert float(cfg["supervisor_v4"]["min_risk_pct"]) == 0.15
    assert float(cfg["telegram_signal_agent"]["max_notional_balance_pct"]) == 45.0
    presets = cfg["risk_presets"]
    assert float(presets["conservative"]["trading"]["risk_pct_per_trade"]) == 0.375
    assert float(presets["normal"]["trading"]["risk_pct_per_trade"]) == 0.525
    assert float(presets["aggressive"]["trading"]["risk_pct_per_trade"]) == 0.675


def test_agent_world_sizing_baseline() -> None:
    cfg = _load("config.agent_world_sandbox.yaml")
    assert float(cfg["trading"]["risk_pct_per_trade"]) == 0.225
    assert int(cfg["trading"]["dynamic_leverage"]["min"]) == 10
    assert float(cfg["supervisor_v4"]["min_risk_pct"]) == 0.15
    assert float(cfg["telegram_signal_agent"]["max_notional_balance_pct"]) == 45.0
    presets = cfg["risk_presets"]
    assert float(presets["conservative"]["trading"]["risk_pct_per_trade"]) == 0.375
    assert float(presets["normal"]["trading"]["risk_pct_per_trade"]) == 0.525
    assert float(presets["aggressive"]["trading"]["risk_pct_per_trade"]) == 0.675


def test_self_improver_risk_floor_protects_baseline() -> None:
    from prd_agent.evolution.self_improver import LOW_RISK_TUNING

    lo, hi, step = LOW_RISK_TUNING[("trading", "risk_pct_per_trade")]
    assert lo == 0.225
    assert hi == 1.5
    assert step == 0.05
