"""Правила выхода: time-stop, ранний/поздний breakeven по прогрессу в ATR."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


@dataclass
class ExitManagementConfig:
    enabled: bool = True
    time_stop_enabled: bool = True
    time_stop_minutes: float = 120.0
    time_stop_min_atr_progress: float = 0.25
    close_on_time_stop: bool = True
    early_breakeven_enabled: bool = True
    early_breakeven_atr_mult: float = 0.45
    early_breakeven_pct: float = 0.18
    late_breakeven_enabled: bool = True
    late_breakeven_retrace_pct: float = 40.0
    close_on_late_retrace: bool = False
    late_tighten_distance_factor: float = 0.55

    @classmethod
    def from_cfg(cls, positions_cfg: Dict[str, Any]) -> ExitManagementConfig:
        raw = positions_cfg.get("exit_management")
        if not isinstance(raw, dict):
            raw = {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            time_stop_enabled=bool(raw.get("time_stop_enabled", True)),
            time_stop_minutes=float(raw.get("time_stop_minutes", 120) or 120),
            time_stop_min_atr_progress=float(raw.get("time_stop_min_atr_progress", 0.25) or 0.25),
            close_on_time_stop=bool(raw.get("close_on_time_stop", True)),
            early_breakeven_enabled=bool(raw.get("early_breakeven_enabled", True)),
            early_breakeven_atr_mult=float(raw.get("early_breakeven_atr_mult", 0.45) or 0.45),
            early_breakeven_pct=float(raw.get("early_breakeven_pct", 0.18) or 0.18),
            late_breakeven_enabled=bool(raw.get("late_breakeven_enabled", True)),
            late_breakeven_retrace_pct=float(raw.get("late_breakeven_retrace_pct", 40) or 40),
            close_on_late_retrace=bool(raw.get("close_on_late_retrace", False)),
            late_tighten_distance_factor=float(raw.get("late_tighten_distance_factor", 0.55) or 0.55),
        )


def profit_pct(side: str, entry: float, price: float) -> float:
    if entry <= 0 or price <= 0:
        return 0.0
    if side == "Buy":
        return (price - entry) / entry * 100.0
    return (entry - price) / entry * 100.0


def progress_in_atr(side: str, entry: float, price: float, atr: float) -> float:
    if entry <= 0 or atr <= 0:
        return 0.0
    move = (price - entry) if side == "Buy" else (entry - price)
    return max(0.0, move / atr)


def age_minutes(opened_at_iso: str, now: Optional[datetime] = None) -> float:
    if not opened_at_iso:
        return 0.0
    now = now or datetime.now(timezone.utc)
    try:
        opened = datetime.fromisoformat(opened_at_iso.replace("Z", "+00:00"))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return max(0.0, (now - opened).total_seconds() / 60.0)
    except (TypeError, ValueError):
        return 0.0


def effective_breakeven_pct(
    base_pct: float,
    *,
    cfg: ExitManagementConfig,
    progress_atr: float,
) -> float:
    if cfg.enabled and cfg.early_breakeven_enabled and progress_atr >= cfg.early_breakeven_atr_mult:
        return min(base_pct, cfg.early_breakeven_pct)
    return base_pct


def late_retrace_active(
    *,
    cfg: ExitManagementConfig,
    peak_profit_pct: float,
    current_profit_pct: float,
) -> bool:
    if not cfg.enabled or not cfg.late_breakeven_enabled:
        return False
    if peak_profit_pct <= 0.05:
        return False
    drop = peak_profit_pct - current_profit_pct
    return drop >= peak_profit_pct * (cfg.late_breakeven_retrace_pct / 100.0)


def evaluate_exit_actions(
    *,
    cfg: ExitManagementConfig,
    side: str,
    entry: float,
    price: float,
    atr: float,
    opened_at_iso: str,
    peak_profit_pct: float,
    now: Optional[datetime] = None,
) -> Tuple[Optional[str], str]:
    """
    Возвращает (action, reason):
    action in {None, "close_time_stop", "close_late_retrace"}
    """
    if not cfg.enabled or entry <= 0 or price <= 0:
        return None, ""

    p_pct = profit_pct(side, entry, price)
    prog_atr = progress_in_atr(side, entry, price, atr)
    age = age_minutes(opened_at_iso, now)

    if cfg.time_stop_enabled and age >= cfg.time_stop_minutes:
        if prog_atr < cfg.time_stop_min_atr_progress:
            if cfg.close_on_time_stop:
                return (
                    "close_time_stop",
                    f"time-stop {age:.0f}m, прогресс {prog_atr:.2f} ATR < {cfg.time_stop_min_atr_progress}",
                )
            return None, "time_stop_would"

    if late_retrace_active(cfg=cfg, peak_profit_pct=peak_profit_pct, current_profit_pct=p_pct):
        if cfg.close_on_late_retrace and p_pct <= 0.05:
            return (
                "close_late_retrace",
                f"откат {peak_profit_pct - p_pct:.2f}% от пика {peak_profit_pct:.2f}%",
            )

    return None, ""
