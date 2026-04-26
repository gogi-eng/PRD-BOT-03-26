#!/usr/bin/env python3
"""
Hybrid Voting PRO — веса XGB / LSTM(or seq) / Gemma на [0,1], пороги 0.65 / 0.35.

Отличие от ``engine.hybrid_voter.HybridVoter``: там сигналы в [-1,1] и симметричные пороги ±threshold.
Здесь — ближе к «вероятностному» ансамблю; входы должны быть в [0,1] (калиброванные proba).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple


Decision = Literal["LONG", "SHORT", "HOLD"]


@dataclass
class HybridVotingPro:
    w_xgb: float = 0.4
    w_lstm: float = 0.3
    w_gemma: float = 0.3
    long_thr: float = 0.65
    short_thr: float = 0.35

    def __post_init__(self) -> None:
        s = self.w_xgb + self.w_lstm + self.w_gemma
        if s <= 0:
            raise ValueError("HybridVotingPro: weights must sum > 0")
        self.w_xgb /= s
        self.w_lstm /= s
        self.w_gemma /= s

    def score(self, xgb: float, lstm: float, gemma: float) -> float:
        """Взвешенная комбинация на [0,1], если все входы в [0,1]."""
        return (
            float(xgb) * self.w_xgb + float(lstm) * self.w_lstm + float(gemma) * self.w_gemma
        )

    def combine(self, xgb: float, lstm: float, gemma: float) -> Decision:
        sc = self.score(xgb, lstm, gemma)
        if sc > self.long_thr:
            return "LONG"
        if sc < self.short_thr:
            return "SHORT"
        return "HOLD"

    def combine_with_confidence(self, xgb: float, lstm: float, gemma: float) -> Tuple[Decision, float]:
        sc = self.score(xgb, lstm, gemma)
        if sc > self.long_thr:
            return "LONG", sc
        if sc < self.short_thr:
            return "SHORT", sc
        return "HOLD", sc
