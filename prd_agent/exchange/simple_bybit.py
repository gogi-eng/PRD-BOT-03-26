"""Минимальный Bybit v5 клиент (fallback, если PRD-репозиторий не подключён)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Dict, List, Optional
from urllib.parse import quote, urlencode

import aiohttp


class SimpleBybitClient:
    BASE = "https://api.bybit.com"
    TEST = "https://api-testnet.bybit.com"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = self.TEST if testnet else self.BASE
        self.recv_window = 20000
        self._session: Optional[aiohttp.ClientSession] = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _sign(self, params: str, ts: str) -> str:
        payload = f"{ts}{self.api_key}{self.recv_window}{params}"
        return hmac.new(
            self.api_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()

    def _headers(self, params: str) -> Dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": self._sign(params, ts),
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
        }

    async def _get(self, endpoint: str, params: Optional[Dict] = None, private: bool = False):
        items = sorted((str(k), str(v)) for k, v in (params or {}).items() if v is not None)
        query = urlencode(items, quote_via=quote)
        url = f"{self.base}{endpoint}" + (f"?{query}" if query else "")
        headers = self._headers(query) if private else {}
        session = await self._session_get()
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
            data = await r.json()
        if data.get("retCode") == 0:
            return data.get("result", {})
        return {}

    async def get_balance(self) -> float:
        result = await self._get(
            "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, private=True
        )
        for acc in result.get("list", []):
            for coin in acc.get("coin", []):
                if coin.get("coin") == "USDT":
                    return float(coin.get("walletBalance", 0))
        return 0.0

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        params: Dict = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        result = await self._get("/v5/position/list", params, private=True)
        return [p for p in result.get("list", []) if float(p.get("size", 0)) > 0]

    async def get_price(self, symbol: str) -> float:
        result = await self._get(
            "/v5/market/tickers", {"category": "linear", "symbol": symbol}
        )
        lst = result.get("list") or []
        return float(lst[0].get("lastPrice", 0)) if lst else 0.0

    async def place_order(self, symbol: str, side: str, qty: float, **kwargs) -> Dict:
        return {"success": False, "error": "Подключите PRD-репозиторий для торговли"}
