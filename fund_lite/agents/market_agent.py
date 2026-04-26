#!/usr/bin/env python3
"""Сбор рыночных данных: в проде используйте ``BybitClient`` из бота."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MarketAgent:
    client: Optional[Any] = None

    async def get_data(self, symbol: str) -> Dict[str, Any]:
        """OHLCV + orderbook + funding — заглушка; подключите ``client.get_klines`` и т.д."""
        if self.client is None:
            return {"symbol": symbol, "klines": [], "orderbook": {}, "funding": 0.0}
        return {"symbol": symbol, "klines": [], "orderbook": {}, "funding": 0.0}
