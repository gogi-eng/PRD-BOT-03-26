#!/usr/bin/env python3
"""Capital weights with per-genome cap and top-K cutoff."""
from __future__ import annotations

from typing import Dict, List, Tuple


class Allocator:
    def __init__(self, max_live: int = 5, max_weight_per_genome: float = 0.30):
        self.max_live = max(1, int(max_live))
        self.max_w = float(max_weight_per_genome)
        if self.max_w > 1.0:
            self.max_w = 1.0
        self.active: Dict[int, float] = {}

    def allocate(self, scored: List[Tuple[int, float]]) -> Dict[int, float]:
        if not scored:
            self.active = {}
            return {}
        ranked = sorted(scored, key=lambda x: x[1], reverse=True)[: self.max_live]
        total = sum(max(0.0, float(s)) for _, s in ranked) + 1e-9
        raw = {int(gid): max(0.0, float(s)) / total for gid, s in ranked}
        # Hard per-genome cap; remainder stays unallocated (does not force renormalization past cap).
        self.active = {g: min(w, self.max_w) for g, w in raw.items()}
        return dict(self.active)

    def weight(self, genome_id: int) -> float:
        return float(self.active.get(int(genome_id), 0.0))
