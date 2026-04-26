#!/usr/bin/env python3
"""Walk-forward validation (no peeking at future train chunks in-model — split first)."""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from auto_ml.trainer import Trainer


class Validator:
    def __init__(self, splits: int = 5):
        self.splits = max(3, int(splits))

    def walk_forward(
        self,
        df: pd.DataFrame,
        trainer: Trainer,
        feature_columns: Optional[List[str]] = None,
    ) -> Tuple[float, float, list]:
        """
        Expanding-window style: for each test slice, train only on data before that slice.
        Returns mean PnL proxy, std, and per-split scores.
        """
        n = len(df)
        if n < self.splits * 30:
            return 0.0, 1.0, []

        chunk = max(n // self.splits, 20)
        scores = []
        fold_details = []

        for i in range(2, self.splits):
            end_train = i * chunk
            start_test = end_train
            end_test = min((i + 1) * chunk, n)
            if end_test <= start_test + 5:
                continue
            train_df = df.iloc[:end_train].copy()
            test_df = df.iloc[start_test:end_test].copy()
            if len(train_df) < 80 or len(test_df) < 10:
                continue

            model, feats, thr, _ = trainer.train(train_df, feature_columns=feature_columns)
            X = test_df[feats].replace([np.inf, -np.inf], np.nan).fillna(0.0)
            if hasattr(model, "predict_proba"):
                proba = model.predict_proba(X.values)[:, 1]
            else:  # pragma: no cover
                proba = model.predict(X.values).astype(float)

            signals = (proba > thr).astype(int)
            ret = test_df["close"].pct_change().shift(-1).fillna(0.0)
            pnl = float((signals * ret).sum())
            scores.append(pnl)
            fold_details.append({"fold": i, "pnl": pnl, "n_test": len(test_df)})

        if not scores:
            return 0.0, 1.0, []
        return float(np.mean(scores)), float(np.std(scores) + 1e-9), fold_details
