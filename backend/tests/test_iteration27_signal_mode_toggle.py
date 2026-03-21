#!/usr/bin/env python3
from __future__ import annotations

import inspect
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'bot'))

import main as main_module
from main import TradingBot
from tg.controller import TelegramController


class DummyControls:
    def __init__(self, signal_only: bool):
        self.signal_only = signal_only


def test_switch_signal_mode_persists_to_config(tmp_path, monkeypatch):
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
    assert "LIVE" in msg
    assert bot.signal_only is False
    assert bot.controls.signal_only is False

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert cfg["bot"]["signal_only"] is False


def test_switch_signal_mode_noop_when_same_state(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump({"bot": {"signal_only": False}}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    monkeypatch.setattr(main_module, "BOT_DIR", tmp_path)

    bot = TradingBot.__new__(TradingBot)
    bot.signal_only = False
    bot.controls = DummyControls(signal_only=False)

    ok, msg = bot._switch_signal_mode(False)
    assert ok is True
    assert "уже" in msg.lower()


def test_telegram_controller_has_mode_switch_actions():
    source = inspect.getsource(TelegramController.on_button)
    assert "SWITCH_MODE_PROMPT" in source
    assert "SWITCH_MODE_CONFIRM_SIGNAL" in source
    assert "SWITCH_MODE_CONFIRM_LIVE" in source
