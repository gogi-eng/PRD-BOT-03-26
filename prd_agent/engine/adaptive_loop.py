"""Адаптивный интервал основного цикла бота."""
from __future__ import annotations

from typing import Any, Dict


def compute_loop_interval_sec(
    cfg: Dict[str, Any],
    *,
    open_positions: int,
    signals_this_cycle: int,
    seconds_since_activity: float,
) -> float:
    """
    active_sec — есть открытые позиции (чаще смотрим SL/трейлинг).
    base_sec — есть сигналы, но позиций нет.
    idle_sec — тишина дольше idle_after_quiet_min.
    """
    t = cfg.get("trading", {}) if isinstance(cfg.get("trading"), dict) else {}
    al = t.get("adaptive_loop", {})
    if not isinstance(al, dict) or not bool(al.get("enabled", False)):
        return float(t.get("loop_interval_sec", 60))

    base = float(al.get("base_sec", t.get("loop_interval_sec", 60)))
    active = float(al.get("active_sec", min(base, 45)))
    idle = float(al.get("idle_sec", max(base, 120)))
    quiet_min = float(al.get("idle_after_quiet_min", 60))

    if open_positions > 0:
        return max(15.0, active)
    if signals_this_cycle > 0:
        return max(15.0, base)
    if seconds_since_activity >= quiet_min * 60.0:
        return max(15.0, idle)
    return max(15.0, base)
