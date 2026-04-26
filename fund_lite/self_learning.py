#!/usr/bin/env python3
"""
Самообучение: в проде вызывайте отдельными джобами (cron), а не каждый тик.

Точки интеграции в репозитории:
- ``scripts/feedback_retrain_once.py``
- ``auto_ml.orchestrator.AutoMLSystem.train_cycle`` / ``train_cycle_from_df``
- ``engine.signal_feedback_loop.SignalFeedbackLoop``
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Tuple


@dataclass
class SelfLearning:
    """Заглушка-контракт: реальное дообучение = тяжёлые скрипты, не блок цикла бота."""

    bot_dir: Path

    def load_recent_trades(self, loader: Optional[Callable[[], list]] = None) -> list:
        if loader:
            return loader()
        return []

    def prepare_dataset(self, trades: list) -> Tuple[Any, Any]:
        """Переопределите под свой формат логов."""
        return [], []

    def update(self, trainer: Optional[Callable[[], None]] = None) -> None:
        if trainer:
            trainer()

    def save_models(self, saver: Optional[Callable[[], None]] = None) -> None:
        if saver:
            saver()
