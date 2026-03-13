#!/usr/bin/env python3
"""Market regime AI classifier with RandomForest-style voting."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from analysis.market_analyzer import MarketRegime


@dataclass
class RegimePrediction:
    regime: MarketRegime = MarketRegime.CHOP
    confidence: float = 0.0
    tree_votes: Dict[str, int] = field(default_factory=dict)
    reason: str = ""


class MarketRegimeAI:
    """Approximates a LightGBM/RandomForest classifier using voting rules."""

    def classify(self, market) -> RegimePrediction:
        votes = {MarketRegime.TREND.value: 0, MarketRegime.CHOP.value: 0, MarketRegime.BREAKOUT.value: 0}
        reasons = []

        if market.adx >= 22 and market.trend.value != 0 and market.htf_trend == market.trend:
            votes[MarketRegime.TREND.value] += 1
            reasons.append(f"ADX {market.adx:.1f} + HTF alignment")
        else:
            votes[MarketRegime.CHOP.value] += 1

        if market.range_compression <= 0.82 and market.volume_expansion >= 1.25 and market.current_range_pct >= market.atr_pct / 100:
            votes[MarketRegime.BREAKOUT.value] += 1
            reasons.append("Compression + volume expansion")
        elif market.range_compression >= 1.05 and abs(market.trend.value) == 0:
            votes[MarketRegime.CHOP.value] += 1

        if market.adx < 18 and market.volume_expansion < 1.1:
            votes[MarketRegime.CHOP.value] += 1
            reasons.append("Weak trend strength")
        elif market.adx >= 28:
            votes[MarketRegime.TREND.value] += 1

        winner = max(votes, key=votes.get)
        confidence = votes[winner] / max(1, sum(votes.values()))
        reason = ", ".join(reasons[:2]) if reasons else "Rule ensemble consensus"
        return RegimePrediction(regime=MarketRegime(winner), confidence=round(confidence, 4), tree_votes=votes, reason=reason)