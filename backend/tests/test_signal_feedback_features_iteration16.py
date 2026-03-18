#!/usr/bin/env python3
"""
Tests for Iteration 16 features per review request:

1. signal-only feedback-loop registers sent signals
2. feedback-loop auto-labels win/loss by SL/TP/timeout and appends to training_data.json
3. daily retrain gate logic works by UTC hour and min_new_labels_for_retrain
4. main.py reads controls leverage/max_positions from config (no hardcoded mismatch)
5. analysis/ai_analyzer.py cache ttl is 600
6. entry_engine rejects invalid SL with invalid_sl_long/invalid_sl_short
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

from engine.signal_feedback_loop import SignalFeedbackLoop, SignalOutcome
from analysis.ai_analyzer import AITradeAnalyzer
from analysis.liquidation_clusters import LiquidationAnalysis
from analysis.orderflow_analyzer import OrderflowSnapshot
from analysis.transformer_model import TransformerPrediction
from engine.entry_engine import EntryEngine


# ══════════════════════════════════════════════════════════════════════════════
# MOCK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

class MockConfig:
    """Mock BotConfig for testing."""
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
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 8,
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
    """Minimal signal for testing."""
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    confidence: float
    metadata: dict = field(default_factory=dict)


def make_liq():
    return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: SIGNAL-ONLY FEEDBACK LOOP REGISTERS SIGNALS
# ══════════════════════════════════════════════════════════════════════════════

class TestSignalFeedbackLoopRegistration:
    """Tests for signal registration in feedback loop."""

    def test_register_signal_adds_to_queue(self, tmp_path: Path):
        """register_signal should add signal to queue file."""
        cfg = MockConfig({
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        signal = FakeSignal(
            side="BUY",
            entry_price=100.0,
            stop_loss=97.0,
            take_profit=106.0,
            rr_ratio=2.0,
            confidence=0.85,
            metadata={"composite_score": 0.85, "htf_4h_trend": 1},
        )
        loop.register_signal("BTCUSDT", signal)
        
        # Check queue has 1 item
        assert len(loop._queue) == 1
        assert loop._queue[0]["symbol"] == "BTCUSDT"
        assert loop._queue[0]["side"] == "BUY"
        assert loop._queue[0]["entry_price"] == 100.0
        
        # Check file was saved
        with open(tmp_path / "queue.json") as f:
            saved_queue = json.load(f)
        assert len(saved_queue) == 1

    def test_register_signal_captures_all_metadata(self, tmp_path: Path):
        """register_signal should capture all required metadata fields."""
        cfg = MockConfig({
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        signal = FakeSignal(
            side="SELL",
            entry_price=50000.0,
            stop_loss=51000.0,
            take_profit=48000.0,
            rr_ratio=2.5,
            confidence=0.75,
            metadata={
                "composite_score": 0.75,
                "trend_score": 0.8,
                "orderflow_score": 0.7,
                "ai_score": 0.65,
                "normalized_imbalance": -0.3,
                "htf_4h_trend": -1,
                "trained_model_prob": 0.62,
                "entry_zone": "ob_bearish",
            },
        )
        loop.register_signal("ETHUSDT", signal)
        
        queued = loop._queue[0]
        assert queued["symbol"] == "ETHUSDT"
        assert queued["side"] == "SELL"
        assert queued["stop_loss"] == 51000.0
        assert queued["take_profit"] == 48000.0
        assert queued["rr_ratio"] == 2.5
        assert queued["confidence"] == 0.75
        assert queued["composite_score"] == 0.75
        assert queued["htf_4h_trend"] == -1
        assert "created_at" in queued
        assert "id" in queued

    def test_register_signal_disabled_does_nothing(self, tmp_path: Path):
        """When feedback loop disabled, register_signal does nothing."""
        cfg = MockConfig({
            ("feedback_loop", "enabled"): False,
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        signal = FakeSignal(
            side="BUY", entry_price=100.0, stop_loss=97.0,
            take_profit=106.0, rr_ratio=2.0, confidence=0.85,
        )
        loop.register_signal("BTCUSDT", signal)
        
        assert len(loop._queue) == 0


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: AUTO-LABELING WIN/LOSS BY SL/TP/TIMEOUT
# ══════════════════════════════════════════════════════════════════════════════

class TestAutoLabelingLogic:
    """Tests for auto-labeling win/loss based on SL/TP/timeout."""

    @pytest.mark.asyncio
    async def test_label_win_when_tp_hit_long(self, tmp_path: Path):
        """BUY signal: price >= take_profit → win."""
        cfg = MockConfig({
            ("feedback_loop", "dataset_path"): str(tmp_path / "dataset.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        loop.register_signal("XRPUSDT", FakeSignal(
            side="BUY", entry_price=1.00, stop_loss=0.97,
            take_profit=1.05, rr_ratio=1.67, confidence=0.8,
        ))
        
        async def get_price(_: str):
            return 1.06  # above TP
        
        outcomes = await loop.process_pending(get_price)
        assert len(outcomes) == 1
        assert outcomes[0].record["result"] == "win"
        assert outcomes[0].reason == "take_profit"

    @pytest.mark.asyncio
    async def test_label_loss_when_sl_hit_long(self, tmp_path: Path):
        """BUY signal: price <= stop_loss → loss."""
        cfg = MockConfig({
            ("feedback_loop", "dataset_path"): str(tmp_path / "dataset.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        loop.register_signal("BTCUSDT", FakeSignal(
            side="BUY", entry_price=50000.0, stop_loss=49000.0,
            take_profit=52000.0, rr_ratio=2.0, confidence=0.75,
        ))
        
        async def get_price(_: str):
            return 48500.0  # below SL
        
        outcomes = await loop.process_pending(get_price)
        assert len(outcomes) == 1
        assert outcomes[0].record["result"] == "loss"
        assert outcomes[0].reason == "stop_loss"

    @pytest.mark.asyncio
    async def test_label_win_when_tp_hit_short(self, tmp_path: Path):
        """SELL signal: price <= take_profit → win."""
        cfg = MockConfig({
            ("feedback_loop", "dataset_path"): str(tmp_path / "dataset.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        loop.register_signal("ETHUSDT", FakeSignal(
            side="SELL", entry_price=3000.0, stop_loss=3100.0,
            take_profit=2850.0, rr_ratio=1.5, confidence=0.7,
        ))
        
        async def get_price(_: str):
            return 2800.0  # below TP
        
        outcomes = await loop.process_pending(get_price)
        assert len(outcomes) == 1
        assert outcomes[0].record["result"] == "win"
        assert outcomes[0].reason == "take_profit"

    @pytest.mark.asyncio
    async def test_label_loss_when_sl_hit_short(self, tmp_path: Path):
        """SELL signal: price >= stop_loss → loss."""
        cfg = MockConfig({
            ("feedback_loop", "dataset_path"): str(tmp_path / "dataset.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        loop.register_signal("SOLUSDT", FakeSignal(
            side="SELL", entry_price=100.0, stop_loss=105.0,
            take_profit=90.0, rr_ratio=2.0, confidence=0.65,
        ))
        
        async def get_price(_: str):
            return 106.0  # above SL
        
        outcomes = await loop.process_pending(get_price)
        assert len(outcomes) == 1
        assert outcomes[0].record["result"] == "loss"
        assert outcomes[0].reason == "stop_loss"

    @pytest.mark.asyncio
    async def test_label_timeout_after_max_pending_hours(self, tmp_path: Path):
        """Signal exceeding max_pending_hours should be labeled by timeout."""
        cfg = MockConfig({
            ("feedback_loop", "max_pending_hours"): 0.001,  # Very short for test
            ("feedback_loop", "dataset_path"): str(tmp_path / "dataset.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        # Manually add old signal
        old_time = datetime.now(timezone.utc) - timedelta(hours=1)
        loop._queue.append({
            "id": "test_timeout",
            "created_at": old_time.isoformat(),
            "symbol": "LINKUSDT",
            "side": "BUY",
            "entry_price": 15.0,
            "stop_loss": 14.0,
            "take_profit": 17.0,
            "rr_ratio": 2.0,
            "confidence": 0.7,
        })
        
        async def get_price(_: str):
            return 15.5  # In profit but no SL/TP hit
        
        outcomes = await loop.process_pending(get_price)
        assert len(outcomes) == 1
        assert outcomes[0].reason == "timeout"
        # Win/loss based on pnl at timeout
        assert outcomes[0].record["result"] == "win"  # 15.5 > 15.0 for BUY

    @pytest.mark.asyncio
    async def test_appends_to_training_data_json(self, tmp_path: Path):
        """Labeled records should be appended to training_data.json."""
        cfg = MockConfig({
            ("feedback_loop", "dataset_path"): str(tmp_path / "training_data.json"),
            ("feedback_loop", "queue_path"): str(tmp_path / "queue.json"),
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        # Register and process multiple signals
        loop.register_signal("BTCUSDT", FakeSignal(
            side="BUY", entry_price=50000.0, stop_loss=49000.0,
            take_profit=52000.0, rr_ratio=2.0, confidence=0.8,
        ))
        loop.register_signal("ETHUSDT", FakeSignal(
            side="SELL", entry_price=3000.0, stop_loss=3100.0,
            take_profit=2850.0, rr_ratio=1.5, confidence=0.7,
        ))
        
        async def get_price(symbol: str):
            if symbol == "BTCUSDT":
                return 52500.0  # TP hit
            return 2800.0  # TP hit
        
        outcomes = await loop.process_pending(get_price)
        assert len(outcomes) == 2
        
        # Check dataset file
        with open(tmp_path / "training_data.json") as f:
            dataset = json.load(f)
        
        assert len(dataset) == 2
        assert dataset[0]["symbol"] == "BTCUSDT"
        assert dataset[0]["source"] == "signal_only_feedback"
        assert dataset[1]["symbol"] == "ETHUSDT"


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: DAILY RETRAIN GATE LOGIC
# ══════════════════════════════════════════════════════════════════════════════

class TestDailyRetrainGate:
    """Tests for daily retrain gate logic."""

    def test_retrain_blocked_when_disabled(self, tmp_path: Path):
        """When retrain_daily=False, should_run_daily_retrain returns False."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_daily"): False,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["new_labels_since_retrain"] = 100
        
        now = datetime.now(timezone.utc).replace(hour=5)
        assert loop.should_run_daily_retrain(now) is False

    def test_retrain_blocked_before_retrain_hour_utc(self, tmp_path: Path):
        """Should not retrain before retrain_hour_utc."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 5,
            ("feedback_loop", "min_new_labels_for_retrain"): 1,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["new_labels_since_retrain"] = 10
        
        # Hour 3 UTC (before 5)
        now = datetime(2026, 1, 15, 3, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is False

    def test_retrain_allowed_after_retrain_hour_utc(self, tmp_path: Path):
        """Should allow retrain after retrain_hour_utc if labels sufficient."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 1,
            ("feedback_loop", "min_new_labels_for_retrain"): 5,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["new_labels_since_retrain"] = 8
        loop._state["last_retrain_attempt_date"] = ""
        
        # Hour 2 UTC (after 1)
        now = datetime(2026, 1, 15, 2, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is True

    def test_retrain_blocked_below_min_new_labels(self, tmp_path: Path):
        """Should not retrain if new_labels_since_retrain < min_new_labels_for_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 8,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["new_labels_since_retrain"] = 5  # Less than 8
        
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is False

    def test_retrain_blocked_if_already_attempted_today(self, tmp_path: Path):
        """Should not retrain twice on the same day."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 1,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["new_labels_since_retrain"] = 10
        loop._state["last_retrain_attempt_date"] = "2026-01-15"
        
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is False

    def test_mark_retrain_attempt_success_resets_counter(self, tmp_path: Path):
        """mark_retrain_attempt(success=True) should reset new_labels counter."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["new_labels_since_retrain"] = 15
        
        loop.mark_retrain_attempt(success=True)
        
        assert loop._state["new_labels_since_retrain"] == 0
        assert loop._state["last_retrain_success_date"] != ""

    def test_mark_retrain_attempt_failure_keeps_counter(self, tmp_path: Path):
        """mark_retrain_attempt(success=False) should NOT reset counter."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["new_labels_since_retrain"] = 15
        
        loop.mark_retrain_attempt(success=False)
        
        assert loop._state["new_labels_since_retrain"] == 15


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: MAIN.PY READS LEVERAGE/MAX_POSITIONS FROM CONFIG
# ══════════════════════════════════════════════════════════════════════════════

class TestMainConfigReading:
    """Tests that main.py reads controls from config (no hardcoded mismatch)."""

    def test_config_contains_leverage(self):
        """config.yaml should define trading.leverage."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert "leverage" in cfg["trading"]
        assert isinstance(cfg["trading"]["leverage"], int)

    def test_config_contains_max_positions(self):
        """config.yaml should define trading.max_positions."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert "max_positions" in cfg["trading"]
        assert isinstance(cfg["trading"]["max_positions"], int)

    def test_main_reads_leverage_from_config(self):
        """main.py should read leverage via cfg.get, not hardcode."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        
        # Should use cfg.get("trading", "leverage", ...) for LiveControls init
        assert 'self.cfg.get("trading", "leverage"' in content
        # Should NOT have hardcoded leverage in LiveControls (besides default)
        # Check the default value line
        assert 'leverage=self.cfg.get("trading", "leverage"' in content

    def test_main_reads_max_positions_from_config(self):
        """main.py should read max_positions via cfg.get, not hardcode."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        
        # Should use cfg.get("trading", "max_positions", ...) for LiveControls init
        assert 'self.cfg.get("trading", "max_positions"' in content

    def test_config_leverage_value(self):
        """Current leverage value should be 15 as per config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["trading"]["leverage"] == 15

    def test_config_max_positions_value(self):
        """Current max_positions value should be 6 as per config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["trading"]["max_positions"] == 6


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: AI ANALYZER CACHE TTL IS 600
# ══════════════════════════════════════════════════════════════════════════════

class TestAIAnalyzerCacheTTL:
    """Tests for AI analyzer cache TTL configuration."""

    def test_cache_ttl_is_600(self):
        """AITradeAnalyzer._cache_ttl should be 600 seconds."""
        analyzer = AITradeAnalyzer()
        assert analyzer._cache_ttl == 600

    def test_cache_ttl_hardcoded_in_init(self):
        """_cache_ttl=600 should be set in __init__."""
        ai_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'analysis', 'ai_analyzer.py')
        with open(ai_path) as f:
            content = f.read()
        assert "_cache_ttl = 600" in content

    def test_cache_used_in_analyze(self):
        """Cache should be checked using _cache_ttl in analyze method."""
        ai_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'analysis', 'ai_analyzer.py')
        with open(ai_path) as f:
            content = f.read()
        # Check cache is used
        assert "self._cache_ttl" in content
        assert "_cache" in content


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: ENTRY ENGINE REJECTS INVALID SL
# ══════════════════════════════════════════════════════════════════════════════

class TestEntryEngineInvalidSLRejection:
    """Tests for invalid SL rejection with specific reject reasons."""

    def test_invalid_sl_long_rejection(self):
        """Long entry with SL >= entry_price should be rejected as invalid_sl_long."""
        engine = EntryEngine(MockConfig())
        
        # Structure that would produce SL above entry price for long
        structure = MockStructure(
            trend=type("T", (), {"value": "up"})(),
            last_sweep=type("SW", (), {"direction": "down"})(),
            last_bos=type("B", (), {"direction": "up"})(),
            sweep_low=50100.0,  # SL would be > entry_price of 50000
            previous_high=56000.0,
            previous_low=49000.0,
        )
        
        signal = engine.generate_signal(
            "BTCUSDT",
            [],
            50000.0,  # entry price
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

    def test_invalid_sl_short_rejection(self):
        """Short entry with SL <= entry_price should be rejected as invalid_sl_short."""
        engine = EntryEngine(MockConfig())
        
        # Structure that would produce SL below entry price for short
        structure = MockStructure(
            trend=type("T", (), {"value": "down"})(),
            last_sweep=type("SW", (), {"direction": "up"})(),
            last_bos=type("B", (), {"direction": "down"})(),
            sweep_high=49800.0,  # SL would be < entry_price of 50000
            previous_high=51000.0,
            previous_low=43000.0,
        )
        
        signal = engine.generate_signal(
            "BTCUSDT",
            [],
            50000.0,  # entry price
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

    def test_valid_sl_long_not_rejected(self):
        """Long entry with valid SL < entry_price should NOT be rejected for SL."""
        engine = EntryEngine(MockConfig())
        
        structure = MockStructure(
            trend=type("T", (), {"value": "up"})(),
            last_sweep=type("SW", (), {"direction": "down"})(),
            last_bos=type("B", (), {"direction": "up"})(),
            sweep_low=49000.0,  # Valid SL below entry
            previous_high=56000.0,
            previous_low=48000.0,
        )
        
        signal = engine.generate_signal(
            "BTCUSDT",
            [],
            50000.0,
            MockMarket(),
            MockRegime(),
            TransformerPrediction(prob_up=0.75, prob_down=0.15, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=0.35, spread_pct=0.02),
            make_liq(),
            atr_value=400.0,
            structure=structure,
            htf_4h_trend=1,
        )
        
        # Should NOT reject due to invalid_sl_long
        reject_reason = signal.metadata.get("reject_reason", "")
        assert "invalid_sl_long" not in reject_reason

    def test_valid_sl_short_not_rejected(self):
        """Short entry with valid SL > entry_price should NOT be rejected for SL."""
        engine = EntryEngine(MockConfig())
        
        structure = MockStructure(
            trend=type("T", (), {"value": "down"})(),
            last_sweep=type("SW", (), {"direction": "up"})(),
            last_bos=type("B", (), {"direction": "down"})(),
            sweep_high=51000.0,  # Valid SL above entry
            previous_high=52000.0,
            previous_low=44000.0,
        )
        
        signal = engine.generate_signal(
            "BTCUSDT",
            [],
            50000.0,
            MockMarket(),
            MockRegime(),
            TransformerPrediction(prob_up=0.15, prob_down=0.75, prob_flat=0.10),
            OrderflowSnapshot(normalized_imbalance=-0.35, spread_pct=0.02),
            make_liq(),
            atr_value=400.0,
            structure=structure,
            htf_4h_trend=-1,
        )
        
        # Should NOT reject due to invalid_sl_short
        reject_reason = signal.metadata.get("reject_reason", "")
        assert "invalid_sl_short" not in reject_reason

    def test_entry_engine_contains_sl_validation_code(self):
        """Entry engine source should contain SL validation checks."""
        engine_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'engine', 'entry_engine.py')
        with open(engine_path) as f:
            content = f.read()
        
        assert 'invalid_sl_long' in content
        assert 'invalid_sl_short' in content
        assert 'sl >= current_price' in content or 'sl <= current_price' in content


# ══════════════════════════════════════════════════════════════════════════════
# ADDITIONAL INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestFeedbackLoopConfig:
    """Tests for feedback loop config in config.yaml."""

    def test_feedback_loop_enabled_in_config(self):
        """feedback_loop.enabled should be True in config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["enabled"] is True

    def test_retrain_daily_in_config(self):
        """feedback_loop.retrain_daily should be True in config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["retrain_daily"] is True

    def test_min_new_labels_for_retrain_in_config(self):
        """feedback_loop.min_new_labels_for_retrain should be 8 in config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["min_new_labels_for_retrain"] == 8

    def test_retrain_hour_utc_in_config(self):
        """feedback_loop.retrain_hour_utc should be 1 in config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["retrain_hour_utc"] == 1

    def test_dataset_path_in_config(self):
        """feedback_loop.dataset_path should be training_data.json in config."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["dataset_path"] == "training_data.json"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
