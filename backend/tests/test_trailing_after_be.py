#!/usr/bin/env python3
"""Тесты: после BE/BE+ trailing distance на N п.п. короче."""
from __future__ import annotations

from pathlib import Path

from prd_agent.positions.breakeven_fees import breakeven_stop_price
from prd_agent.positions.trailing_after_be import (
    TrailingAfterBeConfig,
    apply_trailing_after_be_distance,
    is_be_phase,
    should_tighten_trailing_after_be,
    sl_is_at_or_beyond_be,
)


def test_from_cfg_defaults_disabled():
    cfg = TrailingAfterBeConfig.from_cfg({})
    assert cfg.enabled is False
    assert cfg.distance_reduce_pct == 0.5


def test_from_cfg_reads_distance_reduce_pct():
    cfg = TrailingAfterBeConfig.from_cfg(
        {"trailing_after_be": {"enabled": True, "distance_reduce_pct": 0.5}}
    )
    assert cfg.enabled is True
    assert cfg.distance_reduce_pct == 0.5


def test_from_cfg_clamps_distance_reduce_pct():
    cfg = TrailingAfterBeConfig.from_cfg(
        {"trailing_after_be": {"enabled": True, "distance_reduce_pct": -1.0}}
    )
    assert cfg.distance_reduce_pct == 0.0
    cfg2 = TrailingAfterBeConfig.from_cfg(
        {"trailing_after_be": {"enabled": True, "distance_reduce_pct": 9.0}}
    )
    assert cfg2.distance_reduce_pct == 5.0


def test_is_be_phase():
    assert is_be_phase("breakeven") is True
    assert is_be_phase("sr_trail") is True
    assert is_be_phase("none") is False
    assert is_be_phase("") is False


def test_sl_at_be_plus_long():
    entry = 100.0
    be_pct = 0.70  # fee+lock
    be = breakeven_stop_price("Buy", entry, be_pct)
    assert sl_is_at_or_beyond_be("Buy", entry, be, be_pct) is True
    assert sl_is_at_or_beyond_be("Buy", entry, be + 0.1, be_pct) is True
    assert sl_is_at_or_beyond_be("Buy", entry, entry - 1.0, be_pct) is False


def test_sl_at_be_plus_short():
    entry = 100.0
    be_pct = 0.70
    be = breakeven_stop_price("Sell", entry, be_pct)
    assert sl_is_at_or_beyond_be("Sell", entry, be, be_pct) is True
    assert sl_is_at_or_beyond_be("Sell", entry, be - 0.1, be_pct) is True
    assert sl_is_at_or_beyond_be("Sell", entry, entry + 1.0, be_pct) is False


def test_tighten_after_be_phase():
    cfg = TrailingAfterBeConfig(enabled=True, distance_reduce_pct=0.5)
    dist, note = apply_trailing_after_be_distance(
        3.5,
        cfg=cfg,
        min_distance_pct=1.8,
        tp_progress_phase="breakeven",
    )
    assert abs(dist - 3.0) < 1e-9
    assert note is not None
    assert "Trailing tighten after BE −0.5%" in note


def test_tighten_respects_min_floor():
    cfg = TrailingAfterBeConfig(enabled=True, distance_reduce_pct=0.5)
    dist, note = apply_trailing_after_be_distance(
        2.0,
        cfg=cfg,
        min_distance_pct=1.8,
        tp_progress_phase="breakeven",
    )
    assert abs(dist - 1.8) < 1e-9
    assert note is not None


def test_tighten_after_sl_at_be():
    cfg = TrailingAfterBeConfig(enabled=True, distance_reduce_pct=0.5)
    entry = 100.0
    be_pct = 0.7
    be = breakeven_stop_price("Buy", entry, be_pct)
    dist, note = apply_trailing_after_be_distance(
        3.5,
        cfg=cfg,
        min_distance_pct=1.8,
        tp_progress_phase="",
        side="Buy",
        entry=entry,
        stop_loss=be,
        be_buffer_pct=be_pct,
    )
    assert abs(dist - 3.0) < 1e-9
    assert note is not None


def test_no_tighten_before_be():
    cfg = TrailingAfterBeConfig(enabled=True, distance_reduce_pct=0.5)
    dist, note = apply_trailing_after_be_distance(
        3.5,
        cfg=cfg,
        min_distance_pct=1.8,
        tp_progress_phase="none",
        side="Buy",
        entry=100.0,
        stop_loss=98.0,
        be_buffer_pct=0.7,
    )
    assert dist == 3.5
    assert note is None


def test_disabled_no_tighten():
    cfg = TrailingAfterBeConfig(enabled=False, distance_reduce_pct=0.5)
    assert should_tighten_trailing_after_be(cfg=cfg, tp_progress_phase="breakeven") is False
    dist, note = apply_trailing_after_be_distance(
        3.5, cfg=cfg, tp_progress_phase="breakeven"
    )
    assert dist == 3.5
    assert note is None


def test_sandbox_config_has_trailing_after_be_tighten():
    root = Path(__file__).resolve().parents[2]
    text = (root / "deploy" / "config.agent_world_sandbox.yaml").read_text(encoding="utf-8")
    assert "trailing_after_be:" in text
    section = text.split("trailing_after_be:", 1)[1][:160]
    assert "enabled: true" in section
    assert "distance_reduce_pct: 0.5" in section
    assert "widen_mult" not in section


def test_production_config_has_trailing_after_be_tighten():
    root = Path(__file__).resolve().parents[2]
    text = (root / "deploy" / "config.production.yaml").read_text(encoding="utf-8")
    assert "trailing_after_be:" in text
    section = text.split("trailing_after_be:", 1)[1][:160]
    assert "enabled: true" in section
    assert "distance_reduce_pct: 0.5" in section
    assert "widen_mult" not in section
