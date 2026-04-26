#!/usr/bin/env python3
"""Single place to turn OHLCV (+ optional regime) into model features."""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


class FeatureStore:
    def __init__(self, rsi_period: int = 14, vol_window: int = 20, momentum_lag: int = 5):
        self.rsi_period = int(rsi_period)
        self.vol_window = int(vol_window)
        self.momentum_lag = int(momentum_lag)

    @staticmethod
    def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
        delta = s.diff()
        up = delta.clip(lower=0).rolling(n).mean()
        down = (-delta.clip(upper=0)).rolling(n).mean()
        rs = up / (down + 1e-9)
        return 100 - (100 / (1 + rs))

    def build(
        self,
        df: pd.DataFrame,
        regime_col: Optional[str] = None,
    ) -> pd.DataFrame:
        out = df.copy()
        if "close" not in out.columns:
            raise ValueError("feature_store.build: need column 'close'")

        out["ret"] = out["close"].pct_change()
        out["volatility"] = out["ret"].rolling(self.vol_window).std()
        out["momentum"] = out["close"] - out["close"].shift(self.momentum_lag)
        out["rsi"] = self._rsi(out["close"], self.rsi_period)
        out["ema_fast"] = out["close"].ewm(span=9, adjust=False).mean()
        out["ema_slow"] = out["close"].ewm(span=21, adjust=False).mean()

        # Time-of-day (liquidity cycles) — requires dt or time ms
        if "dt" in out.columns and out["dt"].notna().any():
            hour = out["dt"].dt.hour.astype(float)
            minute = out["dt"].dt.minute.astype(float)
            out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
            out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
            out["minute_sin"] = np.sin(2 * np.pi * minute / 60.0)
            out["minute_cos"] = np.cos(2 * np.pi * minute / 60.0)
        elif "time" in out.columns:
            ts = pd.to_datetime(out["time"], unit="ms", utc=True)
            hour = ts.dt.hour.astype(float)
            minute = ts.dt.minute.astype(float)
            out["hour_sin"] = np.sin(2 * np.pi * hour / 24.0)
            out["hour_cos"] = np.cos(2 * np.pi * hour / 24.0)
            out["minute_sin"] = np.sin(2 * np.pi * minute / 60.0)
            out["minute_cos"] = np.cos(2 * np.pi * minute / 60.0)

        if regime_col and regime_col in out.columns:
            # one-hot TREND / RANGE / other
            reg = out[regime_col].astype(str).str.upper()
            out["regime_trend"] = (reg == "TREND").astype(float)
            out["regime_range"] = (reg == "RANGE").astype(float)

        return out.dropna().reset_index(drop=True)

    def default_feature_columns(self, df: pd.DataFrame) -> list:
        base = ["ret", "volatility", "momentum", "rsi", "ema_fast", "ema_slow"]
        extras = [c for c in df.columns if c.startswith("hour_") or c.startswith("minute_")]
        regimes = [c for c in df.columns if c.startswith("regime_")]
        return [c for c in base + extras + regimes if c in df.columns]
