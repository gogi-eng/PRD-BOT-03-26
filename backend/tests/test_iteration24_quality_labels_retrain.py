#!/usr/bin/env python3
"""
Tests for Iteration 24 features:

1. feedback_loop.min_new_labels_for_retrain set to 150 in config
2. SignalFeedbackLoop tracks quality_labels_since_retrain
3. should_run_daily_retrain gate checks quality labels threshold (not raw label count)
4. main.py increments quality label counter from resolved outcomes
5. main.py sends Telegram notification on retrain failure and exception
6. Quality label definition: exit_reason in {take_profit, stop_loss} + hold>=8m + |pnl|>=0.4%
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

from engine.signal_feedback_loop import SignalFeedbackLoop


# ══════════════════════════════════════════════════════════════════════════════
# MOCK HELPERS
# ══════════════════════════════════════════════════════════════════════════════

class MockConfig:
    """Mock BotConfig for testing."""
    def __init__(self, overrides=None):
        self._defaults = {
            ("feedback_loop", "enabled"): True,
            ("feedback_loop", "max_pending_hours"): 12,
            ("feedback_loop", "retrain_daily"): True,
            ("feedback_loop", "retrain_hour_utc"): 1,
            ("feedback_loop", "min_new_labels_for_retrain"): 150,
            ("feedback_loop", "dataset_path"): "training_data.json",
            ("feedback_loop", "queue_path"): "signal_feedback_queue.json",
            ("feedback_loop", "state_path"): "signal_feedback_state.json",
        }
        if overrides:
            self._defaults.update(overrides)

    def get(self, *keys, default=None):
        return self._defaults.get(keys, default)


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


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1: CONFIG HAS min_new_labels_for_retrain = 150
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigMinLabelsThreshold:
    """Tests for min_new_labels_for_retrain = 150 in config."""

    def test_config_min_new_labels_for_retrain_is_150(self):
        """config.yaml should have feedback_loop.min_new_labels_for_retrain = 150."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["min_new_labels_for_retrain"] == 150

    def test_signal_feedback_loop_reads_min_labels_from_config(self, tmp_path: Path):
        """SignalFeedbackLoop should read min_new_labels_for_retrain from config."""
        cfg = MockConfig({
            ("feedback_loop", "min_new_labels_for_retrain"): 150,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        assert loop.min_new_labels_for_retrain == 150


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2: SignalFeedbackLoop TRACKS quality_labels_since_retrain
# ══════════════════════════════════════════════════════════════════════════════

class TestQualityLabelsTracking:
    """Tests for quality_labels_since_retrain tracking."""

    def test_state_has_quality_labels_since_retrain_field(self, tmp_path: Path):
        """State should have quality_labels_since_retrain field."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        assert "quality_labels_since_retrain" in loop._state

    def test_add_quality_labels_increments_counter(self, tmp_path: Path):
        """add_quality_labels should increment quality_labels_since_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        initial = loop._state.get("quality_labels_since_retrain", 0)
        loop.add_quality_labels(5)
        assert loop._state["quality_labels_since_retrain"] == initial + 5
        
        loop.add_quality_labels(10)
        assert loop._state["quality_labels_since_retrain"] == initial + 15

    def test_add_quality_labels_persists_to_file(self, tmp_path: Path):
        """add_quality_labels should persist to state file."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop.add_quality_labels(7)
        
        # Reload from file
        with open(tmp_path / "state.json") as f:
            saved_state = json.load(f)
        assert saved_state["quality_labels_since_retrain"] == 7

    def test_add_quality_labels_ignores_zero_or_negative(self, tmp_path: Path):
        """add_quality_labels should ignore zero or negative counts."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 10
        
        loop.add_quality_labels(0)
        assert loop._state["quality_labels_since_retrain"] == 10
        
        loop.add_quality_labels(-5)
        assert loop._state["quality_labels_since_retrain"] == 10

    def test_mark_retrain_attempt_success_resets_quality_labels(self, tmp_path: Path):
        """mark_retrain_attempt(success=True) should reset quality_labels_since_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 200
        
        loop.mark_retrain_attempt(success=True)
        
        assert loop._state["quality_labels_since_retrain"] == 0

    def test_mark_retrain_attempt_failure_keeps_quality_labels(self, tmp_path: Path):
        """mark_retrain_attempt(success=False) should NOT reset quality_labels_since_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 200
        
        loop.mark_retrain_attempt(success=False)
        
        assert loop._state["quality_labels_since_retrain"] == 200


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3: should_run_daily_retrain CHECKS QUALITY LABELS THRESHOLD
# ══════════════════════════════════════════════════════════════════════════════

class TestRetrainGateUsesQualityLabels:
    """Tests that should_run_daily_retrain uses quality_labels_since_retrain."""

    def test_retrain_blocked_when_quality_labels_below_threshold(self, tmp_path: Path):
        """Should not retrain if quality_labels_since_retrain < min_new_labels_for_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 150,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 100  # Below 150
        loop._state["new_labels_since_retrain"] = 500  # Raw labels high but quality low
        
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is False

    def test_retrain_allowed_when_quality_labels_at_threshold(self, tmp_path: Path):
        """Should allow retrain if quality_labels_since_retrain >= min_new_labels_for_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 150,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 150  # Exactly at threshold
        loop._state["last_retrain_attempt_date"] = ""
        
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is True

    def test_retrain_allowed_when_quality_labels_above_threshold(self, tmp_path: Path):
        """Should allow retrain if quality_labels_since_retrain > min_new_labels_for_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 150,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 200  # Above threshold
        loop._state["last_retrain_attempt_date"] = ""
        
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is True

    def test_retrain_uses_quality_labels_not_raw_labels(self, tmp_path: Path):
        """Retrain gate should use quality_labels, not new_labels_since_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 150,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        # High raw labels but low quality labels
        loop._state["new_labels_since_retrain"] = 1000
        loop._state["quality_labels_since_retrain"] = 50
        loop._state["last_retrain_attempt_date"] = ""
        
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        # Should be blocked because quality_labels (50) < threshold (150)
        assert loop.should_run_daily_retrain(now) is False

    def test_backward_compat_fallback_to_new_labels(self, tmp_path: Path):
        """If quality_labels_since_retrain is 0, should fallback to new_labels_since_retrain."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 0,
            ("feedback_loop", "min_new_labels_for_retrain"): 10,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        # quality_labels is 0, but new_labels is high
        loop._state["quality_labels_since_retrain"] = 0
        loop._state["new_labels_since_retrain"] = 50
        loop._state["last_retrain_attempt_date"] = ""
        
        now = datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)
        # Should fallback to new_labels_since_retrain (50) >= threshold (10)
        assert loop.should_run_daily_retrain(now) is True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4: main.py INCREMENTS QUALITY LABEL COUNTER
# ══════════════════════════════════════════════════════════════════════════════

class TestMainIncrementQualityLabels:
    """Tests that main.py increments quality label counter from resolved outcomes."""

    def test_main_has_is_quality_feedback_record_method(self):
        """main.py should have _is_quality_feedback_record method."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        assert "_is_quality_feedback_record" in content

    def test_main_calls_add_quality_labels(self):
        """main.py should call signal_feedback.add_quality_labels."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        assert "add_quality_labels" in content
        assert "self.signal_feedback.add_quality_labels" in content

    def test_main_counts_quality_labels_from_outcomes(self):
        """main.py should count quality labels from resolved outcomes."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        # Should iterate outcomes and check quality
        assert "_is_quality_feedback_record" in content
        assert "quality_labels" in content

    def test_quality_label_definition_in_main(self):
        """main.py should define quality label criteria: exit_reason, hold time, pnl."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        # Check for quality label criteria
        assert "exit_reason" in content
        assert "take_profit" in content or "stop_loss" in content
        # Check for hold time and pnl criteria
        assert "min_feedback_label_hold_minutes" in content or "hold" in content.lower()
        assert "min_feedback_label_abs_pnl_pct" in content or "pnl" in content.lower()


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5: TELEGRAM NOTIFICATION ON RETRAIN FAILURE
# ══════════════════════════════════════════════════════════════════════════════

class TestTelegramRetrainFailureNotification:
    """Tests for Telegram notification on retrain failure and exception."""

    def test_main_sends_telegram_on_retrain_failure(self):
        """main.py should send Telegram message when retrain fails."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        # Check for failure notification
        assert "DAILY RETRAIN FAILED" in content

    def test_main_sends_telegram_on_retrain_exception(self):
        """main.py should send Telegram message when retrain raises exception."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        # Check for exception notification
        assert "DAILY RETRAIN ERROR" in content

    def test_retrain_failure_notification_contains_reason(self):
        """Retrain failure notification should contain failure reason."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        # Check that failure message has context
        assert "RETRAIN FAILED" in content
        # Should mention checkpoint or validation
        assert "чекпоинт" in content.lower() or "checkpoint" in content.lower() or "улучшен" in content.lower()

    def test_retrain_exception_notification_contains_error(self):
        """Retrain exception notification should contain error message."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        # Check that exception message includes error
        assert "RETRAIN ERROR" in content
        assert "Ошибка" in content or "error" in content.lower()


# ══════════════════════════════════════════════════════════════════════════════
# TEST 6: QUALITY LABEL DEFINITION
# ══════════════════════════════════════════════════════════════════════════════

class TestQualityLabelDefinition:
    """Tests for quality label definition: exit_reason in {take_profit, stop_loss} + hold>=8m + |pnl|>=0.4%."""

    def test_config_has_min_feedback_label_abs_pnl_pct(self):
        """config.yaml should have feedback_loop.min_feedback_label_abs_pnl_pct = 0.4."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["min_feedback_label_abs_pnl_pct"] == 0.4

    def test_config_has_min_feedback_label_hold_minutes(self):
        """config.yaml should have feedback_loop.min_feedback_label_hold_minutes = 8."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["min_feedback_label_hold_minutes"] == 8

    def test_main_reads_quality_label_config(self):
        """main.py should read quality label config values."""
        main_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'main.py')
        with open(main_path) as f:
            content = f.read()
        assert "min_feedback_label_abs_pnl_pct" in content
        assert "min_feedback_label_hold_minutes" in content


# ══════════════════════════════════════════════════════════════════════════════
# TEST 7: RETRAIN HOUR UTC = 1
# ══════════════════════════════════════════════════════════════════════════════

class TestRetrainHourUTC:
    """Tests for retrain_hour_utc = 1 (01:00 UTC)."""

    def test_config_retrain_hour_utc_is_1(self):
        """config.yaml should have feedback_loop.retrain_hour_utc = 1."""
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'bot', 'config.yaml')
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        assert cfg["feedback_loop"]["retrain_hour_utc"] == 1

    def test_retrain_blocked_before_01_utc(self, tmp_path: Path):
        """Should not retrain before 01:00 UTC."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 1,
            ("feedback_loop", "min_new_labels_for_retrain"): 10,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 200
        loop._state["last_retrain_attempt_date"] = ""
        
        # Hour 0 UTC (before 1)
        now = datetime(2026, 1, 15, 0, 30, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is False

    def test_retrain_allowed_at_01_utc(self, tmp_path: Path):
        """Should allow retrain at 01:00 UTC."""
        cfg = MockConfig({
            ("feedback_loop", "retrain_hour_utc"): 1,
            ("feedback_loop", "min_new_labels_for_retrain"): 10,
            ("feedback_loop", "state_path"): str(tmp_path / "state.json"),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        loop._state["quality_labels_since_retrain"] = 200
        loop._state["last_retrain_attempt_date"] = ""
        
        # Hour 1 UTC
        now = datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
        assert loop.should_run_daily_retrain(now) is True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 8: BACKWARD COMPATIBILITY - STATE MIGRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestStateMigration:
    """Tests for backward-compatible state migration."""

    def test_state_migration_adds_quality_labels_field(self, tmp_path: Path):
        """Old state without quality_labels_since_retrain should be migrated."""
        # Create old state file without quality_labels_since_retrain
        old_state = {
            "new_labels_since_retrain": 50,
            "last_retrain_attempt_date": "",
            "last_retrain_success_date": "",
        }
        state_path = tmp_path / "state.json"
        with open(state_path, "w") as f:
            json.dump(old_state, f)
        
        cfg = MockConfig({
            ("feedback_loop", "state_path"): str(state_path),
        })
        loop = SignalFeedbackLoop(tmp_path, cfg)
        
        # Should have migrated quality_labels_since_retrain
        assert "quality_labels_since_retrain" in loop._state
        # Should be initialized from new_labels_since_retrain
        assert loop._state["quality_labels_since_retrain"] == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
