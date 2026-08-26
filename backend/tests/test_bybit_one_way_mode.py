"""One-way Bybit: never send hedge positionIdx 1/2."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from exchange.bybit_client import BybitClient


@pytest.mark.asyncio
async def test_place_order_always_sends_position_idx_zero():
    client = BybitClient("k", "s", testnet=False)
    captured: Dict[str, Any] = {}

    async def fake_request(method, endpoint, params=None, private=False, signed=False, retries=4, return_full=False):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["params"] = dict(params or {})
        return {"orderId": "oid-1"}

    client._request = fake_request  # type: ignore[method-assign]

    result = await client.place_order(
        symbol="XRPUSDT",
        side="Buy",
        qty=10,
        order_type="Market",
        position_idx=1,
    )
    assert result["success"] is True
    assert captured["endpoint"] == "/v5/order/create"
    assert captured["params"].get("positionIdx") == 0


@pytest.mark.asyncio
async def test_ensure_one_way_mode_calls_switch_mode_zero():
    client = BybitClient("k", "s", testnet=False)
    calls = []

    async def fake_request(method, endpoint, params=None, private=False, signed=False, retries=4, return_full=False):
        calls.append({"method": method, "endpoint": endpoint, "params": dict(params or {}), "return_full": return_full})
        if endpoint == "/v5/position/switch-mode":
            if return_full:
                return {"retCode": 0, "retMsg": "OK", "result": {}}
            return {}
        if endpoint == "/v5/position/list":
            return {"list": [{"symbol": "BTCUSDT", "positionIdx": 0, "size": "0"}]}
        return {}

    client._request = fake_request  # type: ignore[method-assign]
    info = await client.ensure_one_way_mode()
    assert info["ok"] is True
    assert info["mode"] == "one_way"
    switch_calls = [c for c in calls if c["endpoint"] == "/v5/position/switch-mode"]
    assert switch_calls
    assert switch_calls[0]["params"].get("mode") == 0
