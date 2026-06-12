"""Scalp: 1m/5m, без 4H, быстрый path."""
from __future__ import annotations

from prd_agent.strategies.base import StrategyProfile

SCALP_PROFILE = StrategyProfile(
    name="scalp",
    kline_interval="5",
    htf_interval=None,
    require_htf=False,
    zone_entry_enabled=False,
    require_bos=False,
    impulse_retest_enabled=False,
    label="Scalp 5m",
)
