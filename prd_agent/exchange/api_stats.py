"""Журнал API-нагрузки: счётчик запросов на цикл и с момента старта."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("prd_agent.api_stats")


class ApiCallJournal:
    def __init__(self) -> None:
        self._cycle_num = 0
        self._cycle_calls: Dict[str, int] = {}
        self._cycle_cache_hits: Dict[str, int] = {}
        self._total_calls = 0
        self._last_cycle_total = 0
        self._last_cycle_cache_hits: Dict[str, int] = {}
        self._last_cycle_hit_rate_pct = 0.0

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
        hits = dict(self._cycle_cache_hits)
        hit_n = sum(hits.values())
        denom = total + hit_n
        self._last_cycle_total = total
        self._last_cycle_cache_hits = hits
        self._last_cycle_hit_rate_pct = round(hit_n / denom * 100, 1) if denom else 0.0
        snap = {
            "cycle": self._cycle_num,
            "calls": total,
            "by_endpoint": dict(sorted(self._cycle_calls.items())),
            "cache_hits": dict(sorted(hits.items())),
            "total_since_boot": self._total_calls,
            "hit_rate_pct": self._last_cycle_hit_rate_pct,
        }
        if total > 0 or hit_n > 0:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(self._cycle_calls.items()))
            logger.info(
                "API cycle %d: %d REST calls (%s) cache_hits=%d (%.0f%%)",
                self._cycle_num,
                total,
                parts or "-",
                hit_n,
                self._last_cycle_hit_rate_pct,
            )
        return snap

    def snapshot(self) -> Dict[str, Any]:
        return {
            "cycle": self._cycle_num,
            "last_cycle_calls": self._last_cycle_total,
            "total_since_boot": self._total_calls,
            "last_cycle_cache_hits": dict(self._last_cycle_cache_hits),
            "last_cycle_hit_rate_pct": self._last_cycle_hit_rate_pct,
        }


def format_api_stats_lines(snap: Dict[str, Any], journal: Optional[ApiCallJournal] = None) -> List[str]:
    """Строки для bi-hourly / лог: REST на последний цикл и hit-rate кеша."""
    if journal is not None:
        snap = journal.snapshot()
    calls = int(snap.get("last_cycle_calls", 0) or 0)
    total_boot = int(snap.get("total_since_boot", 0) or 0)
    hit_pct = float(snap.get("last_cycle_hit_rate_pct", 0) or 0)
    hits = snap.get("last_cycle_cache_hits", {})
    hit_n = sum(int(v) for v in hits.values()) if isinstance(hits, dict) else 0
    lines = [
        f"• REST на цикл: {calls} | с момента старта: {total_boot}",
        f"• Кеш API: hit {hit_n} ({hit_pct:.0f}% последний цикл)",
    ]
    if isinstance(hits, dict) and hits:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(hits.items()))
        lines.append(f"  ↳ hits: {parts}")
    return lines
