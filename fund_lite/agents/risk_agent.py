#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass

from ..dynamic_risk import DynamicRisk
from ..portfolio_risk import PortfolioRisk


@dataclass
class RiskAgent:
    dynamic: DynamicRisk
    portfolio: PortfolioRisk

    def size_and_gate(self, features: dict) -> tuple[float, bool]:
        sz = self.dynamic.calculate(features)
        ok = self.portfolio.check(sz)
        return sz, ok
