#!/usr/bin/env python3
"""
Iteration 27: Signal Mode Toggle Feature Tests

Features tested:
1. Telegram menu has mode switch button
2. Pressing button opens safe confirmation UI
3. Confirm action invokes mode switch callback
4. Mode switch updates runtime flag and persists bot.signal_only in config.yaml
5. Menu reflects execution mode clearly (SIGNAL-ONLY/LIVE)
6. No regression in adaptive preset / strict htf / volatility features
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import main as main_module
from main import TradingBot
from tg.controller import TelegramController
from core.live_controls import LiveControls


# ============================================================================
# Test Fixtures
# ============================================================================

class DummyControls:
    """Minimal controls mock for testing."""
    def __init__(self, signal_only: bool = True):
        self.signal_only = signal_only
        self.enabled = True
        self.dry_run = False
        self.emergency = False
        self.ai_enabled = True
        self.rl_enabled = True
        self.risk_per_trade_pct = 0.5
        self.leverage = 10
        self.margin_total_pct = 10.0
        self.tp_pct = 3.0
        self.sl_pct = 1.5
        self.max_positions = 3
        self.trailing_stop_pct = 2.0
        self._guard = None

    def guard_snapshot(self):
        return {"pnl_today": 0.0, "trades_today": 0, "blocked": False, "block_reason": ""}


@pytest.fixture
def dummy_controls():
    return DummyControls(signal_only=True)


@pytest.fixture
def config_file(tmp_path):
    """Create a temporary config file."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"bot": {"signal_only": True}}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return config_path


# ============================================================================
# Test 1: Telegram menu has mode switch button
# ============================================================================

class TestTelegramMenuHasModeSwitch:
    """Verify Telegram menu includes mode switch button."""

    def test_build_keyboard_has_mode_switch_button(self, dummy_controls):
        """_build_keyboard should include a button with SWITCH_MODE_PROMPT callback."""
        tg = TelegramController.__new__(TelegramController)
        tg.controls = dummy_controls
        tg._profit_lock = None

        keyboard = tg._build_keyboard()
        
        # Flatten all buttons from all rows
        all_buttons = []
        for row in keyboard.inline_keyboard:
            for btn in row:
                all_buttons.append(btn)
        
        # Find button with SWITCH_MODE_PROMPT callback
        mode_buttons = [b for b in all_buttons if b.callback_data == "SWITCH_MODE_PROMPT"]
        assert len(mode_buttons) == 1, "Should have exactly one SWITCH_MODE_PROMPT button"

    def test_mode_button_shows_signal_only_when_signal_only_true(self, dummy_controls):
        """Button should show 'SIGNAL-ONLY' when signal_only=True."""
        dummy_controls.signal_only = True
        tg = TelegramController.__new__(TelegramController)
        tg.controls = dummy_controls
        tg._profit_lock = None

        keyboard = tg._build_keyboard()
        
        all_buttons = []
        for row in keyboard.inline_keyboard:
            for btn in row:
                all_buttons.append(btn)
        
        mode_buttons = [b for b in all_buttons if b.callback_data == "SWITCH_MODE_PROMPT"]
        assert "SIGNAL-ONLY" in mode_buttons[0].text

    def test_mode_button_shows_live_when_signal_only_false(self, dummy_controls):
        """Button should show 'LIVE' when signal_only=False."""
        dummy_controls.signal_only = False
        tg = TelegramController.__new__(TelegramController)
        tg.controls = dummy_controls
        tg._profit_lock = None

        keyboard = tg._build_keyboard()
        
        all_buttons = []
        for row in keyboard.inline_keyboard:
            for btn in row:
                all_buttons.append(btn)
        
        mode_buttons = [b for b in all_buttons if b.callback_data == "SWITCH_MODE_PROMPT"]
        assert "LIVE" in mode_buttons[0].text


# ============================================================================
# Test 2: Pressing button opens safe confirmation UI
# ============================================================================

class TestConfirmationUI:
    """Verify confirmation dialog is shown before mode switch."""

    def test_on_button_source_has_switch_mode_prompt_handler(self):
        """on_button should handle SWITCH_MODE_PROMPT action."""
        source = inspect.getsource(TelegramController.on_button)
        assert "SWITCH_MODE_PROMPT" in source

    def test_on_button_source_shows_confirmation_keyboard(self):
        """SWITCH_MODE_PROMPT should create confirmation keyboard."""
        source = inspect.getsource(TelegramController.on_button)
        # Check for confirmation buttons
        assert "SWITCH_MODE_CONFIRM_SIGNAL" in source
        assert "SWITCH_MODE_CONFIRM_LIVE" in source
        assert "SWITCH_MODE_CANCEL" in source

    def test_confirmation_message_shows_current_and_target_mode(self):
        """Confirmation message should show current and target mode."""
        source = inspect.getsource(TelegramController.on_button)
        # Check for mode labels in confirmation message
        assert "Текущий режим" in source or "current" in source.lower()
        assert "Новый режим" in source or "target" in source.lower()


# ============================================================================
# Test 3: Confirm action invokes mode switch callback
# ============================================================================

class TestConfirmActionInvokesCallback:
    """Verify confirm action calls mode_switcher callback."""

    def test_controller_accepts_mode_switcher_parameter(self):
        """TelegramController should accept mode_switcher in __init__."""
        sig = inspect.signature(TelegramController.__init__)
        params = list(sig.parameters.keys())
        assert "mode_switcher" in params

    def test_controller_stores_mode_switcher(self, dummy_controls):
        """Controller should store mode_switcher callback."""
        def mock_switcher(signal_only: bool):
            return True, "OK"

        tg = TelegramController(
            token="test_token",
            controls=dummy_controls,
            mode_switcher=mock_switcher,
        )
        assert tg.mode_switcher == mock_switcher

    def test_on_button_calls_mode_switcher_on_confirm(self):
        """SWITCH_MODE_CONFIRM_* should call mode_switcher."""
        source = inspect.getsource(TelegramController.on_button)
        # Check that mode_switcher is called
        assert "self.mode_switcher" in source
        assert "mode_switcher(" in source


# ============================================================================
# Test 4: Mode switch updates runtime flag and persists to config.yaml
# ============================================================================

class TestModeSwitchPersistence:
    """Verify mode switch updates runtime and persists to config."""

    def test_switch_signal_mode_updates_bot_signal_only(self, tmp_path, monkeypatch):
        """_switch_signal_mode should update bot.signal_only."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"bot": {"signal_only": True}}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = True
        bot.controls = DummyControls(signal_only=True)

        ok, msg = bot._switch_signal_mode(False)
        
        assert ok is True
        assert bot.signal_only is False

    def test_switch_signal_mode_updates_controls_signal_only(self, tmp_path, monkeypatch):
        """_switch_signal_mode should update controls.signal_only."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"bot": {"signal_only": True}}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = True
        bot.controls = DummyControls(signal_only=True)

        ok, msg = bot._switch_signal_mode(False)
        
        assert ok is True
        assert bot.controls.signal_only is False

    def test_switch_signal_mode_persists_to_config_yaml(self, tmp_path, monkeypatch):
        """_switch_signal_mode should write to config.yaml."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"bot": {"signal_only": True}}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = True
        bot.controls = DummyControls(signal_only=True)

        ok, msg = bot._switch_signal_mode(False)
        
        # Read config and verify
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert cfg["bot"]["signal_only"] is False

    def test_switch_to_signal_only_persists_true(self, tmp_path, monkeypatch):
        """Switching to SIGNAL-ONLY should persist True."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"bot": {"signal_only": False}}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = False
        bot.controls = DummyControls(signal_only=False)

        ok, msg = bot._switch_signal_mode(True)
        
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert cfg["bot"]["signal_only"] is True

    def test_switch_noop_when_already_in_target_mode(self, tmp_path, monkeypatch):
        """Should return early if already in target mode."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"bot": {"signal_only": True}}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = True
        bot.controls = DummyControls(signal_only=True)

        ok, msg = bot._switch_signal_mode(True)
        
        assert ok is True
        assert "уже" in msg.lower()  # "already" in Russian

    def test_switch_returns_live_in_message_when_switching_to_live(self, tmp_path, monkeypatch):
        """Message should contain 'LIVE' when switching to LIVE mode."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"bot": {"signal_only": True}}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = True
        bot.controls = DummyControls(signal_only=True)

        ok, msg = bot._switch_signal_mode(False)
        
        assert "LIVE" in msg

    def test_switch_returns_signal_only_in_message_when_switching_to_signal(self, tmp_path, monkeypatch):
        """Message should contain 'SIGNAL-ONLY' when switching to SIGNAL-ONLY mode."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump({"bot": {"signal_only": False}}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = False
        bot.controls = DummyControls(signal_only=False)

        ok, msg = bot._switch_signal_mode(True)
        
        assert "SIGNAL-ONLY" in msg


# ============================================================================
# Test 5: Menu reflects execution mode clearly
# ============================================================================

class TestMenuReflectsMode:
    """Verify menu text shows execution mode clearly."""

    def test_menu_text_shows_signal_only_execution_mode(self, dummy_controls):
        """_menu_text should show 'SIGNAL-ONLY' when signal_only=True."""
        dummy_controls.signal_only = True
        tg = TelegramController.__new__(TelegramController)
        tg.controls = dummy_controls
        tg._profit_lock = None

        text = tg._menu_text()
        
        assert "SIGNAL-ONLY" in text

    def test_menu_text_shows_live_execution_mode(self, dummy_controls):
        """_menu_text should show 'LIVE' when signal_only=False."""
        dummy_controls.signal_only = False
        tg = TelegramController.__new__(TelegramController)
        tg.controls = dummy_controls
        tg._profit_lock = None

        text = tg._menu_text()
        
        # Should show LIVE for execution mode
        assert "Исполнение:" in text or "execution" in text.lower()
        # The execution mode line should contain LIVE
        lines = text.split("\n")
        execution_line = [l for l in lines if "Исполнение" in l or "execution" in l.lower()]
        assert len(execution_line) > 0
        assert "LIVE" in execution_line[0]

    def test_menu_text_has_execution_mode_label(self, dummy_controls):
        """_menu_text should have an 'Исполнение' (Execution) label."""
        tg = TelegramController.__new__(TelegramController)
        tg.controls = dummy_controls
        tg._profit_lock = None

        text = tg._menu_text()
        
        assert "Исполнение" in text


# ============================================================================
# Test 6: No regression in adaptive preset / strict htf / volatility features
# ============================================================================

class TestNoRegressionAdaptivePresets:
    """Verify adaptive preset features still work."""

    def test_resolve_regime_preset_exists(self):
        """_resolve_regime_preset method should exist."""
        assert hasattr(TradingBot, "_resolve_regime_preset")

    def test_resolve_regime_preset_trend(self, tmp_path, monkeypatch):
        """Trend regime should map to trend profile."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.adaptive_trend_strict_htf_mode = True
        bot.adaptive_trend_volatility_floor_atr_pct = 0.8
        bot.adaptive_range_strict_htf_mode = True
        bot.adaptive_range_volatility_floor_atr_pct = 1.0

        profile, strict_htf, vol_floor = bot._resolve_regime_preset("trend")
        
        assert profile == "trend"
        assert strict_htf is True
        assert vol_floor == 0.8

    def test_resolve_regime_preset_range(self, tmp_path, monkeypatch):
        """Range regime should map to range profile."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.adaptive_trend_strict_htf_mode = True
        bot.adaptive_trend_volatility_floor_atr_pct = 0.8
        bot.adaptive_range_strict_htf_mode = True
        bot.adaptive_range_volatility_floor_atr_pct = 1.0

        profile, strict_htf, vol_floor = bot._resolve_regime_preset("range")
        
        assert profile == "range"
        assert strict_htf is True
        assert vol_floor == 1.0


class TestNoRegressionStrictHTF:
    """Verify strict HTF mode features still work."""

    def test_passes_strict_htf_mode_exists(self):
        """_passes_strict_htf_mode method should exist."""
        assert hasattr(TradingBot, "_passes_strict_htf_mode")

    def test_strict_htf_blocks_buy_in_bear(self, tmp_path, monkeypatch):
        """Strict HTF should block BUY in bearish 4H trend."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.strict_htf_mode = True

        ok, reason = bot._passes_strict_htf_mode("BUY", -1)
        
        assert ok is False
        assert "strict_htf" in reason

    def test_strict_htf_blocks_sell_in_bull(self, tmp_path, monkeypatch):
        """Strict HTF should block SELL in bullish 4H trend."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.strict_htf_mode = True

        ok, reason = bot._passes_strict_htf_mode("SELL", 1)
        
        assert ok is False
        assert "strict_htf" in reason

    def test_strict_htf_allows_aligned_direction(self, tmp_path, monkeypatch):
        """Strict HTF should allow aligned direction."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.strict_htf_mode = True

        ok_buy, _ = bot._passes_strict_htf_mode("BUY", 1)
        ok_sell, _ = bot._passes_strict_htf_mode("SELL", -1)
        
        assert ok_buy is True
        assert ok_sell is True


class TestNoRegressionVolatilityFloor:
    """Verify volatility floor features still work."""

    def test_passes_volatility_floor_exists(self):
        """_passes_volatility_floor method should exist."""
        assert hasattr(TradingBot, "_passes_volatility_floor")

    def test_volatility_floor_blocks_low_atr(self, tmp_path, monkeypatch):
        """Volatility floor should block low ATR%."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.volatility_floor_enabled = True
        bot.volatility_floor_atr_pct = 0.8

        ok, reason = bot._passes_volatility_floor(0.5)
        
        assert ok is False
        assert "volatility_floor" in reason

    def test_volatility_floor_allows_high_atr(self, tmp_path, monkeypatch):
        """Volatility floor should allow high ATR%."""
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.volatility_floor_enabled = True
        bot.volatility_floor_atr_pct = 0.8

        ok, reason = bot._passes_volatility_floor(1.0)
        
        assert ok is True


# ============================================================================
# Test LiveControls signal_only attribute
# ============================================================================

class TestLiveControlsSignalOnly:
    """Verify LiveControls has signal_only attribute."""

    def test_live_controls_has_signal_only_attribute(self):
        """LiveControls should have signal_only attribute."""
        controls = LiveControls()
        assert hasattr(controls, "signal_only")

    def test_live_controls_signal_only_default_true(self):
        """LiveControls.signal_only should default to True."""
        controls = LiveControls()
        assert controls.signal_only is True

    def test_live_controls_signal_only_can_be_set(self):
        """LiveControls.signal_only should be settable."""
        controls = LiveControls()
        controls.signal_only = False
        assert controls.signal_only is False


# ============================================================================
# Test TradingBot mode_switcher wiring
# ============================================================================

class TestTradingBotModeSwitcherWiring:
    """Verify TradingBot wires mode_switcher to TelegramController."""

    def test_trading_bot_passes_mode_switcher_to_telegram_controller(self):
        """TradingBot should pass _switch_signal_mode to TelegramController."""
        source = inspect.getsource(TradingBot.__init__)
        # Check that mode_switcher is passed to TelegramController
        assert "mode_switcher" in source
        assert "_switch_signal_mode" in source


# ============================================================================
# Test Cancel action
# ============================================================================

class TestCancelAction:
    """Verify cancel action works correctly."""

    def test_on_button_handles_switch_mode_cancel(self):
        """on_button should handle SWITCH_MODE_CANCEL action."""
        source = inspect.getsource(TelegramController.on_button)
        assert "SWITCH_MODE_CANCEL" in source
        # Should send cancellation message
        assert "отменено" in source.lower() or "cancel" in source.lower()


# ============================================================================
# Test error handling in mode switch
# ============================================================================

class TestModeSwitchErrorHandling:
    """Verify error handling in mode switch."""

    def test_switch_handles_missing_config_gracefully(self, tmp_path, monkeypatch):
        """_switch_signal_mode should handle missing config gracefully."""
        # Point to non-existent config
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = True
        bot.controls = DummyControls(signal_only=True)

        # Should not raise, but return error
        ok, msg = bot._switch_signal_mode(False)
        
        # Either succeeds (creates file) or returns error message
        assert isinstance(ok, bool)
        assert isinstance(msg, str)

    def test_switch_handles_invalid_yaml_gracefully(self, tmp_path, monkeypatch):
        """_switch_signal_mode should handle invalid YAML gracefully."""
        config_path = tmp_path / "config.yaml"
        config_path.write_text("invalid: yaml: content: [", encoding="utf-8")
        monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)
        
        bot = TradingBot.__new__(TradingBot)
        bot.signal_only = True
        bot.controls = DummyControls(signal_only=True)

        # Should not raise
        ok, msg = bot._switch_signal_mode(False)
        
        assert isinstance(ok, bool)
        assert isinstance(msg, str)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
