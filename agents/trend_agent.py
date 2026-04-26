#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


class TrendAgent:
    name = "trend"

    def get_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        ema_fast = df["close"].ewm(span=9, adjust=False).mean().iloc[-1]
        ema_slow = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
        signal = float(np.tanh((ema_fast - ema_slow) * 10))
        confidence = float(min(1.0, abs(signal)))
        return {"signal": signal, "confidence": confidence, "name": self.name}
