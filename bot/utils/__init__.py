#!/usr/bin/env python3
"""
Единый ATR калькулятор.
Один модуль для всего бота — никаких дублей.
"""
from typing import Dict, List, Optional
import time


class ATRCalculator:
    """ATR (Average True Range) с кешированием по символам."""

    def __init__(self, period: int = 14, min_atr_pct: float = 0.15, cache_ttl: int = 300):
        self.period = period
        self.min_atr_pct = min_atr_pct
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, Dict] = {}

    def calculate(self, klines: List[Dict], period: int = None) -> float:
        """Вычислить ATR из списка свечей."""
        p = period or self.period
        if not klines or len(klines) < 2:
            return 0.0

        trs = []
        for i in range(1, len(klines)):
            high = float(klines[i].get("high", 0))
            low = float(klines[i].get("low", 0))
            prev_close = float(klines[i - 1].get("close", 0))
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        if len(trs) < p:
            return sum(trs) / len(trs) if trs else 0.0

        atr = sum(trs[:p]) / p
        for i in range(p, len(trs)):
            atr = (atr * (p - 1) + trs[i]) / p
        return atr

    def get_atr(self, symbol: str, klines: List[Dict]) -> float:
        """Получить ATR с кешированием."""
        now = time.time()
        cached = self._cache.get(symbol)
        if cached and (now - cached["time"]) < self.cache_ttl:
            return cached["atr"]

        atr = self.calculate(klines)
        self._cache[symbol] = {"atr": atr, "time": now}
        return atr

    def get_atr_pct(self, symbol: str, klines: List[Dict]) -> float:
        """ATR в процентах от цены."""
        if not klines:
            return 0.0
        price = float(klines[-1].get("close", 0))
        if price <= 0:
            return 0.0
        atr = self.get_atr(symbol, klines)
        return (atr / price) * 100

    def get_fallback_atr(self, price: float) -> float:
        """Минимальный ATR как процент от цены."""
        return price * (self.min_atr_pct / 100)

    def clear_cache(self):
        self._cache.clear()
