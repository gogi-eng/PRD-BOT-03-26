"""Сырой Bybit-клиент принимает аргументы адаптера и всё равно отдаёт стакан."""
from __future__ import annotations

import asyncio

from exchange.bybit_client import BybitClient


def test_orderbook_and_trades_accept_lazy_kwargs() -> None:
    client = BybitClient("k", "s", testnet=True)
    seen: dict = {}

    async def fake_request(method, path, params=None, private=False):
        seen["path"] = path
        seen["params"] = params
        if "orderbook" in path:
            return {"b": [["100", "1"]], "a": [["101", "2"]], "ts": 5}
        return {"list": [{"price": "100", "size": "1", "side": "Buy", "time": "1"}]}

    client._request = fake_request  # type: ignore[method-assign]
    client._orderbook_from_cache = lambda sym, limit: None  # type: ignore[method-assign]

    async def run() -> None:
        book = await client.get_orderbook(
            "btcusdt",
            limit=5,
            lazy=False,
            signal_passed_cheap_filters=True,
        )
        trades = await client.get_recent_trades(
            "BTCUSDT",
            limit=5,
            lazy=True,
            signal_passed_cheap_filters=False,
        )
        assert book["bids"] == [["100", "1"]]
        assert book["source"] == "rest"
        assert len(trades) == 1
        assert seen["params"]["symbol"] == "BTCUSDT"

    asyncio.run(run())
