"""Инварианты ×1.5 sizing + dynamic_leverage.min в deploy yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

from prd_agent.analysis.reject_journal import MIN_NOTIONAL_USDT
from prd_agent.risk.dynamic_leverage import load_dynamic_leverage_settings
from prd_agent.risk.guard import RiskGuard

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
    assert int(cfg["trading"]["dynamic_leverage"]["max"]) == 25
    assert float(cfg["supervisor_v4"]["min_risk_pct"]) == 0.15
    assert float(cfg["telegram_signal_agent"]["max_notional_balance_pct"]) == 45.0
    presets = cfg["risk_presets"]
    assert float(presets["conservative"]["trading"]["risk_pct_per_trade"]) == 0.375
    assert float(presets["normal"]["trading"]["risk_pct_per_trade"]) == 0.525
    assert float(presets["aggressive"]["trading"]["risk_pct_per_trade"]) == 0.675


def test_agent_world_git_config_leverage_max_25() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert int(cfg["trading"]["dynamic_leverage"]["max"]) == 25
    settings = load_dynamic_leverage_settings(cfg)
    assert settings.max_leverage == 25


def test_leverage_25_is_ceiling_not_skip_of_sl_or_min_notional() -> None:
    """25x — потолок заявки. Широкий SL и номинал 5 USDT остаются как были."""
    cfg = _load("config.agent_world_sandbox.yaml")
    settings = load_dynamic_leverage_settings(cfg)
    assert settings.max_leverage == 25
    # SL 2.5% -> int(0.65 / (0.025 + 0.005)) = 21x, ниже потолка 25
    assert RiskGuard.max_safe_leverage_for_sl(100.0, 97.5) == 21
    assert MIN_NOTIONAL_USDT == 5.0


def test_self_improver_risk_floor_protects_baseline() -> None:
    from prd_agent.evolution.self_improver import LOW_RISK_TUNING

    lo, hi, step = LOW_RISK_TUNING[("trading", "risk_pct_per_trade")]
    assert lo == 0.225
    assert hi == 1.5
    assert step == 0.05


def test_agent_world_keeps_garch_trailing_and_long_quality() -> None:
    """Регресс: ветка AW не должна терять Trailing GARCH / Long Quality / manual_sl_guard."""
    cfg = _load("config.agent_world_sandbox.yaml")
    assert cfg["volatility_regime_sizing"]["enabled"] is True
    tvr = cfg["positions"]["trailing_volatility_regime"]
    assert tvr["enabled"] is True
    assert float(tvr["distance_mult"]["calm"]) == 0.75
    assert float(tvr["distance_mult"]["storm"]) == 1.35
    assert cfg["trading"]["long_quality_gate"]["enabled"] is True
    assert cfg["trading"]["long_swing_exit"]["enabled"] is True
    assert cfg["positions"]["manual_sl_guard"]["enabled"] is True
