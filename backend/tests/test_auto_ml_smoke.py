"""Smoke tests for auto_ml / agents (no xgboost required)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from auto_ml.data_collector import klines_to_dataframe
from auto_ml.drift_detector import DriftDetector
from auto_ml.feature_store import FeatureStore
from auto_ml.model_registry import ModelRegistry
import tempfile
from pathlib import Path


def test_klines_to_dataframe():
    kl = [{"timestamp": 1000 * i, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0 + i * 0.01, "volume": 10.0} for i in range(30)]
    df = klines_to_dataframe(kl)
    assert len(df) == 30
    assert "close" in df.columns


def test_feature_store_time_cycles():
    rows = []
    t0 = 1700000000000
    for i in range(80):
        rows.append(
            {
                "timestamp": t0 + i * 60_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + 0.05 * np.sin(i / 5.0),
                "volume": 1.0,
            }
        )
    df = klines_to_dataframe(rows)
    fs = FeatureStore()
    out = fs.build(df)
    assert "hour_sin" in out.columns
    assert len(out) > 40


def test_registry_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        p = str(Path(td) / "registry.json")
        reg = ModelRegistry(p)
        reg.register("models/a.pkl", {"pnl_mean": 0.02, "pnl_std": 0.01})
        reg.register("models/b.pkl", {"pnl_mean": 0.03, "pnl_std": 0.005})
        best = reg.best()
        assert best is not None
        assert "b.pkl" in best["model_path"]


def test_drift_detector():
    d = DriftDetector(recent_n=5, past_n=5)
    for i in range(60):
        d.update(1.0 if i < 40 else -1.0)
    assert d.is_drift() is True


def test_multi_agent_signals():
    from agents import MultiAgentManager

    df = pd.DataFrame({"close": np.linspace(100, 110, 50), "high": np.linspace(101, 111, 50), "low": np.linspace(99, 109, 50)})
    m = MultiAgentManager()
    outs = m.get_signals(df, regime="TREND")
    assert len(outs) >= 1
    agg = m.aggregate(outs)
    assert -1.0 <= agg <= 1.0


def test_trainer_small_fit():
    pytest.importorskip("xgboost")
    pytest.importorskip("sklearn")
    from auto_ml.trainer import Trainer

    n = 200
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            "close": 100 + np.cumsum(rng.normal(0, 0.1, n)),
            "open": 100 + rng.normal(0, 0.05, n),
            "high": 101 + rng.normal(0, 0.05, n),
            "low": 99 + rng.normal(0, 0.05, n),
            "volume": rng.random(n) * 10,
        }
    )
    fs = FeatureStore()
    dff = fs.build(df)
    t = Trainer(n_estimators=30, calibration_cv=2, calibrate=True)
    model, feats, thr, y = t.train(dff)
    assert len(feats) > 0
    assert 0.0 <= thr <= 1.0
