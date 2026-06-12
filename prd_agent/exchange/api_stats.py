"""Журнал API-нагрузки: счётчик запросов на цикл и с момента старта."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("prd_agent.api_stats")


class ApiCallJournal:
    def __init__(self) -> None:
        self._cycle_num = 0
        self._cycle_calls: Dict[str, int] = {}
        self._cycle_cache_hits: Dict[str, int] = {}
        self._total_calls = 0
        self._last_cycle_total = 0

    def begin_cycle(self, cycle_num: int) -> None:
        self._cycle_num = int(cycle_num)
        self._cycle_calls = {}
        self._cycle_cache_hits = {}

    def record(self, endpoint: str, *, cached: bool = False) -> None:
        ep = str(endpoint or "unknown")
        if cached:
            self._cycle_cache_hits[ep] = self._cycle_cache_hits.get(ep, 0) + 1
            return
        self._cycle_calls[ep] = self._cycle_calls.get(ep, 0) + 1
        self._total_calls += 1

    def end_cycle(self) -> Dict[str, Any]:
        total = sum(self._cycle_calls.values())
        self._last_cycle_total = total
        snap = {
            "cycle": self._cycle_num,
            "calls": total,
            "by_endpoint": dict(sorted(self._cycle_calls.items())),
            "cache_hits": dict(sorted(self._cycle_cache_hits.items())),
            "total_since_boot": self._total_calls,
        }
        if total > 0:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(self._cycle_calls.items()))
            logger.info("API cycle %d: %d REST calls (%s)", self._cycle_num, total, parts)
        return snap

    def snapshot(self) -> Dict[str, Any]:
        return {
            "cycle": self._cycle_num,
            "last_cycle_calls": self._last_cycle_total,
            "total_since_boot": self._total_calls,
        }
