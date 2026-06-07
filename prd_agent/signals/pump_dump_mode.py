"""Режим быстрой сделки по сигналу памп/дамп (Mirror scout)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from prd_agent.positions.exit_management import ExitManagementConfig
from prd_agent.positions.tp_progress_exit import TpProgressExitConfig
from prd_agent.signals.types import UnifiedSignal

_PUMP_DUMP_SOURCE_MARKERS = (
    "mirror_pump_dump",
    "pump_dump",
    "pumpdump",
    "pump/dump",
)
_AGENT_WORLD_MARKERS = (
    "agent_world",
    "agent-world",
)


def is_agent_world_signal(sig: UnifiedSignal) -> bool:
    """Сигнал из RSS-новостей AGENT-WORLD (срочный вход по событию)."""
    src = str(sig.source or "").lower()
    if any(m in src for m in _AGENT_WORLD_MARKERS):
        return True
    raw = sig.raw if isinstance(sig.raw, dict) else {}
    for key in ("source", "channel"):
        val = str(raw.get(key, "") or "").lower()
        if any(m in val for m in _AGENT_WORLD_MARKERS):
            return True
    return False


def is_pump_dump_signal(sig: UnifiedSignal) -> bool:
    """Сигнал «прыжок» от Mirror или inbox с тем же source/channel."""
    src = str(sig.source or "").lower()
    if any(m in src for m in _PUMP_DUMP_SOURCE_MARKERS):
        return True
    raw = sig.raw if isinstance(sig.raw, dict) else {}
    for key in ("source", "channel"):
        val = str(raw.get(key, "") or "").lower()
        if any(m in val for m in _PUMP_DUMP_SOURCE_MARKERS):
            return True
    reason = str(sig.reason or "").lower()
    if "pattern_score=" in reason and "fast-exec" in reason:
        return True
    return False


def pump_dump_trade_enabled(cfg: Mapping[str, Any]) -> bool:
    block = cfg.get("pump_dump_trade", {})
    if not isinstance(block, dict):
        return True
    return bool(block.get("enabled", True))


@dataclass(frozen=True)
class TrailingProfile:
    activation_pct: float
    distance_pct: float
    distance_atr_mult: float
    min_distance_pct: float
    breakeven_pct: float
    tp_progress: TpProgressExitConfig
    exit_management: ExitManagementConfig

    @classmethod
    def from_positions_cfg(
        cls,
        positions_cfg: Dict[str, Any],
        *,
        subsection: str = "",
    ) -> TrailingProfile:
        parent = positions_cfg if isinstance(positions_cfg, dict) else {}
        if subsection:
            base = parent.get(subsection, {})
            if not isinstance(base, dict):
                base = {}
            fallbacks = {
                "trailing_activation_pct": 0.45,
                "trailing_distance_pct": 0.55,
                "trailing_distance_atr_mult": 1.15,
                "trailing_min_distance_pct": 0.28,
                "breakeven_after_pct": 0.38,
            }
        else:
            base = parent
            fallbacks = {}
        tp_raw = base.get("tp_progress_exit")
        if not isinstance(tp_raw, dict):
            tp_raw = parent.get("tp_progress_exit") if subsection else {}
        if not isinstance(tp_raw, dict):
            tp_raw = {}
        em_raw = base.get("exit_management")
        if not isinstance(em_raw, dict):
            em_raw = parent.get("exit_management") if subsection else {}
        if not isinstance(em_raw, dict):
            em_raw = {}
        merged_positions = dict(parent)
        merged_positions["tp_progress_exit"] = tp_raw
        merged_positions["exit_management"] = em_raw
        def _f(key: str, default: float) -> float:
            if key in base:
                return float(base[key])
            if fallbacks:
                return float(fallbacks.get(key, default))
            return float(parent.get(key, default))

        return cls(
            activation_pct=_f("trailing_activation_pct", 1.35),
            distance_pct=_f("trailing_distance_pct", 1.55),
            distance_atr_mult=_f("trailing_distance_atr_mult", 2.5),
            min_distance_pct=_f("trailing_min_distance_pct", 0.65),
            breakeven_pct=_f("breakeven_after_pct", 1.05),
            tp_progress=TpProgressExitConfig.from_cfg(merged_positions),
            exit_management=ExitManagementConfig.from_cfg(merged_positions),
        )


def entry_drift_limits(
    cfg: Mapping[str, Any], sig: UnifiedSignal
) -> Optional[Dict[str, float]]:
    """Пороги entry_guard для памп/дамп или AGENT-WORLD (шире drift) или None = обычные."""
    aw = cfg.get("agent_world", {})
    aw_block = aw if isinstance(aw, dict) else {}
    aw_fast = bool(aw_block.get("enabled", False)) and is_agent_world_signal(sig)
    if aw_fast:
        return {
            "max_market_drift_pct": float(aw_block.get("max_market_drift_pct", 0.015)),
            "max_limit_drift_pct": float(aw_block.get("max_limit_drift_pct", 0.022)),
            "max_skip_drift_pct": float(aw_block.get("max_skip_drift_pct", 0.035)),
        }
    if not pump_dump_trade_enabled(cfg) or not is_pump_dump_signal(sig):
        return None
    block = cfg.get("pump_dump_trade", {})
    if not isinstance(block, dict):
        block = {}
    return {
        "max_market_drift_pct": float(block.get("max_market_drift_pct", 0.012)),
        "max_limit_drift_pct": float(block.get("max_limit_drift_pct", 0.018)),
        "max_skip_drift_pct": float(block.get("max_skip_drift_pct", 0.03)),
    }
