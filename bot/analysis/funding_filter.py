#!/usr/bin/env python3
"""
Funding Rate Filter — фильтр на основе funding rate и OI.

Логика из оценки:
- Высокий + funding = много лонгов → риск сквиза вниз
- Высокий - funding = много шортов → риск сквиза вверх
- Растущий OI + рост цены = сильный тренд
- Падающий OI + рост цены = слабый тренд
"""
from typing import Dict, Optional


class FundingSignal:
    def __init__(self):
        self.funding_rate: float = 0.0
        self.open_interest: float = 0.0
        self.oi_change_pct: float = 0.0
        self.sentiment: str = "neutral"
        self.signal: int = 0  # 1 = bullish, -1 = bearish, 0 = neutral
        self.strength: float = 0.0
        self.should_filter: bool = False  # True = DON'T trade in this direction
        self.reason: str = ""


class FundingFilter:
    """
    Анализирует funding rate для фильтрации входов.

    Используется как фильтр, а не генератор сигналов:
    - Если хотим LONG, но funding экстремально положительный → фильтруем
    - Если хотим SHORT, но funding экстремально отрицательный → фильтруем
    """

    HIGH_THRESHOLD = 0.0005    # 0.05%
    EXTREME_THRESHOLD = 0.001  # 0.1%
    OI_SIGNIFICANT = 0.05      # 5%

    def __init__(self, high_threshold: float = None, extreme_threshold: float = None):
        if high_threshold:
            self.HIGH_THRESHOLD = high_threshold
        if extreme_threshold:
            self.EXTREME_THRESHOLD = extreme_threshold

    async def analyze(self, client, symbol: str) -> FundingSignal:
        """Анализирует funding и OI для символа."""
        signal = FundingSignal()

        try:
            # Получаем funding из тикера
            ticker_data = await client.get_funding_rate(symbol)
            if ticker_data:
                signal.funding_rate = ticker_data["funding_rate"]
                signal.open_interest = ticker_data["open_interest"]

            # Получаем историю OI
            oi_history = await client.get_open_interest_history(symbol)
            if len(oi_history) >= 2:
                current_oi = float(oi_history[0].get("openInterest", 0))
                old_oi = float(oi_history[-1].get("openInterest", 0))
                if old_oi > 0:
                    signal.oi_change_pct = (current_oi - old_oi) / old_oi

        except Exception as e:
            print(f"[FUNDING] Error {symbol}: {e}")
            return signal

        funding = signal.funding_rate

        # Определяем sentiment
        if funding > self.EXTREME_THRESHOLD:
            signal.sentiment = "extreme_long"
            signal.signal = -1  # Contra: expect squeeze down
            signal.strength = 0.8
            signal.reason = f"Extreme + funding {funding*100:.3f}%, risk of long squeeze"
        elif funding > self.HIGH_THRESHOLD:
            signal.sentiment = "crowded_long"
            signal.signal = -1
            signal.strength = 0.5
            signal.reason = f"High + funding {funding*100:.3f}%"
        elif funding < -self.EXTREME_THRESHOLD:
            signal.sentiment = "extreme_short"
            signal.signal = 1  # Contra: expect squeeze up
            signal.strength = 0.8
            signal.reason = f"Extreme - funding {funding*100:.3f}%, risk of short squeeze"
        elif funding < -self.HIGH_THRESHOLD:
            signal.sentiment = "crowded_short"
            signal.signal = 1
            signal.strength = 0.5
            signal.reason = f"High - funding {funding*100:.3f}%"
        else:
            signal.sentiment = "neutral"
            signal.signal = 0
            signal.strength = 0.0
            signal.reason = "Neutral funding"

        # OI confirmation
        if signal.oi_change_pct > self.OI_SIGNIFICANT:
            signal.strength = min(1.0, signal.strength + 0.1)
            signal.reason += f" | OI +{signal.oi_change_pct*100:.1f}%"

        return signal

    def should_filter_entry(self, funding_signal: FundingSignal, entry_side: str) -> tuple:
        """
        Проверяет, нужно ли отфильтровать вход.

        Returns:
            (should_filter: bool, reason: str)
        """
        if funding_signal.strength < 0.5:
            return False, ""

        is_long = entry_side.upper() in ["BUY", "LONG"]

        # Хотим LONG, но funding экстремально +  → рискованно
        if is_long and funding_signal.sentiment in ["extreme_long", "crowded_long"]:
            if funding_signal.strength >= 0.8:
                return True, f"BLOCKED: {funding_signal.reason}"
            return False, f"WARNING: {funding_signal.reason}"

        # Хотим SHORT, но funding экстремально - → рискованно
        if not is_long and funding_signal.sentiment in ["extreme_short", "crowded_short"]:
            if funding_signal.strength >= 0.8:
                return True, f"BLOCKED: {funding_signal.reason}"
            return False, f"WARNING: {funding_signal.reason}"

        return False, ""
