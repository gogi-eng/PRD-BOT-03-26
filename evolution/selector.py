#!/usr/bin/env python3
from __future__ import annotations

import random
from typing import List

from .genome import Genome


def select(population: List[Genome], k: int = 5) -> List[Genome]:
    ranked = sorted(
        [g for g in population if not g.retired and g.fitness is not None],
        key=lambda g: float(g.fitness),
        reverse=True,
    )
    return ranked[: max(1, int(k))]


def reproduce(top_k: List[Genome], size: int = 20) -> List[Genome]:
    if not top_k:
        return []
    out: List[Genome] = []
    while len(out) < size:
        parent = random.choice(top_k)
        out.append(parent.copy())
    return out
