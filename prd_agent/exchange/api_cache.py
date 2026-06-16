"""TTL-кеш и лимит параллельных read-запросов к Bybit."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from prd_agent.exchange.api_stats import ApiCallJournal

T = TypeVar("T")


class ExchangeApiCache:
    def __init__(
        self,
        *,
        enabled: bool = True,
        price_ttl_sec: float = 8.0,
        klines_ttl_sec: float = 45.0,
        tickers_ttl_sec: float = 30.0,
        max_parallel_requests: int = 6,
        journal: Optional["ApiCallJournal"] = None,
    ):
        self.enabled = bool(enabled)
        self._journal = journal
        self.price_ttl_sec = max(1.0, float(price_ttl_sec))
        self.klines_ttl_sec = max(5.0, float(klines_ttl_sec))
        self.tickers_ttl_sec = max(5.0, float(tickers_ttl_sec))
        self._sem = asyncio.Semaphore(max(1, int(max_parallel_requests)))
        self._entries: Dict[str, Tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    def _record(self, endpoint: str, *, cached: bool) -> None:
        if self._journal is not None:
            self._journal.record(endpoint, cached=cached)

    async def _cached(
        self,
        key: str,
        ttl_sec: float,
        fetcher: Callable[[], Awaitable[T]],
        endpoint: str = "unknown",
    ) -> T:
        if not self.enabled:
            async with self._sem:
                self._record(endpoint, cached=False)
                return await fetcher()

        now = time.monotonic()
        async with self._lock:
            hit = self._entries.get(key)
            if hit and (now - hit[0]) < ttl_sec:
                self._record(endpoint, cached=True)
                return hit[1]

        async with self._sem:
            now = time.monotonic()
            async with self._lock:
                hit = self._entries.get(key)
                if hit and (now - hit[0]) < ttl_sec:
                    self._record(endpoint, cached=True)
                    return hit[1]
            value = await fetcher()
            async with self._lock:
                self._entries[key] = (time.monotonic(), value)
            self._record(endpoint, cached=False)
            return value

    async def get_price(self, symbol: str, fetcher: Callable[[], Awaitable[float]]) -> float:
        sym = str(symbol).upper()
        return float(
            await self._cached(
                f"price:{sym}",
                self.price_ttl_sec,
                fetcher,
                endpoint="price",
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
                endpoint="klines",
            )
        )

    async def get_tickers(self, fetcher: Callable[[], Awaitable[list]]) -> list:
        return list(
            await self._cached(
                "tickers:all",
                self.tickers_ttl_sec,
                fetcher,
                endpoint="tickers",
            )
        )

    def clear(self) -> None:
        self._entries.clear()


def load_api_cache_settings(
    cfg: Dict[str, Any],
    journal: Optional["ApiCallJournal"] = None,
) -> ExchangeApiCache:
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
        max_parallel_requests=int(block.get("max_parallel_requests", 6)),
        journal=journal,
    )
