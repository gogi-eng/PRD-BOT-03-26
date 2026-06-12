"""
StrategyRouter: выбор Scalp / Swing по trading.active_strategy или часу supervisor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from prd_agent.strategies.base import StrategyProfile
from prd_agent.strategies.scalp_strategy import SCALP_PROFILE
from prd_agent.strategies.swing_strategy import SWING_PROFILE

logger = logging.getLogger("prd_agent.strategies")

_PROFILES = {
    "scalp": SCALP_PROFILE,
    "swing": SWING_PROFILE,
}


def resolve_active_strategy(
    cfg: Dict[str, Any],
    *,
    utc_hour: Optional[int] = None,
) -> StrategyProfile:
    t = cfg.get("trading", {}) if isinstance(cfg.get("trading"), dict) else {}
    strat_block = t.get("strategies", {})
    if not isinstance(strat_block, dict):
        strat_block = {}

    explicit = str(t.get("active_strategy", "") or strat_block.get("active", "")).strip().lower()
    if explicit in _PROFILES:
        return _PROFILES[explicit]

    hour = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour
    scalp_hours = strat_block.get("scalp_hours_utc")
    if isinstance(scalp_hours, list) and scalp_hours:
        try:
            hours_set = {int(h) for h in scalp_hours}
        except (TypeError, ValueError):
            hours_set = set()
        if hour in hours_set:
            return SCALP_PROFILE

    return SWING_PROFILE


class StrategyRouter:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self._profile = resolve_active_strategy(cfg)

    @property
    def profile(self) -> StrategyProfile:
        return self._profile

    def refresh(self, *, utc_hour: Optional[int] = None) -> StrategyProfile:
        prev = self._profile.name
        self._profile = resolve_active_strategy(self.cfg, utc_hour=utc_hour)
        if self._profile.name != prev:
            logger.info("StrategyRouter: %s → %s", prev, self._profile.name)
        return self._profile

    def skip_stats_key(self) -> str:
        return f"strategy:{self._profile.name}"
