#!/usr/bin/env python3
from __future__ import annotations

import random

from .genome import Genome


def mutate(genome: Genome, rate: float = 0.2) -> Genome:
    g = genome.copy()
    g.generation = genome.generation + 1
    for k in g.params:
        if random.random() >= rate:
            continue
        v = g.params[k]
        if isinstance(v, bool):
            g.params[k] = not v
        elif isinstance(v, int):
            g.params[k] = int(v) + random.randint(-3, 3)
        else:
            g.params[k] = float(v) + random.uniform(-0.05, 0.05)
    g.clamp_params()
    g.fitness = None
    g.walk_forward_mean = None
    g.walk_forward_std = None
    return g
