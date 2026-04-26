#!/usr/bin/env python3
"""XGBoost classifier + optional probability calibration + volatility-scaled labels."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import joblib
except ImportError:  # pragma: no cover
    joblib = None  # type: ignore

try:
    import xgboost as xgb
    from sklearn.calibration import CalibratedClassifierCV
except ImportError:  # pragma: no cover
    xgb = None  # type: ignore
    CalibratedClassifierCV = None  # type: ignore


class Trainer:
    def __init__(
        self,
        n_estimators: int = 200,
        max_depth: int = 5,
        learning_rate: float = 0.05,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        n_jobs: int = 4,
        label_vol_mult: float = 0.5,
        probability_quantile_thr: float = 0.6,
        calibrate: bool = True,
        calibration_cv: int = 3,
    ):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.n_jobs = n_jobs
        self.label_vol_mult = float(label_vol_mult)
        self.probability_quantile_thr = float(probability_quantile_thr)
        self.calibrate = bool(calibrate)
        self.calibration_cv = max(2, int(calibration_cv))

    def _ensure_deps(self) -> None:
        if xgb is None or joblib is None:
            raise ImportError(
                "Trainer requires xgboost and joblib. Install: pip install xgboost joblib scikit-learn"
            )
        if self.calibrate and CalibratedClassifierCV is None:
            raise ImportError("Calibration requires scikit-learn: pip install scikit-learn")

    def build_labels(self, df: pd.DataFrame) -> pd.Series:
        """y = 1 if next return > k * volatility (noise-aware)."""
        ret_next = df["close"].pct_change().shift(-1)
        vol = df["volatility"].replace(0, np.nan)
        thr = self.label_vol_mult * vol
        y = (ret_next > thr.fillna(0)).astype(int)
        return y

    def train(
        self,
        df: pd.DataFrame,
        feature_columns: Optional[List[str]] = None,
    ) -> Tuple[Any, List[str], float, pd.Series]:
        self._ensure_deps()
        if feature_columns is None:
            fs_cols = [c for c in ["ret", "volatility", "momentum", "rsi", "ema_fast", "ema_slow"] if c in df.columns]
            for c in df.columns:
                if c.startswith("hour_") or c.startswith("minute_") or c.startswith("regime_"):
                    if c not in fs_cols:
                        fs_cols.append(c)
            features = fs_cols
        else:
            features = list(feature_columns)

        X = df[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        y = self.build_labels(df)
        mask = y.notna() & X.notna().all(axis=1)
        X = X.loc[mask]
        y = y.loc[mask].astype(int)
        if len(X) < 50:
            raise ValueError(f"Trainer.train: too few rows after dropna ({len(X)}), need >= 50")

        base = xgb.XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            eval_metric="logloss",
            n_jobs=self.n_jobs,
            random_state=42,
        )
        if self.calibrate:
            model = CalibratedClassifierCV(base, method="sigmoid", cv=self.calibration_cv)
            model.fit(X.values, y.values)
        else:
            model = base
            model.fit(X.values, y.values)

        if hasattr(model, "predict_proba"):
            probs = model.predict_proba(X.values)[:, 1]
        else:  # pragma: no cover
            probs = model.predict(X.values)

        thr = float(np.quantile(probs, self.probability_quantile_thr))
        return model, features, thr, y

    def save(
        self,
        model: Any,
        features: List[str],
        thr: float,
        path: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._ensure_deps()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "model": model,
            "features": features,
            "thr": thr,
            "meta": extra or {},
        }
        joblib.dump(payload, path)
        sidecar = path + ".meta.json"
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"features": features, "thr": thr, "meta": extra or {}}, f, indent=2)

    @staticmethod
    def load(path: str) -> Dict[str, Any]:
        if joblib is None:  # pragma: no cover
            raise ImportError("joblib required: pip install joblib")
        return joblib.load(path)
