#!/usr/bin/env python3
"""OHLCV: offline DataFrame helpers + async fetch via BybitClient."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, Sequence

import pandas as pd


class _KlineFetcher(Protocol):
    async def get_klines(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]: ...


def klines_to_dataframe(klines: Sequence[Dict[str, Any]]) -> pd.DataFrame:
    """Build DataFrame from Bybit-style kline dicts (timestamp ms, open, high, low, close, volume)."""
    rows = []
    for k in klines:
        rows.append(
            {
                "time": int(k.get("timestamp", 0) or 0),
                "open": float(k.get("open", 0.0) or 0.0),
                "high": float(k.get("high", 0.0) or 0.0),
                "low": float(k.get("low", 0.0) or 0.0),
                "close": float(k.get("close", 0.0) or 0.0),
                "volume": float(k.get("volume", 0.0) or 0.0),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty and df["time"].iloc[0] > 0:
        df["dt"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df


class DataCollector:
    """
    Thin wrapper: either call async_fetch with BybitClient or pass pre-fetched klines.

    ``exchange`` in the original sketch = any object with async get_klines(symbol, interval, limit).
    """

    def __init__(self, exchange: Optional[_KlineFetcher] = None):
        self.exchange = exchange

    @staticmethod
    def from_klines(klines: Sequence[Dict[str, Any]]) -> pd.DataFrame:
        return klines_to_dataframe(klines)

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1",
        limit: int = 500,
    ) -> pd.DataFrame:
        if self.exchange is None:
            raise RuntimeError("DataCollector: exchange (BybitClient) is not set")
        raw = await self.exchange.get_klines(symbol, timeframe, limit)
        return klines_to_dataframe(raw)

    def append_online(self, df: pd.DataFrame, new_row: pd.DataFrame, tail: int = 2000) -> pd.DataFrame:
        out = pd.concat([df, new_row], ignore_index=True)
        return out.tail(int(tail)).reset_index(drop=True)
