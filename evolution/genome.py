#!/usr/bin/env python3
"""Strategy genome: parameter bundle + metadata for evolution."""
from __future__ import annotations

import copy
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _default_params() -> Dict[str, Any]:
    ema_slow = random.randint(20, 60)
    ema_fast = random.randint(5, min(20, ema_slow - 2))
    ema_fast = max(2, ema_fast)
    return {
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "rsi_len": random.randint(7, 21),
        "threshold": random.uniform(0.1, 0.5),
    }


@dataclass
class Genome:
    params: Dict[str, Any] = field(default_factory=_default_params)
    fitness: Optional[float] = None
    walk_forward_mean: Optional[float] = None
    walk_forward_std: Optional[float] = None
    id: int = field(default_factory=lambda: random.randint(1, 10**9))
    generation: int = 0
    retired: bool = False
    retire_reason: str = ""
    created_ts: float = field(default_factory=lambda: time.time())

    def copy(self) -> "Genome":
        g = Genome(
            params=copy.deepcopy(self.params),
            fitness=self.fitness,
            walk_forward_mean=self.walk_forward_mean,
            walk_forward_std=self.walk_forward_std,
            id=random.randint(1, 10**9),
            generation=self.generation,
            retired=False,
            retire_reason="",
            created_ts=time.time(),
        )
        return g

    def clamp_params(self) -> None:
        """Keep params in sane bounds after mutation."""
        self.params["ema_fast"] = int(max(2, min(80, int(self.params["ema_fast"]))))
        self.params["ema_slow"] = int(max(self.params["ema_fast"] + 1, min(120, int(self.params["ema_slow"]))))
        self.params["rsi_len"] = int(max(3, min(50, int(self.params["rsi_len"]))))
        t = float(self.params["threshold"])
        self.params["threshold"] = max(0.01, min(0.99, t))
