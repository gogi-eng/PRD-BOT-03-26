#!/usr/bin/env python3
"""Transformer-style price direction predictor."""
from __future__ import annotations

from dataclasses import dataclass
from math import exp
import math
from typing import List

from analysis.market_analyzer import MarketRegime


@dataclass
class TransformerPrediction:
    prob_up: float = 0.0
    prob_down: float = 0.0
    prob_flat: float = 1.0
    expected_move: str = "flat"
    confidence: float = 0.0
    model_name: str = "transformer_encoder_128"


class TransformerPriceModel:
    """A deterministic transformer-inspired encoder for live signal scoring."""

    def __init__(self, sequence_length: int = 128):
        self.sequence_length = sequence_length

    def predict(self, features, regime_prediction, orderflow, liq_analysis) -> TransformerPrediction:
        if not features.sequence:
            return TransformerPrediction()

        recent = features.sequence[-32:]
        attention_inputs = [abs(vec[0]) * 2.2 + abs(vec[1]) * 1.8 + vec[5] * 0.3 + abs(vec[7]) for vec in recent]
        weights = self._softmax(attention_inputs)
        context = [sum(weight * vec[idx] for weight, vec in zip(weights, recent)) for idx in range(len(recent[0]))]

        momentum = context[1] * 1.4 + context[2] * 1.1
        volatility = context[3] + context[6]
        flow_edge = context[7] * 1.2 + context[8] * 0.9 - context[9] * 0.9
        liq_edge = context[11] * 0.8 - abs(context[12]) * 1.4 + context[13] * 0.5

        up_logit = momentum * 3.2 + flow_edge * 1.6 + liq_edge * 1.4
        down_logit = -momentum * 3.2 - flow_edge * 1.6 - liq_edge * 1.4
        flat_logit = 0.8 - abs(momentum) * 3.0 - abs(flow_edge) * 1.4

        if regime_prediction.regime == MarketRegime.CHOP:
            flat_logit += 0.7
        elif regime_prediction.regime == MarketRegime.BREAKOUT:
            up_logit += max(flow_edge, 0) * 0.4
            down_logit += max(-flow_edge, 0) * 0.4
        elif regime_prediction.regime == MarketRegime.TREND:
            up_logit += max(momentum, 0) * 0.6
            down_logit += max(-momentum, 0) * 0.6

        if volatility > 0.04:
            flat_logit += 0.2

        prob_up, prob_down, prob_flat = self._softmax([up_logit, down_logit, flat_logit])

        # Sigmoid calibration: prevent extreme probabilities (100% / 0%)
        # Clamp to [0.05, 0.85] range — no model should output 100% certainty
        prob_up = self._calibrate(prob_up)
        prob_down = self._calibrate(prob_down)
        prob_flat = self._calibrate(prob_flat)
        # Re-normalize to sum = 1.0
        total = prob_up + prob_down + prob_flat
        if total > 0:
            prob_up /= total
            prob_down /= total
            prob_flat /= total

        confidence = max(prob_up, prob_down, prob_flat)
        expected_move = "up" if prob_up >= max(prob_down, prob_flat) else "down" if prob_down >= max(prob_up, prob_flat) else "flat"
        return TransformerPrediction(
            prob_up=round(prob_up, 4),
            prob_down=round(prob_down, 4),
            prob_flat=round(prob_flat, 4),
            expected_move=expected_move,
            confidence=round(confidence, 4),
        )

    @staticmethod
    def _softmax(values: List[float]) -> List[float]:
        if not values:
            return []
        peak = max(values)
        exps = [exp(value - peak) for value in values]
        total = sum(exps) or 1.0
        return [value / total for value in exps]

    @staticmethod
    def _calibrate(prob: float, floor: float = 0.05, ceiling: float = 0.85) -> float:
        """Sigmoid calibration: compress extreme probabilities into realistic range.

        Prevents 100%/0% outputs. Maps [0,1] → [floor, ceiling].
        Uses sigmoid squashing for smooth transition.
        """
        # Apply sigmoid compression: prob → floor + (ceiling - floor) * sigmoid(logit)
        # where logit is derived from prob
        if prob <= 0.0:
            return floor
        if prob >= 1.0:
            return ceiling
        # Convert prob to logit, squash, convert back
        eps = 1e-7
        clamped = max(eps, min(1 - eps, prob))
        logit = math.log(clamped / (1 - clamped))
        # Reduce logit magnitude by 0.6x to flatten extremes
        dampened = logit * 0.6
        squashed = 1.0 / (1.0 + math.exp(-dampened))
        return floor + (ceiling - floor) * squashed