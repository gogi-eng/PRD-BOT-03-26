#!/usr/bin/env python3
"""RL-style position management agent."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RLAction(Enum):
    HOLD = "hold"
    ADD = "add"
    REDUCE = "reduce"
    CLOSE = "close"


@dataclass
class RLDecision:
    action: RLAction = RLAction.HOLD
    confidence: float = 0.0
    fraction: float = 0.0
    reason: str = ""


class RLPositionAgent:
    """A conservative PPO-style policy approximation for live management."""

    def __init__(self, add_threshold: float = 0.78, reduce_threshold: float = 0.7, close_threshold: float = 0.8):
        self.add_threshold = add_threshold
        self.reduce_threshold = reduce_threshold
        self.close_threshold = close_threshold

    def decide(self, position, state: dict) -> RLDecision:
        trend_bias = state.get("trend_bias", 0.0)
        volatility = state.get("volatility", 0.0)
        pnl_pct = state.get("pnl_pct", 0.0)
        liq_signal = state.get("liq_signal", 0)
        orderflow_edge = state.get("orderflow_edge", 0.0)
        transformer_edge = state.get("transformer_edge", 0.0)

        side = 1 if position.is_long else -1
        aligned_edge = (trend_bias + transformer_edge + orderflow_edge) * side
        adverse_edge = -aligned_edge

        close_score = max(0.0, adverse_edge * 0.55 + max(volatility - 0.03, 0) * 6 + max(-pnl_pct, 0) * 0.08)
        if liq_signal * side < 0:
            close_score += 0.2
        if close_score >= self.close_threshold:
            return RLDecision(RLAction.CLOSE, round(min(close_score, 0.98), 4), 1.0, "RL close: pressure flipped against position")

        reduce_score = max(0.0, adverse_edge * 0.4 + max(volatility - 0.025, 0) * 5 + max(pnl_pct, 0) * 0.015)
        if reduce_score >= self.reduce_threshold:
            return RLDecision(RLAction.REDUCE, round(min(reduce_score, 0.95), 4), 0.5, "RL reduce: lock gains under rising volatility")

        add_score = max(0.0, aligned_edge * 0.7 + max(pnl_pct, 0) * 0.015 - max(volatility - 0.02, 0) * 5)
        if add_score >= self.add_threshold and liq_signal * side >= 0:
            return RLDecision(RLAction.ADD, round(min(add_score, 0.9), 4), 0.25, "RL add: trend and orderflow remain aligned")

        return RLDecision(RLAction.HOLD, round(max(aligned_edge, 0.0), 4), 0.0, "RL hold")