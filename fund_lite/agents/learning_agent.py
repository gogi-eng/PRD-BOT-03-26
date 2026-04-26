#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..self_learning import SelfLearning


@dataclass
class LearningAgent:
    learner: SelfLearning

    def tick(self) -> None:
        self.learner.update()
