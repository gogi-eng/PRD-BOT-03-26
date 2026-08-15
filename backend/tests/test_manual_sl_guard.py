"""Тесты Manual SL guard: защитный SL на manual без стопа; trailing не отключается."""
from __future__ import annotations

import asyncio
from typing import Any, List

from prd_agent.positions.manual_sl_guard import (
    LOG_MARKER,
    MISSING_MARKER,
    ManualSlGuard,
    ManualSlGuardConfig,
    compute_protective_sl,
    exchange_has_sl,
)


def test_config_defaults_off():
    g = ManualSlGuardConfig.from_cfg({})
    assert g.enabled is False
    assert g.default_sl_pct == 1.0


def test_config_from_yaml():
    cfg = {
        "positions": {
            "manual_sl_guard": {
                "enabled": True,
                "default_sl_pct": 1.5,
                "interval_sec": 15,
                "once_per_position": True,
            }
        }
    }
    g = ManualSlGuardConfig.from_cfg(cfg)
    assert g.enabled is True
    assert g.default_sl_pct == 1.5
    assert g.interval_sec == 15


def test_compute_protective_sl_buy_sell():
    assert abs(compute_protective_sl(side="Buy", entry=100.0, default_sl_pct=1.0) - 99.0) < 1e-9
    assert abs(compute_protective_sl(side="Sell", entry=100.0, default_sl_pct=1.0) - 101.0) < 1e-9


def test_exchange_has_sl():
    assert exchange_has_sl({"stopLoss": "0"}) is False
    assert exchange_has_sl({"stopLoss": "99.5"}) is True


class _FakeClient:
    def __init__(self) -> None:
        self.sl_calls: List[Any] = []

    async def update_stop_loss(self, symbol, stop_loss, position_idx=0):
        self.sl_calls.append((symbol, stop_loss, position_idx))
        return {"success": True, "error": ""}


class _FakeEx:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client


def test_sets_sl_once_for_manual_without_sl():
    client = _FakeClient()
    guard = ManualSlGuard()
    guard.apply_config(
        ManualSlGuardConfig(enabled=True, default_sl_pct=1.0, interval_sec=1, once_per_position=True)
    )
    positions = [
        {
            "symbol": "AAAUSDT",
            "side": "Buy",
            "size": 1,
            "avgPrice": 100,
            "stopLoss": "0",
            "positionIdx": 0,
        }
    ]

    async def _run():
        n1 = await guard.ensure(
            _FakeEx(client), positions, bot_symbols=set(), origin_of=lambda s: "manual"
        )
        n2 = await guard.ensure(
            _FakeEx(client), positions, bot_symbols=set(), origin_of=lambda s: "manual"
        )
        return n1, n2

    n1, n2 = asyncio.run(_run())
    assert len(client.sl_calls) == 1
    assert client.sl_calls[0][0] == "AAAUSDT"
    assert abs(client.sl_calls[0][1] - 99.0) < 1e-9
    assert any(LOG_MARKER in x for x in n1)
    assert n2 == []


def test_skips_bot_and_existing_sl():
    client = _FakeClient()
    guard = ManualSlGuard()
    guard.apply_config(ManualSlGuardConfig(enabled=True, interval_sec=1))

    async def _run():
        await guard.ensure(
            _FakeEx(client),
            [
                {
                    "symbol": "BOTUSDT",
                    "side": "Buy",
                    "size": 1,
                    "avgPrice": 10,
                    "stopLoss": "0",
                }
            ],
            bot_symbols={"BOTUSDT"},
        )
        await guard.ensure(
            _FakeEx(client),
            [
                {
                    "symbol": "MANUSDT",
                    "side": "Sell",
                    "size": 1,
                    "avgPrice": 50,
                    "stopLoss": "51",
                }
            ],
            bot_symbols=set(),
            origin_of=lambda s: "manual",
        )

    asyncio.run(_run())
    assert client.sl_calls == []


def test_markers_exist():
    assert "Manual SL" in LOG_MARKER
    assert "Manual SL" in MISSING_MARKER
