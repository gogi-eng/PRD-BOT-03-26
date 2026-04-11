#!/usr/bin/env python3
"""Multi-symbol capital allocation using softmax scoring."""
from __future__ import annotations

from math import exp, log
from typing import Dict, List


class MultiSymbolCapitalAllocator:
    """Allocates capital between candidate symbols from their composite quality score."""

    def allocate(self, candidates: List[Dict], selected_count: int | None = None) -> List[Dict]:
        if not candidates:
            return []

        raw_scores = []
        max_liquidity = max(candidate.get("liquidity", 0.0) for candidate in candidates) or 1.0
        max_volatility = max(candidate.get("volatility", 0.0) for candidate in candidates) or 1.0

        for candidate in candidates:
            signal_strength = candidate.get("signal_strength", 0.0)
            liquidity = candidate.get("liquidity", 0.0)
            volatility = candidate.get("volatility", 0.0)
            spread = candidate.get("spread", 0.0)

            liquidity_score = liquidity / max_liquidity
            volatility_penalty = volatility / max_volatility
            score = (
                signal_strength * 2.6
                + liquidity_score * 0.7
                - volatility_penalty * 0.5
                - spread * 80
                + log(max(liquidity, 1.0)) * 0.02
            )
            raw_scores.append(score)

        weights = self._softmax(raw_scores)
        ranked: List[Dict] = []
        for candidate, weight, score in zip(candidates, weights, raw_scores):
            item = dict(candidate)
            item["allocator_score"] = round(score, 4)
            item["capital_weight"] = round(weight, 4)
            ranked.append(item)
        ranked.sort(key=lambda item: item["capital_weight"], reverse=True)

        # IMPORTANT:
        # We open only top-N symbols per cycle (available slots). If weights are
        # computed on the full candidate universe and used directly for top-N,
        # each selected symbol gets under-allocated. Re-normalize weights over
        # selected symbols only so active orders consume intended capital.
        if selected_count is not None and selected_count > 0:
            selected = ranked[:selected_count]
            selected_sum = sum(float(item.get("capital_weight", 0.0) or 0.0) for item in selected)
            if selected_sum > 0:
                for item in selected:
                    normalized = float(item.get("capital_weight", 0.0) or 0.0) / selected_sum
                    item["capital_weight"] = round(normalized, 4)

        return ranked

    @staticmethod
    def _softmax(values: List[float]) -> List[float]:
        if not values:
            return []
        peak = max(values)
        exps = [exp(value - peak) for value in values]
        total = sum(exps) or 1.0
        return [value / total for value in exps]