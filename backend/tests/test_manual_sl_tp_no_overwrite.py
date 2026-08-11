#!/usr/bin/env python3
"""Manual positions: bot must not overwrite user SL/TP when manage_sl_tp_manual=false."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock

from prd_agent.positions.position_steward import PositionSteward, TrackedPosition


class _FakeClient:
    def __init__(self) -> None:
        self.sl_calls: List[Any] = []
        self.tp_calls: List[Any] = []

    async def update_stop_loss(self, symbol, stop_loss, position_idx=0, take_profit=None):
        self.sl_calls.append(
            {
                "symbol": symbol,
                "stop_loss": stop_loss,
                "position_idx": position_idx,
                "take_profit": take_profit,
            }
        )
        return {"success": True, "error": ""}

    async def update_take_profit(self, symbol, take_profit, position_idx=0):
        self.tp_calls.append((symbol, take_profit, position_idx))
        return {"success": True, "error": ""}


class _FakeExchange:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get_klines(self, symbol, interval="15", limit=80):
        # flat candles → no adaptive surprise; ATR small
        rows = []
        px = 100.0
        for i in range(40):
            rows.append(
                {
                    "high": px + 0.1,
                    "low": px - 0.1,
                    "close": px,
                    "open": px,
                }
            )
        return rows


def _base_cfg(tmp_path, *, manage_manual: bool = False) -> Dict[str, Any]:
    return {
        "_root": str(tmp_path),
        "positions": {
            "trailing_enabled": True,
            "adopt_manual": True,
            "manual_auto_close": False,
            "manage_sl_tp_manual": manage_manual,
            "trailing_activation_pct": 0.1,
            "trailing_distance_pct": 0.5,
            "trailing_distance_atr_mult": 0.1,
            "trailing_min_distance_pct": 0.1,
            "breakeven_after_pct": 0.1,
            "sl_tp_guard": {"enabled": False},
            "exit_management": {"enabled": False},
            "tp_progress_exit": {"enabled": False},
            "adaptive_trailing": {"enabled": False, "apply_to_manual": False},
            "trailing_after_be": {"enabled": False},
            "liquidation_guard": {"enabled": False},
        },
        "manual_management": {"manage_sl_tp": manage_manual},
    }


def test_steward_reads_manage_sl_tp_manual_false(tmp_path):
    steward = PositionSteward(_base_cfg(tmp_path, manage_manual=False))
    assert steward.manage_sl_tp_manual is False


def test_manual_position_no_sl_overwrite(tmp_path):
    steward = PositionSteward(_base_cfg(tmp_path, manage_manual=False))
    client = _FakeClient()
    exchange = _FakeExchange(client)
    steward._tracked["AAAUSDT"] = TrackedPosition(
        symbol="AAAUSDT",
        side="Buy",
        entry=100.0,
        qty=1.0,
        stop_loss=95.0,
        take_profit=110.0,
        best_price=100.0,
        origin="manual",
        last_sl_sent=95.0,
        opened_at_utc="2026-08-11T10:00:00+00:00",
    )
    positions = [
        {
            "symbol": "AAAUSDT",
            "side": "Buy",
            "size": "1",
            "avgPrice": "100",
            "markPrice": "108",  # strong profit → trailing would fire if allowed
            "stopLoss": "97",  # user-set on exchange
            "takeProfit": "112",
            "positionIdx": 0,
        }
    ]
    notes = asyncio.run(steward.manage(exchange, positions))
    assert client.sl_calls == []
    assert client.tp_calls == []
    # exchange levels synced into tracked
    t = steward._tracked["AAAUSDT"]
    assert abs(t.stop_loss - 97.0) < 1e-9
    assert abs(t.take_profit - 112.0) < 1e-9


def test_bot_position_trailing_keeps_tp(tmp_path):
    steward = PositionSteward(_base_cfg(tmp_path, manage_manual=False))
    steward._bot_symbols.add("BBBUSDT")
    client = _FakeClient()
    exchange = _FakeExchange(client)
    steward._tracked["BBBUSDT"] = TrackedPosition(
        symbol="BBBUSDT",
        side="Buy",
        entry=100.0,
        qty=1.0,
        stop_loss=95.0,
        take_profit=120.0,
        best_price=108.0,
        origin="bot",
        last_sl_sent=95.0,
        opened_at_utc="2026-08-11T10:00:00+00:00",
    )
    positions = [
        {
            "symbol": "BBBUSDT",
            "side": "Buy",
            "size": "1",
            "avgPrice": "100",
            "markPrice": "108",
            "stopLoss": "95",
            "takeProfit": "120",
            "positionIdx": 0,
        }
    ]
    asyncio.run(steward.manage(exchange, positions))
    assert client.sl_calls, "bot trailing should be allowed to tighten SL"
    assert client.sl_calls[0]["take_profit"] == 120.0
    assert float(client.sl_calls[0]["stop_loss"]) > 0


def test_bybit_client_refuses_clear_sl_tp():
    from exchange.bybit_client import BybitClient

    client = BybitClient.__new__(BybitClient)
    client.category = "linear"
    client._request = AsyncMock(return_value={"retCode": 0})

    async def _run():
        bad_sl = await BybitClient.update_stop_loss(client, "XUSDT", 0)
        bad_tp = await BybitClient.update_take_profit(client, "XUSDT", 0)
        ok = await BybitClient.update_stop_loss(
            client, "XUSDT", 10.5, take_profit=12.0
        )
        return bad_sl, bad_tp, ok, client._request.await_args_list

    bad_sl, bad_tp, ok, calls = asyncio.run(_run())
    assert bad_sl["success"] is False
    assert bad_tp["success"] is False
    assert ok["success"] is True
    # one real trading-stop call with both levels
    assert len(calls) == 1
    params = calls[0].args[2]
    assert params["stopLoss"] == "10.5"
    assert params["takeProfit"] == "12.0"
