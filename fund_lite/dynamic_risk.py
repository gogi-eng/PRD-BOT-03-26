#!/usr/bin/env python3
"""Доля капитала/риска от волатильности и уверенности (учебный шаблон)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class DynamicRisk:
    base: float = 0.01
    max_size: float = 0.05
    vol_floor: float = 1e-6

    def calculate(self, features: Mapping[str, Any]) -> float:
        vol = float(features.get("volatility", 0.01) or 0.01)
        conf = float(features.get("ai_confidence", 0.5) or 0.5)
        vol = max(vol, self.vol_floor)
        raw = self.base * (conf / vol)
        return float(min(max(raw, 0.0), self.max_size))
