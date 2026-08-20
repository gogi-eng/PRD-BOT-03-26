"""Тесты Long Quality Gate — фильтр Buy по часам/волатильности/ATR."""
from __future__ import annotations

from datetime import datetime, timezone

from prd_agent.entry.entry_soft_rules import detect_active_rules
from prd_agent.entry.long_quality_gate import (
    evaluate_long_quality_gate,
    widen_buy_sl_to_min_pct,
)


def _cfg(**gate_overrides):
    gate = {
        "enabled": True,
        "block_local_hours": [3, 4, 5, 10, 20],
        "preferred_local_hours": [6, 9, 12, 13, 14, 16, 17, 18, 19, 21],
        "block_volatility": ["low"],
        "min_atr_pct": 0.40,
        "min_soft_score": 55,
        "block_soft_labels": ["weak", "caution"],
        "require_htf_align": False,
    }
    gate.update(gate_overrides)
    return {"timezone_offset": 3, "trading": {"long_quality_gate": gate}}


def test_sell_always_allowed():
    r = evaluate_long_quality_gate(
        side="Sell",
        cfg=_cfg(),
        local_hour=5,
        volatility="low",
        atr_pct=0.1,
        soft_score=10,
        soft_label="weak",
    )
    assert r.allowed is True


def test_buy_blocked_bad_hour():
    r = evaluate_long_quality_gate(
        side="Buy",
        cfg=_cfg(),
        local_hour=5,
        volatility="normal",
        atr_pct=0.8,
        soft_score=80,
        soft_label="favorable",
    )
    assert r.allowed is False
    assert "hour 5" in r.reason


def test_buy_ok_green_hour():
    r = evaluate_long_quality_gate(
        side="Buy",
        cfg=_cfg(),
        local_hour=16,
        volatility="normal",
        atr_pct=0.8,
        soft_score=80,
        soft_label="favorable",
    )
    assert r.allowed is True
    assert r.profile == "swing"
    assert "Long quality" in r.reason or "long_quality" in r.reason


def test_buy_blocked_low_vol():
    r = evaluate_long_quality_gate(
        side="Buy",
        cfg=_cfg(),
        local_hour=16,
        volatility="low",
        atr_pct=0.8,
        soft_score=80,
        soft_label="favorable",
    )
    assert r.allowed is False
    assert "volatility=low" in r.reason


def test_buy_blocked_soft_label():
    r = evaluate_long_quality_gate(
        side="Buy",
        cfg=_cfg(),
        local_hour=16,
        volatility="normal",
        atr_pct=0.8,
        soft_score=80,
        soft_label="caution",
    )
    assert r.allowed is False


def test_widen_buy_sl():
    new_sl, changed = widen_buy_sl_to_min_pct(
        side="Buy", entry=100.0, stop_loss=99.7, min_sl_pct=1.0
    )
    assert changed is True
    assert abs(new_sl - 99.0) < 1e-9
    same, ch2 = widen_buy_sl_to_min_pct(
        side="Buy", entry=100.0, stop_loss=98.5, min_sl_pct=1.0
    )
    assert ch2 is False
    assert same == 98.5


def test_soft_hours_side_aware_buy():
    # 14 — раньше был hour_red для всех; для Buy теперь green
    rules = detect_active_rules({"local_hour": 14, "htf_trend": "1"}, side="Buy")
    assert "hour_green" in rules
    assert "hour_red" not in rules
    assert "htf_aligned" in rules


def test_soft_hours_side_aware_sell_at_buy_bad_hour():
    rules = detect_active_rules({"local_hour": 5, "htf_trend": "-1"}, side="Sell")
    assert "hour_green" in rules
    assert "htf_aligned" in rules


def test_disabled_gate_passes():
    cfg = {"trading": {"long_quality_gate": {"enabled": False}}}
    r = evaluate_long_quality_gate(side="Buy", cfg=cfg, local_hour=5)
    assert r.allowed is True
    assert r.profile == "disabled"
