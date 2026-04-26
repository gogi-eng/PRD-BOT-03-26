from __future__ import annotations

import numpy as np

from rl_env import MetaTradingEnv
from rl_env.meta_trading_env import MetaTradingEnvConfig


def _klines(n: int = 300):
    rng = np.random.default_rng(0)
    c = 100 + np.cumsum(rng.normal(0, 0.1, n))
    return [
        {
            "timestamp": i * 60_000,
            "open": float(c[i]),
            "high": float(c[i] + 0.05),
            "low": float(c[i] - 0.05),
            "close": float(c[i]),
            "volume": float(rng.random() * 10 + 1),
        }
        for i in range(n)
    ]


def test_meta_env_reset_step():
    env = MetaTradingEnv(_klines(400), MetaTradingEnvConfig(max_episode_steps=32))
    obs, info = env.reset(seed=1)
    assert obs.shape[0] == env.obs_dim
    obs2, r, term, trunc, inf = env.step(2)
    assert obs2.shape == obs.shape
    assert isinstance(r, float)
