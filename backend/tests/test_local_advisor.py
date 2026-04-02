#!/usr/bin/env python3
"""Tests for local always-on advisor module."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "bot"))
from engine.entry_engine import EntrySignal

ADVISOR_PATH = Path(__file__).resolve().parents[2] / "bot" / "analysis" / "advisor.py"
spec = importlib.util.spec_from_file_location("advisor_module", ADVISOR_PATH)
advisor_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules["advisor_module"] = advisor_module
spec.loader.exec_module(advisor_module)
LocalTradingAdvisor = advisor_module.LocalTradingAdvisor


def _signal(**meta):
    return EntrySignal(
        should_enter=True,
        side=meta.pop("side", "BUY"),
        confidence=meta.pop("confidence", 0.8),
        rr_ratio=meta.pop("rr_ratio", 2.0),
        metadata=meta,
    )


def test_advisor_allows_strong_signal():
    adv = LocalTradingAdvisor(
        {
            "enabled": True,
            "mode": "enforce",
            "min_rr": 1.8,
            "min_confidence": 0.62,
            "min_edge": 0.45,
            "max_spread_pct": 0.12,
            "min_atr_pct": 0.03,
            "min_abs_imbalance": 0.06,
            "allow_countertrend": False,
            "allow_chop": True,
        }
    )
    sig = _signal(
        confidence=0.86,
        rr_ratio=2.1,
        spread_pct=0.04,
        atr_pct=0.42,
        normalized_imbalance=0.20,
        regime="trend",
        htf_4h_trend=1,
        entry_zone="scalp_session",
    )
    decision = adv.evaluate("BTCUSDT", sig)
    assert decision.allow is True
    assert decision.reason == "advisor_ok"
    assert decision.score >= 0.9


def test_advisor_blocks_weak_no_zone_countertrend():
    adv = LocalTradingAdvisor(
        {
            "enabled": True,
            "mode": "enforce",
            "min_rr": 1.8,
            "min_confidence": 0.62,
            "min_edge": 0.45,
            "allow_countertrend": False,
            "allow_chop": False,
        }
    )
    sig = _signal(
        side="BUY",
        confidence=0.55,
        rr_ratio=1.2,
        spread_pct=0.20,
        atr_pct=0.01,
        normalized_imbalance=0.01,
        regime="chop",
        htf_4h_trend=-1,
        entry_zone="no_zone",
    )
    decision = adv.evaluate("EDGEUSDT", sig)
    assert decision.allow is False
    assert decision.reason.startswith("advisor_blocked:")
    assert decision.checks["countertrend_ok"] is False
    assert decision.checks["regime_ok"] is False


def test_advisor_disabled_bypasses():
    adv = LocalTradingAdvisor({"enabled": False})
    sig = _signal()
    decision = adv.evaluate("SOLUSDT", sig)
    assert decision.allow is True
    assert decision.reason == "advisor_disabled"
