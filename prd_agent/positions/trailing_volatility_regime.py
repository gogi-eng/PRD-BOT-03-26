"""GARCH calm/normal/storm → множитель дистанции трейлинг-SL.

Спокойный рынок — ближе поджимать прибыль (меньше distance).
Шторм — дать больше «воздуха» (шире SL), чтобы не выбило шумом.

Config: positions.trailing_volatility_regime
Маркер лога: «Trailing GARCH».
Режим волатильности считается тем же GARCH, что и volatility_regime_sizing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from prd_agent.risk.volatility_regime_sizing import (
    closes_from_klines,
    compute_volatility_regime,
    read_volatility_regime_cfg,
)

logger = logging.getLogger("prd_agent.trailing_garch")

_LOG_MARKER = "Trailing GARCH"

# calm → уже; storm → шире (множитель к trail distance / ATR distance)
_DEFAULT_DISTANCE_MULT = {
    "calm": 0.75,
    "normal": 1.0,
    "storm": 1.35,
    "unknown": 1.0,
}


@dataclass(frozen=True)
class TrailingVolatilityRegimeConfig:
    enabled: bool = False
    advisory_only: bool = False
    reuse_sizing_cfg: bool = True
    kline_interval: str = "15"
    lookback_bars: int = 200
    min_bars: int = 80
    alpha: float = 0.08
    beta: float = 0.90
    calm_percentile: float = 30.0
    storm_percentile: float = 70.0
    distance_mult: Dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_DISTANCE_MULT)
    )
    clamp_min: float = 0.50
    clamp_max: float = 2.0
    apply_to_manual: bool = True
    apply_to_bot: bool = True
    apply_to_pump_dump: bool = True

    @classmethod
    def from_cfg(cls, positions_cfg: Mapping[str, Any]) -> TrailingVolatilityRegimeConfig:
        raw = positions_cfg.get("trailing_volatility_regime")
        if not isinstance(raw, dict):
            return cls(enabled=False)
        dm_raw = raw.get("distance_mult") if isinstance(raw.get("distance_mult"), dict) else {}
        dm = dict(_DEFAULT_DISTANCE_MULT)
        for key in ("calm", "normal", "storm", "unknown"):
            if key in dm_raw:
                try:
                    dm[key] = float(dm_raw[key])
                except (TypeError, ValueError):
                    pass
        try:
            clamp_min = float(raw.get("clamp_min", 0.50) or 0.50)
        except (TypeError, ValueError):
            clamp_min = 0.50
        try:
            clamp_max = float(raw.get("clamp_max", 2.0) or 2.0)
        except (TypeError, ValueError):
            clamp_max = 2.0
        if clamp_min > clamp_max:
            clamp_min, clamp_max = clamp_max, clamp_min
        return cls(
            enabled=bool(raw.get("enabled", False)),
            advisory_only=bool(raw.get("advisory_only", False)),
            reuse_sizing_cfg=bool(raw.get("reuse_sizing_cfg", True)),
            kline_interval=str(raw.get("kline_interval", "15") or "15"),
            lookback_bars=max(40, int(raw.get("lookback_bars", 200) or 200)),
            min_bars=max(20, int(raw.get("min_bars", 80) or 80)),
            alpha=float(raw.get("alpha", 0.08) or 0.08),
            beta=float(raw.get("beta", 0.90) or 0.90),
            calm_percentile=float(raw.get("calm_percentile", 30) or 30),
            storm_percentile=float(raw.get("storm_percentile", 70) or 70),
            distance_mult=dm,
            clamp_min=clamp_min,
            clamp_max=clamp_max,
            apply_to_manual=bool(raw.get("apply_to_manual", True)),
            apply_to_bot=bool(raw.get("apply_to_bot", True)),
            apply_to_pump_dump=bool(raw.get("apply_to_pump_dump", True)),
        )


def log_trailing_garch_startup(
    cfg: Mapping[str, Any],
    log: Optional[logging.Logger] = None,
) -> None:
    lg = log or logger
    pos = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
    block = TrailingVolatilityRegimeConfig.from_cfg(pos if isinstance(pos, dict) else {})
    if not block.enabled:
        return
    mode = "только лог (advisory)" if block.advisory_only else "меняет дистанцию трейлинга"
    dm = block.distance_mult or _DEFAULT_DISTANCE_MULT
    lg.info(
        "%s: GARCH calm/normal/storm включён (%s; calm×%.2f normal×%.2f storm×%.2f)",
        _LOG_MARKER,
        mode,
        float(dm.get("calm", 0.75)),
        float(dm.get("normal", 1.0)),
        float(dm.get("storm", 1.35)),
    )


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
    if origin_l == "manual" and cfg.apply_to_manual:
        return True
    if origin_l == "bot" and cfg.apply_to_bot:
        return True
    return False


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def regime_distance_mult(regime: str, cfg: TrailingVolatilityRegimeConfig) -> float:
    dm = cfg.distance_mult or _DEFAULT_DISTANCE_MULT
    key = str(regime or "unknown").lower()
    try:
        raw = float(dm.get(key, dm.get("unknown", 1.0)))
    except (TypeError, ValueError):
        raw = 1.0
    return _clamp(raw, cfg.clamp_min, cfg.clamp_max)


def _garch_block_for_regime(
    trail_cfg: TrailingVolatilityRegimeConfig,
    root_cfg: Mapping[str, Any],
) -> Dict[str, Any]:
    """Параметры GARCH: из volatility_regime_sizing (если reuse) + overrides из trail."""
    block: Dict[str, Any] = {
        "enabled": True,
        "advisory_only": False,
        "block_on_storm": False,
        "mode": "regime",
        "kline_interval": trail_cfg.kline_interval,
        "lookback_bars": trail_cfg.lookback_bars,
        "min_bars": trail_cfg.min_bars,
        "alpha": trail_cfg.alpha,
        "beta": trail_cfg.beta,
        "calm_percentile": trail_cfg.calm_percentile,
        "storm_percentile": trail_cfg.storm_percentile,
        # size mult не важен — нам нужен только regime
        "regimes": {"calm": 1.0, "normal": 1.0, "storm": 1.0},
        "clamp_min": 0.35,
        "clamp_max": 1.50,
    }
    if trail_cfg.reuse_sizing_cfg:
        sizing = read_volatility_regime_cfg(root_cfg)
        for key in (
            "kline_interval",
            "lookback_bars",
            "min_bars",
            "alpha",
            "beta",
            "calm_percentile",
            "storm_percentile",
            "mode",
        ):
            if key in sizing and sizing[key] is not None:
                block[key] = sizing[key]
        # локальные overrides в trailing секции сильнее sizing
        raw_pos = root_cfg.get("positions") if isinstance(root_cfg.get("positions"), dict) else {}
        trail_raw = (
            raw_pos.get("trailing_volatility_regime")
            if isinstance(raw_pos.get("trailing_volatility_regime"), dict)
            else {}
        )
        for key in (
            "kline_interval",
            "lookback_bars",
            "min_bars",
            "alpha",
            "beta",
            "calm_percentile",
            "storm_percentile",
        ):
            if key in trail_raw and trail_raw[key] is not None:
                block[key] = trail_raw[key]
    return block


def compute_trailing_garch_distance_factor(
    *,
    klines: Sequence[Mapping[str, Any]],
    trail_cfg: TrailingVolatilityRegimeConfig,
    root_cfg: Mapping[str, Any],
) -> Tuple[float, str, str]:
    """
    Возвращает (distance_factor, regime, note).
    factor=1.0 если выключено / мало данных / advisory.
    """
    if not trail_cfg.enabled:
        return 1.0, "disabled", "disabled"

    closes = closes_from_klines(klines)
    garch_block = _garch_block_for_regime(trail_cfg, root_cfg)
    # min_bars из garch_block
    result = compute_volatility_regime(closes, garch_block)
    regime = str(result.regime or "unknown")
    mult = regime_distance_mult(regime, trail_cfg)

    if regime in ("disabled",):
        return 1.0, regime, result.reason
    if regime == "unknown" and not result.apply:
        return 1.0, regime, f"skip:{result.reason}"

    if trail_cfg.advisory_only:
        note = f"advisory {regime} mult={mult:.2f} {result.reason}"
        return 1.0, regime, note

    note = f"{regime} mult={mult:.2f} pct={result.percentile:.0f} {result.reason}"
    return mult, regime, note


def apply_trailing_garch_to_distance_factor(
    dist_factor: float,
    *,
    klines: Sequence[Mapping[str, Any]],
    trail_cfg: TrailingVolatilityRegimeConfig,
    root_cfg: Mapping[str, Any],
    symbol: str = "",
    side: str = "",
    prev_regime: str = "",
    log: Optional[logging.Logger] = None,
) -> Tuple[float, str, Optional[str]]:
    """
    Умножает текущий dist_factor на GARCH-множитель.
    Возвращает (new_factor, regime, note).
    Лог только при смене режима (чтобы не спамить каждый цикл).
    """
    if not trail_cfg.enabled:
        return dist_factor, prev_regime or "disabled", None
    g_mult, regime, note = compute_trailing_garch_distance_factor(
        klines=klines,
        trail_cfg=trail_cfg,
        root_cfg=root_cfg,
    )
    lg = log or logger
    new_factor = float(dist_factor) * float(g_mult)
    if trail_cfg.advisory_only:
        new_factor = float(dist_factor)
    if regime != str(prev_regime or ""):
        lg.info(
            "%s %s %s: %s → dist_factor %.2f→%.2f",
            _LOG_MARKER,
            str(symbol or "").upper(),
            str(side or "-").upper(),
            note,
            dist_factor,
            new_factor,
        )
    return new_factor, regime, note
