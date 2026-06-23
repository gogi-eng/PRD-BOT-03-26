"""Smoke-тесты профиля Hermes (confidence 0.92, RR 2.2, soft_score 75.5)."""
from __future__ import annotations

import yaml
from pathlib import Path

from prd_agent.entry.entry_pipeline import evaluate_entry_pipeline
from prd_agent.entry.entry_soft_rules import compute_soft_score
from prd_agent.risk.quality_gate import QualityGate
from prd_agent.signals.types import UnifiedSignal

ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(name: str) -> dict:
    path = ROOT / "deploy" / name
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


def _sig(**kw) -> UnifiedSignal:
    base = dict(
        symbol="ETHUSDT",
        side="Sell",
        confidence=0.93,
        source="telegram",
        entry=100.0,
        stop_loss=102.0,
        take_profit=95.6,
        reason="test",
        raw={},
    )
    base.update(kw)
    return UnifiedSignal(**base)


def test_sandbox_config_hermes_thresholds():
    cfg = _load_yaml("config.agent_world_sandbox.yaml")
    assert cfg["quality_gate"]["min_confidence"] == 0.92
    assert cfg["quality_gate"]["min_rr_ratio"] == 2.2
    assert cfg["rule_weight_learning"]["min_score_to_enter"] == 75.5
    assert cfg["entry_pipeline"]["min_confidence_high"] == 0.92
    assert cfg["entry_pipeline"]["min_atr_pct"] == 0.288


def test_production_config_hermes_thresholds():
    cfg = _load_yaml("config.production.yaml")
    assert cfg["quality_gate"]["min_confidence"] == 0.92
    assert cfg["quality_gate"]["min_rr_ratio"] == 2.2
    assert cfg["rule_weight_learning"]["min_score_to_enter"] == 75.5


def test_quality_gate_blocks_low_confidence():
    cfg = _load_yaml("config.production.yaml")
    gate = QualityGate(cfg)
    sig = _sig(confidence=0.90)
    import asyncio

    ok, reason = asyncio.run(
        gate.check(sig, exchange=None, entry=100.0, sl=102.0, tp=95.6)
    )
    assert not ok
    assert "confidence" in reason


def test_quality_gate_passes_hermes_winner():
    cfg = _load_yaml("config.production.yaml")
    gate = QualityGate(cfg)
    sig = _sig(confidence=0.93)
    import asyncio

    ok, reason = asyncio.run(
        gate.check(sig, exchange=None, entry=100.0, sl=102.0, tp=95.6)
    )
    assert ok, reason


def test_entry_pipeline_hermes_atr_scoring():
    cfg = _load_yaml("config.agent_world_sandbox.yaml")
    sig = _sig(confidence=0.93, raw={"regime": "trend"})
    res = evaluate_entry_pipeline(
        sig,
        cfg,
        entry=100.0,
        sl=102.0,
        tp=95.6,
        supervisor_ok=True,
        atr_pct=0.0051,
        market_regime="trend",
    )
    assert res.breakdown["volatility"] == 1.0
    assert res.breakdown["confidence"] == 2.0
    assert res.passed


def test_soft_score_hermes_median_context_passes():
    cfg = _load_yaml("config.agent_world_sandbox.yaml")
    ctx = {
        "local_hour": 10,
        "side": "SELL",
        "regime": "trend",
        "adx": 31.6,
        "atr_pct": 0.5089,
        "htf_trend": "bearish",
        "normalized_imbalance": -0.53,
        "volume_24h_usdt": 450_000_000,
    }
    soft = compute_soft_score(ctx, side="Sell", cfg=cfg)
    min_score = float(cfg["rule_weight_learning"]["min_score_to_enter"])
    assert soft.score >= min_score, f"score={soft.score} label={soft.label}"


def test_soft_score_weak_context_fails_gate():
    cfg = _load_yaml("config.agent_world_sandbox.yaml")
    ctx = {"local_hour": 3, "side": "BUY", "adx": 10.0, "atr_pct": 0.05}
    soft = compute_soft_score(ctx, side="Buy", cfg=cfg)
    min_score = float(cfg["rule_weight_learning"]["min_score_to_enter"])
    assert soft.score < min_score
