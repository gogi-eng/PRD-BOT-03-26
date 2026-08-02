"""Один reload config на пакет auto-tune правок."""
from __future__ import annotations

from pathlib import Path

from prd_agent.evolution.self_improver import SelfImprover


def test_process_proposals_reloads_config_once(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "quality_gate:\n  min_rr_ratio: 2.0\n"
        "trading:\n  min_signal_confidence: 0.7\n",
        encoding="utf-8",
    )
    reloads: list[int] = []
    imp = SelfImprover(
        {
            "_root": str(tmp_path),
            "self_improvement": {
                "enabled": True,
                "auto_apply_low_risk": True,
                "max_auto_applies_per_hour": 10,
            },
        },
        tmp_path,
        on_config_reload=lambda: reloads.append(1),
    )
    proposals = [
        {
            "risk": "low",
            "path": ["quality_gate", "min_rr_ratio"],
            "delta": -0.05,
            "summary": "tune rr",
        },
        {
            "risk": "low",
            "path": ["trading", "min_signal_confidence"],
            "delta": -0.02,
            "summary": "tune conf",
        },
    ]
    applied = imp.process_proposals(proposals)
    assert len(applied) == 2
    assert len(reloads) == 0
    assert imp.flush_reload() is True
    assert len(reloads) == 1

    reloads.clear()
    imp.process_proposals(proposals, reload=True)
    assert len(reloads) == 1


def test_process_proposals_rate_limits_one_per_hour(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "quality_gate:\n  min_rr_ratio: 2.0\n"
        "trading:\n  min_signal_confidence: 0.7\n",
        encoding="utf-8",
    )
    imp = SelfImprover(
        {
            "self_improvement": {
                "enabled": True,
                "auto_apply_low_risk": True,
                "max_auto_applies_per_hour": 1,
            },
        },
        tmp_path,
    )
    proposals = [
        {
            "risk": "low",
            "path": ["quality_gate", "min_rr_ratio"],
            "delta": -0.05,
            "summary": "tune rr",
        },
        {
            "risk": "low",
            "path": ["trading", "min_signal_confidence"],
            "delta": -0.02,
            "summary": "tune conf",
        },
    ]
    applied = imp.process_proposals(proposals)
    assert len(applied) == 1
    applied2 = imp.process_proposals(proposals)
    assert applied2 == []
