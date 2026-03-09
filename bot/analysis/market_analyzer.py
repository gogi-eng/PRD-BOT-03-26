#!/usr/bin/env python3
"""
MARKET ANALYZER — единый анализатор рынка.
Объединяет: trend detection + volatility regime + market regime.
Заменяет все разрозненные analysis модули.
"""
from typing import Dict, List, Optional
from enum import Enum
import math


class MarketRegime(Enum):
    STRONG_TREND = "strong_trend"
    TREND = "trend"
    RANGE = "range"
    VOLATILE = "volatile"
    CRASH = "crash"
    PUMP = "pump"


class TrendDirection(Enum):
    BULLISH = 1
    BEARISH = -1
    NEUTRAL = 0


class VolatilityRegime(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class MarketAnalysis:
    """Результат анализа рынка."""
    def __init__(self):
        self.regime: MarketRegime = MarketRegime.RANGE
        self.trend: TrendDirection = TrendDirection.NEUTRAL
        self.volatility: VolatilityRegime = VolatilityRegime.NORMAL
        self.adx: float = 0.0
        self.rsi: float = 50.0
        self.atr_pct: float = 0.0
        self.ema_fast: float = 0.0
        self.ema_slow: float = 0.0
        self.htf_trend: TrendDirection = TrendDirection.NEUTRAL
        self.can_trade: bool = True
        self.reason: str = ""


class MarketAnalyzer:
    """
    Единый анализатор рынка.
    Определяет: тренд (HTF), волатильность, режим рынка.
    """

    def __init__(self, ema_fast_period: int = 21, ema_slow_period: int = 50,
                 adx_period: int = 14, rsi_period: int = 14,
                 atr_period: int = 14):
        self.ema_fast_period = ema_fast_period
        self.ema_slow_period = ema_slow_period
        self.adx_period = adx_period
        self.rsi_period = rsi_period
        self.atr_period = atr_period

    def analyze(self, klines: List[Dict], htf_klines: List[Dict] = None) -> MarketAnalysis:
        """Полный анализ рынка из свечей."""
        result = MarketAnalysis()
        if not klines or len(klines) < self.ema_slow_period + 5:
            result.can_trade = False
            result.reason = "Not enough klines"
            return result

        closes = [float(k["close"]) for k in klines]
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]

        # EMA
        result.ema_fast = self._ema(closes, self.ema_fast_period)
        result.ema_slow = self._ema(closes, self.ema_slow_period)

        # RSI
        result.rsi = self._rsi(closes, self.rsi_period)

        # ADX
        result.adx = self._adx(highs, lows, closes, self.adx_period)

        # ATR %
        atr = self._atr(highs, lows, closes, self.atr_period)
        current_price = closes[-1]
        result.atr_pct = (atr / current_price * 100) if current_price > 0 else 0

        # Trend direction (LTF)
        if result.ema_fast > result.ema_slow * 1.001:
            result.trend = TrendDirection.BULLISH
        elif result.ema_fast < result.ema_slow * 0.999:
            result.trend = TrendDirection.BEARISH
        else:
            result.trend = TrendDirection.NEUTRAL

        # HTF trend (4H candles)
        if htf_klines and len(htf_klines) >= self.ema_slow_period + 5:
            htf_closes = [float(k["close"]) for k in htf_klines]
            htf_ema_fast = self._ema(htf_closes, self.ema_fast_period)
            htf_ema_slow = self._ema(htf_closes, self.ema_slow_period)
            if htf_ema_fast > htf_ema_slow * 1.001:
                result.htf_trend = TrendDirection.BULLISH
            elif htf_ema_fast < htf_ema_slow * 0.999:
                result.htf_trend = TrendDirection.BEARISH
            else:
                result.htf_trend = TrendDirection.NEUTRAL

        # Volatility regime (based on ATR %)
        if result.atr_pct < 0.5:
            result.volatility = VolatilityRegime.LOW
        elif result.atr_pct < 1.5:
            result.volatility = VolatilityRegime.NORMAL
        elif result.atr_pct < 3.0:
            result.volatility = VolatilityRegime.HIGH
        else:
            result.volatility = VolatilityRegime.EXTREME

        # Market regime
        result.regime = self._determine_regime(result)

        # Can trade?
        if result.regime == MarketRegime.CRASH:
            result.can_trade = False
            result.reason = "Market crash detected"
        elif result.volatility == VolatilityRegime.EXTREME:
            result.can_trade = False
            result.reason = "Extreme volatility"
        elif result.adx < 15 and result.regime == MarketRegime.RANGE:
            result.can_trade = False
            result.reason = "Low ADX range — no clear direction"

        return result

    def _determine_regime(self, analysis: MarketAnalysis) -> MarketRegime:
        adx = analysis.adx
        atr_pct = analysis.atr_pct
        rsi = analysis.rsi

        # Crash: extreme sell-off
        if rsi < 20 and atr_pct > 3.0:
            return MarketRegime.CRASH
        # Pump: extreme buying
        if rsi > 80 and atr_pct > 3.0:
            return MarketRegime.PUMP
        # Volatile
        if atr_pct > 2.5:
            return MarketRegime.VOLATILE
        # Strong trend
        if adx > 35:
            return MarketRegime.STRONG_TREND
        # Trend
        if adx > 20:
            return MarketRegime.TREND
        # Range
        return MarketRegime.RANGE

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
