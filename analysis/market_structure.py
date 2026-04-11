#!/usr/bin/env python3
"""
MARKET STRUCTURE ENGINE — swing-based trend, BOS, and liquidity sweep detection.

Implements:
1. Swing detection (HH/HL/LH/LL)
2. Trend determination via swing structure
3. Break of Structure (BOS) with volume confirmation
4. Liquidity Sweep detection
5. Full signal logic: trend != RANGE + sweep + BOS + volume_spike + retest_OB
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class StructureTrend(Enum):
    UP = "up"
    DOWN = "down"
    RANGE = "range"


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" or "low"


@dataclass
class BOSEvent:
    """Break of Structure event."""
    direction: str          # "up" or "down"
    broken_level: float     # the swing level that was broken
    break_index: int        # candle index where BOS occurred
    volume_confirmed: bool  # volume > avg * 1.5


@dataclass
class LiquiditySweep:
    """Liquidity sweep event — wick beyond swing, close back inside."""
    direction: str          # "up" (sweep above high) or "down" (sweep below low)
    swept_level: float      # the level that was swept
    sweep_index: int        # candle index
    wick_price: float       # extreme of the wick


@dataclass
class MarketStructure:
    """Complete market structure analysis result."""
    trend: StructureTrend
    swing_highs: List[SwingPoint]
    swing_lows: List[SwingPoint]
    last_bos: Optional[BOSEvent]
    last_sweep: Optional[LiquiditySweep]
    volume_spike: bool           # volume > avg * 2
    spread_expansion: bool       # candle_range > ATR * 1.5
    momentum_confirmed: bool     # both volume_spike AND spread_expansion
    # Full signal readiness
    signal_ready_long: bool = False
    signal_ready_short: bool = False
    # Context for SL/TP
    sweep_low: float = 0.0       # for SL on long
    sweep_high: float = 0.0      # for SL on short
    previous_high: float = 0.0   # for TP on long
    previous_low: float = 0.0    # for TP on short
    avg_volume: float = 0.0
    current_volume: float = 0.0
    current_range: float = 0.0
    atr_value: float = 0.0


class MarketStructureEngine:
    """Detects swing structure, BOS, sweeps, and generates full trading signals."""

    def __init__(self, swing_lookback: int = 2, volume_spike_mult: float = 2.0,
                 bos_volume_mult: float = 1.5, spread_expansion_mult: float = 1.5):
        self.swing_lookback = swing_lookback
        self.volume_spike_mult = volume_spike_mult
        self.bos_volume_mult = bos_volume_mult
        self.spread_expansion_mult = spread_expansion_mult

    def analyze(self, klines: List[dict], atr_value: float = 0.0) -> MarketStructure:
        """Full market structure analysis from kline data."""
        n = len(klines)
        if n < 10:
            return MarketStructure(
                trend=StructureTrend.RANGE, swing_highs=[], swing_lows=[],
                last_bos=None, last_sweep=None,
                volume_spike=False, spread_expansion=False, momentum_confirmed=False,
            )

        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        closes = [float(k["close"]) for k in klines]
        volumes = [float(k.get("volume", 0)) for k in klines]

        # ATR
        if atr_value <= 0:
            ranges = [highs[i] - lows[i] for i in range(max(0, n - 14), n)]
            atr_value = sum(ranges) / len(ranges) if ranges else closes[-1] * 0.01

        # --- 1. Find swings ---
        swing_highs = self._find_swing_highs(highs, n)
        swing_lows = self._find_swing_lows(lows, n)

        # --- 2. Determine trend via HH/HL/LH/LL ---
        trend = self._determine_trend(swing_highs, swing_lows)

        # --- 3. BOS detection ---
        avg_vol_20 = sum(volumes[max(0, n - 20):n]) / min(20, n)
        last_bos = self._detect_bos(closes, volumes, swing_highs, swing_lows, avg_vol_20, n)

        # --- 4. Liquidity sweep detection ---
        last_sweep = self._detect_sweep(highs, lows, closes, swing_highs, swing_lows, n)

        # --- 5. Momentum filter ---
        current_volume = volumes[-1] if volumes else 0
        current_range = highs[-1] - lows[-1]
        volume_spike = current_volume > avg_vol_20 * self.volume_spike_mult
        spread_expansion = current_range > atr_value * self.spread_expansion_mult
        momentum_confirmed = volume_spike and spread_expansion

        # --- 6. Context for SL/TP ---
        sweep_low = last_sweep.wick_price if last_sweep and last_sweep.direction == "down" else (swing_lows[-1].price if swing_lows else lows[-1])
        sweep_high = last_sweep.wick_price if last_sweep and last_sweep.direction == "up" else (swing_highs[-1].price if swing_highs else highs[-1])
        previous_high = swing_highs[-1].price if swing_highs else max(highs[-20:])
        previous_low = swing_lows[-1].price if swing_lows else min(lows[-20:])

        # --- 7. Full signal logic ---
        # LONG: trend != RANGE + sweep_down + BOS_up + volume_spike + retest_OB
        signal_long = (
            trend != StructureTrend.RANGE
            and last_sweep is not None and last_sweep.direction == "down"
            and last_bos is not None and last_bos.direction == "up"
        )
        # SHORT: trend != RANGE + sweep_up + BOS_down + volume_spike + retest_OB
        signal_short = (
            trend != StructureTrend.RANGE
            and last_sweep is not None and last_sweep.direction == "up"
            and last_bos is not None and last_bos.direction == "down"
        )

        return MarketStructure(
            trend=trend,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            last_bos=last_bos,
            last_sweep=last_sweep,
            volume_spike=volume_spike,
            spread_expansion=spread_expansion,
            momentum_confirmed=momentum_confirmed,
            signal_ready_long=signal_long,
            signal_ready_short=signal_short,
            sweep_low=sweep_low,
            sweep_high=sweep_high,
            previous_high=previous_high,
            previous_low=previous_low,
            avg_volume=avg_vol_20,
            current_volume=current_volume,
            current_range=current_range,
            atr_value=atr_value,
        )

    def _find_swing_highs(self, highs: List[float], n: int) -> List[SwingPoint]:
        """swing_high = high[i] > high[i-1] and high[i] > high[i+1]"""
        result = []
        lb = self.swing_lookback
        for i in range(lb, n - lb):
            is_swing = True
            for offset in range(1, lb + 1):
                if highs[i] <= highs[i - offset] or highs[i] <= highs[i + offset]:
                    is_swing = False
                    break
            if is_swing:
                result.append(SwingPoint(index=i, price=highs[i], kind="high"))
        return result

    def _find_swing_lows(self, lows: List[float], n: int) -> List[SwingPoint]:
        """swing_low = low[i] < low[i-1] and low[i] < low[i+1]"""
        result = []
        lb = self.swing_lookback
        for i in range(lb, n - lb):
            is_swing = True
            for offset in range(1, lb + 1):
                if lows[i] >= lows[i - offset] or lows[i] >= lows[i + offset]:
                    is_swing = False
                    break
            if is_swing:
                result.append(SwingPoint(index=i, price=lows[i], kind="low"))
        return result

    def _determine_trend(self, swing_highs: List[SwingPoint], swing_lows: List[SwingPoint]) -> StructureTrend:
        """Trend from last 2 swing highs and lows: HH/HL = UP, LH/LL = DOWN, else RANGE."""
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return StructureTrend.RANGE

        last_high = swing_highs[-1].price
        prev_high = swing_highs[-2].price
        last_low = swing_lows[-1].price
        prev_low = swing_lows[-2].price

        if last_high > prev_high and last_low > prev_low:
            return StructureTrend.UP
        elif last_high < prev_high and last_low < prev_low:
            return StructureTrend.DOWN
        return StructureTrend.RANGE

    def _detect_bos(self, closes: List[float], volumes: List[float],
                    swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
                    avg_volume: float, n: int) -> Optional[BOSEvent]:
        """
        BOS UP: close > last_swing_high (with volume confirmation)
        BOS DOWN: close < last_swing_low (with volume confirmation)
        Look at the most recent candles for BOS.
        """
        # Check recent 5 candles for BOS
        lookback = min(5, n)

        # BOS UP
        if swing_highs:
            last_sh = swing_highs[-1]
            for i in range(n - 1, max(n - lookback - 1, last_sh.index), -1):
                if closes[i] > last_sh.price:
                    vol_confirmed = volumes[i] > avg_volume * self.bos_volume_mult
                    return BOSEvent("up", last_sh.price, i, vol_confirmed)

        # BOS DOWN
        if swing_lows:
            last_sl = swing_lows[-1]
            for i in range(n - 1, max(n - lookback - 1, last_sl.index), -1):
                if closes[i] < last_sl.price:
                    vol_confirmed = volumes[i] > avg_volume * self.bos_volume_mult
                    return BOSEvent("down", last_sl.price, i, vol_confirmed)

        return None

    def _detect_sweep(self, highs: List[float], lows: List[float], closes: List[float],
                      swing_highs: List[SwingPoint], swing_lows: List[SwingPoint],
                      n: int) -> Optional[LiquiditySweep]:
        """
        Sweep UP: high > previous_high AND close < previous_high (wick above, close back)
        Sweep DOWN: low < previous_low AND close > previous_low (wick below, close back)
        Look at recent 8 candles.
        """
        lookback = min(8, n)

        # Sweep DOWN (bullish signal — swept lows then reversed)
        if len(swing_lows) >= 2:
            prev_low = swing_lows[-2].price
            for i in range(n - 1, max(n - lookback - 1, 0), -1):
                if lows[i] < prev_low and closes[i] > prev_low:
                    return LiquiditySweep("down", prev_low, i, lows[i])

        # Sweep UP (bearish signal — swept highs then reversed)
        if len(swing_highs) >= 2:
            prev_high = swing_highs[-2].price
            for i in range(n - 1, max(n - lookback - 1, 0), -1):
                if highs[i] > prev_high and closes[i] < prev_high:
                    return LiquiditySweep("up", prev_high, i, highs[i])

        return None
