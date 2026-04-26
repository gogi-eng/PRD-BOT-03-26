#!/usr/bin/env python3
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


class ScalpAgent:
    name = "scalp"

    def get_signal(self, df: pd.DataFrame) -> Dict[str, Any]:
        change = float(df["close"].pct_change().iloc[-1])
        signal = float(np.tanh(change * 50))
        confidence = float(abs(signal))
        return {"signal": signal, "confidence": confidence, "name": self.name}
