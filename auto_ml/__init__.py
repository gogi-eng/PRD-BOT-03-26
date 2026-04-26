"""AutoML loop: data → features → train → validate → registry → drift (scaffold)."""
from __future__ import annotations

from auto_ml.data_collector import DataCollector, klines_to_dataframe
from auto_ml.drift_detector import DriftDetector
from auto_ml.feature_store import FeatureStore
from auto_ml.model_registry import ModelRegistry
from auto_ml.orchestrator import AutoMLSystem
from auto_ml.trainer import Trainer
from auto_ml.validator import Validator

__all__ = [
    "AutoMLSystem",
    "DataCollector",
    "DriftDetector",
    "FeatureStore",
    "klines_to_dataframe",
    "ModelRegistry",
    "Trainer",
    "Validator",
]
