#!/usr/bin/env python3
"""Core market feature extraction for the AI-fund style entry stack."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class MarketRegime(Enum):
    TREND = "trend"
    CHOP = "chop"
    BREAKOUT = "breakout"


class TrendDirection(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


class VolatilityRegime(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


@dataclass
class MarketAnalysis:
    regime: MarketRegime = MarketRegime.CHOP
    trend: TrendDirection = TrendDirection.NEUTRAL
    htf_trend: TrendDirection = TrendDirection.NEUTRAL
    volatility: VolatilityRegime = VolatilityRegime.NORMAL
    adx: float = 0.0
    rsi: float = 50.0
    atr_pct: float = 0.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    range_compression: float = 1.0
    volume_expansion: float = 1.0
    current_range_pct: float = 0.0
    can_trade: bool = True
    reason: str = ""


class MarketAnalyzer:
    """Computes the features needed by the AI models and entry engine."""

    def __init__(self, ema_fast_period: int = 21, ema_slow_period: int = 55, adx_period: int = 14, rsi_period: int = 14, atr_period: int = 14):
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.adx_period = adx_period
        self.rsi_period = rsi_period
        self.atr_period = atr_period

    def analyze(self, klines: List[Dict], htf_klines: Optional[List[Dict]] = None) -> MarketAnalysis:
        result = MarketAnalysis()
        if not klines or len(klines) < self.ema_slow_period + 5:
            result.can_trade = False
            result.reason = "Not enough klines"
            return result

        closes = [float(k["close"]) for k in klines]
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        volumes = [float(k.get("volume", 0.0)) for k in klines]
        current_price = closes[-1]

        result.ema_fast = self._ema(closes, self.ema_fast_period)
        result.ema_slow = self._ema(closes, self.ema_slow_period)
        result.rsi = self._rsi(closes, self.rsi_period)
        result.adx = self._adx(highs, lows, closes, self.adx_period)
        atr = self._atr(highs, lows, closes, self.atr_period)
        result.atr_pct = (atr / current_price * 100) if current_price > 0 else 0.0
        result.current_range_pct = ((highs[-1] - lows[-1]) / current_price) if current_price > 0 else 0.0

        recent_ranges = [((highs[i] - lows[i]) / closes[i]) for i in range(max(0, len(closes) - 10), len(closes)) if closes[i] > 0]
        baseline_ranges = [((highs[i] - lows[i]) / closes[i]) for i in range(max(0, len(closes) - 40), max(0, len(closes) - 10)) if closes[i] > 0]
        avg_recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else 0.0
        avg_baseline_range = sum(baseline_ranges) / len(baseline_ranges) if baseline_ranges else avg_recent_range or 1.0
        result.range_compression = avg_recent_range / avg_baseline_range if avg_baseline_range > 0 else 1.0

        recent_volume = sum(volumes[-5:]) / max(1, len(volumes[-5:]))
        baseline_volume = sum(volumes[-25:-5]) / max(1, len(volumes[-25:-5])) if len(volumes) > 10 else recent_volume
        result.volume_expansion = recent_volume / baseline_volume if baseline_volume > 0 else 1.0

        result.trend = self._resolve_trend(current_price, result.ema_fast, result.ema_slow)
        if htf_klines and len(htf_klines) >= self.ema_slow_period + 5:
            htf_closes = [float(k["close"]) for k in htf_klines]
            htf_fast = self._ema(htf_closes, self.ema_fast_period)
            htf_slow = self._ema(htf_closes, self.ema_slow_period)
            result.htf_trend = self._resolve_trend(htf_closes[-1], htf_fast, htf_slow)
        else:
            result.htf_trend = result.trend

        if result.range_compression <= 0.82 and result.volume_expansion >= 1.25 and result.current_range_pct >= (result.atr_pct / 100):
            result.regime = MarketRegime.BREAKOUT
        elif result.adx >= 20 and result.trend.value != 0:
            result.regime = MarketRegime.TREND
        else:
            result.regime = MarketRegime.CHOP

        if result.atr_pct < 0.2:
            result.volatility = VolatilityRegime.LOW
        elif result.atr_pct < 1.2:
            result.volatility = VolatilityRegime.NORMAL
        elif result.atr_pct < 2.5:
            result.volatility = VolatilityRegime.HIGH
        else:
            result.volatility = VolatilityRegime.EXTREME

        if result.volatility == VolatilityRegime.EXTREME:
            result.can_trade = False
            result.reason = "Extreme volatility"
        return result

    @staticmethod
    def _resolve_trend(price: float, ema_fast: float, ema_slow: float) -> TrendDirection:
        if price > ema_fast > ema_slow:
            return TrendDirection.BULLISH
        if price < ema_fast < ema_slow:
            return TrendDirection.BEARISH
        if ema_fast > ema_slow * 1.001:
            return TrendDirection.BULLISH
        if ema_fast < ema_slow * 0.999:
            return TrendDirection.BEARISH
        return TrendDirection.NEUTRAL

    # === Индикаторы ===

    @staticmethod
    def _ema(data: List[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0.0
        multiplier = 2.0 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * multiplier + ema
        return ema

    @staticmethod
    def _rsi(closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(highs) < 2:
            return 0.0
        trs = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0.0
        atr = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atr = (atr * (period - 1) + trs[i]) / period
        return atr

    @staticmethod
    def _adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
        if len(highs) < period + 2:
            return 0.0

        plus_dm = []
        minus_dm = []
        trs = []

        for i in range(1, len(highs)):
            up = highs[i] - highs[i - 1]
            down = lows[i - 1] - lows[i]
            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs.append(tr)

        if len(trs) < period:
            return 0.0

        # Smooth
        atr_s = sum(trs[:period])
        plus_s = sum(plus_dm[:period])
        minus_s = sum(minus_dm[:period])

        dx_list = []
        for i in range(period, len(trs)):
            atr_s = atr_s - atr_s / period + trs[i]
            plus_s = plus_s - plus_s / period + plus_dm[i]
            minus_s = minus_s - minus_s / period + minus_dm[i]

            if atr_s == 0:
                continue
            plus_di = 100 * plus_s / atr_s
            minus_di = 100 * minus_s / atr_s
            di_sum = plus_di + minus_di
            if di_sum == 0:
                continue
            dx = 100 * abs(plus_di - minus_di) / di_sum
            dx_list.append(dx)

        if not dx_list:
            return 0.0
        if len(dx_list) < period:
            return sum(dx_list) / len(dx_list)

        adx = sum(dx_list[:period]) / period
        for i in range(period, len(dx_list)):
            adx = (adx * (period - 1) + dx_list[i]) / period
        return adx
