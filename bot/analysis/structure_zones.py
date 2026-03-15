#!/usr/bin/env python3
"""HTF fair value gap and order block detection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class StructureZone:
    kind: str
    bias: str
    low: float
    high: float
    strength: float
    created_at_index: int

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass
class ZoneContext:
    bullish_fvg: Optional[StructureZone]
    bearish_fvg: Optional[StructureZone]
    bullish_ob: Optional[StructureZone]
    bearish_ob: Optional[StructureZone]
    support_levels: List[float]
    resistance_levels: List[float]

    @property
    def bullish_confluence(self) -> bool:
        return self.bullish_fvg is not None and self.bullish_ob is not None

    @property
    def bearish_confluence(self) -> bool:
        return self.bearish_fvg is not None and self.bearish_ob is not None


class StructureZoneAnalyzer:
    """Extracts FVG and order block zones from higher timeframe candles."""

    def analyze(self, klines: List[dict], current_price: float) -> ZoneContext:
        bullish_fvgs: List[StructureZone] = []
        bearish_fvgs: List[StructureZone] = []
        bullish_obs: List[StructureZone] = []
        bearish_obs: List[StructureZone] = []

        if len(klines) < 6:
            return ZoneContext(None, None, None, None, [], [])

        highs = [float(item["high"]) for item in klines]
        lows = [float(item["low"]) for item in klines]
        opens = [float(item["open"]) for item in klines]
        closes = [float(item["close"]) for item in klines]
        avg_range = sum(high - low for high, low in zip(highs[-20:], lows[-20:])) / max(1, len(highs[-20:]))

        for idx in range(2, len(klines)):
            first_high = highs[idx - 2]
            first_low = lows[idx - 2]
            third_high = highs[idx]
            third_low = lows[idx]

            if third_low > first_high:
                bullish_fvgs.append(StructureZone("fvg", "bullish", first_high, third_low, third_low - first_high, idx))
            if third_high < first_low:
                bearish_fvgs.append(StructureZone("fvg", "bearish", third_high, first_low, first_low - third_high, idx))

        for idx in range(len(klines) - 3):
            candle_open = opens[idx]
            candle_close = closes[idx]
            candle_high = highs[idx]
            candle_low = lows[idx]
            future_closes = closes[idx + 1 : idx + 4]

            if candle_close < candle_open and max(future_closes) > candle_high + avg_range * 0.3:
                bullish_obs.append(StructureZone("ob", "bullish", candle_low, candle_open, candle_open - candle_low, idx))
            if candle_close > candle_open and min(future_closes) < candle_low - avg_range * 0.3:
                bearish_obs.append(StructureZone("ob", "bearish", candle_close, candle_high, candle_high - candle_close, idx))

        support_levels = sorted({zone.low for zone in bullish_fvgs + bullish_obs if zone.low < current_price} | {low for low in lows[-20:] if low < current_price})
        resistance_levels = sorted({zone.high for zone in bearish_fvgs + bearish_obs if zone.high > current_price} | {high for high in highs[-20:] if high > current_price})

        return ZoneContext(
            bullish_fvg=self._nearest_zone(bullish_fvgs, current_price, below=True),
            bearish_fvg=self._nearest_zone(bearish_fvgs, current_price, below=False),
            bullish_ob=self._nearest_zone(bullish_obs, current_price, below=True),
            bearish_ob=self._nearest_zone(bearish_obs, current_price, below=False),
            support_levels=support_levels,
            resistance_levels=resistance_levels,
        )

    @staticmethod
    def _nearest_zone(zones: List[StructureZone], current_price: float, below: bool) -> Optional[StructureZone]:
        candidates = []
        for zone in zones:
            reference = zone.high if below else zone.low
            if below and reference <= current_price:
                candidates.append(zone)
            if (not below) and reference >= current_price:
                candidates.append(zone)
        if not candidates:
            return None
        return min(candidates, key=lambda zone: abs(zone.mid - current_price))