#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


def test_config_has_scalp_unblock_overrides():
    cfg = (Path(__file__).resolve().parents[2] / "bot" / "config.yaml").read_text(encoding="utf-8")
    assert "scalp:" in cfg
    assert "enabled: true" in cfg
    assert "hardgate_orderflow_relax_mult:" in cfg
    assert "impulse_retest_bypass_confidence:" in cfg
    assert "quality_gate_bypass_confidence:" in cfg

