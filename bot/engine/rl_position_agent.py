#!/usr/bin/env python3
"""RL-style position management agent v2.

Enhanced with:
- Market regime awareness (trend/range/breakout adapts thresholds)
- Position age factor (longer hold → more conservative)
- Drawdown from peak tracking
- TIGHTEN action (move SL closer without closing)
- Adaptive confidence thresholds per regime
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("RL_AGENT")


class RLAction(Enum):
    HOLD = "hold"
    ADD = "add"
    REDUCE = "reduce"
    CLOSE = "close"
    TIGHTEN = "tighten"


@dataclass
class RLDecision:
    action: RLAction = RLAction.HOLD
    confidence: float = 0.0
    fraction: float = 0.0
    reason: str = ""


# Regime-adaptive thresholds
REGIME_PROFILES = {
    "trend": {
        "add_threshold": 0.65,
        "reduce_threshold": 0.72,
        "close_threshold": 0.78,
        "tighten_threshold": 0.55,
        "max_hold_bars": 120,
    },
    "breakout": {
        "add_threshold": 0.70,
        "reduce_threshold": 0.68,
        "close_threshold": 0.75,
        "tighten_threshold": 0.50,
        "max_hold_bars": 80,
    },
    "chop": {
        "add_threshold": 0.90,
        "reduce_threshold": 0.60,
        "close_threshold": 0.65,
        "tighten_threshold": 0.45,
        "max_hold_bars": 40,
    },
}


class RLPositionAgent:
    """Enhanced PPO-style policy with regime awareness and adaptive thresholds."""

    def __init__(
        self,
        add_threshold: float = 0.78,
        reduce_threshold: float = 0.7,
        close_threshold: float = 0.8,
        min_close_profit_pct: float = 0.5,
        max_panic_loss_pct: float = 0.6,
        min_reduce_profit_pct: float = 0.8,
    ):
        self.base_add_threshold = add_threshold
        self.base_reduce_threshold = reduce_threshold
        self.base_close_threshold = close_threshold
        self.min_close_profit_pct = min_close_profit_pct
        self.max_panic_loss_pct = max_panic_loss_pct
        self.min_reduce_profit_pct = min_reduce_profit_pct

    def _get_thresholds(self, regime: str) -> dict:
        """Get regime-adaptive thresholds, falling back to base values."""
        profile = REGIME_PROFILES.get(regime)
        if profile:
            return profile
        return {
            "add_threshold": self.base_add_threshold,
            "reduce_threshold": self.base_reduce_threshold,
            "close_threshold": self.base_close_threshold,
            "tighten_threshold": 0.55,
            "max_hold_bars": 100,
        }

    def decide(self, position, state: dict) -> RLDecision:
        trend_bias = state.get("trend_bias", 0.0)
        volatility = state.get("volatility", 0.0)
        pnl_pct = state.get("pnl_pct", 0.0)
        liq_signal = state.get("liq_signal", 0)
        orderflow_edge = state.get("orderflow_edge", 0.0)
        transformer_edge = state.get("transformer_edge", 0.0)
        regime = state.get("regime", "chop")
        bars_held = state.get("bars_held", 0)
        drawdown_from_peak_pct = state.get("drawdown_from_peak_pct", 0.0)

        thresholds = self._get_thresholds(regime)
        side = 1 if position.is_long else -1
        aligned_edge = (trend_bias + transformer_edge + orderflow_edge) * side
        adverse_edge = -aligned_edge

        # Age penalty: older positions get more conservative management
        age_factor = min(bars_held / max(thresholds["max_hold_bars"], 1), 1.0)
        age_penalty = age_factor * 0.15

        # --- CLOSE ---
        close_score = max(
            0.0,
            adverse_edge * 0.50
            + max(volatility - 0.03, 0) * 6
            + max(-pnl_pct, 0) * 0.08
            + age_penalty
            + max(drawdown_from_peak_pct - 30, 0) * 0.01,
        )
        if liq_signal * side < 0:
            close_score += 0.2

        can_close_profit = pnl_pct >= self.min_close_profit_pct
        can_close_loss = pnl_pct <= -self.max_panic_loss_pct
        stale = bars_held >= thresholds["max_hold_bars"] and pnl_pct < 0.1

        if close_score >= thresholds["close_threshold"] and (can_close_profit or can_close_loss or stale):
            reason = "pressure flipped" if adverse_edge > 0.3 else ("stale" if stale else "drawdown/volatility")
            logger.info(
                f"[RL CLOSE] score={close_score:.3f} thr={thresholds['close_threshold']:.2f} "
                f"regime={regime} pnl={pnl_pct:.2f}% reason={reason}"
            )
            return RLDecision(RLAction.CLOSE, round(min(close_score, 0.98), 4), 1.0, f"RL close: {reason}")

        # --- REDUCE ---
        reduce_score = max(
            0.0,
            adverse_edge * 0.40
            + max(volatility - 0.025, 0) * 5
            + max(pnl_pct, 0) * 0.015
            + max(drawdown_from_peak_pct - 20, 0) * 0.008,
        )
        if reduce_score >= thresholds["reduce_threshold"] and pnl_pct >= self.min_reduce_profit_pct:
            logger.info(
                f"[RL REDUCE] score={reduce_score:.3f} thr={thresholds['reduce_threshold']:.2f} "
                f"regime={regime} pnl={pnl_pct:.2f}%"
            )
            return RLDecision(RLAction.REDUCE, round(min(reduce_score, 0.95), 4), 0.5, "RL reduce: lock gains")

        # --- TIGHTEN (move SL closer) ---
        tighten_score = max(
            0.0,
            adverse_edge * 0.35
            + max(volatility - 0.02, 0) * 4
            + age_penalty * 0.5,
        )
        if tighten_score >= thresholds["tighten_threshold"] and pnl_pct > 0.2:
            logger.info(
                f"[RL TIGHTEN] score={tighten_score:.3f} thr={thresholds['tighten_threshold']:.2f} "
                f"regime={regime} pnl={pnl_pct:.2f}%"
            )
            return RLDecision(RLAction.TIGHTEN, round(min(tighten_score, 0.9), 4), 0.3, "RL tighten: reduce risk exposure")

        # --- ADD ---
        add_score = max(
            0.0,
            aligned_edge * 0.70
            + max(pnl_pct, 0) * 0.015
            - max(volatility - 0.02, 0) * 5
            - age_penalty,
        )
        if add_score >= thresholds["add_threshold"] and liq_signal * side >= 0:
            logger.info(
                f"[RL ADD] score={add_score:.3f} thr={thresholds['add_threshold']:.2f} "
                f"regime={regime} pnl={pnl_pct:.2f}%"
            )
            return RLDecision(RLAction.ADD, round(min(add_score, 0.9), 4), 0.25, "RL add: trend aligned")

        return RLDecision(RLAction.HOLD, round(max(aligned_edge, 0.0), 4), 0.0, "RL hold")
