#!/usr/bin/env python3
"""Wires collector → features → train → validate → registry → drift."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from auto_ml.data_collector import DataCollector
from auto_ml.drift_detector import DriftDetector
from auto_ml.feature_store import FeatureStore
from auto_ml.model_registry import ModelRegistry
from auto_ml.trainer import Trainer
from auto_ml.validator import Validator


class AutoMLSystem:
    def __init__(
        self,
        exchange: Any = None,
        symbol: str = "BTCUSDT",
        timeframe: str = "5",
        models_dir: str = "models",
        registry_path: str = "models/registry.json",
        trainer: Optional[Trainer] = None,
        feature_store: Optional[FeatureStore] = None,
        regime_column: Optional[str] = None,
    ):
        self.collector = DataCollector(exchange)
        self.fs = feature_store or FeatureStore()
        self.trainer = trainer or Trainer()
        self.validator = Validator()
        self.registry = ModelRegistry(registry_path)
        self.drift = DriftDetector()

        self.symbol = symbol
        self.timeframe = timeframe
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.regime_column = regime_column

        self.model_bundle: Optional[Dict[str, Any]] = None

    def train_cycle_from_df(self, df: pd.DataFrame, feature_columns: Optional[list] = None) -> Dict[str, Any]:
        """Offline / notebook: train on ready OHLCV DataFrame."""
        dff = self.fs.build(df, regime_col=self.regime_column)
        if feature_columns is None:
            feature_columns = self.fs.default_feature_columns(dff)

        model, feats, thr, _ = self.trainer.train(dff, feature_columns=feature_columns)
        pnl_mean, pnl_std, folds = self.validator.walk_forward(dff, self.trainer, feature_columns=feats)

        path = str(self.models_dir / f"model_{int(time.time())}.pkl")
        metrics = {
            "pnl_mean": pnl_mean,
            "pnl_std": pnl_std,
            "sharpe_like": pnl_mean / max(pnl_std, 1e-9),
            "folds": folds,
        }
        self.trainer.save(
            model,
            feats,
            thr,
            path,
            extra={"symbol": self.symbol, "timeframe": self.timeframe, "n_rows": len(dff)},
        )
        self.registry.register(path, metrics, extra={"features": feats})

        best = self.registry.best()
        if best:
            self.model_bundle = best
        return {"model_path": path, "metrics": metrics, "features": feats, "thr": thr}

    async def train_cycle(self) -> Dict[str, Any]:
        """Fetch from Bybit via async client, then same as offline."""
        df = await self.collector.fetch_ohlcv(self.symbol, self.timeframe, limit=1500)
        if df.empty or len(df) < 120:
            raise RuntimeError(f"AutoMLSystem.train_cycle: insufficient data for {self.symbol}")
        return self.train_cycle_from_df(df)

    def should_retrain(self) -> bool:
        return self.drift.is_drift()

    def update_live(self, pnl: float) -> None:
        self.drift.update(pnl)

    def load_active(self) -> Optional[Dict[str, Any]]:
        return self.model_bundle

    def set_active_from_registry_best(self) -> Optional[Dict[str, Any]]:
        self.model_bundle = self.registry.best()
        return self.model_bundle
