"""TTL-кеш и лимит параллельных read-запросов к Bybit."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar

T = TypeVar("T")


class ExchangeApiCache:
    def __init__(
        self,
        *,
        enabled: bool = True,
        price_ttl_sec: float = 8.0,
        klines_ttl_sec: float = 45.0,
        tickers_ttl_sec: float = 30.0,
        orderbook_ttl_sec: float = 12.0,
        trades_ttl_sec: float = 15.0,
        max_parallel_requests: int = 6,
        on_fetch: Optional[Callable[[str, bool], None]] = None,
    ):
        self.enabled = bool(enabled)
        self.price_ttl_sec = max(1.0, float(price_ttl_sec))
        self.klines_ttl_sec = max(5.0, float(klines_ttl_sec))
        self.tickers_ttl_sec = max(5.0, float(tickers_ttl_sec))
        self.orderbook_ttl_sec = max(3.0, float(orderbook_ttl_sec))
        self.trades_ttl_sec = max(3.0, float(trades_ttl_sec))
        self._sem = asyncio.Semaphore(max(1, int(max_parallel_requests)))
        self._entries: Dict[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self._on_fetch = on_fetch

    async def _cached(
        self,
        key: str,
        ttl_sec: float,
        fetcher: Callable[[], Awaitable[T]],
    ) -> T:
        if not self.enabled:
            async with self._sem:
                return await fetcher()

        now = time.monotonic()
        async with self._lock:
            hit = self._entries.get(key)
            if hit and (now - hit[0]) < ttl_sec:
                if self._on_fetch:
                    self._on_fetch(key, True)
                return hit[1]

        async with self._sem:
            now = time.monotonic()
            async with self._lock:
                hit = self._entries.get(key)
                if hit and (now - hit[0]) < ttl_sec:
                    if self._on_fetch:
                        self._on_fetch(key, True)
                    return hit[1]
            if self._on_fetch:
                self._on_fetch(key, False)
            value = await fetcher()
            async with self._lock:
                self._entries[key] = (time.monotonic(), value)
            return value

    async def get_price(self, symbol: str, fetcher: Callable[[], Awaitable[float]]) -> float:
        sym = str(symbol).upper()
        return float(
            await self._cached(
                f"price:{sym}",
                self.price_ttl_sec,
                fetcher,
            )
        )

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int,
        fetcher: Callable[[], Awaitable[list]],
    ) -> list:
        sym = str(symbol).upper()
        key = f"klines:{sym}:{interval}:{int(limit)}"
        return list(
            await self._cached(
                key,
                self.klines_ttl_sec,
                fetcher,
            )
        )

    async def get_tickers(self, fetcher: Callable[[], Awaitable[list]]) -> list:
        return list(
            await self._cached(
                "tickers:all",
                self.tickers_ttl_sec,
                fetcher,
            )
        )

    async def get_orderbook(
        self,
        symbol: str,
        limit: int,
        fetcher: Callable[[], Awaitable[Dict]],
    ) -> Dict:
        sym = str(symbol).upper()
        key = f"orderbook:{sym}:{int(limit)}"
        val = await self._cached(key, self.orderbook_ttl_sec, fetcher)
        return dict(val) if isinstance(val, dict) else {}

    async def get_recent_trades(
        self,
        symbol: str,
        limit: int,
        fetcher: Callable[[], Awaitable[list]],
    ) -> list:
        sym = str(symbol).upper()
        key = f"trades:{sym}:{int(limit)}"
        return list(await self._cached(key, self.trades_ttl_sec, fetcher))

    def clear(self) -> None:
        self._entries.clear()


def load_api_cache_settings(cfg: Dict[str, Any]) -> ExchangeApiCache:
    block = cfg.get("api_cache", {})
    if not isinstance(block, dict):
        block = {}
    bybit = cfg.get("bybit", {})
    if isinstance(bybit, dict) and "api_cache" in bybit:
        nested = bybit.get("api_cache", {})
        if isinstance(nested, dict):
            block = {**block, **nested}
    enabled = bool(block.get("enabled", True))
    return ExchangeApiCache(
        enabled=enabled,
        price_ttl_sec=float(block.get("price_ttl_sec", 8)),
        klines_ttl_sec=float(block.get("klines_ttl_sec", 45)),
        tickers_ttl_sec=float(block.get("tickers_ttl_sec", 30)),
        orderbook_ttl_sec=float(block.get("orderbook_ttl_sec", 12)),
        trades_ttl_sec=float(block.get("trades_ttl_sec", 15)),
        max_parallel_requests=int(block.get("max_parallel_requests", 6)),
    )
