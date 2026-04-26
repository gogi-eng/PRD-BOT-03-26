"""Specialized signal agents + multi-agent aggregation (scaffold)."""
from __future__ import annotations

from .breakout_agent import BreakoutAgent
from .meanrev_agent import MeanRevAgent
from .multi_agent_manager import MultiAgentManager
from .scalp_agent import ScalpAgent
from .trend_agent import TrendAgent

__all__ = [
    "BreakoutAgent",
    "MeanRevAgent",
    "MultiAgentManager",
    "ScalpAgent",
    "TrendAgent",
]
