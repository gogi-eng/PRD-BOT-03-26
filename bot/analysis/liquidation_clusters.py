#!/usr/bin/env python3
"""
Liquidation Clusters Detector — анализ зон ликвидаций.

Оценивает где сосредоточены ликвидации (на основе расчёта):
- Вычисляем уровни ликвидации для типичных плечей (5x, 10x, 25x, 50x, 100x)
- Определяем магнит — куда цена притягивается
"""
from typing import Dict, List, Optional
from dataclasses import dataclass
import math


@dataclass
class LiquidationLevel:
    price: float
    leverage: int
    side: str  # "long" or "short"
    distance_pct: float


@dataclass
class LiquidationAnalysis:
    levels_above: List[LiquidationLevel]
    levels_below: List[LiquidationLevel]
    magnet_direction: str  # "up", "down", "neutral"
    nearest_long_liq: Optional[LiquidationLevel]
    nearest_short_liq: Optional[LiquidationLevel]
    signal: int  # 1 = price likely to go up (clear longs below), -1 = down, 0 = neutral


class LiquidationClusterDetector:
    """
    Расчётный детектор ликвидационных кластеров.

    Идея: крупные игроки целятся в зоны где много ликвидаций.
    Мы можем определить эти зоны, зная текущую цену и типичные плечи.
    """

    LEVERAGE_LEVELS = [5, 10, 25, 50, 100]

    def __init__(self):
        pass

    def analyze(self, current_price: float, recent_highs: List[float] = None,
                recent_lows: List[float] = None) -> LiquidationAnalysis:
        """
        Вычисляет уровни ликвидаций на основе цены и стандартных плечей.

        Ликвидация LONG при плече Nx: price * (1 - 1/N) (упрощённо, без maintenance margin)
        Ликвидация SHORT при плече Nx: price * (1 + 1/N)
        """
        if current_price <= 0:
            return LiquidationAnalysis([], [], "neutral", None, None, 0)

        levels_above = []
        levels_below = []

        # Reference prices: текущая цена + недавние high/low
        ref_prices = [current_price]
        if recent_highs:
            ref_prices.extend(recent_highs[-5:])
        if recent_lows:
            ref_prices.extend(recent_lows[-5:])

        for ref_price in ref_prices:
            for lev in self.LEVERAGE_LEVELS:
                # Long liquidation (ниже entry)
                long_liq = ref_price * (1 - 0.9 / lev)  # ~90% margin
                dist = abs(current_price - long_liq) / current_price * 100
                if long_liq < current_price:
                    levels_below.append(LiquidationLevel(
                        price=long_liq, leverage=lev, side="long", distance_pct=dist
                    ))

                # Short liquidation (выше entry)
                short_liq = ref_price * (1 + 0.9 / lev)
                dist = abs(short_liq - current_price) / current_price * 100
                if short_liq > current_price:
                    levels_above.append(LiquidationLevel(
                        price=short_liq, leverage=lev, side="short", distance_pct=dist
                    ))

        # Sort by distance
        levels_above.sort(key=lambda x: x.distance_pct)
        levels_below.sort(key=lambda x: x.distance_pct)

        # Deduplicate close levels
        levels_above = self._deduplicate(levels_above)
        levels_below = self._deduplicate(levels_below)

        # Nearest
        nearest_long = levels_below[0] if levels_below else None
        nearest_short = levels_above[0] if levels_above else None

        # Magnet direction: where are more high-leverage liquidations?
        high_lev_above = sum(1 for lv in levels_above if lv.leverage >= 25 and lv.distance_pct < 5)
        high_lev_below = sum(1 for lv in levels_below if lv.leverage >= 25 and lv.distance_pct < 5)

        if high_lev_below > high_lev_above + 2:
            magnet = "down"
            signal = -1
        elif high_lev_above > high_lev_below + 2:
            magnet = "up"
            signal = 1
        else:
            magnet = "neutral"
            signal = 0

        return LiquidationAnalysis(
            levels_above=levels_above[:10],
            levels_below=levels_below[:10],
            magnet_direction=magnet,
            nearest_long_liq=nearest_long,
            nearest_short_liq=nearest_short,
            signal=signal,
        )

    @staticmethod
    def _deduplicate(levels: List[LiquidationLevel], min_gap_pct: float = 0.5) -> List[LiquidationLevel]:
        """Убираем уровни, которые слишком близко друг к другу."""
        if not levels:
            return []
        result = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl.price - result[-1].price) / result[-1].price * 100 >= min_gap_pct:
                result.append(lvl)
        return result
