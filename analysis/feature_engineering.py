#!/usr/bin/env python3
"""Feature engineering for price, orderflow and liquidation context."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class FeatureBatch:
    sequence: List[List[float]]
    latest_vector: List[float]
    feature_count: int
    sequence_length: int
    summary: Dict[str, float]


class FeatureEngineer:
    """Builds the feature tensor consumed by the transformer-style predictor."""

    def __init__(self, sequence_length: int = 128):
        self.sequence_length = sequence_length

    @staticmethod
    def _f(x, default: float = 0.0) -> float:
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    def build(self, klines: List[Dict], orderflow, liq_analysis, atr_value: float) -> FeatureBatch:
        if not klines:
            return FeatureBatch([], [], 0, 0, {})

        window = klines[-self.sequence_length :]
        closes = [float(item["close"]) for item in window]
        volumes = [float(item.get("volume", 0.0)) for item in window]
        avg_volume = sum(volumes) / len(volumes) if volumes else 1.0
        target_level = self._f(getattr(liq_analysis, "target_level", 0.0))
        liq_bias = self._f(getattr(liq_analysis, "signal", 0))
        liq_density = self._f(getattr(liq_analysis, "target_density", 0.0))
        dtp = self._f(getattr(liq_analysis, "distance_to_target_pct", 0.0))
        liq_distance = dtp / 100 if dtp > 0 else 0.02
        atr_value = self._f(atr_value)

        sequence: List[List[float]] = []
        for idx, candle in enumerate(window):
            close = float(candle["close"])
            open_price = float(candle["open"])
            high = float(candle["high"])
            low = float(candle["low"])
            volume = float(candle.get("volume", 0.0))
            prev_close = closes[idx - 1] if idx > 0 else close
            ret_1 = ((close / prev_close) - 1.0) if prev_close > 0 else 0.0
            ref_5 = closes[idx - 5] if idx >= 5 else closes[0]
            momentum_5 = ((close / ref_5) - 1.0) if ref_5 > 0 else 0.0
            ref_20 = closes[idx - 20] if idx >= 20 else closes[0]
            momentum_20 = ((close / ref_20) - 1.0) if ref_20 > 0 else 0.0
            range_pct = ((high - low) / close) if close > 0 else 0.0
            body_pct = (abs(close - open_price) / close) if close > 0 else 0.0
            volume_rel = (volume / avg_volume) if avg_volume > 0 else 1.0
            atr_pct = (atr_value / close) if close > 0 and atr_value > 0 else 0.0
            dist_to_target = ((target_level - close) / close) if target_level > 0 and close > 0 else 0.0
            sequence.append(
                [
                    ret_1,
                    momentum_5,
                    momentum_20,
                    range_pct,
                    body_pct,
                    volume_rel,
                    atr_pct,
                    self._f(getattr(orderflow, "imbalance_score", 0.0)),
                    self._f(getattr(orderflow, "bullish_ratio", 1.0)) - 1.0,
                    self._f(getattr(orderflow, "bearish_ratio", 1.0)) - 1.0,
                    self._f(getattr(orderflow, "volume_spike", 1.0)) - 1.0,
                    liq_bias,
                    dist_to_target,
                    liq_density / 1_000_000,
                    self._f(getattr(orderflow, "normalized_imbalance", 0.0)),
                ]
            )

        latest_vector = sequence[-1]
        summary = {
            "close": closes[-1],
            "target_level": target_level,
            "liq_distance": liq_distance,
            "liq_density": liq_density,
            "avg_return": sum(item[0] for item in sequence[-16:]) / max(1, len(sequence[-16:])),
            "momentum": sum(item[1] for item in sequence[-8:]) / max(1, len(sequence[-8:])),
            "volatility": sum(item[3] for item in sequence[-14:]) / max(1, len(sequence[-14:])),
        }
        return FeatureBatch(
            sequence=sequence,
            latest_vector=latest_vector,
            feature_count=len(latest_vector),
            sequence_length=len(sequence),
            summary=summary,
        )