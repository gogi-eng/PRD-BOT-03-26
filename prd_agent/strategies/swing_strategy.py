"""Swing: 15m/4h, BOS, зоны, строже HTF."""
from __future__ import annotations

from prd_agent.strategies.base import StrategyProfile

SWING_PROFILE = StrategyProfile(
    name="swing",
    kline_interval="15",
    htf_interval="240",
    require_htf=True,
    zone_entry_enabled=True,
    require_bos=True,
    impulse_retest_enabled=True,
    label="Swing 15m/4h",
)
