#!/usr/bin/env python3
"""Simple rolling PnL drift flag (production would add PSI / KS on features)."""
from __future__ import annotations

from collections import deque
from typing import Deque, Optional


class DriftDetector:
    def __init__(
        self,
        window: int = 100,
        recent_n: int = 20,
        past_n: int = 20,
        degrade_ratio: float = 0.5,
    ):
        self.window = max(10, int(window))
        self.recent_n = max(5, int(recent_n))
        self.past_n = max(5, int(past_n))
        self.degrade_ratio = float(degrade_ratio)
        self.hist: Deque[float] = deque(maxlen=self.window)

    def update(self, pnl: float) -> None:
        self.hist.append(float(pnl))

    def is_drift(self) -> bool:
        if len(self.hist) < max(50, self.recent_n + self.past_n):
            return False
        arr = list(self.hist)
        recent = sum(arr[-self.recent_n :]) / self.recent_n
        past = sum(arr[: self.past_n]) / self.past_n
        return recent < past * self.degrade_ratio

    def reset(self) -> None:
        self.hist.clear()
