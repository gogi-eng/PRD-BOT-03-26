#!/usr/bin/env python3
"""
Bybit v5 API Client — единственный модуль работы с биржей.
"""
import asyncio
import hmac
import hashlib
import time
import json
import aiohttp
from typing import Dict, List, Optional


class BybitClient:
    """Async Bybit v5 API Client."""

    BASE_URL = "https://api.bybit.com"
    TESTNET_URL = "https://api-testnet.bybit.com"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, category: str = "linear"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        self.category = category
        self.recv_window = 20000
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _generate_signature(self, params: str, timestamp: str) -> str:
        param_str = f"{timestamp}{self.api_key}{self.recv_window}{params}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

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

    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None,
                       private: bool = False, signed: bool = False,
                       retries: int = 3, return_full: bool = False) -> Optional[Dict]:
        is_private = private or signed
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        for attempt in range(retries):
            try:
                data = {}
                if method == "GET":
                    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
                    headers = self._get_headers(qs) if is_private else {}
                    full_url = f"{url}?{qs}" if qs else url
                    async with session.get(full_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        try:
                            data = await resp.json()
                        except Exception:
                            text = await resp.text()
                            if not text.strip():
                                return {"_empty": True}
                            return {"_error": "Non-JSON", "_raw": text[:200]}
                elif method == "POST":
                    body = json.dumps(params or {})
                    headers = self._get_headers(body) if is_private else {"Content-Type": "application/json"}
                    async with session.post(url, headers=headers, data=body, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        try:
                            data = await resp.json()
                        except Exception:
                            text = await resp.text()
                            if not text.strip():
                                return {"_empty": True}
                            return {"_error": "Non-JSON", "_raw": text[:200]}

                if not isinstance(data, dict):
                    return {"_error": "Invalid response"}

                if return_full:
                    return data

                if data.get("retCode") == 0:
                    return data.get("result", {})
                else:
                    ret_code = data.get("retCode")
                    error_msg = data.get("retMsg", "Unknown error")
                    if ret_code == 110043:
                        return data.get("result", {})
                    print(f"[BYBIT] API error: {error_msg} (code: {ret_code})")
                    if ret_code in [10001, 10002, 10003]:
                        return {"_error": error_msg, "_code": ret_code}
                    # Rate limit — ждём и повторяем
                    if ret_code == 10006:
                        if attempt < retries - 1:
                            wait = 3 * (attempt + 1)
                            print(f"[BYBIT] Rate limit, waiting {wait}s...")
                            await asyncio.sleep(wait)
                            continue
                        return {"_error": "Rate limit exceeded", "_code": 10006}

            except aiohttp.ClientError as e:
                print(f"[BYBIT] Request error (attempt {attempt + 1}/{retries}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                print(f"[BYBIT] Unexpected error: {e}")
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
                          reduce_only: bool = False) -> Dict:
        params = {
            "category": self.category, "symbol": symbol, "side": side,
            "orderType": order_type, "qty": str(qty), "timeInForce": "GTC",
        }
        if price and order_type == "Limit":
            params["price"] = str(price)
        if stop_loss:
            params["stopLoss"] = str(stop_loss)
        if take_profit:
            params["takeProfit"] = str(take_profit)
        if reduce_only:
            params["reduceOnly"] = True

        result = await self._request("POST", "/v5/order/create", params, private=True)
        if result:
            return {"success": True, "orderId": result.get("orderId", ""), "error": ""}
        return {"success": False, "orderId": "", "error": "Order failed"}

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

    async def close_position(self, symbol: str, side: str, qty: float = None) -> Dict:
        close_side = "Sell" if side.upper() in ["BUY", "LONG"] else "Buy"
        if qty is None:
            positions = await self.get_positions(symbol)
            if positions:
                qty = float(positions[0].get("size", 0))
            else:
                return {"success": False, "orderId": "", "error": "Position not found"}
        return await self.place_order(symbol=symbol, side=close_side, qty=qty, order_type="Market", reduce_only=True)

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
