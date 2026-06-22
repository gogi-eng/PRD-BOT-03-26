"""Тесты: Telegram панель, partial pass, liquidation guard."""
from __future__ import annotations

from pathlib import Path

import pytest

from prd_agent.entry.entry_pipeline import evaluate_entry_pipeline
from prd_agent.ops.runtime_controls import (
    is_signal_only_active,
    load_runtime_controls,
    save_runtime_controls,
    toggle_runtime_flag,
)
from prd_agent.positions.liquidation_guard import (
    LiquidationGuardConfig,
    distance_to_liq_pct,
    evaluate_liquidation_stop,
    protective_level,
)
from prd_agent.signals.types import UnifiedSignal
from prd_agent.telegram.panel_guide import build_panel_help_text


def test_partial_pass_reduces_size_instead_of_skip():
    cfg = {
        "entry_pipeline": {
            "enabled": True,
            "mode": "balanced",
            "partial_pass_band": 1.5,
            "partial_pass_size_mult": 0.35,
            "regime_thresholds": {"enabled": False},
        },
        "quality_gate": {"min_rr_ratio": 2.0},
    }
    sig = UnifiedSignal(symbol="BTCUSDT", side="Buy", confidence=0.71, source="own_multi_agent")
    res = evaluate_entry_pipeline(
        sig,
        cfg,
        entry=100.0,
        sl=99.5,
        tp=101.0,
        has_zone=False,
        has_bos=False,
        supervisor_ok=True,
        atr_pct=0.001,
        market_regime="chop",
    )
    assert res.passed is True
    assert res.size_mult < 1.0
    assert "partial" in res.reason


def test_liquidation_stop_long_before_exchange_liq():
    cfg = LiquidationGuardConfig(enabled=True, buffer_pct=1.0, skip_manual=False)
    liq = 90.0
    guard = protective_level(liq, "Buy", cfg.buffer_pct)
    assert guard > liq
    hit, reason = evaluate_liquidation_stop(
        side="Buy",
        mark_price=guard - 0.01,
        liq_price=liq,
        cfg=cfg,
        origin="bot",
    )
    assert hit is True
    assert "liquidation_stop" in reason


def test_liquidation_skips_manual_when_configured():
    cfg = LiquidationGuardConfig(skip_manual=True)
    hit, _ = evaluate_liquidation_stop(
        side="Buy",
        mark_price=50.0,
        liq_price=49.0,
        cfg=cfg,
        origin="manual",
    )
    assert hit is False


def test_distance_to_liq_pct_long():
    assert distance_to_liq_pct("Buy", 100.0, 95.0) == pytest.approx(5.0)


def test_signal_only_runtime_toggle(tmp_path):
    root = tmp_path
    save_runtime_controls(root, load_runtime_controls(root))
    toggle_runtime_flag(root, "signal_only_mode")
    assert is_signal_only_active({}, root) is True


def test_panel_help_contains_key_buttons():
    text = build_panel_help_text({"bot": {"signal_only": False}}, Path("."))
    assert "Лаборатория" in text
    assert "Signal-only" in text
