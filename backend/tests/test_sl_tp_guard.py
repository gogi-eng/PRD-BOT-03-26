#!/usr/bin/env python3
"""SL/TP guard: missing exchange levels → restore via trading-stop path."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from prd_agent.positions.sl_tp_guard import (
    LOG_MARKER,
    MISSING_MARKER,
    SlTpExchangeGuard,
    SlTpGuardConfig,
    compute_fallback_levels,
    exchange_sl_tp,
    missing_sides,
    should_guard_origin,
)
from prd_agent.positions.position_steward import PositionSteward


def test_config_from_yaml_positions_block():
    cfg = {
        "positions": {
            "sl_tp_guard": {
                "enabled": True,
                "interval_sec": 30,
                "include_manual": True,
                "default_sl_pct": 0.8,
                "default_tp_pct": 1.6,
                "min_rr": 2.5,
            }
        }
    }
    g = SlTpGuardConfig.from_cfg(cfg)
    assert g.enabled is True
    assert g.interval_sec == 30
    assert g.include_manual is True
    assert g.default_sl_pct == 0.8
    assert g.default_tp_pct == 1.6
    assert g.min_rr == 2.5


def test_exchange_sl_tp_and_missing_sides():
    sl, tp = exchange_sl_tp({"stopLoss": "0", "takeProfit": ""})
    assert sl == 0.0 and tp == 0.0
    need_sl, need_tp = missing_sides(sl, tp)
    assert need_sl and need_tp
    sl2, tp2 = exchange_sl_tp({"stopLoss": "100.5", "takeProfit": "120"})
    assert sl2 == 100.5 and tp2 == 120.0
    assert missing_sides(sl2, tp2) == (False, False)


def test_compute_fallback_uses_bot_levels_and_rr():
    sl, tp = compute_fallback_levels(
        side="Buy",
        entry=100.0,
        bot_sl=99.0,
        bot_tp=101.0,
        default_sl_pct=0.5,
        default_tp_pct=1.0,
        min_rr=2.0,
    )
    # risk=1 → TP at least entry+2
    assert sl == 99.0
    assert tp >= 102.0 - 1e-9


def test_compute_fallback_defaults_pct():
    sl, tp = compute_fallback_levels(
        side="Sell",
        entry=100.0,
        bot_sl=0.0,
        bot_tp=0.0,
        default_sl_pct=0.5,
        default_tp_pct=1.0,
        min_rr=2.0,
    )
    assert abs(sl - 100.5) < 1e-9
    # risk 0.5 → min_rr 2 → tp distance 1.0 → tp=99.0
    assert abs(tp - 99.0) < 1e-9


def test_should_guard_origin_manual_flag():
    assert should_guard_origin("bot", False) is True
    assert should_guard_origin("manual", False) is False
    assert should_guard_origin("manual", True) is True


class _FakeClient:
    def __init__(self) -> None:
        self.sl_calls: List[Any] = []
        self.tp_calls: List[Any] = []

    async def update_stop_loss(self, symbol, stop_loss, position_idx=0):
        self.sl_calls.append((symbol, stop_loss, position_idx))
        return {"success": True, "error": ""}

    async def update_take_profit(self, symbol, take_profit, position_idx=0):
        self.tp_calls.append((symbol, take_profit, position_idx))
        return {"success": True, "error": ""}


class _FakeExchange:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client


def test_ensure_restores_missing_sl_and_tp():
    guard = SlTpExchangeGuard()
    guard.apply_config(
        SlTpGuardConfig(
            enabled=True,
            interval_sec=1,
            include_manual=True,
            default_sl_pct=0.5,
            default_tp_pct=1.0,
            min_rr=2.0,
        )
    )
    client = _FakeClient()
    exchange = _FakeExchange(client)
    positions = [
        {
            "symbol": "BTCUSDT",
            "side": "Buy",
            "size": "0.01",
            "avgPrice": "50000",
            "markPrice": "50100",
            "stopLoss": "0",
            "takeProfit": "0",
            "positionIdx": 0,
        }
    ]
    notes = asyncio.run(
        guard.ensure(
            exchange,
            positions,
            bot_levels={"BTCUSDT": {"stop_loss": 49000.0, "take_profit": 52000.0}},
            bot_symbols={"BTCUSDT"},
        )
    )
    assert client.sl_calls and client.tp_calls
    assert client.sl_calls[0][0] == "BTCUSDT"
    assert abs(client.sl_calls[0][1] - 49000.0) < 1e-6
    assert abs(client.tp_calls[0][1] - 52000.0) < 1e-6
    assert any(LOG_MARKER in n for n in notes)


def test_ensure_skips_when_both_present():
    guard = SlTpExchangeGuard()
    guard.apply_config(SlTpGuardConfig(enabled=True, interval_sec=1))
    client = _FakeClient()
    notes = asyncio.run(
        guard.ensure(
            _FakeExchange(client),
            [
                {
                    "symbol": "ETHUSDT",
                    "side": "Sell",
                    "size": "1",
                    "avgPrice": "3000",
                    "stopLoss": "3100",
                    "takeProfit": "2800",
                }
            ],
            bot_levels={},
            bot_symbols=set(),
        )
    )
    assert client.sl_calls == []
    assert client.tp_calls == []
    assert notes == []


def test_steward_reads_sl_tp_guard_config(tmp_path):
    cfg: Dict[str, Any] = {
        "_root": str(tmp_path),
        "positions": {
            "trailing_enabled": False,
            "adopt_manual": True,
            "sl_tp_guard": {
                "enabled": True,
                "interval_sec": 45,
                "include_manual": True,
                "default_sl_pct": 0.7,
                "default_tp_pct": 1.4,
                "min_rr": 2.0,
            },
            "exit_management": {"enabled": False},
            "tp_progress_exit": {"enabled": False},
        },
    }
    steward = PositionSteward(cfg)
    assert steward._sl_tp_guard_cfg.enabled is True
    assert steward._sl_tp_guard_cfg.interval_sec == 45
    assert steward._sl_tp_guard_cfg.default_sl_pct == 0.7


def test_markers_are_stable_strings():
    assert "SL/TP guard" in LOG_MARKER
    assert "Missing SL/TP" in MISSING_MARKER
