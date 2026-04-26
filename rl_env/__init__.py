"""
Gymnasium-подобная среда для обучения meta-RL на тех же фичах, что ``FeatureEngineer``.

Направление «альфы» берётся из уже посчитанного momentum (без lookahead на будущий бар).
Действие 0..3 — уровень агрессии/риска как у ``engine.rl_meta_controller.ACTION_RISK_MULT``.
"""
from __future__ import annotations

from .meta_trading_env import MetaTradingEnv

try:
    from .sb3_env import SB3MetaTradingEnv
except ImportError:  # pragma: no cover
    SB3MetaTradingEnv = None  # type: ignore

__all__ = ["MetaTradingEnv", "SB3MetaTradingEnv"]
