#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, rel_path: str):
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


risk_manager_module = _load_module("risk_manager_module", "bot/engine/risk_manager.py")
main_module = _load_module("trading_bot_main_module", "bot/main.py")

RiskGuard = risk_manager_module.RiskGuard
TradingBot = main_module.TradingBot


def test_classify_exchange_closed_reason_tp_sl_manual():
    assert TradingBot._classify_exchange_closed_reason([]) == "exchange_closed_manual"
    assert (
        TradingBot._classify_exchange_closed_reason(
            [{"stopOrderType": "TakeProfit", "execType": "Trade"}]
        )
        == "exchange_closed_tp_hit"
    )
    assert (
        TradingBot._classify_exchange_closed_reason(
            [{"stopOrderType": "StopLoss", "execType": "Trade"}]
        )
        == "exchange_closed_sl_hit"
    )
    assert (
        TradingBot._classify_exchange_closed_reason(
            [{"execType": "Manual", "orderType": "Market"}]
        )
        == "exchange_closed_manual"
    )


def test_exchange_closed_subreasons_are_ignored_by_risk_guard():
    guard = RiskGuard(
        max_consecutive_losses=3,
        max_daily_loss_pct=50.0,
        max_daily_loss_usdt=9999.0,
        max_trades_per_day=100,
        max_positions=5,
        cooldown_after_loss_sec=3600,
        min_loss_usdt_for_cooldown=0.1,
        min_loss_usdt_for_consecutive=0.1,
        ignore_loss_cooldown_reasons=["exchange_closed"],
        ignore_consecutive_loss_reasons=["exchange_closed"],
    )

    guard.record_trade(-1.0, symbol="XAGUSDT", reason="exchange_closed_tp_hit")
    assert guard.day_stats.consecutive_losses == 0
    assert guard.last_loss_time is None

    guard.record_trade(-1.0, symbol="XAGUSDT", reason="exchange_closed_sl_hit")
    assert guard.day_stats.consecutive_losses == 0
    assert guard.last_loss_time is None

    guard.record_trade(-1.0, symbol="XAGUSDT", reason="hard_sl")
    assert guard.day_stats.consecutive_losses == 1
    assert guard.last_loss_time is not None
