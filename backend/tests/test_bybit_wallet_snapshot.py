#!/usr/bin/env python3
"""Тесты устойчивого чтения wallet-balance (balance=0 при ошибке API)."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from exchange.bybit_client import BybitClient


class _FakeBybit(BybitClient):
    def __init__(self) -> None:
        # Не вызываем полный __init__ с сетью
        self._calls = 0
        self._responses: list = []

    def set_responses(self, *responses: Any) -> None:
        self._responses = list(responses)
        self._calls = 0

    async def _request(self, method, endpoint, params=None, private=False, signed=False, retries=4, return_full=False):
        idx = self._calls
        self._calls += 1
        if idx < len(self._responses):
            return self._responses[idx]
        return self._responses[-1] if self._responses else None


def test_wallet_snapshot_retries_on_error_then_ok():
    client = _FakeBybit()
    client.set_responses(
        {"_error": "circuit open", "_code": "circuit_open"},
        {
            "list": [
                {
                    "totalAvailableBalance": "19.11",
                    "totalEquity": "19.11",
                    "coin": [{"coin": "USDT", "walletBalance": "19.11", "availableBalance": "19.11"}],
                }
            ]
        },
    )
    snap = asyncio.get_event_loop().run_until_complete(client.get_wallet_snapshot())
    assert snap["wallet_balance"] == 19.11
    assert snap["available_balance"] == 19.11
    assert client._calls == 2


def test_wallet_snapshot_error_returns_marker():
    client = _FakeBybit()
    client.set_responses(
        {"_error": "sign error", "_code": 10004},
        {"_error": "sign error", "_code": 10004},
    )
    snap = asyncio.get_event_loop().run_until_complete(client.get_wallet_snapshot())
    assert snap["wallet_balance"] == 0.0
    assert snap.get("_error")
    assert snap.get("_code") == 10004


def test_get_balance_falls_back_to_equity():
    client = _FakeBybit()
    client.set_responses(
        {
            "list": [
                {
                    "totalAvailableBalance": "0",
                    "totalEquity": "19.11",
                    "coin": [{"coin": "USDT", "walletBalance": "0", "equity": "0"}],
                }
            ]
        }
    )
    bal = asyncio.get_event_loop().run_until_complete(client.get_balance())
    assert bal == 19.11


def test_empty_list_logs_zero():
    client = _FakeBybit()
    client.set_responses({})
    snap = asyncio.get_event_loop().run_until_complete(client.get_wallet_snapshot())
    assert snap["wallet_balance"] == 0.0
    assert snap.get("_error") == "empty_wallet_list"
