#!/usr/bin/env python3
"""Ограничение суммарной экспозиции (анти-слив слоя)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class PortfolioRisk:
    max_risk: float = 0.1
    _exposure_fn: Optional[Callable[[], float]] = field(default=None, repr=False)

    def set_exposure_fn(self, fn: Callable[[], float]) -> None:
        """Например: lambda: sum(abs(p.qty*p.entry) for p in positions) / balance"""
        self._exposure_fn = fn

    def get_total_exposure(self) -> float:
        if self._exposure_fn is None:
            return 0.0
        return float(self._exposure_fn())

    def check(self, size: float) -> bool:
        current = self.get_total_exposure()
        return (current + float(size)) < float(self.max_risk)
