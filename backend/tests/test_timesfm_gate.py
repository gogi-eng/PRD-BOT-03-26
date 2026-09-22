"""Unit-тесты TimesFM gate (без загрузки модели)."""
from __future__ import annotations

from pathlib import Path

import pytest

from prd_agent.integrations.timesfm_gate import (
    TimesFMGate,
    actual_move_pct,
    direction_from_delta,
    evaluate_calibration,
    side_to_direction,
)


def test_side_to_direction():
    assert side_to_direction("Buy") == "up"
    assert side_to_direction("SELL") == "down"
    assert side_to_direction("") == "flat"


def test_direction_from_delta():
    assert direction_from_delta(0.5) == "up"
    assert direction_from_delta(-0.5) == "down"
    assert direction_from_delta(0.01, eps=0.02) == "flat"


def test_actual_move_pct():
    closes = [100.0, 101.0, 102.0, 103.0]
    assert actual_move_pct(closes, 3) == pytest.approx(3.0)


def test_evaluate_calibration_pass():
    samples = [{"resolved": True, "correct": True} for _ in range(5)]
    ok, acc = evaluate_calibration(samples, required=5, min_accuracy=0.8)
    assert ok is True
    assert acc == pytest.approx(1.0)


def test_evaluate_calibration_fail():
    samples = [
        {"resolved": True, "correct": True},
        {"resolved": True, "correct": True},
        {"resolved": True, "correct": True},
        {"resolved": True, "correct": False},
        {"resolved": True, "correct": False},
    ]
    ok, acc = evaluate_calibration(samples, required=5, min_accuracy=0.8)
    assert ok is False
    assert acc == pytest.approx(0.6)


def test_evaluate_calibration_not_enough():
    samples = [{"resolved": True, "correct": True} for _ in range(3)]
    ok, acc = evaluate_calibration(samples, required=5, min_accuracy=0.8)
    assert ok is False
    assert acc == 0.0


def test_gate_toggle_and_state(tmp_path: Path):
    cfg = {
        "timesfm_gate": {
            "enabled": True,
            "calibration_samples": 5,
            "min_accuracy": 0.8,
        }
    }
    gate = TimesFMGate(cfg, tmp_path / "data")
    assert gate.feature_enabled() is True
    assert gate.get_global_enabled() is True
    new_on = gate.toggle_global()
    assert new_on is False
    assert gate.get_global_enabled() is False
    gate.toggle_global()
    assert gate.get_global_enabled() is True


def test_gate_trading_enabled_per_symbol(tmp_path: Path):
    cfg = {"timesfm_gate": {"enabled": True}}
    gate = TimesFMGate(cfg, tmp_path / "data")
    sym_st = gate._sym_state("SOLUSDT")
    sym_st["trading_enabled"] = True
    gate._save_state()
    assert gate.is_trading_enabled_for("SOLUSDT") is True
    gate.toggle_global()
    assert gate.is_trading_enabled_for("SOLUSDT") is False


def test_build_telegram_report_with_symbols(tmp_path: Path):
    cfg = {"timesfm_gate": {"enabled": True, "calibration_samples": 5, "min_accuracy": 0.8}}
    gate = TimesFMGate(cfg, tmp_path / "data")
    sym_st = gate._sym_state("BTCUSDT")
    sym_st["samples"] = [{"resolved": True, "correct": True}]
    sym_st["trading_enabled"] = False
    sym_st["last_accuracy"] = 80.0
    gate._save_state()
    text = gate.build_telegram_report(header="Test")
    assert "BTCUSDT" in text
    assert "калибровка" in text.lower() or "Калибровка" in text


@pytest.mark.asyncio
async def test_check_entry_skipped_when_not_trading(tmp_path: Path):
    cfg = {"timesfm_gate": {"enabled": True}}
    gate = TimesFMGate(cfg, tmp_path / "data")
    ok, reason = await gate.check_entry(None, "ETHUSDT", "Buy")
    assert ok is True
    assert reason == ""
