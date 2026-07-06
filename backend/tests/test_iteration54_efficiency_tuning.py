#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

BOT_MAIN_PATH = Path(__file__).resolve().parents[2] / "bot" / "main.py"
BOT_ENTRY_PATH = Path(__file__).resolve().parents[2] / "bot" / "engine" / "entry_engine.py"
BOT_RISK_PATH = Path(__file__).resolve().parents[2] / "bot" / "engine" / "risk_manager.py"
BOT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "bot" / "config.yaml"


def test_config_has_efficiency_tuning_keys():
    source = BOT_CONFIG_PATH.read_text(encoding="utf-8")
    assert "ema_exit_buffer_pct: 0.15" in source
    assert "buy_momentum_guard_min_pct: 0.35" in source
    assert "buy_volume_guard_min_ratio: 0.50" in source
    assert "early_exit_bars: 65" in source
    assert "early_exit_min_profit_atr: 0.35" in source
    assert "symbol_loss_streak_threshold: 2" in source
    assert "symbol_loss_streak_cooldown_sec: 28800" in source


def test_entry_engine_has_asymmetric_buy_guards():
    source = BOT_ENTRY_PATH.read_text(encoding="utf-8")
    assert 'self.buy_momentum_guard_min_pct = float(' in source
    assert 'self.buy_volume_guard_min_ratio = float(' in source
    assert 'buy_threshold = max(self.buy_momentum_guard_min_pct, 0.0)' in source
    assert 'min_ratio = self.buy_volume_guard_min_ratio if is_long else self.sell_volume_guard_min_ratio' in source


def test_risk_guard_has_symbol_loss_streak_cooldown():
    source = BOT_RISK_PATH.read_text(encoding="utf-8")
    assert "self.symbol_loss_streak_threshold = max(int(resolved_streak_threshold), 0)" in source
    assert "self.symbol_loss_streak_cooldown_enabled = bool(symbol_loss_streak_cooldown_enabled)" in source
    assert "self._symbol_consecutive_losses: Dict[str, int] = {}" in source
    assert "self._symbol_streak_cooldown_until: Dict[str, datetime] = {}" in source
    assert "Symbol streak cooldown" in source


def test_main_wires_new_risk_and_entry_tuning():
    source = BOT_MAIN_PATH.read_text(encoding="utf-8")
    assert 'symbol_loss_streak_cooldown_enabled=bool(' in source
    assert 'symbol_loss_streak_threshold=int(' in source
    assert '"risk", "symbol_loss_streak_limit", default=2' in source
    assert '"risk", "symbol_loss_streak_cooldown_sec", default=21600' in source
