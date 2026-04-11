#!/usr/bin/env python3
"""SMC Structure Zone Analyzer — FVG and Order Block detection with mitigation tracking."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StructureZone:
    kind: str          # "fvg" or "ob"
    bias: str          # "bullish" or "bearish"
    low: float
    high: float
    strength: float    # normalized 0..1
    created_at_index: int
    mitigated: bool = False

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    @property
    def size(self) -> float:
        return self.high - self.low


@dataclass
class ZoneContext:
    bullish_fvg: Optional[StructureZone]
    bearish_fvg: Optional[StructureZone]
    bullish_ob: Optional[StructureZone]
    bearish_ob: Optional[StructureZone]
    support_levels: List[float]
    resistance_levels: List[float]
    all_bullish_zones: List[StructureZone] = field(default_factory=list)
    all_bearish_zones: List[StructureZone] = field(default_factory=list)

    @property
    def bullish_confluence(self) -> bool:
        return self.bullish_fvg is not None and self.bullish_ob is not None

    @property
    def bearish_confluence(self) -> bool:
        return self.bearish_fvg is not None and self.bearish_ob is not None

    def best_long_entry_zone(self) -> Optional[StructureZone]:
        """Return the strongest unmitigated bullish zone near price."""
        active = [z for z in self.all_bullish_zones if not z.mitigated]
        if not active:
            return None
        return max(active, key=lambda z: z.strength)

    def best_short_entry_zone(self) -> Optional[StructureZone]:
        """Return the strongest unmitigated bearish zone near price."""
        active = [z for z in self.all_bearish_zones if not z.mitigated]
        if not active:
            return None
        return max(active, key=lambda z: z.strength)

    def structural_sl_long(self, current_price: float, atr: float) -> float:
        """SL for long = below nearest bullish zone low - buffer."""
        candidates = [z.low for z in self.all_bullish_zones if not z.mitigated and z.low < current_price]
        if candidates:
            return max(candidates) - atr * 0.3
        if self.support_levels:
            below = [s for s in self.support_levels if s < current_price]
            if below:
                return max(below) - atr * 0.3
        return current_price - atr * 1.8

    def structural_sl_short(self, current_price: float, atr: float) -> float:
        """SL for short = above nearest bearish zone high + buffer."""
        candidates = [z.high for z in self.all_bearish_zones if not z.mitigated and z.high > current_price]
        if candidates:
            return min(candidates) + atr * 0.3
        if self.resistance_levels:
            above = [r for r in self.resistance_levels if r > current_price]
            if above:
                return min(above) + atr * 0.3
        return current_price + atr * 1.8

    def structural_tp_long(self, current_price: float, atr: float) -> tuple[float, float]:
        """TP1 and TP2 for long = next resistance zones above."""
        targets = sorted(set(
            [z.low for z in self.all_bearish_zones if not z.mitigated and z.low > current_price] +
            [r for r in self.resistance_levels if r > current_price]
        ))
        tp1 = targets[0] - atr * 0.15 if targets else current_price + atr * 2.5
        tp2 = targets[1] - atr * 0.15 if len(targets) > 1 else tp1 + atr * 1.5
        return tp1, tp2

    def structural_tp_short(self, current_price: float, atr: float) -> tuple[float, float]:
        """TP1 and TP2 for short = next support zones below."""
        targets = sorted(set(
            [z.high for z in self.all_bullish_zones if not z.mitigated and z.high < current_price] +
            [s for s in self.support_levels if s < current_price]
        ), reverse=True)
        tp1 = targets[0] + atr * 0.15 if targets else current_price - atr * 2.5
        tp2 = targets[1] + atr * 0.15 if len(targets) > 1 else tp1 - atr * 1.5
        return tp1, tp2

    def price_in_bullish_zone(self, price: float) -> Optional[StructureZone]:
        """Check if price is currently inside an unmitigated bullish zone."""
        for z in self.all_bullish_zones:
            if not z.mitigated and z.low <= price <= z.high:
                return z
        return None

    def price_in_bearish_zone(self, price: float) -> Optional[StructureZone]:
        """Check if price is currently inside an unmitigated bearish zone."""
        for z in self.all_bearish_zones:
            if not z.mitigated and z.low <= price <= z.high:
                return z
        return None

    def price_near_bullish_zone(self, price: float, tolerance_pct: float = 0.3) -> Optional[StructureZone]:
        """Check if price is near (within tolerance%) an unmitigated bullish zone."""
        tol = price * tolerance_pct / 100
        for z in sorted(self.all_bullish_zones, key=lambda x: abs(x.mid - price)):
            if z.mitigated:
                continue
            if z.low - tol <= price <= z.high + tol:
                return z
        return None

    def price_near_bearish_zone(self, price: float, tolerance_pct: float = 0.3) -> Optional[StructureZone]:
        """Check if price is near (within tolerance%) an unmitigated bearish zone."""
        tol = price * tolerance_pct / 100
        for z in sorted(self.all_bearish_zones, key=lambda x: abs(x.mid - price)):
            if z.mitigated:
                continue
            if z.low - tol <= price <= z.high + tol:
                return z
        return None


class StructureZoneAnalyzer:
    """Extracts FVG and order block zones from HTF candles with mitigation tracking."""

    def analyze(self, klines: List[dict], current_price: float) -> ZoneContext:
        if len(klines) < 6:
            return ZoneContext(None, None, None, None, [], [])

        highs = [float(item["high"]) for item in klines]
        lows = [float(item["low"]) for item in klines]
        opens = [float(item["open"]) for item in klines]
        closes = [float(item["close"]) for item in klines]
        volumes = [float(item.get("volume", 0.0)) for item in klines]
        n = len(klines)
        avg_range = sum(h - lo for h, lo in zip(highs[-20:], lows[-20:])) / max(1, len(highs[-20:]))
        avg_vol = sum(volumes[-20:]) / max(1, len(volumes[-20:])) if volumes else 1.0

        bullish_zones: List[StructureZone] = []
        bearish_zones: List[StructureZone] = []

        # --- FVG detection ---
        for idx in range(2, n):
            first_high = highs[idx - 2]
            third_low = lows[idx]
            # Bullish FVG: gap up (candle 3 low > candle 1 high)
            if third_low > first_high:
                gap = third_low - first_high
                freshness = 1.0 - (n - idx) / max(n, 1) * 0.5
                vol_weight = min(volumes[idx] / max(avg_vol, 1e-9), 2.0) if avg_vol > 0 else 1.0
                strength = min(1.0, (gap / max(avg_range, 1e-9)) * 0.5 * freshness * vol_weight)
                mitigated = self._is_mitigated_below(highs, lows, idx + 1, n, first_high)
                bullish_zones.append(StructureZone("fvg", "bullish", first_high, third_low, round(strength, 3), idx, mitigated))

            first_low = lows[idx - 2]
            third_high = highs[idx]
            # Bearish FVG: gap down (candle 3 high < candle 1 low)
            if third_high < first_low:
                gap = first_low - third_high
                freshness = 1.0 - (n - idx) / max(n, 1) * 0.5
                vol_weight = min(volumes[idx] / max(avg_vol, 1e-9), 2.0) if avg_vol > 0 else 1.0
                strength = min(1.0, (gap / max(avg_range, 1e-9)) * 0.5 * freshness * vol_weight)
                mitigated = self._is_mitigated_above(highs, lows, idx + 1, n, first_low)
                bearish_zones.append(StructureZone("fvg", "bearish", third_high, first_low, round(strength, 3), idx, mitigated))

        # --- Order Block detection ---
        for idx in range(n - 3):
            candle_open = opens[idx]
            candle_close = closes[idx]
            candle_high = highs[idx]
            candle_low = lows[idx]
            future_closes = closes[idx + 1: idx + 4]

            # Bullish OB: bearish candle followed by strong move up
            if candle_close < candle_open and max(future_closes) > candle_high + avg_range * 0.3:
                freshness = 1.0 - (n - idx) / max(n, 1) * 0.5
                displacement = (max(future_closes) - candle_high) / max(avg_range, 1e-9)
                strength = min(1.0, displacement * 0.3 * freshness)
                mitigated = self._is_mitigated_below(highs, lows, idx + 4, n, candle_low)
                bullish_zones.append(StructureZone("ob", "bullish", candle_low, candle_open, round(strength, 3), idx, mitigated))

            # Bearish OB: bullish candle followed by strong move down
            if candle_close > candle_open and min(future_closes) < candle_low - avg_range * 0.3:
                freshness = 1.0 - (n - idx) / max(n, 1) * 0.5
                displacement = (candle_low - min(future_closes)) / max(avg_range, 1e-9)
                strength = min(1.0, displacement * 0.3 * freshness)
                mitigated = self._is_mitigated_above(highs, lows, idx + 4, n, candle_high)
                bearish_zones.append(StructureZone("ob", "bearish", candle_close, candle_high, round(strength, 3), idx, mitigated))

        # Build support/resistance from unmitigated zones + swing points
        swing_support = self._swing_lows(lows, current_price, lookback=20)
        swing_resistance = self._swing_highs(highs, current_price, lookback=20)
        support_levels = sorted(set(
            [z.low for z in bullish_zones if not z.mitigated and z.low < current_price] + swing_support
        ))
        resistance_levels = sorted(set(
            [z.high for z in bearish_zones if not z.mitigated and z.high > current_price] + swing_resistance
        ))

        return ZoneContext(
            bullish_fvg=self._nearest_zone([z for z in bullish_zones if z.kind == "fvg"], current_price, below=True),
            bearish_fvg=self._nearest_zone([z for z in bearish_zones if z.kind == "fvg"], current_price, below=False),
            bullish_ob=self._nearest_zone([z for z in bullish_zones if z.kind == "ob"], current_price, below=True),
            bearish_ob=self._nearest_zone([z for z in bearish_zones if z.kind == "ob"], current_price, below=False),
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            all_bullish_zones=[z for z in bullish_zones if not z.mitigated],
            all_bearish_zones=[z for z in bearish_zones if not z.mitigated],
        )

    @staticmethod
    def _is_mitigated_below(highs, lows, start_idx, end_idx, zone_low):
        """A bullish zone is mitigated if price went back below zone low after creation."""
        for i in range(start_idx, min(end_idx, len(lows))):
            if lows[i] < zone_low:
                return True
        return False

    @staticmethod
    def _is_mitigated_above(highs, lows, start_idx, end_idx, zone_high):
        """A bearish zone is mitigated if price went back above zone high after creation."""
        for i in range(start_idx, min(end_idx, len(highs))):
            if highs[i] > zone_high:
                return True
        return False

    @staticmethod
    def _swing_lows(lows, current_price, lookback=20):
        """Find swing low levels below current price."""
        if len(lows) < 5:
            return []
        result = []
        window = lows[-lookback:]
        for i in range(2, len(window) - 2):
            if window[i] < window[i-1] and window[i] < window[i-2] and window[i] < window[i+1] and window[i] < window[i+2]:
                if window[i] < current_price:
                    result.append(window[i])
        return result

    @staticmethod
    def _swing_highs(highs, current_price, lookback=20):
        """Find swing high levels above current price."""
        if len(highs) < 5:
            return []
        result = []
        window = highs[-lookback:]
        for i in range(2, len(window) - 2):
            if window[i] > window[i-1] and window[i] > window[i-2] and window[i] > window[i+1] and window[i] > window[i+2]:
                if window[i] > current_price:
                    result.append(window[i])
        return result

    @staticmethod
    def _nearest_zone(zones: List[StructureZone], current_price: float, below: bool) -> Optional[StructureZone]:
        candidates = []
        for zone in zones:
            if zone.mitigated:
                continue
            reference = zone.high if below else zone.low
            if below and reference <= current_price:
                candidates.append(zone)
            if (not below) and reference >= current_price:
                candidates.append(zone)
        if not candidates:
            return None
        return min(candidates, key=lambda z: abs(z.mid - current_price))
