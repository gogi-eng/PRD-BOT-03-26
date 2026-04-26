#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


class BreakoutAgent:
    name = "breakout"

    def get_signal(self, df: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
        lb = max(5, int(lookback))
        high = float(df["high"].rolling(lb).max().iloc[-1])
        low = float(df["low"].rolling(lb).min().iloc[-1])
        c = float(df["close"].iloc[-1])
        mid = 0.5 * (high + low)
        if mid <= 0 or high <= low:
            return {"signal": 0.0, "confidence": 0.0, "name": self.name}
        # Positive if close near top of range (bullish breakout bias)
        signal = float(np.tanh(((c - mid) / (high - low + 1e-9)) * 4))
        confidence = float(min(1.0, abs(signal)))
        return {"signal": signal, "confidence": confidence, "name": self.name}
