#!/usr/bin/env python3
"""
Funding Rate Filter — фильтр на основе funding rate и OI.
"""
from __future__ import annotations

import logging
from typing import Dict, Mapping, Optional

logger = logging.getLogger("analysis.funding_filter")


def oi_contracts(row: Mapping) -> float:
    """Контракты одной стороны: singleOpenInterest, иначе старое openInterest."""
    if not isinstance(row, Mapping):
        return 0.0
    raw = row.get("singleOpenInterest")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    try:
        return float(row.get("openInterest") or 0)
    except (TypeError, ValueError):
        return 0.0


def ticker_derivatives(row: Mapping) -> Dict[str, float]:
    """Funding и OI из строки get_tickers. С 11.06.2026 OI — одна сторона."""

    def _f(key: str, default: float = 0.0) -> float:
        if not isinstance(row, Mapping):
            return default
        val = row.get(key)
        if val in (None, ""):
            return default
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    return {
        "funding_rate": _f("fundingRate"),
        "open_interest": oi_contracts(row),
        "open_interest_value": _f("singleOpenInterestValue"),
        "bid1": _f("bid1Price"),
        "ask1": _f("ask1Price"),
    }


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

    def __init__(
        self,
        high_threshold: float | None = None,
        extreme_threshold: float | None = None,
        oi_significant: float | None = None,
    ):
        if high_threshold is not None:
            self.HIGH_THRESHOLD = float(high_threshold)
        if extreme_threshold is not None:
            self.EXTREME_THRESHOLD = float(extreme_threshold)
        if oi_significant is not None:
            self.OI_SIGNIFICANT = float(oi_significant)

    async def analyze(self, client, symbol: str) -> FundingSignal:
        """Анализирует funding и OI для символа."""
        signal = FundingSignal()

        try:
            # Funding и текущий OI — из уже скачанного списка тикеров, если он свежий.
            ticker_data = await client.get_funding_rate(symbol)
            if ticker_data:
                signal.funding_rate = float(ticker_data.get("funding_rate", 0) or 0)
                signal.open_interest = float(ticker_data.get("open_interest", 0) or 0)

            # История OI — отдельный HTTP. Только когда по тикеру уже крайний funding.
            if abs(signal.funding_rate) >= self.EXTREME_THRESHOLD and hasattr(
                client, "get_open_interest_history"
            ):
                oi_history = await client.get_open_interest_history(symbol)
                if len(oi_history) >= 2:
                    current_oi = oi_contracts(oi_history[0])
                    old_oi = oi_contracts(oi_history[-1])
                    if old_oi > 0:
                        signal.oi_change_pct = (current_oi - old_oi) / old_oi

        except Exception as exc:
            logger.warning("[FUNDING] Error %s: %s", symbol, exc)
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
