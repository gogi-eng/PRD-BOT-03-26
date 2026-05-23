"""
Индикаторы для теханализа (pandas, без внешних TA-библиотек).
"""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd


def klines_to_df(klines: list[dict[str, Any]]) -> pd.DataFrame:
    if not klines:
        return pd.DataFrame()
    df = pd.DataFrame(klines)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    return df


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> float:
    if len(close) < period + 2:
        return 50.0
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain.iloc[-1] / (loss.iloc[-1] + 1e-12)
    return float(100 - (100 / (1 + rs)))


def atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 2:
        return 0.0
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def intraday_range_pct(df: pd.DataFrame, bars: int = 12) -> float:
    """Диапазон high-low за последние N свечей, % от цены."""
    if len(df) < bars:
        bars = len(df)
    if bars < 2:
        return 0.0
    tail = df.tail(bars)
    lo, hi = float(tail["low"].min()), float(tail["high"].max())
    mid = float(tail["close"].iloc[-1]) or 1.0
    return (hi - lo) / mid * 100.0
