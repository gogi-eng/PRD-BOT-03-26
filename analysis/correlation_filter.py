#!/usr/bin/env python3
"""
Correlation Filter — не открываем коррелированные позиции.
"""
from typing import Dict, List


class CorrelationFilter:
    """
    Фильтрует входы если уже есть позиция в коррелированном символе.
    Рассчитывает корреляцию по closes.
    """

    def __init__(self, threshold: float = 0.70, max_correlated: int = 1, lookback: int = 50):
        self.threshold = threshold
        self.max_correlated = max_correlated
        self.lookback = lookback
        self._price_cache: Dict[str, List[float]] = {}

    def update_prices(self, symbol: str, closes: List[float]):
        """Обновить кеш цен для символа."""
        self._price_cache[symbol] = closes[-self.lookback:]

    def calculate_correlation(self, sym1: str, sym2: str) -> float:
        """Pearson correlation между двумя символами."""
        prices1 = self._price_cache.get(sym1, [])
        prices2 = self._price_cache.get(sym2, [])
        n = min(len(prices1), len(prices2))
        if n < 10:
            return 0.0

        p1 = prices1[-n:]
        p2 = prices2[-n:]

        # Returns
        r1 = [(p1[i] - p1[i - 1]) / p1[i - 1] for i in range(1, n) if p1[i - 1] != 0]
        r2 = [(p2[i] - p2[i - 1]) / p2[i - 1] for i in range(1, n) if p2[i - 1] != 0]
        m = min(len(r1), len(r2))
        if m < 5:
            return 0.0

        r1, r2 = r1[:m], r2[:m]
        mean1 = sum(r1) / m
        mean2 = sum(r2) / m

        cov = sum((r1[i] - mean1) * (r2[i] - mean2) for i in range(m)) / m
        std1 = (sum((x - mean1) ** 2 for x in r1) / m) ** 0.5
        std2 = (sum((x - mean2) ** 2 for x in r2) / m) ** 0.5

        if std1 == 0 or std2 == 0:
            return 0.0

        return cov / (std1 * std2)

    def should_filter(self, symbol: str, open_positions: List[str]) -> tuple:
        """
        Проверяет, нужно ли фильтровать вход из-за корреляции.

        Returns:
            (should_filter: bool, reason: str)
        """
        correlated_count = 0
        for pos_symbol in open_positions:
            if pos_symbol == symbol:
                continue
            corr = self.calculate_correlation(symbol, pos_symbol)
            if abs(corr) >= self.threshold:
                correlated_count += 1
                if correlated_count >= self.max_correlated:
                    return True, f"Correlated with {pos_symbol} ({corr:.2f})"

        return False, ""
