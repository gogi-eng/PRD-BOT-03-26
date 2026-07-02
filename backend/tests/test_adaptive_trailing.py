#!/usr/bin/env python3
"""Тесты адаптивного трейлинга по динамике свечей."""
from __future__ import annotations

from prd_agent.positions.adaptive_trailing import (
    AdaptiveTrailingConfig,
    compute_adaptive_distance_factor,
    favorable_move_pct,
    should_apply_adaptive_trailing,
)


def _k(closes: list[float]) -> list[dict]:
    return [{"close": c, "open": c, "high": c, "low": c} for c in closes]


def test_favorable_move_short_when_price_falls():
    klines = _k([100.0, 99.0, 97.5])
    move = favorable_move_pct("Sell", klines, lookback_bars=3)
    assert move > 2.0


def test_favorable_move_long_when_price_rises():
    klines = _k([100.0, 101.0, 102.5])
    move = favorable_move_pct("Buy", klines, lookback_bars=3)
    assert move > 2.0


def test_fast_move_tightens_trailing_for_short():
    cfg = AdaptiveTrailingConfig(
        enabled=True,
        lookback_bars=3,
        fast_move_pct=1.0,
        slow_move_pct=0.3,
        tight_distance_factor=0.55,
        normal_distance_factor=1.0,
    )
    klines = _k([100.0, 99.0, 97.0])
    factor, note = compute_adaptive_distance_factor(side="Sell", klines=klines, cfg=cfg)
    assert factor == 0.55
    assert "fast_move" in note


def test_slow_move_keeps_normal_trailing():
    cfg = AdaptiveTrailingConfig(
        enabled=True,
        lookback_bars=3,
        fast_move_pct=1.0,
        slow_move_pct=0.3,
        tight_distance_factor=0.55,
        normal_distance_factor=1.0,
    )
    klines = _k([100.0, 99.9, 99.8])
    factor, note = compute_adaptive_distance_factor(side="Sell", klines=klines, cfg=cfg)
    assert factor == 1.0
    assert "slow_move" in note


def test_manual_position_gets_adaptive_trailing():
    cfg = AdaptiveTrailingConfig(enabled=True, apply_to_manual=True)
    assert should_apply_adaptive_trailing(cfg=cfg, origin="manual", pump_dump_mode=False) is True


def test_production_config_has_adaptive_trailing():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "deploy" / "config.production.yaml").read_text(encoding="utf-8")
    assert "adaptive_trailing:" in text
    block = text.split("adaptive_trailing:", 1)[1].split("manual_management:", 1)[0]
    assert "enabled: true" in block
    assert "apply_to_manual: true" in block
