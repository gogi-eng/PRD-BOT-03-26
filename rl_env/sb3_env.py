#!/usr/bin/env python3
"""
Официальная обёртка ``gymnasium.Env`` для Stable-Baselines3 поверх ``MetaTradingEnv``.

SB3 проверяет наследование от ``gymnasium.Env``; ядро логики остаётся в ``MetaTradingEnv``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError as e:  # pragma: no cover
    raise ImportError("SB3MetaTradingEnv requires gymnasium: pip install gymnasium") from e

from analysis.feature_engineering import FeatureEngineer
from rl_env.meta_trading_env import MetaTradingEnv, MetaTradingEnvConfig

Observation = np.ndarray


class SB3MetaTradingEnv(gym.Env):
    """VecEnv / PPO / DQN — совместимо со Stable-Baselines3 >= 2.3."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        klines: List[Dict[str, Any]],
        config: Optional[MetaTradingEnvConfig] = None,
        feature_engineer: Optional[FeatureEngineer] = None,
    ):
        super().__init__()
        self._core = MetaTradingEnv(klines, config, feature_engineer)
        self.action_space = spaces.Discrete(4)
        self.observation_space = spaces.Box(
            low=-np.inf,
            high=np.inf,
            shape=(self._core.obs_dim,),
            dtype=np.float32,
        )

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Observation, Dict[str, Any]]:
        super().reset(seed=seed)
        return self._core.reset(seed=seed, options=options)

    def step(self, action: int) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        return self._core.step(int(action))

    def render(self) -> None:
        return None
