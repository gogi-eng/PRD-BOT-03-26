#!/usr/bin/env python3
"""Evolution loop: evaluate → select → reproduce → mutate → sandbox deploy."""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

import pandas as pd

from .allocator import Allocator
from .backtester import Backtester
from .generator import generate_population
from .genome import Genome
from .mutator import mutate
from .sandbox import Sandbox
from .selector import select


class EvolutionEngine:
    def __init__(
        self,
        population_size: int = 20,
        elite_k: int = 5,
        mutation_rate: float = 0.25,
        backtester: Optional[Backtester] = None,
        sandbox: Optional[Sandbox] = None,
        allocator: Optional[Allocator] = None,
        regime_col: Optional[str] = None,
    ):
        self.population_size = max(5, int(population_size))
        self.elite_k = max(2, int(elite_k))
        self.mutation_rate = float(mutation_rate)
        self.bt = backtester or Backtester()
        self.sandbox = sandbox or Sandbox()
        self.alloc = allocator or Allocator()
        self.regime_col = regime_col
        self.population: List[Genome] = generate_population(self.population_size)

    def evolve(self, df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty or "close" not in df.columns:
            return {"ok": False, "error": "empty_or_no_close"}

        for g in self.population:
            if g.retired:
                continue
            self.bt.walk_forward_evaluate(g, df, regime_col=self.regime_col)

        top = select(self.population, k=self.elite_k)
        if not top:
            return {"ok": False, "error": "no_valid_fitness"}

        new_pop: List[Genome] = []
        for i in range(min(self.elite_k, len(top))):
            new_pop.append(top[i].copy())
        while len(new_pop) < self.population_size:
            parent = random.choice(top)
            new_pop.append(mutate(parent, rate=self.mutation_rate))
        self.population = new_pop

        for g in top:
            self.sandbox.deploy(g)

        return {
            "ok": True,
            "elite_fitness": [float(g.fitness) for g in top],
            "elite_ids": [g.id for g in top],
        }

    def update_live(self, genome_id: int, pnl: float) -> None:
        self.sandbox.update(genome_id, pnl)

    def reallocate(self) -> Dict[int, float]:
        scored = []
        for gid in list(self.sandbox.live.keys()):
            if self.sandbox.is_retired(gid):
                continue
            s = self.sandbox.score(gid)
            if s is not None:
                scored.append((gid, s))
        return self.alloc.allocate(scored)

    def retire_genome(self, genome: Genome, reason: str) -> None:
        genome.retired = True
        genome.retire_reason = reason
