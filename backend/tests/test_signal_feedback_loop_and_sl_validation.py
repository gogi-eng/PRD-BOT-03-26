#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from analysis.liquidation_clusters import LiquidationAnalysis
from analysis.orderflow_analyzer import OrderflowSnapshot
from analysis.transformer_model import TransformerPrediction
from engine.entry_engine import EntryEngine
from engine.signal_feedback_loop import SignalFeedbackLoop
from engine.entry_engine import EntrySignal
from main import TradingBot


class MockConfig:
    def __init__(self, overrides=None):
        self._defaults = {
            ("entry", "min_rr_ratio"): 2.0,
            ("entry", "min_target_profit_pct"): 0.0,
            ("entry", "min_stop_distance_pct"): 0.0,
            ("entry", "sl_buffer_atr_mult"): 0.5,
            ("entry", "zone_proximity_pct"): 0.4,
            ("entry", "max_spread_pct"): 0.08,
            ("entry", "max_funding_rate"): 0.05,
            ("entry", "entry_threshold"): 0.55,
            ("entry", "trained_model_enabled"): False,
            ("feedback_loop", "enabled"): True,
            ("feedback_loop", "max_pending_hours"): 12,
            ("feedback_loop", "retrain_daily"): True,
            ("feedback_loop", "retrain_hour_utc"): 1,
            ("feedback_loop", "min_new_labels_for_retrain"): 1,
            ("feedback_loop", "dataset_path"): "training_data.json",
            ("feedback_loop", "queue_path"): "signal_feedback_queue.json",
            ("feedback_loop", "state_path"): "signal_feedback_state.json",
        }
        if overrides:
            self._defaults.update(overrides)

    def get(self, *keys, default=None):
        return self._defaults.get(keys, default)


@dataclass
class MockMarket:
    can_trade: bool = True


@dataclass
class MockRegime:
    regime: object = None

    def __post_init__(self):
        if self.regime is None:
            self.regime = type("R", (), {"value": "trend"})()


@dataclass
class MockStructure:
    trend: object
    last_sweep: object
    last_bos: object
    sweep_low: float = 0.0
    sweep_high: float = 0.0
    previous_high: float = 0.0
    previous_low: float = 0.0
    swing_highs: list = field(default_factory=list)
    swing_lows: list = field(default_factory=list)


@dataclass
class FakeSignal:
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    confidence: float
    metadata: dict


def make_liq():
    return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)


def test_invalid_sl_long_rejected():
    engine = EntryEngine(MockConfig({("entry", "min_stop_atr_mult"): 0.0}))
    structure = MockStructure(
        trend=type("T", (), {"value": "up"})(),
        last_sweep=type("SW", (), {"direction": "down"})(),
        last_bos=type("B", (), {"direction": "up"})(),
        sweep_low=50100.0,
        previous_high=56000.0,
        previous_low=49000.0,
    )

    signal = engine.generate_signal(
        "BTCUSDT",
        [],
        50000.0,
        MockMarket(),
        MockRegime(),
        TransformerPrediction(prob_up=0.8, prob_down=0.1, prob_flat=0.1),
        OrderflowSnapshot(normalized_imbalance=0.45, spread_pct=0.02),
        make_liq(),
        atr_value=100.0,
        structure=structure,
        htf_4h_trend=1,
    )
    assert signal.should_enter is False
    assert signal.metadata.get("reject_reason") == "invalid_sl_long"


def test_invalid_sl_short_rejected():
    engine = EntryEngine(MockConfig({("entry", "min_stop_atr_mult"): 0.0}))
    structure = MockStructure(
        trend=type("T", (), {"value": "down"})(),
        last_sweep=type("SW", (), {"direction": "up"})(),
        last_bos=type("B", (), {"direction": "down"})(),
        sweep_high=49800.0,
        previous_high=51000.0,
        previous_low=43000.0,
    )

    signal = engine.generate_signal(
        "BTCUSDT",
        [],
        50000.0,
        MockMarket(),
        MockRegime(),
        TransformerPrediction(prob_up=0.1, prob_down=0.8, prob_flat=0.1),
        OrderflowSnapshot(normalized_imbalance=-0.45, spread_pct=0.02),
        make_liq(),
        atr_value=100.0,
        structure=structure,
        htf_4h_trend=-1,
    )
    assert signal.should_enter is False
    assert signal.metadata.get("reject_reason") == "invalid_sl_short"


@pytest.mark.asyncio
async def test_signal_feedback_loop_labels_and_appends_dataset(tmp_path: Path):
    cfg = MockConfig(
        {
            ("feedback_loop", "dataset_path"): str(tmp_path / "training_data.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "signal_feedback_queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "signal_feedback_state.json"),
        }
    )
    loop = SignalFeedbackLoop(tmp_path, cfg)

    loop.register_signal(
        "XRPUSDT",
        FakeSignal(
            side="BUY",
            entry_price=1.00,
            stop_loss=0.97,
            take_profit=1.05,
            rr_ratio=2.0,
            confidence=0.86,
            metadata={
                "composite_score": 0.86,
                "trend_score": 0.9,
                "orderflow_score": 0.75,
                "ai_score": 0.7,
                "normalized_imbalance": 0.22,
                "htf_4h_trend": 1,
            },
        ),
    )

    async def get_price(_symbol: str):
        return 1.06

    outcomes = await loop.process_pending(get_price)
    assert len(outcomes) == 1
    assert outcomes[0].record["result"] == "win"

    with open(tmp_path / "training_data.json", "r", encoding="utf-8") as handle:
        dataset = __import__("json").load(handle)
    assert isinstance(dataset, list)
    assert len(dataset) == 1
    assert dataset[0]["symbol"] == "XRPUSDT"
    assert dataset[0]["source"] == "signal_only_feedback"


def test_feedback_loop_daily_retrain_gate(tmp_path: Path):
    cfg = MockConfig(
        {
            ("feedback_loop", "dataset_path"): str(tmp_path / "training_data.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "signal_feedback_queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "signal_feedback_state.json"),
            ("feedback_loop", "min_new_labels_for_retrain"): 1,
            ("feedback_loop", "retrain_hour_utc"): 0,
        }
    )
    loop = SignalFeedbackLoop(tmp_path, cfg)
    loop._state["new_labels_since_retrain"] = 1

    now = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)
    assert loop.should_run_daily_retrain(now) is True

    loop.mark_retrain_attempt(True)
    assert loop.should_run_daily_retrain(now) is False


def test_feedback_loop_quality_counter_controls_retrain_gate(tmp_path: Path):
    cfg = MockConfig(
        {
            ("feedback_loop", "dataset_path"): str(tmp_path / "training_data.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "signal_feedback_queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "signal_feedback_state.json"),
            ("feedback_loop", "min_new_labels_for_retrain"): 2,
            ("feedback_loop", "retrain_hour_utc"): 0,
        }
    )
    loop = SignalFeedbackLoop(tmp_path, cfg)

    now = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)
    assert loop.should_run_daily_retrain(now) is False

    loop.add_quality_labels(1)
    assert loop.should_run_daily_retrain(now) is False

    loop.add_quality_labels(1)
    assert loop.should_run_daily_retrain(now) is True


def _build_quality_gate_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.quality_gate_enabled = True
    bot.quality_gate_min_confidence = 0.68
    bot.quality_gate_min_expected_edge = 0.75
    bot.quality_gate_min_adx = 16.0
    bot.quality_gate_min_atr_pct = 0.20
    bot.quality_gate_min_abs_imbalance = 0.08
    bot.quality_gate_allow_chop = False
    bot.quality_gate_require_htf_trend = False
    bot.quality_gate_countertrend_min_confidence = 0.82
    bot.quality_gate_countertrend_min_abs_imbalance = 0.20
    bot.quality_gate_no_zone_min_confidence = 0.84
    return bot


def test_quality_gate_rejects_low_expected_edge():
    bot = _build_quality_gate_bot()
    signal = EntrySignal(
        should_enter=True,
        side="BUY",
        confidence=0.70,
        rr_ratio=1.2,
        metadata={
            "regime": "trend",
            "adx": 25,
            "atr_pct": 0.5,
            "entry_zone": "fvg_bullish",
            "htf_4h_trend": 1,
            "htf_trend": "up",
            "normalized_imbalance": 0.3,
        },
    )
    ok, reason, _ = bot._passes_signal_quality_gate("BTCUSDT", signal)
    assert ok is False
    assert reason == "low_expected_edge"


def test_quality_gate_rejects_chop_regime():
    bot = _build_quality_gate_bot()
    signal = EntrySignal(
        should_enter=True,
        side="BUY",
        confidence=0.90,
        rr_ratio=3.0,
        metadata={
            "regime": "chop",
            "adx": 30,
            "atr_pct": 0.6,
            "htf_trend": "up",
            "normalized_imbalance": 0.4,
        },
    )
    ok, reason, _ = bot._passes_signal_quality_gate("ETHUSDT", signal)
    assert ok is False
    assert reason == "chop_regime"


def test_quality_gate_passes_strong_signal():
    bot = _build_quality_gate_bot()
    signal = EntrySignal(
        should_enter=True,
        side="BUY",
        confidence=0.90,
        rr_ratio=3.0,
        metadata={
            "regime": "trend",
            "adx": 30,
            "atr_pct": 0.6,
            "htf_trend": "up",
            "normalized_imbalance": 0.4,
        },
    )
    ok, reason, meta = bot._passes_signal_quality_gate("SOLUSDT", signal)
    assert ok is True
    assert reason == "ok"
    assert meta.get("quality_expected_edge", 0) > 0.75


def test_quality_gate_rejects_countertrend_without_strong_confirmation():
    bot = _build_quality_gate_bot()
    signal = EntrySignal(
        should_enter=True,
        side="SELL",
        confidence=0.79,
        rr_ratio=3.0,
        metadata={
            "regime": "trend",
            "adx": 25,
            "atr_pct": 0.5,
            "htf_trend": "up",
            "htf_4h_trend": 1,
            "entry_zone": "ob_bearish",
            "normalized_imbalance": -0.35,
        },
    )
    ok, reason, _ = bot._passes_signal_quality_gate("LYNUSDT", signal)
    assert ok is False
    assert reason == "countertrend_low_confidence"


def test_quality_gate_rejects_no_zone_low_confidence():
    bot = _build_quality_gate_bot()
    signal = EntrySignal(
        should_enter=True,
        side="BUY",
        confidence=0.81,
        rr_ratio=3.0,
        metadata={
            "regime": "trend",
            "adx": 30,
            "atr_pct": 0.7,
            "htf_trend": "up",
            "htf_4h_trend": 1,
            "entry_zone": "no_zone",
            "normalized_imbalance": 0.45,
        },
    )
    ok, reason, _ = bot._passes_signal_quality_gate("LYNUSDT", signal)
    assert ok is False
    assert reason == "no_zone_low_confidence"
