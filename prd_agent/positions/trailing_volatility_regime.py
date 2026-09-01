"""
GARCH calm/normal/storm → множитель дистанции трейлинг-SL.

Маркер лога: «Trailing GARCH».
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from prd_agent.risk.volatility_regime_sizing import (
    closes_from_klines,
    compute_volatility_regime,
    read_volatility_regime_cfg,
)

logger = logging.getLogger("prd_agent.trailing_garch")

_LOG_MARKER = "Trailing GARCH"


@dataclass
class TrailingVolatilityRegimeConfig:
    enabled: bool = False
    advisory_only: bool = False
    reuse_sizing_cfg: bool = True
    distance_mult: Dict[str, float] = field(
        default_factory=lambda: {"calm": 0.75, "normal": 1.0, "storm": 1.35}
    )
    clamp_min: float = 0.50
    clamp_max: float = 2.0
    apply_to_manual: bool = True
    apply_to_bot: bool = True
    apply_to_pump_dump: bool = True
    min_bars: int = 80
    lookback_bars: int = 200

    @classmethod
    def from_cfg(cls, root_cfg: Mapping[str, Any]) -> "TrailingVolatilityRegimeConfig":
        positions = root_cfg.get("positions", {}) if isinstance(root_cfg.get("positions"), dict) else {}
        raw = positions.get("trailing_volatility_regime")
        if not isinstance(raw, dict):
            return cls(enabled=False)
        dm = raw.get("distance_mult")
        distance_mult = dict(dm) if isinstance(dm, dict) else {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            advisory_only=bool(raw.get("advisory_only", False)),
            reuse_sizing_cfg=bool(raw.get("reuse_sizing_cfg", True)),
            distance_mult=distance_mult or {"calm": 0.75, "normal": 1.0, "storm": 1.35},
            clamp_min=float(raw.get("clamp_min", 0.50) or 0.50),
            clamp_max=float(raw.get("clamp_max", 2.0) or 2.0),
            apply_to_manual=bool(raw.get("apply_to_manual", True)),
            apply_to_bot=bool(raw.get("apply_to_bot", True)),
            apply_to_pump_dump=bool(raw.get("apply_to_pump_dump", True)),
            min_bars=int(raw.get("min_bars", 80) or 80),
            lookback_bars=int(raw.get("lookback_bars", 200) or 200),
        )


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def regime_distance_mult(regime: str, cfg: TrailingVolatilityRegimeConfig) -> float:
    key = str(regime or "normal").lower()
    defaults = {"calm": 0.75, "normal": 1.0, "storm": 1.35, "unknown": 1.0, "disabled": 1.0}
    try:
        raw = float(cfg.distance_mult.get(key, defaults.get(key, 1.0)))
    except (TypeError, ValueError):
        raw = float(defaults.get(key, 1.0))
    return _clamp(raw, cfg.clamp_min, cfg.clamp_max)


def should_apply_trailing_volatility_regime(
    *,
    cfg: TrailingVolatilityRegimeConfig,
    origin: str,
    pump_dump_mode: bool,
) -> bool:
    if not cfg.enabled:
        return False
    origin_l = str(origin or "").lower()
    if pump_dump_mode and cfg.apply_to_pump_dump:
        return True
    if origin_l == "manual":
        return cfg.apply_to_manual
    return cfg.apply_to_bot


def _garch_block_for_trailing(
    trail_cfg: TrailingVolatilityRegimeConfig,
    root_cfg: Mapping[str, Any],
) -> Dict[str, Any]:
    if trail_cfg.reuse_sizing_cfg:
        block = dict(read_volatility_regime_cfg(root_cfg))
        if block:
            block.setdefault("min_bars", trail_cfg.min_bars)
            block.setdefault("lookback_bars", trail_cfg.lookback_bars)
            return block
    return {
        "enabled": True,
        "min_bars": trail_cfg.min_bars,
        "lookback_bars": trail_cfg.lookback_bars,
        "kline_interval": "15",
        "alpha": 0.08,
        "beta": 0.90,
        "calm_percentile": 30,
        "storm_percentile": 70,
    }


def compute_trailing_garch_distance_factor(
    *,
    klines: Sequence[Mapping[str, Any]],
    trail_cfg: TrailingVolatilityRegimeConfig,
    root_cfg: Mapping[str, Any],
) -> Tuple[float, str, str]:
    if not trail_cfg.enabled:
        return 1.0, "disabled", "disabled"
    closes = closes_from_klines(klines)
    block = _garch_block_for_trailing(trail_cfg, root_cfg)
    block["enabled"] = True
    result = compute_volatility_regime(closes, block)
    regime = str(result.regime or "unknown")
    mult = regime_distance_mult(regime, trail_cfg)
    note = result.reason or regime
    return mult, regime, note


def apply_trailing_garch_to_distance_factor(
    base_factor: float,
    *,
    klines: Sequence[Mapping[str, Any]],
    trail_cfg: TrailingVolatilityRegimeConfig,
    root_cfg: Mapping[str, Any],
    symbol: str = "",
    side: str = "",
    prev_regime: str = "",
) -> Tuple[float, str, Optional[str]]:
    if not trail_cfg.enabled:
        return base_factor, "disabled", "disabled"
    mult, regime, note = compute_trailing_garch_distance_factor(
        klines=klines,
        trail_cfg=trail_cfg,
        root_cfg=root_cfg,
    )
    if trail_cfg.advisory_only:
        if regime != prev_regime and regime not in ("disabled", "unknown"):
            logger.info(
                "%s %s %s advisory regime=%s mult=%.2f (%s)",
                _LOG_MARKER,
                symbol,
                side,
                regime,
                mult,
                note,
            )
        return base_factor, regime, note
    new_factor = float(base_factor) * mult
    if regime != prev_regime:
        logger.info(
            "%s %s %s regime=%s dist×%.2f (base=%.2f → %.2f) %s",
            _LOG_MARKER,
            symbol,
            side,
            regime,
            mult,
            base_factor,
            new_factor,
            note,
        )
    return new_factor, regime, note
