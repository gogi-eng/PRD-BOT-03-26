"""Short SB3 training smoke test (optional deps)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_sb3_ppo_short_run():
    pytest.importorskip("stable_baselines3")
    pytest.importorskip("gymnasium")
    from stable_baselines3 import PPO
    from stable_baselines3.common.vec_env import DummyVecEnv

    from rl_env.klines_loader import synthetic_klines
    from rl_env.meta_trading_env import MetaTradingEnvConfig
    from rl_env.sb3_env import SB3MetaTradingEnv

    klines = synthetic_klines(600)
    mcfg = MetaTradingEnvConfig(sequence_length=64, max_episode_steps=64)
    vec = DummyVecEnv([lambda: SB3MetaTradingEnv(klines, mcfg)])
    model = PPO("MlpPolicy", vec, n_steps=128, batch_size=32, verbose=0)
    model.learn(total_timesteps=256)
    assert model.policy is not None
