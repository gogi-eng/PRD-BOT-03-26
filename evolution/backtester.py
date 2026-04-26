#!/usr/bin/env python3
"""
Walk-forward backtester with fees/slippage penalties (no future leakage inside each fold).
Long-only toy rule: long when EMA_fast > EMA_slow and RSI not overbought; exit long when RSI oversold boost (simplified).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .genome import Genome


class Backtester:
    def __init__(
        self,
        splits: int = 5,
        fee_roundtrip_pct: float = 0.12,
        slippage_pct: float = 0.02,
        min_fold_bars: int = 40,
    ):
        self.splits = max(3, int(splits))
        self.fee_rt = float(fee_roundtrip_pct) / 100.0
        self.slip = float(slippage_pct) / 100.0
        self.min_fold_bars = int(min_fold_bars)

    @staticmethod
    def _rsi(s: pd.Series, n: int) -> pd.Series:
        d = s.diff()
        up = d.clip(lower=0).rolling(int(n)).mean()
        dn = (-d.clip(upper=0)).rolling(int(n)).mean()
        rs = up / (dn + 1e-9)
        return 100 - 100 / (1 + rs)

    def _signals(self, df: pd.DataFrame, p: dict) -> pd.Series:
        c = df["close"]
        ema_fast = c.ewm(span=int(p["ema_fast"]), adjust=False).mean()
        ema_slow = c.ewm(span=int(p["ema_slow"]), adjust=False).mean()
        rsi = self._rsi(c, int(p["rsi_len"]))
        th = float(p.get("threshold", 0.35))
        rsi_cap = 70.0 - 15.0 * th
        long_cond = (ema_fast > ema_slow) & (rsi < rsi_cap)
        sig = long_cond.astype(int)
        return sig

    @staticmethod
    def _drawdown(returns: pd.Series) -> float:
        if returns.empty:
            return 0.0
        cum = returns.cumsum()
        peak = np.maximum.accumulate(cum.to_numpy())
        dd = peak - cum.to_numpy()
        return float(np.max(dd)) if len(dd) else 0.0

    def _fold_metrics(self, sig: pd.Series, ret: pd.Series) -> Tuple[float, float, float]:
        """PnL sum, max DD, Sharpe-like on bar returns (not annualized)."""
        aligned = pd.concat([sig, ret], axis=1).dropna()
        if aligned.empty:
            return 0.0, 0.0, 0.0
        s = aligned.iloc[:, 0]
        r = aligned.iloc[:, 1]
        pos_change = s.diff().fillna(s).abs()
        costs = (self.fee_rt + self.slip) * pos_change
        strat_ret = s * r - costs
        pnl = float(strat_ret.sum())
        dd = self._drawdown(strat_ret)
        mu = float(r.mean())
        sd = float(r.std() + 1e-12)
        sharpe_like = mu / sd
        return pnl, dd, sharpe_like

    def evaluate(
        self,
        genome: Genome,
        df: pd.DataFrame,
        dd_penalty: float = 2.0,
        sharpe_weight: float = 0.5,
    ) -> float:
        """Single-pass fitness (use for quick screen; prefer walk_forward for selection)."""
        p = genome.params
        sig = self._signals(df, p)
        ret = df["close"].pct_change().shift(-1)
        pnl, dd, sh = self._fold_metrics(sig, ret)
        genome.fitness = pnl - dd_penalty * dd + sharpe_weight * sh
        return float(genome.fitness)

    def walk_forward_evaluate(
        self,
        genome: Genome,
        df: pd.DataFrame,
        regime_col: Optional[str] = None,
        dd_penalty: float = 2.0,
        sharpe_weight: float = 0.5,
    ) -> float:
        """
        Expanding window: train params are fixed (genome); for each fold we score only on the test slice,
        signals computed only from data available in that slice (no lookahead beyond bar t for signal at t).
        """
        work = df
        if regime_col and regime_col in df.columns:
            work = df[df[regime_col] == df[regime_col].iloc[-1]].copy()

        n = len(work)
        if n < self.splits * self.min_fold_bars:
            genome.walk_forward_mean = 0.0
            genome.walk_forward_std = 1.0
            genome.fitness = -1e9
            return genome.fitness

        chunk = max(n // self.splits, self.min_fold_bars)
        fold_scores: List[float] = []

        for i in range(2, self.splits):
            start_test = i * chunk
            end_test = min((i + 1) * chunk, n)
            if end_test - start_test < 10:
                continue
            test_df = work.iloc[start_test:end_test]
            sig = self._signals(test_df, genome.params)
            ret = test_df["close"].pct_change().shift(-1)
            pnl, dd, sh = self._fold_metrics(sig, ret)
            fold_scores.append(pnl - dd_penalty * dd + sharpe_weight * sh)

        if not fold_scores:
            genome.walk_forward_mean = 0.0
            genome.walk_forward_std = 1.0
            genome.fitness = -1e9
            return genome.fitness

        genome.walk_forward_mean = float(np.mean(fold_scores))
        genome.walk_forward_std = float(np.std(fold_scores) + 1e-9)
        # Selection objective: mean fold score penalized by instability
        genome.fitness = genome.walk_forward_mean - 0.25 * genome.walk_forward_std
        return float(genome.fitness)
