"""Базовый профиль стратегии."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    kline_interval: str
    htf_interval: Optional[str]
    require_htf: bool
    zone_entry_enabled: bool
    require_bos: bool
    impulse_retest_enabled: bool
    label: str
