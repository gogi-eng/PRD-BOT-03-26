"""Smoke tests for evolution package."""
from __future__ import annotations

import numpy as np
import pandas as pd

from evolution import Allocator, Backtester, EvolutionEngine, Genome, Sandbox, mutate


def _df(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    c = 100 + np.cumsum(rng.normal(0, 0.15, n))
    return pd.DataFrame(
        {
            "close": c,
            "open": c - rng.normal(0, 0.02, n),
            "high": c + abs(rng.normal(0, 0.05, n)),
            "low": c - abs(rng.normal(0, 0.05, n)),
        }
    )


def test_mutate_clamps():
    g = Genome()
    g.params["ema_fast"] = 100
    g.clamp_params()
    assert g.params["ema_fast"] <= 80
    child = mutate(g, rate=1.0)
    assert child.params["ema_slow"] > child.params["ema_fast"]


def test_walk_forward_finite():
    bt = Backtester(splits=5, min_fold_bars=30)
    g = Genome()
    fit = bt.walk_forward_evaluate(g, _df(500))
    assert fit > -1e8
    assert g.walk_forward_mean is not None


def test_evolution_engine_step():
    evo = EvolutionEngine(population_size=12, elite_k=3)
    r = evo.evolve(_df(600))
    assert r.get("ok") is True
    assert len(evo.population) == 12


def test_sandbox_kill_streak():
    sb = Sandbox(min_trades_for_score=3, max_consecutive_losses=3, max_sandbox_dd=0.99)
    sb.deploy(Genome())
    gid = list(sb.live.keys())[0]
    for _ in range(4):
        sb.update(gid, -1.0)
    assert sb.is_retired(gid) is True


def test_allocator_cap():
    a = Allocator(max_live=3, max_weight_per_genome=0.3)
    w = a.allocate([(1, 10.0), (2, 8.0), (3, 6.0), (4, 1.0)])
    assert len(w) <= 3
    assert all(v <= 0.3001 for v in w.values())
