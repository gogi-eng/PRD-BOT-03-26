#!/usr/bin/env python3
"""Unified Bybit v5 client with public market data and execution helpers."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Dict, List, Optional, Tuple

import aiohttp


class BybitClient:
    """Async Bybit v5 API Client."""

    BASE_URL = "https://api.bybit.com"
    TESTNET_URL = "https://api-testnet.bybit.com"
    WS_PUBLIC_URL = "wss://stream.bybit.com/v5/public/linear"
    WS_TESTNET_URL = "wss://stream-testnet.bybit.com/v5/public/linear"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, category: str = "linear"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        self.ws_public_url = self.WS_TESTNET_URL if testnet else self.WS_PUBLIC_URL
        self.category = category
        self.recv_window = 20000
        self._session: Optional[aiohttp.ClientSession] = None
        self._liq_task: Optional[asyncio.Task] = None
        self._liq_symbols: Tuple[str, ...] = ()
        self._liquidation_cache: Dict[str, List[Dict]] = {}
        self._request_lock = asyncio.Lock()
        self._last_public_request_at = 0.0
        self._last_private_request_at = 0.0
        self.public_min_interval = 0.22
        self.private_min_interval = 0.35

    async def _respect_rate_limit(self, is_private: bool):
        async with self._request_lock:
            now = time.monotonic()
            min_interval = self.private_min_interval if is_private else self.public_min_interval
            last_at = self._last_private_request_at if is_private else self._last_public_request_at
            wait_for = min_interval - (now - last_at)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            updated = time.monotonic()
            if is_private:
                self._last_private_request_at = updated
            else:
                self._last_public_request_at = updated

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        await self.stop_liquidation_stream()
        if self._session and not self._session.closed:
            await self._session.close()

    async def stop_liquidation_stream(self):
        if self._liq_task and not self._liq_task.done():
            self._liq_task.cancel()
            try:
                await self._liq_task
            except asyncio.CancelledError:
                pass
        self._liq_task = None
        self._liq_symbols = ()

    async def set_liquidation_symbols(self, symbols: List[str]):
        normalized = tuple(sorted(set(symbols[:30])))
        if normalized == self._liq_symbols:
            return
        await self.stop_liquidation_stream()
        if not normalized:
            return
        self._liq_symbols = normalized
        self._liq_task = asyncio.create_task(self._liquidation_worker(list(normalized)))

    async def _liquidation_worker(self, symbols: List[str]):
        args = [f"allLiquidation.{symbol}" for symbol in symbols]
        while tuple(sorted(symbols)) == self._liq_symbols:
            try:
                session = await self._get_session()
                async with session.ws_connect(self.ws_public_url, heartbeat=20, autoping=True) as ws:
                    await ws.send_json({"op": "subscribe", "args": args})
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(message.data)
                            if payload.get("topic", "").startswith("allLiquidation."):
                                for item in payload.get("data", []):
                                    self._store_liquidation_event(item)
                        elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[BYBIT] liquidation stream error: {exc}")
                await asyncio.sleep(5)

    def _store_liquidation_event(self, payload: Dict):
        symbol = payload.get("s")
        if not symbol:
            return
        event = {
            "price": float(payload.get("p", 0.0)),
            "size": float(payload.get("v", 0.0)),
            "side": payload.get("S", ""),
            "timestamp": int(payload.get("T", int(time.time() * 1000))),
        }
        bucket = self._liquidation_cache.setdefault(symbol, [])
        bucket.append(event)
        if len(bucket) > 400:
            del bucket[:-400]

    def get_liquidation_events(self, symbol: str, max_age_sec: int = 3600, limit: int = 250) -> List[Dict]:
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - max_age_sec * 1000
        events = [item for item in self._liquidation_cache.get(symbol, []) if item.get("timestamp", 0) >= cutoff]
        return events[-limit:]

    def _generate_signature(self, params: str, timestamp: str) -> str:
        payload = f"{timestamp}{self.api_key}{self.recv_window}{params}"
        return hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _get_headers(self, params: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(params, timestamp)
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, private: bool = False, signed: bool = False, retries: int = 3, return_full: bool = False) -> Optional[Dict]:
        is_private = private or signed
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        for attempt in range(retries):
            try:
                await self._respect_rate_limit(is_private)
                data = {}
                if method == "GET":
                    query = "&".join(f"{key}={value}" for key, value in (params or {}).items())
                    headers = self._get_headers(query) if is_private else {}
                    full_url = f"{url}?{query}" if query else url
                    async with session.get(full_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as response:
                        try:
                            data = await response.json()
                        except Exception:
                            text = await response.text()
                            return {"_error": "Non-JSON", "_raw": text[:200]}
                else:
                    body = json.dumps(params or {})
                    headers = self._get_headers(body) if is_private else {"Content-Type": "application/json"}
                    async with session.post(url, headers=headers, data=body, timeout=aiohttp.ClientTimeout(total=15)) as response:
                        try:
                            data = await response.json()
                        except Exception:
                            text = await response.text()
                            return {"_error": "Non-JSON", "_raw": text[:200]}

                if not isinstance(data, dict):
                    return {"_error": "Invalid response"}
                if return_full:
                    return data
                if data.get("retCode") == 0:
                    return data.get("result", {})

                ret_code = data.get("retCode")
                error_msg = data.get("retMsg", "Unknown error")
                print(f"[BYBIT] API error: {error_msg} (code: {ret_code})")
                if ret_code == 110043:
                    return data.get("result", {})
                if ret_code == 10006 and attempt < retries - 1:
                    await asyncio.sleep(5 + attempt * 5)
                    continue
                return {"_error": error_msg, "_code": ret_code}
            except aiohttp.ClientError as exc:
                print(f"[BYBIT] Request error {attempt + 1}/{retries}: {exc}")
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as exc:
                print(f"[BYBIT] Unexpected error: {exc}")
                return None
        return None

    # === PUBLIC ===

    async def get_price(self, symbol: str) -> float:
        result = await self._request("GET", "/v5/market/tickers", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            return float(result["list"][0].get("lastPrice", 0))
        return 0.0

    async def get_klines(self, symbol: str, interval: str = "15", limit: int = 200) -> List[Dict]:
        result = await self._request("GET", "/v5/market/kline", {
            "category": self.category, "symbol": symbol, "interval": interval, "limit": limit,
        })
        if result and result.get("list"):
            klines = []
            for k in reversed(result["list"]):
                klines.append({
                    "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                })
            return klines
        return []

    async def get_tickers(self) -> List[Dict]:
        result = await self._request("GET", "/v5/market/tickers", {"category": self.category})
        if result and result.get("list"):
            return result["list"]
        return []

    async def get_orderbook(self, symbol: str, limit: int = 50) -> Dict:
        result = await self._request("GET", "/v5/market/orderbook", {"category": self.category, "symbol": symbol, "limit": limit})
        if result:
            return {
                "bids": result.get("b", []),
                "asks": result.get("a", []),
                "ts": int(result.get("ts", 0) or 0),
            }
        return {"bids": [], "asks": [], "ts": 0}

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        result = await self._request("GET", "/v5/market/recent-trade", {"category": self.category, "symbol": symbol, "limit": limit})
        if result and result.get("list"):
            trades = []
            for item in result["list"]:
                trades.append(
                    {
                        "price": float(item.get("price", 0.0)),
                        "size": float(item.get("size", 0.0)),
                        "side": item.get("side", ""),
                        "timestamp": int(item.get("time", 0) or 0),
                    }
                )
            return trades
        return []

    async def get_instrument_info(self, symbol: str) -> Optional[Dict]:
        result = await self._request("GET", "/v5/market/instruments-info", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            inst = result["list"][0]
            return {
                "min_qty": float(inst.get("lotSizeFilter", {}).get("minOrderQty", 1)),
                "qty_step": float(inst.get("lotSizeFilter", {}).get("qtyStep", 1)),
                "price_step": float(inst.get("priceFilter", {}).get("tickSize", 0.0001)),
            }
        return None

    async def get_max_leverage(self, symbol: str) -> int:
        result = await self._request("GET", "/v5/market/instruments-info", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            return int(float(result["list"][0].get("leverageFilter", {}).get("maxLeverage", 100)))
        return 100

    # === PRIVATE ===

    async def get_positions(self, symbol: str = None) -> List[Dict]:
        params = {"category": self.category, "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        result = await self._request("GET", "/v5/position/list", params, private=True)
        if result and result.get("list"):
            return [p for p in result["list"] if float(p.get("size", 0)) > 0]
        return []

    async def get_balance(self) -> float:
        result = await self._request("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, private=True)
        if result and result.get("list"):
            for acc in result["list"]:
                for coin in acc.get("coin", []):
                    if coin.get("coin") == "USDT":
                        return float(coin.get("walletBalance", 0))
        return 0.0

    async def place_order(self, symbol: str, side: str, qty: float,
                          order_type: str = "Market", price: float = None,
                          stop_loss: float = None, take_profit: float = None,
                          reduce_only: bool = False, time_in_force: str = "GTC",
                          position_idx: int = 0) -> Dict:
        params = {
            "category": self.category, "symbol": symbol, "side": side,
            "orderType": order_type, "qty": str(qty), "timeInForce": time_in_force,
        }
        if price and order_type == "Limit":
            params["price"] = str(price)
        if stop_loss:
            params["stopLoss"] = str(stop_loss)
        if take_profit:
            params["takeProfit"] = str(take_profit)
        if reduce_only:
            params["reduceOnly"] = True
        if position_idx > 0:
            params["positionIdx"] = position_idx

        result = await self._request("POST", "/v5/order/create", params, private=True)
        if result and not result.get("_error"):
            return {"success": True, "orderId": result.get("orderId", ""), "error": ""}
        return {"success": False, "orderId": "", "error": "Order failed"}

    async def get_order_status(self, symbol: str, order_id: str) -> Dict:
        result = await self._request("GET", "/v5/order/realtime", {"category": self.category, "symbol": symbol, "orderId": order_id}, private=True)
        if result and result.get("list"):
            order = result["list"][0]
            return {
                "status": order.get("orderStatus", "Unknown"),
                "filled_qty": float(order.get("cumExecQty", 0.0)),
                "avg_price": float(order.get("avgPrice", 0.0) or 0.0),
            }
        return {"status": "Unknown", "filled_qty": 0.0, "avg_price": 0.0}

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        result = await self._request("POST", "/v5/order/cancel", {"category": self.category, "symbol": symbol, "orderId": order_id}, private=True)
        return bool(result and not result.get("_error"))

    async def update_stop_loss(self, symbol: str, stop_loss: float, position_idx: int = 0) -> Dict:
        params = {
            "category": self.category, "symbol": symbol,
            "stopLoss": str(stop_loss), "positionIdx": position_idx,
            "slTriggerBy": "MarkPrice",
        }
        result = await self._request("POST", "/v5/position/trading-stop", params, private=True, return_full=True)
        if result and isinstance(result, dict) and result.get("retCode") == 0:
            return {"success": True, "error": ""}
        error = result.get("retMsg", "Failed") if isinstance(result, dict) else "Empty response"
        return {"success": False, "error": error}

    async def update_take_profit(self, symbol: str, take_profit: float, position_idx: int = 0) -> Dict:
        params = {
            "category": self.category, "symbol": symbol,
            "takeProfit": str(take_profit), "positionIdx": position_idx,
            "tpTriggerBy": "MarkPrice",
        }
        result = await self._request("POST", "/v5/position/trading-stop", params, private=True, return_full=True)
        if result and isinstance(result, dict) and result.get("retCode") == 0:
            return {"success": True, "error": ""}
        error = result.get("retMsg", "Failed") if isinstance(result, dict) else "Empty response"
        return {"success": False, "error": error}

    async def close_position(self, symbol: str, side: str, qty: float = None, position_idx: int = 0) -> Dict:
        close_side = "Sell" if side.upper() in ["BUY", "LONG"] else "Buy"
        if qty is None:
            positions = await self.get_positions(symbol)
            if positions:
                qty = float(positions[0].get("size", 0))
            else:
                return {"success": False, "orderId": "", "error": "Position not found"}
        return await self.place_order(symbol=symbol, side=close_side, qty=qty, order_type="Market", reduce_only=True, position_idx=position_idx)

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        params = {
            "category": self.category, "symbol": symbol,
            "buyLeverage": str(leverage), "sellLeverage": str(leverage),
        }
        result = await self._request("POST", "/v5/position/set-leverage", params, private=True)
        return result is not None

    async def get_closed_pnl(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        params = {"category": self.category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        result = await self._request("GET", "/v5/position/closed-pnl", params, private=True)
        if result and result.get("list"):
            return result["list"]
        return []

    async def get_funding_rate(self, symbol: str) -> Optional[Dict]:
        """Получить текущий funding rate из тикера."""
        result = await self._request("GET", "/v5/market/tickers", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            ticker = result["list"][0]
            return {
                "funding_rate": float(ticker.get("fundingRate", 0)),
                "open_interest": float(ticker.get("openInterest", 0)),
                "bid1": float(ticker.get("bid1Price", 0) or 0),
                "ask1": float(ticker.get("ask1Price", 0) or 0),
            }
        return None

    async def get_open_interest_history(self, symbol: str, interval: str = "1h", limit: int = 25) -> List[Dict]:
        """Получить историю OI."""
        result = await self._request("GET", "/v5/market/open-interest", {
            "category": self.category, "symbol": symbol, "intervalTime": interval, "limit": limit,
        })
        if result and result.get("list"):
            return result["list"]
        return []
