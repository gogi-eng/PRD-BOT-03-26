#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


class MeanRevAgent:
    name = "meanrev"

    def get_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        mean = float(df["close"].rolling(20).mean().iloc[-1])
        price = float(df["close"].iloc[-1])
        if mean <= 0:
            return {"signal": 0.0, "confidence": 0.0, "name": self.name}
        diff = (price - mean) / mean
        signal = float(-np.tanh(diff * 10))
        confidence = float(min(1.0, abs(signal)))
        return {"signal": signal, "confidence": confidence, "name": self.name}
