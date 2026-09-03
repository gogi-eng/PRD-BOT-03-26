"""
GARCH: закрытие при откате от пика после достижения зоны TP (≥ min_tp_progress_pct).

Маркер лога: «GARCH TP peak retrace».
Config: manual_trailing_garch_learning.tp_peak_retrace_exit
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

from prd_agent.positions.tp_progress_exit import progress_to_take_profit_pct

logger = logging.getLogger("prd_agent.garch_tp_peak_retrace")

_LOG_MARKER = "GARCH TP peak retrace"

_DEFAULT_RETRACE_BY_REGIME = {
    "calm": 20.0,
    "normal": 25.0,
    "storm": 35.0,
}


@dataclass
class GarchTpPeakRetraceConfig:
    enabled: bool = False
    min_tp_progress_pct: float = 90.0
    retrace_from_peak_pct: float = 25.0
    retrace_by_regime: Dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_RETRACE_BY_REGIME)
    )
    min_peak_profit_pct: float = 0.5
    apply_to_manual: bool = True
    apply_to_bot: bool = True
    log_marker: str = _LOG_MARKER

    @classmethod
    def from_cfg(cls, root_cfg: Mapping[str, Any]) -> "GarchTpPeakRetraceConfig":
        raw_root = root_cfg.get("manual_trailing_garch_learning")
        if not isinstance(raw_root, dict):
            return cls(enabled=False)
        raw = raw_root.get("tp_peak_retrace_exit")
        if not isinstance(raw, dict):
            return cls(enabled=False)
        by_regime = dict(_DEFAULT_RETRACE_BY_REGIME)
        rm = raw.get("retrace_by_regime")
        if isinstance(rm, dict):
            for key in ("calm", "normal", "storm"):
                if key in rm:
                    try:
                        by_regime[key] = float(rm[key])
                    except (TypeError, ValueError):
                        pass
        base = float(raw.get("retrace_from_peak_pct", 25.0) or 25.0)
        if "normal" not in (rm or {}):
            by_regime["normal"] = base
        return cls(
            enabled=bool(raw.get("enabled", False)),
            min_tp_progress_pct=float(raw.get("min_tp_progress_pct", 90.0) or 90.0),
            retrace_from_peak_pct=base,
            retrace_by_regime=by_regime,
            min_peak_profit_pct=float(raw.get("min_peak_profit_pct", 0.5) or 0.5),
            apply_to_manual=bool(raw.get("apply_to_manual", True)),
            apply_to_bot=bool(raw.get("apply_to_bot", True)),
            log_marker=str(raw.get("log_marker", _LOG_MARKER)),
        )


def retrace_threshold_pct(regime: str, cfg: GarchTpPeakRetraceConfig) -> float:
    key = str(regime or "normal").lower()
    dm = cfg.retrace_by_regime or _DEFAULT_RETRACE_BY_REGIME
    try:
        return float(dm.get(key, dm.get("normal", cfg.retrace_from_peak_pct)))
    except (TypeError, ValueError):
        return cfg.retrace_from_peak_pct


def should_apply_tp_peak_retrace(
    *,
    cfg: GarchTpPeakRetraceConfig,
    origin: str,
) -> bool:
    if not cfg.enabled:
        return False
    origin_l = str(origin or "").lower()
    if origin_l == "manual":
        return cfg.apply_to_manual
    if origin_l == "bot":
        return cfg.apply_to_bot
    return cfg.apply_to_manual or cfg.apply_to_bot


def evaluate_garch_tp_peak_retrace(
    *,
    side: str,
    entry: float,
    mark: float,
    take_profit: float,
    current_profit_pct: float,
    tp_zone_armed: bool,
    tp_zone_peak_profit_pct: float,
    regime: str,
    origin: str,
    cfg: GarchTpPeakRetraceConfig,
    symbol: str = "",
) -> Tuple[bool, float, Optional[str], str]:
    """
    Обновляет состояние зоны TP и возвращает (armed, zone_peak, action, note).
    action = close_garch_tp_retrace или None.
    """
    if not should_apply_tp_peak_retrace(cfg=cfg, origin=origin):
        return tp_zone_armed, tp_zone_peak_profit_pct, None, "origin skip"

    tp_prog = progress_to_take_profit_pct(side, entry, mark, take_profit)
    armed = tp_zone_armed
    zone_peak = tp_zone_peak_profit_pct

    if tp_prog is not None and tp_prog >= cfg.min_tp_progress_pct:
        if not armed:
            armed = True
            zone_peak = max(zone_peak, current_profit_pct)
        else:
            zone_peak = max(zone_peak, current_profit_pct)

    if not armed:
        return armed, zone_peak, None, f"tp_prog={tp_prog or 0:.1f}%<{cfg.min_tp_progress_pct:.0f}%"

    if zone_peak < cfg.min_peak_profit_pct:
        return armed, zone_peak, None, f"zone_peak={zone_peak:.2f}%<{cfg.min_peak_profit_pct:.2f}%"

    thr = retrace_threshold_pct(regime, cfg)
    drop = zone_peak - current_profit_pct
    if zone_peak <= 1e-9:
        return armed, zone_peak, None, "zone_peak~0"

    retrace_pct = drop / zone_peak * 100.0
    if retrace_pct < thr:
        return armed, zone_peak, None, (
            f"retrace={retrace_pct:.1f}%<{thr:.0f}% peak={zone_peak:.2f}% regime={regime}"
        )

    note = (
        f"{cfg.log_marker}: {symbol} {side} regime={regime} "
        f"tp_prog={tp_prog or 0:.1f}% peak={zone_peak:.2f}% now={current_profit_pct:.2f}% "
        f"retrace={retrace_pct:.1f}%>={thr:.0f}%"
    )
    logger.info(note)
    return armed, zone_peak, "close_garch_tp_retrace", note


def format_telegram_tp_retrace_summary(cfg: GarchTpPeakRetraceConfig) -> str:
    if not cfg.enabled:
        return "TP peak retrace: <i>выкл</i>"
    dm = cfg.retrace_by_regime or _DEFAULT_RETRACE_BY_REGIME
    return (
        f"TP peak retrace (после {cfg.min_tp_progress_pct:.0f}% пути к TP): "
        f"calm <code>{dm.get('calm', 20):.0f}%</code> / "
        f"normal <code>{dm.get('normal', 25):.0f}%</code> / "
        f"storm <code>{dm.get('storm', 35):.0f}%</code>"
    )
