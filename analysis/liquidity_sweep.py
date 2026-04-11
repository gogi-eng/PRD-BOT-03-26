#!/usr/bin/env python3
"""
Liquidity Sweep Detector — обнаружение ликвидити свипов.

Логика:
- Цена пробивает недавний high/low (забирает стопы)
- Но закрывается обратно внутри диапазона
- Это сигнал разворота (крупный игрок набрал ликвидность)
"""
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class SweepSignal:
    """Результат детекции свипа."""
    detected: bool = False
    direction: int = 0  # 1 = bullish (свип вниз → лонг), -1 = bearish (свип вверх → шорт)
    sweep_level: float = 0.0
    recovery_price: float = 0.0
    strength: float = 0.0  # 0-1
    description: str = ""


class LiquiditySweepDetector:
    """
    Детектирует ликвидити свипы — ключевой компонент Entry Engine.

    Sweep вниз (bullish): цена пробивает Low → возвращается → сигнал BUY
    Sweep вверх (bearish): цена пробивает High → возвращается → сигнал SELL
    """

    def __init__(self, lookback: int = 20, wick_ratio: float = 2.0,
                 min_break_pct: float = 0.1, recovery_threshold: float = 0.5):
        self.lookback = lookback
        self.wick_ratio = wick_ratio
        self.min_break_pct = min_break_pct
        self.recovery_threshold = recovery_threshold

    def detect(self, klines: List[Dict]) -> SweepSignal:
        """Анализирует свечи и ищет свип."""
        if len(klines) < self.lookback + 2:
            return SweepSignal()

        # Данные
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        closes = [float(k["close"]) for k in klines]
        opens = [float(k["open"]) for k in klines]

        # Последние N свечей (кроме текущей)
        lookback_highs = highs[-(self.lookback + 1):-1]
        lookback_lows = lows[-(self.lookback + 1):-1]

        recent_high = max(lookback_highs)
        recent_low = min(lookback_lows)

        # Текущая свеча
        curr_high = highs[-1]
        curr_low = lows[-1]
        curr_close = closes[-1]
        curr_open = opens[-1]
        body = abs(curr_close - curr_open)

        # === SWEEP DOWN (bullish signal) ===
        if curr_low < recent_low:
            break_amount = recent_low - curr_low
            break_pct = (break_amount / recent_low * 100) if recent_low > 0 else 0

            if break_pct >= self.min_break_pct:
                # Проверяем recovery: закрытие выше recent_low
                if curr_close > recent_low:
                    # Wick ratio: нижний фитиль должен быть длиннее тела
                    lower_wick = min(curr_open, curr_close) - curr_low
                    if body > 0 and lower_wick / body >= self.wick_ratio:
                        strength = min(1.0, break_pct / 0.5)
                        return SweepSignal(
                            detected=True,
                            direction=1,
                            sweep_level=recent_low,
                            recovery_price=curr_close,
                            strength=strength,
                            description=f"Sweep LOW {recent_low:.4f}, recovered to {curr_close:.4f} ({break_pct:.2f}%)"
                        )
                    # Даже без wick ratio, если recovery сильная
                    elif curr_close > (recent_low + recent_high) * self.recovery_threshold:
                        strength = min(0.7, break_pct / 0.5)
                        return SweepSignal(
                            detected=True,
                            direction=1,
                            sweep_level=recent_low,
                            recovery_price=curr_close,
                            strength=strength,
                            description=f"Weak sweep LOW {recent_low:.4f}, close={curr_close:.4f}"
                        )

        # === SWEEP UP (bearish signal) ===
        if curr_high > recent_high:
            break_amount = curr_high - recent_high
            break_pct = (break_amount / recent_high * 100) if recent_high > 0 else 0

            if break_pct >= self.min_break_pct:
                # Recovery: закрытие ниже recent_high
                if curr_close < recent_high:
                    upper_wick = curr_high - max(curr_open, curr_close)
                    if body > 0 and upper_wick / body >= self.wick_ratio:
                        strength = min(1.0, break_pct / 0.5)
                        return SweepSignal(
                            detected=True,
                            direction=-1,
                            sweep_level=recent_high,
                            recovery_price=curr_close,
                            strength=strength,
                            description=f"Sweep HIGH {recent_high:.4f}, reversed to {curr_close:.4f} ({break_pct:.2f}%)"
                        )
                    elif curr_close < (recent_low + recent_high) * (1 - self.recovery_threshold + 0.5):
                        strength = min(0.7, break_pct / 0.5)
                        return SweepSignal(
                            detected=True,
                            direction=-1,
                            sweep_level=recent_high,
                            recovery_price=curr_close,
                            strength=strength,
                            description=f"Weak sweep HIGH {recent_high:.4f}, close={curr_close:.4f}"
                        )

        return SweepSignal()

    def detect_multi_bar(self, klines: List[Dict], bars_back: int = 3) -> SweepSignal:
        """Ищет свип за последние N свечей (не только текущую)."""
        best = SweepSignal()
        for offset in range(bars_back):
            if len(klines) > offset + 1:
                sub = klines[:len(klines) - offset] if offset > 0 else klines
                signal = self.detect(sub)
                if signal.detected and signal.strength > best.strength:
                    best = signal
        return best
