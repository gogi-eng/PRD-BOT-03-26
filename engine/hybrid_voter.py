"""
Hybrid Voting: агрегация сигналов XGB(слот) / Gemma(слот) / TA в [-1,1].
Источник: +Gemma.txt
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _as_cfg_dict(cfg: Any) -> dict:
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return cfg
    raw = getattr(cfg, "raw", None)
    if isinstance(raw, dict):
        return raw
    return {}


@dataclass
class HybridVoter:
    weights: Dict[str, float] = field(
        default_factory=lambda: {"xgb": 0.4, "gemma": 0.3, "ta": 0.3}
    )
    performance: Dict[str, List[float]] = field(
        default_factory=lambda: {"xgb": [], "gemma": [], "ta": []}
    )
    threshold: float = 0.2
    dynamic_threshold_vol_scale: float = 0.0
    # последняя волатильность ряда (σ доходностей) для сдвига порога
    _last_vol: float = 0.0

    @classmethod
    def from_config(cls, cfg: Any) -> "HybridVoter":
        d = _as_cfg_dict(cfg).get("hybrid_voter") or {}
        w = d.get("weights") or {"xgb": 0.4, "gemma": 0.3, "ta": 0.3}
        return cls(
            weights={str(k): float(v) for k, v in w.items()},
            threshold=float(d.get("threshold", 0.2)),
            dynamic_threshold_vol_scale=float(d.get("dynamic_threshold_vol_scale", 0.0)),
        )

    @staticmethod
    def normalize(x: float) -> float:
        return max(-1.0, min(1.0, float(x)))

    def set_benchmark_volatility(self, vol: float) -> None:
        """σ доходностей (например бенчмарк) — при dynamic_threshold_vol_scale>0 поднимаем threshold."""
        self._last_vol = max(0.0, float(vol))

    def effective_threshold(self) -> float:
        t = self.threshold
        t += self.dynamic_threshold_vol_scale * self._last_vol
        return min(0.95, max(0.02, t))

    def vote(self, signals: Dict[str, float]) -> float:
        total, wsum = 0.0, 0.0
        for k, v in signals.items():
            w = float(self.weights.get(k, 0.0) or 0.0)
            if w <= 0:
                continue
            total += self.normalize(v) * w
            wsum += w
        if wsum <= 0:
            return 0.0
        return total / wsum

    def decide(self, score: float) -> str:
        th = self.effective_threshold()
        if score > th:
            return "LONG"
        if score < -th:
            return "SHORT"
        return "NO_TRADE"

    def update_performance(self, results: Dict[str, float]) -> None:
        for k, v in results.items():
            if k in self.performance:
                self.performance[k].append(v)
        self._recalculate_weights()

    def _recalculate_weights(self) -> None:
        scores: Dict[str, float] = {}
        for k, hist in self.performance.items():
            if not hist:
                scores[k] = 0.0
            else:
                scores[k] = float(sum(hist) / max(len(hist), 1))
        tot = sum(abs(v) for v in scores.values()) + 1e-8
        for k in list(self.weights.keys()):
            if k in scores:
                self.weights[k] = max(0.01, min(0.9, abs(scores[k]) / tot))
