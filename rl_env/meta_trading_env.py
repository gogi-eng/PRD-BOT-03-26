#!/usr/bin/env python3
"""
Meta-trading environment: observations = ``FeatureEngineer.latest_vector`` + лёгкий meta-хвост.

Совместимость: при установленном ``gymnasium`` наследуется от ``gymnasium.Env``;
иначе те же методы ``reset`` / ``step`` без наследования (достаточно для кастомного тренера).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from analysis.feature_engineering import FeatureEngineer
from analysis.liquidation_clusters import LiquidationAnalysis
from analysis.orderflow_analyzer import OrderflowSnapshot
from engine.rl_meta_controller import ACTION_RISK_MULT
from utils import ATRCalculator

try:
    import gymnasium as gym
    from gymnasium import spaces

    _GYM = True
except Exception:  # pragma: no cover
    gym = None  # type: ignore
    spaces = None  # type: ignore
    _GYM = False


Observation = np.ndarray
StepResult = Tuple[Observation, float, bool, bool, Dict[str, Any]]


@dataclass
class MetaTradingEnvConfig:
    sequence_length: int = 128
    fee_per_turnover: float = 0.0006
    max_episode_steps: int = 512
    drawdown_penalty_coef: float = 0.5


class _MetaTradingEnvBase:
    """Общая логика (с/без gymnasium)."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        klines: List[Dict[str, Any]],
        config: Optional[MetaTradingEnvConfig] = None,
        feature_engineer: Optional[FeatureEngineer] = None,
    ):
        if not klines or len(klines) < 50:
            raise ValueError("MetaTradingEnv: need klines with sufficient length")
        self.klines = klines
        self.cfg = config or MetaTradingEnvConfig()
        self.fe = feature_engineer or FeatureEngineer(sequence_length=self.cfg.sequence_length)
        self.atr = ATRCalculator()
        self._neutral_of = OrderflowSnapshot()
        self._neutral_liq = LiquidationAnalysis(
            [], [], None, None, 0.0, 0.0, "neutral", 0, 0.0
        )

        fv = self._build_batch(self.cfg.sequence_length).feature_count
        self._meta_tail = 4
        self.obs_dim = fv + self._meta_tail

        self.t: int = 0
        self._steps_in_episode = 0
        self._prev_exposure = 0.0
        self._equity = 1.0
        self._peak_equity = 1.0
        self._last_pnl = 0.0
        self._dd = 0.0
        self._win_hint = 0.5

    def _build_batch(self, t_end: int):
        sl = self.cfg.sequence_length
        chunk = self.klines[: t_end + 1]
        atr = self.atr.calculate(chunk)
        return self.fe.build(chunk[-sl:] if len(chunk) >= sl else chunk, self._neutral_of, self._neutral_liq, atr)

    def _obs_array(self) -> Observation:
        t = self.t
        batch = self._build_batch(t)
        v = np.asarray(batch.latest_vector, dtype=np.float32)
        meta = np.array(
            [
                float(min(1.0, max(0.0, self._dd))),
                float(np.clip(self._last_pnl, -1.0, 1.0)),
                float(min(1.0, max(0.0, self._win_hint))),
                float(self._steps_in_episode) / max(1.0, float(self.cfg.max_episode_steps)),
            ],
            dtype=np.float32,
        )
        return np.concatenate([v, meta]).astype(np.float32)

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Observation, Dict[str, Any]]:
        if seed is not None:
            np.random.seed(seed)
        options = options or {}
        start_min = self.cfg.sequence_length
        start_max = max(start_min + 10, len(self.klines) - self.cfg.max_episode_steps - 3)
        if options.get("start_t") is not None:
            self.t = int(options["start_t"])
        elif start_max > start_min:
            self.t = int(np.random.randint(start_min, start_max))
        else:
            self.t = start_min

        self._steps_in_episode = 0
        self._prev_exposure = 0.0
        self._equity = 1.0
        self._peak_equity = 1.0
        self._last_pnl = 0.0
        self._dd = 0.0
        self._win_hint = 0.5
        obs = self._obs_array()
        info = {"t": self.t, "equity": self._equity}
        return obs, info

    def step(self, action: int) -> StepResult:
        a = int(action) % 4
        risk = float(ACTION_RISK_MULT.get(a, 1.0))
        exposure = 0.0 if a == 0 else risk

        t = self.t
        if t >= len(self.klines) - 2:
            obs = self._obs_array()
            return obs, 0.0, True, False, {"done": "eod"}

        batch = self._build_batch(t)
        mom = float(batch.summary.get("momentum", 0.0) or 0.0)
        dir_sig = float(np.sign(mom)) if abs(mom) > 1e-8 else 0.0

        c0 = float(self.klines[t]["close"])
        c1 = float(self.klines[t + 1]["close"])
        bar_ret = (c1 / c0 - 1.0) if c0 > 0 else 0.0

        pnl = dir_sig * bar_ret * exposure
        turnover = abs(exposure - self._prev_exposure)
        fee_cost = self.cfg.fee_per_turnover * turnover
        reward = pnl - fee_cost

        self._prev_exposure = exposure
        self._equity *= 1.0 + reward
        self._peak_equity = max(self._peak_equity, self._equity)
        self._dd = (self._peak_equity - self._equity) / max(self._peak_equity, 1e-9)
        self._last_pnl = reward
        self._win_hint = 0.95 * self._win_hint + 0.05 * (1.0 if reward > 0 else 0.0)

        reward -= self.cfg.drawdown_penalty_coef * max(0.0, self._dd - 0.05)

        self.t += 1
        self._steps_in_episode += 1

        terminated = self.t >= len(self.klines) - 2
        truncated = self._steps_in_episode >= self.cfg.max_episode_steps
        obs = self._obs_array()
        info = {
            "t": self.t,
            "equity": self._equity,
            "bar_ret": bar_ret,
            "exposure": exposure,
            "dir_sig": dir_sig,
        }
        return obs, float(reward), terminated, truncated, info


class MetaTradingEnv(_MetaTradingEnvBase):
    """
    Gymnasium-совместимый API (reset → (obs, info), step → (obs, reward, term, trunc, info)).

    Для Stable-Baselines3 / Ray: ``pip install gymnasium`` и оберните env в
    ``gymnasium.wrappers`` или наследуйте от ``gymnasium.Env`` локально (два строковых изменения).
    """

    def __init__(
        self,
        klines: List[Dict[str, Any]],
        config: Optional[MetaTradingEnvConfig] = None,
        feature_engineer: Optional[FeatureEngineer] = None,
    ):
        super().__init__(klines, config, feature_engineer)
        if _GYM and spaces is not None:
            self.action_space = spaces.Discrete(4)
            self.observation_space = spaces.Box(
                low=-np.inf, high=np.inf, shape=(self.obs_dim,), dtype=np.float32
            )
        else:  # pragma: no cover
            self.action_space = None
            self.observation_space = None
