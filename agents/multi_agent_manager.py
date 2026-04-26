#!/usr/bin/env python3
"""Aggregate agent dict outputs; optional regime-based subset and weight updates."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from .breakout_agent import BreakoutAgent
from .meanrev_agent import MeanRevAgent
from .scalp_agent import ScalpAgent
from .trend_agent import TrendAgent


class MultiAgentManager:
    REGIME_AGENTS = {
        "TREND": ("trend", "breakout"),
        "RANGE": ("meanrev", "scalp"),
        "CHOP": ("scalp", "meanrev"),
        "NONE": ("trend", "scalp", "meanrev", "breakout"),
    }

    def __init__(
        self,
        agents: Optional[Sequence[Any]] = None,
        min_weight: float = 0.1,
    ):
        self.agents: List[Any] = (
            list(agents)
            if agents is not None
            else [TrendAgent(), ScalpAgent(), MeanRevAgent(), BreakoutAgent()]
        )
        self.min_weight = float(min_weight)
        self.weights: Dict[str, float] = {}
        for a in self.agents:
            key = getattr(a, "name", a.__class__.__name__.replace("Agent", "").lower())
            self.weights[key] = 1.0

    def agents_for_regime(self, regime: Optional[str]) -> List[Any]:
        if not regime:
            return list(self.agents)
        r = str(regime).upper()
        allowed = self.REGIME_AGENTS.get(r, self.REGIME_AGENTS["NONE"])
        out = []
        for a in self.agents:
            key = getattr(a, "name", "")
            if key in allowed:
                out.append(a)
        return out or list(self.agents)

    def get_signals(self, df: pd.DataFrame, regime: Optional[str] = None) -> List[Dict[str, Any]]:
        outputs = []
        for agent in self.agents_for_regime(regime):
            out = agent.get_signal(df)
            outputs.append(out)
        return outputs

    def aggregate(self, outputs: List[Dict[str, Any]]) -> float:
        total = 0.0
        weight_sum = 0.0
        for o in outputs:
            name = str(o.get("name", ""))
            w = float(self.weights.get(name, 1.0))
            if w < self.min_weight:
                continue
            sig = float(o.get("signal", 0.0))
            conf = float(o.get("confidence", 0.0))
            total += sig * conf * w
            weight_sum += w
        if weight_sum <= 0:
            return 0.0
        return float(total / weight_sum)

    def update_weights(self, performance: Dict[str, float]) -> None:
        """performance = agent_name -> pnl contribution (can be negative)."""
        total = sum(abs(v) for v in performance.values()) + 1e-8
        for k in list(self.weights.keys()):
            self.weights[k] = abs(performance.get(k, 0.0)) / total
        # floor then renorm
        for k in list(self.weights.keys()):
            if self.weights[k] < self.min_weight:
                self.weights[k] = 0.0
        s = sum(self.weights.values()) + 1e-8
        for k in self.weights:
            self.weights[k] /= s

    def allocation(self) -> Dict[str, float]:
        return dict(self.weights)
