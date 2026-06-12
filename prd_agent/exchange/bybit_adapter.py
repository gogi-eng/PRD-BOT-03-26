"""
Адаптер Bybit: локальный exchange.bybit_client из корня проекта (без внешнего клона).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from prd_agent.exchange.api_cache import load_api_cache_settings
from prd_agent.exchange.api_stats import ApiCallJournal


def _import_local_client(root: Path):
    root_s = str(root.resolve())
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    try:
        from exchange.bybit_client import BybitClient  # type: ignore

        return BybitClient
    except ImportError:
        return None


class BybitAdapter:
    """Единая точка доступа к Bybit API."""

    def __init__(self, cfg: Dict[str, Any]):
        b = cfg.get("bybit", {})
        root = Path(cfg["_root"])
        ClientCls = _import_local_client(root)
        self._use_prd = ClientCls is not None
        if self._use_prd:
            self._client = ClientCls(
                api_key=b["api_key"],
                api_secret=b["api_secret"],
                testnet=bool(b.get("testnet", False)),
                category=b.get("category", "linear"),
            )
        else:
            from prd_agent.exchange.simple_bybit import SimpleBybitClient

            self._client = SimpleBybitClient(
                api_key=b["api_key"],
                api_secret=b["api_secret"],
                testnet=bool(b.get("testnet", False)),
            )
        self._api_stats = ApiCallJournal()
        self._cache = load_api_cache_settings(cfg)
        self._cache._on_fetch = self._on_cache_fetch  # noqa: SLF001
        self._cycle_tickers: Dict[str, Dict] = {}

    def _on_cache_fetch(self, key: str, cached: bool) -> None:
        ep = str(key).split(":", 1)[0] if key else "unknown"
        self._api_stats.record(ep, cached=cached)

    def begin_api_cycle(self, cycle_num: int) -> None:
        self._api_stats.begin_cycle(cycle_num)

    def end_api_cycle(self) -> Dict:
        return self._api_stats.end_cycle()

    def api_stats_snapshot(self) -> Dict:
        return self._api_stats.snapshot()

    async def refresh_cycle_tickers(self) -> Dict[str, Dict]:
        """Один get_tickers() на цикл → словарь symbol → ticker."""
        rows = await self.get_tickers()
        self._cycle_tickers = {
            str(t.get("symbol", "")).upper(): t for t in rows if t.get("symbol")
        }
        return dict(self._cycle_tickers)

    def get_tickers_map(self) -> Dict[str, Dict]:
        return dict(self._cycle_tickers)

    async def close(self) -> None:
        if hasattr(self._client, "close"):
            await self._client.close()

    async def get_balance(self) -> float:
        self._api_stats.record("balance")
        return float(await self._client.get_balance())

    async def get_available_balance(self) -> float:
        if hasattr(self._client, "get_available_balance"):
            return float(await self._client.get_available_balance())
        return float(await self._client.get_balance())

    async def get_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        self._api_stats.record("positions")
        return list(await self._client.get_positions(symbol))

    async def has_open_position(self, symbol: str) -> bool:
        if hasattr(self._client, "has_open_position"):
            return bool(await self._client.has_open_position(symbol))
        rows = await self.get_positions(symbol)
        return len(rows) > 0

    async def get_price(self, symbol: str) -> float:
        return await self._cache.get_price(
            symbol,
            lambda: self._client.get_price(symbol),
        )

    async def get_klines(self, symbol: str, interval: str = "15", limit: int = 200):
        if not hasattr(self._client, "get_klines"):
            return []
        return await self._cache.get_klines(
            symbol,
            interval,
            limit,
            lambda: self._client.get_klines(symbol, interval=interval, limit=limit),
        )

    async def get_tickers(self) -> List[Dict]:
        if not hasattr(self._client, "get_tickers"):
            return []
        return await self._cache.get_tickers(lambda: self._client.get_tickers())

    async def get_orderbook(
        self,
        symbol: str,
        limit: int = 50,
        *,
        lazy: bool = True,
        signal_passed_cheap_filters: bool = False,
    ) -> Dict:
        """Lazy: orderbook только если сигнал прошёл дешёвые фильтры."""
        if lazy and not signal_passed_cheap_filters:
            return {}
        if not hasattr(self._client, "get_orderbook"):
            return {}
        sym = str(symbol).upper()
        return await self._cache.get_orderbook(
            sym,
            limit,
            lambda: self._client.get_orderbook(sym, limit=limit),
        )

    async def get_recent_trades(
        self,
        symbol: str,
        limit: int = 100,
        *,
        lazy: bool = True,
        signal_passed_cheap_filters: bool = False,
    ) -> List[Dict]:
        if lazy and not signal_passed_cheap_filters:
            return []
        if not hasattr(self._client, "get_recent_trades"):
            return []
        sym = str(symbol).upper()
        return await self._cache.get_recent_trades(
            sym,
            limit,
            lambda: self._client.get_recent_trades(sym, limit=limit),
        )

    async def set_liquidation_symbols(self, symbols: List[str]) -> None:
        if hasattr(self._client, "set_liquidation_symbols"):
            await self._client.set_liquidation_symbols(symbols)

    async def get_recent_liquidations(self, symbol: str, limit: int = 20) -> List[Dict]:
        if hasattr(self._client, "get_liquidation_events"):
            return list(self._client.get_liquidation_events(symbol, limit=limit))
        return []

    async def get_funding_rate(self, symbol: str) -> Optional[Dict]:
        if hasattr(self._client, "get_funding_rate"):
            return await self._client.get_funding_rate(symbol)
        return None

    async def get_open_interest_history(self, symbol: str, interval: str = "1h", limit: int = 25):
        if hasattr(self._client, "get_open_interest_history"):
            return await self._client.get_open_interest_history(symbol, interval=interval, limit=limit)
        return []

    async def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        *,
        order_type: str = "Market",
        price: Optional[float] = None,
    ) -> Dict:
        return await self._client.place_order(
            symbol=symbol,
            side=side,
            qty=qty,
            stop_loss=stop_loss,
            take_profit=take_profit,
            order_type=order_type,
            price=price,
        )

    async def apply_trade_leverage(self, symbol: str, requested: int):
        from prd_agent.exchange.leverage_apply import apply_trade_leverage

        return await apply_trade_leverage(self._client, symbol, requested)

    async def get_symbol_leverage(self, symbol: str) -> int:
        if hasattr(self._client, "get_symbol_leverage"):
            return int(await self._client.get_symbol_leverage(symbol) or 0)
        return 0

    async def get_max_leverage(self, symbol: str) -> int:
        if hasattr(self._client, "get_max_leverage"):
            return int(await self._client.get_max_leverage(symbol) or 100)
        return 100

    def api_circuit_snapshot(self) -> Dict:
        cb = getattr(self._client, "circuit_breaker", None)
        if cb and hasattr(cb, "snapshot"):
            return cb.snapshot()
        return {"open": False}

    async def get_closed_pnl_page(
        self,
        *,
        symbol: Optional[str] = None,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ):
        if hasattr(self._client, "get_closed_pnl_page"):
            return await self._client.get_closed_pnl_page(
                symbol=symbol,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                cursor=cursor,
                limit=limit,
            )
        rows = await self._client.get_closed_pnl(symbol=symbol, limit=limit) if hasattr(
            self._client, "get_closed_pnl"
        ) else []
        return rows, ""

    @property
    def uses_prd_client(self) -> bool:
        return self._use_prd

    @property
    def is_testnet(self) -> bool:
        return bool(getattr(self._client, "base_url", "").find("testnet") >= 0)
