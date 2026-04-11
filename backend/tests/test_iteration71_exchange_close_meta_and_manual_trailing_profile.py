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


main_module = _load_module("trading_bot_main_module_iter71", "bot/main.py")
TradingBot = main_module.TradingBot


def test_exchange_close_meta_contains_expected_bybit_fields():
    bot = TradingBot.__new__(TradingBot)
    bot._last_exchange_close_meta = {}
    bot._set_exchange_close_meta(
        "HYPEUSDT",
        [
            {
                "execType": "Trade",
                "stopOrderType": "StopLoss",
                "orderType": "Market",
                "createType": "CreateByStopLoss",
                "closeType": "Unknown",
                "orderFilter": "StopOrder",
                "orderLinkId": "abc-123",
                "updatedTime": "1000",
                "createdTime": "900",
            }
        ],
    )
    meta = bot._pop_exchange_close_meta("HYPEUSDT")
    assert meta["execType"] == "Trade"
    assert meta["stopOrderType"] == "StopLoss"
    assert meta["orderType"] == "Market"
    assert meta["createType"] == "CreateByStopLoss"
    assert meta["orderFilter"] == "StopOrder"
    assert meta["orderLinkId"] == "abc-123"
    assert meta["updatedTime"] == "1000"
    assert meta["createdTime"] == "900"


def test_config_manual_trailing_profile_is_more_conservative_than_auto():
    cfg = (ROOT / "bot" / "config.yaml").read_text(encoding="utf-8")
    assert "manual_management:" in cfg
    assert "trailing_activation_atr: 1.8" in cfg
    assert "trailing_distance_atr: 2.2" in cfg
    assert "trailing_min_distance_pct: 1.6" in cfg
