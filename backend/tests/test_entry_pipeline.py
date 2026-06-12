"""Тесты entry_pipeline scoring modes."""
from __future__ import annotations

from prd_agent.entry.entry_pipeline import evaluate_entry_pipeline, resolve_pipeline_mode
from prd_agent.signals.types import UnifiedSignal


def _sig(conf: float = 0.85) -> UnifiedSignal:
    return UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=conf,
        source="own_multi_agent",
        entry=100.0,
        stop_loss=98.0,
        take_profit=104.0,
        reason="test",
    )


def test_strict_blocks_weak_structure():
    cfg = {
        "entry_pipeline": {"enabled": True, "mode": "strict"},
        "quality_gate": {"min_rr_ratio": 2.0},
    }
    res = evaluate_entry_pipeline(
        _sig(0.72),
        cfg,
        entry=100,
        sl=99,
        tp=100.5,
        has_zone=False,
        has_bos=False,
        supervisor_ok=True,
        atr_pct=0.002,
    )
    assert res.passed is False
    assert "entry_pipeline" in res.reason


def test_aggressive_passes_with_size_mult():
    cfg = {
        "entry_pipeline": {"enabled": True, "mode": "aggressive"},
        "quality_gate": {"min_rr_ratio": 2.0},
    }
    res = evaluate_entry_pipeline(
        _sig(0.82),
        cfg,
        entry=100,
        sl=98,
        tp=104,
        has_zone=True,
        has_bos=False,
        supervisor_ok=True,
        atr_pct=0.004,
    )
    assert res.passed is True
    assert res.size_mult == 0.5


def test_preset_maps_to_mode():
    cfg = {"risk_presets_meta": {"active": "conservative"}, "entry_pipeline": {"enabled": True}}
    assert resolve_pipeline_mode(cfg) == "strict"
    cfg["risk_presets_meta"]["active"] = "aggressive"
    assert resolve_pipeline_mode(cfg) == "aggressive"
