#!/usr/bin/env python3
"""
Market Regime Detector — clean, numpy-free implementation.

Classifies market into: trend | volatile | range
Based on price action statistics (no ML, no weights needed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from analysis.market_analyzer import MarketRegime


@dataclass
class RegimePrediction:
    regime: MarketRegime = MarketRegime.CHOP
    confidence: float = 0.0
    tree_votes: Dict[str, int] = field(default_factory=dict)
    reason: str = ""


class MarketRegimeAI:
    """Detects market regime from kline statistics.

    Logic (from user's quant spec):
    - If |trend| > volatility → TREND
    - If volatility > mean * threshold → BREAKOUT (volatile)
    - Otherwise → CHOP (range)

    Enhanced with ADX and volume expansion for higher accuracy.
    """

    def __init__(self, volatility_threshold: float = 0.02):
        self.volatility_threshold = volatility_threshold

    def classify(self, market) -> RegimePrediction:
        votes = {MarketRegime.TREND.value: 0, MarketRegime.CHOP.value: 0, MarketRegime.BREAKOUT.value: 0}
        reasons = []

        # --- Method 1: ADX-based (classic) ---
        if market.adx >= 25 and market.trend.value != 0:
            votes[MarketRegime.TREND.value] += 2
            reasons.append(f"ADX={market.adx:.0f} strong trend")
        elif market.adx >= 20 and market.trend.value != 0:
            votes[MarketRegime.TREND.value] += 1
            reasons.append(f"ADX={market.adx:.0f} moderate trend")
        elif market.adx < 18:
            votes[MarketRegime.CHOP.value] += 1
            reasons.append(f"ADX={market.adx:.0f} weak")

        # --- Method 2: Volatility vs trend (user's approach) ---
        # atr_pct is ATR/price * 100
        volatility = market.atr_pct / 100  # normalize to decimal
        if volatility > self.volatility_threshold:
            votes[MarketRegime.BREAKOUT.value] += 1
            reasons.append(f"vol={volatility:.3f} > {self.volatility_threshold}")
        else:
            votes[MarketRegime.CHOP.value] += 1

        # --- Method 3: Range compression + volume expansion ---
        if market.range_compression <= 0.82 and market.volume_expansion >= 1.25:
            votes[MarketRegime.BREAKOUT.value] += 1
            reasons.append("compression+volume_expansion")
        elif market.range_compression >= 1.05 and market.volume_expansion < 1.1:
            votes[MarketRegime.CHOP.value] += 1
            reasons.append("expanded_range+low_volume")

        # --- Method 4: HTF alignment bonus ---
        if market.htf_trend == market.trend and market.trend.value != 0:
            votes[MarketRegime.TREND.value] += 1
            reasons.append("HTF_aligned")

        winner = max(votes, key=votes.get)
        total = max(1, sum(votes.values()))
        confidence = votes[winner] / total
        reason = " | ".join(reasons[:3]) if reasons else "default"

        return RegimePrediction(
            regime=MarketRegime(winner),
            confidence=round(confidence, 4),
            tree_votes=votes,
            reason=reason,
        )

    def detect_from_klines(self, klines: List[Dict]) -> str:
        """Simplified regime detection directly from klines (user's formula).

        Returns: 'trend', 'volatile', or 'range'
        """
        if len(klines) < 20:
            return "range"

        closes = [float(k["close"]) for k in klines]

        # Trend = end - start
        trend = closes[-1] - closes[0]

        # Volatility = std of closes
        mean_close = sum(closes) / len(closes)
        variance = sum((c - mean_close) ** 2 for c in closes) / len(closes)
        volatility = variance ** 0.5

        if abs(trend) > volatility:
            return "trend"
        if volatility > mean_close * self.volatility_threshold:
            return "volatile"
        return "range"
